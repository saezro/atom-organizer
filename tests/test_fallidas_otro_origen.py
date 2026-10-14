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
    # Aviso visible, A sin tocar.
    assert "3 fallida(s) de otro origen ignoradas" in texto
    assert "2026_10_07_TUTLY" in texto
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


def test_cierre_fallidas_otro_origen_son_aviso_no_error(tmp_path):
    from atom_core.cierre import separar_problemas, verificar
    m, _ = _manifiesto_cierre(tmp_path)
    p = verificar(m, _cfg_cierre(tmp_path, r"C:\Users\Jonathan\Desktop\2026_10_08_TUTLY"))
    errores, avisos = separar_problemas(p)
    assert not [e for e in errores if "fallaron" in e]
    assert any("7072 fallida(s)/pendiente(s) de otro origen" in a and "2026_10_07_TUTLY" in a
               for a in avisos)
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
