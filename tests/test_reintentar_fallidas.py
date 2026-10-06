"""Reintento dirigido de fallidas: `solo_fallidas` reabre SOLO las filas
'fallido' del manifiesto (sin reescanear el origen), no toca las 'hecho',
registra `n_reintentadas` y rehace el cierre con el total."""
import os

from atom_core.manifiesto import (
    NOMBRE_CARPETA_MANIFIESTO, Manifiesto, fallidas_reintentables,
)

from tests.test_organizado_plan_apply_e2e import (  # noqa: F401  (fixtures + helpers)
    _HostDePrueba, _cfg, _correr, _inspeccion, _SignalFalsa, _fake_convert_dji_image_to_tif,
    _fake_run_exif_batch_local,
)


def _db(cfg):
    return os.path.join(cfg.output_folder, NOMBRE_CARPETA_MANIFIESTO, "manifiesto.db")


def test_reintento_procesa_solo_fallidas_y_el_cierre_las_cuenta(_inspeccion, logger, monkeypatch):
    cfg = _cfg(_inspeccion)
    host = _HostDePrueba(logger)
    pcb, _, _ = _correr(host, cfg, monkeypatch)

    manifiesto = Manifiesto(_db(cfg))
    manifiesto.crear_esquema()
    filas = manifiesto.todas()
    hechas_antes = {f["id"]: (f["estado"], f["verificacion"], f["ruta_salida_original"])
                    for f in filas if f["estado"] == "hecho"}
    objetivo = next(f for f in filas if f["tipo"] == "TERMICA" and f["estado"] == "hecho")
    manifiesto.marcar_fallida(objetivo["id"], "lectura truncada")
    manifiesto.cerrar()

    # Con la fallida pendiente de reintento, el destino la ofrece (mismo origen).
    assert fallidas_reintentables(cfg.output_folder, cfg.input_folder) == {
        "fallidas": 1, "mismo_origen": True}
    assert fallidas_reintentables(cfg.output_folder, "/otro/origen")["mismo_origen"] is False

    convertidas = []

    def _contar(input_folder, output_folder, image_name, *a, **k):
        convertidas.append(image_name)
        return _fake_convert_dji_image_to_tif(input_folder, output_folder, image_name, *a, **k)

    host2 = _HostDePrueba(logger)
    host2.solo_fallidas = True
    monkeypatch.setattr(host2.split_images_obj, "convert_dji_image_to_tif", _contar)
    monkeypatch.setattr(host2.split_images_obj, "_run_exif_batch_local", _fake_run_exif_batch_local)
    host2.organizar_plan_apply(cfg, _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    assert len(convertidas) == 1  # solo la fallida

    manifiesto = Manifiesto(_db(cfg))
    manifiesto.crear_esquema()
    filas = {f["id"]: f for f in manifiesto.todas()}
    assert filas[objetivo["id"]]["estado"] == "hecho"
    for id_, (estado, verif, ruta) in hechas_antes.items():
        assert (filas[id_]["estado"], filas[id_]["verificacion"],
                filas[id_]["ruta_salida_original"]) == (estado, verif, ruta)
    ultima = list(manifiesto.ejecuciones().values())[-1]
    assert ultima["n_reintentadas"] == 1
    assert ultima["n_nuevas"] == 0
    manifiesto.cerrar()


def test_reintento_que_vuelve_a_fallar_el_cierre_dice_error_no_avisos(_inspeccion, logger, monkeypatch):
    cfg = _cfg(_inspeccion)
    host = _HostDePrueba(logger)
    _correr(host, cfg, monkeypatch)
    manifiesto = Manifiesto(_db(cfg))
    manifiesto.crear_esquema()
    objetivo = next(f for f in manifiesto.todas() if f["tipo"] == "TERMICA")
    manifiesto.marcar_fallida(objetivo["id"], "lectura truncada")
    manifiesto.cerrar()

    def _revienta(*a, **k):
        raise OSError("lectura truncada otra vez")

    host2 = _HostDePrueba(logger)
    host2.solo_fallidas = True
    monkeypatch.setattr(host2.split_images_obj, "convert_dji_image_to_tif", _revienta)
    monkeypatch.setattr(host2.split_images_obj, "_run_exif_batch_local", _fake_run_exif_batch_local)
    pcb = _SignalFalsa()
    host2.organizar_plan_apply(cfg, pcb, _SignalFalsa(), _SignalFalsa())
    texto = "".join(str(v) for v in pcb.mensajes)
    assert "HA HABIDO ERRORES" in texto
    assert "HA HABIDO AVISOS" not in texto


# ---- auditoría v3.4.109: conteos por run, WAL sin -shm ---------------------

def _fila_sql(manifiesto, n, tipo, estado, ejecucion_id, motivo=None):
    con = manifiesto._conexion()
    with con:
        con.execute(
            "INSERT INTO imagenes (ruta_origen, clave, tipo, ruta_salida_original, estado, "
            "motivo_fallo, ejecucion_id) VALUES (?, ?, ?, 'x', ?, ?, ?)",
            (f"/o/{n}.jpg", f"k{n}", tipo, estado, motivo, ejecucion_id))


def test_conteo_y_fallidas_se_limitan_al_run_actual(tmp_path):
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    _fila_sql(m, 1, "RGB", "hecho", 2)
    _fila_sql(m, 2, "RGB", "fallido", 2, "truncada")
    _fila_sql(m, 3, "RGB", "pendiente", 1)   # tanda previa / otro shard
    _fila_sql(m, 4, "RGB", "fallido", 1, "vieja")
    _fila_sql(m, 5, "TERMICA", "pendiente", 3)  # otro shard
    run = m.conteo_por_tipos({"RGB"}, 2)
    assert (run["hecho"], run["fallido"], run["pendiente"]) == (1, 1, 0)
    assert m.fallidas_por_tipos({"RGB"}, 2) == [("/o/2.jpg", "truncada")]
    # Sin run: comportamiento anterior (todo).
    assert m.conteo_por_tipos({"RGB"})["pendiente"] == 1
    assert sum(m.conteo_por_tipos(None, 2).values()) == 2
    m.cerrar()


def test_reabrir_fallidas_adopta_el_run_y_no_duplica(tmp_path):
    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    _fila_sql(m, 1, "RGB", "fallido", 1, "vieja")
    assert m.reabrir_fallidas(5) == 1
    m.marcar_fallida(1, "otra vez")
    assert m.conteo_por_tipos({"RGB"}, 5)["fallido"] == 1  # una sola, no suma tandas
    assert m.conteo_por_tipos({"RGB"}, 1)["fallido"] == 0
    m.cerrar()


def test_fallidas_reintentables_wal_sin_shm_cae_a_select_normal(tmp_path, monkeypatch):
    import sqlite3
    from atom_core import manifiesto as mf
    cfg_out = tmp_path / "dest"
    (cfg_out / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True)
    m = Manifiesto(cfg_out / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db")
    m.crear_esquema()
    _fila_sql(m, 1, "RGB", "fallido", 1, "x")
    m.abrir_ejecucion("/mi/origen/", "t")
    m.cerrar()
    real = sqlite3.connect

    def _ro_falla(dsn, *a, **k):
        if "mode=ro" in str(dsn):
            raise sqlite3.OperationalError("unable to open database file")
        return real(dsn, *a, **k)

    monkeypatch.setattr(mf.sqlite3, "connect", _ro_falla)
    # destino_organizado también usa mode=ro: se parchea solo la lectura de fallidas.
    monkeypatch.setattr(mf, "destino_organizado", lambda c: True)
    info = fallidas_reintentables(cfg_out, "/mi/origen")
    assert info == {"fallidas": 1, "mismo_origen": True}


def test_fallidas_reintentables_devuelve_error_explicito(tmp_path, monkeypatch):
    import sqlite3
    from atom_core import manifiesto as mf
    cfg_out = tmp_path / "dest"
    (cfg_out / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True)
    (cfg_out / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db").write_bytes(b"no es sqlite" * 50)
    monkeypatch.setattr(mf, "destino_organizado", lambda c: True)
    info = fallidas_reintentables(cfg_out, "/o")
    assert info["fallidas"] == 0 and info["mismo_origen"] is False
    assert "no se pudo leer el manifiesto" in info["error"]
