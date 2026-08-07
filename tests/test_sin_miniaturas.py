"""Se quitan las miniaturas (Cas, 2026-08-05): ni RGB ni térmicas.

La fase 5 pasa a llamarse "Rotación" y solo hace eso. Los tres riesgos de haberla
tocado, que es lo que fija este fichero:

  1. Borrar la fase entera habría dejado de girar también las RGB, porque el giro
     del original RGB in-place vivía dentro de la misma función que escribía la
     miniatura térmica. La rotación RGB tiene que seguir intacta.
  2. El criterio de giro (`_Videofiles.csv`, columna `Degree`) vivía DENTRO de
     MINIATURAS. Es lo único que le dice a la conversión a TIFF y al giro del JPG térmico
     cuánto girar: si desapareciera con la carpeta, ambas dejarían de rotar EN
     SILENCIO. Ahora va a `CSVs/`, plano.
  3. El `*_T.JPG` crudo sigue sin tocarse: es R-JPEG propietario de DJI y re-encodarlo
     pierde el payload radiométrico.
"""
import os
import types

import pandas as pd
from PIL import Image

import utils


def _noop_progress():
    return types.SimpleNamespace(emit=lambda *a, **k: None)


def _vuelo(tmp_path, make_dji_jpeg, nombre_imagen, yaw=-90.0):
    """<planta>/TERMICA|RGB/PB1_V01/<imagen>. Devuelve (planta, carpeta del vuelo)."""
    planta = tmp_path / "PLANTA"
    carpeta = planta / ("RGB" if nombre_imagen.endswith("_V.JPG") else "TERMICA") / "PB1_V01"
    carpeta.mkdir(parents=True)
    make_dji_jpeg(str(carpeta / nombre_imagen), gimbal_yaw=yaw)
    return planta, carpeta


def _corre_fase(logger, planta, carpeta, rgb_processing):
    import pipeline as gen_struct_folder

    obj = gen_struct_folder.GenStructFolder(logger)
    obj.root_folder = str(planta)
    obj.csvs_root_folder = str(planta / "CSVs")
    obj.total_images_number = 1
    obj.current_image_number = 0
    progress = _noop_progress()
    obj.gen_thumbnails_and_rotate(
        str(carpeta), rgb_processing=rgb_processing, max_error=0,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )
    return obj


def test_no_se_crea_la_carpeta_miniaturas(tmp_path, logger, make_dji_jpeg):
    planta, carpeta = _vuelo(tmp_path, make_dji_jpeg, "DJI_0001_T.JPG")
    _corre_fase(logger, planta, carpeta, rgb_processing=False)

    assert not (planta / "MINIATURAS").exists(), "Se sigue generando la carpeta MINIATURAS."
    assert not list(carpeta.glob("*_miniaturas*")), "Quedan restos de miniaturas junto al vuelo."


def test_el_criterio_de_giro_se_escribe_en_csvs(tmp_path, logger, make_dji_jpeg):
    """Sin este CSV, el TIFF y el JPG térmico dejan de rotar sin decir nada."""
    planta, carpeta = _vuelo(tmp_path, make_dji_jpeg, "DJI_0001_T.JPG")
    _corre_fase(logger, planta, carpeta, rgb_processing=False)

    csv_path = planta / "CSVs" / utils.CRITERIO_DIRNAME / "PB1_V01_Videofiles.csv"
    assert csv_path.exists(), (
        "El criterio de giro no está en CSVs/: se ha ido con MINIATURAS y el vuelo "
        "dejará de rotar el TIFF y el JPG térmico en silencio."
    )
    assert set(pd.read_csv(csv_path)["Degree"].unique()) == {270}


def test_la_termica_cruda_no_se_toca(tmp_path, logger, make_dji_jpeg):
    """El *_T.JPG es R-JPEG de DJI: re-encodarlo pierde el payload radiométrico y
    ese fichero ya no se puede convertir a TIFF nunca más."""
    planta, carpeta = _vuelo(tmp_path, make_dji_jpeg, "DJI_0001_T.JPG")
    original = (carpeta / "DJI_0001_T.JPG").read_bytes()

    _corre_fase(logger, planta, carpeta, rgb_processing=False)

    assert (carpeta / "DJI_0001_T.JPG").read_bytes() == original, (
        "La fase de rotación ha reescrito el JPG térmico crudo."
    )


def test_la_rgb_se_sigue_girando_in_place(tmp_path, logger, make_dji_jpeg):
    """El riesgo de quitar la fase: la rotación RGB vivía en la misma función que
    la miniatura térmica."""
    planta, carpeta = _vuelo(tmp_path, make_dji_jpeg, "DJI_0001_V.JPG")
    with Image.open(carpeta / "DJI_0001_V.JPG") as img:
        w, h = img.size

    _corre_fase(logger, planta, carpeta, rgb_processing=True)

    with Image.open(carpeta / "DJI_0001_V.JPG") as img:
        assert img.size == (h, w), (
            "La RGB no se ha girado: quitar las miniaturas se ha llevado por delante la "
            "rotación del original RGB."
        )
