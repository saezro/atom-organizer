"""Destino acumulado: las fallidas de OTRO origen (p. ej. el día anterior, cuyas
rutas ya no existen) no se reintentan, no sustituyen la corrida normal del
origen actual y no entran en ERRORES_*.csv; se avisa en el log."""
import os

import pytest

from atom_core.manifiesto import (
    NOMBRE_CARPETA_MANIFIESTO, Manifiesto, fallidas_reintentables, ruta_bajo_origen,
)

from tests.test_organizado_plan_apply_e2e import (  # noqa: F401  (fixtures + helpers)
    _HostDePrueba, _cfg, _correr, _inspeccion, _SignalFalsa, _fake_convert_dji_image_to_tif,
    _fake_run_exif_batch_local,
)

ORIGEN_A = r"C:\Users\Jonathan\Desktop\2026_10_07_TUTLY"
RUTA_A = ORIGEN_A + r"\DCIM1\DJI_0001_T.JPG"


def _sembrar_fallidas_de_a(cfg, n=3):
    os.makedirs(os.path.join(cfg.output_folder, NOMBRE_CARPETA_MANIFIESTO), exist_ok=True)
    m = Manifiesto(os.path.join(cfg.output_folder, NOMBRE_CARPETA_MANIFIESTO, "manifiesto.db"))
    m.crear_esquema()
    eid = m.abrir_ejecucion(ORIGEN_A, "t")
    con = m._conexion()
    with con:
        for i in range(n):
            con.execute(
                "INSERT INTO imagenes (ruta_origen, clave, tipo, ruta_salida_original, estado, "
                "motivo_fallo, ejecucion_id) VALUES (?, ?, 'TERMICA', 'x', 'fallido', "
                "'WinError 3', ?)", (RUTA_A.replace("0001", f"{i:04d}"), f"kA{i}", eid))
    m.cerrar_ejecucion(eid, n, 0, 0)
    m.cerrar()


@pytest.mark.parametrize("solo_fallidas", [True, False])
def test_fallidas_de_otro_origen_no_bloquean_la_corrida_normal(
        _inspeccion, logger, monkeypatch, solo_fallidas):
    cfg = _cfg(_inspeccion)
    os.makedirs(cfg.output_folder, exist_ok=True)
    _sembrar_fallidas_de_a(cfg)

    host = _HostDePrueba(logger)
    host.solo_fallidas = solo_fallidas
    pcb, _, _ = _correr(host, cfg, monkeypatch)
    texto = "".join(str(v) for v in pcb.mensajes)

    # B (origen actual) organizado entero.
    assert os.path.isdir(os.path.join(cfg.output_folder, "TERMICA", "PB1", "PB1_V1"))
    assert os.path.isdir(os.path.join(cfg.output_folder, "RGB", "PB1", "PB1_V1"))
    # Una sola línea informativa neutra sobre A; sin aviso ni error; A sin tocar.
    assert texto.count("de otra carpeta") == 1
    assert "2026_10_07_TUTLY" in texto
    assert "HA HABIDO AVISOS" not in texto and "HA HABIDO ERRORES" not in texto
    assert "de otro origen" not in texto
    m = Manifiesto(os.path.join(cfg.output_folder, NOMBRE_CARPETA_MANIFIESTO, "manifiesto.db"))
    m.crear_esquema()
    filas = m.todas()
    a = [f for f in filas if f["ruta_origen"].startswith(ORIGEN_A)]
    assert len(a) == 3 and all(f["estado"] == "fallido" for f in a)
    propias = [f for f in filas if not f["ruta_origen"].startswith(ORIGEN_A)]
    assert propias and all(f["estado"] == "hecho" for f in propias)
    m.cerrar()
    # CSV de errores sin A (o inexistente).
    logs = os.path.join(cfg.output_folder, "LOGS")
    for raiz, _d, ficheros in os.walk(logs):
        for fn in ficheros:
            if fn.startswith("ERRORES_"):
                with open(os.path.join(raiz, fn), encoding="utf-8-sig") as fh:
                    assert "2026_10_07_TUTLY" not in fh.read()


def test_reabrir_fallidas_y_conteo_filtran_por_origen_windows(tmp_path):
    cfg = type("C", (), {})()
    cfg.output_folder = str(tmp_path)
    _sembrar_fallidas_de_a(cfg, 2)
    # Mayúsculas/separadores distintos: misma carpeta.
    assert fallidas_reintentables(cfg.output_folder, ORIGEN_A.lower() + "\\")["fallidas"] == 2
    assert fallidas_reintentables(cfg.output_folder, ORIGEN_A.replace("\\", "/"))["fallidas"] == 2
    # Otro día: ninguna fallida suya.
    otro = r"C:\Users\Jonathan\Desktop\2026_10_08_TUTLY"
    assert fallidas_reintentables(str(tmp_path), otro)["fallidas"] == 0
    m = Manifiesto(os.path.join(str(tmp_path), NOMBRE_CARPETA_MANIFIESTO, "manifiesto.db"))
    m.crear_esquema()
    assert m.reabrir_fallidas(9, otro) == 0
    assert len(m.fallidas_de_otro_origen(otro)) == 2
    assert m.reabrir_fallidas(9, ORIGEN_A) == 2
    m.cerrar()


def test_ruta_bajo_origen_no_confunde_prefijos():
    assert ruta_bajo_origen(r"C:\X\Dia1\a.jpg", r"c:\x\dia1")
    assert not ruta_bajo_origen(r"C:\X\Dia10\a.jpg", r"C:\X\Dia1")
    assert not ruta_bajo_origen(r"C:\X\a.jpg", "")


def _manifiesto_cierre(tmp_path):
    m = Manifiesto(os.path.join(str(tmp_path), "m.db"))
    m.crear_esquema()
    eid = m.abrir_ejecucion(ORIGEN_A, "t")
    con = m._conexion()
    with con:
        for i in range(7072):
            con.execute(
                "INSERT INTO imagenes (ruta_origen, clave, tipo, ruta_salida_original, estado, "
                "motivo_fallo, ejecucion_id) VALUES (?, ?, 'TERMICA', 'x', 'fallido', "
                "'WinError 3', ?)", (ORIGEN_A + f"\\DCIM1\\DJI_{i:05d}_T.JPG", f"kA{i}", eid))
    return m, eid


def _cfg_cierre(tmp_path, origen):
    c = type("C", (), {})()
    c.input_folder, c.output_folder = origen, str(tmp_path)
    return c


def test_cierre_fallidas_otro_origen_ni_aviso_ni_error(tmp_path):
    from atom_core.cierre import separar_problemas, verificar
    m, _ = _manifiesto_cierre(tmp_path)
    p = verificar(m, _cfg_cierre(tmp_path, r"C:\Users\Jonathan\Desktop\2026_10_08_TUTLY"))
    errores, avisos = separar_problemas(p)
    assert not [e for e in errores if "fallaron" in e]
    assert not avisos and not errores
    m.cerrar()


def test_cierre_fallida_del_origen_actual_sigue_siendo_error(tmp_path):
    from atom_core.cierre import separar_problemas, verificar
    m, _ = _manifiesto_cierre(tmp_path)
    b = r"C:\Users\Jonathan\Desktop\2026_10_08_TUTLY"
    eid = m.abrir_ejecucion(b, "t")
    con = m._conexion()
    with con:
        con.execute(
            "INSERT INTO imagenes (ruta_origen, clave, tipo, ruta_salida_original, estado, "
            "motivo_fallo, ejecucion_id) VALUES (?, 'kB', 'TERMICA', 'x', 'fallido', 'boom', ?)",
            (b + r"\DCIM1\DJI_0001_T.JPG", eid))
    errores, _ = separar_problemas(verificar(m, _cfg_cierre(tmp_path, b)))
    assert any(e.startswith("ERROR: 1 imagen(es) fallaron") for e in errores)
    m.cerrar()


def test_cierre_origen_vacio_todas_cuentan_como_error(tmp_path):
    from atom_core.cierre import separar_problemas, verificar
    m, _ = _manifiesto_cierre(tmp_path)
    errores, _ = separar_problemas(verificar(m, _cfg_cierre(tmp_path, "")))
    assert any("7072 imagen(es) fallaron" in e for e in errores)
    m.cerrar()


B = r"C:\Users\Jonathan\Desktop\2026_10_08_TUTLY"


def _insertar(m, ruta, estado, clave, tipo="TERMICA", pb="PB1", vuelo="V1"):
    eid = m.abrir_ejecucion(ruta, "t")
    with m._conexion() as con:
        con.execute(
            "INSERT INTO imagenes (ruta_origen, clave, tipo, ruta_salida_original, estado, "
            "pb, vuelo, ejecucion_id) VALUES (?, ?, ?, 'x', ?, ?, ?, ?)",
            (ruta, clave, tipo, estado, pb, vuelo, eid))


def _mixto(tmp_path):
    m = Manifiesto(os.path.join(str(tmp_path), "m.db"))
    m.crear_esquema()
    _insertar(m, ORIGEN_A + r"\DCIM1\a1_T.JPG", "fallido", "a1")
    _insertar(m, ORIGEN_A + r"\DCIM1\a2_T.JPG", "pendiente", "a2")
    _insertar(m, ORIGEN_A + r"\DCIM1\a3_T.JPG", "en_curso", "a3")
    _insertar(m, B + r"\DCIM1\b1_T.JPG", "pendiente", "b1")
    _insertar(m, B + r"\DCIM1\b2_T.JPG", "hecho", "b2")
    return m


def test_pendientes_solo_del_origen_actual(tmp_path):
    m = _mixto(tmp_path)
    assert len(m.pendientes()) == 2
    assert [f["clave"] for f in m.pendientes(origen=B)] == ["b1"]
    assert [f["clave"] for f in m.pendientes(origen=ORIGEN_A)] == ["a2"]
    assert len(m.pendientes(origen="")) == 2
    m.cerrar()


def test_reabrir_huerfanas_limitado_al_origen(tmp_path):
    m = _mixto(tmp_path)
    assert m.reabrir_huerfanas(B) == 0
    assert m.resumen()["en_curso"] == 1
    assert m.reabrir_huerfanas(ORIGEN_A) == 1
    m.cerrar()


def test_resumen_para_cancelacion_solo_origen_actual(tmp_path):
    m = _mixto(tmp_path)
    r = m.resumen(B)
    assert r["hecho"] == 1 and r["pendiente"] == 1 and r["fallido"] == 0
    assert sum(r.values()) == 2
    assert sum(m.resumen().values()) == 5
    m.cerrar()


def test_cierre_mixto_sin_avisos_por_otro_origen(tmp_path):
    from atom_core.cierre import separar_problemas, verificar
    m = _mixto(tmp_path)
    with m._conexion() as con:
        con.execute("DELETE FROM imagenes WHERE clave LIKE 'b%'")  # B sin filas: solo A ajena
    cfg = _cfg_cierre(tmp_path, B)
    cfg.convert_to_tif = cfg.cropping_rgb = cfg.gen_meta_location = False
    cfg.calculate_proyected_distance, cfg.flight_height = False, 0
    errores, avisos = separar_problemas(verificar(m, cfg))
    assert not [x for x in errores + avisos if "2026_10_07_TUTLY" in x]
    assert not [x for x in errores if "pendiente" in x or "fallaron" in x]
    assert not [x for x in avisos if "otro origen" in x]
    m.cerrar()


def test_guard_termico_y_sin_vuelo_ignoran_otro_origen(tmp_path):
    from atom_core import apply
    m = _mixto(tmp_path)
    cfg = _cfg_cierre(tmp_path, B)
    filas = [f for f in m.todas() if f["estado"] in ("pendiente", "en_curso")
             and apply._fila_del_origen(f, cfg.input_folder)]
    assert [f["clave"] for f in filas] == ["b1"]
    m.cerrar()


def test_guard_sin_vuelo_en_raiz_ignora_filas_de_otro_origen(tmp_path):
    from types import SimpleNamespace
    import pytest as _pt
    from atom_core.manifiesto import FilaManifiesto
    from tests.test_guard_termicas_sin_girar import _Cb, _fake_phases, _fila_t, _manifiesto as _mf
    raiz = tmp_path / "destino"
    (raiz / "TERMICA").mkdir(parents=True)
    (raiz / "TERMICA" / "DJI_0001_T.JPG").write_bytes(b"x")  # homónimo del día 7
    _mf(raiz / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db",
        [_fila_t(raiz, "/dia7/DJI_0001_T.JPG")]).cerrar()
    fake, _ = _fake_phases(tmp_path)
    # Run del día 8: la fila del día 7 no cuenta.
    fake._exigir_sin_vuelo_asignado_en_raiz(
        SimpleNamespace(output_folder=str(raiz), input_folder="/dia8"), _Cb())
    # Run del día 7: sí cuenta (sigue siendo error).
    with _pt.raises(ValueError, match="DJI_0001_T.JPG"):
        fake._exigir_sin_vuelo_asignado_en_raiz(
            SimpleNamespace(output_folder=str(raiz), input_folder="/dia7"), _Cb())


def test_linea_info_ajenas_neutra(tmp_path):
    from atom_core.cierre import linea_info_ajenas, separar_problemas, verificar
    m, _ = _manifiesto_cierre(tmp_path)
    cfg = _cfg_cierre(tmp_path, B)
    linea = linea_info_ajenas(m, cfg)
    assert "7072" in linea and "otra carpeta" in linea and "2026_10_07_TUTLY" in linea
    assert linea_info_ajenas(m, _cfg_cierre(tmp_path, ORIGEN_A)) is None
    assert linea_info_ajenas(m, _cfg_cierre(tmp_path, "")) is None
    m.cerrar()
