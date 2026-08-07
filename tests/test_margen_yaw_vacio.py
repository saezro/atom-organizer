"""
El margen de yaw a 0 deja el intervalo de decisión VACÍO y no rota nada.

Las cuatro corridas del usuario del 2026-08-04 (v3.2.5 y v3.2.6) terminaron con
"Número de imágenes rotadas 270: 0 / 90: 0" en TODOS los vuelos, y el usuario
reportó que ninguna imagen salía girada. La causa no está en el mapeo
ROTATE_90/ROTATE_270 ni en los datos: los umbrales llegan a
`gen_thumbnails_and_rotate` como

    lim_max_90  =  90 + add_to_angle      lim_min_90  =  90 - subs_to_angle
    lim_max_270 = -90 + add_to_angle      lim_min_270 = -90 - subs_to_angle

(gui.py:2388-2391 y :2266-2269) y se comparan con `<` ESTRICTO
(pipeline.py:1155,1157). Con los márgenes al valor por defecto del formulario
(0 — webui/src/schema.js:116-117) queda `90 > yaw > 90`, que no admite NINGÚN
valor: ni siquiera un yaw de exactamente 90. Los contadores salen 0 y 0 sea cual
sea la imagen, y el vuelo entero se va por la rama "no se deben rotar"
(pipeline.py:1209) imprimiendo un "OK" en el log.
"""
import os
import types

import pandas as pd

import utils


def _recording_progress():
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


def _flight_folder(tmp_path, make_dji_jpeg, yaw):
    """PLANTA/PB1_V01 con dos térmicas al yaw pedido, listo para procesar."""
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    make_dji_jpeg(str(flight_folder / "DJI_0001_T.JPG"), gimbal_yaw=yaw)
    make_dji_jpeg(str(flight_folder / "DJI_0002_T.JPG"), gimbal_yaw=yaw)
    return planta_folder, flight_folder


def _run(obj, flight_folder, progress, **limites):
    obj.total_images_number = 2
    obj.current_image_number = 0
    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=False, max_error=0,
        progress_callback=progress, progress_bar=progress, **limites,
    )


def test_margen_cero_no_rota_ni_con_el_yaw_exacto(tmp_path, logger, make_dji_jpeg):
    """Yaw = -90 clavado, que es justo el caso que DEBE rotar 270: sale 0."""
    import pipeline as gen_struct_folder

    planta_folder, flight_folder = _flight_folder(tmp_path, make_dji_jpeg, yaw=-90.0)
    obj = gen_struct_folder.GenStructFolder(logger)
    obj.root_folder = str(planta_folder)
    obj.csvs_root_folder = str(planta_folder / "CSVs")

    progress, _ = _recording_progress()
    # add_to_angle = 0 y subs_to_angle = 0 -> los cuatro límites colapsan.
    _run(obj, flight_folder, progress,
         lim_max_270=-90, lim_min_270=-90, lim_max_90=90, lim_min_90=90)

    csv_path = os.path.join(obj.csvs_root_folder, utils.CRITERIO_DIRNAME, "PB1_V01_Videofiles.csv")
    df = pd.read_csv(csv_path)
    assert set(df["Degree"].unique()) == {0}, (
        "Con márgenes a 0 y yaw -90 exacto se rotó algo: el intervalo ya no está vacío."
    )


def test_margen_amplio_si_rota_el_mismo_yaw(tmp_path, logger, make_dji_jpeg):
    """Control: las MISMAS imágenes rotan en cuanto el margen abre el intervalo."""
    import pipeline as gen_struct_folder

    planta_folder, flight_folder = _flight_folder(tmp_path, make_dji_jpeg, yaw=-90.0)
    obj = gen_struct_folder.GenStructFolder(logger)
    obj.root_folder = str(planta_folder)
    obj.csvs_root_folder = str(planta_folder / "CSVs")

    progress, _ = _recording_progress()
    # add_to_angle = subs_to_angle = 80 -> (-170, -10) y (10, 170).
    _run(obj, flight_folder, progress,
         lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10)

    csv_path = os.path.join(obj.csvs_root_folder, utils.CRITERIO_DIRNAME, "PB1_V01_Videofiles.csv")
    df = pd.read_csv(csv_path)
    assert set(df["Degree"].unique()) == {270}, (
        "El mismo yaw -90 con margen amplio debería rotar 270 — si no, el fallo no es el margen."
    )


def test_avisa_cuando_el_intervalo_esta_vacio(tmp_path, logger, make_dji_jpeg):
    """
    El usuario tiene que enterarse ANTES de esperar 20 minutos a un no-op. Que la
    corrección sea automática (ver test_criterio_rotacion_por_defecto.py) no
    justifica hacerla en silencio: el log tiene que decir con qué se rotó.
    """
    import pipeline as gen_struct_folder

    planta_folder, flight_folder = _flight_folder(tmp_path, make_dji_jpeg, yaw=-90.0)
    termica = planta_folder / "TERMICA"
    termica.mkdir()

    obj = gen_struct_folder.GenStructFolder(logger)
    progress, mensajes = _recording_progress()
    obj.check_input_folder_and_iterate(
        str(planta_folder), ["TERMICA"], max_error=0,
        lim_max_270=-90, lim_min_270=-90, lim_max_90=90, lim_min_90=90,
        rotation_mode_auto=True, rotation_value_90=False,
        progress_callback=progress, progress_bar=progress,
    )

    avisos = [m for m in mensajes if isinstance(m, str) and "margen de yaw" in m]
    assert avisos, (
        "Con el intervalo vacío no se avisó de nada: el usuario ve 'OK ... NO se deben rotar' "
        "y no puede saber que el criterio era imposible de cumplir."
    )


def test_no_avisa_cuando_el_margen_es_valido(tmp_path, logger, make_dji_jpeg):
    """El aviso no debe salir en la configuración normal (sería ruido)."""
    import pipeline as gen_struct_folder

    planta_folder, _ = _flight_folder(tmp_path, make_dji_jpeg, yaw=-90.0)
    termica = planta_folder / "TERMICA"
    termica.mkdir()

    obj = gen_struct_folder.GenStructFolder(logger)
    progress, mensajes = _recording_progress()
    obj.check_input_folder_and_iterate(
        str(planta_folder), ["TERMICA"], max_error=95,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        rotation_mode_auto=True, rotation_value_90=False,
        progress_callback=progress, progress_bar=progress,
    )

    avisos = [m for m in mensajes if isinstance(m, str) and "margen de yaw" in m]
    assert not avisos, "Se avisó de margen vacío con un margen perfectamente válido."
