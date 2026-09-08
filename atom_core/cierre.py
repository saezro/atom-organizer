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
from pathlib import Path

import pandas as pd

import utils
from atom_core.almacen import abrir_para_lectura, es_uri_gcs, existe_ruta, publicar_en, tamano_de, unir
from atom_core.indice import TIPOS_RGB

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
    for pb, vuelo in _vuelos_del_manifiesto(manifiesto):
        filas_vuelo = manifiesto.filas_por_vuelo(pb, vuelo)
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


def _emitir_meta_location(cfg, progress_callback) -> dict[str, str]:
    """Emite `meta.csv` y `location.csv` reutilizando `exif.MetaLocation` tal
    cual la usa hoy `PipelinePhasesMixin.split_images` (`phases.py:627-654`).

    No se reimplementa la lectura de GPS/gimbal: el manifiesto no guarda esos
    datos (no le hacen falta al apply) y sacarlos exige reabrir cada imagen,
    justo lo que el resto del motor plan→apply evita. Aquí solo se decide SI
    hace falta correrlo (`cfg.gen_meta_location`) y se delega en la clase ya
    validada. El `import` es perezoso a propósito: `exif.py` arrastra
    `pyexiv2`/`geopy`/`exifread`, y el resto de `cierre.py` (CSV de criterio,
    verificaciones) tiene que poder usarse sin esas dependencias cargadas.
    """
    import exif  # noqa: PLC0415 (import perezoso, ver docstring)

    organizer_logger = utils.OrganizerLogger("cierre_organizado", create_file_handler=False)
    meta_location_obj = exif.MetaLocation(organizer_logger)

    csv_folder = unir(cfg.output_folder, "CSVs")
    rutas_emitidas: dict[str, str] = {}
    try:
        meta_location_obj.total_images_number = 0
        ok = meta_location_obj.check_input_folder_and_iterate(
            cfg.output_folder, progress_callback, progress_callback, csv_folder,
            cfg.flight_height, cfg.calculate_proyected_distance,
        )
        if not ok:
            progress_callback.emit(
                "\nNo se han podido generar los archivos meta y location.\n"
            )
        else:
            rutas_emitidas["meta_location"] = csv_folder
    except Exception as excepcion:  # pragma: no cover - salvaguarda, ver docstring
        # No tumbar el cierre entero por un fallo al leer EXIF de una imagen
        # concreta: el CSV de criterio y las verificaciones sí tienen que
        # completarse aunque meta/location falle.
        progress_callback.emit(
            f"\nERROR generando meta/location: {excepcion}\n"
        )
    return rutas_emitidas


def emitir_csvs(manifiesto, cfg, progress_callback) -> dict[str, str]:
    """Emite todos los CSV de salida del run desde el manifiesto.

    Devuelve un diccionario `{clave: ruta}` con lo emitido: una entrada por
    vuelo para el CSV de criterio (`"PB1_V01": ".../PB1_V01_Videofiles.csv"`)
    y, si `cfg.gen_meta_location`, la entrada `"meta_location"` con la carpeta
    `CSVs/` donde quedaron `meta.csv`/`location.csv`.
    """
    rutas_emitidas = _emitir_csv_criterio(manifiesto, cfg, progress_callback)
    if cfg.gen_meta_location:
        rutas_emitidas.update(_emitir_meta_location(cfg, progress_callback))
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

    # 5. Toda ruta de salida de una fila 'hecho' existe en disco y no está vacía.
    # Es el fallo más peligroso: el manifiesto dice que la imagen está lista
    # pero no hay nada que entregar.
    for fila in filas:
        if fila["estado"] != "hecho":
            continue
        for campo in ("ruta_salida_original", "ruta_salida_crop", "ruta_salida_tiff"):
            ruta = fila[campo]
            if not ruta:
                continue
            if not existe_ruta(ruta):
                problemas.append(
                    f"{fila['ruta_origen']}: 'hecho' en el manifiesto pero {ruta} "
                    "no existe en disco."
                )
            elif tamano_de(ruta) == 0:
                problemas.append(
                    f"{fila['ruta_origen']}: {ruta} existe pero está vacío."
                )

    vuelos = _vuelos_del_manifiesto(manifiesto)
    for pb, vuelo in vuelos:
        filas_vuelo = manifiesto.filas_por_vuelo(pb, vuelo)

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
