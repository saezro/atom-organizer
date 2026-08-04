"""
La rama de 90 del criterio automático rotaba en el sentido contrario.

`gen_thumbnails_and_rotate` decide entre tres buckets (270 / 90 / no rotar) y
llama a `rotate_and_save` con la constante de PIL correspondiente. PIL rota en
sentido ANTIHORARIO, así que para corregir un yaw de -90 hay que pasarle
`Image.ROTATE_90` y para corregir un yaw de +90, `Image.ROTATE_270`. La rama de
270 (pipeline.py:1227) lo hacía bien; la de 90 (pipeline.py:1244) pasaba también
`Image.ROTATE_90`, contradiciendo su propio comentario de la línea 1239.

La referencia autoritativa es el modo MANUAL (pipeline.py:1355-1358), que sí
distingue: `rotation_value_90` -> ROTATE_270, si no -> ROTATE_90.

Segundo fallo del mismo bloque: el modo manual escribía `Degree: 270` fijo en
`_Videofiles.csv` aunque hubiera rotado 90. Ese CSV es justo el que lee después
el criterio de giro del TIFF (ver test_tiff_rotacion_auto.py), así que un vuelo
rotado a mano 90 acababa girando el TIFF al revés.
"""
import os
import types

import pandas as pd
from PIL import Image


def _recording_progress():
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


def _flight_folder(tmp_path, make_dji_jpeg, yaw):
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    make_dji_jpeg(str(flight_folder / "DJI_0001_T.JPG"), gimbal_yaw=yaw)
    make_dji_jpeg(str(flight_folder / "DJI_0002_T.JPG"), gimbal_yaw=yaw)
    return planta_folder, flight_folder


def _obj_con_espia(logger, planta_folder):
    """GenStructFolder listo para usar, que anota los `degrees` de cada rotate_and_save."""
    import pipeline as gen_struct_folder

    obj = gen_struct_folder.GenStructFolder(logger)
    obj.root_folder = str(planta_folder)
    obj.miniaturas_root_folder = str(planta_folder / "MINIATURAS")
    os.makedirs(obj.miniaturas_root_folder)

    grados = []
    original = obj.compress_image_obj.rotate_and_save

    def espia(image_name, input_folder, output_folder, degrees, *args, **kwargs):
        grados.append(degrees)
        return original(image_name, input_folder, output_folder, degrees, *args, **kwargs)

    obj.compress_image_obj.rotate_and_save = espia
    return obj, grados


def _leer_degrees(obj):
    csv_path = os.path.join(obj.miniaturas_root_folder, "PB1_V01_miniaturas", "PB1_V01_Videofiles.csv")
    return set(pd.read_csv(csv_path)["Degree"].unique())


# ---------------------------------------------------------------- modo automático

def test_bucket_90_rota_en_el_sentido_correcto(tmp_path, logger, make_dji_jpeg):
    """Yaw +90 debe corregirse con ROTATE_270, no con ROTATE_90."""
    planta_folder, flight_folder = _flight_folder(tmp_path, make_dji_jpeg, yaw=90.0)
    obj, grados = _obj_con_espia(logger, planta_folder)

    progress, _ = _recording_progress()
    obj.total_images_number = 2
    obj.current_image_number = 0
    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=0,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    assert _leer_degrees(obj) == {90}, "El vuelo no cayó en el bucket de 90; el test no prueba nada."
    assert set(grados) == {Image.ROTATE_270}, (
        f"El bucket de 90 rotó con {grados[0]!r}: PIL rota antihorario, así que un yaw "
        "de +90 se corrige con ROTATE_270. Con ROTATE_90 la imagen sale a 180º de donde debe."
    )


def test_bucket_270_sigue_rotando_como_antes(tmp_path, logger, make_dji_jpeg):
    """Control: el bucket de 270, que ya era correcto, no se toca."""
    planta_folder, flight_folder = _flight_folder(tmp_path, make_dji_jpeg, yaw=-90.0)
    obj, grados = _obj_con_espia(logger, planta_folder)

    progress, _ = _recording_progress()
    obj.total_images_number = 2
    obj.current_image_number = 0
    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=0,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    assert _leer_degrees(obj) == {270}
    assert set(grados) == {Image.ROTATE_90}, (
        "El bucket de 270 dejó de rotar con ROTATE_90: la corrección de la rama de 90 "
        "no debía tocar esta."
    )


def test_los_dos_buckets_rotan_en_sentidos_opuestos(tmp_path, logger, make_dji_jpeg):
    """
    El invariante de fondo, independiente de qué constante concreta se use: dos
    vuelos con yaw opuesto no pueden corregirse con la MISMA rotación. Es lo que
    hacía que el bug pasara desapercibido leyendo cada rama por separado.
    """
    grados_por_yaw = {}
    for yaw in (90.0, -90.0):
        planta_folder, flight_folder = _flight_folder(tmp_path / f"y{int(yaw)}", make_dji_jpeg, yaw)
        obj, grados = _obj_con_espia(logger, planta_folder)
        progress, _ = _recording_progress()
        obj.total_images_number = 2
        obj.current_image_number = 0
        obj.gen_thumbnails_and_rotate(
            str(flight_folder), rgb_processing=False, max_error=0,
            lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
            progress_callback=progress, progress_bar=progress,
        )
        grados_por_yaw[yaw] = set(grados)

    assert grados_por_yaw[90.0] != grados_por_yaw[-90.0], (
        f"Yaw +90 y yaw -90 se corrigieron con la misma rotación ({grados_por_yaw[90.0]}): "
        "uno de los dos vuelos sale girado 180º."
    )


# ------------------------------------------------------------------- modo manual

def test_manual_90_anota_90_en_el_csv(tmp_path, logger, make_dji_jpeg):
    """El Degree del CSV alimenta el giro del TIFF: tiene que decir la verdad."""
    planta_folder, flight_folder = _flight_folder(tmp_path, make_dji_jpeg, yaw=90.0)
    obj, grados = _obj_con_espia(logger, planta_folder)

    progress, _ = _recording_progress()
    obj.total_images_number = 2
    obj.current_image_number = 0
    obj.gen_thumbnails_and_rotate_manual(
        str(flight_folder), rgb_processing=False, rotation_value_90=True,
        progress_callback=progress, progress_bar=progress,
    )

    assert set(grados) == {Image.ROTATE_270}, "El manual de 90 ya rotaba bien; no debería cambiar."
    assert _leer_degrees(obj) == {90}, (
        "Rotando 90 a mano el CSV anotó otro Degree: el criterio de giro del TIFF lee "
        "esta columna y giraría el TIFF al revés."
    )


def test_manual_270_sigue_anotando_270(tmp_path, logger, make_dji_jpeg):
    """Control del caso que ya funcionaba (era el único que se anotaba bien)."""
    planta_folder, flight_folder = _flight_folder(tmp_path, make_dji_jpeg, yaw=-90.0)
    obj, _ = _obj_con_espia(logger, planta_folder)

    progress, _ = _recording_progress()
    obj.total_images_number = 2
    obj.current_image_number = 0
    obj.gen_thumbnails_and_rotate_manual(
        str(flight_folder), rgb_processing=False, rotation_value_90=False,
        progress_callback=progress, progress_bar=progress,
    )

    assert _leer_degrees(obj) == {270}
