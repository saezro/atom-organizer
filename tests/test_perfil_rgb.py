"""Tests de la instrumentación OPT-IN de la fase RGB (`atom_core.perfil_rgb`).

Dos cosas hay que sujetar:

1. Apagada (env var sin definir) — comportamiento de `apply.py` INTACTO: no
   se crea ningún fichero, ninguna columna de más, ningún efecto.
2. Encendida (`ORGANIZER_PERFIL_RGB=<ruta>`) — el CSV sale con cabecera + una
   fila por imagen, y `t_total` > 0 en cada una.
"""
import importlib

import pytest

import pipeline as pipeline_real
from atom_core import apply


def _fila_dict(origen, salida_original, salida_crop=None, angulo_giro=0, pct_recorte=None):
    return {
        "ruta_origen": str(origen),
        "angulo_giro": angulo_giro,
        "pct_recorte": pct_recorte,
        "ruta_salida_original": str(salida_original),
        "ruta_salida_crop": str(salida_crop) if salida_crop else None,
    }


def _cfg(compress_level=85):
    from utils import SplitImagesConfig
    base = dict(
        input_folder="/origen", output_folder="/destino",
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad="/estadillo.csv", choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=compress_level, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=True, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=True, gen_thumbnails=True, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5.0,
        gen_thumbnails_max_error=80, gen_thumbnails_subs_to_angle=5.0,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=True, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    return SplitImagesConfig(**base)


@pytest.fixture
def _reimportar_perfil_rgb(monkeypatch):
    """Recarga `atom_core.perfil_rgb` (y el `apply` que lo importó) DESPUÉS
    de tocar la env var: `ACTIVO` se calcula una vez al importar el módulo,
    así que cambiar la env var en caliente no basta sin recargar.
    Restaura ambos módulos a su estado original al acabar el test."""
    def _recargar():
        from atom_core import perfil_rgb as mod
        importlib.reload(mod)
        importlib.reload(apply)
        return mod

    yield _recargar

    monkeypatch.delenv("ORGANIZER_PERFIL_RGB", raising=False)
    _recargar()


def test_sin_env_var_no_crea_fichero_ni_cambia_resultado(tmp_path, make_dji_jpeg,
                                                          _reimportar_perfil_rgb,
                                                          monkeypatch):
    monkeypatch.delenv("ORGANIZER_PERFIL_RGB", raising=False)
    perfil_rgb = _reimportar_perfil_rgb()
    assert perfil_rgb.ACTIVO is False

    ruta_csv = tmp_path / "no_deberia_existir.csv"

    origen = tmp_path / "origen" / "DJI_0001.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen))
    salida = tmp_path / "salida" / "DJI_0001.JPG"

    fila = _fila_dict(origen, salida, angulo_giro=0)
    verificacion = apply._escribir_salidas_de_fila(fila, _cfg(), pipeline_real)

    assert salida.exists()
    assert str(salida) in verificacion
    assert not ruta_csv.exists()
    # Sin activar, `escribir_cabecera`/`resumen` tampoco tocan disco.
    perfil_rgb.escribir_cabecera()
    assert perfil_rgb.resumen() is None
    assert not ruta_csv.exists()


def test_con_env_var_escribe_csv_con_cabecera_y_una_fila_por_imagen(
        tmp_path, make_dji_jpeg, _reimportar_perfil_rgb, monkeypatch):
    ruta_csv = tmp_path / "perfil.csv"
    monkeypatch.setenv("ORGANIZER_PERFIL_RGB", str(ruta_csv))
    perfil_rgb = _reimportar_perfil_rgb()
    assert perfil_rgb.ACTIVO is True

    cfg = _cfg()
    nombres = ["DJI_0001.JPG", "DJI_0002.JPG", "DJI_0003.JPG"]
    filas = []
    for nombre in nombres:
        origen = tmp_path / "origen" / nombre
        origen.parent.mkdir(exist_ok=True)
        make_dji_jpeg(str(origen))
        salida_original = tmp_path / "salida" / nombre
        salida_crop = tmp_path / "salida" / nombre.replace(".JPG", "_CROP.JPG")
        filas.append(_fila_dict(origen, salida_original, salida_crop,
                                angulo_giro=90, pct_recorte=0.8))

    perfil_rgb.escribir_cabecera()
    for fila in filas:
        apply._escribir_salidas_de_fila(fila, cfg, pipeline_real)

    contenido = ruta_csv.read_text(encoding="utf-8").splitlines()
    cabecera, *filas_csv = contenido
    assert cabecera == (
        "ts_inicio,ts_fin,pid,nombre,bytes_origen,t_lectura,t_decode,"
        "t_encode_original,t_encode_crop,t_thumbnail,t_escritura,t_total"
    )
    assert len(filas_csv) == len(nombres)

    columnas = cabecera.split(",")
    idx_total = columnas.index("t_total")
    idx_nombre = columnas.index("nombre")
    idx_bytes = columnas.index("bytes_origen")
    nombres_vistos = set()
    for linea in filas_csv:
        valores = linea.split(",")
        assert len(valores) == len(columnas)
        assert float(valores[idx_total]) > 0
        assert int(valores[idx_bytes]) > 0
        nombres_vistos.add(valores[idx_nombre])
    assert nombres_vistos == set(nombres)

    resumen = perfil_rgb.resumen()
    assert resumen is not None
    assert resumen["n_imagenes"] == len(nombres)
    assert resumen["medias"]["t_total"] > 0
    # No hay giro real medido como etapa propia (queda dentro de t_total);
    # thumbnail no aplica en este camino: ambas quedan a 0.0 siempre.
    assert resumen["medias"]["t_thumbnail"] == 0.0
