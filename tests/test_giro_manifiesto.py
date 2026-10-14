"""El criterio de giro de un vuelo vive en la tabla `vuelos` del manifiesto
(`<raiz>/.organizado/manifiesto.db`), no en `*_Videofiles.csv`.

Cubre: escritura (`GenStructFolder.registrar_giro_vuelo`), lectura
(`SplitImages.read_auto_rotate_degree`), el CSV legado como SOLO lectura y el
error visible cuando no hay criterio (sin fallbacks silenciosos).
"""
import sqlite3
import types

import pytest

import pipeline
from atom_core.indice import VERSION_ALGORITMO_GIRO, _nombre_carpeta_vuelo
from tests.helpers_giro import csv_videofiles_existentes, leer_vuelos, ruta_manifiesto


def _cb():
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


def _gen(logger, raiz):
    obj = pipeline.GenStructFolder(logger)
    obj.root_folder = str(raiz)
    obj.csvs_root_folder = str(raiz / "CSVs")
    return obj


def _split(tmp_path):
    from utils import OrganizerLogger as ol
    return pipeline.SplitImages(ol("t_giro_man", log_dir=str(tmp_path / "L"), create_file_handler=False))


def _vuelo_termica(raiz, nombre="PB1_V1"):
    vuelo = raiz / "TERMICA" / nombre.split("_")[0] / nombre
    vuelo.mkdir(parents=True)
    return vuelo


# --------------------------------------------------------------- escritura

@pytest.mark.parametrize("grados,n_90,n_270,n_0,estado,motivo,pct_ganadora", [
    (270, 0, 9, 1, "ok", None, 90.0),                      # auto, 270
    (90, 10, 0, 0, "ok", None, 100.0),                     # auto / manual 90
    (0, 3, 3, 4, "ok", "carpeta panorámica, no se rota", 40.0),   # panorama
    (0, 4, 3, 3, "sin_consenso", "ningún sentido llega al 80%", 30.0),
])
def test_registrar_giro_vuelo_escribe_la_fila(tmp_path, logger, grados, n_90, n_270, n_0,
                                              estado, motivo, pct_ganadora):
    cb, mensajes = _cb()
    obj = _gen(logger, tmp_path)
    obj.registrar_giro_vuelo(str(tmp_path / "TERMICA" / "PB1_V1"), grados, 10, n_90, n_270, n_0,
                             estado, motivo, cb)

    fila = leer_vuelos(tmp_path)["PB1_V1"]
    assert (fila["pb"], fila["vuelo"], fila["sufijo"]) == ("1", "1", "")
    assert (fila["angulo"], fila["n_total"], fila["n_90"], fila["n_270"], fila["n_0"]) == (
        grados, 10, n_90, n_270, n_0)
    assert (fila["estado"], fila["motivo"]) == (estado, motivo)
    assert fila["pct_ganadora"] == pct_ganadora
    assert fila["version_algoritmo"] == pipeline.VERSION_GIRO_LEGADO
    assert fila["version_algoritmo"] != VERSION_ALGORITMO_GIRO  # el índice no la reutiliza
    assert mensajes == [] and obj.error_gen_struct_folder == 0
    assert not csv_videofiles_existentes(tmp_path)


@pytest.mark.parametrize("nombre", ["PB1_V01", "PB12_V3_20260814", "PBPano_V1", "PBA_V2"])
def test_la_clave_coincide_con_el_nombre_de_carpeta_del_indice(tmp_path, logger, nombre):
    cb, _ = _cb()
    obj = _gen(logger, tmp_path)
    obj.registrar_giro_vuelo(str(tmp_path / "TERMICA" / nombre), 90, 4, 4, 0, 0, "ok", None, cb)

    (clave, fila), = leer_vuelos(tmp_path).items()
    assert clave == nombre
    assert _nombre_carpeta_vuelo(fila["pb"], fila["vuelo"], True, fila["sufijo"] or None) == nombre


def test_registrar_giro_vuelo_reemplaza_la_fila_del_mismo_vuelo(tmp_path, logger):
    cb, _ = _cb()
    obj = _gen(logger, tmp_path)
    obj.registrar_giro_vuelo(str(tmp_path / "TERMICA" / "PB1_V1"), 90, 4, 4, 0, 0, "ok", None, cb)
    obj.registrar_giro_vuelo(str(tmp_path / "TERMICA" / "PB1_V1"), 270, 4, 0, 4, 0, "ok", None, cb)
    vuelos = leer_vuelos(tmp_path)
    assert list(vuelos) == ["PB1_V1"] and vuelos["PB1_V1"]["angulo"] == 270


def test_registrar_giro_vuelo_nombre_invalido_es_error_visible(tmp_path, logger):
    cb, mensajes = _cb()
    obj = _gen(logger, tmp_path)
    obj.registrar_giro_vuelo(str(tmp_path / "TERMICA" / "Otra_carpeta"), 90, 4, 4, 0, 0, "ok", None, cb)

    assert obj.error_gen_struct_folder == 1
    assert any("ERROR" in str(m) and "Otra_carpeta" in str(m) for m in mensajes)
    assert leer_vuelos(tmp_path) == {}


def test_registrar_giro_vuelo_fallo_de_sqlite_es_error_visible(tmp_path, logger):
    cb, mensajes = _cb()
    obj = _gen(logger, tmp_path)
    ruta = ruta_manifiesto(tmp_path)
    import os
    os.makedirs(os.path.dirname(ruta))
    with open(ruta, "wb") as f:
        f.write(b"esto no es una base sqlite " * 50)

    obj.registrar_giro_vuelo(str(tmp_path / "TERMICA" / "PB1_V1"), 90, 4, 4, 0, 0, "ok", None, cb)

    assert obj.error_gen_struct_folder == 1
    assert any("ERROR" in str(m) and "manifiesto" in str(m) for m in mensajes)


def test_la_rotacion_no_deja_ningun_videofiles_csv(tmp_path, logger, make_dji_jpeg):
    """Auto (con rotación) y manual: tras la fase solo hay fila en `vuelos`."""
    for modo in ("auto", "manual"):
        raiz = tmp_path / modo
        vuelo = raiz / "PB1_V01"
        vuelo.mkdir(parents=True)
        for i in (1, 2):
            make_dji_jpeg(str(vuelo / f"DJI_000{i}_T.JPG"), gimbal_yaw=-90.0)
        obj = _gen(logger, raiz)
        obj.total_images_number = 2
        obj.current_image_number = 0
        cb, _ = _cb()
        if modo == "auto":
            obj.gen_thumbnails_and_rotate(str(vuelo), rgb_processing=False, max_error=0,
                                          lim_max_270=-10, lim_min_270=-170, lim_max_90=170,
                                          lim_min_90=10, progress_callback=cb, progress_bar=cb)
        else:
            obj.gen_thumbnails_and_rotate_manual(str(vuelo), False, False, cb, cb)
        assert csv_videofiles_existentes(raiz) == []
        fila = leer_vuelos(raiz)["PB1_V01"]
        assert fila["angulo"] == 270 and fila["n_total"] == 2
    assert leer_vuelos(tmp_path / "manual")["PB1_V01"]["n_270"] == 2


def test_rotacion_manual_90_registra_n_90(tmp_path, logger, make_dji_jpeg):
    vuelo = tmp_path / "PB1_V01"
    vuelo.mkdir()
    make_dji_jpeg(str(vuelo / "DJI_0001_T.JPG"), gimbal_yaw=0.0)
    obj = _gen(logger, tmp_path)
    obj.total_images_number = 1
    obj.current_image_number = 0
    cb, _ = _cb()
    obj.gen_thumbnails_and_rotate_manual(str(vuelo), False, True, cb, cb)
    fila = leer_vuelos(tmp_path)["PB1_V01"]
    assert (fila["angulo"], fila["n_90"], fila["n_270"], fila["estado"], fila["motivo"]) == (
        90, 1, 0, "ok", "giro manual")


# ----------------------------------------------------------------- lectura

def _escribir_fila(tmp_path, logger, nombre, grados, n_90, n_270, n_0, estado="ok", motivo=None):
    cb, _ = _cb()
    _gen(logger, tmp_path).registrar_giro_vuelo(str(tmp_path / "TERMICA" / nombre), grados,
                                                n_90 + n_270 + n_0, n_90, n_270, n_0, estado, motivo, cb)


@pytest.mark.parametrize("grados,n_90,n_270", [(90, 4, 0), (270, 0, 4), (0, 0, 0)])
def test_lee_el_giro_desde_el_manifiesto(tmp_path, logger, grados, n_90, n_270):
    _escribir_fila(tmp_path, logger, "PB1_V1", grados, n_90, n_270, 4 - n_90 - n_270)
    obj = _split(tmp_path)
    cb, mensajes = _cb()
    assert obj.read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == grados
    assert mensajes == [] and obj.error_splitting_images == 0
    assert obj._criterio_giro_cache  # lectura válida: se memoiza


def test_lee_vuelo_con_sufijo_del_manifiesto(tmp_path, logger):
    _escribir_fila(tmp_path, logger, "PB1_V1_20260814", 90, 4, 0, 0)
    cb, _ = _cb()
    assert _split(tmp_path).read_auto_rotate_degree(str(_vuelo_termica(tmp_path, "PB1_V1_20260814")), cb) == 90


def test_el_manifiesto_manda_sobre_el_csv_legado(tmp_path, logger):
    _escribir_fila(tmp_path, logger, "PB1_V1", 270, 0, 4, 0)
    csvs = tmp_path / "CSVs" / pipeline.utils.CRITERIO_DIRNAME
    csvs.mkdir(parents=True)
    (csvs / "PB1_V1_Videofiles.csv").write_text("Degree\n90\n", encoding="utf-8")
    cb, _ = _cb()
    assert _split(tmp_path).read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == 270


def test_sin_consenso_en_manifiesto_no_rota_y_no_se_memoiza(tmp_path, logger):
    """Igual que el CSV legado vacío de un vuelo sin consenso: aviso, 0 y fallo (sin caché)."""
    _escribir_fila(tmp_path, logger, "PB1_V1", 0, 4, 3, 3, "sin_consenso", "ningún sentido llega al 80%")
    obj = _split(tmp_path)
    cb, mensajes = _cb()
    assert obj.read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == 0
    assert any("sin rotar" in str(m).lower() for m in mensajes)
    assert not obj._criterio_giro_cache


# --------------------------------------------------------- CSV legado (solo lectura)

def test_csv_legado_se_lee_y_no_se_escribe_nada(tmp_path):
    csvs = tmp_path / "CSVs" / pipeline.utils.CRITERIO_DIRNAME
    csvs.mkdir(parents=True)
    csv = csvs / "PB1_V1_Videofiles.csv"
    csv.write_text("New Name,Original Name,Degree\na,b,90\n", encoding="utf-8")
    antes = csv.read_bytes()
    obj = _split(tmp_path)
    cb, mensajes = _cb()

    assert obj.read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == 90

    assert mensajes == [] and obj.error_splitting_images == 0
    assert csv.read_bytes() == antes
    assert not (tmp_path / ".organizado").exists(), "Leer el CSV legado no debe crear manifiesto"


# ------------------------------------------------------------ error visible

def test_sin_manifiesto_ni_csv_es_error_visible(tmp_path):
    obj = _split(tmp_path)
    cb, mensajes = _cb()

    assert obj.read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == 0

    assert obj.error_splitting_images == 1, "El error debe quedar registrado"
    assert any("ERROR" in str(m) and "PB1_V1" in str(m) and "manifiesto" in str(m) for m in mensajes)
    assert not obj._criterio_giro_cache, "El fallo no se memoiza"


def test_manifiesto_con_otro_vuelo_y_sin_csv_es_error_visible(tmp_path, logger):
    _escribir_fila(tmp_path, logger, "PB2_V1", 90, 4, 0, 0)
    obj = _split(tmp_path)
    cb, mensajes = _cb()
    assert obj.read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == 0
    assert obj.error_splitting_images == 1
    assert any("ERROR" in str(m) for m in mensajes)


def test_manifiesto_ilegible_es_error_visible_y_no_cae_al_csv(tmp_path):
    import os
    ruta = ruta_manifiesto(tmp_path)
    os.makedirs(os.path.dirname(ruta))
    with open(ruta, "wb") as f:
        f.write(b"esto no es una base sqlite " * 50)
    # Aunque haya CSV legado, un manifiesto roto NO se esquiva en silencio.
    csvs = tmp_path / "CSVs" / pipeline.utils.CRITERIO_DIRNAME
    csvs.mkdir(parents=True)
    (csvs / "PB1_V1_Videofiles.csv").write_text("Degree\n90\n", encoding="utf-8")
    obj = _split(tmp_path)
    cb, mensajes = _cb()

    assert obj.read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == 0

    assert obj.error_splitting_images == 1
    assert any("ERROR" in str(m) and "manifiesto" in str(m) for m in mensajes)
    assert not obj._criterio_giro_cache


def test_manifiesto_sin_tabla_vuelos_sigue_con_el_csv_legado(tmp_path):
    """Manifiesto anterior a la tabla `vuelos`: es ausencia de dato, no fallo de sqlite."""
    import os
    ruta = ruta_manifiesto(tmp_path)
    os.makedirs(os.path.dirname(ruta))
    sqlite3.connect(ruta).close()
    csvs = tmp_path / "CSVs"
    csvs.mkdir()
    (csvs / "PB1_V1_Videofiles.csv").write_text("Degree\n270\n", encoding="utf-8")
    obj = _split(tmp_path)
    cb, _ = _cb()
    assert obj.read_auto_rotate_degree(str(_vuelo_termica(tmp_path)), cb) == 270
    assert obj.error_splitting_images == 0
