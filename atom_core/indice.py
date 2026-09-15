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
from dataclasses import dataclass

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
    """Se han fusionado estadillos con el mismo (PB, vuelo) y fechas
    distintas. `construir_indice` aborta con esta excepción ANTES de escribir
    ninguna fila en el manifiesto, no a mitad como hacía el motor viejo (que
    ya había movido parte de las imágenes cuando se topaba con la colisión)."""


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
    col_equipo = nombres_columnas.get("Equipo_de_vuelo")

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

        ventanas.append({"pb": pb, "vuelo": vuelo, "inicio": inicio, "fin": fin,
                         "equipo": equipo})
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
        carpeta_vuelo = almacen.unir(
            output_folder, "TERMICA", f"PB{pb}", _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v))
        candidato_csv = almacen.unir(
            output_folder, "CSVs", utils.CRITERIO_DIRNAME,
            f"{_nombre_carpeta_vuelo(pb, vuelo, cfg.include_v)}_Videofiles.csv")
        if almacen.existe_ruta(candidato_csv):
            angulos[(pb, vuelo)] = pipeline.read_auto_rotate_degree(
                carpeta_vuelo, progress_callback)
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
        pct = pipeline.get_percentage_by_model(dato.modelo, pipeline.percentage_by_models)
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
                    angulos: dict[tuple[str, str], int], cfg, pipeline) -> FilaManifiesto:
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
        angulo_giro = angulos.get((pb, vuelo), 0)
        carpeta_destino = almacen.unir(
            cfg.output_folder, tipo, f"PB{pb}",
            _nombre_carpeta_vuelo(pb, vuelo, cfg.include_v))

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

    asignaciones = [(dato, _asignar_vuelo(dato, ventanas)) for dato in metadatos]
    vuelos_equipo_discrepa = _avisar_equipo(asignaciones, progress_callback)
    angulos = _consenso_de_angulo_por_vuelo(asignaciones, pipeline, cfg,
                                            cfg.output_folder, progress_callback)
    # Cachito posterior del mismo destino: el ángulo ya decidido para un vuelo
    # manda sobre el recalculado, para que JPG y TIFF del vuelo no discrepen.
    angulos.update(manifiesto.angulos_por_vuelo())

    filas = [_construir_fila(dato, ventana, angulos, cfg, pipeline)
             for dato, ventana in asignaciones]
    resultado = manifiesto.insertar_o_reabrir(filas, ejecucion_id=ejecucion_id)
    if resultado.saltadas:
        vuelos = ", ".join(_nombre_carpeta_vuelo(pb, vuelo, cfg.include_v)
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
