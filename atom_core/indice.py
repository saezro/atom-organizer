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

import datetime
import io
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

import exifread
import PIL.Image

import exif as exif_mod
import utils
from atom_core import almacen
from atom_core import equipo as equipo_mod
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

# Carpeta de destino, HERMANA de TERMICA/RGB/RGB_Extra en la raíz de
# `output_folder`, de las imágenes cuyo PB en el estadillo es "GENERALES"
# (fotos de contexto de la planta, no de un vuelo concreto): planas, sin
# subcarpeta PB/vuelo, sin girar (ver `_es_pb_generales`/`_construir_fila`).
# Formato confirmado en bucket (`gs://plantas_pv_nl/KL88|CELSO/.../2026/
# FOTOS_GENERALES/`): exacto, mayúsculas y guion bajo.
NOMBRE_CARPETA_GENERALES = "FOTOS_GENERALES"

# Variantes de escritura del PB "genérico" que el estadillo puede traer,
# todas normalizadas (`strip().upper()`) antes de comparar en
# `_es_pb_generales`.
_PB_GENERALES_VARIANTES = frozenset({"GENERALES", "GENERAL", "FOTOS GENERALES", "FOTOS_GENERALES"})

# Tipos que reciben tratamiento de RGB (compresión + recorte): además de
# "RGB", el tercer grupo de sufijos ("RGB_Extra") corre por el MISMO camino
# en el motor viejo -`iterate_folders_for_rgb_cropping` (pipeline.py:4003)
# recorre todo el árbol de salida salvo `TERMICA`, y `iterate_folders`
# (pipeline.py:2829) comprime "RGB_Extra" con el mismo flag `compress_checked`
# que "RGB"-. Solo "TERMICA" queda fuera de este conjunto.
TIPOS_RGB = frozenset({"RGB", NOMBRE_CARPETA_RGB_EXTRA})

#: Prefijo del canal `progress_summarize` (texto) que `atom_core.organize`
#: intercepta y convierte en `emit("stats", {...})` con el desglose del
#: índice — ver `organize._STATS_INDICE_PREFIX`. Mismo mecanismo que
#: `apply._STATS_APPLY_PREFIX`: el índice no tiene acceso al `emit` real de
#: `organize.run_task`, solo a las tres señales de siempre.
STATS_INDICE_PREFIX = "---> STATS_INDICE: "


class ErrorColisionEstadillo(Exception):
    """Se han fusionado estadillos con el mismo (PB, vuelo) Y LA MISMA fecha
    procedentes de ficheros de origen DISTINTOS (dos pilotos con el mismo
    PB+Vuelo el mismo día: `estadillo.detectar_colisiones_mismo_dia`), un
    choque que el sufijo de fecha no puede desambiguar. `construir_indice`
    aborta con esta excepción ANTES de escribir ninguna fila en el
    manifiesto, no a mitad como hacía el motor viejo (que ya había movido
    parte de las imágenes cuando se topaba con la colisión).

    Una colisión con FECHA DISTINTA entre estadillos (`estadillo.
    detectar_colisiones_pb_vuelo`) YA NO aborta: se resuelve con el mismo
    sufijo de fecha que aplica `pipeline.GenStructFolder.gen_folder_struct`,
    ver `_nombre_carpeta_vuelo`."""


class ErrorEstadillosMismaCarpeta(Exception):
    """Dos o más ficheros de estadillo detectados/aportados viven en la MISMA
    carpeta (`estadillo.agrupar_rutas_por_carpeta`). Antes se fusionaban en
    silencio con `combinar_estadillos` (gana el primero en los solapes); eso
    es justo el caso que motivó el scoping por carpeta -la carpeta es la
    unidad de "un estadillo"-, así que dos ficheros ahí dentro casi siempre
    es un despiste (dos pilotos que no separaron sus CSV en subcarpetas
    propias), nunca una decisión deliberada. `construir_indice` aborta ANTES
    de leer ninguna imagen ni escribir ninguna fila, con la carpeta y los
    nombres de los ficheros en conflicto en el mensaje (decisión de
    Rodrigo)."""


class ErrorModeloSinRecorte(Exception):
    """El recorte automático de RGB está activado (`cfg.cropping_rgb` +
    `cfg.cropping_mode_auto`) pero alguna imagen trae un modelo EXIF de dron
    sin entrada en `Config.ini` (`percentage_by_models`). Antes, esto
    reventaba tarde con un `KeyError` crudo dentro de `Pipeline.
    get_percentage_by_model` (pipeline.py:4328), después de que el pool de
    hilos ya hubiese leído el EXIF completo de TODAS las imágenes — todo ese
    tiempo perdido por una imagen. `construir_indice` valida los modelos
    justo después de leer los metadatos y ANTES de montar ninguna fila, así
    que aborta en un único golpe con la lista completa de modelos que
    faltan por configurar."""


@dataclass
class _Posicion:
    """Posición de una imagen tal y como la escribe hoy meta/location: lat/lon
    en decimal y el resto como TEXTO crudo del XMP DJI (`get_gimbal_yaw_pitch`,
    `get_xmp_data`), para que el CSV desde manifiesto salga idéntico."""

    lat: float | None
    lon: float | None
    altitud_abs: str | None
    altura_relativa: str | None
    gimbal_yaw: str | None
    gimbal_pitch: str | None
    gimbal_roll: str | None
    flight_yaw: str | None
    ancho_px: int | None
    alto_px: int | None


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
    posicion: "_Posicion | None" = None
    meta_leida: bool = False
    make: str | None = None


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
    if _termina_en_sufijo(base, cfg.end_thermo_files):
        return "TERMICA"
    if _termina_en_sufijo(base, cfg.end_rgb_extra_files):
        return NOMBRE_CARPETA_RGB_EXTRA
    return "RGB"


def _termina_en_sufijo(base: str, sufijos: str) -> bool:
    """¿`base` acaba en alguno de `sufijos` (lista por comas, como en
    `utils.check_suffix_within_the_name`), admitiendo detrás el `_pointN` que
    añade la H30T en disparos por punto (`DJI_..._T_point0`)? #3890."""
    for suf in (sufijos or "").split(","):
        if suf and re.search(re.escape(suf) + r"(_point\d+)?$", base):
            return True
    return False


def _es_pb_generales(pb: str | None) -> bool:
    """¿Es `pb` alguna variante de "GENERALES" (fotos de contexto, sin vuelo
    real)? Normaliza `strip().upper()` antes de comparar contra
    `_PB_GENERALES_VARIANTES`, igual que el resto de comparaciones de PB
    (`pb = str(...).strip()` en `_ventanas_por_vuelo`)."""
    return bool(pb) and pb.strip().upper() in _PB_GENERALES_VARIANTES


def _nombre_carpeta_vuelo(pb: str, vuelo: str, include_v: bool, sufijo: str | None = None) -> str:
    """Mismo patrón que `GenStructFolder.gen_folder_struct` (pipeline.py:1317-1322).
    `sufijo` (el de `estadillo.sufijo_fecha`) solo llega no-`None` cuando este
    (pb, vuelo) colisiona entre estadillos fusionados con fecha distinta
    (`estadillo.detectar_colisiones_pb_vuelo`): es la MISMA regla que aplica
    `gen_folder_struct`, para que el nombre de carpeta salga idéntico en
    índice y en el motor viejo."""
    base = f"PB{pb}_V{vuelo}" if include_v else f"PB{pb}_{vuelo}"
    return f"{base}_{sufijo}" if sufijo else base


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


# Cuántos bytes se leen de golpe para la pasada rápida de metadatos: igual
# que `exif._XMP_HEADER_BYTES` (256 KB), que ya cubre de sobra la cabecera
# EXIF/XMP de un JPEG de dron. Reutilizamos la misma constante en vez de
# duplicar el número para que ambas lecturas de cabecera sigan alineadas si
# algún día cambia.
_BYTES_CABECERA = exif_mod._XMP_HEADER_BYTES


def _timestamp_desde_buffer(buf: bytes) -> datetime.datetime | None:
    """Réplica en memoria de `exif.GeneralInformationFromImage.get_timestamp_from_image`,
    sin logging (eso lo sigue haciendo la función original cuando `_leer_metadatos`
    cae al fallback por ruta). Cualquier excepción se deja subir tal cual: quien
    llama decide si reintenta con el fichero completo o cae al fallback."""
    img = PIL.Image.open(io.BytesIO(buf))
    datos_exif = img._getexif()
    img.close()
    if datos_exif is None:
        return None
    if 36867 not in datos_exif:
        return None
    fecha_hora = datos_exif[36867].split(' ')
    return datetime.datetime.strptime(fecha_hora[0] + '_' + fecha_hora[1], '%Y:%m:%d_%H:%M:%S')


def _modelo_desde_buffer(buf: bytes) -> str:
    """Réplica en memoria de `get_model`. Si `Image Model` no aparece ni en
    la pasada rápida (`stop_tag`) ni en la de detalle, deja subir el
    `KeyError` tal cual hace la original (la captura ahí, no aquí)."""
    f = io.BytesIO(buf)
    tags = exifread.process_file(f, details=False, stop_tag="Image Model")
    if "Image Model" not in tags:
        f.seek(0)
        tags = exifread.process_file(f, details=True)
    return str(tags["Image Model"])


def _make_desde_buffer(buf: bytes) -> str | None:
    """Fabricante EXIF (`Image Make`), misma lectura que `_modelo_desde_buffer`.
    `exif` no tiene función original para el fabricante: sin tag -> `None`."""
    f = io.BytesIO(buf)
    tags = exifread.process_file(f, details=False, stop_tag="Image Make")
    if "Image Make" not in tags:
        f.seek(0)
        tags = exifread.process_file(f, details=True)
    if "Image Make" not in tags:
        return None
    return str(tags["Image Make"]).strip("\x00").strip() or None


def _gps_desde_buffer(buf: bytes) -> tuple[float, float] | None:
    """Réplica en memoria de `MetaLocation.leerLatitudLongitudAltitud_exif_DJI`
    (exif.py:1164), sin logging ni contadores: mismas referencias N/S/E/W,
    misma aritmética (mismo orden de operaciones -> mismo float). `None` en
    los mismos casos en que la original devuelve `None`."""
    img = PIL.Image.open(io.BytesIO(buf))
    try:
        datos_exif = img.getexif()
    finally:
        img.close()
    if len(datos_exif) == 0:
        return None
    coordenadas = datos_exif.get_ifd(34853)
    lat_ref = coordenadas.get(1)
    lon_ref = coordenadas.get(3)
    if lat_ref is None or lon_ref is None:
        return None
    latitud = coordenadas.get(2, 0)
    longitud = coordenadas.get(4, 0)
    if lat_ref == 'N':
        lat = float(latitud[0]) + float(latitud[1]) / 60 + float(latitud[2]) / 3600
    elif lat_ref == 'S':
        lat = (float(latitud[0]) + float(latitud[1]) / 60 + float(latitud[2]) / 3600) * -1
    else:
        return None
    if lon_ref == 'E':
        lon = float(longitud[0]) + float(longitud[1]) / 60 + float(longitud[2]) / 3600
    elif lon_ref == 'W':
        lon = (float(longitud[0]) + float(longitud[1]) / 60 + float(longitud[2]) / 3600) * -1
    else:
        return None
    return lat, lon


def _dimensiones_desde_buffer(buf: bytes) -> tuple[int | None, int | None]:
    try:
        with PIL.Image.open(io.BytesIO(buf)) as img:
            return img.size
    except Exception:  # noqa: BLE001 — dato informativo del índice, no bloquea
        return None, None


def _bloque_xmp_desde_buffer(buf: bytes, ruta: str) -> str:
    """Réplica de `exif.leer_bloque_xmp` a partir de una cabecera ya leída
    en memoria: mismo criterio (latin-1 + universal newlines) y mismo
    resultado exacto.

    `open(..., encoding='latin-1')` en modo texto decodifica Y normaliza
    `\\r\\n`/`\\r` a `\\n` (universal newlines) según va leyendo del fichero,
    así que sus primeros N *caracteres* no siempre corresponden a los
    primeros N *bytes* de `buf` cuando el binario trae `\\r`/`\\n` sueltos
    antes del XMP: el texto normalizado de `buf` puede ser más corto que ese
    trozo de N caracteres. Es, en el peor caso, un PREFIJO idéntico de esa
    cadena (mismos bytes de origen, mismo punto de partida), así que si el
    cierre del XMP aparece dentro del texto normalizado de `buf`, aparece
    exactamente en el mismo índice que si se hubiera calculado desde el
    fichero — es seguro usarlo tal cual.

    Si NO aparece, no se puede distinguir "no hay XMP" de "se cortó el
    prefijo antes de encontrarlo": en ese caso (raro: solo si el bloque XMP
    queda pasados los primeros `_BYTES_CABECERA` bytes crudos) se llama a la
    función original, que relee el fichero con el mismo criterio exacto."""
    texto = buf.decode('latin-1').replace('\r\n', '\n').replace('\r', '\n')
    if texto.find('</x:xmpmeta') != -1:
        return texto
    return exif_mod.leer_bloque_xmp(ruta)


def _exif_entero_en_buffer(buf: bytes) -> bool:
    """¿Contiene `buf` el EXIF COMPLETO de la imagen? Si el recorte deja un
    valor EXIF a medias, PIL/exifread pueden devolver una cadena truncada SIN
    excepción (no dispararía el fallback). Solo se fía del buffer si es el
    fichero entero, o si es un JPEG cuyo segmento APP1 `Exif` termina dentro
    de él (los offsets EXIF son relativos a ese segmento, así que todos sus
    valores quedan dentro). Cualquier otro caso (TIFF, marcadores raros) ->
    False, y timestamp/modelo usan las funciones originales por ruta."""
    if len(buf) < _BYTES_CABECERA:
        return True
    if buf[:2] != b'\xff\xd8':
        return False
    pos = 2
    while pos + 4 <= len(buf):
        if buf[pos] != 0xFF:
            return False
        marcador = buf[pos + 1]
        if marcador == 0xDA:  # SOS: empiezan los datos de imagen sin haber visto APP1 Exif
            return False
        longitud = int.from_bytes(buf[pos + 2:pos + 4], 'big')
        fin = pos + 2 + longitud
        if marcador == 0xE1 and buf[pos + 4:pos + 10] == b'Exif\x00\x00':
            return fin <= len(buf)
        pos = fin
    return False


def _con_reintento_fichero_completo(funcion_pura, buf: bytes, ruta: str):
    """Ejecuta `funcion_pura(buf)`; si lanza, relee el fichero ENTERO a
    memoria una única vez y reintenta con ese buffer completo (así un XMP o
    un EXIF que caiga más allá de `_BYTES_CABECERA` no se pierde por el
    tamaño del recorte). Si sigue fallando, la excepción sube tal cual."""
    try:
        return funcion_pura(buf)
    except Exception:
        with open(ruta, 'rb') as fd:
            buf_completo = fd.read()
        return funcion_pura(buf_completo)


def _leer_metadatos(ruta: str, exif, progress_callback) -> _MetadatosImagen:
    """Una imagen -> sus metadatos, o `None` en cada campo que no se pudo
    leer. Cada lectura va en su propio `try/except`: que falle el yaw no
    tiene por qué tirar también el timestamp de la misma imagen.

    Lee la imagen UNA sola vez (los primeros `_BYTES_CABECERA` bytes, que
    cubren de sobra la cabecera EXIF/XMP) y de ahí saca timestamp/modelo/yaw
    con réplicas puras en memoria de las funciones originales de `exif`, en
    vez de que cada campo abra el fichero por su cuenta -antes eran hasta 4
    aperturas por imagen, caro en lotes servidos desde HDD. Por campo: si la
    réplica rápida lanza excepción o da `None`, se cae a la función ORIGINAL
    de `exif` por ruta, así que el resultado -y el logging/contadores de
    error de la original en el caso de fallo real- es idéntico a antes.

    El GPS se lee ahora con una réplica pura en memoria (`_gps_desde_buffer`),
    con el mismo reintento con fichero completo que el resto de campos.

    Las rutas `gs://…` tampoco entran en el atajo (la lectura cruda de bytes
    solo vale para ficheros locales): siguen el camino de siempre, llamando
    directamente a las funciones originales."""
    nombre = os.path.basename(ruta)

    buf = None
    if not almacen.es_uri_gcs(ruta):
        try:
            with open(ruta, 'rb') as fd:
                buf = fd.read(_BYTES_CABECERA)
        except Exception:  # noqa: BLE001 — sin buffer, cada campo cae a su original
            buf = None
    buf_exif = buf if buf is not None and _exif_entero_en_buffer(buf) else None

    timestamp = None
    if buf_exif is not None:
        try:
            timestamp = _con_reintento_fichero_completo(_timestamp_desde_buffer, buf_exif, ruta)
        except Exception:  # noqa: BLE001
            timestamp = None
    if timestamp is None:
        try:
            timestamp = exif.get_timestamp_from_image(ruta)
        except Exception:  # noqa: BLE001 — una imagen ilegible no tumba el índice
            timestamp = None

    modelo = None
    if buf_exif is not None:
        try:
            modelo = _con_reintento_fichero_completo(_modelo_desde_buffer, buf_exif, ruta)
        except Exception:  # noqa: BLE001
            modelo = None
    if modelo is None:
        try:
            modelo = exif.get_model(ruta, progress_callback)
        except Exception:  # noqa: BLE001
            modelo = None

    make = None
    if buf_exif is not None:
        try:
            make = _con_reintento_fichero_completo(_make_desde_buffer, buf_exif, ruta)
        except Exception:  # noqa: BLE001 — dato informativo, no bloquea
            make = None

    yaw = None
    gimbal = None
    xmp = None
    try:
        if buf is not None:
            bloque_xmp = _bloque_xmp_desde_buffer(buf, ruta)
            gimbal = exif.get_gimbal_yaw_pitch(ruta, bloque_xmp=bloque_xmp)
            try:
                xmp = exif.get_xmp_data(ruta, bloque_xmp=bloque_xmp)
            except Exception:  # noqa: BLE001
                xmp = None
        else:
            gimbal = exif.get_gimbal_yaw_pitch(ruta)
        yaw = float(gimbal[0])
    except Exception:  # noqa: BLE001
        yaw = None

    gps = None
    if buf is not None:
        try:
            if buf_exif is not None:
                gps = _con_reintento_fichero_completo(_gps_desde_buffer, buf_exif, ruta)
            else:
                with open(ruta, 'rb') as fd:
                    gps = _gps_desde_buffer(fd.read())
        except Exception:  # noqa: BLE001 — sin GPS la fila queda fuera de meta/location, como hoy
            gps = None

    # Solo local: con buffer, gimbal y XMP leídos. `gs://…` o lectura rota ->
    # `meta_leida=False` y el cierre relee el fichero de salida como siempre.
    posicion = None
    meta_leida = buf is not None and gimbal is not None and xmp is not None
    if meta_leida:
        ancho, alto = _dimensiones_desde_buffer(buf)
        posicion = _Posicion(
            lat=gps[0] if gps else None, lon=gps[1] if gps else None,
            altitud_abs=xmp[0], altura_relativa=xmp[1],
            gimbal_yaw=gimbal[0], gimbal_pitch=gimbal[1],
            gimbal_roll=xmp[2], flight_yaw=xmp[4],
            ancho_px=ancho, alto_px=alto)

    return _MetadatosImagen(ruta=ruta, nombre=nombre, timestamp=timestamp,
                            modelo=modelo, yaw=yaw, gps=gps,
                            posicion=posicion, meta_leida=meta_leida, make=make)


def _ventanas_por_vuelo(estadillo_df, nombres_columnas: dict, pipeline, cfg,
                        progress_callback, colisiones_pb_vuelo: dict | None = None) -> list[dict]:
    """Franja horaria y (pb, vuelo) de cada fila del estadillo fusionado, EN
    EL ORDEN DEL ESTADILLO (ese orden es el desempate cuando dos vuelos
    solapan: gana el primero, igual que `obtenerListaImagenesVuelo`). Una
    fila sin hora de inicio o fin legible se descarta con un aviso, igual que
    hace `gen_folder_struct` (pipeline.py:1301-1311), en vez de reventar el
    índice entero por una celda vacía.

    `colisiones_pb_vuelo` (salida de `estadillo.detectar_colisiones_pb_vuelo`
    sobre el estadillo fusionado GLOBAL, no sobre este `estadillo_df` -que
    puede ser solo el de una carpeta escopada-) marca qué (pb, vuelo) llevan
    sufijo de fecha (`ventana["sufijo"]`) porque colisionan con OTRO
    estadillo fusionado con fecha distinta; el resto de filas quedan con
    `sufijo=None`, igual que siempre."""
    col_pb = nombres_columnas["PB"]
    col_vuelo = nombres_columnas["Vuelo"]
    col_fecha = nombres_columnas["Fecha"]
    col_inicio = nombres_columnas["Hora_de_inicio"]
    col_final = nombres_columnas["Hora_final"]
    col_equipo = nombres_columnas.get("Equipo_de_vuelo")
    colisiones_pb_vuelo = colisiones_pb_vuelo or {}

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

        equipo = None
        if col_equipo is not None and col_equipo in estadillo_df.columns:
            texto = str(estadillo_df[col_equipo].iloc[indice]).strip()
            equipo = texto if texto and texto.lower() != "nan" else None

        sufijo = (estadillo_mod.sufijo_fecha(fecha)
                  if (pb, vuelo) in colisiones_pb_vuelo else None)

        ventanas.append({"pb": pb, "vuelo": vuelo, "inicio": inicio, "fin": fin,
                         "equipo": equipo, "fecha": fecha, "sufijo": sufijo})
    return ventanas


def _asignar_vuelo(dato: _MetadatosImagen, ventanas: list[dict]) -> dict | None:
    """Primera ventana (en orden del estadillo) cuyo `(inicio, fin)` contiene
    ESTRICTAMENTE el timestamp de la imagen, igual que
    `obtenerListaImagenesVuelo` (pipeline.py:1528: `inicio < ts < fin`). Sin
    timestamp, o sin ninguna ventana que la reclame, la imagen es
    `unassigned`.

    `ventanas` ya viene ESCOPADA a la carpeta que le corresponde a `dato`
    (ver `_ventanas_para_imagen`): solo se compara contra las ventanas del
    estadillo cuyo directorio es el ancestro más cercano de la imagen, no
    contra TODOS los estadillos fusionados -eso es lo que evita que una
    imagen de un piloto se cuele en el vuelo de otro piloto con horas
    solapadas el mismo día (ver `agrupar_rutas_por_carpeta`)."""
    if dato.timestamp is None:
        return None
    for ventana in ventanas:
        if ventana["inicio"] < dato.timestamp < ventana["fin"]:
            return ventana
    return None


def _bajo_carpeta(directorio: str, ruta: str) -> bool:
    """¿Vive `ruta` dentro del árbol de `directorio` (a cualquier
    profundidad)? Comparación por prefijo de ruta normalizada
    (`os.path.normcase` + `os.sep` de separador), no por `Path.is_relative_to`
    -no existe en todas las versiones de Python que corre esta app-, así que
    funciona igual en Windows (rutas case-insensitive) y en Linux/mac."""
    d = os.path.normcase(os.path.normpath(directorio))
    r = os.path.normcase(os.path.normpath(ruta))
    return r == d or r.startswith(d + os.sep)


def _ventanas_para_imagen(ruta_imagen: str, ventanas_por_carpeta: dict[str, list[dict]],
                          ventanas_sin_carpeta: list[dict]) -> list[dict]:
    """El grupo de ventanas contra el que se compara ESTA imagen: el del
    directorio de estadillo ANCESTRO MÁS CERCANO de `ruta_imagen` (el más
    profundo de los que la contienen), o `ventanas_sin_carpeta` -el pool de
    estadillos cuyo directorio no es ancestro de NINGUNA imagen del lote,
    p.ej. un `--estadillo` elegido a mano desde una ubicación ajena al árbol
    que se está organizando- si ninguno de los directorios de estadillo es
    ancestro de esta imagen.

    Esto es justo el scoping por carpeta: dos pilotos el mismo día, cada uno
    en su carpeta con su propio estadillo, no se contaminan aunque sus horas
    de vuelo se solapen -cada imagen solo compite contra las ventanas de SU
    carpeta-."""
    mejor_directorio = None
    for directorio in ventanas_por_carpeta:
        if _bajo_carpeta(directorio, ruta_imagen):
            if mejor_directorio is None or len(directorio) > len(mejor_directorio):
                mejor_directorio = directorio
    if mejor_directorio is not None:
        return ventanas_por_carpeta[mejor_directorio]
    return ventanas_sin_carpeta


def _orientacion_normalizada(valor) -> str:
    """`cfg.orientacion` normalizada para comparar: strip + lower. `""` si no
    hay valor (planta desconocida o inspección sin seleccionar)."""
    return str(valor or "").strip().lower()


def _consenso_de_angulo_por_vuelo(asignaciones: list[tuple[_MetadatosImagen, dict | None]],
                                  pipeline, cfg, output_folder: str,
                                  progress_callback) -> dict[tuple[str, str], int]:
    """Un único ángulo por `(pb, vuelo)`, calculado UNA vez y compartido por
    todas las filas de ese vuelo (RGB y térmica): este reparto por vuelo, no
    por carpeta, es lo que corrige el bug que motiva el proyecto entero (hoy
    el TIFF y su JPG pueden acabar con criterios de giro distintos).

    Regla de Rodrigo (2026-09-28, caso KL88 -`plantas_pv.orientacion` =
    'Horizontal', 1986/1986 imágenes giradas por yaw cuando no debían-):
    una planta `Horizontal` **nunca se gira**, ángulo 0 para todas sus
    imágenes (térmica, RGB, JPG térmico), decisión que manda sobre
    cualquier consenso por yaw o CSV de un re-proceso anterior. El resto
    de orientaciones (`Vertical`, `Varias`, desconocida) sigue el
    comportamiento de siempre: consenso por yaw. Si la orientación no se
    conoce (inspección sin elegir, o API de la Suite sin el campo
    -`lib/organizer-catalogo.js` de Atom-suite todavía no lo manda-) se seguirá
    girando por yaw como hasta ahora, pero se avisa (ver abajo): es un giro
    decidido a ciegas, sin saber si la planta es Horizontal.

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
    desactivado, 0.

    La clave es `(pb, vuelo, sufijo)`, no solo `(pb, vuelo)`: cuando dos
    estadillos fusionados colisionan con fecha distinta (`ventana["sufijo"]`,
    ver `_ventanas_por_vuelo`) son carpetas de salida DISTINTAS
    (`_nombre_carpeta_vuelo`), y cada una necesita su propio consenso -
    fundirlas en una sola clave mezclaría los yaw de dos vuelos que ni
    siquiera comparten fecha."""
    yaws_por_vuelo: dict[tuple[str, str, str | None], list[float]] = {}
    for dato, ventana in asignaciones:
        if ventana is None or dato.yaw is None or _es_pb_generales(ventana["pb"]):
            continue
        clave = (ventana["pb"], ventana["vuelo"], ventana.get("sufijo"))
        yaws_por_vuelo.setdefault(clave, []).append(dato.yaw)

    orientacion = _orientacion_normalizada(getattr(cfg, "orientacion", ""))
    if orientacion == "horizontal":
        # Punto único de la regla: se decide ANTES de mirar ningún yaw ni CSV
        # de un re-proceso anterior -es una propiedad de la planta, no del
        # vuelo ni de una corrida concreta-.
        return {clave: 0 for clave in yaws_por_vuelo}

    angulos: dict[tuple[str, str, str | None], int] = {}

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

    for (pb, vuelo, sufijo), yaws in yaws_por_vuelo.items():
        nombre_carpeta = _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v, sufijo)
        carpeta_vuelo = almacen.unir(output_folder, "TERMICA", f"PB{pb}", nombre_carpeta)
        candidato_csv = almacen.unir(
            output_folder, "CSVs", utils.CRITERIO_DIRNAME, f"{nombre_carpeta}_Videofiles.csv")
        clave = (pb, vuelo, sufijo)
        if almacen.existe_ruta(candidato_csv):
            angulos[clave] = pipeline.read_auto_rotate_degree(
                carpeta_vuelo, progress_callback)
            continue

        rotate_90 = sum(1 for yaw in yaws if lim_min_90 < yaw < lim_max_90)
        rotate_270 = sum(1 for yaw in yaws if lim_min_270 < yaw < lim_max_270)
        total = len(yaws)
        if total and rotate_270 and (rotate_270 / total) > (max_error / 100):
            angulos[clave] = 270
        elif total and rotate_90 and (rotate_90 / total) > (max_error / 100):
            angulos[clave] = 90
        else:
            angulos[clave] = 0

    if not orientacion and any(a in (90, 270) for a in angulos.values()):
        # Orientación desconocida (sin inspección elegida, o la API de la
        # Suite todavía sin el campo) y al menos un vuelo se va a girar: se
        # sigue girando por yaw -comportamiento de siempre-, pero a ciegas,
        # sin saber si esta planta es Horizontal (que no debería girar
        # nunca). Aviso visible, no silencioso.
        progress_callback.emit(
            "\nAVISO: orientación de la planta desconocida; se gira por "
            "consenso de yaw sin poder aplicar la regla 'Horizontal nunca "
            "gira'. Verifica la orientación en la Suite si esta planta es "
            "horizontal.\n")

    return angulos


def _pct_por_modelo(modelo: str, pipeline) -> float:
    """Getter de dominio sobre `Pipeline.get_percentage_by_model`: mismo
    lookup, pero un modelo sin configurar lanza `ErrorModeloSinRecorte` con
    mensaje claro en vez del `KeyError` crudo de `normalized_dict[...]`
    (pipeline.py:4328). Red de seguridad: `construir_indice` ya valida todos
    los modelos ANTES de llegar aquí (`_validar_modelos_recorte`); este
    getter cubre además cualquier otro llamador directo."""
    try:
        return pipeline.get_percentage_by_model(modelo, pipeline.percentage_by_models)
    except KeyError:
        raise ErrorModeloSinRecorte(
            f"El modelo de dron '{modelo}' no tiene % de recorte configurado "
            "en Config.ini (sección [percentage_by_models]). Añádelo en "
            "Configuración > Recorte automático antes de reintentar."
        ) from None


def _modelos_sin_recorte_configurado(
    metadatos: list[_MetadatosImagen], cfg, pipeline
) -> dict[str, list[str]]:
    """Modelos EXIF de imágenes RGB/RGB_Extra (las únicas que se recortan,
    ver `TIPOS_RGB`) que no tienen entrada en `pipeline.percentage_by_models`.
    Solo aplica si el recorte automático está activo: en manual no se mira
    el modelo (`cfg.crop_percentage` manda). Devuelve
    `{modelo: [nombres de ejemplo]}`, vacío si no falta nada."""
    if not cfg.cropping_rgb or not cfg.cropping_mode_auto:
        return {}
    conocidos = {str(k).strip().upper() for k in (pipeline.percentage_by_models or {}).keys()}
    faltantes: dict[str, list[str]] = {}
    for dato in metadatos:
        if _clasificar_tipo(dato.nombre, cfg) not in TIPOS_RGB:
            continue
        if not dato.modelo:
            continue
        modelo = str(dato.modelo).strip("\x00").strip().upper()
        if modelo and modelo not in conocidos:
            faltantes.setdefault(modelo, []).append(dato.nombre)
    return faltantes


def _validar_modelos_recorte(metadatos: list[_MetadatosImagen], cfg, pipeline) -> None:
    """Aborta pronto, ANTES de montar ninguna fila del índice, si el recorte
    automático de RGB está activo y alguna imagen trae un modelo EXIF sin %
    configurado. Se llama justo tras leer los metadatos (ya son datos
    gratis: `dato.modelo` viene del mismo EXIF que se lee para todo lo
    demás), no repite lectura de disco. Ver `ErrorModeloSinRecorte`."""
    faltantes = _modelos_sin_recorte_configurado(metadatos, cfg, pipeline)
    if not faltantes:
        return
    detalle = "; ".join(
        f"'{modelo}' (p.ej. {ejemplos[0]}"
        + (f" y {len(ejemplos) - 1} más" if len(ejemplos) > 1 else "")
        + ")"
        for modelo, ejemplos in sorted(faltantes.items())
    )
    raise ErrorModeloSinRecorte(
        "El recorte automático de RGB está activado pero hay modelo(s) de "
        f"dron sin % de recorte configurado en Config.ini: {detalle}. "
        "Añádelos en Configuración > Recorte automático (sección "
        "[percentage_by_models]) antes de reintentar."
    )


def _pct_recorte(dato: _MetadatosImagen, tipo: str, cfg, pipeline) -> float | None:
    """El % de recorte aplica a RGB y RGB_Extra (el recorte centrado es cosa
    de `RGBCropping`, nunca de la térmica; ver `TIPOS_RGB`:
    `iterate_folders_for_rgb_cropping`, pipeline.py:4003, recorre TODO el
    árbol de salida salvo `TERMICA`, así que RGB_Extra se recorta igual que
    RGB). Sale de `Config.ini` vía `get_percentage_by_model` cuando el modo
    es automático; en manual, el valor de la interfaz (`cfg.crop_percentage`)
    manda sin mirar el modelo.

    Devuelve una FRACCIÓN 0-1, no un porcentaje: `ImageProcessConfig.
    crop_centered_pct` (pipeline.py:199) multiplica directamente por el ancho
    y el alto. Devolver aquí el porcentaje crudo (70) hacía que el recorte
    pidiese 8000*70 x 6000*70 px y Pillow abortase con
    `DecompressionBombError`, dejando todas las RGB en SIN_ORDENAR."""
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
        pct = _pct_por_modelo(dato.modelo, pipeline)
        if pct is None:
            return None
        return float(pct) / 100
    return float(cfg.crop_percentage) / 100


def campos_posicion(dato: _MetadatosImagen) -> dict:
    """kwargs de posición para `FilaManifiesto`. Sin lectura, solo
    `meta_leida=False` (el resto queda en su default `None`)."""
    if not dato.meta_leida or dato.posicion is None:
        return {"meta_leida": False}
    p = dato.posicion
    return {"meta_leida": True, "lat": p.lat, "lon": p.lon,
            "altitud_abs": p.altitud_abs, "altura_relativa": p.altura_relativa,
            "gimbal_yaw": p.gimbal_yaw, "gimbal_pitch": p.gimbal_pitch,
            "gimbal_roll": p.gimbal_roll, "flight_yaw": p.flight_yaw,
            "ancho_px": p.ancho_px, "alto_px": p.alto_px}


def _avisar_equipo(asignaciones, progress_callback) -> int:
    """Una línea por vuelo cuyo `Equipo_de_vuelo` no cuadra con el modelo EXIF
    de alguna de sus imágenes. Solo avisa. Devuelve cuántos vuelos discrepan."""
    discrepancias: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for dato, ventana in asignaciones:
        if ventana is None or not ventana.get("equipo"):
            continue
        if equipo_mod.equipo_coincide(ventana["equipo"], dato.modelo) is False:
            _texto, modelos = discrepancias.setdefault(
                (ventana["pb"], ventana["vuelo"]), (ventana["equipo"], set()))
            modelos.add(str(dato.modelo).strip("\x00").strip())
    for (pb, vuelo), (texto, modelos) in discrepancias.items():
        progress_callback.emit(
            f"\nAVISO: PB{pb} vuelo {vuelo}: el estadillo dice '{texto}' pero el EXIF "
            f"es {', '.join(sorted(modelos))}. Revisa el estadillo.\n")
    return len(discrepancias)


def _construir_fila(dato: _MetadatosImagen, ventana: dict | None,
                    angulos: dict[tuple[str, str, str | None], int], cfg, pipeline) -> FilaManifiesto:
    tipo = _clasificar_tipo(dato.nombre, cfg)
    unassigned = ventana is None

    # `timestamp=dato.timestamp`: el EXIF de esta imagen YA se leyó en
    # `_leer_metadatos`, dentro del pool de hilos. Sin pasarlo, `nombre_destino`
    # reabría el fichero con PIL para releer exactamente el mismo dato — y esta
    # comprensión de lista va en serie, en el hilo principal: era una segunda
    # pasada EXIF completa sobre todo el dataset, sin paralelizar.
    nombre_nuevo = pipeline.nombre_destino(
        dato.nombre, cfg.input_folder, cfg.rename_images,
        cfg.mismatch_hours, cfg.mismatch_minutes, ruta_local=dato.ruta,
        timestamp=dato.timestamp)
    nombre_final = nombre_nuevo if nombre_nuevo else dato.nombre

    if unassigned:
        pb = vuelo = None
        angulo_giro = 0
        carpeta_destino = almacen.unir(cfg.output_folder, NOMBRE_CARPETA_SIN_ORDENAR, tipo)
    else:
        pb, vuelo = ventana["pb"], ventana["vuelo"]
        if _es_pb_generales(pb):
            # Fotos de contexto, no de un vuelo: carpeta HERMANA de
            # TERMICA/RGB/RGB_Extra, plana (sin PB/vuelo) y sin girar -no hay
            # consenso de ángulo posible sin línea de vuelo real. El recorte
            # (`_pct_recorte`, abajo) NO se toca: el bucket confirma que las
            # RGB generales sí llevan su `_CROP` hermano.
            angulo_giro = 0
            carpeta_destino = almacen.unir(cfg.output_folder, NOMBRE_CARPETA_GENERALES)
        else:
            sufijo = ventana.get("sufijo")
            angulo_giro = angulos.get((pb, vuelo, sufijo), 0)
            carpeta_destino = almacen.unir(
                cfg.output_folder, tipo, f"PB{pb}",
                _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v, sufijo))

    ruta_salida_original = almacen.unir(carpeta_destino, nombre_final)

    pct_recorte = _pct_recorte(dato, tipo, cfg, pipeline)
    ruta_salida_crop = None
    if pct_recorte is not None:
        raiz, ext = os.path.splitext(nombre_final)
        ruta_salida_crop = almacen.unir(carpeta_destino, f"{raiz}_CROP{ext}")

    ruta_salida_tiff = None
    if tipo == "TERMICA" and cfg.convert_to_tif:
        raiz, _ext = os.path.splitext(nombre_final)
        ruta_salida_tiff = almacen.unir(carpeta_destino, f"{raiz}.tiff")

    # RGB_Extra comprime con el MISMO flag que RGB (`Pipeline.iterate_folders`,
    # pipeline.py:2829: `compress_checked` es el único condicional, sin
    # distinguir el tercer grupo de sufijos del segundo). Ver `TIPOS_RGB`.
    comprime = bool(cfg.compress_rgb) if tipo in TIPOS_RGB else False

    # El tamaño de entrada se apunta AQUÍ, con el original todavía en su
    # sitio: cuando el run termina, ese fichero puede haberse movido y ya no
    # habría con qué comparar la entrega. Un origen ilegible cuenta 0 en vez
    # de tumbar el indexado entero por una estadística.
    try:
        bytes_origen = almacen.tamano_de(dato.ruta)
    except Exception:
        bytes_origen = 0

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
        bytes_origen=bytes_origen,
        **campos_posicion(dato),
        make=dato.make,
        equipo_estadillo=None if unassigned else ventana.get("equipo"),
    )


def _con_sufijo(ruta: str, contador: int) -> str:
    """`ruta` con `_<contador>` metido justo antes de la extensión."""
    raiz, ext = os.path.splitext(ruta)
    return f"{raiz}_{contador}{ext}"


def _desambiguar_colisiones_generales(filas: list[FilaManifiesto]) -> list[FilaManifiesto]:
    """FOTOS_GENERALES es plana (ver `_construir_fila`): imágenes de vuelos o
    días distintos que antes vivían en carpetas `PB.../V...` separadas ahora
    comparten directorio, y pueden traer el MISMO nombre de fichero (p.ej. dos
    tarjetas SD reiniciando la numeración DJI). Sin este paso, la segunda
    pisaría a la primera al escribir -nunca sobrescribir-.

    Sufijo determinista `_2`, `_3`... por orden de aparición (el mismo orden
    ya `sorted` de `_listar_imagenes`), aplicado a la vez al original, al
    `_CROP` y al `.tiff` para que los tres seguían siendo el mismo fichero
    con apellido. Filas que no son GENERALES pasan intactas."""
    vistos: dict[str, int] = {}
    resultado: list[FilaManifiesto] = []
    for fila in filas:
        if not _es_pb_generales(fila.pb):
            resultado.append(fila)
            continue
        nombre = os.path.basename(fila.ruta_salida_original)
        vistos[nombre] = vistos.get(nombre, 0) + 1
        contador = vistos[nombre]
        if contador == 1:
            resultado.append(fila)
            continue
        resultado.append(replace(
            fila,
            ruta_salida_original=_con_sufijo(fila.ruta_salida_original, contador),
            ruta_salida_crop=(_con_sufijo(fila.ruta_salida_crop, contador)
                              if fila.ruta_salida_crop else None),
            ruta_salida_tiff=(_con_sufijo(fila.ruta_salida_tiff, contador)
                              if fila.ruta_salida_tiff else None),
        ))
    return resultado


def construir_indice(
    cfg,
    pipeline,
    exif,
    manifiesto: "Manifiesto",
    progress_callback,
    progress_bar,
    progress_summarize,
    max_hilos: int | None = None,
    ejecucion_id: int | None = None,
) -> dict:
    """Construye el manifiesto completo del run: una pasada de metadatos
    sobre `cfg.input_folder`, cruzada con el estadillo, decidiendo vuelo,
    nombre, ángulo, % de recorte y rutas de salida de cada imagen. No
    escribe ni mueve NINGUNA imagen: solo inserta (o reabre) filas en
    `manifiesto`.

    Devuelve `{"total", "unassigned", "sin_timestamp", "vuelos", "nuevas",
    "saltadas", "reintentadas", "vuelos_equipo_discrepa"}`.
    """
    progress_summarize.emit("---> SUBPROCESO: Índice")

    rutas_estadillo = estadillo_mod.desempaquetar_rutas(cfg.estad)
    # El DataFrame GLOBAL (todos los estadillos fusionados, en el orden de
    # siempre) es el que decide colisiones y sufijo de fecha: eso replica
    # exactamente `pipeline.GenStructFolder.gen_folder_struct` (naming
    # coherente índice <-> motor viejo), y NO depende del scoping por
    # carpeta de abajo -da igual en qué carpeta viva cada estadillo, el
    # sufijo de fecha se decide mirándolos TODOS juntos-.
    estadillo_df = estadillo_mod.combinar_estadillos(rutas_estadillo)

    utils_helper = _sin_utils_helper()
    nombres_columnas = utils_helper.get_nombres_columnas(list(estadillo_df.columns.values))

    # (PB, Vuelo) con fecha distinta entre estadillos: YA NO aborta, se
    # resuelve con sufijo de fecha en el nombre de carpeta (ver
    # `_nombre_carpeta_vuelo` / `_ventanas_por_vuelo`), igual que
    # `gen_folder_struct`.
    colisiones_pb_vuelo = estadillo_mod.detectar_colisiones_pb_vuelo(estadillo_df, nombres_columnas)

    # (PB, Vuelo, Fecha) con 2+ estadillos de ORIGEN distinto: esto SÍ aborta
    # -dos pilotos con el mismo PB+Vuelo el mismo día no tienen forma de
    # desambiguarse con un sufijo de fecha, que sería idéntico para ambos-.
    colisiones_mismo_dia = estadillo_mod.detectar_colisiones_mismo_dia(estadillo_df, nombres_columnas)
    if colisiones_mismo_dia:
        detalle = "; ".join(
            f"PB{pb} vuelo {vuelo} el {fecha}: {', '.join(origenes)}"
            for (pb, vuelo, fecha), origenes in colisiones_mismo_dia.items())
        raise ErrorColisionEstadillo(
            "El mismo (PB, Vuelo) aparece el MISMO día en estadillos de origen "
            f"distinto, y eso no se puede desambiguar con un sufijo de fecha: {detalle}. "
            "Revisa los estadillos de origen antes de reintentar.")

    imagenes = _listar_imagenes(cfg.input_folder)

    # Scoping por carpeta: cada imagen solo compite contra las ventanas del
    # estadillo cuyo directorio es su ancestro más cercano (ver
    # `_ventanas_para_imagen`/`_bajo_carpeta`). Un estadillo cuyo directorio
    # no es ancestro de NINGUNA imagen del lote (p.ej. uno elegido a mano
    # desde otra ubicación) cae en el pool `ventanas_sin_carpeta`, comparado
    # por timestamp contra TODAS sus filas -el comportamiento de siempre,
    # sin scoping-, salvo que sea un estadillo AUTODETECTADO dentro del árbol
    # de `cfg.input_folder` (ver más abajo): ese caso ya no entra en el pool.
    grupos_carpeta = estadillo_mod.agrupar_rutas_por_carpeta(rutas_estadillo)

    # Estadillos AUTODETECTABLES dentro del propio `cfg.input_folder` (mismo
    # escaneo que ofrece la UI antes de elegir, `estadillo.detectar_estadillos`):
    # si el directorio de uno de estos no es ancestro de NINGUNA imagen del
    # lote, no es "un estadillo elegido a mano desde otra ubicación" (el caso
    # que sí debe caer en `ventanas_sin_carpeta`), es un estadillo que vive
    # DENTRO del árbol que se está organizando pero sin fotos debajo -un
    # despiste casi seguro (carpeta equivocada, CSV suelto)-, así que sus
    # ventanas NO entran en el pool compartido y no pueden reclamar imágenes
    # de otro sitio. Solo se avisa (no aborta): a diferencia de
    # `ErrorEstadillosMismaCarpeta`, aquí no hay ambigüedad que resolver, solo
    # un estadillo que se queda sin usar.
    # Comparadas por `estadillo_mod.clave_ruta` (no como strings crudos):
    # `cfg.estad` puede traer una ruta relativa, con otra capitalización o
    # con `\` en vez de `/` (CLI `--estadillo`), y aun así referirse al MISMO
    # fichero que devuelve `detectar_estadillos` en `abspath`. Sin normalizar,
    # un estadillo que SÍ está dentro de `cfg.input_folder` se trataba como
    # externo (bug: se saltaba `ErrorEstadillosMismaCarpeta` y el WARNING de
    # "sin fotos", acabando reclamando imágenes huérfanas).
    rutas_autodetectables = set(
        estadillo_mod.clave_ruta(r)
        for r in estadillo_mod.detectar_estadillos(cfg.input_folder)["rutas"])

    # Dos o más ficheros de estadillo en la MISMA carpeta: hasta ahora se
    # fusionaban en silencio (`combinar_estadillos`, gana el primero en los
    # solapes). Eso es justo el caso contrario a lo que persigue el scoping
    # por carpeta -la carpeta es la unidad de "un estadillo"-, así que ahora
    # bloquea ANTES de leer ninguna imagen: casi siempre es un despiste (dos
    # pilotos sin separar sus CSV en subcarpetas propias), nunca una decisión
    # deliberada (decisión de Rodrigo). Solo aplica a estadillos AUTODETECTADOS
    # dentro de `cfg.input_folder`: uno elegido a mano desde fuera (p.ej.
    # `--estadillo` apuntando a una carpeta compartida por varios pilotos) no
    # es un despiste de organización del lote, así que conserva el
    # comportamiento de siempre (fusión con sufijo de fecha si colisiona).
    rutas_estadillo_autodetectadas = [
        r for r in rutas_estadillo if estadillo_mod.clave_ruta(r) in rutas_autodetectables]
    aviso_misma_carpeta = estadillo_mod.aviso_estadillos_misma_carpeta(
        rutas_estadillo_autodetectadas)
    if aviso_misma_carpeta:
        raise ErrorEstadillosMismaCarpeta(aviso_misma_carpeta)

    ventanas_por_carpeta: dict[str, list[dict]] = {}
    ventanas_sin_carpeta: list[dict] = []
    for directorio, rutas_grupo in grupos_carpeta.items():
        sub_df = estadillo_mod.combinar_estadillos(rutas_grupo)
        nombres_columnas_grupo = utils_helper.get_nombres_columnas(list(sub_df.columns.values))
        ventanas_grupo = _ventanas_por_vuelo(sub_df, nombres_columnas_grupo, pipeline, cfg,
                                             progress_callback, colisiones_pb_vuelo)
        ventanas_por_carpeta[directorio] = ventanas_grupo
        if any(_bajo_carpeta(directorio, imagen) for imagen in imagenes):
            continue
        es_autodetectado = all(
            estadillo_mod.clave_ruta(r) in rutas_autodetectables for r in rutas_grupo)
        if es_autodetectado:
            progress_callback.emit(
                f"\nWARNING: El estadillo {', '.join(rutas_grupo)} no tiene "
                "imágenes en su carpeta; no se ha usado.\n")
            continue
        ventanas_sin_carpeta.extend(ventanas_grupo)

    ventanas = [v for lista in ventanas_por_carpeta.values() for v in lista]

    # Son HILOS leyendo EXIF/XMP: trabajo I/O-bound, así que el dimensionado es
    # `max_io_workers` (hasta 32 hilos), no `workers_para_lote`, que calcula
    # PROCESOS que decodifican imágenes enteras y topa por RAM (600 MB/worker).
    # El resto del código ya usa este helper para este mismo patrón
    # (`pipeline.py:1642`, `exif.py:985`); el índice era el único que no.
    hilos = max_hilos or utils.max_io_workers()
    if imagenes:
        with ThreadPoolExecutor(max_workers=hilos) as ejecutor:
            metadatos = list(ejecutor.map(
                lambda ruta: _leer_metadatos(ruta, exif, progress_callback), imagenes))
    else:
        metadatos = []

    # Falla pronto y en un único golpe si el recorte automático está activo
    # y algún modelo EXIF no tiene % configurado: ANTES de asignar vuelos o
    # montar ninguna fila, no a mitad del `list comprehension` de abajo
    # (donde antes reventaba con `KeyError` crudo, imagen a imagen). Ver
    # `ErrorModeloSinRecorte`.
    _validar_modelos_recorte(metadatos, cfg, pipeline)

    asignaciones = [
        (dato, _asignar_vuelo(dato, _ventanas_para_imagen(
            dato.ruta, ventanas_por_carpeta, ventanas_sin_carpeta)))
        for dato in metadatos
    ]
    vuelos_equipo_discrepa = _avisar_equipo(asignaciones, progress_callback)
    angulos = _consenso_de_angulo_por_vuelo(asignaciones, pipeline, cfg,
                                            cfg.output_folder, progress_callback)
    # Cachito posterior del mismo destino: el ángulo ya decidido para un vuelo
    # manda sobre el recalculado, para que JPG y TIFF del vuelo no discrepen.
    # El manifiesto solo guarda (pb, vuelo) -no sufijo, columnas que no tiene
    # ni falta le hace-, así que el cache solo pisa la clave sin colisión
    # (`sufijo=None`); un (pb, vuelo) colisionado con sufijo siempre se
    # recalcula, que es justo la situación nueva que antes abortaba.
    for (pb_cache, vuelo_cache), angulo_cache in manifiesto.angulos_por_vuelo().items():
        angulos[(pb_cache, vuelo_cache, None)] = angulo_cache

    filas = [_construir_fila(dato, ventana, angulos, cfg, pipeline)
             for dato, ventana in asignaciones]
    filas = _desambiguar_colisiones_generales(filas)
    resultado = manifiesto.insertar_o_reabrir(filas, ejecucion_id=ejecucion_id)
    if resultado.saltadas:
        # `resultado.vuelos_saltados` solo trae (pb, vuelo) -el manifiesto no
        # guarda sufijo-, así que el sufijo de fecha (si ese par colisionó,
        # ver `colisiones_pb_vuelo`) se recupera de `ventanas` de ESTE run
        # para que el nombre de carpeta del log coincida con el real
        # (`_construir_fila`:869).
        sufijo_por_vuelo: dict[tuple[str, str], str | None] = {}
        for ventana in ventanas:
            sufijo_por_vuelo.setdefault((ventana["pb"], ventana["vuelo"]), ventana.get("sufijo"))
        vuelos = ", ".join(
            _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v, sufijo_por_vuelo.get((pb, vuelo)))
            for pb, vuelo in resultado.vuelos_saltados) or "sin vuelo asignado"
        progress_callback.emit(
            f"\n{resultado.saltadas} imagen(es) ya estaban organizadas en este destino "
            f"y se saltan (vuelos: {vuelos}).\n")

    total = len(metadatos)
    unassigned = sum(1 for _dato, ventana in asignaciones if ventana is None)
    sin_timestamp = sum(1 for dato, _ventana in asignaciones if dato.timestamp is None)

    # Desglose por tipo para el modal de progreso (ver LEDGER-metricas-progreso.md):
    # RGB y RGB_Extra se cuentan APARTE aunque `TIPOS_RGB` los trate igual para
    # procesar — es justo lo que el ledger pide, y fundirlos aquí escondería
    # cuántas imágenes van por el "tercer grupo" de sufijos.
    conteo_tipos = Counter(fila.tipo for fila in filas)
    progress_summarize.emit(STATS_INDICE_PREFIX + json.dumps({
        "fase": "Índice",
        "total": total,
        "rgb": conteo_tipos.get("RGB", 0),
        "termica": conteo_tipos.get("TERMICA", 0),
        "rgb_extra": conteo_tipos.get(NOMBRE_CARPETA_RGB_EXTRA, 0),
        "sin_asignar": unassigned,
        "sin_timestamp": sin_timestamp,
        "vuelos": len(ventanas),
        "ya_organizadas": resultado.saltadas,
    }))

    progress_bar.emit(100)
    return {
        "total": total,
        "unassigned": unassigned,
        "sin_timestamp": sin_timestamp,
        "vuelos": len(ventanas),
        "nuevas": resultado.nuevas,
        "saltadas": resultado.saltadas,
        "reintentadas": resultado.reintentadas,
        "vuelos_equipo_discrepa": vuelos_equipo_discrepa,
    }
