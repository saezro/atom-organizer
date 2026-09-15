"""Cierre del organizado: emite los CSV de salida y verifica el manifiesto contra disco.

Hoy (motor de 7 fases) los CSV se emiten incrementalmente, fase a fase, y las
verificaciones son contadores que cada fase va acumulando en objetos separados
(`RgbCropping.error_rgb_cropping`, `MetaLocation.error_meta_location`,
`ConvertToTif`...): si una fase se salta una carpeta o cuenta dos veces, nadie
más se entera. Aquí el manifiesto es la ÚNICA fuente: `emitir_csvs` escribe
desde una consulta a sus filas y `verificar` compara esas mismas filas contra
lo que hay realmente en disco, así que un desajuste no puede quedar oculto
detrás de un contador que ya se resetió.

`verificar` NUNCA lanza: devuelve la lista de problemas encontrados (vacía =
run correcto). Es la señal que decide si el resultado es publicable, así que
tiene que poder inspeccionarse y mostrarse entera, no solo un booleano.
"""
from __future__ import annotations

import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

import utils
from atom_core.almacen import abrir_para_lectura, es_uri_gcs, existe_ruta, publicar_en, tamano_de, unir
from atom_core.indice import NOMBRE_CARPETA_RGB_EXTRA, TIPOS_RGB

# Columnas exactas del CSV de criterio de giro que hoy escribe
# `Pipeline.write_videofiles_csv` (pipeline.py:1972). El giro/TIFF térmico lee
# este fichero por nombre de columna: cambiar el orden o el texto lo rompería
# en silencio.
_COLUMNAS_VIDEOFILES = ["New Name", "Original Name", "Degree"]


def _vuelos_del_manifiesto(manifiesto) -> list[tuple[str, str]]:
    """Pares (pb, vuelo) presentes en el manifiesto, en orden de aparición.

    Las filas "unassigned" (sin pb/vuelo asignado, `manifiesto.py`) no
    pertenecen a ningún vuelo y no generan CSV de criterio ni entran en las
    comprobaciones por vuelo: no hay carpeta `PBx_Vy` a la que asociarlas.
    """
    vistos: set[tuple[str, str]] = set()
    claves: list[tuple[str, str]] = []
    for fila in manifiesto.todas():
        pb, vuelo = fila["pb"], fila["vuelo"]
        if not pb or not vuelo:
            continue
        clave = (pb, vuelo)
        if clave not in vistos:
            vistos.add(clave)
            claves.append(clave)
    return claves


def _agrupar_por_vuelo(filas) -> "dict[tuple[str, str], list]":
    """Agrupa filas YA leídas por (pb, vuelo), en orden de aparición.

    Equivale a `_vuelos_del_manifiesto` + un `filas_por_vuelo` por vuelo, pero
    sin volver a la base de datos: `verificar` ya tiene todas las filas en la
    mano y repetir el escaneo completo del manifiesto más una consulta por
    vuelo no aporta nada (el manifiesto no cambia durante el cierre).
    Las filas "unassigned" (sin pb/vuelo) no pertenecen a ningún vuelo.
    """
    grupos: "dict[tuple[str, str], list]" = {}
    for fila in filas:
        pb, vuelo = fila["pb"], fila["vuelo"]
        if not pb or not vuelo:
            continue
        grupos.setdefault((pb, vuelo), []).append(fila)
    return grupos


def _revisar_ruta(ruta_origen: str, ruta: str) -> str | None:
    """Problema con `ruta` (o None si está bien). Una sola función para poder
    lanzarla en paralelo: cada llamada son 2 round-trips de metadata y en
    almacenamiento remoto (GCS/SMB) la latencia manda sobre todo lo demás."""
    if not existe_ruta(ruta):
        return f"{ruta_origen}: 'hecho' en el manifiesto pero {ruta} no existe en disco."
    if tamano_de(ruta) == 0:
        return f"{ruta_origen}: {ruta} existe pero está vacío."
    return None


def _escribir_csv(df: pd.DataFrame, destino: str) -> None:
    """Vuelca `df` a `destino` (local o `gs://…`) vía un temporal.

    Calca el patrón de `Pipeline.write_videofiles_csv`/`exif.gen_meta_location`:
    no se puede escribir directamente sobre `gs://…`, así que se vuelca a un
    temporal local y se publica desde ahí con el nombre final ya compuesto.
    """
    descriptor, nombre_temporal = tempfile.mkstemp(suffix=".csv")
    os.close(descriptor)
    ruta_temporal = Path(nombre_temporal)
    try:
        df.to_csv(ruta_temporal, sep=",", header=True, index=False)
        publicar_en(ruta_temporal, destino)
    finally:
        ruta_temporal.unlink(missing_ok=True)


def _emitir_csv_criterio(manifiesto, cfg, progress_callback) -> dict[str, str]:
    """Escribe `CSVs/_criterio/<PBx_Vy>_Videofiles.csv` por cada vuelo con térmicas.

    Solo las térmicas llevan este CSV (igual que hoy: `write_videofiles_csv`
    solo se llama `if not rgb_processing`), porque es lo que consume el giro
    del TIFF/JPG térmico (`read_auto_rotate_degree`, `pipeline.py:3308`), no
    algo que necesite la RGB.

    El `New Name` es `<PBx_Vy>_NNNN.JPG` con `NNNN` = posición de la imagen
    dentro del vuelo empezando en 1 (`str(indice + 1).zfill(4)`), en el orden
    de `Manifiesto.filas_por_vuelo` (por `id`, es decir, orden de inserción del
    índice) — igual que `pipeline.py:2078-2189` numera por posición dentro del
    bucle, nunca por un campo de la imagen.
    """
    csvs_root = unir(cfg.output_folder, "CSVs")
    criterio_folder = unir(csvs_root, utils.CRITERIO_DIRNAME)
    if not es_uri_gcs(criterio_folder):
        os.makedirs(criterio_folder, exist_ok=True)

    rutas_emitidas: dict[str, str] = {}
    # Un solo escaneo del manifiesto y agrupado en memoria, en vez de un
    # escaneo completo + una consulta por vuelo (ver `_agrupar_por_vuelo`).
    for (pb, vuelo), filas_vuelo in _agrupar_por_vuelo(manifiesto.todas()).items():
        termicas = [fila for fila in filas_vuelo if fila["tipo"] == "TERMICA"]
        if not termicas:
            continue

        clave = f"{pb}_{vuelo}"
        df = pd.DataFrame(columns=_COLUMNAS_VIDEOFILES)
        for indice, fila in enumerate(termicas):
            df.loc[len(df)] = {
                "New Name": f"{clave}_{str(indice + 1).zfill(4)}.JPG",
                "Original Name": os.path.basename(fila["ruta_origen"]),
                "Degree": fila["angulo_giro"],
            }

        nombre_csv = f"{clave}_Videofiles.csv"
        destino = unir(criterio_folder, nombre_csv)
        _escribir_csv(df, destino)
        progress_callback.emit(f"\nGenerado CSV de criterio: {destino}\n")
        rutas_emitidas[clave] = destino

    return rutas_emitidas


# Mismo recorrido que `MetaLocation.check_input_folder_and_iterate` (exif.py:1119):
# RGB, TERMICA y, si existe, RGB_Extra, en ESTE orden. El location de RGB_Extra
# pisa en `CSVs/` al de RGB homónimo igual que hoy.
_RAICES_META_LOCATION = (("RGB", "location.csv"), ("TERMICA", "meta.csv"),
                         (NOMBRE_CARPETA_RGB_EXTRA, "location.csv"))


def _lectura_de_fila(meta_location_obj, fila, progress_callback) -> tuple:
    """(coords, gimbal, xmp_data) como los devolvería `leer_exif_imagen`, pero
    desde el manifiesto. Filas sin posición leída (migradas, `gs://…`) caen a
    releer SU fichero de salida, como siempre."""
    if fila["meta_leida"] and fila["gimbal_yaw"] is not None:
        if fila["lat"] is None:
            return None, None, None
        nombre = os.path.basename(fila["ruta_salida_original"])
        return ((nombre, fila["lat"], fila["lon"], None),
                [fila["gimbal_yaw"], fila["gimbal_pitch"]],
                [fila["altitud_abs"], fila["altura_relativa"]])
    return meta_location_obj.leer_exif_imagen(fila["ruta_salida_original"], progress_callback)


def _carpeta_de(ruta: str) -> str:
    """Directorio que contiene `ruta` (fichero), URI-aware: en `gs://` las
    rutas son claves con `/` siempre, así que no vale `os.path.dirname` (mete
    `\\` en Windows)."""
    if es_uri_gcs(ruta):
        return ruta.rsplit("/", 1)[0]
    return os.path.dirname(ruta)


def _emitir_meta_location(manifiesto, cfg, progress_callback, proyecciones=None) -> dict[str, str]:
    """Emite `meta.csv`/`location.csv` desde el manifiesto.

    Antes reabría TODAS las imágenes de salida (≈300 s en MELINESTI). Ahora la
    posición viene del índice y solo se relee lo que no la tenga. La
    construcción del DataFrame y la escritura son las MISMAS funciones de
    `exif.MetaLocation` (`df_desde_lecturas`, `publicar_csv`): mismo orden,
    misma corrección de gimbal, mismo formato. El `import` es perezoso: `exif.py`
    arrastra `pyexiv2`/`geopy`/`exifread`."""
    import exif  # noqa: PLC0415 (import perezoso, ver docstring)
    from natsort import natsorted  # noqa: PLC0415
    from rjpeg_a_tiff import EXTS_FUENTE  # noqa: PLC0415

    organizer_logger = utils.OrganizerLogger("cierre_organizado", create_file_handler=False)
    meta_location_obj = exif.MetaLocation(organizer_logger)
    csv_folder = unir(cfg.output_folder, "CSVs")

    if not (existe_ruta(unir(cfg.output_folder, "TERMICA")) and existe_ruta(unir(cfg.output_folder, "RGB"))):
        progress_callback.emit("\nNo se han podido generar los archivos meta y location.\n")
        return {}

    grupos: "dict[tuple[str, str, str], list]" = {}
    for fila in manifiesto.todas():
        if fila["estado"] != "hecho" or fila["unassigned"] or not fila["pb"] or not fila["vuelo"]:
            continue
        grupos.setdefault((fila["tipo"], fila["pb"], fila["vuelo"]), []).append(fila)
    meta_location_obj.total_images_number = sum(len(f) for f in grupos.values())

    rutas_emitidas: dict[str, str] = {}
    try:
        for tipo, nombre_csv in _RAICES_META_LOCATION:
            for (tipo_grupo, pb, vuelo), filas in grupos.items():
                if tipo_grupo != tipo:
                    continue
                # Mismo filtro que `get_images_from_dir(..., ["_CROP"], solo_fuente=True)`.
                por_nombre = {}
                for fila in filas:
                    nombre = os.path.basename(fila["ruta_salida_original"])
                    if os.path.splitext(nombre)[1].lower() in EXTS_FUENTE and "_CROP" not in nombre:
                        por_nombre[nombre] = fila
                images = natsorted(por_nombre)
                if not images:
                    continue
                # Carpeta REAL de las imágenes de este vuelo, no recompuesta con
                # el `include_v` del run actual: si cambió entre cachitos (o
                # entre este cierre y el que escribió las imágenes), reconstruir
                # el nombre de carpeta con `_nombre_carpeta_vuelo` podía apuntar
                # a una carpeta inexistente y tumbar meta/location del resto de
                # vuelos (ver `except` de abajo, ahora por vuelo).
                try:
                    carpeta = _carpeta_de(por_nombre[images[0]]["ruta_salida_original"])
                    if not es_uri_gcs(carpeta):
                        os.makedirs(carpeta, exist_ok=True)
                    progress_callback.emit(
                        "\nProcesando {0} imágenes en directorio {1}".format(len(images), carpeta) + "\n")
                    # Las filas sin posición leída (migradas, `gs://…`) relanzan la
                    # lectura EXIF, que es I/O puro: en serie es la única parte del
                    # cierre que se queda esperando red en `gs://…` (ver
                    # `exif.MetaLocation.gen_meta_location`, mismo patrón/workers).
                    # `executor.map` conserva el orden de `images`.
                    with ThreadPoolExecutor(max_workers=utils.max_io_workers()) as executor:
                        lecturas = list(executor.map(
                            lambda n: _lectura_de_fila(meta_location_obj, por_nombre[n], progress_callback),
                            images))
                    df = meta_location_obj.df_desde_lecturas(
                        images, lecturas, progress_callback, progress_callback,
                        cfg.flight_height, cfg.calculate_proyected_distance)
                    meta_location_obj.publicar_csv(df, carpeta, nombre_csv, csv_folder, progress_callback)
                    if proyecciones is not None and cfg.calculate_proyected_distance:
                        for _indice, linea in df.iterrows():
                            proyecciones[por_nombre[linea["Foto"]]["ruta_salida_original"]] = (
                                linea["CalculatedDistance"], linea["LatitudFoto"], linea["LongitudFoto"])
                except Exception as excepcion_vuelo:  # pragma: no cover - salvaguarda
                    # Un vuelo roto no puede tumbar meta/location del resto: se
                    # avisa y se sigue con el siguiente (pb, vuelo).
                    progress_callback.emit(
                        f"\nERROR generando meta/location de PB{pb}_{vuelo}: {excepcion_vuelo}\n")
        rutas_emitidas["meta_location"] = csv_folder
    except Exception as excepcion:  # pragma: no cover - salvaguarda
        # No tumbar el cierre entero: criterio y verificaciones tienen que completarse.
        progress_callback.emit(f"\nERROR generando meta/location: {excepcion}\n")
    return rutas_emitidas


def emitir_csvs(manifiesto, cfg, progress_callback, proyecciones: dict | None = None) -> dict[str, str]:
    """Emite todos los CSV de salida del run desde el manifiesto.

    Devuelve un diccionario `{clave: ruta}` con lo emitido: una entrada por
    vuelo para el CSV de criterio (`"PB1_V01": ".../PB1_V01_Videofiles.csv"`)
    y, si `cfg.gen_meta_location`, la entrada `"meta_location"` con la carpeta
    `CSVs/` donde quedaron `meta.csv`/`location.csv`. Si `proyecciones` es un
    dict, se rellena con `{ruta_salida_original: (CalculatedDistance,
    LatitudFoto, LongitudFoto)}` para el índice Excel.
    """
    rutas_emitidas = _emitir_csv_criterio(manifiesto, cfg, progress_callback)
    if cfg.gen_meta_location:
        rutas_emitidas.update(_emitir_meta_location(manifiesto, cfg, progress_callback, proyecciones))
    return rutas_emitidas


def _ruta_csv_criterio(cfg, pb: str, vuelo: str) -> str:
    clave = f"{pb}_{vuelo}"
    return unir(unir(cfg.output_folder, "CSVs"), utils.CRITERIO_DIRNAME, f"{clave}_Videofiles.csv")


def _contar_lineas_csv(ruta: str) -> int:
    with abrir_para_lectura(ruta) as ruta_local, open(ruta_local, "r", encoding="utf-8") as fichero:
        total = sum(1 for linea in fichero if linea.strip())
    return max(total - 1, 0)  # -1 por la cabecera (header=True al escribirlo)


def verificar(manifiesto, cfg) -> list[str]:
    """Compara el manifiesto contra lo que hay en disco.

    Devuelve la lista de problemas encontrados; vacía significa run correcto.
    Nunca lanza: un run con problemas se reporta, no se hunde el cierre por
    ello (quien orquesta decide qué hacer con la lista).
    """
    problemas: list[str] = []
    filas = manifiesto.todas()

    # 4. Toda fila está 'hecho' o 'fallido': ninguna 'pendiente' ni 'en_curso'.
    # Un run interrumpido a mitad no puede darse por bueno solo porque las
    # filas que sí se procesaron salieron bien.
    sin_terminar = [fila for fila in filas if fila["estado"] in ("pendiente", "en_curso")]
    if sin_terminar:
        problemas.append(
            f"{len(sin_terminar)} imagen(es) siguen 'pendiente'/'en_curso': el run "
            f"se interrumpió antes de terminar. Ejemplo: {sin_terminar[0]['ruta_origen']}"
        )

    # 4b. Ninguna fila quedó 'fallido' (manifiesto.marcar_fallida): esas imágenes
    # no se han procesado y el run no puede darse por limpio solo porque el
    # resto salió bien.
    fallidas = [fila for fila in filas if fila["estado"] == "fallido"]
    if fallidas:
        ejemplo = fallidas[0]
        motivo = ejemplo["motivo_fallo"] if ejemplo["motivo_fallo"] else "sin motivo registrado"
        problemas.append(
            f"{len(fallidas)} imagen(es) fallaron y NO se han procesado. "
            f"Ejemplo: {ejemplo['ruta_origen']} ({motivo})"
        )

    # 5. Toda ruta de salida de una fila 'hecho' existe en disco y no está vacía.
    # Es el fallo más peligroso: el manifiesto dice que la imagen está lista
    # pero no hay nada que entregar.
    # En serie esto son hasta 3 rutas x 2 round-trips por imagen: en un run
    # grande, y sobre todo en almacenamiento remoto, tarda más que el propio
    # apply. Son operaciones de I/O puras (nada de CPU del intérprete), así
    # que van a un pool de hilos. `executor.map` conserva el orden de entrada,
    # así que la lista de problemas sale idéntica a la del bucle secuencial.
    por_comprobar = [
        (fila["ruta_origen"], fila[campo])
        for fila in filas if fila["estado"] == "hecho"
        for campo in ("ruta_salida_original", "ruta_salida_crop", "ruta_salida_tiff")
        if fila[campo]
    ]
    if por_comprobar:
        trabajadores = min(utils.max_io_workers(), len(por_comprobar))
        with ThreadPoolExecutor(max_workers=trabajadores) as executor:
            for problema in executor.map(lambda par: _revisar_ruta(*par), por_comprobar):
                if problema is not None:
                    problemas.append(problema)

    for (pb, vuelo), filas_vuelo in _agrupar_por_vuelo(filas).items():

        # 1. jpg_count == tiff_count por vuelo (hoy pipeline.py:2446-2556): cada
        # térmica emite un JPG y, si hay conversión, un TIFF hermano.
        if cfg.convert_to_tif:
            termicas = [fila for fila in filas_vuelo if fila["tipo"] == "TERMICA"]
            if termicas:
                jpg_count = len(termicas)
                tiff_count = sum(1 for fila in termicas if fila["ruta_salida_tiff"])
                if jpg_count != tiff_count:
                    problemas.append(
                        f"{pb}/{vuelo}: {jpg_count} imágenes JPG térmicas pero "
                        f"{tiff_count} TIFF. No coinciden."
                    )

            # 3. csv_lines == image_count (hoy exif.py:780-864): el CSV de
            # criterio ya emitido no puede tener menos ni más líneas que
            # térmicas del vuelo. Si aún no se ha emitido, no hay nada que
            # comparar todavía y no es un problema de `verificar` en sí.
            ruta_csv = _ruta_csv_criterio(cfg, pb, vuelo)
            if existe_ruta(ruta_csv):
                csv_lines = _contar_lineas_csv(ruta_csv)
                if csv_lines != len(termicas):
                    problemas.append(
                        f"{pb}/{vuelo}: el CSV de criterio tiene {csv_lines} línea(s) "
                        f"pero hay {len(termicas)} imagen(es) térmica(s). No coinciden."
                    )

        # 2. crop_count == non_crop_count (hoy pipeline.py:3900-3960): cada RGB
        # original produce un `_CROP` hermano cuando el recorte está activo.
        # `RGB_Extra` (`TIPOS_RGB`) cuenta aquí igual que RGB: en el motor
        # viejo `iterate_folders_for_rgb_cropping` recorre TODO el árbol de
        # salida salvo `TERMICA`, así que el tercer grupo de sufijos también
        # se recorta — dejarlo fuera de esta cuenta escondería un desajuste
        # real detrás de la verificación.
        if cfg.cropping_rgb:
            rgb = [fila for fila in filas_vuelo if fila["tipo"] in TIPOS_RGB]
            if rgb:
                non_crop_count = len(rgb)
                crop_count = sum(1 for fila in rgb if fila["ruta_salida_crop"])
                if crop_count != non_crop_count:
                    problemas.append(
                        f"{pb}/{vuelo}: {crop_count} imágenes recortadas pero "
                        f"{non_crop_count} originales RGB. No coinciden."
                    )

    return problemas
