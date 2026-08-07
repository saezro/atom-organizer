"""
Tests de dos cambios de la 3.4.x:

1. Las carpetas panorámicas (PBPano_V*) se procesan "tal cual", sin rotar y sin
   contar como error de rotación. Antes caían siempre al else del chequeo del
   max_error (un panorama es un barrido: nunca hay un giro dominante) y tumbaban
   el exit code de corridas por lo demás correctas.
2. gen_folder_struct no vuelve a leer el EXIF de la misma imagen una vez por
   vuelo del estadillo, y los moves van en paralelo. Son optimizaciones puras:
   el resultado tiene que ser idéntico al del recorrido en serie.
"""
import datetime
import os

import pandas as pd
import pytest

import utils
from pipeline import GenStructFolder
from utils import OrganizerLogger as ol


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_gsf(tmp_path):
    logger = ol("test_panorama", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    return GenStructFolder(logger)


# --- 1. Detección de carpeta panorámica ---------------------------------------

@pytest.mark.parametrize("nombre", ["PBPano_V1", "PBPano_V12", "PBPANO_V1", "PBpano_V2", "/ruta/a/PBPano_V1"])
def test_es_carpeta_panoramica_detecta_los_panoramas(nombre):
    assert utils.es_carpeta_panoramica(nombre) is True


@pytest.mark.parametrize("nombre", ["PB1_V1", "PBA_V1", "PB12_V3", "PBPanorama_V1", "CSVs", "TERMICA", ""])
def test_es_carpeta_panoramica_no_confunde_vuelos_normales(nombre):
    assert utils.es_carpeta_panoramica(nombre) is False


# --- 2. Un panorama no rota y no es error -------------------------------------

def _preparar_vuelo(tmp_path, nombre_vuelo, yaws):
    """Monta una carpeta de vuelo con N imágenes y fija el yaw que devolverá el EXIF."""
    root = tmp_path / "PLANTA"
    carpeta = root / "TERMICA" / nombre_vuelo
    carpeta.mkdir(parents=True)
    imagenes = []
    for i in range(len(yaws)):
        nombre = f"DJI_{i:04d}.JPG"
        (carpeta / nombre).write_bytes(b"FOTO")
        imagenes.append(nombre)

    gsf = _make_gsf(tmp_path)
    gsf.root_folder = str(root)
    gsf.csvs_root_folder = str(root / "CSVs")
    gsf.miniaturas_root_folder = str(root / "MINIATURAS")
    gsf.total_images_number = len(imagenes)
    gsf.utils_obj.get_images_from_dir = lambda folder, *a, **kw: list(imagenes)

    yaw_por_imagen = dict(zip(imagenes, yaws))
    gsf.exif_management_obj.get_gimbal_yaw_pitch = lambda path: (yaw_por_imagen[os.path.basename(path)], 0)
    return gsf, carpeta


def _degrees_del_videofiles(gsf, nombre_vuelo):
    csv = os.path.join(gsf.csvs_root_folder, utils.CRITERIO_DIRNAME, nombre_vuelo + "_Videofiles.csv")
    df = pd.read_csv(csv)
    return list(df["Degree"]) if not df.empty else []


def _correr_rotacion(gsf, carpeta):
    cb = FakeSignal()
    gsf.gen_thumbnails_and_rotate(str(carpeta), rgb_processing=False, max_error=95,
                                  lim_max_270=-45, lim_min_270=-135, lim_max_90=45, lim_min_90=-45,
                                  progress_callback=cb, progress_bar=cb)


def test_panorama_con_giros_dispares_no_es_error_y_no_rota(tmp_path):
    # Mezcla deliberada: 4 a 90º, 3 a 270º y 2 sin girar. Ninguna llega al 95%.
    yaws = [10, 10, 10, 10, -100, -100, -100, 180, 180]
    gsf, carpeta = _preparar_vuelo(tmp_path, "PBPano_V1", yaws)
    lotes_rotados = []
    gsf._rotate_images_batch = lambda *a, **kw: lotes_rotados.append(a[0])

    _correr_rotacion(gsf, carpeta)

    assert lotes_rotados == [], "Un panorama no debe rotar ninguna imagen"
    assert _degrees_del_videofiles(gsf, "PBPano_V1") == [0] * len(yaws), "El panorama debe quedar con Degree 0"
    assert gsf.error_gen_struct_folder == 0, "Un panorama no debe sumar errores"


def test_vuelo_normal_con_giros_dispares_sigue_siendo_error(tmp_path):
    # Mismo reparto de yaws, pero en un vuelo normal: el veredicto NO cambia.
    yaws = [10, 10, 10, 10, -100, -100, -100, 180, 180]
    gsf, carpeta = _preparar_vuelo(tmp_path, "PB1_V1", yaws)
    lotes_rotados = []
    gsf._rotate_images_batch = lambda *a, **kw: lotes_rotados.append(a[0])

    _correr_rotacion(gsf, carpeta)

    assert lotes_rotados == [], "En el caso de error no se rota nada"
    assert _degrees_del_videofiles(gsf, "PB1_V1") == [], "En el caso de error el Videofiles.csv queda vacío"


def test_vuelo_normal_con_giro_dominante_sigue_rotando(tmp_path):
    yaws = [10] * 20
    gsf, carpeta = _preparar_vuelo(tmp_path, "PB1_V1", yaws)
    lotes_rotados = []
    gsf._rotate_images_batch = lambda *a, **kw: lotes_rotados.append(a[0])

    _correr_rotacion(gsf, carpeta)

    assert len(lotes_rotados) == 1 and len(lotes_rotados[0]) == 20, "Un vuelo normal con giro dominante sí rota"
    assert _degrees_del_videofiles(gsf, "PB1_V1") == [90] * 20


# --- 3. Caché de timestamps EXIF ----------------------------------------------

def test_el_exif_de_cada_imagen_se_lee_una_sola_vez(tmp_path):
    carpeta = tmp_path / "TERMICA"
    carpeta.mkdir()
    for i in range(5):
        (carpeta / f"DJI_{i:04d}.jpg").write_bytes(b"FOTO")

    gsf = _make_gsf(tmp_path)
    lecturas = []

    def _fake_timestamp(path):
        lecturas.append(path)
        return datetime.datetime(2026, 3, 17, 12, 0, 0)

    gsf.exif_management_obj.get_timestamp_from_image = _fake_timestamp

    imagenes = sorted(os.listdir(carpeta))
    # Tres "vuelos" que recorren la misma lista completa, como hace gen_folder_struct.
    for _ in range(3):
        gsf.obtenerListaImagenesVuelo(str(carpeta), "2026:03:17", "11:00:00", "13:00:00", imagenes, 0, 0, 0)

    assert len(lecturas) == len(imagenes), (
        f"Se esperaba una lectura de EXIF por imagen ({len(imagenes)}), hubo {len(lecturas)}"
    )


def test_precargar_timestamps_evita_lecturas_posteriores(tmp_path):
    carpeta = tmp_path / "RGB"
    carpeta.mkdir()
    for i in range(10):
        (carpeta / f"DJI_{i:04d}.jpg").write_bytes(b"FOTO")

    gsf = _make_gsf(tmp_path)
    lecturas = []
    gsf.exif_management_obj.get_timestamp_from_image = lambda path: (
        lecturas.append(path) or datetime.datetime(2026, 3, 17, 12, 0, 0)
    )

    gsf.precargar_timestamps([str(carpeta)], FakeSignal())
    assert len(lecturas) == 10

    imagenes = sorted(os.listdir(carpeta))
    seleccion = gsf.obtenerListaImagenesVuelo(str(carpeta), "2026:03:17", "11:00:00", "13:00:00", imagenes, 0, 0, 0)

    assert len(lecturas) == 10, "Tras precargar no debe volver a leerse ningún EXIF"
    assert sorted(seleccion) == imagenes, "La selección por franja horaria debe ser la misma"


def test_la_seleccion_por_franja_horaria_es_la_misma_con_y_sin_cache(tmp_path):
    carpeta = tmp_path / "TERMICA"
    carpeta.mkdir()
    horas = {}
    for i in range(12):
        nombre = f"DJI_{i:04d}.jpg"
        (carpeta / nombre).write_bytes(b"FOTO")
        horas[nombre] = datetime.datetime(2026, 3, 17, 11, 30) + datetime.timedelta(minutes=i * 5)

    gsf = _make_gsf(tmp_path)
    gsf.exif_management_obj.get_timestamp_from_image = lambda path: horas[os.path.basename(path)]
    imagenes = sorted(horas)

    sin_cache = [n for n, h in sorted(horas.items())
                 if datetime.datetime(2026, 3, 17, 12, 0, 0) < h < datetime.datetime(2026, 3, 17, 12, 30, 0)]
    gsf.precargar_timestamps([str(carpeta)], FakeSignal())
    con_cache = gsf.obtenerListaImagenesVuelo(str(carpeta), "2026:03:17", "12:00:00", "12:30:00", imagenes, 0, 0, 0)

    assert con_cache == sin_cache, "La caché no puede cambiar qué imágenes caen en cada vuelo"


# --- 4. Moves en paralelo ------------------------------------------------------

def test_moverListaImagenes_en_paralelo_mueve_todo_y_cuenta_bien(tmp_path):
    origen = tmp_path / "origen"
    destino = tmp_path / "destino"
    origen.mkdir()
    destino.mkdir()
    esperado = {}
    for i in range(200):
        nombre = f"DJI_{i:04d}.jpg"
        contenido = f"FOTO_{i}".encode()
        (origen / nombre).write_bytes(contenido)
        esperado[nombre] = contenido

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = len(esperado)
    cb = FakeSignal()

    gsf.moverListaImagenes(str(origen), str(destino), sorted(esperado), cb, cb)

    assert os.listdir(origen) == [], "Deben haberse movido todas las imágenes"
    assert sorted(os.listdir(destino)) == sorted(esperado)
    for nombre, contenido in esperado.items():
        assert (destino / nombre).read_bytes() == contenido, f"Contenido corrupto en {nombre}"
    assert gsf.current_image_number == len(esperado), (
        f"El contador de progreso debe llegar a {len(esperado)}, quedó en {gsf.current_image_number}"
    )


def test_moverListaImagenes_respeta_el_stop(tmp_path):
    origen = tmp_path / "origen"
    destino = tmp_path / "destino"
    origen.mkdir()
    destino.mkdir()
    for i in range(20):
        (origen / f"DJI_{i:04d}.jpg").write_bytes(b"FOTO")

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 20
    gsf.set_stop(True)
    cb = FakeSignal()

    gsf.moverListaImagenes(str(origen), str(destino), sorted(os.listdir(origen)), cb, cb)

    assert os.listdir(destino) == [], "Con stop pedido no debe moverse nada"
