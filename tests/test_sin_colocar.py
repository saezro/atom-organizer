"""Fotos fuera de rango del estadillo: grupos, vecinos y colocación manual.

Lo que sujetan estos tests (`atom_core.sin_colocar`):
- el índice agrupa las fotos de SIN_ORDENAR por hora EXIF con un corte configurable,
  sin partir las tomas T/W/Z, y guarda las filas vecinas del estadillo;
- `asignar_grupo` reescribe las filas del manifiesto como una asignación automática
  (carpeta del vuelo, `_CROP`, giro único por vuelo) y el apply las coloca ahí;
- la decisión del usuario sobrevive a re-índices y a tandas posteriores;
- todo error (grupo/fila inexistente, estado incompatible) es explícito y no escribe.
"""
import datetime as dt
import json
import os

import pytest

import organize_cli
import pipeline as pipeline_real
from atom_core import apply, sin_colocar
from atom_core.indice import construir_indice
from atom_core.manifiesto import Manifiesto
from atom_core.sin_colocar import ErrorSinColocar, asignar_grupo, grupos_sin_colocar
from tests.test_apply_rgb import _cfg as _cfg_apply
from tests.test_apply_rgb import _SignalFalsa
from tests.test_giro_manifiesto_gcs import CLAVE as CLAVE_GCS
from tests.test_giro_manifiesto_gcs import RAIZ as RAIZ_GCS
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)
from tests.test_indice_organizado import (
    _cfg, _crear_imagen, _escribir_estadillo, _ExifDePrueba, _PipelineDePrueba, _Signal)

ESTADILLO = [
    ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),   # fila 0
    ("1", "2", "2024:06:01", "11:00:00", "11:10:00"),   # fila 1
]
VENTANAS = {
    ("2024:06:01", "10:00:00", "10:10:00"): (
        dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
    ("2024:06:01", "11:00:00", "11:10:00"): (
        dt.datetime(2024, 6, 1, 11, 0, 0), dt.datetime(2024, 6, 1, 11, 10, 0)),
}


def _t(h, m, s=0, dia=1):
    return dt.datetime(2024, 6, dia, h, m, s)


class _Tanda:
    """Un destino con su manifiesto en la ruta estándar y utilidades de índice."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.destino = str(tmp_path / "destino")
        os.makedirs(os.path.join(self.destino, ".organizado"), exist_ok=True)
        self.n = 0

    def indexar(self, fotos, estadillo=ESTADILLO, ventanas=VENTANAS, hueco=None,
                yaws=None, cfg_extra=None, pct=None, origen=None):
        """`fotos`: `[(nombre, datetime | None)]`. Devuelve `(cfg, mensajes)`."""
        self.n += 1
        _escribir_estadillo(self.tmp / "estadillo.csv", estadillo)
        carpeta = origen or str(self.tmp / "origen")
        cfg = _cfg(self.tmp, input_folder=carpeta, **(cfg_extra or {}))
        rutas = {nombre: _crear_imagen(carpeta, nombre) for nombre, _ts in fotos}
        exif = _ExifDePrueba(
            timestamps={rutas[n]: ts for n, ts in fotos if ts is not None},
            yaws={rutas[n]: y for n, y in (yaws or {}).items()})
        manifiesto = Manifiesto(os.path.join(self.destino, ".organizado", "manifiesto.db"))
        manifiesto.crear_esquema()
        senal = _Signal()
        try:
            construir_indice(
                cfg, _PipelineDePrueba(ventanas, pct_por_modelo=pct), exif, manifiesto,
                senal, _Signal(), _Signal(),
                ejecucion_id=manifiesto.abrir_ejecucion(carpeta, "test"), hueco_grupo_s=hueco)
        finally:
            manifiesto.cerrar()
        self.rutas = rutas
        return cfg, senal.mensajes

    def filas(self):
        m = Manifiesto(os.path.join(self.destino, ".organizado", "manifiesto.db"))
        try:
            return {f["nombre_original"]: f for f in m.todas()}
        finally:
            m.cerrar()

    def manifiesto(self):
        return Manifiesto(os.path.join(self.destino, ".organizado", "manifiesto.db"))


@pytest.fixture
def tanda(tmp_path):
    return _Tanda(tmp_path)


FOTOS_HUECOS = [
    ("DJI_0001_D.JPG", _t(10, 20, 0)), ("DJI_0002_D.JPG", _t(10, 20, 30)),
    ("DJI_0003_D.JPG", _t(10, 21, 0)),
    ("DJI_0004_D.JPG", _t(10, 30, 0)), ("DJI_0005_D.JPG", _t(10, 30, 10)),
    ("DJI_0006_D.JPG", _t(12, 0, 0)),
    ("DJI_0007_D.JPG", _t(10, 5, 0)),           # dentro de la fila 0: no es un grupo
]


def test_agrupa_con_huecos_y_da_los_vecinos(tanda):
    tanda.indexar(FOTOS_HUECOS)
    grupos = grupos_sin_colocar(tanda.destino)
    assert [g["n_fotos"] for g in grupos] == [3, 2, 1]
    a, b, c = grupos
    assert (a["hora_inicio"], a["hora_fin"]) == (_t(10, 20, 0).isoformat(), _t(10, 21, 0).isoformat())
    assert a["motivo"] == "fuera-de-ventana" and a["zona_horaria"] == "exif-local"
    assert a["fotos_por_tipo"] == {"RGB": 3}
    # Vecinos: fila 0 acaba 10:10 (600 s antes), fila 1 empieza 11:00 (2340 s después).
    assert a["vecino_anterior"] == {
        "fila": 0, "pb": "1", "vuelo": "1", "fecha": "2024:06:01",
        "hora_inicio": _t(10, 0).isoformat(), "hora_fin": _t(10, 10).isoformat(),
        "distancia_s": 600.0}
    assert a["vecino_siguiente"]["fila"] == 1 and a["vecino_siguiente"]["distancia_s"] == 2340.0
    assert b["vecino_anterior"]["distancia_s"] == 1200.0
    assert c["vecino_siguiente"] is None and c["vecino_anterior"]["fila"] == 1
    assert c["vecino_anterior"]["distancia_s"] == 3000.0
    assert all(g["estado"] == "pendiente" and g["decision"] is None for g in grupos)
    assert len({g["grupo_id"] for g in grupos}) == 3


def test_el_hueco_es_configurable_y_corta_solo_si_lo_supera(tanda):
    fotos = [("DJI_0001_D.JPG", _t(10, 20, 0)), ("DJI_0002_D.JPG", _t(10, 22, 0)),   # 120 s
             ("DJI_0003_D.JPG", _t(10, 24, 1))]                                      # 121 s
    tanda.indexar(fotos)   # defecto 120: el hueco de 120 NO corta, el de 121 sí
    assert [g["n_fotos"] for g in grupos_sin_colocar(tanda.destino)] == [2, 1]
    tanda.indexar(fotos, hueco=500)
    assert [g["n_fotos"] for g in grupos_sin_colocar(tanda.destino)] == [3]
    # Un hueco inválido no se corrige: la función lo rechaza y, en el índice, es un
    # ERROR visible (las filas ya están escritas).
    with pytest.raises(ValueError):
        sin_colocar.hueco_segundos(0)
    _cfg_, msgs = tanda.indexar(fotos, hueco=0)
    assert any("ERROR" in str(m) and "grupos de fotos sin colocar" in str(m) for m in msgs)


def test_dia_sin_estadillo_y_sin_fecha_no_tienen_vecinos(tanda):
    fotos = [("DJI_0001_D.JPG", _t(10, 20, 0, dia=2)), ("DJI_0002_D.JPG", None),
             ("DJI_0003_D.JPG", _t(10, 20, 0))]
    tanda.indexar(fotos)
    por_motivo = {g["motivo"]: g for g in grupos_sin_colocar(tanda.destino)}
    assert set(por_motivo) == {"dia-sin-estadillo", "sin-fecha-exif", "fuera-de-ventana"}
    for motivo in ("dia-sin-estadillo", "sin-fecha-exif"):
        g = por_motivo[motivo]
        assert g["vecino_anterior"] is None and g["vecino_siguiente"] is None
    assert por_motivo["sin-fecha-exif"]["hora_inicio"] is None
    assert por_motivo["fuera-de-ventana"]["vecino_anterior"]["fila"] == 0


def test_no_separa_las_tomas_t_w_z(tanda):
    # Hueco minúsculo (0.5 s): sin la regla de toma, T/W/Z de 1-2 s se partirían.
    fotos = [("DJI_20240601102000_0001_T.JPG", _t(10, 20, 0)),
             ("DJI_20240601102001_0001_W.JPG", _t(10, 20, 1)),
             ("DJI_20240601102002_0001_Z.JPG", _t(10, 20, 2)),
             ("DJI_20240601102003_0002_T.JPG", _t(10, 20, 3))]    # otra toma
    tanda.indexar(fotos, hueco=0.5, cfg_extra=dict(end_rgb_files="_W", end_rgb_extra_files="_Z"))
    grupos = grupos_sin_colocar(tanda.destino, incluir_fotos=True)
    assert [g["n_fotos"] for g in grupos] == [3, 1]
    assert grupos[0]["fotos_por_tipo"] == {"RGB": 1, "RGB_Extra": 1, "TERMICA": 1}


def test_asignar_reescribe_filas_con_la_logica_automatica(tanda):
    cfg, _ = tanda.indexar(
        FOTOS_HUECOS, cfg_extra=dict(cropping_rgb=True), pct={"M3T": 80})
    a = grupos_sin_colocar(tanda.destino)[0]
    res = asignar_grupo(tanda.destino, a["grupo_id"], 1, quien="rodrigo_saez")
    assert res["n_fotos"] == 3 and res["pendientes_apply"] == 3 and res["recolocadas"] == 0
    filas = tanda.filas()
    carpeta = os.path.join(tanda.destino, "RGB", "PB1", "PB1_V2")
    for nombre in ("DJI_0001_D.JPG", "DJI_0002_D.JPG", "DJI_0003_D.JPG"):
        f = filas[nombre]
        assert (f["pb"], f["vuelo"], f["unassigned"], f["estado"]) == ("1", "2", 0, "pendiente")
        assert f["ruta_salida_original"] == os.path.join(carpeta, nombre)
        assert f["ruta_salida_crop"] == os.path.join(carpeta, nombre.replace(".JPG", "_CROP.JPG"))
        assert f["pct_recorte"] == 0.8
    # Las que no eran del grupo siguen en SIN_ORDENAR.
    assert filas["DJI_0004_D.JPG"]["unassigned"] == 1
    assert "SIN_ORDENAR" in filas["DJI_0004_D.JPG"]["ruta_salida_original"]
    # Registro: quién, cuándo y fila.
    m = tanda.manifiesto()
    try:
        reg = m.asignaciones_manuales()
    finally:
        m.cerrar()
    assert len(reg) == 3
    for r in reg.values():
        assert r["fila"] == 1 and r["quien"] == "rodrigo_saez" and r["grupo_id"] == a["grupo_id"]
        assert r["cuando"].endswith(("+01:00", "+02:00"))      # Europe/Madrid
    # Ya decidido: no sale entre los pendientes, sí con todos.
    assert [g["grupo_id"] for g in grupos_sin_colocar(tanda.destino)] != []
    todos = {g["grupo_id"]: g for g in grupos_sin_colocar(tanda.destino, solo_pendientes=False)}
    assert todos[a["grupo_id"]]["estado"] == "asignado"
    assert todos[a["grupo_id"]]["decision"]["fila_estadillo"] == 1
    assert a["grupo_id"] not in {g["grupo_id"] for g in grupos_sin_colocar(tanda.destino)}


def test_camara_rgb_se_respeta_al_asignar(tanda):
    # Con W y Z en la tanda y camara_rgb=Z solo se recorta la Z (decisión del índice, que
    # la asignación no puede cambiar).
    fotos = [("DJI_20240601102000_0001_W.JPG", _t(10, 20, 0)),
             ("DJI_20240601102001_0001_Z.JPG", _t(10, 20, 1))]
    tanda.indexar(fotos, cfg_extra=dict(cropping_rgb=True, end_rgb_files="_W",
                                        end_rgb_extra_files="_Z"),
                  pct={"M3T": 80, "M3T_Z": 60})
    antes = tanda.filas()
    assert antes["DJI_20240601102000_0001_W.JPG"]["ruta_salida_crop"] is None
    assert antes["DJI_20240601102001_0001_Z.JPG"]["ruta_salida_crop"]
    g = grupos_sin_colocar(tanda.destino)[0]
    asignar_grupo(tanda.destino, g["grupo_id"], 0, quien="x")
    despues = tanda.filas()
    assert despues["DJI_20240601102000_0001_W.JPG"]["ruta_salida_crop"] is None
    z = despues["DJI_20240601102001_0001_Z.JPG"]
    assert z["ruta_salida_crop"].endswith("_Z_CROP.JPG") and z["pct_recorte"] == 0.6
    assert os.path.dirname(z["ruta_salida_crop"]) == os.path.dirname(z["ruta_salida_original"])
    assert all(f["ruta_salida_original"].startswith(os.path.join(tanda.destino, "RGB", "PB1"))
               or f["ruta_salida_original"].startswith(os.path.join(tanda.destino, "RGB_Extra", "PB1"))
               for f in despues.values())


def test_el_apply_coloca_las_fotos_en_la_carpeta_de_la_fila(tanda, make_dji_jpeg):
    tanda.indexar([("DJI_0001_D.JPG", _t(10, 20, 0))])
    g = grupos_sin_colocar(tanda.destino)[0]
    asignar_grupo(tanda.destino, g["grupo_id"], 0, quien="x")
    origen = tanda.rutas["DJI_0001_D.JPG"]
    make_dji_jpeg(origen)       # el índice usó un fichero vacío: ahora la imagen real
    m = tanda.manifiesto()
    try:
        r = apply.aplicar_rgb(m, _cfg_apply(output_folder=tanda.destino), pipeline_real,
                              _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
        fila = m.todas()[0]
    finally:
        m.cerrar()
    assert r == {"hecho": 1, "fallido": 0}
    esperado = os.path.join(tanda.destino, "RGB", "PB1", "PB1_V1", "DJI_0001_D.JPG")
    assert fila["ruta_salida_original"] == esperado and os.path.isfile(esperado)
    assert not os.path.exists(os.path.join(tanda.destino, "SIN_ORDENAR"))


def test_el_giro_es_uno_por_vuelo(tanda):
    """Vuelo 2 con una foto ya colocada a 270º: las manuales (yaw 90) adoptan 270º;
    en un vuelo vacío el consenso se calcula con las fotos del grupo y queda en `vuelos`."""
    fotos = [("DJI_0001_D.JPG", _t(11, 5, 0)),      # dentro de la fila 1
             ("DJI_0002_D.JPG", _t(10, 20, 0)),     # grupo A (a mano)
             ("DJI_0003_D.JPG", _t(10, 40, 0))]     # grupo B (a mano)
    tanda.indexar(fotos, yaws={"DJI_0001_D.JPG": -90.0, "DJI_0002_D.JPG": 90.0,
                               "DJI_0003_D.JPG": 90.0})
    a, b = grupos_sin_colocar(tanda.destino)
    asignar_grupo(tanda.destino, a["grupo_id"], 1, quien="x")      # vuelo 2: ya a 270º
    asignar_grupo(tanda.destino, b["grupo_id"], 0, quien="x")      # vuelo 1: vacío
    filas = tanda.filas()
    assert filas["DJI_0001_D.JPG"]["angulo_giro"] == 270
    assert filas["DJI_0002_D.JPG"]["angulo_giro"] == 270
    assert filas["DJI_0003_D.JPG"]["angulo_giro"] == 90
    assert filas["DJI_0003_D.JPG"]["angulo_version"]
    m = tanda.manifiesto()
    try:
        vuelos = m.vuelos()
    finally:
        m.cerrar()
    assert vuelos[("1", "1", None)]["angulo"] == 90 and vuelos[("1", "1", None)]["n_total"] == 1


def test_la_asignacion_sobrevive_a_un_reindice_y_a_otra_tanda(tanda):
    tanda.indexar(FOTOS_HUECOS)
    a, b, c = grupos_sin_colocar(tanda.destino)
    asignar_grupo(tanda.destino, a["grupo_id"], 1, quien="x")
    antes = tanda.filas()["DJI_0001_D.JPG"]["ruta_salida_original"]

    # Re-índice del mismo origen (el manifiesto aún lo tiene pendiente): no se pisa.
    _cfg_, msgs = tanda.indexar(FOTOS_HUECOS)
    filas = tanda.filas()
    assert filas["DJI_0001_D.JPG"]["unassigned"] == 0 and filas["DJI_0001_D.JPG"]["vuelo"] == "2"
    assert filas["DJI_0001_D.JPG"]["ruta_salida_original"] == antes
    assert any("colocadas a mano" in str(m) for m in msgs)
    pend = grupos_sin_colocar(tanda.destino)
    assert sorted(g["n_fotos"] for g in pend) == [1, 2] and a["grupo_id"] not in {
        g["grupo_id"] for g in pend}

    # Tanda posterior con OTRO origen: las pendientes de la anterior no se pierden.
    otro = str(tanda.tmp / "origen2")
    tanda.indexar([("DJI_0100_D.JPG", _t(10, 50, 0))], origen=otro)
    grupos = grupos_sin_colocar(tanda.destino)
    assert sorted(g["n_fotos"] for g in grupos) == [1, 1, 2]
    assert tanda.filas()["DJI_0001_D.JPG"]["vuelo"] == "2"


def test_la_asignacion_sobrevive_aunque_el_apply_ya_corriera(tanda, make_dji_jpeg):
    tanda.indexar([("DJI_0001_D.JPG", _t(10, 20, 0))])
    g = grupos_sin_colocar(tanda.destino)[0]
    asignar_grupo(tanda.destino, g["grupo_id"], 0, quien="x")
    make_dji_jpeg(tanda.rutas["DJI_0001_D.JPG"])
    m = tanda.manifiesto()
    try:
        apply.aplicar_rgb(m, _cfg_apply(output_folder=tanda.destino), pipeline_real,
                          _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
    finally:
        m.cerrar()
    tanda.indexar([("DJI_0001_D.JPG", _t(10, 20, 0))])
    fila = tanda.filas()["DJI_0001_D.JPG"]
    assert fila["estado"] == "hecho" and fila["vuelo"] == "1" and fila["unassigned"] == 0


def test_si_la_fila_desaparece_del_estadillo_hay_error_visible(tanda):
    tanda.indexar(FOTOS_HUECOS)
    a = grupos_sin_colocar(tanda.destino)[0]
    asignar_grupo(tanda.destino, a["grupo_id"], 1, quien="x")
    # El estadillo ya no tiene el vuelo 2.
    _c, msgs = tanda.indexar(FOTOS_HUECOS, estadillo=ESTADILLO[:1])
    assert any("ERROR" in str(m) and "ya no existe" in str(m) for m in msgs)
    assert tanda.filas()["DJI_0001_D.JPG"]["unassigned"] == 1


def test_none_deja_el_grupo_en_sin_ordenar_y_queda_decidido(tanda):
    tanda.indexar(FOTOS_HUECOS)
    a = grupos_sin_colocar(tanda.destino)[0]
    res = asignar_grupo(tanda.destino, a["grupo_id"], None, quien="x")
    assert res["pendientes_apply"] == 0 and res["sin_cambio"] == 3
    assert tanda.filas()["DJI_0001_D.JPG"]["unassigned"] == 1
    assert a["grupo_id"] not in {g["grupo_id"] for g in grupos_sin_colocar(tanda.destino)}
    todos = {g["grupo_id"]: g for g in grupos_sin_colocar(tanda.destino, solo_pendientes=False)}
    assert todos[a["grupo_id"]]["estado"] == "sin_ordenar"
    # Tras un re-índice sigue decidido (mismo grupo, misma decisión).
    tanda.indexar(FOTOS_HUECOS)
    todos = {g["grupo_id"]: g for g in grupos_sin_colocar(tanda.destino, solo_pendientes=False)}
    assert todos[a["grupo_id"]]["estado"] == "sin_ordenar"
    # Se puede volver atrás mientras no se haya aplicado.
    asignar_grupo(tanda.destino, a["grupo_id"], 0, quien="y")
    assert tanda.filas()["DJI_0001_D.JPG"]["vuelo"] == "1"
    asignar_grupo(tanda.destino, a["grupo_id"], None, quien="y")
    f = tanda.filas()["DJI_0001_D.JPG"]
    assert f["unassigned"] == 1 and f["pb"] is None and "SIN_ORDENAR" in f["ruta_salida_original"]


def test_errores_explicitos_y_sin_escribir(tanda):
    tanda.indexar(FOTOS_HUECOS)
    a = grupos_sin_colocar(tanda.destino)[0]
    antes = {n: dict(f) for n, f in tanda.filas().items()}
    with pytest.raises(ErrorSinColocar, match="fila 99 no existe"):
        asignar_grupo(tanda.destino, a["grupo_id"], 99, quien="x")
    with pytest.raises(ErrorSinColocar, match="inexistente"):
        asignar_grupo(tanda.destino, "g-noexiste", 0, quien="x")
    with pytest.raises(ErrorSinColocar, match="quien"):
        asignar_grupo(tanda.destino, a["grupo_id"], 0, quien=" ")
    with pytest.raises(ErrorSinColocar, match="entero"):
        asignar_grupo(tanda.destino, a["grupo_id"], "1", quien="x")
    assert {n: dict(f) for n, f in tanda.filas().items()} == antes
    m = tanda.manifiesto()
    try:
        assert m.asignaciones_manuales() == {}
    finally:
        m.cerrar()
    with pytest.raises(ErrorSinColocar, match="no hay manifiesto"):
        grupos_sin_colocar(str(tanda.tmp / "vacio"))


def test_tras_el_apply_se_recolocan_las_de_sin_ordenar_y_no_las_ya_colocadas(tanda):
    tanda.indexar(FOTOS_HUECOS)
    a, b, _c = grupos_sin_colocar(tanda.destino)
    m = tanda.manifiesto()
    try:
        for f in m.todas():
            m.marcar_en_curso(f["id"])
            m.marcar_hecha(f["id"], "")
    finally:
        m.cerrar()
    # SIN_ORDENAR ya aplicado: se recoloca (la fila se reabre y apunta las rutas viejas).
    res = asignar_grupo(tanda.destino, a["grupo_id"], 1, quien="x")
    assert res["recolocadas"] == 3 and res["pendientes_apply"] == 3
    f = tanda.filas()["DJI_0001_D.JPG"]
    assert f["estado"] == "pendiente" and f["vuelo"] == "2" and f["previas_sin_ordenar"]
    assert "SIN_ORDENAR" in f["previas_sin_ordenar"]
    # Una vez aplicada en su nueva carpeta, moverla a otra fila es un error claro.
    m = tanda.manifiesto()
    try:
        m.marcar_en_curso(f["id"])
        m.marcar_hecha(f["id"], "")
    finally:
        m.cerrar()
    with pytest.raises(ErrorSinColocar, match="ya está colocada y aplicada"):
        asignar_grupo(tanda.destino, a["grupo_id"], 0, quien="x")
    # Idempotente sobre la misma fila.
    res = asignar_grupo(tanda.destino, a["grupo_id"], 1, quien="x")
    assert res["sin_cambio"] == 3 and res["pendientes_apply"] == 0


def test_apply_en_curso_es_error(tanda):
    tanda.indexar(FOTOS_HUECOS)
    a = grupos_sin_colocar(tanda.destino)[0]
    m = tanda.manifiesto()
    try:
        m.marcar_en_curso(tanda.filas()["DJI_0001_D.JPG"]["id"])
    finally:
        m.cerrar()
    with pytest.raises(ErrorSinColocar, match="apply en curso"):
        asignar_grupo(tanda.destino, a["grupo_id"], 1, quien="x")


def test_cli_listar_y_asignar(tanda, capsys):
    tanda.indexar(FOTOS_HUECOS)
    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "listar"]) == 0
    listado = json.loads(capsys.readouterr().out)
    assert len(listado["grupos"]) == 3 and "claves" not in listado["grupos"][0]
    gid = listado["grupos"][0]["grupo_id"]

    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "asignar",
                              "--grupo", gid, "--fila", "1", "--quien", "cli"]) == 0
    res = json.loads(capsys.readouterr().out)["resultado"]
    assert res["pendientes_apply"] == 3 and res["fila_estadillo"] == 1

    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "listar"]) == 0
    assert len(json.loads(capsys.readouterr().out)["grupos"]) == 2
    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "listar", "--todos",
                              "--incluir-fotos"]) == 0
    todos = json.loads(capsys.readouterr().out)["grupos"]
    assert len(todos) == 3 and "claves" in todos[0]

    gid2 = listado["grupos"][1]["grupo_id"]
    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "asignar",
                              "--grupo", gid2, "--fila", "ninguna", "--quien", "cli"]) == 0
    assert json.loads(capsys.readouterr().out)["resultado"]["fila_estadillo"] is None

    # Errores: código 2, mensaje por stderr, nada por stdout.
    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "asignar",
                              "--grupo", gid, "--fila", "77", "--quien", "cli"]) == 2
    salida = capsys.readouterr()
    assert salida.out == "" and "fila 77 no existe" in salida.err
    with pytest.raises(SystemExit):
        organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "asignar", "--grupo", gid])


def test_destino_gcs_lista_y_asigna_y_sube_el_manifiesto(tanda, bucket, tmp_path):
    from atom_core import almacen as almacen_mod
    tanda.indexar(FOTOS_HUECOS)
    almacen_mod.publicar_en(os.path.join(tanda.destino, ".organizado", "manifiesto.db"),
                            RAIZ_GCS + "/.organizado/manifiesto.db", if_generation_match=0)
    grupos = grupos_sin_colocar(RAIZ_GCS)
    assert [g["n_fotos"] for g in grupos] == [3, 2, 1]
    res = asignar_grupo(RAIZ_GCS, grupos[0]["grupo_id"], 1, quien="nube")
    assert res["pendientes_apply"] == 3
    local = tmp_path / "bajado.db"
    local.write_bytes(bucket.objetos[CLAVE_GCS])
    m = Manifiesto(local)
    try:
        filas = {f["nombre_original"]: f for f in m.todas()}
    finally:
        m.cerrar()
    assert filas["DJI_0001_D.JPG"]["ruta_salida_original"] == (
        RAIZ_GCS + "/RGB/PB1/PB1_V2/DJI_0001_D.JPG")
    # Un error no sube nada: la generación del objeto no cambia.
    generacion = bucket.generaciones[CLAVE_GCS]
    with pytest.raises(ErrorSinColocar):
        asignar_grupo(RAIZ_GCS, grupos[1]["grupo_id"], 99, quien="nube")
    assert bucket.generaciones[CLAVE_GCS] == generacion


def test_cli_salida_local_listar_asignar_y_error(tanda, capsys, tmp_path):
    tanda.indexar(FOTOS_HUECOS)
    sal = tmp_path / "sub" / "listado.json"
    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "listar",
                              "--sin-colocar-salida", str(sal)]) == 0
    out = json.loads(capsys.readouterr().out)
    en_fichero = json.loads(sal.read_text(encoding="utf-8"))
    assert en_fichero == out and out["ok"] is True
    assert out["zona_horaria"] == "exif-local" and len(out["grupos"]) == 3
    assert out["filas"] and {"ejecucion", "fila", "pb", "vuelo", "fecha",
                             "hora_inicio", "hora_fin"} <= set(out["filas"][0])
    assert not list(sal.parent.glob("*.tmp"))
    gid = out["grupos"][0]["grupo_id"]

    sal2 = tmp_path / "asignar.json"
    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "asignar", "--grupo", gid,
                              "--fila", "1", "--quien", "cli", "--sin-colocar-salida", str(sal2)]) == 0
    capsys.readouterr()
    r = json.loads(sal2.read_text(encoding="utf-8"))
    assert r["ok"] is True and r["resultado"]["pendientes_apply"] == 3

    sal3 = tmp_path / "error.json"
    assert organize_cli.main(["--destino", tanda.destino, "--sin-colocar", "asignar", "--grupo", gid,
                              "--fila", "77", "--quien", "cli", "--sin-colocar-salida", str(sal3)]) == 2
    capsys.readouterr()
    e = json.loads(sal3.read_text(encoding="utf-8"))
    assert e["ok"] is False and "fila 77 no existe" in e["error"]


def test_cli_salida_gcs(tanda, bucket, capsys, tmp_path):
    (tmp_path / "tmp").mkdir(exist_ok=True)  # la fixture `bucket` redirige gettempdir ahí
    tanda.indexar(FOTOS_HUECOS)
    destino = os.path.join(tanda.destino)
    salida = "gs://bucket-falso/salidas/sc.json"
    assert organize_cli.main(["--destino", destino, "--sin-colocar", "listar",
                              "--sin-colocar-salida", salida]) == 0
    out = json.loads(capsys.readouterr().out)
    assert json.loads(bucket.objetos["salidas/sc.json"].decode("utf-8")) == out
