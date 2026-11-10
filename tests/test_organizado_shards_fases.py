"""Organizado en 3 ejecuciones encadenadas (indice -> apply xN shards -> cierre).

Cloud Run Jobs lanza el MISMO contenedor tres veces; la fase pesada (apply)
se reparte por imagen entre N tasks. Cada task baja `manifiesto.db` en solo
lectura y NO lo sube: escribe sus deltas en
`<destino>/.organizado/shards/<ejecucion>/<shard>.jsonl`; el cierre los funde.

Lo que se sujeta:
1. indice + N x apply + cierre produce EXACTAMENTE la misma salida que
   `completo` (N=1 y N=3; destino local y destino `gs://` falso).
2. Un shard que muere a mitad y se relanza no rehace lo ya hecho (jsonl).
3. Fila sin reportar por ningun shard = pendiente y el cierre lo da por ERROR.
4. Radiometria: con `--sin-rotacion` el R-JPEG termico sale con APP3/4/5
   iguales byte a byte.
5. Puerta de entrada: el guard de reparto solo rechaza completo/indice/cierre.
"""
import hashlib
import os
import sqlite3
import struct

import pytest

from atom_core import apply_shards, sharding
from atom_core import organize, phases
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)
from tests.test_organizado_plan_apply_e2e import (  # noqa: F401 (fixtures)
    _HostDePrueba, _SignalFalsa, _cfg, _escribir_estadillo, _inspeccion,
    _fake_convert_dji_image_to_tif, _fake_run_exif_batch_local,
)

RAIZ_GCS = "gs://bucket-falso/PLANTA/INSPECCION"
PREFIJO_GCS = "PLANTA/INSPECCION/"


def _correr_fase(logger, monkeypatch, cfg, fase, shard_index=0, shard_count=1,
                 ejecucion="exec-1"):
    host = _HostDePrueba(logger)
    host.fase = fase
    host.shard_index = shard_index
    host.shard_count = shard_count
    host.ejecucion = ejecucion
    monkeypatch.setattr(host.split_images_obj, "convert_dji_image_to_tif",
                        _fake_convert_dji_image_to_tif)
    monkeypatch.setattr(host.split_images_obj, "_run_exif_batch_local",
                        _fake_run_exif_batch_local)
    pcb, pbar, psum = _SignalFalsa(), _SignalFalsa(), _SignalFalsa()
    host.organizar_plan_apply(cfg, pcb, pbar, psum)
    return pcb


def _encadenado(logger, monkeypatch, cfg, n, ejecucion="exec-1", fase_apply="apply"):
    _correr_fase(logger, monkeypatch, cfg, "indice")
    for i in range(n):
        _correr_fase(logger, monkeypatch, cfg, fase_apply, i, n, ejecucion)
    return _correr_fase(logger, monkeypatch, cfg, "cierre")


def _sha(datos):
    return hashlib.sha256(datos).hexdigest()


def _salida_local(destino):
    """{ruta relativa: sha} de lo entregado (sin `.organizado/`, sin LOGS/ ni el
    indice Excel, que llevan marcas de tiempo del run)."""
    salida = {}
    for raiz, _d, ficheros in os.walk(destino):
        for nombre in ficheros:
            ruta = os.path.join(raiz, nombre)
            rel = os.path.relpath(ruta, destino).replace(os.sep, "/")
            if rel.startswith((".organizado/", "LOGS/")) or rel.endswith(".xlsx"):
                continue
            with open(ruta, "rb") as fh:
                salida[rel] = _sha(fh.read())
    return salida


def _salida_gcs(bucket):
    salida = {}
    for clave, datos in bucket.objetos.items():
        if not clave.startswith(PREFIJO_GCS):
            continue
        rel = clave[len(PREFIJO_GCS):]
        if rel.startswith((".organizado/", "LOGS/")) or rel.endswith(".xlsx"):
            continue
        salida[rel] = _sha(datos)
    return salida


def _estados(ruta_db):
    con = sqlite3.connect(ruta_db)
    try:
        return sorted(con.execute(
            "SELECT ruta_origen, estado, ruta_salida_original FROM imagenes").fetchall())
    finally:
        con.close()


@pytest.fixture
def _referencia(_inspeccion, logger, monkeypatch, tmp_path):
    """Salida de `completo` sobre la misma inspeccion (en otro destino)."""
    cfg = _cfg(_inspeccion, output_folder=str(tmp_path / "ref"))
    _correr_fase(logger, monkeypatch, cfg, "completo")
    return _salida_local(cfg.output_folder), _estados(
        os.path.join(cfg.output_folder, ".organizado", "manifiesto.db"))


@pytest.mark.parametrize("n", [1, 3])
def test_encadenado_equivale_a_completo_local(_inspeccion, _referencia, logger, monkeypatch, n):
    salida_ref, estados_ref = _referencia
    cfg = _cfg(_inspeccion, output_folder=str(_inspeccion / f"enc{n}"))
    _encadenado(logger, monkeypatch, cfg, n)
    assert _salida_local(cfg.output_folder) == salida_ref
    assert len(salida_ref) >= 7  # 2 RGB + 1 SIN_ORDENAR + 2 JPG + 2 TIF de termica
    estados = _estados(os.path.join(cfg.output_folder, ".organizado", "manifiesto.db"))
    # Misma tabla salvo el prefijo del destino en las rutas de salida.
    assert [(o, e) for o, e, _s in estados] == [(o, e) for o, e, _s in estados_ref]
    assert all(e == "hecho" for _o, e, _s in estados)
    # Un jsonl por shard, todos en la carpeta de la ejecucion.
    carpeta = os.path.join(cfg.output_folder, ".organizado", "shards", "exec-1")
    assert sorted(os.listdir(carpeta)) == [f"apply-{i}.jsonl" for i in range(n)]


def test_apply_no_toca_el_manifiesto_del_destino(_inspeccion, logger, monkeypatch):
    cfg = _cfg(_inspeccion)
    _correr_fase(logger, monkeypatch, cfg, "indice")
    db = os.path.join(cfg.output_folder, ".organizado", "manifiesto.db")
    antes = _sha(open(db, "rb").read())
    _correr_fase(logger, monkeypatch, cfg, "apply", 0, 2, "exec-1")
    assert _sha(open(db, "rb").read()) == antes
    assert {e for _o, e, _s in _estados(db)} == {"pendiente"}


def test_cada_imagen_la_procesa_un_solo_shard(_inspeccion, logger, monkeypatch):
    cfg = _cfg(_inspeccion)
    _encadenado(logger, monkeypatch, cfg, 3)
    carpeta = os.path.join(cfg.output_folder, ".organizado", "shards", "exec-1")
    ids = []
    for i in range(3):
        for linea in open(os.path.join(carpeta, f"apply-{i}.jsonl"), encoding="utf-8"):
            ids.append(__import__("json").loads(linea)["id"])
    assert len(ids) == 5 and len(set(ids)) == 5


def test_encadenado_equivale_a_completo_en_gcs(_inspeccion, _referencia, logger, monkeypatch, bucket, tmp_path):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    # El bucket falso vive en ESTE proceso: el pool de procesos de RGB no lo veria.
    # Sin controlador, el apply corre secuencial en el proceso (camino de los tests).
    monkeypatch.setattr(phases.paralelismo_mod, "ControladorAdaptativo", lambda **kw: None)
    salida_ref, _ = _referencia
    cfg = _cfg(_inspeccion, output_folder=RAIZ_GCS)
    _encadenado(logger, monkeypatch, cfg, 3)
    assert _salida_gcs(bucket) == salida_ref
    assert any(k.startswith(PREFIJO_GCS + ".organizado/shards/exec-1/") for k in bucket.objetos)
    assert not os.path.exists("gs:") and not os.path.exists("gs:/bucket-falso")


def test_reanudacion_de_un_shard_salta_lo_ya_hecho(_inspeccion, logger, monkeypatch):
    cfg = _cfg(_inspeccion)
    _encadenado(logger, monkeypatch, cfg, 1)
    db = os.path.join(cfg.output_folder, ".organizado", "manifiesto.db")
    jsonl = os.path.join(cfg.output_folder, ".organizado", "shards", "exec-1", "apply-0.jsonl")
    entradas = open(jsonl, encoding="utf-8").read().splitlines()
    assert len(entradas) == 5
    con = sqlite3.connect(db)
    ids = {fila[0]: fila[1] for fila in con.execute("SELECT id, ruta_salida_original FROM imagenes")}
    con.close()
    # El shard "murio" tras la primera fila: su jsonl solo tiene esa. Las demas
    # salidas no existen; la ya reportada lleva un centinela.
    import json
    hecha = json.loads(entradas[0])["id"]
    with open(jsonl, "w", encoding="utf-8") as fh:
        fh.write(entradas[0] + "\n")
    for id_fila, ruta in ids.items():
        if id_fila == hecha:
            with open(ruta, "wb") as fh:
                fh.write(b"CENTINELA")
        elif os.path.exists(ruta):
            os.remove(ruta)
    # Se rehace el manifiesto "como lo dejo el indice": todo pendiente.
    con = sqlite3.connect(db)
    con.execute("UPDATE imagenes SET estado='pendiente', verificacion=NULL")
    con.commit()
    con.close()

    # Relanzado con el mismo CLOUD_RUN_EXECUTION (maxRetries=1).
    _correr_fase(logger, monkeypatch, cfg, "apply", 0, 1, "exec-1")

    assert open(ids[hecha], "rb").read() == b"CENTINELA"  # no se rehizo
    for id_fila, ruta in ids.items():
        assert os.path.exists(ruta), ruta
    assert len(open(jsonl, encoding="utf-8").read().splitlines()) == 5


def test_fila_sin_reportar_queda_pendiente_y_el_cierre_la_da_por_error(_inspeccion, logger, monkeypatch):
    cfg = _cfg(_inspeccion)
    _correr_fase(logger, monkeypatch, cfg, "indice")
    # Solo corren 2 de los 3 shards: lo del tercero no se reporta nunca.
    _correr_fase(logger, monkeypatch, cfg, "apply", 0, 3, "exec-1")
    _correr_fase(logger, monkeypatch, cfg, "apply", 1, 3, "exec-1")
    pcb = _correr_fase(logger, monkeypatch, cfg, "cierre")
    texto = "\n".join(str(m) for m in pcb.mensajes)
    assert "HA HABIDO ERRORES" in texto and "siguen 'pendiente'/'en_curso'" in texto
    db = os.path.join(cfg.output_folder, ".organizado", "manifiesto.db")
    estados = {e for _o, e, _s in _estados(db)}
    assert "pendiente" in estados

    # Relanzar el shard que faltaba lo recoge (nueva ejecucion, filas ya
    # fundidas por el cierre anterior no se tocan).
    _correr_fase(logger, monkeypatch, cfg, "apply", 2, 3, "exec-2")
    pcb = _correr_fase(logger, monkeypatch, cfg, "cierre")
    assert {e for _o, e, _s in _estados(db)} == {"hecho"}
    assert "HA HABIDO ERRORES" not in "\n".join(str(m) for m in pcb.mensajes)


def test_apply_fallida_se_reporta_en_el_cierre(_inspeccion, logger, monkeypatch):
    cfg = _cfg(_inspeccion)
    _correr_fase(logger, monkeypatch, cfg, "indice")
    os.remove(os.path.join(cfg.input_folder, "DJI_0001_D.JPG"))
    _correr_fase(logger, monkeypatch, cfg, "apply", 0, 1, "exec-1")
    pcb = _correr_fase(logger, monkeypatch, cfg, "cierre")
    db = os.path.join(cfg.output_folder, ".organizado", "manifiesto.db")
    assert "fallido" in {e for _o, e, _s in _estados(db)}
    assert "HA HABIDO ERRORES" in "\n".join(str(m) for m in pcb.mensajes)


# ---- radiometria ----------------------------------------------------------

def _segmentos_app(datos, tipos=(0xE3, 0xE4, 0xE5)):
    """{marcador: [payload,...]} de los segmentos APPn pedidos (hasta SOS)."""
    assert datos[:2] == b"\xff\xd8"
    pos, salida = 2, {}
    while pos + 4 <= len(datos) and datos[pos] == 0xFF:
        marcador = datos[pos + 1]
        if marcador == 0xDA:
            break
        (longitud,) = struct.unpack(">H", datos[pos + 2:pos + 4])
        if marcador in tipos:
            salida.setdefault(marcador, []).append(datos[pos + 4:pos + 2 + longitud])
        pos += 2 + longitud
    return salida


def _con_app345(ruta):
    datos = open(ruta, "rb").read()
    extra = b""
    for marcador, payload in ((0xE3, b"RJPG-APP3" + bytes(range(200))),
                              (0xE4, b"RJPG-APP4" + bytes(range(100, 250))),
                              (0xE5, b"RJPG-APP5" + bytes(range(50, 240)))):
        extra += bytes([0xFF, marcador]) + struct.pack(">H", len(payload) + 2) + payload
    with open(ruta, "wb") as fh:
        fh.write(datos[:2] + extra + datos[2:])


def test_rjpeg_termico_intacto_con_sin_rotacion(tmp_path, make_dji_jpeg, logger, monkeypatch):
    import datetime as dt
    origen = tmp_path / "origen"
    origen.mkdir()
    ts = dt.datetime(2026, 1, 15, 10, 0, 0)
    # Yaw lejos de 0: si algo girase, el JPG cambiaria.
    for i in (1, 2, 3):
        make_dji_jpeg(str(origen / f"DJI_000{i}_D.JPG"), dt_val=ts + dt.timedelta(seconds=i),
                      gimbal_yaw=-90.0, make="DJI")
        make_dji_jpeg(str(origen / f"DJI_000{i}_T.JPG"), dt_val=ts + dt.timedelta(seconds=i),
                      gimbal_yaw=-90.0, make="DJI")
        _con_app345(str(origen / f"DJI_000{i}_T.JPG"))
    _escribir_estadillo(tmp_path / "estadillo.csv",
                       [("1", "1", "2026:01:15", "09:55:00", "10:05:00")])
    cfg = _cfg(tmp_path, gen_thumbnails=False, convert_to_tiff_rotate_auto=False)
    _encadenado(logger, monkeypatch, cfg, 3)

    carpeta = os.path.join(cfg.output_folder, "TERMICA", "PB1", "PB1_V1")
    jpgs = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".jpg"))
    assert len(jpgs) == 3
    for nombre in jpgs:
        salida = open(os.path.join(carpeta, nombre), "rb").read()
        coincide = [f for f in os.listdir(origen) if f.endswith("_T.JPG") and nombre.endswith(f)]
        assert len(coincide) == 1, (nombre, coincide)
        original = open(origen / coincide[0], "rb").read()
        seg_o, seg_s = _segmentos_app(original), _segmentos_app(salida)
        assert set(seg_o) == {0xE3, 0xE4, 0xE5}
        assert seg_s == seg_o
        assert salida == original


# ---- unidades -------------------------------------------------------------

def test_fases_validas_en_sharding():
    assert sharding.FASES == ("completo", "indice", "indice-reduce", "apply", "rgb",
                              "termica", "cierre")
    assert set(sharding.FASES_REPARTIBLES) == {"apply", "rgb", "termica"}
    assert sharding.FASES_MAPREDUCE == ("indice",)


def test_ejecucion_env_manda_y_arg_es_respaldo():
    assert sharding.ejecucion_desde_entorno({"CLOUD_RUN_EXECUTION": "job-abc"}, "x") == "job-abc"
    assert sharding.ejecucion_desde_entorno({}, "x") == "x"
    assert sharding.ejecucion_desde_entorno({}, None) == ""


def test_registro_vuelca_cada_n_filas_y_al_cerrar(tmp_path):
    reg = apply_shards.RegistroDeltas(str(tmp_path), "e", 0, cada_filas=2, cada_segundos=1e9)
    ruta = apply_shards.ruta_deltas(str(tmp_path), "e", 0)
    reg.registrar_hecha(1, "o1", "v1")
    assert not os.path.exists(ruta)
    reg.registrar_fallida(2, "o2", "boom")
    assert len(open(ruta, encoding="utf-8").read().splitlines()) == 2
    reg.registrar_hecha(3, "o3", "v3")
    reg.cerrar()
    assert len(open(ruta, encoding="utf-8").read().splitlines()) == 3


def test_registro_vuelca_por_tiempo(tmp_path):
    t = {"v": 0.0}
    reg = apply_shards.RegistroDeltas(str(tmp_path), "e", 1, cada_filas=1000, cada_segundos=60,
                                      reloj=lambda: t["v"])
    reg.registrar_hecha(1, "o1", "v1")
    t["v"] = 61.0
    reg.registrar_hecha(2, "o2", "v2")
    assert len(open(apply_shards.ruta_deltas(str(tmp_path), "e", 1), encoding="utf-8").read().splitlines()) == 2


def test_leer_deltas_tolera_linea_final_cortada(tmp_path):
    ruta = apply_shards.ruta_deltas(str(tmp_path), "e", 0)
    os.makedirs(os.path.dirname(ruta))
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write('{"id": 1, "estado": "hecho", "verificacion": "v", "ruta_origen": "o"}\n{"id": 2, "est')
    assert [d["id"] for d in apply_shards.leer_deltas(str(tmp_path))] == [1]


# ---- puerta de entrada (guard de reparto) ---------------------------------

def _errores_run_task(**extra):
    eventos = []
    params = {"origen": "/no/existe", "destino": "/tmp/x", "estadillo": "/no/existe.csv",
              "etapa": "todo", "shard_index": 0, "shard_count": 3, **extra}
    organize.run_task("split_images", params, lambda k, p=None: eventos.append((k, p)))
    return [p for k, p in eventos if k == "error"]


@pytest.mark.parametrize("fase", ["completo", "indice", "cierre"])
def test_guard_rechaza_reparto_en_fases_no_repartibles(fase):
    errores = _errores_run_task(fase=fase)
    assert errores and "repart" in errores[0], errores


@pytest.mark.parametrize("fase", ["apply", "rgb", "termica"])
def test_guard_admite_reparto_en_apply(fase):
    errores = _errores_run_task(fase=fase, ejecucion="e")
    assert not any("repart" in e for e in errores), errores


def test_fase_desconocida_se_rechaza():
    errores = _errores_run_task(fase="rapida", shard_count=1)
    assert errores and "Fase desconocida" in errores[0]


def test_apply_sin_ejecucion_se_rechaza():
    errores = _errores_run_task(fase="apply")
    assert errores and "ejecucion" in errores[0].lower()


# ---- rgb y termica a la vez, telemetria, tamanos de pool -------------------

def test_rgb_y_termica_concurrentes_equivalen_a_completo_en_gcs(
        _inspeccion, _referencia, logger, monkeypatch, bucket, tmp_path):
    """Dos ejecuciones SEPARADAS (rgb y termica), solapadas en el tiempo, cada una
    con 2 shards, sobre el mismo destino gs://: deltas en ficheros distintos, nada
    compartido que se pise, y el cierre las funde igual que `completo`."""
    import threading
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setattr(phases.paralelismo_mod, "ControladorAdaptativo", lambda **kw: None)
    salida_ref, _ = _referencia
    cfg = _cfg(_inspeccion, output_folder=RAIZ_GCS)
    _correr_fase(logger, monkeypatch, cfg, "indice")
    errores = []

    def _ejecucion(fase, ejecucion):
        try:
            for i in range(2):
                _correr_fase(logger, monkeypatch, cfg, fase, i, 2, ejecucion)
        except BaseException as exc:  # noqa: BLE001
            errores.append(exc)

    hilos = [threading.Thread(target=_ejecucion, args=("rgb", "exec-rgb")),
             threading.Thread(target=_ejecucion, args=("termica", "exec-term"))]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores, errores
    claves = bucket.objetos
    for nombre in ("exec-rgb/rgb-0", "exec-rgb/rgb-1", "exec-term/termica-0", "exec-term/termica-1"):
        assert f"{PREFIJO_GCS}.organizado/shards/{nombre}.jsonl" in claves, sorted(claves)
    pcb = _correr_fase(logger, monkeypatch, cfg, "cierre")
    assert _salida_gcs(bucket) == salida_ref
    assert "HA HABIDO ERRORES" not in "\n".join(str(m) for m in pcb.mensajes)


def test_telemetria_por_task_en_stdout_y_en_destino(_inspeccion, logger, monkeypatch, capsys):
    import json
    cfg = _cfg(_inspeccion)
    _correr_fase(logger, monkeypatch, cfg, "indice")
    capsys.readouterr()
    _correr_fase(logger, monkeypatch, cfg, "apply", 0, 1, "exec-1")
    lineas = [json.loads(l) for l in capsys.readouterr().out.splitlines()
              if l.startswith("{") and '"telemetria"' in l]
    assert len(lineas) == 1
    t = lineas[0]
    assert t["telemetria"] == "organizer" and t["fase"] == "apply"
    assert (t["shard_index"], t["shard_count"], t["imagenes"]) == (0, 1, 5)
    assert t["pared_s"] > 0 and t["cpu_s"] >= 0 and t["vcpu"] >= 1
    assert t["utilizacion"] == pytest.approx(t["cpu_s"] / (t["vcpu"] * t["pared_s"]), abs=0.01)
    ruta = os.path.join(cfg.output_folder, ".organizado", "telemetria", "exec-1-apply-0.json")
    assert json.load(open(ruta, encoding="utf-8"))["imagenes"] == 5


def test_cpus_efectivas_respeta_cgroup_y_afinidad(monkeypatch):
    import utils
    monkeypatch.setattr(os, "sched_getaffinity", lambda _p: set(range(8)))
    ficheros = {"/sys/fs/cgroup/cpu.max": "200000 100000"}
    monkeypatch.setattr(utils, "_leer_texto", lambda r: ficheros.get(r))
    assert utils.cpus_efectivas() == 2
    ficheros["/sys/fs/cgroup/cpu.max"] = "max 100000"
    assert utils.cpus_efectivas() == 8
    monkeypatch.setattr(os, "sched_getaffinity", lambda _p: {0, 1, 2})
    assert utils.cpus_efectivas() == 3


def test_memoria_cgroup_y_tamano_de_pool_rgb(monkeypatch):
    import utils
    ficheros = {"/sys/fs/cgroup/memory.max": str(2048 * 1024 * 1024),
                "/sys/fs/cgroup/memory.current": str(1024 * 1024 * 1024),
                "/sys/fs/cgroup/cpu.max": "800000 100000"}
    monkeypatch.setattr(utils, "_leer_texto", lambda r: ficheros.get(r))
    monkeypatch.setattr(os, "sched_getaffinity", lambda _p: set(range(16)))
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    assert utils.memoria_cgroup_mb() == pytest.approx(1024.0)
    assert utils.workers_para_lote(mb_por_worker=0) == 8          # vCPU
    assert utils.workers_para_lote(utils.MB_POR_WORKER) <= 1024 // 600  # RAM del cgroup
    monkeypatch.delenv("ATOM_IO_WORKERS", raising=False)
    assert utils.workers_termica() == 8                           # hilos = vCPU
