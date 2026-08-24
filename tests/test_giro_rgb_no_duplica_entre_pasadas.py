"""Relanzar el pipeline sobre un vuelo RGB ya girado NO puede volver a girarlo.

El criterio de giro (`gen_thumbnails_and_rotate`, pipeline.py:1776) se decide con
el **gimbal yaw del XMP**, y `rotate_and_save` reescribe ese mismo yaw tal cual
tras girar los píxeles: el EXIF/XMP que decide NO cambia con la rotación. En una
segunda pasada sobre la misma carpeta —el caso normal de recuperación, porque el
Job de Cloud Run no reanuda por fases y se relanza a mano— el criterio vuelve a
dar el mismo bucket y el vuelo entero acaba a 180º.

La térmica ya está protegida con la misma guardia que se prueba aquí
(`rotate_thermal_jpgs_in_place`, pipeline.py:2861: "si esta ya viene vertical, es
que se giró en una pasada anterior"). La RGB no lo estaba.

Ver también `test_fallback_no_duplica_rotacion.py`: el mismo 90+90=180, pero
dentro de una única ejecución.
"""
import os
import types

from PIL import Image


def _recording_progress():
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


def _pasada(obj, flight_folder, n_imagenes):
    progress, _ = _recording_progress()
    obj.total_images_number = n_imagenes
    obj.current_image_number = 0
    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=True, max_error=0,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )


def _obj(logger, planta_folder):
    import pipeline as gen_struct_folder

    obj = gen_struct_folder.GenStructFolder(logger)
    obj.root_folder = str(planta_folder)
    obj.csvs_root_folder = str(planta_folder / "CSVs")
    return obj


def _dims(path):
    with Image.open(path) as im:
        return im.size


def test_segunda_pasada_no_vuelve_a_girar_el_rgb(tmp_path, logger, make_dji_jpeg):
    """Dos pasadas seguidas deben dejar la imagen igual que una sola.

    El fixture escribe 64x48 (apaisada, como sale de la cámara). Tras UNA pasada
    debe quedar 48x64. Si la segunda vuelve a girar, sale otra vez 64x48: ese es
    el 180º, y es lo que este test cierra.
    """
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    imagen = str(flight_folder / "DJI_0001.JPG")
    make_dji_jpeg(imagen, gimbal_yaw=90.0)

    obj = _obj(logger, planta_folder)
    _pasada(obj, flight_folder, 1)
    tras_primera = _dims(imagen)
    assert tras_primera == (48, 64), (
        f"La primera pasada no giró la imagen ({tras_primera}); el test no prueba nada."
    )

    _pasada(_obj(logger, planta_folder), flight_folder, 1)
    assert _dims(imagen) == tras_primera, (
        "La segunda pasada volvió a girar una imagen ya girada: el vuelo queda a 180º. "
        "El yaw del XMP no cambia al rotar, así que el criterio no puede ser la única guarda."
    )


def test_segunda_pasada_no_vuelve_a_girar_el_crop(tmp_path, logger, make_dji_jpeg):
    """El `_CROP` se gira en el mismo paso y necesita su propia guarda.

    El original y su recorte se guardan en dos escrituras distintas: una caída
    entre ambas deja el par descuadrado, así que cada fichero se comprueba por
    separado y no por el estado del otro.
    """
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    imagen = str(flight_folder / "DJI_0001.JPG")
    crop = str(flight_folder / "DJI_0001_CROP.JPG")
    make_dji_jpeg(imagen, gimbal_yaw=90.0)
    make_dji_jpeg(crop, gimbal_yaw=90.0)

    obj = _obj(logger, planta_folder)
    _pasada(obj, flight_folder, 1)
    tras_primera = _dims(crop)
    assert tras_primera == (48, 64), f"La primera pasada no giró el _CROP ({tras_primera})."

    _pasada(_obj(logger, planta_folder), flight_folder, 1)
    assert _dims(crop) == tras_primera, "El _CROP se giró dos veces: queda a 180º."


def test_crop_pendiente_se_gira_aunque_el_original_ya_lo_este(tmp_path, logger, make_dji_jpeg):
    """Caída entre las dos escrituras: el original quedó girado y el crop no.

    La guarda es por fichero, así que la pasada de recuperación debe terminar el
    trabajo a medias en vez de saltarse la imagen entera por mirar solo el original.
    """
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    imagen = str(flight_folder / "DJI_0001.JPG")
    crop = str(flight_folder / "DJI_0001_CROP.JPG")
    make_dji_jpeg(imagen, gimbal_yaw=90.0)

    # Estado "a medias" montado con el propio pipeline: una pasada sin `_CROP` deja
    # el original girado con su XMP intacto (rehacerlo a mano con PIL borraría el
    # gimbal yaw y el criterio caería en el bucket de "no rotar", que es otro caso).
    _pasada(_obj(logger, planta_folder), flight_folder, 1)
    make_dji_jpeg(crop, gimbal_yaw=90.0)
    assert _dims(imagen) == (48, 64) and _dims(crop) == (64, 48)

    _pasada(_obj(logger, planta_folder), flight_folder, 1)

    assert _dims(imagen) == (48, 64), "El original ya girado se volvió a girar."
    assert _dims(crop) == (48, 64), (
        "El _CROP que quedó pendiente no se giró: la guarda no puede mirar solo al original."
    )


def test_el_original_sin_girar_si_se_gira(tmp_path, logger, make_dji_jpeg):
    """Control: la guarda no puede bloquear el giro legítimo de la primera pasada."""
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    imagen = str(flight_folder / "DJI_0001.JPG")
    make_dji_jpeg(imagen, gimbal_yaw=-90.0)

    _pasada(_obj(logger, planta_folder), flight_folder, 1)
    assert _dims(imagen) == (48, 64), "La primera pasada dejó de girar: la guarda es demasiado agresiva."
