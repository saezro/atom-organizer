"""
El criterio de rotación tiene que venir bien de fábrica: el usuario no debe
tener que rellenar ningún campo para que las imágenes se giren.

Complementa a test_margen_yaw_vacio.py, que documenta POR QUÉ el 0 rompe (el
intervalo de decisión queda vacío). Aquí se fija que ese 0 ya no puede llegar al
criterio por ninguno de los tres caminos: los defaults del formulario webview,
`_default_split_config`, y la última barrera de `check_input_folder_and_iterate`
(por la que pasan también la GUI Qt y la ruta de Aerotools).
"""
import json
import os
import re
import types

import pandas as pd
import pytest

import utils
from utils import (
    ROTATION_MIN_AGREEMENT_PCT,
    ROTATION_YAW_MARGIN,
    sane_rotation_criteria,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _recording_progress():
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


# --- 1. Los valores sanos no dejan hueco al 0 -------------------------------
@pytest.mark.parametrize("entrada", [
    (0, 0, 0),            # los defaults viejos del formulario
    ("0", "0", "0"),      # el front manda números como string
    (None, None, None),   # campo ausente
    ("", "", ""),         # campo vacío
    (-5, -5, -5),         # negativo: intervalo invertido, tampoco rota
])
def test_valores_invalidos_se_sustituyen_por_los_sanos(entrada):
    assert sane_rotation_criteria(*entrada) == (
        ROTATION_YAW_MARGIN, ROTATION_YAW_MARGIN, ROTATION_MIN_AGREEMENT_PCT
    )


def test_valores_validos_se_respetan():
    """Saneado != imponer: si el usuario pone algo utilizable, manda él."""
    assert sane_rotation_criteria(30, 45, 95) == (30, 45, 95)


def test_el_margen_sano_produce_un_intervalo_no_vacio():
    add, subs, _ = sane_rotation_criteria(0, 0, 0)
    assert 90 - subs < 90 < 90 + add, "El margen sano deja el intervalo de rotar 90 vacío."
    assert -90 - subs < -90 < -90 + add, "El margen sano deja el intervalo de rotar 270 vacío."


def test_el_margen_sano_no_se_come_la_zona_de_no_rotar():
    """
    Con yaw ~0 (cámara alineada con la línea de vuelo) NO hay que rotar. Un
    margen de 90 se tragaría ese caso; por eso el valor sano es menor que 90.
    Vuelo real ANTOLIN: hay tomas con yaw -1.4.
    """
    add, subs, _ = sane_rotation_criteria(0, 0, 0)
    assert not (-90 - subs < -1.4 < -90 + add), (
        "Un yaw de -1.4 (cámara casi alineada con el vuelo) se clasificaría como rotar 270."
    )


def test_el_margen_sano_cubre_la_dispersion_real_de_un_vuelo():
    """Vuelo real ZARATAN, 4632 térmicas: yaw entre -101.6 y -97.7."""
    add, subs, _ = sane_rotation_criteria(0, 0, 0)
    for yaw in (-101.6, -98.0, -97.7):
        assert -90 - subs < yaw < -90 + add, (
            f"El yaw real {yaw} queda fuera del intervalo de rotar 270 con el margen sano."
        )


# --- 2. El formulario del webview no ofrece 0 -------------------------------
def test_schema_del_webview_no_trae_ceros_en_el_criterio():
    schema = open(os.path.join(REPO, "webui", "src", "schema.js"), encoding="utf-8").read()
    campos = re.findall(
        r"\{\s*name:\s*'(\w*(?:add_to_angle|subs_to_angle|max_error))'[^}]*default:\s*'(-?\d+)'",
        schema,
    )
    assert campos, "No se localizaron los campos del criterio de rotación en schema.js."
    for nombre, valor in campos:
        assert int(valor) > 0, (
            f"{nombre} vuelve a tener default {valor} en schema.js: con 0 no se rota nada."
        )


# --- 3. La config por defecto de split_images (la que usó el usuario) -------
def test_default_split_config_trae_el_criterio_puesto():
    """
    Comprobación estática del fuente: `atom_core.organize` importa `gui`, que
    necesita PyQt, así que no se puede llamar a _default_split_config desde la
    suite. Lo que hay que impedir es que estos tres campos vuelvan a quedarse con
    un literal 0, que es como estaban.
    """
    fuente = open(os.path.join(REPO, "atom_core", "organize.py"), encoding="utf-8").read()
    bloque = fuente[fuente.index("def _default_split_config"):]
    bloque = bloque[:bloque.index("\n\n\n")]
    for campo, constante in (("gen_thumbnails_add_to_angle", "ROTATION_YAW_MARGIN"),
                             ("gen_thumbnails_subs_to_angle", "ROTATION_YAW_MARGIN"),
                             ("gen_thumbnails_max_error", "ROTATION_MIN_AGREEMENT_PCT")):
        asignacion = re.search(rf"{campo}\s*=\s*([A-Za-z_0-9]+)", bloque)
        assert asignacion, f"No se encontró la asignación de {campo} en _default_split_config."
        assert asignacion.group(1) == constante, (
            f"{campo} vale {asignacion.group(1)} en vez de {constante}: si es 0, no se rota nada."
        )


# --- 4. Última barrera: aunque llegue un 0, se rota -------------------------
def test_con_los_limites_a_cero_la_barrera_rota_igual(tmp_path, logger, make_dji_jpeg):
    """
    Reproduce la corrida del usuario: márgenes a 0 -> límites colapsados. Antes
    salía 0/0 y ni una imagen girada; ahora el vuelo se rota igualmente.
    """
    import pipeline as gen_struct_folder

    planta_folder = tmp_path / "PLANTA"
    termica = planta_folder / "TERMICA"
    flight_folder = termica / "PB1_V01"
    flight_folder.mkdir(parents=True)
    for i in (1, 2):
        make_dji_jpeg(str(flight_folder / f"DJI_000{i}_T.JPG"), gimbal_yaw=-98.0)

    obj = gen_struct_folder.GenStructFolder(logger)
    obj.total_images_number = 2
    obj.current_image_number = 0
    progress, mensajes = _recording_progress()

    obj.check_input_folder_and_iterate(
        str(planta_folder), ["TERMICA"], max_error=0,
        lim_max_270=-90, lim_min_270=-90, lim_max_90=90, lim_min_90=90,
        rotation_mode_auto=True, rotation_value_90=False,
        progress_callback=progress, progress_bar=progress,
    )

    csv_path = os.path.join(planta_folder, "CSVs", utils.CRITERIO_DIRNAME, "PB1_V01_Videofiles.csv")
    assert os.path.exists(csv_path), "No se llegó a procesar el vuelo."
    df = pd.read_csv(csv_path)
    assert set(df["Degree"].unique()) == {270}, (
        "Con los límites colapsados el vuelo sigue sin rotarse: la barrera no actuó."
    )
    assert any(isinstance(m, str) and "margen de yaw" in m for m in mensajes), (
        "Se corrigió el margen en silencio; el usuario debe verlo en el log."
    )


def test_con_limites_validos_la_barrera_no_toca_nada(tmp_path, logger, make_dji_jpeg):
    """El saneado no debe pisar una configuración deliberada del usuario."""
    import pipeline as gen_struct_folder

    planta_folder = tmp_path / "PLANTA"
    termica = planta_folder / "TERMICA"
    flight_folder = termica / "PB1_V01"
    flight_folder.mkdir(parents=True)
    # yaw 20: dentro de (10, 170) -> rota 90 con el margen estrecho del usuario,
    # pero NO estaría en (10, 170) si la barrera lo hubiera reescrito a ±80... sí
    # lo estaría, así que se comprueba por el aviso, que no debe emitirse.
    for i in (1, 2):
        make_dji_jpeg(str(flight_folder / f"DJI_000{i}_T.JPG"), gimbal_yaw=20.0)

    obj = gen_struct_folder.GenStructFolder(logger)
    obj.total_images_number = 2
    obj.current_image_number = 0
    progress, mensajes = _recording_progress()

    obj.check_input_folder_and_iterate(
        str(planta_folder), ["TERMICA"], max_error=95,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        rotation_mode_auto=True, rotation_value_90=False,
        progress_callback=progress, progress_bar=progress,
    )

    assert not any(isinstance(m, str) and "margen de yaw" in m for m in mensajes), (
        "Se corrigió un margen perfectamente válido."
    )
