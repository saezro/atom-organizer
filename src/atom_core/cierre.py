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

import logging
import os
from concurrent.futures import ThreadPoolExecutor

import utils
from atom_core.almacen import es_uri_gcs, existe_ruta, tamano_de, unir
from atom_core import pares as pares_mod
from atom_core.manifiesto import ruta_bajo_origen
from atom_core.indice import NOMBRE_CARPETA_RGB_EXTRA, TIPOS_RGB

logger = logging.getLogger(__name__)


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


_SIN_FECHA = "SIN_FECHA"


def _datetime_captura(texto):
    """datetime de `timestamp_exif` del manifiesto (ISO) o None."""
    import datetime as _dt  # noqa: PLC0415
    if not texto:
        return None
    try:
        return _dt.datetime.fromisoformat(str(texto))
    except ValueError:
        return None


def _fecha_captura(texto) -> str:
    """`YYYYMMDD` de la hora de captura del manifiesto, o '' si no hay."""
    t = _datetime_captura(texto)
    return t.strftime("%Y%m%d") if t is not None else ""


def _carpeta_de(ruta: str) -> str:
    """Directorio que contiene `ruta` (fichero), URI-aware: en `gs://` las
    rutas son claves con `/` siempre, así que no vale `os.path.dirname` (mete
    `\\` en Windows)."""
    if es_uri_gcs(ruta):
        return ruta.rsplit("/", 1)[0]
    return os.path.dirname(ruta)


def _emitir_meta_location(manifiesto, cfg, progress_callback, proyecciones=None, recuentos_csv=None) -> dict[str, str]:
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

    # Basta con una de las dos carpetas (run solo RGB o solo TERMICA).
    if not (existe_ruta(unir(cfg.output_folder, "TERMICA")) or existe_ruta(unir(cfg.output_folder, "RGB"))):
        organizer_logger.logger.error("ERROR: No existen los directorios TERMICA ni RGB; no se pueden generar los archivos meta y location.")
        progress_callback.emit("\nERROR: No se han podido generar los archivos meta y location (no existen TERMICA ni RGB).\n")
        return {}

    # Grupos por (tipo, pb, vuelo, FECHA): en un destino acumulado el mismo PB_V puede traer
    # días distintos y nunca se mezclan en un CSV. La fecha es la de la hora de captura del
    # manifiesto (la misma con la que el estadillo asignó el vuelo); sin ella, la del nombre
    # (respaldo, contado y avisado); sin ninguna, grupo "SIN_FECHA" (ERROR y no se emite).
    grupos: "dict[tuple[str, str, str, str], list]" = {}
    n_fecha_nombre = 0
    for fila in manifiesto.todas():
        if fila["estado"] != "hecho" or fila["unassigned"] or not fila["pb"] or not fila["vuelo"]:
            continue
        fecha = _fecha_captura(fila["timestamp_exif"])
        if not fecha:
            t_nombre = meta_location_obj._ts_nombre(fila["ruta_salida_original"])
            if t_nombre is not None:
                fecha, n_fecha_nombre = t_nombre.strftime("%Y%m%d"), n_fecha_nombre + 1
        grupos.setdefault((fila["tipo"], fila["pb"], fila["vuelo"], fecha or _SIN_FECHA), []).append(fila)
    meta_location_obj.total_images_number = sum(len(f) for f in grupos.values())
    if n_fecha_nombre:
        meta_location_obj._aviso(
            progress_callback,
            f"WARN: {n_fecha_nombre} imagen(es) sin hora de captura en el manifiesto: su día se "
            "toma del NOMBRE del fichero.", error=False)
    fechas_de_vuelo: "dict[tuple[str, str], list[str]]" = {}
    for (_tipo, pb, vuelo, fecha) in grupos:
        if fecha != _SIN_FECHA and fecha not in fechas_de_vuelo.setdefault((pb, vuelo), []):
            fechas_de_vuelo[(pb, vuelo)].append(fecha)
    for (pb, vuelo), fechas in sorted(fechas_de_vuelo.items()):
        if len(fechas) > 1:
            meta_location_obj._aviso(
                progress_callback,
                f"WARN: PB{pb}_{vuelo} aparece en {len(fechas)} fechas distintas ({', '.join(sorted(fechas))}): "
                "se genera un meta/location por fecha (<vuelo>_<YYYYMMDD>_meta|location.csv), sin mezclar días. "
                "Si existe un <vuelo>_meta|location.csv de una tanda anterior, mezcla días: bórralo a mano.",
                error=False)

    def _prefijo(pb, vuelo, fecha) -> str:
        return f"{fecha}_" if len(fechas_de_vuelo.get((pb, vuelo), [])) > 1 else ""

    rutas_emitidas: dict[str, str] = {}
    ctx = {"clave": None}  # (pb, vuelo) del grupo en curso; None = no contar (RGB_Extra)

    horas: dict = {}  # nombre -> hora de captura del manifiesto (del grupo en curso)

    def _por_nombre(filas) -> dict:
        # Mismo filtro que `get_images_from_dir(..., ["_CROP"], solo_fuente=True)`.
        res = {}
        for fila in filas:
            nombre = os.path.basename(fila["ruta_salida_original"])
            if os.path.splitext(nombre)[1].lower() in EXTS_FUENTE and "_CROP" not in nombre:
                res[nombre] = fila
                t = _datetime_captura(fila["timestamp_exif"])
                if t is not None:
                    horas[nombre] = t
        return res

    def _leer(images, por_nombre) -> list:
        # Las filas sin posición leída (migradas, `gs://…`) relanzan la lectura
        # EXIF, que es I/O puro: en paralelo (mismo patrón/workers que
        # `exif.MetaLocation.gen_meta_location`). `executor.map` conserva el orden.
        with ThreadPoolExecutor(max_workers=utils.max_io_workers()) as executor:
            return list(executor.map(
                lambda n: _lectura_de_fila(meta_location_obj, por_nombre[n], progress_callback),
                images))

    def _carpeta_real(por_nombre, images) -> str:
        # Carpeta REAL de las imágenes del vuelo, no recompuesta con el `include_v`
        # del run actual (ver historial: podía apuntar a una carpeta inexistente).
        carpeta = _carpeta_de(por_nombre[images[0]]["ruta_salida_original"])
        if not es_uri_gcs(carpeta):
            os.makedirs(carpeta, exist_ok=True)
        return carpeta

    def _publicar(images, por_nombre, lecturas, nombre_csv, carpeta, reordenar):
        df = meta_location_obj.df_desde_lecturas(
            images, lecturas, progress_callback, progress_callback,
            cfg.flight_height, cfg.calculate_proyected_distance)
        return df

    def _emitir_df(df, por_nombre, carpeta, nombre_csv, reordenar):
        meta_location_obj.publicar_csv(df, carpeta, nombre_csv, csv_folder, progress_callback,
                                       reordenar=reordenar)
        if recuentos_csv is not None and ctx["clave"] is not None:
            tipo_csv = "meta" if nombre_csv.endswith("meta.csv") else (
                "location" if nombre_csv.endswith("location.csv") else None)
            if tipo_csv:
                acum = recuentos_csv.setdefault(ctx["clave"], {"meta": 0, "location": 0})
                acum[tipo_csv] += len(df)
        if proyecciones is not None and cfg.calculate_proyected_distance:
            for _indice, linea in df.iterrows():
                proyecciones[por_nombre[linea["Foto"]]["ruta_salida_original"]] = (
                    linea["CalculatedDistance"], linea["LatitudFoto"], linea["LongitudFoto"])

    def _independiente(por_nombre, nombre_csv):
        # Orden SIEMPRE por hora de captura (manifiesto; respaldo: nombre); el idx DJI solo
        # desempata. Sin hora en ninguna parte: ERROR y la foto no entra (no se adivina).
        images, sin_hora, n_nombre = [], [], 0
        for n in por_nombre:
            t = horas.get(n)
            if t is None:
                t = meta_location_obj._ts_nombre(n)
                n_nombre += t is not None
            if t is None:
                sin_hora.append(n)
            else:
                clave = pares_mod.clave_pareja(n)
                images.append((t, clave[0] if clave else -1, n))
        images = [n for _t, _i, n in sorted(images)]
        if sin_hora:
            meta_location_obj._aviso(
                progress_callback,
                f"ERROR: {nombre_csv}: {len(sin_hora)} foto(s) sin hora de captura (p.ej. {sin_hora[0]}); "
                "no se pueden ordenar cronológicamente y se excluyen.")
        if n_nombre:
            meta_location_obj._aviso(
                progress_callback,
                f"WARN: {nombre_csv}: {n_nombre} foto(s) ordenadas por la hora del NOMBRE "
                "(sin hora de captura en el manifiesto).", error=False)
        if not images:
            return
        carpeta = _carpeta_real(por_nombre, images)
        progress_callback.emit(
            "\nProcesando {0} imágenes en directorio {1}".format(len(images), carpeta) + "\n")
        df = _publicar(images, por_nombre, _leer(images, por_nombre), nombre_csv, carpeta, False)
        _emitir_df(df, por_nombre, carpeta, nombre_csv, False)

    def _emparejado(pn_t, pn_w, pref=""):
        """Espejo de `MetaLocation.gen_meta_location_emparejado`: meta (T) y location (W)
        del vuelo desde UNA lista de pares por idx DJI, mismo orden, misma longitud."""
        imgs_t, imgs_w = natsorted(pn_t), natsorted(pn_w)
        if not imgs_t and not imgs_w:
            return
        pares, sin_pareja, sin_patron = meta_location_obj.emparejar_por_idx(imgs_t, imgs_w)
        if (not imgs_t or not imgs_w) or (not pares and sin_patron and not sin_pareja):
            # Vuelo solo RGB (o solo T) o sin patrón DJI: independientes, como antes del emparejado.
            _independiente(pn_w, pref + "location.csv")
            _independiente(pn_t, pref + "meta.csv")
            return
        meta_location_obj.avisos_orden(progress_callback)
        sin_hora = {n for par in meta_location_obj.pares_sin_hora for n in par}
        pares = [p for p in pares if p[0] not in sin_hora]
        meta_location_obj._aviso_sin_pareja(progress_callback, sin_pareja, _carpeta_de(
            (pn_t.get(imgs_t[0]) or pn_w[imgs_w[0]])["ruta_salida_original"]))
        for n in sin_patron:
            meta_location_obj._aviso(progress_callback, f"ERROR: {n} sin patrón DJI '_<idx>_T|W|V'; excluida de meta y location.")
        if not pares:
            meta_location_obj._aviso_sin_pares(progress_callback, _carpeta_de(
                (pn_t.get(imgs_t[0]) or pn_w[imgs_w[0]])["ruta_salida_original"]), len(imgs_t))
            return
        flat = [t for t, _w in pares]
        flat_w = [w for _t, w in pares]
        lect_t, lect_w = _leer(flat, pn_t), _leer(flat_w, pn_w)
        ok_t, ok_w, l_t, l_w = [], [], [], []
        for (t, w), lt, lw in zip(pares, lect_t, lect_w):
            if lt[0] is None or lw[0] is None:
                malas = [n for n, lec in ((t, lt), (w, lw)) if lec[0] is None]
                meta_location_obj._aviso(
                    progress_callback,
                    f"ERROR: par {t} / {w} excluido de meta y location: sin datos EXIF en {', '.join(malas)}.")
                continue
            ok_t.append(t); ok_w.append(w); l_t.append(lt); l_w.append(lw)
        if not ok_t:
            return
        carpeta_t, carpeta_w = _carpeta_real(pn_t, ok_t), _carpeta_real(pn_w, ok_w)
        df_t = _publicar(ok_t, pn_t, l_t, pref + "meta.csv", carpeta_t, False)
        df_w = _publicar(ok_w, pn_w, l_w, pref + "location.csv", carpeta_w, False)
        if len(df_t) != len(df_w):
            meta_location_obj._aviso(
                progress_callback,
                f"ERROR: {carpeta_t}: meta y location con distinta longitud ({len(df_t)} vs {len(df_w)}); no se escriben.")
            return
        _emitir_df(df_t, pn_t, carpeta_t, pref + "meta.csv", False)
        _emitir_df(df_w, pn_w, carpeta_w, pref + "location.csv", False)

    try:
        # RGB y TERMICA EMPAREJADOS por vuelo (mismo idx DJI = misma fila), igual que
        # `MetaLocation.check_input_folder_and_iterate`; luego RGB_Extra, que pisa el
        # location de RGB homónimo igual que hoy.
        vuelos = sorted({(pb, vuelo, fecha) for (tipo, pb, vuelo, fecha) in grupos
                         if tipo in ("RGB", "TERMICA")})
        for pb, vuelo, fecha in vuelos:
            if fecha == _SIN_FECHA:
                n = sum(len(grupos.get((t, pb, vuelo, fecha), [])) for t in ("RGB", "TERMICA"))
                meta_location_obj._aviso(
                    progress_callback,
                    f"ERROR: PB{pb}_{vuelo}: {n} imagen(es) sin hora de captura ni fecha en el nombre; "
                    "se excluyen de meta y location.")
                continue
            try:
                horas.clear()
                pn_t = _por_nombre(grupos.get(("TERMICA", pb, vuelo, fecha), []))
                pn_w = _por_nombre(grupos.get(("RGB", pb, vuelo, fecha), []))
                meta_location_obj._ts_externo = dict(horas)
                ctx["clave"] = (pb, vuelo)
                _emparejado(pn_t, pn_w, _prefijo(pb, vuelo, fecha))
            except Exception as excepcion_vuelo:  # pragma: no cover - salvaguarda
                progress_callback.emit(
                    f"\nERROR generando meta/location de PB{pb}_{vuelo} ({fecha}): {excepcion_vuelo}\n")
        for (tipo_grupo, pb, vuelo, fecha), filas in grupos.items():
            if tipo_grupo != NOMBRE_CARPETA_RGB_EXTRA or fecha == _SIN_FECHA:
                continue
            try:
                horas.clear()
                pn_x = _por_nombre(filas)
                meta_location_obj._ts_externo = dict(horas)
                ctx["clave"] = None
                _independiente(pn_x, _prefijo(pb, vuelo, fecha) + "location.csv")
            except Exception as excepcion_vuelo:  # pragma: no cover - salvaguarda
                progress_callback.emit(
                    f"\nERROR generando meta/location de PB{pb}_{vuelo}: {excepcion_vuelo}\n")
        rutas_emitidas["meta_location"] = csv_folder
    except Exception as excepcion:  # pragma: no cover - salvaguarda
        # No tumbar el cierre entero: criterio y verificaciones tienen que completarse.
        progress_callback.emit(f"\nERROR generando meta/location: {excepcion}\n")
    return rutas_emitidas


def emitir_csvs(manifiesto, cfg, progress_callback, proyecciones: dict | None = None,
                recuentos_csv: dict | None = None) -> dict[str, str]:
    """Emite todos los CSV de salida del run desde el manifiesto.

    Devuelve un diccionario `{clave: ruta}` con lo emitido: si
    `cfg.gen_meta_location`, la entrada `"meta_location"` con la carpeta
    `CSVs/` donde quedaron `meta.csv`/`location.csv`. Si `proyecciones` es un
    dict, se rellena con `{ruta_salida_original: (CalculatedDistance,
    LatitudFoto, LongitudFoto)}` para el índice Excel. Si `recuentos_csv` es un dict,
    se rellena con `{(pb, vuelo): {"meta": n, "location": n}}` (filas realmente publicadas).
    """
    rutas_emitidas: dict[str, str] = {}
    if cfg.gen_meta_location:
        rutas_emitidas.update(_emitir_meta_location(
            manifiesto, cfg, progress_callback, proyecciones, recuentos_csv))
    return rutas_emitidas


#: Prefijo de los problemas que son ERROR (imágenes fallidas), frente a los
#: avisos (desajustes de verificación). Lo usa `separar_problemas`.
PREFIJO_ERROR = "ERROR: "


#: Categorías de ERROR que NO salen del manifiesto (no las cuenta `fallido`) y
#: por eso deben sumarse al n_fallos del marcador de cierre. Cada línea va como
#: `ERROR: <prefijo><texto>`. La línea de imágenes fallidas NO está aquí: ya la
#: cuenta el manifiesto. Cuenta 1 por línea, no por foto.
PREFIJO_TERMICAS_PENDIENTES = "Térmicas pendientes: "
PREFIJO_DUPLICADOS = "Duplicados SIN_ORDENAR: "
PREFIJO_SIN_ORDENAR_NO_COMPROBABLE = "SIN_ORDENAR no comprobable: "
PREFIJO_VIEJOS_SIN_BORRAR = "Viejos SIN_ORDENAR sin borrar: "
PREFIJO_COHERENCIA_NO_COMPROBABLE = "Coherencia no comprobable: "


def contar_errores_extra(errores) -> int:
    """Nº de líneas ERROR de categorías no contadas por el manifiesto
    (coherencia incluida): 1 por línea. Ver las constantes PREFIJO_*."""
    marcas = tuple(PREFIJO_ERROR + m for m in (
        PREFIJO_COHERENCIA, PREFIJO_TERMICAS_PENDIENTES, PREFIJO_DUPLICADOS,
        PREFIJO_SIN_ORDENAR_NO_COMPROBABLE, PREFIJO_VIEJOS_SIN_BORRAR,
        PREFIJO_COHERENCIA_NO_COMPROBABLE))
    return sum(1 for p in errores if p.startswith(marcas))


def separar_problemas(problemas) -> tuple[list[str], list[str]]:
    """`(errores, avisos)`: las imágenes fallidas cuentan como ERROR."""
    errores = [p for p in problemas if p.startswith(PREFIJO_ERROR)]
    return errores, [p for p in problemas if not p.startswith(PREFIJO_ERROR)]


def linea_info_ajenas(manifiesto, cfg) -> str | None:
    """Línea INFORMATIVA (no es problema: no suma avisos ni cambia el estado) con
    las fallidas/pendientes de OTRO origen del destino acumulado; None si no hay."""
    origen = str(getattr(cfg, "input_folder", "") or "").strip()
    if not origen:
        return None
    rutas = [f["ruta_origen"] for f in manifiesto.todas()
             if f["estado"] in ("pendiente", "en_curso", "fallido")
             and not ruta_bajo_origen(f["ruta_origen"], origen)]
    if not rutas:
        return None
    carpetas = sorted({r.replace("\\", "/").rsplit("/", 1)[0] for r in rutas})
    return (f"ℹ️ Quedan {len(rutas)} imagen(es) fallidas/pendientes de otra carpeta "
            f"({', '.join(carpetas[:3])}"
            + (f" y {len(carpetas) - 3} más" if len(carpetas) > 3 else "")
            + "), no de esta tanda.")


#: Las líneas de coherencia por vuelo van como `ERROR: Incoherente <vuelo>: ...`.
PREFIJO_COHERENCIA = "Incoherente "


def etiqueta_vuelo(pb: str, vuelo: str) -> str:
    """`PB1_V1`; el PB GENERALES no es un vuelo: `FOTOS_GENERALES (<planta>)`."""
    if str(pb).strip().upper() == "GENERALES":
        return f"FOTOS_GENERALES ({vuelo})"
    return f"PB{pb}_V{vuelo}"


def recuentos_por_vuelo(filas_manifiesto, filas_csv, *, convert_to_tif: bool = False,
                        cropping_rgb: bool = False) -> "dict[tuple[str, str], dict]":
    """Recuentos y veredicto de coherencia por (pb, vuelo). PURA: sin I/O.

    Solo filas `hecho` con vuelo asignado (las fallidas/pendientes ya se
    reportan aparte). `filas_csv`: `{(pb, vuelo): {"meta": n, "location": n}}`
    con las filas REALES escritas (suma de todas las fechas), o None para no
    comprobar CSV (gen_meta_location apagado). RGB_Extra queda fuera.

    Reglas ERROR (solo lo que aplique al vuelo):
    - convert_to_tif y T>0: n_T != n_tiff
    - cropping_rgb y W>0: n_W != n_crop
    - CSV (si `filas_csv` no es None): T>0 -> filas_meta == n_T;
      T>0 y W>0 -> filas_location == filas_meta; solo W -> filas_location == n_W.
    """
    acum: "dict[tuple[str, str], dict]" = {}
    for fila in filas_manifiesto:
        pb, vuelo = fila["pb"], fila["vuelo"]
        if fila["estado"] != "hecho" or fila["unassigned"] or not pb or not vuelo:
            continue
        r = acum.setdefault((pb, vuelo), {"pb": pb, "vuelo": vuelo, "n_T": 0, "n_tiff": 0,
                                          "n_W": 0, "n_crop": 0})
        if fila["tipo"] == "TERMICA":
            r["n_T"] += 1
            r["n_tiff"] += 1 if fila["ruta_salida_tiff"] else 0
        elif fila["tipo"] == "RGB":
            r["n_W"] += 1
            r["n_crop"] += 1 if fila["ruta_salida_crop"] else 0

    for clave, r in acum.items():
        csv = None if filas_csv is None else filas_csv.get(clave, {})
        r["filas_meta"] = None if csv is None else int(csv.get("meta", 0))
        r["filas_location"] = None if csv is None else int(csv.get("location", 0))
        t, w, motivos = r["n_T"], r["n_W"], []
        if convert_to_tif and t and r["n_T"] != r["n_tiff"]:
            motivos.append("tiff != T")
        if cropping_rgb and w and r["n_W"] != r["n_crop"]:
            motivos.append("crop != W")
        if csv is not None:
            if t and r["filas_meta"] != t:
                motivos.append("meta != T")
            if t and w and r["filas_location"] != r["filas_meta"]:
                motivos.append("location != meta")
            if w and not t and r["filas_location"] != w:
                motivos.append("location != W")
        r["estado"] = "ERROR" if motivos else "OK"
        if str(r["pb"]).strip().upper() == "GENERALES":
            r["estado"], r["motivo"] = "EXCLUIDO", "FOTOS_GENERALES fuera de la regla"
            continue
        if motivos:
            partes = []
            if t:
                partes.append(f"T {t}")
                if convert_to_tif:
                    partes.append(f"tiff {r['n_tiff']}")
            if w:
                partes.append(f"W {w}")
                if cropping_rgb:
                    partes.append(f"crop {r['n_crop']}")
            if csv is not None:
                if t:
                    partes.append(f"meta {r['filas_meta']}")
                if w:
                    partes.append(f"location {r['filas_location']}")
            r["motivo"] = f"{', '.join(partes)} ({'; '.join(motivos)})"
        else:
            r["motivo"] = ""
    return acum


def calcular_recuentos(manifiesto, cfg, filas_csv) -> "dict[tuple[str, str], dict]":
    """`recuentos_por_vuelo` sobre todo el manifiesto con los flags de `cfg`."""
    return recuentos_por_vuelo(
        manifiesto.todas(), filas_csv,
        convert_to_tif=bool(getattr(cfg, "convert_to_tif", False)),
        cropping_rgb=bool(getattr(cfg, "cropping_rgb", False)))


def verificar(manifiesto, cfg, recuentos_csv=None) -> list[str]:
    """Compara el manifiesto contra lo que hay en disco.

    Devuelve la lista de problemas encontrados; vacía significa run correcto.
    Nunca lanza: un run con problemas se reporta, no se hunde el cierre por
    ello (quien orquesta decide qué hacer con la lista).
    """
    problemas: list[str] = []
    filas = manifiesto.todas()

    # Destino acumulado: pendientes/fallidas de OTRO origen (p. ej. el día anterior,
    # rutas que ya no existen) no son error de ESTA corrida; se avisan aparte. Sin
    # origen de corrida disponible (`cfg.input_folder` vacío) todas cuentan como error.
    origen = str(getattr(cfg, "input_folder", "") or "").strip()

    def _propia(fila) -> bool:
        return (not origen) or ruta_bajo_origen(fila["ruta_origen"], origen)

    ajenas: list = []

    # 4. Toda fila está 'hecho' o 'fallido': ninguna 'pendiente' ni 'en_curso'.
    # Un run interrumpido a mitad no puede darse por bueno solo porque las
    # filas que sí se procesaron salieron bien.
    sin_terminar_todas = [fila for fila in filas if fila["estado"] in ("pendiente", "en_curso")]
    sin_terminar = [fila for fila in sin_terminar_todas if _propia(fila)]
    ajenas += [fila for fila in sin_terminar_todas if not _propia(fila)]
    termicas_sin_procesar = [
        fila for fila in sin_terminar
        if fila["tipo"] == "TERMICA" and not fila["unassigned"] and fila["pb"] and fila["vuelo"]]
    if termicas_sin_procesar:
        # ERROR (no aviso): térmicas con vuelo asignado sin pasar por la fase T
        # acaban sin girar (incidente WILLKA 2026-10-07).
        problemas.append(
            f"{PREFIJO_ERROR}{PREFIJO_TERMICAS_PENDIENTES}{len(termicas_sin_procesar)} térmica(s) con vuelo asignado "
            f"siguen 'pendiente'/'en_curso': la fase T no las procesó (sin girar). "
            f"Ejemplo: {termicas_sin_procesar[0]['ruta_origen']}"
        )
    if sin_terminar:
        problemas.append(
            f"{len(sin_terminar)} imagen(es) siguen 'pendiente'/'en_curso': el run "
            f"se interrumpió antes de terminar. Ejemplo: {sin_terminar[0]['ruta_origen']}"
        )

    # 4b. Ninguna fila quedó 'fallido' (manifiesto.marcar_fallida): esas imágenes
    # no se han procesado y el run no puede darse por limpio solo porque el
    # resto salió bien.
    fallidas_todas = [fila for fila in filas if fila["estado"] == "fallido"]
    fallidas = [fila for fila in fallidas_todas if _propia(fila)]
    ajenas += [fila for fila in fallidas_todas if not _propia(fila)]
    if ajenas:
        # Destino acumulado: filas de otro origen no son de esta corrida; ni
        # aviso ni warning (solo traza interna).
        logger.debug("cierre.verificar: %d fila(s) fallida/pendiente de otro origen ignoradas",
                     len(ajenas))
    if fallidas:
        ejemplo = fallidas[0]
        motivo = ejemplo["motivo_fallo"] if ejemplo["motivo_fallo"] else "sin motivo registrado"
        problemas.append(
            f"{PREFIJO_ERROR}{len(fallidas)} imagen(es) fallaron y NO se han procesado. "
            f"Ejemplo: {ejemplo['ruta_origen']} ({motivo})"
        )

    # 4c. Duplicados en SIN_ORDENAR: foto ya ordenada que además sigue en
    # SIN_ORDENAR (misma foto con otra ruta de origen entre tandas, o borrado
    # fallido). ERROR visible; no se borra nada automáticamente.
    try:
        duplicados = manifiesto.duplicados_en_sin_ordenar()
        pendientes_borrado = manifiesto.borrados_sin_ordenar_pendientes()
    except Exception as exc:  # noqa: BLE001 — la comprobación no se traga
        problemas.append(f"{PREFIJO_ERROR}{PREFIJO_SIN_ORDENAR_NO_COMPROBABLE}{exc}")
        duplicados, pendientes_borrado = [], []
    if duplicados:
        ejemplos = "; ".join(f"{a} (ya ordenada en {b})" for a, b in duplicados[:10])
        problemas.append(
            f"{PREFIJO_ERROR}{PREFIJO_DUPLICADOS}{len(duplicados)} foto(s) en SIN_ORDENAR duplican una foto "
            f"ya ordenada en destino (mismo nombre y EXIF). Ejemplos (máx 10): {ejemplos}")
    if pendientes_borrado:
        problemas.append(
            f"{PREFIJO_ERROR}{PREFIJO_VIEJOS_SIN_BORRAR}{len(pendientes_borrado)} fichero(s) viejos de SIN_ORDENAR "
            f"no se pudieron borrar (se reintentará en la próxima tanda). Ejemplos "
            f"(máx 10): {'; '.join(pendientes_borrado[:10])}")

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

    # 6. Coherencia imágenes == tiff == filas meta/location por vuelo (ERROR, rojo).
    try:
        for (pb, vuelo), r in calcular_recuentos(manifiesto, cfg, recuentos_csv).items():
            if r["estado"] == "ERROR":
                problemas.append(
                    f"{PREFIJO_ERROR}{PREFIJO_COHERENCIA}{etiqueta_vuelo(pb, vuelo)}: {r['motivo']}")
    except Exception as exc:  # noqa: BLE001 la comprobación no se traga
        problemas.append(f"{PREFIJO_ERROR}{PREFIJO_COHERENCIA_NO_COMPROBABLE}{exc}")

    return problemas
