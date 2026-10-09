"""Índice: ángulo del manifiesto previo versionado, aviso de paridad T/W y marca de cola JPG sin EOI."""
import datetime as dt
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))  # reutiliza los dobles de test_indice_organizado

from atom_core import cierre, indice as indice_mod
from atom_core.indice import (VERSION_ALGORITMO_GIRO, _avisar_jpeg_sin_eoi, _avisar_paridad_tw,
                              _leer_cabecera_y_cola, construir_indice)
from atom_core.manifiesto import FilaManifiesto
from test_indice_organizado import (_ExifDePrueba, _PipelineDePrueba, _Signal, _cfg,
                                    _crear_imagen, _escribir_estadillo, _manifiesto)


def _emisor():
    msgs = []
    return msgs, types.SimpleNamespace(emit=lambda m=None, *a, **k: msgs.append(m))


def _indexar(tmp_path, manifiesto, cfg, ruta):
    ts = dt.datetime(2024, 6, 1, 10, 5, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"):
                (dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0))}
    msgs, cb = _emisor()
    construir_indice(cfg, _PipelineDePrueba(ventanas), _ExifDePrueba(timestamps={ruta: ts}),
                     manifiesto, cb, _Signal(), _Signal())
    return msgs


def _montar(tmp_path):
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    return cfg, ruta, _manifiesto(tmp_path)


def _forzar_angulo(manifiesto, angulo, version):
    con = manifiesto._conexion()
    with con:
        con.execute("UPDATE imagenes SET angulo_giro = ?, angulo_version = ?", (angulo, version))


def test_la_fila_guarda_la_version_del_algoritmo(tmp_path):
    cfg, ruta, manifiesto = _montar(tmp_path)
    _indexar(tmp_path, manifiesto, cfg, ruta)
    assert manifiesto.angulos_versionados_por_vuelo() == {("1", "1"): (0, VERSION_ALGORITMO_GIRO)}
    manifiesto.cerrar()


@pytest.mark.parametrize("version", [None, "0-vieja"])
def test_angulo_previo_de_otra_version_se_recalcula_y_avisa(tmp_path, version):
    cfg, ruta, manifiesto = _montar(tmp_path)
    _indexar(tmp_path, manifiesto, cfg, ruta)
    _forzar_angulo(manifiesto, 90, version)

    msgs = _indexar(tmp_path, manifiesto, cfg, ruta)

    assert manifiesto.angulos_versionados_por_vuelo() == {("1", "1"): (0, VERSION_ALGORITMO_GIRO)}
    aviso = [m for m in msgs if isinstance(m, str) and "ángulo de giro cambia" in m]
    assert aviso and "90º" in aviso[0] and "a 0º" in aviso[0]
    manifiesto.cerrar()


def test_angulo_previo_de_la_version_actual_se_respeta(tmp_path):
    cfg, ruta, manifiesto = _montar(tmp_path)
    _indexar(tmp_path, manifiesto, cfg, ruta)
    _forzar_angulo(manifiesto, 90, VERSION_ALGORITMO_GIRO)

    msgs = _indexar(tmp_path, manifiesto, cfg, ruta)

    assert manifiesto.angulos_versionados_por_vuelo() == {("1", "1"): (90, VERSION_ALGORITMO_GIRO)}
    assert not [m for m in msgs if isinstance(m, str) and "ángulo de giro cambia" in m]
    manifiesto.cerrar()


def _fila(nombre, tipo, pb="1", vuelo="1", sin_eoi=None):
    return FilaManifiesto(
        ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None, pb=pb,
        vuelo=vuelo, nombre_nuevo="", angulo_giro=0, pct_recorte=None, comprime=False,
        ruta_salida_original=f"/o/{nombre}", ruta_salida_crop=None, ruta_salida_tiff=None,
        unassigned=False, jpeg_sin_eoi=sin_eoi)


def test_paridad_tw_avisa_por_vuelo_con_los_idx_huerfanos():
    filas = [_fila("DJI_20260901120000_0001_T.JPG", "TERMICA"),
             _fila("DJI_20260901120000_0001_W.JPG", "RGB"),
             _fila("DJI_20260901120000_0002_T.JPG", "TERMICA"),
             _fila("DJI_20260901120000_0003_W.JPG", "RGB"),
             _fila("DJI_20260901120000_0005_T.JPG", "TERMICA", vuelo="2")]
    msgs, cb = _emisor()
    huerfanas = _avisar_paridad_tw(filas, cb)
    assert huerfanas == [("1", "1", ["0002"], ["0003"]), ("1", "2", ["0005"], [])]
    assert len(msgs) == 2 and "0002" in msgs[0] and "0003" in msgs[0] and "AVISO" in msgs[0]


def test_paridad_tw_completa_no_avisa():
    msgs, cb = _emisor()
    assert _avisar_paridad_tw([_fila("DJI_20260901120000_0001_T.JPG", "TERMICA"),
                               _fila("DJI_20260901120000_0001_W.JPG", "RGB")], cb) == []
    assert msgs == []


def test_paridad_tw_acepta_nombre_ya_renombrado():
    from atom_core.pares import paridad_tw
    assert paridad_tw(["20260115_100000_DJI_0001_T.JPG", "20260115_100000_DJI_0001_W.JPG"]) == ([], [])


@pytest.fixture
def filtro_activo(monkeypatch):
    monkeypatch.setattr(indice_mod, "_FILTRO_ARCHIVO_A_MEDIAS", True)


def _jpeg_con_cola(tmp_path, nombre, cola):
    ruta = tmp_path / nombre
    ruta.write_bytes(b"\xff\xd8" + b"\x11" * 5000 + cola)
    return str(ruta)


def test_cola_con_eoi_y_relleno_no_se_marca(tmp_path, filtro_activo):
    ruta = _jpeg_con_cola(tmp_path, "ok.JPG", b"\xff\xd9" + b"\xab" * 4097)
    cab, a_medias, sin_eoi = _leer_cabecera_y_cola(ruta)
    assert (a_medias, sin_eoi) == (False, False)


def test_cola_sin_eoi_se_marca_pero_no_se_omite(tmp_path, filtro_activo):
    ruta = _jpeg_con_cola(tmp_path, "cortado.JPG", b"\x22" * 100)
    cab, a_medias, sin_eoi = _leer_cabecera_y_cola(ruta)
    assert (a_medias, sin_eoi) == (False, True)
    # La API histórica de 2 valores sigue igual.
    assert indice_mod._leer_cabecera(ruta)[1] is False


def test_cola_a_ceros_sigue_siendo_a_medias(tmp_path, filtro_activo):
    ruta = _jpeg_con_cola(tmp_path, "ceros.JPG", b"\x00" * 100)
    assert _leer_cabecera_y_cola(ruta)[1] is True


def test_no_jpg_no_se_comprueba(tmp_path, filtro_activo):
    p = tmp_path / "a.tiff"
    p.write_bytes(b"II*\x00" + b"\x11" * 100)
    assert _leer_cabecera_y_cola(str(p))[2] is None


def test_aviso_de_jpeg_sin_eoi_solo_cuenta_las_marcadas():
    msgs, cb = _emisor()
    n = _avisar_jpeg_sin_eoi([_fila("a.JPG", "RGB", sin_eoi=True), _fila("b.JPG", "RGB", sin_eoi=False),
                              _fila("c.JPG", "RGB", sin_eoi=None)], cb)
    assert n == 1 and "a.JPG" in msgs[0] and "b.JPG" not in msgs[0]


def test_apply_no_relee_si_el_indice_vio_eoi(monkeypatch):
    from atom_core import apply
    from atom_core import cancelacion
    cancelacion.limpiar()
    llamadas = []
    monkeypatch.setattr(apply, "_motivo_jpeg_truncado", lambda r: llamadas.append(r))

    class _M:
        def marcar_fallida(self, *a, **k):
            pass

    filas = [{"id": 1, "ruta_origen": "/x/1.jpg", "jpeg_sin_eoi": 0},
             {"id": 2, "ruta_origen": "/x/2.jpg", "jpeg_sin_eoi": 1},
             {"id": 3, "ruta_origen": "/x/3.jpg", "jpeg_sin_eoi": None}]
    apply._validar_jpeg_origen(_M(), filas, types.SimpleNamespace(emit=lambda *a, **k: None))
    assert sorted(llamadas) == ["/x/2.jpg", "/x/3.jpg"]


def test_cierre_escribe_el_sidecar_con_la_version(tmp_path):
    manifiesto = _manifiesto(tmp_path)
    fila = _fila("DJI_0001_T.JPG", "TERMICA")
    manifiesto.insertar_o_reabrir([FilaManifiesto(**{**fila.__dict__, "angulo_giro": 90,
                                                      "angulo_version": VERSION_ALGORITMO_GIRO})])
    for f in manifiesto.todas():
        manifiesto.marcar_hecha(f["id"], "")
    (tmp_path / "out").mkdir()
    cfg = types.SimpleNamespace(output_folder=str(tmp_path / "out"))
    cierre._emitir_csv_criterio(manifiesto, cfg, types.SimpleNamespace(emit=lambda *a, **k: None))
    sidecars = list((tmp_path / "out" / "CSVs" / "_criterio").glob("*.giro.json"))
    assert len(sidecars) == 1
    assert json.loads(sidecars[0].read_text()) == {"angulo": 90, "version_algoritmo": VERSION_ALGORITMO_GIRO}
    manifiesto.cerrar()


# --- Vuelo entero reabierto cuando cambia el ángulo (un solo ángulo por vuelo) ---

def _indexar_varias(manifiesto, cfg, rutas):
    ts = {r: dt.datetime(2024, 6, 1, 10, 5, i) for i, r in enumerate(rutas)}
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"):
                (dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0))}
    msgs, cb = _emisor()
    construir_indice(cfg, _PipelineDePrueba(ventanas), _ExifDePrueba(timestamps=ts),
                     manifiesto, cb, _Signal(), _Signal())
    return msgs


def _montar_dos(tmp_path):
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    cfg = _cfg(tmp_path)
    # La 2ª imagen la crea el test cuando toca (el índice escanea toda la carpeta).
    rutas = [_crear_imagen(cfg.input_folder, "DJI_0001_D.JPG"),
             os.path.join(cfg.input_folder, "DJI_0002_D.JPG")]
    return cfg, rutas, _manifiesto(tmp_path)


def _estado(manifiesto):
    return sorted(((f["estado"], f["angulo_giro"], f["angulo_version"]) for f in manifiesto.todas()), key=str)


@pytest.mark.parametrize("version", [None, "0-vieja"])
def test_cambio_de_angulo_reabre_el_vuelo_entero_y_no_repite_aviso(tmp_path, version):
    cfg, rutas, manifiesto = _montar_dos(tmp_path)
    _indexar_varias(manifiesto, cfg, rutas[:1])
    for f in manifiesto.todas():
        manifiesto.marcar_hecha(f["id"], "ok")
    _forzar_angulo(manifiesto, 90, version)  # fila hecha con ángulo viejo

    _crear_imagen(cfg.input_folder, "DJI_0002_D.JPG")
    msgs = _indexar_varias(manifiesto, cfg, rutas)  # llega una imagen nueva al vuelo

    assert _estado(manifiesto) == [("pendiente", 0, VERSION_ALGORITMO_GIRO)] * 2
    avisos = [m for m in msgs if isinstance(m, str) and "ángulo de giro cambia" in m]
    assert len(avisos) == 1 and "90º a 0º" in avisos[0] and "Se reabren 1 " in avisos[0]

    for f in manifiesto.todas():
        manifiesto.marcar_hecha(f["id"], "ok")
    msgs2 = _indexar_varias(manifiesto, cfg, rutas)
    assert not [m for m in msgs2 if isinstance(m, str) and "ángulo de giro cambia" in m]
    assert _estado(manifiesto) == [("hecho", 0, VERSION_ALGORITMO_GIRO)] * 2
    manifiesto.cerrar()


def test_mismo_angulo_con_version_vieja_no_reabre_solo_actualiza_version(tmp_path):
    cfg, rutas, manifiesto = _montar_dos(tmp_path)
    _indexar_varias(manifiesto, cfg, rutas[:1])
    for f in manifiesto.todas():
        manifiesto.marcar_hecha(f["id"], "ok")
    _forzar_angulo(manifiesto, 0, None)  # mismo ángulo que el recalculado
    _crear_imagen(cfg.input_folder, "DJI_0002_D.JPG")

    msgs = _indexar_varias(manifiesto, cfg, rutas)

    assert _estado(manifiesto) == [("hecho", 0, VERSION_ALGORITMO_GIRO),
                                   ("pendiente", 0, VERSION_ALGORITMO_GIRO)]
    assert not [m for m in msgs if isinstance(m, str) and "ángulo de giro cambia" in m]
    manifiesto.cerrar()


def test_cierre_no_escribe_sidecar_con_angulos_mezclados(tmp_path):
    manifiesto = _manifiesto(tmp_path)
    filas = []
    for nombre, angulo in (("DJI_0001_T.JPG", 90), ("DJI_0002_T.JPG", 0)):
        base = _fila(nombre, "TERMICA")
        filas.append(FilaManifiesto(**{**base.__dict__, "angulo_giro": angulo,
                                       "angulo_version": VERSION_ALGORITMO_GIRO}))
    manifiesto.insertar_o_reabrir(filas)
    (tmp_path / "out").mkdir()
    cfg = types.SimpleNamespace(output_folder=str(tmp_path / "out"))
    msgs, cb = _emisor()
    cierre._emitir_csv_criterio(manifiesto, cfg, cb)
    assert not list((tmp_path / "out" / "CSVs" / "_criterio").glob("*.giro.json"))
    assert any(isinstance(m, str) and "mezclados" in m for m in msgs)
    manifiesto.cerrar()
