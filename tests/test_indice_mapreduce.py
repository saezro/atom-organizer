"""Índice map/reduce: `--fase indice` repartida en N tasks (ver `indice_mapreduce`).

Lo que se sujeta:
1. N=1 es el índice de siempre (no deja parciales ni lock).
2. Salida IDÉNTICA entre N=1 y N=7: manifiesto (todas las tablas salvo marcas de
   tiempo), meta/location, árbol entregado e INDICE xlsx (salvo FechaEjecucion),
   con destino local y con destino `gs://` falso.
3. Solo hace el reduce la ÚLTIMA task en terminar (sea cual sea su índice), una
   sola vez, y lo marca en la telemetría.
4. Sin fallbacks silenciosos: parcial ausente, corrupto, de otro reparto o sobre
   un listado distinto = error visible.
5. `indice-reduce` manual rehace el reduce y da lo mismo que el automático.
6. Lock: O_EXCL en local, `ifGenerationMatch=0` en gs://; el reintento de la
   ganadora lo recupera.
"""
import datetime as dt
import gzip
import hashlib
import json
import os
import sqlite3

import pytest

from atom_core import indice_mapreduce as mr
from atom_core import phases, sharding
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)
from tests.test_organizado_plan_apply_e2e import (  # noqa: F401 (fixtures)
    _HostDePrueba, _SignalFalsa, _cfg, _escribir_estadillo,
    _fake_convert_dji_image_to_tif, _fake_run_exif_batch_local,
)

RAIZ_GCS = "gs://bucket-falso/PLANTA/INSPECCION"
PREFIJO_GCS = "PLANTA/INSPECCION/"
EJEC = "exec-idx"


@pytest.fixture
def _origen(tmp_path, make_dji_jpeg):
    """24 imágenes DJI de 3 vuelos repartidas en 2 subcarpetas + 2 fuera de toda
    ventana (SIN_ORDENAR) + 1 no DJI (ignorada). Yaw variado: hay consenso de giro."""
    origen = tmp_path / "origen"
    (origen / "A").mkdir(parents=True)
    (origen / "B").mkdir()
    base = dt.datetime(2026, 1, 15, 10, 0, 0)
    n = 0
    for carpeta, hora, yaw in (("A", 0, 90.0), ("A", 60, -90.0), ("B", 120, 0.0)):
        for i in range(4):
            n += 1
            ts = base + dt.timedelta(minutes=hora, seconds=i * 7)
            for sufijo in ("W", "T"):
                make_dji_jpeg(str(origen / carpeta / f"DJI_{ts:%Y%m%d%H%M%S}_{n:04d}_{sufijo}.JPG"),
                              dt_val=ts,
                              gimbal_yaw=yaw + i, make="DJI")
    for k in (1, 2):
        make_dji_jpeg(str(origen / "B" / f"DJI_2020010{k}000000_90{k:02d}_W.JPG"),
                      dt_val=dt.datetime(2020, 1, k, 0, 0, 0), gimbal_yaw=0.0, make="DJI")
    make_dji_jpeg(str(origen / "B" / "FOTO_AJENA.JPG"), dt_val=base, gimbal_yaw=0.0, make="Canon")
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2026:01:15", "09:55:00", "10:05:00"),
        ("1", "2", "2026:01:15", "10:55:00", "11:05:00"),
        ("2", "1", "2026:01:15", "11:55:00", "12:05:00"),
    ])
    return tmp_path


def _correr(logger, monkeypatch, cfg, fase, shard_index=0, shard_count=1, ejecucion=EJEC,
            ejecucion_indice=""):
    host = _HostDePrueba(logger)
    host.fase = fase
    host.shard_index = shard_index
    host.shard_count = shard_count
    host.ejecucion = ejecucion
    host.ejecucion_indice = ejecucion_indice
    monkeypatch.setattr(host.split_images_obj, "convert_dji_image_to_tif",
                        _fake_convert_dji_image_to_tif)
    monkeypatch.setattr(host.split_images_obj, "_run_exif_batch_local",
                        _fake_run_exif_batch_local)
    pcb, pbar, psum = _SignalFalsa(), _SignalFalsa(), _SignalFalsa()
    host.organizar_plan_apply(cfg, pcb, pbar, psum)
    return pcb


def _indice_n(logger, monkeypatch, cfg, n, orden=None):
    """N tasks de `indice` una tras otra; `orden` = en qué orden terminan."""
    mensajes = {}
    for k in (orden if orden is not None else range(n)):
        mensajes[k] = _correr(logger, monkeypatch, cfg, "indice", k, n)
    return mensajes


def _pipeline_completo(logger, monkeypatch, cfg, n, orden=None):
    _indice_n(logger, monkeypatch, cfg, n, orden)
    _correr(logger, monkeypatch, cfg, "apply", 0, 1, "exec-apply")
    _correr(logger, monkeypatch, cfg, "cierre", 0, 1, "exec-cierre")


def _cfg_meta(origen, **kw):
    return _cfg(origen, gen_meta_location=True, convert_to_tif=False, **kw)


# ---- comparación de salidas --------------------------------------------------

def _volcado_db(ruta_db, destino=None):
    """Todas las tablas del manifiesto, sin las marcas de tiempo de la ejecución
    y (si se da `destino`) con el prefijo del destino sustituido por `<D>`."""
    con = sqlite3.connect(ruta_db)
    con.row_factory = sqlite3.Row
    try:
        salida = {}
        for (tabla,) in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            filas = [dict(f) for f in con.execute(f"SELECT * FROM {tabla}")]
            for f in filas:
                for col in ("inicio", "fin"):
                    f.pop(col, None) if tabla == "ejecuciones" else None
            salida[tabla] = sorted(json.dumps(f, sort_keys=True, default=str).replace(
                json.dumps(destino)[1:-1], "<D>") if destino else
                json.dumps(f, sort_keys=True, default=str) for f in filas)
        return salida
    finally:
        con.close()


def _celdas_xlsx(ruta):
    import openpyxl
    libro = openpyxl.load_workbook(ruta, data_only=True)
    salida = {}
    for hoja in libro.worksheets:
        filas = [list(f) for f in hoja.iter_rows(values_only=True)]
        if filas:
            cabecera = filas[0]
            if "FechaEjecucion" in cabecera:
                col = cabecera.index("FechaEjecucion")
                filas = [f[:col] + f[col + 1:] for f in filas]
        salida[hoja.title] = filas
    return salida


def _salida_local(destino):
    salida = {}
    for raiz, _d, ficheros in os.walk(destino):
        for nombre in ficheros:
            ruta = os.path.join(raiz, nombre)
            rel = os.path.relpath(ruta, destino).replace(os.sep, "/")
            if rel.startswith((".organizado/", "LOGS/")) or rel.endswith(".xlsx"):
                continue
            with open(ruta, "rb") as fh:
                salida[rel] = hashlib.sha256(fh.read()).hexdigest()
    return salida


def _xlsx_local(destino):
    ruta = [os.path.join(r, f) for r, _d, fs in os.walk(destino) for f in fs if f.endswith(".xlsx")]
    assert len(ruta) == 1, ruta
    return _celdas_xlsx(ruta[0])


def _instantanea_local(destino):
    return (_volcado_db(os.path.join(destino, ".organizado", "manifiesto.db"), destino),
            _salida_local(destino), _xlsx_local(destino))


def _instantanea_gcs(bucket, tmp_path):
    db = tmp_path / "bajado.db"
    db.write_bytes(bucket.objetos[PREFIJO_GCS + ".organizado/manifiesto.db"])
    salida, xlsx = {}, None
    for clave, datos in bucket.objetos.items():
        if not clave.startswith(PREFIJO_GCS):
            continue
        rel = clave[len(PREFIJO_GCS):]
        if rel.endswith(".xlsx"):
            f = tmp_path / "indice.xlsx"
            f.write_bytes(datos)
            xlsx = _celdas_xlsx(str(f))
            continue
        if rel.startswith((".organizado/", "LOGS/")):
            continue
        salida[rel] = hashlib.sha256(datos).hexdigest()
    return _volcado_db(str(db), RAIZ_GCS), salida, xlsx


# ---- N=1 y paridad N=1 / N=7 ---------------------------------------------------

def test_n1_no_deja_parciales_ni_lock(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    _correr(logger, monkeypatch, cfg, "indice", 0, 1, ejecucion="")
    assert not os.path.exists(os.path.join(cfg.output_folder, ".organizado", "indice"))
    assert os.path.exists(os.path.join(cfg.output_folder, ".organizado", "manifiesto.db"))


def test_paridad_n1_n7_local(_origen, logger, monkeypatch):
    cfg1 = _cfg_meta(_origen, output_folder=str(_origen / "salida1"))
    cfg7 = _cfg_meta(_origen, output_folder=str(_origen / "salida7"))
    _pipeline_completo(logger, monkeypatch, cfg1, 1)
    # Terminan en orden inverso: la que hace el reduce es la task 0, no la 6.
    _pipeline_completo(logger, monkeypatch, cfg7, 7, orden=[6, 5, 4, 3, 2, 1, 0])
    uno, siete = _instantanea_local(cfg1.output_folder), _instantanea_local(cfg7.output_folder)
    assert uno[0] == siete[0]
    assert uno[1] == siete[1]
    assert uno[2] == siete[2]
    # Hay de todo: meta/location, vuelos con giro, sin ordenar.
    assert any("location" in r.lower() for r in uno[1]), sorted(uno[1])
    assert any("SIN_ORDENAR" in r for r in uno[1])
    assert len(uno[0]["vuelos"]) == 3
    assert len(uno[0]["imagenes"]) == 26  # 24 + 2 sin ordenar; la Canon no entra
    # Los parciales sí existen con N=7.
    carpeta = os.path.join(cfg7.output_folder, ".organizado", "indice", EJEC)
    assert sorted(os.listdir(carpeta)) == sorted(
        [f"map-{k}.jsonl.gz" for k in range(7)] + ["reduce.lock", "propuesta.json", "resumen.json"])


def test_paridad_n1_n7_gcs(_origen, logger, monkeypatch, bucket, tmp_path):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setattr(phases.paralelismo_mod, "ControladorAdaptativo", lambda **kw: None)
    cfg1 = _cfg_meta(_origen, output_folder=str(_origen / "salida1"))
    _pipeline_completo(logger, monkeypatch, cfg1, 1)
    cfg7 = _cfg_meta(_origen, output_folder=RAIZ_GCS)
    _pipeline_completo(logger, monkeypatch, cfg7, 7, orden=[3, 0, 6, 1, 5, 2, 4])
    uno = _instantanea_local(cfg1.output_folder)
    siete = _instantanea_gcs(bucket, tmp_path)
    assert uno[0] == siete[0]
    assert uno[1] == siete[1]
    assert uno[2] == siete[2]
    assert any(k.startswith(PREFIJO_GCS + ".organizado/indice/" + EJEC + "/map-") for k in bucket.objetos)
    assert not os.path.exists("gs:")


# ---- quién hace el reduce ------------------------------------------------------

def test_solo_la_ultima_task_hace_el_reduce(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    db = os.path.join(cfg.output_folder, ".organizado", "manifiesto.db")
    orden = [2, 0, 3, 1]
    for pos, k in enumerate(orden):
        pcb = _correr(logger, monkeypatch, cfg, "indice", k, 4)
        texto = "\n".join(str(m) for m in pcb.mensajes)
        if pos < len(orden) - 1:
            assert not os.path.exists(db), f"reduce prematuro tras {pos + 1} task(s)"
            assert "aún faltan parciales" in texto
        else:
            assert os.path.exists(db)
            assert "gana el lock y hace el reduce" in texto
    tel = os.path.join(cfg.output_folder, ".organizado", "telemetria")
    marcas = {}
    for k in range(4):
        dato = json.load(open(os.path.join(tel, f"{EJEC}-indice-{k}.json"), encoding="utf-8"))
        assert dato["telemetria"] == "organizer" and dato["fase"] == "indice"
        assert dato["shard_index"] == k and dato["shard_count"] == 4 and dato["ok"] is True
        assert {"imagenes", "pared_s", "cpu_s", "vcpu", "utilizacion", "reduce"} <= set(dato)
        marcas[k] = (dato["reduce"], dato["imagenes"])
    assert [k for k, (red, _n) in marcas.items() if red] == [1]  # la última en terminar
    assert sum(n for _r, n in marcas.values()) == 27  # cada imagen la lee UNA task


def test_task_reintentada_tras_el_reduce_no_lo_repite(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    _indice_n(logger, monkeypatch, cfg, 3)
    db = os.path.join(cfg.output_folder, ".organizado", "manifiesto.db")
    antes = hashlib.sha256(open(db, "rb").read()).hexdigest()
    pcb = _correr(logger, monkeypatch, cfg, "indice", 0, 3)  # reintento de una task no ganadora
    assert "otra tarea hace el reduce" in "\n".join(str(m) for m in pcb.mensajes)
    assert hashlib.sha256(open(db, "rb").read()).hexdigest() == antes


def test_ganadora_reintentada_recupera_el_lock(_origen, logger, monkeypatch):
    """La ganadora murió a mitad del reduce: su reintento vuelve a hacerlo."""
    cfg = _cfg_meta(_origen)
    _indice_n(logger, monkeypatch, cfg, 3)  # gana la 2
    pcb = _correr(logger, monkeypatch, cfg, "indice", 2, 3)
    assert "gana el lock y hace el reduce" in "\n".join(str(m) for m in pcb.mensajes)


def test_indice_repartido_exige_ejecucion(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    with pytest.raises(ValueError, match="ejecución"):
        _correr(logger, monkeypatch, cfg, "indice", 0, 3, ejecucion="")


def test_cierre_no_se_reparte(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    with pytest.raises(ValueError, match="no se puede repartir"):
        _correr(logger, monkeypatch, cfg, "cierre", 0, 2)
    with pytest.raises(ValueError):
        _correr(logger, monkeypatch, cfg, "indice-reduce", 0, 2)


# ---- sin fallbacks silenciosos ---------------------------------------------------

def _mapear_sin_reduce(logger, monkeypatch, cfg, n):
    """Deja los N parciales SIN reducir (como si el reduce hubiera muerto)."""
    _indice_n(logger, monkeypatch, cfg, n)
    os.remove(os.path.join(cfg.output_folder, ".organizado", "manifiesto.db"))
    carpeta = os.path.join(cfg.output_folder, ".organizado", "indice", EJEC)
    os.remove(os.path.join(carpeta, "reduce.lock"))
    os.remove(os.path.join(carpeta, "resumen.json"))  # lo escribe el reduce
    os.remove(os.path.join(carpeta, "propuesta.json"))  # idem
    return carpeta


def test_parcial_ausente_es_error_visible(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    carpeta = _mapear_sin_reduce(logger, monkeypatch, cfg, 4)
    os.remove(os.path.join(carpeta, "map-2.jsonl.gz"))
    with pytest.raises(mr.ErrorIndiceParcial, match="map-2"):
        _correr(logger, monkeypatch, cfg, "indice-reduce", 0, 1, "exec-red", EJEC)


def test_parcial_truncado_es_error_visible(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    carpeta = _mapear_sin_reduce(logger, monkeypatch, cfg, 4)
    ruta = os.path.join(carpeta, "map-1.jsonl.gz")
    crudo = open(ruta, "rb").read()
    open(ruta, "wb").write(crudo[: len(crudo) // 2])
    with pytest.raises(mr.ErrorIndiceParcial, match="corrupto o truncado"):
        _correr(logger, monkeypatch, cfg, "indice-reduce", 0, 1, "exec-red", EJEC)


def test_parcial_sin_pie_es_error_visible(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    carpeta = _mapear_sin_reduce(logger, monkeypatch, cfg, 4)
    ruta = os.path.join(carpeta, "map-3.jsonl.gz")
    lineas = gzip.decompress(open(ruta, "rb").read()).decode("utf-8").splitlines()
    open(ruta, "wb").write(gzip.compress(("\n".join(lineas[:-1]) + "\n").encode("utf-8")))
    with pytest.raises(mr.ErrorIndiceParcial, match="sin cabecera o sin pie"):
        _correr(logger, monkeypatch, cfg, "indice-reduce", 0, 1, "exec-red", EJEC)


def test_origen_cambiado_entre_map_y_reduce_es_error_visible(_origen, logger, monkeypatch,
                                                              make_dji_jpeg):
    cfg = _cfg_meta(_origen)
    _mapear_sin_reduce(logger, monkeypatch, cfg, 4)
    make_dji_jpeg(str(_origen / "origen" / "B" / "DJI_20260115100100_5000_W.JPG"),
                  dt_val=dt.datetime(2026, 1, 15, 10, 1, 0), gimbal_yaw=0.0, make="DJI")
    with pytest.raises(mr.ErrorIndiceParcial, match="el origen cambió"):
        _correr(logger, monkeypatch, cfg, "indice-reduce", 0, 1, "exec-red", EJEC)


def test_parcial_de_otro_reparto_es_error_visible(_origen, logger, monkeypatch):
    cfg = _cfg_meta(_origen)
    carpeta = _mapear_sin_reduce(logger, monkeypatch, cfg, 4)
    # Se cuela el map-0 de un reparto de 2 tasks en la carpeta de un reparto de 4.
    cfg2 = _cfg_meta(_origen, output_folder=str(_origen / "otra"))
    _indice_n(logger, monkeypatch, cfg2, 2)
    otro = os.path.join(cfg2.output_folder, ".organizado", "indice", EJEC, "map-0.jsonl.gz")
    # `shard_count_de` lee map-0: el reduce cree que son 2 y le faltan datos del listado.
    open(os.path.join(carpeta, "map-0.jsonl.gz"), "wb").write(open(otro, "rb").read())
    with pytest.raises(mr.ErrorIndiceParcial):
        _correr(logger, monkeypatch, cfg, "indice-reduce", 0, 1, "exec-red", EJEC)


def test_indice_reduce_manual_equivale_al_automatico(_origen, logger, monkeypatch):
    auto = _cfg_meta(_origen, output_folder=str(_origen / "auto"))
    manual = _cfg_meta(_origen, output_folder=str(_origen / "manual"))
    _pipeline_completo(logger, monkeypatch, auto, 5)
    carpeta = _mapear_sin_reduce(logger, monkeypatch, manual, 5)
    assert sorted(os.listdir(carpeta)) == [f"map-{k}.jsonl.gz" for k in range(5)]
    pcb = _correr(logger, monkeypatch, manual, "indice-reduce", 0, 1, "exec-red", EJEC)
    assert "reduce" in "\n".join(str(m) for m in pcb.mensajes)
    _correr(logger, monkeypatch, manual, "apply", 0, 1, "exec-apply")
    _correr(logger, monkeypatch, manual, "cierre", 0, 1, "exec-cierre")
    assert _instantanea_local(auto.output_folder)[0] == _instantanea_local(manual.output_folder)[0]
    assert _instantanea_local(auto.output_folder)[1] == _instantanea_local(manual.output_folder)[1]
    dato = json.load(open(os.path.join(manual.output_folder, ".organizado", "telemetria",
                                       "exec-red-indice-reduce-0.json"), encoding="utf-8"))
    assert dato["fase"] == "indice-reduce" and dato["reduce"] is True


# ---- unidades --------------------------------------------------------------------

@pytest.mark.parametrize("n,count", [(0, 3), (1, 7), (3, 7), (26, 7), (60787, 7), (10, 1)])
def test_tramos_contiguos_y_exhaustivos(n, count):
    tramos = [mr.tramo(n, k, count) for k in range(count)]
    assert tramos[0][0] == 0 and tramos[-1][1] == n
    assert all(tramos[i][1] == tramos[i + 1][0] for i in range(count - 1))
    tamanos = [b - a for a, b in tramos]
    assert max(tamanos) - min(tamanos) <= 1


def test_n_mayor_que_imagenes_deja_tramos_vacios_que_funcionan(tmp_path, make_dji_jpeg, logger,
                                                               monkeypatch):
    origen = tmp_path / "origen"
    origen.mkdir()
    ts = dt.datetime(2026, 1, 15, 10, 0, 0)
    for i in (1, 2, 3):
        make_dji_jpeg(str(origen / f"DJI_2026011510000{i}_000{i}_W.JPG"), dt_val=ts + dt.timedelta(seconds=i),
                      gimbal_yaw=0.0, make="DJI")
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", "2026:01:15", "09:55:00", "10:05:00")])
    cfg1 = _cfg(tmp_path, convert_to_tif=False, output_folder=str(tmp_path / "s1"))
    cfg7 = _cfg(tmp_path, convert_to_tif=False, output_folder=str(tmp_path / "s7"))
    _indice_n(logger, monkeypatch, cfg1, 1)
    _indice_n(logger, monkeypatch, cfg7, 7)
    assert _volcado_db(os.path.join(cfg1.output_folder, ".organizado", "manifiesto.db"),
                       cfg1.output_folder) == \
        _volcado_db(os.path.join(cfg7.output_folder, ".organizado", "manifiesto.db"),
                    cfg7.output_folder)


def test_lock_local_excl_y_reintento_del_dueno(tmp_path):
    destino = str(tmp_path)
    assert mr.adquirir_lock_reduce(destino, "e", 2) is True
    assert mr.adquirir_lock_reduce(destino, "e", 0) is False
    assert mr.adquirir_lock_reduce(destino, "e", 2) is True  # reintento de la ganadora
    assert mr.adquirir_lock_reduce(destino, "otra", 0) is True  # otra ejecución, otro lock


def test_lock_gcs_if_generation_match_0(bucket, tmp_path):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    destino = RAIZ_GCS
    assert mr.adquirir_lock_reduce(destino, "e", 1) is True
    assert mr.adquirir_lock_reduce(destino, "e", 3) is False
    assert bucket.rechazos == 1
    assert mr.adquirir_lock_reduce(destino, "e", 1) is True
    assert json.loads(bucket.objetos[PREFIJO_GCS + ".organizado/indice/e/reduce.lock"]) == \
        {"shard_index": 1}


def test_lock_gcs_error_distinto_de_412_se_propaga(bucket, monkeypatch, tmp_path):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    def _falla(origen, destino, **kw):
        raise RuntimeError("403 sin permiso")
    monkeypatch.setattr(mr, "publicar_en", _falla)
    with pytest.raises(RuntimeError, match="403"):
        mr.adquirir_lock_reduce(RAIZ_GCS, "e", 0)


def test_serializacion_de_metadatos_es_exacta(_origen, logger):
    from atom_core import indice
    import exif as exif_mod
    rutas = sorted(str(p) for p in (_origen / "origen").rglob("*.JPG"))
    exif = exif_mod.GeneralInformationFromImage(logger)

    class _Cb:
        def emit(self, *a, **k):
            pass
    for ruta in rutas:
        dato = indice._leer_metadatos(ruta, exif, _Cb())
        ida_y_vuelta = mr.dato_de_json(json.loads(json.dumps(mr.dato_a_json(dato))))
        assert ida_y_vuelta == dato, ruta
