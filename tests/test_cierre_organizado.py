"""Cierre del organizado: CSV de criterio desde el manifiesto y verificación
manifiesto-contra-disco.

Hoy los CSV se emiten fase a fase y las verificaciones son contadores
acumulados en objetos distintos (`RgbCropping`, `ConvertToTif`,
`MetaLocation`...) que pueden desincronizarse entre sí sin que nadie se
entere. `atom_core.cierre` los sustituye por una única fuente: el manifiesto.
Estos tests prueban que `emitir_csvs` escribe el CSV de criterio con el
formato exacto que consume el giro del TIFF/JPG térmico, y que `verificar`
detecta los mismos desajustes que hoy detectan las fases de comprobación,
pero mirando el manifiesto en vez de contadores que se resetean.
"""
import csv
import os

import pytest

from atom_core.cierre import emitir_csvs, verificar
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from utils import MODO_SOBRESCRIBIR, SplitImagesConfig


class _FakeCallback:
    """Sustituto Qt-free de un Signal: acumula cada `.emit(...)` sin tocar la GUI."""

    def __init__(self):
        self.lineas = []

    def emit(self, value):
        self.lineas.append(value)


def _cfg(destino, *, convert_to_tif=False, cropping_rgb=False, gen_meta_location=False):
    """Construye el `SplitImagesConfig` mínimo que necesita `cierre.py`.

    Solo `output_folder` y los tres flags que gobiernan qué comprobaciones
    corren importan aquí; el resto son los defaults inertes ya usados en
    `tests/test_conteo_imagenes_avisa.py::_cfg`.
    """
    return SplitImagesConfig(
        input_folder=str(destino), output_folder=str(destino),
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad="", choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0,
        organize_images=False,
        cropping_rgb=cropping_rgb, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=gen_meta_location, gen_thumbnails=False, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=0,
        gen_thumbnails_max_error=0, gen_thumbnails_subs_to_angle=0,
        choose_mode_auto=True, gen_thumbnails_rgb=False, gen_thumbnails_termica=False,
        convert_to_tif=convert_to_tif, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
        modo_destino=MODO_SOBRESCRIBIR,
    )


def _fila_termica(destino, ruta_origen, pb, vuelo, *, angulo_giro, ruta_salida_tiff=None):
    nombre = os.path.basename(ruta_origen)
    return FilaManifiesto(
        ruta_origen=ruta_origen,
        tipo="TERMICA",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb=pb,
        vuelo=vuelo,
        nombre_nuevo=nombre,
        angulo_giro=angulo_giro,
        pct_recorte=None,
        comprime=False,
        ruta_salida_original=str(destino / f"{pb}_{vuelo}" / "TERMICA" / nombre),
        ruta_salida_crop=None,
        ruta_salida_tiff=ruta_salida_tiff,
        unassigned=False,
    )


def _fila_rgb(destino, ruta_origen, pb, vuelo, *, ruta_salida_crop=None):
    nombre = os.path.basename(ruta_origen)
    return FilaManifiesto(
        ruta_origen=ruta_origen,
        tipo="RGB",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb=pb,
        vuelo=vuelo,
        nombre_nuevo=nombre,
        angulo_giro=0,
        pct_recorte=0.8 if ruta_salida_crop else None,
        comprime=True,
        ruta_salida_original=str(destino / f"{pb}_{vuelo}" / "RGB" / nombre),
        ruta_salida_crop=ruta_salida_crop,
        ruta_salida_tiff=None,
        unassigned=False,
    )


def _crear_fichero(ruta, contenido=b"contenido"):
    """Crea `ruta` en disco (y sus carpetas) con contenido no vacío: hace falta
    para que `verificar` no reporte 'fichero hecho pero vacío/ausente' en filas
    que sí deben salir limpias."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "wb") as fh:
        fh.write(contenido)


def _leer_criterio_csv(ruta):
    with open(ruta, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_el_csv_de_criterio_numera_desde_uno_con_cuatro_digitos(tmp_path):
    """`New Name` es `<PBx_Vy>_NNNN.JPG`, NNNN = posición dentro del vuelo desde
    1 con cuatro dígitos (pipeline.py:2078-2189) — NUNCA un dato de la imagen
    (ni el nombre original, ni un timestamp). Si el índice arrancara en 0 o sin
    `zfill`, el giro del TIFF (`read_auto_rotate_degree`) no encontraría la fila
    de la imagen que le toca y dejaría de rotar en silencio."""
    destino = tmp_path / "destino"
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila_termica(destino, "/origen/PB1_V01/TERMICA/DJI_0001.JPG", "PB1", "V01", angulo_giro=90),
        _fila_termica(destino, "/origen/PB1_V01/TERMICA/DJI_0002.JPG", "PB1", "V01", angulo_giro=90),
        _fila_termica(destino, "/origen/PB1_V01/TERMICA/DJI_0003.JPG", "PB1", "V01", angulo_giro=90),
    ])
    cfg = _cfg(destino)
    callback = _FakeCallback()

    rutas = emitir_csvs(manifiesto, cfg, callback)

    ruta_csv = rutas["PB1_V01"]
    filas = _leer_criterio_csv(ruta_csv)
    assert [fila["New Name"] for fila in filas] == [
        "PB1_V01_0001.JPG", "PB1_V01_0002.JPG", "PB1_V01_0003.JPG",
    ]
    assert [fila["Degree"] for fila in filas] == ["90", "90", "90"]
    manifiesto.cerrar()


def test_desajuste_jpg_tiff_se_reporta(tmp_path):
    """Un vuelo con 3 JPG térmicos pero solo 2 TIFF (conversión a medias, o un
    worker que murió tras el JPG y antes del TIFF) tiene que aparecer en la
    lista de problemas: hoy lo detecta `checking_convert_to_tif`
    (pipeline.py:2446-2556) contando ficheros en disco; aquí se detecta
    contando filas del manifiesto, sin volver a listar la carpeta."""
    destino = tmp_path / "destino"
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila_termica(destino, "/origen/A.JPG", "PB1", "V01", angulo_giro=0,
                      ruta_salida_tiff=str(destino / "PB1_V01" / "TERMICA" / "A.tiff")),
        _fila_termica(destino, "/origen/B.JPG", "PB1", "V01", angulo_giro=0,
                      ruta_salida_tiff=str(destino / "PB1_V01" / "TERMICA" / "B.tiff")),
        _fila_termica(destino, "/origen/C.JPG", "PB1", "V01", angulo_giro=0, ruta_salida_tiff=None),
    ])
    for fila in manifiesto.todas():
        _crear_fichero(fila["ruta_salida_original"])
        if fila["ruta_salida_tiff"]:
            _crear_fichero(fila["ruta_salida_tiff"])
        manifiesto.marcar_hecha(fila["id"], verificacion="ok")

    cfg = _cfg(destino, convert_to_tif=True)
    problemas = verificar(manifiesto, cfg)

    assert any("JPG" in problema and "TIFF" in problema for problema in problemas), problemas
    manifiesto.cerrar()


def test_desajuste_crop_se_reporta(tmp_path):
    """Igual que el JPG/TIFF pero para el recorte RGB: si de 3 imágenes solo 2
    tienen su `_CROP` hermano, es la misma clase de fallo que hoy detecta
    `checking_results_rgb_cropping` (pipeline.py:3900-3960)."""
    destino = tmp_path / "destino"
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila_rgb(destino, "/origen/A.JPG", "PB1", "V01",
                  ruta_salida_crop=str(destino / "PB1_V01" / "RGB" / "A_CROP.JPG")),
        _fila_rgb(destino, "/origen/B.JPG", "PB1", "V01",
                  ruta_salida_crop=str(destino / "PB1_V01" / "RGB" / "B_CROP.JPG")),
        _fila_rgb(destino, "/origen/C.JPG", "PB1", "V01", ruta_salida_crop=None),
    ])
    for fila in manifiesto.todas():
        _crear_fichero(fila["ruta_salida_original"])
        if fila["ruta_salida_crop"]:
            _crear_fichero(fila["ruta_salida_crop"])
        manifiesto.marcar_hecha(fila["id"], verificacion="ok")

    cfg = _cfg(destino, cropping_rgb=True)
    problemas = verificar(manifiesto, cfg)

    assert any("recortadas" in problema for problema in problemas), problemas
    manifiesto.cerrar()


def test_fila_hecha_sin_fichero_en_disco_se_reporta(tmp_path):
    """El fallo más peligroso posible: el manifiesto dice 'hecho' pero el
    fichero de salida no está (se borró, el disco se llenó a mitad de escritura,
    el rename final no llegó a ocurrir...). Si esto no se reporta, el run se da
    por publicable con imágenes que en realidad faltan."""
    destino = tmp_path / "destino"
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila_rgb(destino, "/origen/A.JPG", "PB1", "V01"),
    ])
    fila = manifiesto.todas()[0]
    # Deliberadamente NO se crea `ruta_salida_original` en disco.
    manifiesto.marcar_hecha(fila["id"], verificacion="ok")

    cfg = _cfg(destino)
    problemas = verificar(manifiesto, cfg)

    assert len(problemas) == 1
    assert fila["ruta_salida_original"] in problemas[0]
    assert "no existe" in problemas[0]
    manifiesto.cerrar()


def test_run_completo_y_correcto_no_reporta_problemas(tmp_path):
    """El caso feliz: un vuelo con RGB (con su CROP) y térmica (con su TIFF),
    todo 'hecho' y todos los ficheros presentes y con contenido. `verificar`
    tiene que devolver la lista vacía — si aquí sale algo, las comprobaciones
    tienen falsos positivos y el motor nuevo nunca daría un run por bueno."""
    destino = tmp_path / "destino"
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila_rgb(destino, "/origen/A.JPG", "PB1", "V01",
                  ruta_salida_crop=str(destino / "PB1_V01" / "RGB" / "A_CROP.JPG")),
        _fila_rgb(destino, "/origen/B.JPG", "PB1", "V01",
                  ruta_salida_crop=str(destino / "PB1_V01" / "RGB" / "B_CROP.JPG")),
        _fila_termica(destino, "/origen/A.JPG", "PB1", "V01", angulo_giro=0,
                      ruta_salida_tiff=str(destino / "PB1_V01" / "TERMICA" / "A.tiff")),
        _fila_termica(destino, "/origen/B.JPG", "PB1", "V01", angulo_giro=0,
                      ruta_salida_tiff=str(destino / "PB1_V01" / "TERMICA" / "B.tiff")),
    ])
    for fila in manifiesto.todas():
        _crear_fichero(fila["ruta_salida_original"])
        if fila["ruta_salida_crop"]:
            _crear_fichero(fila["ruta_salida_crop"])
        if fila["ruta_salida_tiff"]:
            _crear_fichero(fila["ruta_salida_tiff"])
        manifiesto.marcar_hecha(fila["id"], verificacion="ok")

    cfg = _cfg(destino, convert_to_tif=True, cropping_rgb=True)
    emitir_csvs(manifiesto, cfg, _FakeCallback())
    problemas = verificar(manifiesto, cfg)

    assert problemas == []
    manifiesto.cerrar()


def test_las_filas_pendientes_se_reportan(tmp_path):
    """Un run interrumpido (el proceso murió, o quedó a medias) deja filas en
    'pendiente'. `verificar` no puede dar por bueno un run así solo porque las
    imágenes que sí se llegaron a procesar están correctas."""
    destino = tmp_path / "destino"
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila_rgb(destino, "/origen/A.JPG", "PB1", "V01"),
        _fila_rgb(destino, "/origen/B.JPG", "PB1", "V01"),
    ])
    filas = manifiesto.todas()
    _crear_fichero(filas[0]["ruta_salida_original"])
    manifiesto.marcar_hecha(filas[0]["id"], verificacion="ok")
    # `filas[1]` se queda 'pendiente' a propósito.

    cfg = _cfg(destino)
    problemas = verificar(manifiesto, cfg)

    assert len(problemas) == 1
    assert "pendiente" in problemas[0]
    manifiesto.cerrar()
