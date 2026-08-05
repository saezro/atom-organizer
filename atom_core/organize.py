"""Dispatcher headless del pipeline de ATOM Organizer (sin Qt).

`run_task(task, params, emit, advanced=None)` construye la sub-config del task y
ejecuta la orquestación REAL de la GUI (`MainWindow.<método>`) sobre un host
duck-typed. No duplica ni diverge del pipeline. El único acoplamiento Qt del
pipeline son los callbacks de progreso (solo usan `.emit()`), sustituidos aquí
por shims Python que reenvían a `emit(kind, payload)`.

Contrato de `emit`:
  emit('log', str) · emit('summary', str) · emit('progress', int)
  emit('plant', str) · emit('plan', list[str]) · emit('phase', dict)
  emit('stats', dict) · emit('done', None) · emit('error', str)

Eventos del modal de progreso por fases:
  'plant' -> nombre de planta derivado (título del modal).
  'plan'  -> lista ordenada de fases activas según la cfg (checklist inicial).
  'phase' -> {index, total, name}: fase que arranca (1-based). Se detecta
             interceptando el prefijo '---> SUBPROCESO:' del canal summary.
  'stats' -> snapshot de `atom_core.progress_stats.StatsTracker`: imágenes
             analizadas / anunciadas de la fase, reparto RGB vs térmica, y
             rotaciones 270/90/sin rotar acumuladas del run.

Tasks soportados (== los de `run_thread` en gui.py):
  split_images, gen_meta_location, compress_image, gen_thumbnails,
  rename_images, do_extraction, gen_struct, rgb_aerotools,
  gen_thumbnails_aerotools, manual_geotagging, convert_to_tif,
  rgb_cropping, tif_rotating.
"""
from __future__ import annotations

import dataclasses
import os
import re
import sys
import traceback
import typing
from datetime import datetime
from dataclasses import replace
from typing import Callable, Optional

import external_tools as config
import exif as meta_location
import pipeline
import utils
from atom_core.progress_stats import StatsTracker
from gui import MainWindow  # solo se usan funciones (globals del módulo intactos)
from utils import (
    ROTATION_MIN_AGREEMENT_PCT,
    ROTATION_YAW_MARGIN,
    TIF_ROTATION_INTENT_KEY,
    CompressRgbsConfig,
    ConvertToTifConfig,
    ExtractionConfig,
    GenMetaLocationConfig,
    GenStructFolderConfig,
    GenThumbnailsConfig,
    ManualGeotaggingConfig,
    OrganizerLogger,
    RenameImagesConfig,
    RgbAerotoolsConfig,
    RgbCroppingConfig,
    SplitImagesConfig,
    TifRotatingConfig,
    resolve_tif_rotation_intent,
)

EmitFn = Callable[[str, object], None]


class _Signal:
    """Sustituto Qt-free de un Signal: el pipeline solo llama `.emit(x)`."""

    def __init__(self, on_emit: Callable[[object], None]) -> None:
        self._on_emit = on_emit

    def emit(self, value: object) -> None:
        self._on_emit(value)


class _LogStub:
    """Reemplaza `new_log_gui` (LogWindow, Qt). Replica el efecto útil de
    `enable_process()`: rearmar a False el stop cooperativo de los objetos."""

    def __init__(self, objs) -> None:
        self._objs = objs

    def set_obj(self, obj) -> None:  # noqa: D401 — compat con prepare_log
        pass

    def enable_process(self) -> None:
        for obj in self._objs:
            if hasattr(obj, "set_stop"):
                obj.set_stop(False)


class HeadlessHost:
    """Host duck-typed con los mismos objetos de negocio y métodos que
    MainWindow, para invocar la orquestación real sin arrancar Qt."""

    # Métodos reutilizados TAL CUAL de la GUI (se rebindan a este host).
    split_images = MainWindow.split_images
    gen_meta_location = MainWindow.gen_meta_location
    call_to_compress_image = MainWindow.call_to_compress_image
    gen_thumbnails = MainWindow.gen_thumbnails
    gen_thumbnails_for_aerotools = MainWindow.gen_thumbnails_for_aerotools
    rename_images = MainWindow.rename_images
    do_extraction = MainWindow.do_extraction
    gen_struct_folder = MainWindow.gen_struct_folder
    do_rgb_aerotools_processing = MainWindow.do_rgb_aerotools_processing
    do_manual_geotagging = MainWindow.do_manual_geotagging
    do_convert_to_tif = MainWindow.do_convert_to_tif
    # Autodetección de dron por EXIF para la conversión a TIF. La llaman TANTO
    # split_images (subfase TIF, gui.py:2301) COMO do_convert_to_tif (task
    # standalone, gui.py:2761). Sin rebindarla aquí, cualquier corrida con
    # convert_to_tif=True bajo el host headless (== build Windows, donde el gate
    # sys.platform=='win32' la activa) revienta con AttributeError. No usa Qt:
    # solo self.meta_location_obj (ya presente), os.walk y progress_callback.
    _resolve_dron_selector = MainWindow._resolve_dron_selector
    do_rgb_cropping = MainWindow.do_rgb_cropping
    do_tif_rotating = MainWindow.do_tif_rotating
    show_summarize = MainWindow.show_summarize

    def __init__(self) -> None:
        log = OrganizerLogger(
            name="Organizer",
            max_bytes=50 * 1024 * 1024,
            backup_count=7,
            create_file_handler=False,
        )
        # Sin handler de fichero, el logger es un agujero negro: el único handler que
        # tiene es el QueueHandler, y el QueueListener que vacía esa cola solo arranca
        # desde set_handlers(). Resultado en el producto real (webview, .exe sin
        # consola): TODOS los organizer_logger.logger.error() del pipeline se quedaban
        # en una queue.Queue(-1) que nadie consume -> ni llegan a disco ni a consola, y
        # además se acumulan en memoria durante toda la corrida. Por eso los diagnósticos
        # que se escriben ahí (p. ej. el código de salida del conversor DJI) no aparecían
        # en ningún log que pedirle al usuario.
        #
        # Se crea en user_log_dir() -- la MISMA carpeta que ya usa el tee de run_task, que
        # se sabe escribible -- y no el literal "Logs" relativo al CWD, que es lo que en su
        # día reventaba con PermissionError. Si aun así falla, se sigue sin fichero: un
        # logger mudo es malo, pero no debe impedir la corrida.
        try:
            # El StreamHandler que OrganizerLogger crea siempre apunta a sys.stderr, que
            # en un .exe congelado sin consola es None. Dejarlo activo cambiaría un logger
            # mudo por uno que revienta en cada emit(), así que se retira antes de arrancar
            # el listener: en ese entorno no hay consola a la que escribir de todas formas.
            if getattr(sys, "stderr", None) is None and hasattr(log, "console_handler"):
                del log.console_handler
            log.create_file_handler()
            log.set_formatter()
            log.set_handlers()  # fija niveles y arranca el QueueListener
        except Exception:
            pass
        self.organizer_logger_obj = log
        self.compress_image_obj = pipeline.CompressImage(log)
        self.meta_location_obj = meta_location.MetaLocation(log)
        self.split_images_obj = pipeline.SplitImages(log)
        self.gen_struct_folder_obj = pipeline.GenStructFolder(log)
        self.utils_obj = utils.Utils(log)
        self.extraction_obj = pipeline.Extraction(log)
        self.config_obj = config.ReadLoadConfig()
        # La GUI carga Config.ini en MainWindow.__init__ (gui.py:657); el host
        # headless debe hacer lo MISMO o `percentage_by_models` queda {} y el
        # recorte RGB revienta con KeyError sobre el modelo del dron (p.ej.
        # 'M4T') en pipeline.get_percentage_by_model. Mismo path que la GUI.
        self.config_obj.load_new_config(config._user_config_path())
        self.rgb_processing_obj = pipeline.RGBProcessing(log)
        self.rgb_cropping_obj = pipeline.RGBCropping(log)
        self.new_log_gui = _LogStub(
            [
                self.compress_image_obj,
                self.meta_location_obj,
                self.split_images_obj,
                self.gen_struct_folder_obj,
                self.extraction_obj,
                self.rgb_processing_obj,
                self.rgb_cropping_obj,
            ]
        )


# ---- construcción de configs -------------------------------------------------

def _coerce(value, annotation):
    if value is None:
        return None
    if annotation is bool:
        return bool(value)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(str(value).replace(",", "."))
    if annotation is str:
        return str(value)
    return value


def _type_default(annotation):
    return {bool: False, int: 0, float: 0.0, str: ""}.get(annotation, None)


def _build_generic(config_cls, params: dict):
    """Construye un dataclass de config desde `params`, keyeado por nombre de
    campo. Los campos ausentes toman el default por tipo (str="", int=0,
    float=0.0, bool=False). La pantalla React envía los campos que el usuario
    rellena con el MISMO nombre que el field del dataclass."""
    hints = typing.get_type_hints(config_cls)
    kwargs = {}
    for f in dataclasses.fields(config_cls):
        ann = hints.get(f.name, str)
        if f.name in params:
            kwargs[f.name] = _coerce(params[f.name], ann)
        else:
            kwargs[f.name] = _type_default(ann)
    return config_cls(**kwargs)


def _default_split_config(params: dict) -> SplitImagesConfig:
    """SplitImagesConfig con los defaults del .ui (Organizar completo recién
    abierto) + override de los 4 controles de la pantalla principal.
    `organize_images` sigue a la presencia de estadillo (== _run_main_organize)."""
    origen = (params.get("origen") or params.get("input_folder") or "").strip()
    destino = (params.get("destino") or params.get("output_folder") or "").strip()
    estad = (params.get("estadillo") or params.get("estad") or "").strip()
    rename = bool(params.get("rename", params.get("rename_images", True)))
    # Sufijo térmico: sin él (y sin sufijo RGB), la separación por sufijo NO
    # clasifica NINGUNA imagen -> se copian 0 fotos (bug histórico del default
    # vacío del .ui). Por defecto "_T" (convención DJI M3T/M30T/M4T): las _T van
    # a TÉRMICA y el resto a RGB. La pantalla simple lo expone editable; se puede
    # sobreescribir por Modo avanzado. Depende del dron -> el operador lo ajusta.
    thermo = params.get("thermo_suffix", params.get("end_thermo_files"))
    thermo = "_T" if thermo is None else str(thermo).strip()

    # Parámetros radiométricos de la conversión a TIF térmica. El DJI SDK exige
    # rangos válidos (emissivity [0.10~1.00], humidity [20~100]); un 0.0 hace fallar
    # dirp_set_measurement_params en CADA imagen. Defaults de inspección estándar
    # (emisividad 0.95, humedad 70%, rango de temperatura AUTO); se leen de `params`
    # si la pantalla los envía, cayendo al default si vienen ausentes o fuera de rango.
    def _pos_float(value, default):
        try:
            value = float(str(value).replace(",", "."))
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default
    tif_emissivity = _pos_float(params.get("convert_to_tif_emissivity", params.get("emissivity")), 0.95)
    tif_humidity = _pos_float(params.get("convert_to_tif_humidity", params.get("humidity")), 70.0)
    tif_temp_auto = 1 if bool(params.get("convert_to_tif_temp_auto", params.get("temp_auto", True))) else 0

    return SplitImagesConfig(
        input_folder=origen, output_folder=destino,
        end_rgb_extra_files="", end_thermo_files=thermo, end_rgb_files="",
        estad=estad, choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=rename,
        mismatch_hours=0, mismatch_minutes=0, organize_images=bool(estad),
        cropping_rgb=True, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=True, gen_thumbnails=True, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=ROTATION_YAW_MARGIN,
        gen_thumbnails_max_error=ROTATION_MIN_AGREEMENT_PCT, gen_thumbnails_subs_to_angle=ROTATION_YAW_MARGIN,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        # TIF: en Windows usa dji_irp.exe/exiftool.exe; en Linux usa libdirp.so
        # (DJI Thermal SDK v1.7) vía ctypes + exiftool del sistema (ver
        # pipeline._dji_measure_to_raw_linux). Gate por capacidad, no por SO:
        # dji_irp_thermal está disponible en {win, linux}.
        convert_to_tif=config.is_feature_available("dji_irp_thermal"), convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=tif_emissivity, convert_to_tif_humidity=tif_humidity,
        convert_to_tif_temp_auto=tif_temp_auto, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )


# ---- fases del modal de progreso ---------------------------------------------
# Secuencia canónica de las 7 fases de `split_images` (mismo ORDEN en que
# gui.py emite `progress_summarize.emit("---> SUBPROCESO: ...")`). Cada entrada:
# (condición sobre la cfg, nombre legible para el modal). El orden aquí DEBE
# coincidir con el orden de emisión en gui.py:2124/2146/2168/2192/2216/2239/2293.
_SPLIT_PHASES = (
    (lambda c: True, "Separación RGB / térmica"),
    (lambda c: bool(getattr(c, "end_rgb_extra_files", "")), "Carpeta RGB_EXTRA"),
    (lambda c: bool(getattr(c, "organize_images", False)), "Estructura de carpetas"),
    (lambda c: bool(getattr(c, "cropping_rgb", False)), "Recorte RGB"),
    (lambda c: bool(getattr(c, "gen_meta_location", False)), "Meta y geolocalización"),
    (lambda c: bool(getattr(c, "gen_thumbnails", False)), "Rotación"),
    (lambda c: bool(getattr(c, "convert_to_tif", False)), "Convertir a TIF"),
)

_PHASE_PREFIX = "---> SUBPROCESO:"


def _active_split_phases(cfg) -> list:
    """Nombres de las fases que realmente correrán, en orden, según los flags."""
    return [name for cond, name in _SPLIT_PHASES if cond(cfg)]


def _derive_plant(params: dict) -> str:
    """Nombre de planta para el título del modal. Cas (2026-07-22): tomarlo del
    ESTADILLO (basename sin extensión); si no hay estadillo, del destino."""
    estad = (params.get("estadillo") or params.get("estad") or "").strip()
    if estad:
        return os.path.splitext(os.path.basename(estad))[0]
    destino = (params.get("destino") or params.get("output_folder") or "").strip()
    if destino:
        return os.path.basename(destino.rstrip("/\\")) or destino
    return ""


# ---- registro de tasks -------------------------------------------------------
# task -> (nombre de método en HeadlessHost, dataclass de config o None si custom)
_TASKS = {
    "split_images": ("split_images", None),  # config custom (_default_split_config)
    "gen_meta_location": ("gen_meta_location", GenMetaLocationConfig),
    "compress_image": ("call_to_compress_image", CompressRgbsConfig),
    "gen_thumbnails": ("gen_thumbnails", GenThumbnailsConfig),
    "gen_thumbnails_aerotools": ("gen_thumbnails_for_aerotools", GenThumbnailsConfig),
    "rename_images": ("rename_images", RenameImagesConfig),
    "do_extraction": ("do_extraction", ExtractionConfig),
    "gen_struct": ("gen_struct_folder", GenStructFolderConfig),
    "rgb_aerotools": ("do_rgb_aerotools_processing", RgbAerotoolsConfig),
    "manual_geotagging": ("do_manual_geotagging", ManualGeotaggingConfig),
    "convert_to_tif": ("do_convert_to_tif", ConvertToTifConfig),
    "rgb_cropping": ("do_rgb_cropping", RgbCroppingConfig),
    "tif_rotating": ("do_tif_rotating", TifRotatingConfig),
}


def run_task(
    task: str,
    params: dict,
    emit: EmitFn,
    advanced: Optional[dict] = None,
) -> None:
    """Ejecuta un task del pipeline en el hilo actual (bloqueante)."""
    # Tee a fichero: en la ruta webview los logs solo iban al panel de React y
    # se perdían al cerrar (no hay app_*.log como en la ruta Qt). Persistimos
    # cada evento en la carpeta de logs del usuario para diagnosticar corridas
    # post-mortem.
    #
    # Antes esto escribía en "/tmp", ruta que NO existe en Windows: el open()
    # petaba, el except lo tragaba y _run_log quedaba en None. Resultado: en el
    # producto real (Windows) NO se generaba ningún log de corrida, así que ante
    # un fallo del usuario no había nada que pedirle. Ahora usa user_log_dir(),
    # la misma carpeta que ya usa la ruta Qt (%APPDATA%\ATOM-Organizer\Logs).
    _run_log = None
    try:
        _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        from external_tools import user_log_dir
        _log_dir = user_log_dir()
        os.makedirs(_log_dir, exist_ok=True)
        # El PID va en el nombre: dos corridas en el MISMO segundo (p. ej. dos
        # sesiones a la vez) caían antes en el mismo fichero y se INTERLEAVABAN
        # -> un log con origen de una corrida y errores de otra, imposible de leer.
        _run_log = open(os.path.join(_log_dir, f"atom-organizer-run_{_ts}_pid{os.getpid()}.log"),
                        "a", encoding="utf-8")
        # Cabecera de contexto: qué task, con qué directorios y qué PID. Permite
        # detectar de un vistazo cruces de directorio (origen X -> salida Y) y
        # colisiones entre procesos que operan sobre los MISMOS ficheros.
        _ctx = {k: params.get(k) for k in ("origen", "destino", "input_folder",
                                           "estadillo", "dron_selector") if k in params}
        # La versión va en la cabecera porque sin ella un log no es diagnosticable:
        # el 2026-08-04 hubo que deducir a mano si una corrida era 3.2.5 o 3.2.6
        # buscando qué mensajes de error existían en cada commit.
        try:
            from version import __version__ as _app_version
        except Exception:
            _app_version = "desconocida"
        _run_log.write(f"[run] version={_app_version} pid={os.getpid()} task={task} ctx={_ctx}\n")
        # Y los AJUSTES con los que se lanzó: los umbrales de rotación (márgenes de
        # yaw y max_error) deciden si se rota o no, y sin verlos en el log es
        # imposible distinguir "el criterio no dispara" de "el usuario dejó los
        # campos a 0". Se excluyen las rutas, que ya van en ctx.
        _settings = {k: v for k, v in params.items()
                     if k not in _ctx and not isinstance(v, (bytes, bytearray))}
        _run_log.write(f"[params] {_settings}\n")
        # Y el panel AVANZADO tal y como llega del front. Es lo que PISA los defaults
        # del backend (`replace(cfg, **coerced)` más abajo), así que un flag mal puesto
        # aquí cambia el resultado sin dejar rastro en ningún otro sitio. En v3.4.3 el
        # TIFF salía sin girar y no había forma de saber, desde el log, si
        # `convert_to_tiff_rotate_auto` había llegado False o el criterio era ilegible.
        _run_log.write(f"[advanced] {advanced if advanced else '(ninguno: se usan los defaults del backend)'}\n")
        _run_log.flush()
    except Exception:
        _run_log = None

    _emit = emit

    def emit(kind: str, payload=None) -> None:  # noqa: A001 — shadow intencional
        if _run_log is not None:
            try:
                if kind in ("log", "summary", "error", "plant"):
                    _run_log.write(f"[{kind}] {payload}\n")
                elif kind == "phase" and isinstance(payload, dict):
                    _run_log.write(f"[phase] {payload.get('index')}/{payload.get('total')} {payload.get('name')}\n")
                elif kind == "stats" and isinstance(payload, dict):
                    _run_log.write(
                        "[stats] {done}/{total} img (RGB {rgb} / térmica {termica}) · "
                        "rot 270:{rot270} 90:{rot90} sin:{rot_none}\n".format(**payload))
                elif kind in ("done", "progress", "plan"):
                    _run_log.write(f"[{kind}] {payload}\n")
                _run_log.flush()
            except Exception:
                pass
        _emit(kind, payload)

    try:
        if task not in _TASKS:
            emit("error", f"Task desconocido: {task}")
            return
        method_name, config_cls = _TASKS[task]

        if config_cls is None:  # split_images
            cfg = _default_split_config(params)
        else:
            cfg = _build_generic(config_cls, params)
        if advanced:
            # GIRO DEL TIFF. `advanced` puede traer los tres bools de giro a false
            # por DOS motivos que el log de v3.4.4 no permitía distinguir (el dict
            # resultante es byte a byte el mismo): porque el usuario eligió «Sin
            # giro», o porque el front es anterior a v3.4.3 y forzaba el índice 0
            # del select. `__tif_rot_mode` es un sentinel que SOLO manda el front
            # nuevo (schema.js): si no llega, la elección no es expresable y no se
            # pisan los flags -> manda el default sano del backend (auto=True).
            # (la lógica vive en utils.resolve_tif_rotation_intent: `organize`
            # arrastra Qt por `from gui import MainWindow` y no es testeable en CI)
            _rot_mode = advanced.get(TIF_ROTATION_INTENT_KEY)
            advanced, _rot_aviso = resolve_tif_rotation_intent(advanced)
            if _rot_aviso:
                emit("log", _rot_aviso)
            elif _rot_mode is not None:
                emit("log", f"Giro del TIFF: modo pedido por el front = {_rot_mode!r}.")
            # `advanced` llega del front con posibles números como string
            # (folder/file/number se envían crudos). `replace` no coacciona,
            # así que coaccionamos por tipo del dataclass antes de sustituir.
            hints = typing.get_type_hints(type(cfg))
            coerced = {
                k: _coerce(v, hints.get(k, str))
                for k, v in advanced.items()
                if k in hints
            }
            cfg = replace(cfg, **coerced)

        # Y si el «Sin giro» ES la elección del usuario, que no sea silencioso: el
        # TIFF sale 640x512 y sin copias _ROT, que es exactamente el síntoma que se
        # reporta como fallo. En el canal `summary` (visible en la UI), no en `log`.
        if getattr(cfg, "convert_to_tif", False) \
                and not getattr(cfg, "convert_to_tiff_rotate_auto", False) \
                and not getattr(cfg, "convert_to_tiff_rotate_90", False) \
                and not getattr(cfg, "convert_to_tiff_rotate_minus_90", False):
            emit("summary", "AVISO: el giro del TIFF está en «Sin giro»; el TIFF "
                            "térmico saldrá sin girar y no se escribirán copias _ROT. "
                            "Cámbialo en Avanzado → Convertir DJI → TIF → Giro.")

        # Normalización radiométrica (red de seguridad): emissivity/humidity DEBEN
        # caer en el rango del DJI SDK (emissivity [0.10~1.00], humidity [20~100]).
        # El front (schema.js) los envía a 0 por defecto y el override de `advanced`
        # PISA los defaults sanos de _default_split_config SIN re-validar -> 0.0 llega
        # a dirp_set_measurement_params y falla rc=-3 en CADA imagen. La tarea directa
        # convert_to_tif (vía _build_generic) tiene el mismo problema. Clampeamos aquí
        # sobre el cfg FINAL, sea cual sea el origen del valor, y avisamos.
        def _in_range(v, lo, hi):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return False
            return lo <= v <= hi
        # (campo_emisividad, campo_humedad) según el dataclass: SplitImagesConfig usa
        # el prefijo convert_to_tif_*, ConvertToTifConfig usa el nombre pelado.
        for _emis_f, _hum_f in (("convert_to_tif_emissivity", "convert_to_tif_humidity"),
                                ("emissivity", "humidity")):
            if hasattr(cfg, _emis_f) and not _in_range(getattr(cfg, _emis_f), 0.10, 1.00):
                emit("summary", f"AVISO: emisividad {getattr(cfg, _emis_f)} fuera de rango [0.10-1.00]; se usa 0.95.")
                cfg = replace(cfg, **{_emis_f: 0.95})
            if hasattr(cfg, _hum_f) and not _in_range(getattr(cfg, _hum_f), 20.0, 100.0):
                emit("summary", f"AVISO: humedad {getattr(cfg, _hum_f)} fuera de rango [20-100]; se usa 70.")
                cfg = replace(cfg, **{_hum_f: 70.0})

        # Misma red de seguridad para el criterio de rotación, y por el mismo motivo:
        # el margen de yaw y el porcentaje de acuerdo llegan a 0 desde el front o desde
        # `advanced`, y un margen de 0 deja el intervalo de decisión VACÍO -> el vuelo
        # entero se va por "no rotar" con un "OK" en el log y NADA sale girado. No hay
        # ningún uso legítimo del 0 en estos campos, así que se corrige sobre el cfg
        # FINAL, sea cual sea el origen del valor. Los tres juegos de nombres son los
        # de SplitImagesConfig (gen_thumbnails_*) y GenThumbnailsConfig (pelado y
        # aerotools_*).
        for _pref in ("gen_thumbnails_", "", "aerotools_"):
            for _campo, _sano, _que in ((f"{_pref}add_to_angle", ROTATION_YAW_MARGIN, "margen de yaw +"),
                                        (f"{_pref}subs_to_angle", ROTATION_YAW_MARGIN, "margen de yaw −"),
                                        (f"{_pref}max_error", ROTATION_MIN_AGREEMENT_PCT, "% de imágenes que deben girar igual")):
                if not hasattr(cfg, _campo):
                    continue
                try:
                    _actual = float(getattr(cfg, _campo))
                except (TypeError, ValueError):
                    _actual = 0.0
                if _actual <= 0:
                    emit("summary", f"AVISO: {_que} venía a {getattr(cfg, _campo)}, que impediría rotar ninguna imagen; se usa {_sano}.")
                    cfg = replace(cfg, **{_campo: _sano})

        # Guarda: en separación por sufijo, si NO hay sufijo térmico NI RGB, la
        # clasificación no asigna ninguna imagen y se copiarían 0 fotos en
        # silencio (bug histórico). Avisar en claro en vez de terminar vacío.
        if task == "split_images" and not getattr(cfg, "choose_mode_size", False) \
                and not getattr(cfg, "end_thermo_files", "") \
                and not getattr(cfg, "end_rgb_files", ""):
            emit("error", "No se puede separar: falta el sufijo térmico (o RGB). "
                          "Indica el sufijo de las imágenes térmicas de tu dron "
                          "(p. ej. \"_T\") o cambia a separación por tamaño.")
            return

        # Guarda: la carpeta de SALIDA debe estar vacía (decisión de Cas
        # 2026-07-22). Si tiene residuos de una corrida previa, la separación no
        # parte de cero: `unique_dest` genera duplicados `_1/_2` y el recorte
        # re-procesa `_CROP` viejos -> errores "image file is truncated". Mejor
        # rechazar en claro que fusionar en silencio. El usuario vacía la carpeta
        # o elige una nueva.
        if task == "split_images":
            _out = getattr(cfg, "output_folder", "")
            if _out and os.path.isdir(_out) and os.listdir(_out):
                emit("error", "La carpeta de salida no está vacía: "
                              f"\"{_out}\". Vacíala o elige una carpeta vacía "
                              "antes de organizar (una corrida sobre residuos "
                              "genera duplicados y errores de recorte).")
                return

        # --- modal de progreso: título + checklist de fases -------------------
        plant = _derive_plant(params)
        if plant:
            emit("plant", plant)
        plan_names = _active_split_phases(cfg) if task == "split_images" else []
        if plan_names:
            emit("plan", plan_names)

        # Interceptar el inicio de cada fase (prefijo en el canal summary) y
        # re-emitirlo como evento estructurado `phase` para el modal.
        #
        # Además instrumentamos duración y errores POR FASE: el pipeline no
        # aborta ante errores por-imagen (los cuenta y sigue), así que la UI
        # debe reflejar "terminado con N errores" y marcar en ROJO la fase que
        # falló — no pintar verde a secas. El conteo real lo da el propio
        # pipeline en el summary de cada subproceso ("Ha habido N errores");
        # "HAN EXISTIDO ERRORES" es el marcador de cierre por si no hay conteo.
        phase_counter = {"i": 0}
        _t = {"start": datetime.now(), "phase_start": None,
              "cur_errors": 0, "total_errors": 0, "total_warnings": 0}
        _ERR_COUNT_RE = re.compile(r"[Hh]a habido (\d+) error")

        def _scan_errors(text: str) -> None:
            m = _ERR_COUNT_RE.search(text)
            if m:
                n = int(m.group(1))
                _t["cur_errors"] += n
                _t["total_errors"] += n
            elif "HAN EXISTIDO ERRORES" in text and _t["cur_errors"] == 0:
                _t["cur_errors"] += 1
                _t["total_errors"] += 1
            # Avisos NO fatales (SIN_ORDENAR): marcador único emitido una sola vez
            # en el summary final -> el run termina en ámbar, no en rojo.
            if "HA HABIDO AVISOS" in text:
                _t["total_warnings"] += 1

        def _close_phase(idx: int) -> dict:
            start = _t["phase_start"]
            dur = (datetime.now() - start).total_seconds() if start else 0.0
            return {"index": idx, "duration": round(dur, 1),
                    "errors": _t["cur_errors"]}

        # Estadísticas en vivo (imágenes analizadas, RGB vs térmica, rotaciones).
        # Se derivan del texto que el pipeline ya emite; ver progress_stats.
        stats = StatsTracker()

        def _emit_stats() -> None:
            emit("stats", stats.snapshot())

        def _on_summary(s) -> None:
            text = str(s)
            _scan_errors(text)
            if stats.on_line(text):
                _emit_stats()
            emit("summary", text)
            if text.startswith(_PHASE_PREFIX):
                phase_counter["i"] += 1
                idx = phase_counter["i"]
                # Cerrar la fase anterior (duración + errores) antes de arrancar.
                prev = None
                if _t["phase_start"] is not None and idx > 1:
                    prev = _close_phase(idx - 1)
                _t["phase_start"] = datetime.now()
                _t["cur_errors"] = 0
                if idx - 1 < len(plan_names):
                    name = plan_names[idx - 1]
                else:  # fase dinámica (tasks sin plan predefinido)
                    name = text[len(_PHASE_PREFIX):].strip().rstrip(".")
                emit("phase", {"index": idx, "total": len(plan_names),
                               "name": name, "prev": prev})
                # Los contadores por fase arrancan de cero con la fase.
                stats.start_phase(idx, name)
                _emit_stats()

        def _on_log(s) -> None:
            text = str(s)
            # El pipeline original de Aerotools emite un "." por cada imagen como
            # spinner textual. En el panel y en el log solo es ruido (ya hay barra
            # de progreso numérica): se descarta del texto, pero se CUENTA — es la
            # única señal por-imagen que da el pipeline, y de ahí sale el "N de M
            # analizadas" del modal.
            if text.strip(" .\t\r\n") == "":
                if text.strip():  # puntos, no una línea en blanco
                    for _ in range(text.count(".")):
                        if stats.on_image():
                            _emit_stats()
                return
            _scan_errors(text)
            if stats.on_line(text):
                _emit_stats()
            emit("log", text)

        host = HeadlessHost()
        pcb = _Signal(_on_log)
        pbar = _Signal(lambda i: emit("progress", int(i)))
        psum = _Signal(_on_summary)

        getattr(host, method_name)(cfg, pcb, pbar, psum)
        # Snapshot final: el throttling de imágenes puede haber dejado sin emitir
        # las últimas < IMAGE_EMIT_EVERY, y el resumen de rotación del modal se
        # lee de aquí.
        _emit_stats()
        # Cerrar la última fase y emitir el resumen final con estado agregado.
        last = _close_phase(phase_counter["i"]) if _t["phase_start"] else None
        elapsed = round((datetime.now() - _t["start"]).total_seconds(), 1)
        if _t["total_errors"] > 0:
            _status = "errors"
        elif _t["total_warnings"] > 0:
            _status = "warning"
        else:
            _status = "ok"
        emit("done", {
            "status": _status,
            "errors": _t["total_errors"],
            "warnings": _t["total_warnings"],
            "elapsed": elapsed,
            "last": last,
        })
    except Exception as exc:  # noqa: BLE001 — se reenvía al front
        emit("error", f"{type(exc).__name__}: {exc}")
        emit("log", traceback.format_exc())
    finally:
        if _run_log is not None:
            try:
                _run_log.close()
            except Exception:
                pass


def run_organize(params: dict, emit: EmitFn, advanced: Optional[dict] = None) -> None:
    """Atajo de la pantalla principal: 'Organizar completo' (task split_images)."""
    run_task("split_images", params, emit, advanced)
