"""Calendario PREVIO (modal antes de Ejecutar): usa la lectura y la asignación del índice real
(`indice.leer_timestamps`, `indice.ventanas_de_estadillos`, `indice.ventana_de_imagen`)."""
import datetime
import os

import pytest

from atom_core import calendario_revision as cr
from atom_core import indice
from tests.test_estadillo_scoping_carpeta import _ExifDePrueba, _cfg, _crear_imagen

JPG = b"\xff\xd8abc\xff\xd9" + bytes(range(1, 40))   # no cuenta como «a medio copiar»


def _t(h, m=0, s=0):
    return datetime.datetime(2026, 9, 20, h, m, s)


def _estadillo(ruta, filas):
    """`filas`: (pb, vuelo, hora_inicio, hora_fin, piloto, equipo) el 2026:09:20."""
    os.makedirs(os.path.dirname(str(ruta)), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("PB;Vuelo;Fecha;Hora_de_inicio;Hora_final;Piloto;Equipo_de_vuelo\n")
        for pb, vuelo, ini, fin, piloto, equipo in filas:
            fh.write(f"{pb};{vuelo};2026:09:20;{ini};{fin};{piloto};{equipo}\n")
    return str(ruta)


def _calendario(cfg, origen, rutas, horas, cancelar=None):
    """`horas`: {ruta_imagen: datetime}. Misma ruta que el Api: leer -> ventanas -> construir."""
    lectura = indice.leer_timestamps(str(origen), cfg, exif=_ExifDePrueba(timestamps=horas), cancelar=cancelar)
    ventanas = indice.ventanas_de_estadillos(rutas, cfg, [f.ruta for f in lectura.fotos])
    return cr.construir_previo(ventanas, lectura.fotos, cfg), lectura


def _un_estadillo(tmp_path, filas, **cfg_extra):
    origen = tmp_path / "origen"
    est = _estadillo(origen / "A" / "estadillo.csv", filas)
    return origen, est, _cfg(tmp_path, origen, est, **cfg_extra)


FILAS = [("1", "1", "08:00:00", "08:30:00", "Ana", "M3T"),
         ("2", "2", "09:00:00", "09:30:00", "Luis", "M30T")]


def test_vuelos_con_dron_y_piloto_foto_dentro_y_libres_con_bordes_inclusivos(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, FILAS)
    a = _crear_imagen(str(origen / "A"), "DJI_0001_D.JPG", JPG)
    b = _crear_imagen(str(origen / "A"), "DJI_0001_T.JPG", JPG)
    borde = _crear_imagen(str(origen / "A"), "DJI_0002_D.JPG", JPG)
    fuera = _crear_imagen(str(origen / "A"), "DJI_0003_D.JPG", JPG)
    sin_hora = _crear_imagen(str(origen / "A"), "DJI_0004_D.JPG", JPG)
    j, lectura = _calendario(cfg, origen, [est], {a: _t(8, 10), b: _t(8, 10), borde: _t(8, 30), fuera: _t(8, 45)})
    assert j["ok"] and j["previo"] and j["zona_horaria"] == "exif-local"
    v1, v2 = j["vuelos"]
    assert (v1["equipo"], v1["piloto"], v1["pb"], v1["vuelo"]) == ("M3T", "Ana", "1", "1")
    assert v1["inicio"] == "2026-09-20T08:00:00" and v1["fin"] == "2026-09-20T08:30:00"
    assert v1["n_total"] == 3 and v2["n_total"] == 0 and v2["piloto"] == "Luis"
    assert [l["nombre"] for l in j["libres"]] == ["DJI_0003_D.JPG"]
    assert j["libres"][0]["ts"] == "2026-09-20T08:45:00" and j["libres"][0]["grupo_id"]
    assert any("sin hora EXIF" in x for x in j["avisos"])
    assert lectura.n_imagenes == 5


def test_sufijo_termico_configurado_distinto(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, FILAS, end_thermo_files="_TH")
    rgb = _crear_imagen(str(origen / "A"), "DJI_0001_T.JPG", JPG)    # con "_TH" NO es térmica
    th = _crear_imagen(str(origen / "A"), "DJI_0001_TH.JPG", JPG)
    j, _ = _calendario(cfg, origen, [est], {rgb: _t(8, 5), th: _t(8, 5)})
    cub = j["vuelos"][0]["horas"][0]
    assert (cub["n_rgb"], cub["n_t"]) == (1, 1)


def test_h30t_point_es_termica(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, FILAS)
    p0 = _crear_imagen(str(origen / "A"), "DJI_0001_T_point0.JPG", JPG)
    w = _crear_imagen(str(origen / "A"), "DJI_0001_W.JPG", JPG)
    j, lectura = _calendario(cfg, origen, [est], {p0: _t(8, 5), w: _t(8, 6)})
    cub = j["vuelos"][0]["horas"][0]
    assert (cub["n_rgb"], cub["n_t"]) == (1, 1)
    assert {f.nombre: f.tipo for f in lectura.fotos}["DJI_0001_T_point0.JPG"] == "TERMICA"


def test_rgb_extra_cuenta_como_rgb_igual_que_el_calendario_real(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, FILAS, end_rgb_extra_files="_Z")
    z = _crear_imagen(str(origen / "A"), "DJI_0001_Z.JPG", JPG)
    j, lectura = _calendario(cfg, origen, [est], {z: _t(8, 5)})
    assert lectura.fotos[0].tipo == "RGB_Extra"
    assert j["vuelos"][0]["horas"][0]["n_rgb"] == 1


def test_solape_con_generales_gana_generales(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, [
        ("1", "1", "08:00:00", "09:00:00", "Ana", "M3T"),
        ("GENERALES", "1", "08:20:00", "08:40:00", "Ana", "M3T")])
    sola = _crear_imagen(str(origen / "A"), "DJI_0001_D.JPG", JPG)
    solape = _crear_imagen(str(origen / "A"), "DJI_0002_D.JPG", JPG)
    j, _ = _calendario(cfg, origen, [est], {sola: _t(8, 10), solape: _t(8, 30)})
    pb, gen = j["vuelos"]
    assert gen["generales"] is True and pb["generales"] is False
    assert (pb["n_total"], gen["n_total"]) == (1, 1)
    # y es EXACTAMENTE lo que decide el índice
    ventanas = indice.ventanas_de_estadillos([est], cfg, [solape])
    assert indice.ventana_de_imagen(solape, _t(8, 30), ventanas)["pb"] == "GENERALES"


def test_dos_estadillos_en_carpetas_distintas_horas_solapadas(tmp_path):
    origen = tmp_path / "origen"
    est_a = _estadillo(origen / "PILOTO_A" / "estadillo.csv", [("1", "1", "08:00:00", "09:00:00", "Ana", "M3T")])
    est_b = _estadillo(origen / "PILOTO_B" / "estadillo.csv", [("7", "3", "08:00:00", "09:00:00", "Luis", "M30T")])
    cfg = _cfg(tmp_path, origen, est_a)
    fa = _crear_imagen(str(origen / "PILOTO_A"), "DJI_0001_D.JPG", JPG)
    fb = _crear_imagen(str(origen / "PILOTO_B"), "DJI_0001_D.JPG", JPG + b"x")
    j, _ = _calendario(cfg, origen, [est_a, est_b], {fa: _t(8, 30), fb: _t(8, 30)})
    va, vb = j["vuelos"]
    assert (va["pb"], va["piloto"], va["n_total"]) == ("1", "Ana", 1)
    assert (vb["pb"], vb["piloto"], vb["n_total"]) == ("7", "Luis", 1)    # sin contaminarse entre pilotos
    assert j["libres"] == []


def test_colision_pb_vuelo_con_fecha_distinta_lleva_sufijo_como_el_indice(tmp_path):
    origen = tmp_path / "origen"
    est_a = _estadillo(origen / "A" / "estadillo.csv", [("1", "1", "08:00:00", "09:00:00", "Ana", "M3T")])
    est_b = os.path.join(str(origen / "B"), "estadillo.csv")
    os.makedirs(os.path.dirname(est_b))
    with open(est_b, "w", encoding="utf-8") as fh:
        fh.write("PB;Vuelo;Fecha;Hora_de_inicio;Hora_final;Piloto;Equipo_de_vuelo\n"
                 "1;1;2026:09:21;08:00:00;09:00:00;Luis;M3T\n")
    cfg = _cfg(tmp_path, origen, est_a)
    ventanas = indice.ventanas_de_estadillos([est_a, est_b], cfg, [])
    sufijos = [w["sufijo"] for w in ventanas.todas]
    assert all(sufijos) and sufijos[0] != sufijos[1]
    j = cr.construir_previo(ventanas, [], cfg)
    assert [v["sufijo"] for v in j["vuelos"]] == sufijos


def test_origen_inexistente_es_error_explicito(tmp_path):
    cfg = _cfg(tmp_path, tmp_path / "origen", "")
    with pytest.raises(indice.ErrorOrigenInexistente, match="no existe"):
        indice.leer_timestamps(str(tmp_path / "no_esta"), cfg)


def test_origen_vacio_existente_no_es_error(tmp_path):
    (tmp_path / "vacio").mkdir()
    cfg = _cfg(tmp_path, tmp_path / "vacio", "")
    lectura = indice.leer_timestamps(str(tmp_path / "vacio"), cfg, exif=_ExifDePrueba())
    assert (lectura.n_imagenes, lectura.fotos) == (0, [])


def test_cancelar_corta_la_lectura(tmp_path):
    import threading
    from atom_core import cancelacion
    origen, est, cfg = _un_estadillo(tmp_path, FILAS)
    a = _crear_imagen(str(origen / "A"), "DJI_0001_D.JPG", JPG)
    ev = threading.Event()
    ev.set()
    with pytest.raises(cancelacion.RunCancelado):
        indice.leer_timestamps(str(origen), cfg, exif=_ExifDePrueba(timestamps={a: _t(8)}), cancelar=ev)
    assert not cancelacion.cancelado()   # la bandera global de los runs no se toca


def test_estadillo_sin_ventanas_validas_es_error(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, [("1", "1", "08:00:00", "", "Ana", "M3T")])
    ventanas = indice.ventanas_de_estadillos([est], cfg, [])
    with pytest.raises(cr.ErrorCalendarioPrevio):
        cr.construir_previo(ventanas, [], cfg)
    assert any("hora final" in a for a in ventanas.avisos)


def test_fila_sin_fin_se_avisa_pero_las_demas_se_pintan(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, [("1", "1", "08:00:00", "", "Ana", "M3T"), FILAS[1]])
    j = cr.construir_previo(indice.ventanas_de_estadillos([est], cfg, []), [], cfg)
    assert len(j["vuelos"]) == 1 and j["vuelos"][0]["pb"] == "2"
    assert any("PB 1" in a for a in j["avisos"])


# ---- Giro del calendario previo ----------------------------------------------------------------
def _giro(tmp_path, yaws, flight_yaws=None, **cfg_extra):
    """Un vuelo 1/1 (08:00-08:30) con una foto por yaw; devuelve (vuelo, JSON)."""
    origen, est, cfg = _un_estadillo(tmp_path, FILAS[:1], **cfg_extra)
    ventanas = indice.ventanas_de_estadillos([est], cfg, [])
    flight_yaws = flight_yaws or [None] * len(yaws)
    fotos = [indice.FotoLeida(str(origen / "A" / f"DJI_{i:04d}_D.JPG"), f"DJI_{i:04d}_D.JPG",
                              _t(8, 10, i % 60), "RGB", y, 0.0, fy)
             for i, (y, fy) in enumerate(zip(yaws, flight_yaws))]
    j = cr.construir_previo(ventanas, fotos, cfg)
    return j["vuelos"][0], j


def test_giro_90_por_consenso(tmp_path):
    v, j = _giro(tmp_path, [90.0] * 9 + [0.0])
    assert v["angulo"] == 90
    assert (v["horas"][0]["n_90"], v["horas"][0]["n_270"], v["horas"][0]["n_0"]) == (9, 0, 1)
    assert v["n_giro_desconocido"] == 0 and "_votos" not in v


def test_giro_270_por_consenso(tmp_path):
    v, _ = _giro(tmp_path, [-90.0] * 10)
    assert v["angulo"] == 270 and v["horas"][0]["n_270"] == 10


def test_giro_0_por_consenso_y_flight_yaw_manda(tmp_path):
    v, _ = _giro(tmp_path, [90.0] * 5, flight_yaws=[0.5] * 5)
    assert v["angulo"] == 0 and v["horas"][0]["n_0"] == 5


def test_fotos_sin_yaw_son_desconocidas_y_se_avisan(tmp_path):
    v, j = _giro(tmp_path, [None, None, 90.0, 90.0, 90.0, 90.0])
    assert v["angulo"] == 90 and v["n_giro_desconocido"] == 2
    assert v["horas"][0]["n_giro_desconocido"] == 2
    assert any("sin yaw" in a for a in j["avisos"])


def test_todas_sin_yaw_angulo_none_con_aviso(tmp_path):
    v, j = _giro(tmp_path, [None, None])
    assert v["angulo"] is None and v["n_giro_desconocido"] == 2
    assert any("ninguna foto trae yaw" in a for a in j["avisos"])


def test_sin_consenso_angulo_none_con_porcentajes(tmp_path):
    v, j = _giro(tmp_path, [90.0] * 5 + [0.0] * 5)
    assert v["angulo"] is None
    assert any("sin consenso" in a and "90º=50.0%" in a for a in j["avisos"])


def test_modo_manual_angulo_fijo_sin_votar(tmp_path):
    v, j = _giro(tmp_path, [0.0, None, 90.0], choose_mode_auto=False, gen_thumbnails_rotate_90=True)
    assert v["angulo"] == 90 and v["n_giro_desconocido"] == 0
    assert (v["horas"][0]["n_90"], v["horas"][0]["n_0"]) == (0, 0)
    v, _ = _giro(tmp_path / "b", [90.0], choose_mode_auto=False, gen_thumbnails_rotate_90=False)
    assert v["angulo"] == 0


def test_generales_expone_flag_y_no_gira(tmp_path):
    origen, est, cfg = _un_estadillo(tmp_path, [("GENERALES", "1", "08:00:00", "08:30:00", "Ana", "M3T")])
    ventanas = indice.ventanas_de_estadillos([est], cfg, [])
    foto = indice.FotoLeida(str(origen / "A" / "DJI_0001_D.JPG"), "DJI_0001_D.JPG", _t(8, 10), "RGB", 90.0, 0.0, None)
    j = cr.construir_previo(ventanas, [foto], cfg)
    v = j["vuelos"][0]
    assert v["generales"] is True and v["angulo"] is None and v["n_giro_desconocido"] == 0
    assert not any("giro" in a for a in j["avisos"])
