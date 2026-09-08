"""Velocidad y ETA del apply del organizado (`atom_core.apply`).

El modal necesita img/s y ETA "en vivo" durante `aplicar_rgb`/
`aplicar_termicas` (ver LEDGER-metricas-progreso.md). Dos decisiones de
diseño que estos tests sujetan:

1. **Ventana MÓVIL, no media global desde el arranque**: la media global
   miente en cuanto el ritmo cambia (arranque en frío, mezcla de imágenes
   grandes/pequeñas). `_MedidorVelocidad` solo mira las últimas N
   completadas.
2. **Sin muestras suficientes, `None` — nunca un ETA absurdo.** Mejor sin
   ETA que uno que sale de la nada con 0 o 1 muestra.

Dobles a mano (`_SignalFalsa`) y `monkeypatch` (reloj determinista vía
`apply.time.monotonic`), nunca `unittest.mock` — estilo de la casa, ver
`tests/test_etapas_pipeline.py`.
"""
import json
import os

import pytest
from PIL import Image as PILImage

import pipeline as pipeline_real
from atom_core import apply
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from utils import SplitImagesConfig


class _SignalFalsa:
    """Doble de las señales `progress_callback`/`progress_bar`/
    `progress_summarize`: solo apunta lo que se le emite."""

    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *args, **kwargs):
        self.mensajes.append(valor)


# --- _MedidorVelocidad: unidad pura, sin I/O -------------------------------


def test_sin_muestras_no_hay_velocidad_ni_eta():
    medidor = apply._MedidorVelocidad()
    assert medidor.img_por_segundo() is None
    assert medidor.eta_segundos(100) is None


def test_una_sola_muestra_no_basta():
    """Una completada sola no dice nada del ritmo: hace falta al menos un
    intervalo entre dos completadas para hablar de velocidad."""
    medidor = apply._MedidorVelocidad()
    medidor.registrar(ahora=10.0)
    assert medidor.img_por_segundo() is None
    assert medidor.eta_segundos(50) is None


def test_velocidad_y_eta_sobre_dos_muestras():
    medidor = apply._MedidorVelocidad()
    medidor.registrar(ahora=0.0)
    medidor.registrar(ahora=2.0)  # 1 imagen en 2s -> 0.5 img/s
    assert medidor.img_por_segundo() == pytest.approx(0.5)
    assert medidor.eta_segundos(10) == 20  # 10 pendientes / 0.5 img/s


def test_eta_none_sin_pendientes():
    """Sin nada pendiente no hay ETA que dar, aunque la velocidad sea
    perfectamente calculable."""
    medidor = apply._MedidorVelocidad()
    medidor.registrar(ahora=0.0)
    medidor.registrar(ahora=1.0)
    assert medidor.img_por_segundo() == pytest.approx(1.0)
    assert medidor.eta_segundos(0) is None


def test_ventana_movil_sigue_el_ritmo_reciente_no_la_media_global():
    """Arranque rápido (1 img/s) seguido de un tramo lento (1 img/100s): la
    ventana móvil de tamaño 3 debe reflejar el ritmo RECIENTE (lento), no
    la media desde el arranque, que lo maquillaría con el tramo rápido
    inicial. Esto es exactamente lo que motiva la decisión de diseño del
    ledger: "la media global miente cuando el ritmo cambia"."""
    medidor = apply._MedidorVelocidad(ventana=3)
    for ahora in (0.0, 1.0, 2.0, 102.0, 202.0):
        medidor.registrar(ahora=ahora)

    velocidad_ventana = medidor.img_por_segundo()
    velocidad_global = 4 / 202.0  # (5 muestras - 1) / (202 - 0)

    assert velocidad_ventana == pytest.approx(2 / 200.0)
    assert velocidad_ventana < velocidad_global, (
        "la ventana móvil debería marcar el tramo lento reciente, no la "
        "media inflada por el arranque rápido"
    )


# --- Integración: aplicar_rgb / aplicar_termicas emiten el marcador -------


def _cfg_minima(tmp_path, **overrides):
    base = dict(
        input_folder=str(tmp_path / "origen"), output_folder=str(tmp_path / "destino"),
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad=str(tmp_path / "estadillo.csv"), choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=85, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=False, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=False, gen_thumbnails=False, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5.0,
        gen_thumbnails_max_error=80, gen_thumbnails_subs_to_angle=5.0,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=False, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    base.update(overrides)
    return SplitImagesConfig(**base)


def _fila_manifiesto(origen, salida_original):
    return FilaManifiesto(
        ruta_origen=str(origen), tipo="RGB", timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T", pb="PB1", vuelo="V01",
        nombre_nuevo=str(origen).rsplit("/", 1)[-1], angulo_giro=0, pct_recorte=None,
        comprime=True, ruta_salida_original=str(salida_original), ruta_salida_crop=None,
        ruta_salida_tiff=None, unassigned=False,
    )


def test_aplicar_rgb_emite_el_marcador_de_velocidad_solo_al_final_si_hay_pocas_filas(
        tmp_path, make_dji_jpeg, monkeypatch):
    """Con menos filas que `_EMITIR_STATS_CADA` (10), el marcador solo debe
    salir UNA vez, al terminar la última — nunca un `emit` por imagen (ver
    LEDGER-metricas-progreso.md, "el coste de medir no puede penalizar el
    run")."""
    reloj = {"t": 0.0}

    def _reloj_falso():
        reloj["t"] += 1.0
        return reloj["t"]

    monkeypatch.setattr(apply.time, "monotonic", _reloj_falso)

    origen_dir = tmp_path / "origen"
    origen_dir.mkdir()
    filas = []
    for i in range(3):
        origen = origen_dir / f"DJI_{i:04d}.JPG"
        make_dji_jpeg(str(origen))
        filas.append(_fila_manifiesto(origen, tmp_path / "salida" / f"DJI_{i:04d}.JPG"))

    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas(filas)

    pcb = _SignalFalsa()
    apply.aplicar_rgb(manifiesto, _cfg_minima(tmp_path), pipeline_real, pcb,
                      _SignalFalsa(), _SignalFalsa())

    marcadores = [m for m in pcb.mensajes if str(m).startswith(apply.STATS_APPLY_PREFIX)]
    assert len(marcadores) == 1, "3 filas < _EMITIR_STATS_CADA: solo debe emitir la última"
    payload = json.loads(marcadores[0][len(apply.STATS_APPLY_PREFIX):])
    assert payload["fase"] == "Imágenes RGB"
    assert payload["done"] == payload["total"] == 3
    assert payload["rgb"] == 3
    assert payload["termica"] == 0
    # Reloj falso: +1s por cada `registrar()` -> 3 marcas en 1.0/2.0/3.0,
    # velocidad = (3-1)/(3.0-1.0) = 1.0 img/s.
    assert payload["img_por_segundo"] == pytest.approx(1.0)
    # Sin pendientes (done == total): sin ETA, no un 0 disfrazado de dato.
    assert payload["eta_segundos"] is None
    manifiesto.cerrar()


def test_aplicar_rgb_emite_cada_n_filas_y_no_una_por_imagen(tmp_path, make_dji_jpeg, monkeypatch):
    """Con más filas que `_EMITIR_STATS_CADA`, debe haber un marcador
    intermedio (en el múltiplo de 10) además del final — nunca uno por cada
    fila completada."""
    monkeypatch.setattr(apply.time, "monotonic", lambda: 0.0)
    # Con reloj SIEMPRE igual, `img_por_segundo` es `None` (transcurrido<=0);
    # lo que aquí importa es la CADENCIA de emisión, no el valor.

    origen_dir = tmp_path / "origen"
    origen_dir.mkdir()
    filas = []
    total = 12
    for i in range(total):
        origen = origen_dir / f"DJI_{i:04d}.JPG"
        make_dji_jpeg(str(origen))
        filas.append(_fila_manifiesto(origen, tmp_path / "salida" / f"DJI_{i:04d}.JPG"))

    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas(filas)

    pcb = _SignalFalsa()
    apply.aplicar_rgb(manifiesto, _cfg_minima(tmp_path), pipeline_real, pcb,
                      _SignalFalsa(), _SignalFalsa())

    marcadores = [json.loads(str(m)[len(apply.STATS_APPLY_PREFIX):])
                 for m in pcb.mensajes if str(m).startswith(apply.STATS_APPLY_PREFIX)]
    # 10 (múltiplo de _EMITIR_STATS_CADA) y 12 (la última, aunque no sea múltiplo).
    assert [p["done"] for p in marcadores] == [10, 12]
    manifiesto.cerrar()


# --- aplicar_termicas: mismo mecanismo, fase distinta ----------------------


class _PipelineTermicaDePrueba:
    """Doble mínimo de una instancia `SplitImages`, calcado de
    `tests/test_apply_termicas.py::_PipelineDePrueba`: escribe un TIFF real
    y mínimo sin invocar `dji_irp`."""

    def convert_dji_image_to_tif(self, input_folder, output_folder, image_name,
                                  exiftool_exe, dji_utility, progress_callback,
                                  progress_bar, **kwargs):
        ruta_origen = os.path.join(input_folder, image_name)
        if not os.path.exists(ruta_origen):
            return None
        os.makedirs(output_folder, exist_ok=True)
        tiff_path = os.path.join(output_folder, os.path.splitext(image_name)[0] + ".tiff")
        PILImage.new("F", (4, 2)).save(tiff_path, format="TIFF")
        return (ruta_origen, tiff_path)

    def _run_exif_batch_local(self, pairs, exiftool_exe, progress_callback=None):
        return None


def _fila_termica(origen, salida_jpg, salida_tiff):
    return FilaManifiesto(
        ruta_origen=str(origen), tipo="TERMICA", timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T", pb="PB1", vuelo="V01",
        nombre_nuevo="20260501_100000_" + str(origen).rsplit("/", 1)[-1],
        angulo_giro=0, pct_recorte=None, comprime=False,
        ruta_salida_original=str(salida_jpg), ruta_salida_crop=None,
        ruta_salida_tiff=str(salida_tiff), unassigned=False,
    )


def test_aplicar_termicas_emite_el_marcador_con_su_propia_fase(tmp_path, make_dji_jpeg, monkeypatch):
    """`aplicar_termicas` comparte el mecanismo (`_MedidorVelocidad` +
    `_emitir_stats_apply`) con `aplicar_rgb`, pero el marcador debe llevar
    el nombre EXACTO de la fase (`"Conversión térmica"`, ver
    `atom_core.organize._SPLIT_PHASES`) y contar en `termica`, no en `rgb`."""
    monkeypatch.setattr(apply.time, "monotonic", lambda: 0.0)

    origen = tmp_path / "origen" / "DJI_0001_T.JPG"
    origen.parent.mkdir(parents=True)
    make_dji_jpeg(str(origen))
    salida_jpg = tmp_path / "salida" / "DJI_0001_T.JPG"
    salida_tiff = tmp_path / "salida" / "DJI_0001_T.tif"

    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila_termica(origen, salida_jpg, salida_tiff)])

    pcb = _SignalFalsa()
    resultado = apply.aplicar_termicas(
        manifiesto, _cfg_minima(tmp_path, convert_to_tif=True), _PipelineTermicaDePrueba(),
        pcb, _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 1, "fallido": 0}
    marcadores = [json.loads(str(m)[len(apply.STATS_APPLY_PREFIX):])
                 for m in pcb.mensajes if str(m).startswith(apply.STATS_APPLY_PREFIX)]
    assert len(marcadores) == 1
    payload = marcadores[0]
    assert payload["fase"] == "Conversión térmica"
    assert payload["rgb"] == 0
    assert payload["termica"] == 1
    assert payload["done"] == payload["total"] == 1
    manifiesto.cerrar()
