"""
`GenStructFolder` debe dejar constancia, por carpeta de vuelo (PBx_Vy), de qué
giro se aplicó y en qué sentido, para poder reportarlo después.

Contrato: `self.giros_por_vuelo` (dict {nombre_vuelo: entrada}) y
`get_giros_por_vuelo()` (list ordenada por vuelo). Cada entrada:
{"vuelo", "grados" (0/90/270, 0 = no se giró), "imagenes", "modo"
(auto/manual/panoramica/error), "detalle" (str o None, solo relevante cuando
grados==0 o modo==error)}.
"""
import os
import types

import utils


def _recording_progress():
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


def _flight_folder(tmp_path, nombre, make_dji_jpeg, yaws):
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / nombre
    flight_folder.mkdir(parents=True)
    for i, yaw in enumerate(yaws, start=1):
        make_dji_jpeg(str(flight_folder / f"DJI_{i:04d}_T.JPG"), gimbal_yaw=yaw)
    return planta_folder, flight_folder


def _obj(logger, planta_folder):
    import pipeline as gen_struct_folder

    obj = gen_struct_folder.GenStructFolder(logger)
    obj.root_folder = str(planta_folder)
    obj.csvs_root_folder = str(planta_folder / "CSVs")
    obj.total_images_number = 999
    obj.current_image_number = 0
    return obj


# ---------------------------------------------------------------- modo automático

def test_vuelo_que_gira_270_registra_270(tmp_path, logger, make_dji_jpeg):
    planta_folder, flight_folder = _flight_folder(tmp_path, "PB1_V01", make_dji_jpeg, [-90.0, -90.0])
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=0,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    entrada = obj.giros_por_vuelo["PB1_V01"]
    assert entrada == {
        "vuelo": "PB1_V01", "grados": 270, "imagenes": 2, "modo": "auto", "detalle": None,
    }


def test_vuelo_que_gira_90_registra_90(tmp_path, logger, make_dji_jpeg):
    planta_folder, flight_folder = _flight_folder(tmp_path, "PB1_V02", make_dji_jpeg, [90.0, 90.0])
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=0,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    entrada = obj.giros_por_vuelo["PB1_V02"]
    assert entrada["grados"] == 90
    assert entrada["modo"] == "auto"
    assert entrada["imagenes"] == 2
    assert entrada["detalle"] is None


def test_vuelo_que_no_gira_registra_0_con_detalle(tmp_path, logger, make_dji_jpeg):
    """Yaw 0 no cae en ningún bucket de rotación: no se gira nada."""
    planta_folder, flight_folder = _flight_folder(tmp_path, "PB1_V03", make_dji_jpeg, [0.0, 0.0])
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=95,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    entrada = obj.giros_por_vuelo["PB1_V03"]
    assert entrada["grados"] == 0
    assert entrada["modo"] == "auto"
    assert entrada["imagenes"] == 2
    assert entrada["detalle"], "grados==0 debe traer un detalle explicando el motivo."


def test_carpeta_panoramica_registra_panoramica(tmp_path, logger, make_dji_jpeg):
    planta_folder, flight_folder = _flight_folder(
        tmp_path, "PBPano_V1", make_dji_jpeg, [10.0, -50.0, 130.0]
    )
    assert utils.es_carpeta_panoramica(str(flight_folder))
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=95,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    entrada = obj.giros_por_vuelo["PBPano_V1"]
    assert entrada["grados"] == 0
    assert entrada["modo"] == "panoramica"
    assert entrada["imagenes"] == 3
    assert entrada["detalle"], "modo panoramica debe traer detalle."


def test_vuelo_disperso_registra_error(tmp_path, logger, make_dji_jpeg):
    """
    3 imágenes repartidas a partes iguales entre los tres buckets (90 / 270 / no
    rotar): ninguno supera el umbral y el criterio no puede decidir un giro común.
    """
    planta_folder, flight_folder = _flight_folder(
        tmp_path, "PB1_V04", make_dji_jpeg, [90.0, -90.0, 0.0]
    )
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=50,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )

    entrada = obj.giros_por_vuelo["PB1_V04"]
    assert entrada["grados"] == 0
    assert entrada["modo"] == "error"
    assert entrada["imagenes"] == 3
    assert entrada["detalle"], "modo error debe traer detalle."


# ------------------------------------------------------------------- modo manual

def test_manual_90_registra_90(tmp_path, logger, make_dji_jpeg):
    planta_folder, flight_folder = _flight_folder(tmp_path, "PB2_V01", make_dji_jpeg, [90.0, 90.0])
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    obj.gen_thumbnails_and_rotate_manual(
        str(flight_folder), rgb_processing=False, rotation_value_90=True,
        progress_callback=progress, progress_bar=progress,
    )

    entrada = obj.giros_por_vuelo["PB2_V01"]
    assert entrada == {
        "vuelo": "PB2_V01", "grados": 90, "imagenes": 2, "modo": "manual", "detalle": None,
    }


def test_manual_270_registra_270(tmp_path, logger, make_dji_jpeg):
    planta_folder, flight_folder = _flight_folder(tmp_path, "PB2_V02", make_dji_jpeg, [-90.0, -90.0])
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    obj.gen_thumbnails_and_rotate_manual(
        str(flight_folder), rgb_processing=False, rotation_value_90=False,
        progress_callback=progress, progress_bar=progress,
    )

    entrada = obj.giros_por_vuelo["PB2_V02"]
    assert entrada["grados"] == 270
    assert entrada["modo"] == "manual"
    assert entrada["detalle"] is None


# ------------------------------------------------------------------- get_giros_por_vuelo

def test_get_giros_por_vuelo_vacio_por_defecto(logger):
    import pipeline as gen_struct_folder

    obj = gen_struct_folder.GenStructFolder(logger)
    assert obj.get_giros_por_vuelo() == []


def test_get_giros_por_vuelo_devuelve_lista_ordenada_por_vuelo(tmp_path, logger, make_dji_jpeg):
    planta_folder = tmp_path / "PLANTA"
    obj = _obj(logger, planta_folder)
    progress, _ = _recording_progress()

    for nombre, yaws in (
        ("PB3_V02", [-90.0, -90.0]),
        ("PB1_V01", [90.0, 90.0]),
    ):
        flight_folder = planta_folder / nombre
        flight_folder.mkdir(parents=True)
        for i, yaw in enumerate(yaws, start=1):
            make_dji_jpeg(str(flight_folder / f"DJI_{i:04d}_T.JPG"), gimbal_yaw=yaw)
        obj.gen_thumbnails_and_rotate(
            str(flight_folder), rgb_processing=False, max_error=0,
            lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
            progress_callback=progress, progress_bar=progress,
        )

    resultado = obj.get_giros_por_vuelo()
    assert [entrada["vuelo"] for entrada in resultado] == ["PB1_V01", "PB3_V02"]
    assert resultado[0]["grados"] == 90
    assert resultado[1]["grados"] == 270
