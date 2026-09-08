"""Índice del organizado: decide sin tocar píxeles.

Hace UNA pasada de metadatos (EXIF/XMP) sobre `cfg.input_folder`, la cruza
con el estadillo, y decide en memoria todo lo que hoy se decidía a trozos
repartido entre las 7 fases del motor viejo: a qué vuelo pertenece cada
imagen, con qué nombre sale, con qué ángulo se gira, con qué % se recorta y
en qué ruta final acaba. El resultado se persiste en el manifiesto
(`atom_core.manifiesto.Manifiesto`); el apply (Tarea 4) es quien luego
escribe de verdad.

No reimplementa ningún criterio: llama a las funciones que ya existen y que
el motor viejo usa (`Pipeline.ventana_horaria_vuelo`, `Pipeline.nombre_destino`,
`Pipeline.get_percentage_by_model`, `Pipeline.read_auto_rotate_degree`), para
que la decisión salga idéntica a la de siempre.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import utils
from atom_core import almacen
from atom_core import estadillo as estadillo_mod
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from rjpeg_a_tiff import EXTS_FUENTE

# Único nombre de la carpeta extra: fija la inconsistencia de casing entre
# `RGB_Extra` (pipeline.py:2829) y `RGB_extra` (pipeline.py:1377) que en NTFS
# no se nota pero en el bucket sí (ver Tarea 0, punto 6 de las correcciones a
# la spec).
NOMBRE_CARPETA_RGB_EXTRA = "RGB_Extra"

# Carpeta de destino de las imágenes que no caen en ninguna ventana horaria
# de ningún vuelo del estadillo.
NOMBRE_CARPETA_SIN_ORDENAR = "SIN_ORDENAR"

# Tipos que reciben tratamiento de RGB (compresión + recorte): además de
# "RGB", el tercer grupo de sufijos ("RGB_Extra") corre por el MISMO camino
# en el motor viejo -`iterate_folders_for_rgb_cropping` (pipeline.py:4003)
# recorre todo el árbol de salida salvo `TERMICA`, y `iterate_folders`
# (pipeline.py:2829) comprime "RGB_Extra" con el mismo flag `compress_checked`
# que "RGB"-. Solo "TERMICA" queda fuera de este conjunto.
TIPOS_RGB = frozenset({"RGB", NOMBRE_CARPETA_RGB_EXTRA})


class ErrorColisionEstadillo(Exception):
    """Se han fusionado estadillos con el mismo (PB, vuelo) y fechas
    distintas. `construir_indice` aborta con esta excepción ANTES de escribir
    ninguna fila en el manifiesto, no a mitad como hacía el motor viejo (que
    ya había movido parte de las imágenes cuando se topaba con la colisión)."""


@dataclass
class _MetadatosImagen:
    """Lo que la única pasada de EXIF/XMP lee de una imagen. Cada campo que
    no se puede leer queda a `None`: una imagen ilegible no puede tumbar el
    índice entero, solo esa fila queda peor decidida (unassigned, sin
    ángulo…). `gps` se lee porque el cierre (Tarea 5) lo necesitará para el
    CSV de localización, aunque el manifiesto de esta tarea no le guarde
    columna propia todavía."""

    ruta: str
    nombre: str
    timestamp: object
    modelo: str | None
    yaw: float | None
    gps: object


def _sin_utils_helper() -> "utils.Utils":
    """Instancia mínima de `Utils` solo para `get_nombres_columnas` (no toca
    el logger ni hace I/O). Mismo patrón que `atom_core.estadillo._UTILS`."""
    return utils.Utils.__new__(utils.Utils)


def _clasificar_tipo(nombre: str, cfg) -> str:
    """RGB / TERMICA / RGB_Extra según el sufijo del nombre de fichero, con
    los mismos sufijos (`cfg.end_thermo_files`, `cfg.end_rgb_extra_files`,
    `cfg.end_rgb_files`) que usa `iterate_folders` en el motor viejo para
    repartir el mismo origen en varias pasadas. Sin sufijo que case, RGB por
    defecto (el caso mayoritario: cámara sin marcar terminación propia)."""
    base, _ext = os.path.splitext(nombre)
    if cfg.end_thermo_files and base.endswith(cfg.end_thermo_files):
        return "TERMICA"
    if cfg.end_rgb_extra_files and base.endswith(cfg.end_rgb_extra_files):
        return NOMBRE_CARPETA_RGB_EXTRA
    return "RGB"


def _nombre_carpeta_vuelo(pb: str, vuelo: str, include_v: bool) -> str:
    """Mismo patrón que `GenStructFolder.gen_folder_struct` (pipeline.py:1317-1322),
    sin el sufijo de fecha de colisión: `construir_indice` aborta ANTES de
    llegar a necesitarlo (ver `ErrorColisionEstadillo`)."""
    if include_v:
        return f"PB{pb}_V{vuelo}"
    return f"PB{pb}_{vuelo}"


def _listar_imagenes(input_folder: str) -> list[str]:
    """Rutas absolutas de toda imagen FUENTE (`EXTS_FUENTE`) bajo
    `input_folder`, recursivo. Es la única pasada de listado: el resto del
    índice trabaja sobre esta lista, no vuelve a tocar el árbol de origen."""
    rutas: list[str] = []
    if almacen.es_uri_gcs(input_folder):
        rutas.extend(_listar_imagenes_gcs(input_folder))
        return sorted(rutas)
    if not os.path.isdir(input_folder):
        return []
    for raiz, _dirs, ficheros in os.walk(input_folder):
        for nombre in ficheros:
            if os.path.splitext(nombre)[1].lower() in EXTS_FUENTE:
                rutas.append(os.path.join(raiz, nombre))
    return sorted(rutas)


def _listar_imagenes_gcs(carpeta: str) -> list[str]:
    rutas: list[str] = []
    for nombre in almacen.listar_ficheros(carpeta):
        if os.path.splitext(nombre)[1].lower() in EXTS_FUENTE:
            rutas.append(almacen.unir(carpeta, nombre))
    for subcarpeta in almacen.listar_subcarpetas(carpeta):
        rutas.extend(_listar_imagenes_gcs(almacen.unir(carpeta, subcarpeta)))
    return rutas


def _leer_metadatos(ruta: str, exif, progress_callback) -> _MetadatosImagen:
    """Una imagen -> sus metadatos, o `None` en cada campo que no se pudo
    leer. Cada lectura va en su propio `try/except`: que falle el yaw no
    tiene por qué tirar también el timestamp de la misma imagen."""
    nombre = os.path.basename(ruta)

    timestamp = None
    try:
        timestamp = exif.get_timestamp_from_image(ruta)
    except Exception:  # noqa: BLE001 — una imagen ilegible no tumba el índice
        timestamp = None

    modelo = None
    try:
        modelo = exif.get_model(ruta, progress_callback)
    except Exception:  # noqa: BLE001
        modelo = None

    yaw = None
    try:
        yaw_bruto = exif.get_gimbal_yaw_pitch(ruta)[0]
        yaw = float(yaw_bruto)
    except Exception:  # noqa: BLE001
        yaw = None

    gps = None
    try:
        gps = exif.leerLatitudLongitudAltitud_exif_DJI(ruta, progress_callback)
    except Exception:  # noqa: BLE001
        gps = None

    return _MetadatosImagen(ruta=ruta, nombre=nombre, timestamp=timestamp,
                            modelo=modelo, yaw=yaw, gps=gps)


def _ventanas_por_vuelo(estadillo_df, nombres_columnas: dict, pipeline, cfg,
                        progress_callback) -> list[dict]:
    """Franja horaria y (pb, vuelo) de cada fila del estadillo fusionado, EN
    EL ORDEN DEL ESTADILLO (ese orden es el desempate cuando dos vuelos
    solapan: gana el primero, igual que `obtenerListaImagenesVuelo`). Una
    fila sin hora de inicio o fin legible se descarta con un aviso, igual que
    hace `gen_folder_struct` (pipeline.py:1301-1311), en vez de reventar el
    índice entero por una celda vacía."""
    col_pb = nombres_columnas["PB"]
    col_vuelo = nombres_columnas["Vuelo"]
    col_fecha = nombres_columnas["Fecha"]
    col_inicio = nombres_columnas["Hora_de_inicio"]
    col_final = nombres_columnas["Hora_final"]

    ventanas: list[dict] = []
    for indice in range(len(estadillo_df)):
        pb = str(estadillo_df[col_pb].iloc[indice]).strip()
        vuelo = str(estadillo_df[col_vuelo].iloc[indice]).strip()
        fecha = str(estadillo_df[col_fecha].iloc[indice]).strip()
        hora_inicio = str(estadillo_df[col_inicio].iloc[indice]).strip()
        hora_final = str(estadillo_df[col_final].iloc[indice]).strip()

        if not hora_inicio or hora_inicio.lower() == "nan":
            progress_callback.emit(
                f"\nWARNING: No existe hora inicio en PB {pb} y vuelo {vuelo}\n")
            continue
        if not hora_final or hora_final.lower() == "nan":
            progress_callback.emit(
                f"\nWARNING: No existe hora final en PB {pb} y vuelo {vuelo}\n")
            continue

        inicio, fin = pipeline.ventana_horaria_vuelo(
            fecha, hora_inicio, hora_final, cfg.seconds_range,
            cfg.mismatch_hours, cfg.mismatch_minutes)
        ventanas.append({"pb": pb, "vuelo": vuelo, "inicio": inicio, "fin": fin})
    return ventanas


def _asignar_vuelo(dato: _MetadatosImagen, ventanas: list[dict]) -> dict | None:
    """Primera ventana (en orden del estadillo) cuyo `(inicio, fin)` contiene
    ESTRICTAMENTE el timestamp de la imagen, igual que
    `obtenerListaImagenesVuelo` (pipeline.py:1528: `inicio < ts < fin`). Sin
    timestamp, o sin ninguna ventana que la reclame, la imagen es
    `unassigned`."""
    if dato.timestamp is None:
        return None
    for ventana in ventanas:
        if ventana["inicio"] < dato.timestamp < ventana["fin"]:
            return ventana
    return None


def _consenso_de_angulo_por_vuelo(asignaciones: list[tuple[_MetadatosImagen, dict | None]],
                                  pipeline, cfg, output_folder: str,
                                  progress_callback) -> dict[tuple[str, str], int]:
    """Un único ángulo por `(pb, vuelo)`, calculado UNA vez y compartido por
    todas las filas de ese vuelo (RGB y térmica): este reparto por vuelo, no
    por carpeta, es lo que corrige el bug que motiva el proyecto entero (hoy
    el TIFF y su JPG pueden acabar con criterios de giro distintos).

    Si ya existe el CSV de criterio de un re-proceso anterior, se reutiliza
    con `pipeline.read_auto_rotate_degree` (nunca reinventa lo que ya se
    decidió). Si no existe -el caso normal en una corrida nueva, porque ese
    CSV lo escribe el cierre A PARTIR de este mismo consenso- se calcula a
    partir de los yaw ya leídos en la pasada de metadatos, con el mismo
    criterio de mayoría por umbral que `gen_thumbnails_and_rotate`
    (pipeline.py:2004): tres bandas (90º / 270º / sin girar) delimitadas por
    `gen_thumbnails_add_to_angle` / `gen_thumbnails_subs_to_angle`, y se
    necesita superar `gen_thumbnails_max_error`% de las imágenes del vuelo
    para que una banda gane. Sin consenso claro, o en modo manual
    desactivado, 0."""
    yaws_por_vuelo: dict[tuple[str, str], list[float]] = {}
    for dato, ventana in asignaciones:
        if ventana is None or dato.yaw is None:
            continue
        clave = (ventana["pb"], ventana["vuelo"])
        yaws_por_vuelo.setdefault(clave, []).append(dato.yaw)

    angulos: dict[tuple[str, str], int] = {}

    if not cfg.choose_mode_auto:
        # Modo manual: el mismo ángulo fijo para todos los vuelos, sin mirar
        # el yaw de ninguna imagen (igual que `gen_thumbnails_and_rotate_manual`).
        angulo_manual = 90 if cfg.gen_thumbnails_rotate_90 else 0
        for clave in yaws_por_vuelo:
            angulos[clave] = angulo_manual
        return angulos

    add_to_angle, subs_to_angle, max_error = utils.sane_rotation_criteria(
        cfg.gen_thumbnails_add_to_angle, cfg.gen_thumbnails_subs_to_angle,
        cfg.gen_thumbnails_max_error)
    lim_max_90 = add_to_angle + 90
    lim_min_90 = 90 - subs_to_angle
    lim_max_270 = add_to_angle - 90
    lim_min_270 = (-90) - subs_to_angle

    for (pb, vuelo), yaws in yaws_por_vuelo.items():
        carpeta_vuelo = almacen.unir(output_folder, _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v))
        candidato_csv = almacen.unir(
            carpeta_vuelo, "CSVs", utils.CRITERIO_DIRNAME,
            f"{_nombre_carpeta_vuelo(pb, vuelo, cfg.include_v)}_Videofiles.csv")
        if almacen.existe_ruta(candidato_csv):
            angulos[(pb, vuelo)] = pipeline.read_auto_rotate_degree(
                almacen.unir(carpeta_vuelo, "TERMICA"), progress_callback)
            continue

        rotate_90 = sum(1 for yaw in yaws if lim_min_90 < yaw < lim_max_90)
        rotate_270 = sum(1 for yaw in yaws if lim_min_270 < yaw < lim_max_270)
        total = len(yaws)
        if total and rotate_270 and (rotate_270 / total) > (max_error / 100):
            angulos[(pb, vuelo)] = 270
        elif total and rotate_90 and (rotate_90 / total) > (max_error / 100):
            angulos[(pb, vuelo)] = 90
        else:
            angulos[(pb, vuelo)] = 0

    return angulos


def _pct_recorte(dato: _MetadatosImagen, tipo: str, cfg, pipeline) -> float | None:
    """El % de recorte aplica a RGB y RGB_Extra (el recorte centrado es cosa
    de `RGBCropping`, nunca de la térmica; ver `TIPOS_RGB`:
    `iterate_folders_for_rgb_cropping`, pipeline.py:4003, recorre TODO el
    árbol de salida salvo `TERMICA`, así que RGB_Extra se recorta igual que
    RGB). Sale de `Config.ini` vía `get_percentage_by_model` cuando el modo
    es automático; en manual, el valor de la interfaz (`cfg.crop_percentage`)
    manda sin mirar el modelo."""
    if tipo not in TIPOS_RGB or not cfg.cropping_rgb:
        return None
    if cfg.cropping_mode_auto:
        if not dato.modelo:
            return None
        # El diccionario modelo->% viene de `Config.ini` (`external_tools.py`),
        # que hoy cuelga del host de fases como `config_obj.percentage_by_models`
        # y no de `cfg` (`SplitImagesConfig` no lo lleva). Aquí se pide al mismo
        # `pipeline` que ya agrupa el resto de funciones reutilizadas: es quien
        # tiene que exponerlo como `pipeline.percentage_by_models`.
        return float(pipeline.get_percentage_by_model(dato.modelo, pipeline.percentage_by_models))
    return float(cfg.crop_percentage)


def _construir_fila(dato: _MetadatosImagen, ventana: dict | None,
                    angulos: dict[tuple[str, str], int], cfg, pipeline) -> FilaManifiesto:
    tipo = _clasificar_tipo(dato.nombre, cfg)
    unassigned = ventana is None

    nombre_nuevo = pipeline.nombre_destino(
        dato.nombre, cfg.input_folder, cfg.rename_images,
        cfg.mismatch_hours, cfg.mismatch_minutes, ruta_local=dato.ruta)
    nombre_final = nombre_nuevo if nombre_nuevo else dato.nombre

    if unassigned:
        pb = vuelo = None
        angulo_giro = 0
        carpeta_destino = almacen.unir(cfg.output_folder, NOMBRE_CARPETA_SIN_ORDENAR, tipo)
    else:
        pb, vuelo = ventana["pb"], ventana["vuelo"]
        angulo_giro = angulos.get((pb, vuelo), 0)
        carpeta_destino = almacen.unir(
            cfg.output_folder, _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v), tipo)

    ruta_salida_original = almacen.unir(carpeta_destino, nombre_final)

    pct_recorte = _pct_recorte(dato, tipo, cfg, pipeline)
    ruta_salida_crop = None
    if pct_recorte is not None:
        raiz, ext = os.path.splitext(nombre_final)
        ruta_salida_crop = almacen.unir(carpeta_destino, f"{raiz}_CROP{ext}")

    ruta_salida_tiff = None
    if tipo == "TERMICA" and cfg.convert_to_tif:
        raiz, _ext = os.path.splitext(nombre_final)
        ruta_salida_tiff = almacen.unir(carpeta_destino, f"{raiz}.tif")

    # RGB_Extra comprime con el MISMO flag que RGB (`Pipeline.iterate_folders`,
    # pipeline.py:2829: `compress_checked` es el único condicional, sin
    # distinguir el tercer grupo de sufijos del segundo). Ver `TIPOS_RGB`.
    comprime = bool(cfg.compress_rgb) if tipo in TIPOS_RGB else False

    return FilaManifiesto(
        ruta_origen=dato.ruta,
        tipo=tipo,
        timestamp_exif=dato.timestamp.isoformat() if dato.timestamp is not None else None,
        modelo=dato.modelo,
        pb=pb,
        vuelo=vuelo,
        nombre_nuevo=nombre_nuevo,
        angulo_giro=angulo_giro,
        pct_recorte=pct_recorte,
        comprime=comprime,
        ruta_salida_original=ruta_salida_original,
        ruta_salida_crop=ruta_salida_crop,
        ruta_salida_tiff=ruta_salida_tiff,
        unassigned=unassigned,
    )


def construir_indice(
    cfg,
    pipeline,
    exif,
    manifiesto: "Manifiesto",
    progress_callback,
    progress_bar,
    progress_summarize,
    max_hilos: int | None = None,
) -> dict:
    """Construye el manifiesto completo del run: una pasada de metadatos
    sobre `cfg.input_folder`, cruzada con el estadillo, decidiendo vuelo,
    nombre, ángulo, % de recorte y rutas de salida de cada imagen. No
    escribe ni mueve NINGUNA imagen: solo inserta filas en `manifiesto`.

    Devuelve `{"total", "unassigned", "sin_timestamp", "vuelos"}`.
    """
    progress_summarize.emit("---> SUBPROCESO: Índice")

    rutas_estadillo = estadillo_mod.desempaquetar_rutas(cfg.estad)
    estadillo_df = estadillo_mod.combinar_estadillos(rutas_estadillo)

    utils_helper = _sin_utils_helper()
    nombres_columnas = utils_helper.get_nombres_columnas(list(estadillo_df.columns.values))

    colisiones = estadillo_mod.detectar_colisiones_pb_vuelo(estadillo_df, nombres_columnas)
    if colisiones:
        raise ErrorColisionEstadillo(
            "El estadillo fusionado tiene (PB, Vuelo) repetidos con fecha "
            f"distinta, y eso desambigua la carpeta de destino: {colisiones}. "
            "Revisa los estadillos de origen antes de reintentar.")

    ventanas = _ventanas_por_vuelo(estadillo_df, nombres_columnas, pipeline, cfg,
                                   progress_callback)
    imagenes = _listar_imagenes(cfg.input_folder)

    hilos = max_hilos or utils.workers_para_lote()
    if imagenes:
        with ThreadPoolExecutor(max_workers=hilos) as ejecutor:
            metadatos = list(ejecutor.map(
                lambda ruta: _leer_metadatos(ruta, exif, progress_callback), imagenes))
    else:
        metadatos = []

    asignaciones = [(dato, _asignar_vuelo(dato, ventanas)) for dato in metadatos]
    angulos = _consenso_de_angulo_por_vuelo(asignaciones, pipeline, cfg,
                                            cfg.output_folder, progress_callback)

    filas = [_construir_fila(dato, ventana, angulos, cfg, pipeline)
             for dato, ventana in asignaciones]
    manifiesto.insertar_muchas(filas)

    total = len(metadatos)
    unassigned = sum(1 for _dato, ventana in asignaciones if ventana is None)
    sin_timestamp = sum(1 for dato, _ventana in asignaciones if dato.timestamp is None)

    progress_bar.emit(100)
    return {
        "total": total,
        "unassigned": unassigned,
        "sin_timestamp": sin_timestamp,
        "vuelos": len(ventanas),
    }
