"""E2E del motor plan-apply cableado en el orquestador (Tarea 7).

`atom_core.phases.organizar_plan_apply` sustituye a `split_images` (motor
viejo de 7 fases) como lo que de verdad ejecuta el task `split_images` de
`atom_core.organize.run_task` (ver `_TASKS`). Estos tests corren la secuencia
COMPLETA —índice -> manifiesto SQLite -> apply RGB -> apply térmicas ->
cierre— sobre una inspección sintética, con las clases REALES de
`pipeline.py` para las 4 funciones que el índice reutiliza
(`ventana_horaria_vuelo`, `nombre_destino`, `get_percentage_by_model`,
`read_auto_rotate_degree`) a través de `_PipelineAdaptadorPlanApply`: son
puras o de solo-lectura EXIF, el mismo contrato que usa
`atom_core.organize.HeadlessHost` en producción. Solo se sustituyen
`convert_dji_image_to_tif` y `_run_exif_batch_local` (binarios externos
`dji_irp`/`exiftool` que no están en el entorno de test), igual que
`tests/test_apply_termicas.py`.

Dobles a mano (`_HostDePrueba`, `_SignalFalsa`, `_ConfigObjFalso`) y
`monkeypatch`, nunca `unittest.mock` — estilo de la casa, ver
`tests/test_etapas_pipeline.py`.

Lo que estos tests sujetan:
1. El árbol de salida final es el que el equipo espera: `PBx_Vy/RGB`,
   `PBx_Vy/TERMICA`, `SIN_ORDENAR` (lo que no cae en ningún vuelo del
   estadillo) y el CSV de criterio de giro en `CSVs/_criterio/`.
2. Las 4 fases nuevas emiten su prefijo `---> SUBPROCESO:` con el nombre
   EXACTO que espera `atom_core.organize._SPLIT_PHASES`: es lo que
   `atom_core.organize._on_summary` intercepta para alimentar
   `MedidorRecursos` y las métricas MB/s y CPU del modal (v3.4.80).
   Perder el prefijo (o el nombre) las hace desaparecer en silencio.
3. La barra de progreso llega a 100 al final del run.
4. El origen queda intacto byte a byte: el motor plan-apply solo lee de ahí,
   nunca escribe ni mueve nada dentro.
5. Un segundo run sobre el MISMO destino es idempotente: no duplica
   ficheros ni cambia el árbol (las rutas de salida son deterministas —
   EXIF -> timestamp -> nombre — así que sobrescriben, nunca acumulan).
6. AGUJERO conocido resuelto (ver `atom_core.indice.TIPOS_RGB`): las
   imágenes `RGB_Extra` cuentan como RGB en `aplicar_rgb` y en
   `cierre.verificar`. Antes de este cableado se habrían quedado
   'pendiente' para siempre (`aplicar_rgb` solo miraba `tipo == "RGB"`) y
   `verificar` las habría reportado como "run interrumpido" sin haberlo
   estado.
"""
import datetime as dt
import hashlib
import os

import pytest
from PIL import Image as PILImage

import pipeline
from atom_core.phases import PipelinePhasesMixin
from utils import SplitImagesConfig


class _SignalFalsa:
    """Sustituto Qt-free de un Signal: los tres callbacks del pipeline solo
    necesitan `.emit()`."""

    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *args, **kwargs):
        self.mensajes.append(valor)


class _ConfigObjFalso:
    """Doble de `external_tools.ReadLoadConfig`: solo expone el diccionario
    que `indice._pct_recorte` lee vía `_PipelineAdaptadorPlanApply.
    percentage_by_models`. Vacío a propósito — las imágenes sintéticas de
    `make_dji_jpeg` no llevan el tag EXIF `Image Model`, así que
    `_pct_recorte` corta antes (`if not dato.modelo: return None`) y este
    diccionario nunca se consulta de verdad."""

    percentage_by_models: dict = {}


class _HostDePrueba(PipelinePhasesMixin):
    """Host real (sin Qt, sin `HeadlessHost` completo): usa las clases REALES
    de `pipeline.py` que `_PipelineAdaptadorPlanApply` delega
    (`GenStructFolder.ventana_horaria_vuelo`, `SplitImages.nombre_destino` /
    `read_auto_rotate_degree`, `RGBCropping.get_percentage_by_model`) — el
    MISMO contrato que usa `atom_core.organize.HeadlessHost` en producción."""

    def __init__(self, logger):
        self.organizer_logger_obj = logger
        self.gen_struct_folder_obj = pipeline.GenStructFolder(logger)
        self.split_images_obj = pipeline.SplitImages(logger)
        self.rgb_cropping_obj = pipeline.RGBCropping(logger)
        self.config_obj = _ConfigObjFalso()


def _fake_convert_dji_image_to_tif(input_folder, output_folder, image_name, exiftool_exe,
                                   dji_utility, progress_callback, progress_bar, **kwargs):
    """Doble de `SplitImages.convert_dji_image_to_tif`: escribe un TIFF real y
    mínimo (float32, como el conversor de verdad) sin invocar `dji_irp`, que
    no está disponible en el entorno de test. Calca el doble de
    `tests/test_apply_termicas.py::_PipelineDePrueba.convert_dji_image_to_tif`."""
    ruta_origen = os.path.join(input_folder, image_name)
    if not os.path.exists(ruta_origen):
        return None
    os.makedirs(output_folder, exist_ok=True)
    tiff_path = os.path.join(output_folder, os.path.splitext(image_name)[0] + ".tiff")
    PILImage.new("F", (4, 2)).save(tiff_path, format="TIFF")
    return (ruta_origen, tiff_path)


def _fake_run_exif_batch_local(pairs, exiftool_exe, progress_callback=None):
    """Doble de `pipeline._run_exif_batch_local`: no invoca `exiftool` de
    verdad. El contenido EXIF del TIFF ya lo sujeta
    `tests/test_apply_termicas.py`; aquí solo hace falta que el lote no
    falle, para no ensuciar el árbol de salida esperado con filas
    'fallido'."""
    return None


def _escribir_estadillo(ruta, filas):
    """Estadillo mínimo (columnas ES), calcado de
    `tests/test_indice_organizado.py::_escribir_estadillo`."""
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n")
        for pb, vuelo, fecha, hora_inicio, hora_final in filas:
            fh.write(f"{pb};{vuelo};{fecha};{hora_inicio};{hora_final}\n")


def _cfg(tmp_path, **overrides):
    base = dict(
        input_folder=str(tmp_path / "origen"),
        output_folder=str(tmp_path / "destino"),
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="_D",
        estad=str(tmp_path / "estadillo.csv"),
        choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=85, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=False, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=False, gen_thumbnails=False, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5,
        gen_thumbnails_max_error=80, gen_thumbnails_subs_to_angle=5,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=True, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    base.update(overrides)
    return SplitImagesConfig(**base)


@pytest.fixture
def _inspeccion(tmp_path, make_dji_jpeg):
    """Origen con 2 RGB + 2 TERMICA dentro de la ventana de PB1/V01, y una
    RGB con timestamp fuera de toda ventana (-> `SIN_ORDENAR`). Ninguna lleva
    yaw (`gimbal_yaw=0.0`): `angulo_giro` es 0 en todo el run, así que
    `_publicar_tiff_girado` toma el camino SIN rotación (`shutil.copy2`, sin
    reabrir con PIL) — evita depender de que el TIFF sintético en modo "F"
    acepte los kwargs de guardado JPEG (`quality`/`subsampling`) que solo se
    usan cuando SÍ hay giro que aplicar."""
    origen = tmp_path / "origen"
    origen.mkdir()
    ts = dt.datetime(2026, 1, 15, 10, 0, 0)
    make_dji_jpeg(str(origen / "DJI_0001_D.JPG"), dt_val=ts, gimbal_yaw=0.0)
    make_dji_jpeg(str(origen / "DJI_0002_D.JPG"), dt_val=ts + dt.timedelta(seconds=5), gimbal_yaw=0.0)
    make_dji_jpeg(str(origen / "DJI_0001_T.JPG"), dt_val=ts, gimbal_yaw=0.0)
    make_dji_jpeg(str(origen / "DJI_0002_T.JPG"), dt_val=ts + dt.timedelta(seconds=5), gimbal_yaw=0.0)
    # Fuera de toda ventana del estadillo -> SIN_ORDENAR.
    make_dji_jpeg(str(origen / "DJI_9999_D.JPG"), dt_val=dt.datetime(2020, 1, 1, 0, 0, 0), gimbal_yaw=0.0)

    _escribir_estadillo(tmp_path / "estadillo.csv",
                       [("1", "1", "2026:01:15", "09:55:00", "10:05:00")])
    return tmp_path


def _hash_arbol(carpeta):
    """Hash agregado (ruta relativa + contenido) de todo fichero bajo
    `carpeta`: comprueba que el origen no cambió NI DE CONTENIDO NI DE
    ESTRUCTURA, no solo que sigue teniendo el mismo número de ficheros."""
    digest = hashlib.sha256()
    for raiz, _dirs, ficheros in sorted(os.walk(carpeta)):
        for nombre in sorted(ficheros):
            ruta = os.path.join(raiz, nombre)
            relativo = os.path.relpath(ruta, carpeta)
            digest.update(relativo.encode("utf-8"))
            with open(ruta, "rb") as fh:
                digest.update(fh.read())
    return digest.hexdigest()


def _arbol_relativo(carpeta):
    return sorted(
        os.path.relpath(os.path.join(raiz, nombre), carpeta)
        for raiz, _dirs, ficheros in os.walk(carpeta)
        for nombre in ficheros
    )


def _correr(host, cfg, monkeypatch):
    """Sustituye los dos únicos puntos que tocarían binarios externos
    (`dji_irp`/`exiftool`) y ejecuta `organizar_plan_apply` de punta a
    punta. Devuelve los tres `_SignalFalsa` para que cada test inspeccione
    lo que le toca."""
    monkeypatch.setattr(host.split_images_obj, "convert_dji_image_to_tif",
                        _fake_convert_dji_image_to_tif)
    monkeypatch.setattr(host.split_images_obj, "_run_exif_batch_local",
                        _fake_run_exif_batch_local)
    pcb, pbar, psum = _SignalFalsa(), _SignalFalsa(), _SignalFalsa()
    host.organizar_plan_apply(cfg, pcb, pbar, psum)
    return pcb, pbar, psum


def test_organizado_completo_produce_el_arbol_esperado(_inspeccion, logger, monkeypatch):
    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)
    _correr(host, cfg, monkeypatch)

    destino = cfg.output_folder
    assert os.path.isdir(os.path.join(destino, "PB1_V1", "RGB"))
    assert os.path.isdir(os.path.join(destino, "PB1_V1", "TERMICA"))
    assert os.path.isdir(os.path.join(destino, "SIN_ORDENAR"))
    assert os.path.isdir(os.path.join(destino, "CSVs", "_criterio"))

    assert len(os.listdir(os.path.join(destino, "PB1_V1", "RGB"))) == 2
    # JPG + TIF por cada térmica del vuelo (2 térmicas -> 2 JPG + 2 TIF).
    assert len(os.listdir(os.path.join(destino, "PB1_V1", "TERMICA"))) == 4
    # El nombre del CSV de criterio usa `pb_vuelo` tal cual (`cierre._emitir_csv_criterio`),
    # NO el nombre de la carpeta del vuelo (`PBx_Vy`, que lleva el prefijo "PB"/"V").
    assert os.path.isfile(os.path.join(destino, "CSVs", "_criterio", "1_1_Videofiles.csv"))

    sin_ordenar_rgb = os.path.join(destino, "SIN_ORDENAR", "RGB")
    assert os.path.isdir(sin_ordenar_rgb)
    assert len(os.listdir(sin_ordenar_rgb)) == 1


def test_emite_una_fase_por_cada_etapa_con_el_prefijo_de_subproceso(_inspeccion, logger, monkeypatch):
    """Previene perder las métricas MB/s y CPU por fase del modal (v3.4.80):
    `atom_core.organize._on_summary` detecta el arranque de cada fase
    interceptando este prefijo, con este nombre EXACTO, en el canal
    summary."""
    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)
    _pcb, _pbar, psum = _correr(host, cfg, monkeypatch)

    esperadas = ["---> SUBPROCESO: Índice", "---> SUBPROCESO: Imágenes RGB",
                "---> SUBPROCESO: Conversión térmica", "---> SUBPROCESO: Cierre"]
    for esperada in esperadas:
        assert esperada in psum.mensajes, f"falta el prefijo de fase: {esperada!r}"


def test_la_barra_de_progreso_llega_a_cien(_inspeccion, logger, monkeypatch):
    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)
    _pcb, pbar, _psum = _correr(host, cfg, monkeypatch)
    assert pbar.mensajes[-1] == 100


def test_el_origen_no_se_modifica(_inspeccion, logger, monkeypatch):
    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)
    antes = _hash_arbol(cfg.input_folder)
    _correr(host, cfg, monkeypatch)
    despues = _hash_arbol(cfg.input_folder)
    assert antes == despues


def test_un_segundo_run_es_idempotente(_inspeccion, logger, monkeypatch):
    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)
    _correr(host, cfg, monkeypatch)
    arbol_1 = _arbol_relativo(cfg.output_folder)

    # Host NUEVO (mismo criterio que dos procesos de Cloud Run distintos, o
    # dos ejecuciones sueltas de la app de escritorio): sin caché ni estado
    # compartido con el primer run, el manifiesto vive en OTRO directorio
    # temporal, y aun así el resultado tiene que ser el mismo árbol.
    host2 = _HostDePrueba(logger)
    _correr(host2, cfg, monkeypatch)
    arbol_2 = _arbol_relativo(cfg.output_folder)

    assert arbol_1 == arbol_2


def test_rgb_extra_cuenta_como_rgb_no_se_queda_pendiente(tmp_path, make_dji_jpeg, logger, monkeypatch):
    """AGUJERO conocido, resuelto por `atom_core.indice.TIPOS_RGB`:
    `indice._clasificar_tipo` puede devolver `RGB_Extra` (tercer grupo de
    sufijos), pero ni `atom_core.apply.aplicar_rgb` ni
    `atom_core.cierre.verificar` tenían rama para ese tipo antes de este
    cableado — la fila se habría quedado 'pendiente' para siempre y
    `verificar` la habría reportado como "el run se interrumpió antes de
    terminar" sin que eso fuera cierto. Aquí se comprueba que
    `organizar_plan_apply` la procesa igual que una RGB (la publica en
    `RGB_Extra/`) y que el cierre NO la reporta como pendiente."""
    origen = tmp_path / "origen"
    origen.mkdir()
    ts = dt.datetime(2026, 1, 15, 10, 0, 0)
    make_dji_jpeg(str(origen / "DJI_0001_D.JPG"), dt_val=ts, gimbal_yaw=0.0)
    make_dji_jpeg(str(origen / "DJI_0001_T.JPG"), dt_val=ts, gimbal_yaw=0.0)
    make_dji_jpeg(str(origen / "DJI_0001_E.JPG"), dt_val=ts, gimbal_yaw=0.0)
    _escribir_estadillo(tmp_path / "estadillo.csv",
                       [("1", "1", "2026:01:15", "09:55:00", "10:05:00")])

    host = _HostDePrueba(logger)
    cfg = _cfg(tmp_path, end_rgb_extra_files="_E", end_rgb_files="_D")
    pcb, _pbar, _psum = _correr(host, cfg, monkeypatch)

    ruta_rgb_extra = os.path.join(cfg.output_folder, "PB1_V1", "RGB_Extra")
    assert os.path.isdir(ruta_rgb_extra), "RGB_Extra no se creó: la fila no se procesó"
    assert os.listdir(ruta_rgb_extra), "RGB_Extra está vacía: la fila se quedó sin escribir"

    # Mensaje EXACTO de `cierre.verificar` para filas sin terminar
    # (`atom_core/cierre.py`): si `aplicar_rgb` no procesara RGB_Extra, esta
    # imagen se quedaría 'pendiente' para siempre y este mensaje aparecería.
    assert not any(isinstance(m, str) and "siguen 'pendiente'/'en_curso'" in m
                  for m in pcb.mensajes)


def test_un_run_que_revienta_conserva_el_manifiesto_y_el_siguiente_reanuda(
        _inspeccion, logger, monkeypatch):
    """Un organizado muerto a mitad NO puede obligar a repetirlo todo.

    Incidente que previene: hasta la corrección de 2026-09-08 el manifiesto
    vivía en un `tempfile.mkdtemp()` de ruta ALEATORIA y se borraba en un
    `finally` incondicional. Eso hacía que `Manifiesto.reabrir_huerfanas()`
    fuese código muerto por construcción: el fichero no sobrevivía nunca al
    run, y aunque hubiera sobrevivido a un SIGKILL, la corrida siguiente
    creaba otro directorio temporal distinto y jamás lo encontraba. Con una
    planta grande a medio organizar eso significa tirar horas de trabajo ya
    hecho y empezar de cero.

    Aquí se revienta el run DESPUÉS del apply (al emitir los CSVs), se
    comprueba que el manifiesto se queda en disco, y que el segundo run no
    vuelve a convertir las térmicas que ya estaban hechas.
    """
    from atom_core import phases as phases_mod
    from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO

    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)

    def _emitir_csvs_que_revienta(*_args, **_kwargs):
        raise RuntimeError("corte simulado a mitad del organizado")

    convertidas_run1 = []

    def _contar_run1(input_folder, output_folder, image_name, *args, **kwargs):
        convertidas_run1.append(image_name)
        return _fake_convert_dji_image_to_tif(input_folder, output_folder,
                                              image_name, *args, **kwargs)

    monkeypatch.setattr(phases_mod.cierre_mod, "emitir_csvs", _emitir_csvs_que_revienta)
    monkeypatch.setattr(host.split_images_obj, "convert_dji_image_to_tif", _contar_run1)
    monkeypatch.setattr(host.split_images_obj, "_run_exif_batch_local",
                        _fake_run_exif_batch_local)
    pcb, pbar, psum = _SignalFalsa(), _SignalFalsa(), _SignalFalsa()
    with pytest.raises(RuntimeError):
        host.organizar_plan_apply(cfg, pcb, pbar, psum)

    # Sin esto el test podría pasar en vacío: si el primer run no convirtiera
    # nada, que el segundo tampoco convierta no probaría ninguna reanudación.
    assert convertidas_run1, ("el primer run no llegó a convertir ninguna "
                              "térmica; el test no estaría probando nada")

    manifiesto_db = os.path.join(cfg.output_folder, NOMBRE_CARPETA_MANIFIESTO,
                                 "manifiesto.db")
    assert os.path.isfile(manifiesto_db), (
        "el manifiesto se borró pese a que el run murió a mitad: sin él la "
        "corrida siguiente no puede reanudar y repite todo el trabajo")

    # Segundo run, host nuevo y sin el fallo: cuenta cuántas térmicas convierte.
    monkeypatch.undo()
    convertidas = []

    def _contar_conversiones(input_folder, output_folder, image_name, *args, **kwargs):
        convertidas.append(image_name)
        return _fake_convert_dji_image_to_tif(input_folder, output_folder,
                                              image_name, *args, **kwargs)

    host2 = _HostDePrueba(logger)
    monkeypatch.setattr(host2.split_images_obj, "convert_dji_image_to_tif",
                        _contar_conversiones)
    monkeypatch.setattr(host2.split_images_obj, "_run_exif_batch_local",
                        _fake_run_exif_batch_local)
    pcb, pbar, psum = _SignalFalsa(), _SignalFalsa(), _SignalFalsa()
    host2.organizar_plan_apply(cfg, pcb, pbar, psum)

    assert convertidas == [], (
        "el segundo run volvió a convertir térmicas que el manifiesto ya daba "
        f"por hechas ({convertidas}): la reanudación no está saltando las filas "
        "'hecho'")


def test_tras_un_run_limpio_no_queda_el_manifiesto_en_el_arbol_entregado(
        _inspeccion, logger, monkeypatch):
    """La carpeta interna del motor no se entrega al equipo.

    Incidente que previene: el manifiesto pasó a vivir DENTRO de
    `output_folder` (en `.organizado/`) para poder reanudar, y eso abre el
    riesgo contrario: que se quede ahí para siempre y viaje al Drive del
    cliente junto a las imágenes, o que cuente como contenido inesperado.
    Tras un cierre limpio tiene que desaparecer.
    """
    from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO

    host = _HostDePrueba(logger)
    cfg = _cfg(_inspeccion)
    _correr(host, cfg, monkeypatch)

    assert not os.path.exists(os.path.join(cfg.output_folder, NOMBRE_CARPETA_MANIFIESTO)), \
        "la carpeta interna del manifiesto sobrevivió a un run sin problemas"
    assert not any(rel.startswith(NOMBRE_CARPETA_MANIFIESTO + os.sep)
                   for rel in _arbol_relativo(cfg.output_folder)), \
        "quedaron ficheros del manifiesto en el árbol entregado"
