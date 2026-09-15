"""Índice Excel de la planta: `<destino>/INDICE_<PLANTA>.xlsx`, una fila por
imagen del manifiesto.

Se regenera ENTERO en cada cierre desde el manifiesto (la fuente); nunca se
lee de vuelta. Organizar por cachitos sobre el mismo destino hace que el
Excel acumule solo, porque el manifiesto acumula."""
from __future__ import annotations

import datetime
import os
import tempfile

from openpyxl import Workbook

from atom_core.almacen import es_uri_gcs, publicar_en
from atom_core.equipo import equipo_coincide, texto_coincide

COLUMNAS = (
    "PB", "Vuelo", "Tipo", "SinOrdenar",
    "NombreOriginal", "NombreNuevo", "TimestampEXIF", "Make", "Model", "EquipoEstadillo", "EquipoCoincide",
    "AnchoPx", "AltoPx", "BytesOrigen",
    "Lat", "Lon", "AltitudAbs", "AlturaRelativa", "GimbalYaw", "GimbalPitch", "GimbalRoll",
    "FlightYaw", "AlturaVuelo", "CalculatedDistance", "LatitudFoto", "LongitudFoto",
    "AnguloGiro", "PctRecorte", "Comprime", "RutaOriginal", "RutaCrop", "RutaTIFF",
    "Estado", "MotivoFallo", "Ejecucion", "FechaEjecucion", "RutaOrigen",
)


def nombre_planta(output_folder: str) -> str:
    return os.path.basename(str(output_folder).rstrip("/\\")) or "PLANTA"


def ruta_indice(output_folder: str) -> str:
    nombre = f"INDICE_{nombre_planta(output_folder)}.xlsx"
    if es_uri_gcs(output_folder):
        return f"{output_folder.rstrip('/')}/{nombre}"
    return os.path.join(output_folder, nombre)


def _numero(valor):
    """El XMP llega como texto (`+12.50`): en el Excel va como número para
    poder filtrar y ordenar. Lo que no sea número se deja tal cual."""
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return valor


def _relativa(ruta: str | None, base: str) -> str | None:
    if not ruta:
        return None
    if es_uri_gcs(base):
        prefijo = base.rstrip("/") + "/"
        return ruta[len(prefijo):] if ruta.startswith(prefijo) else ruta
    try:
        return os.path.relpath(ruta, base)
    except ValueError:  # otra unidad en Windows
        return ruta


def _si_no(valor) -> str:
    return "Sí" if valor else "No"


def _filas(manifiesto, cfg, proyecciones):
    ejecuciones = manifiesto.ejecuciones()
    base = cfg.output_folder
    for fila in manifiesto.todas():
        ejecucion = ejecuciones.get(fila["ejecucion_id"])
        distancia, lat_foto, lon_foto = proyecciones.get(fila["ruta_salida_original"], (None, None, None))
        pct = fila["pct_recorte"]
        yield (
            fila["pb"], fila["vuelo"], fila["tipo"], _si_no(fila["unassigned"]),
            fila["nombre_original"], fila["nombre_nuevo"] or None,
            fila["timestamp_exif"].replace("T", " ") if fila["timestamp_exif"] else None,
            fila["make"], fila["modelo"], fila["equipo_estadillo"],
            texto_coincide(equipo_coincide(fila["equipo_estadillo"], fila["modelo"])),
            fila["ancho_px"], fila["alto_px"], fila["bytes_origen"],
            fila["lat"], fila["lon"], _numero(fila["altitud_abs"]), _numero(fila["altura_relativa"]),
            _numero(fila["gimbal_yaw"]), _numero(fila["gimbal_pitch"]), _numero(fila["gimbal_roll"]),
            _numero(fila["flight_yaw"]),
            cfg.flight_height if cfg.calculate_proyected_distance else None,
            _numero(distancia), _numero(lat_foto), _numero(lon_foto),
            fila["angulo_giro"], round(pct * 100, 2) if pct is not None else None, _si_no(fila["comprime"]),
            _relativa(fila["ruta_salida_original"], base), _relativa(fila["ruta_salida_crop"], base),
            _relativa(fila["ruta_salida_tiff"], base),
            fila["estado"], fila["motivo_fallo"], fila["ejecucion_id"],
            ejecucion["inicio"] if ejecucion is not None else None, fila["ruta_origen"],
        )


def escribir_indice(manifiesto, cfg, proyecciones: dict, progress_callback) -> str:
    """Escribe el índice y devuelve la ruta final. Si el fichero está abierto
    (Excel en Windows bloquea el `.xlsx`), escribe al lado con marca de hora y
    avisa: un índice bloqueado no puede tumbar el cierre."""
    libro = Workbook(write_only=True)
    hoja = libro.create_sheet("Imagenes")
    hoja.freeze_panes = "A2"
    hoja.append(list(COLUMNAS))
    for fila in _filas(manifiesto, cfg, proyecciones):
        hoja.append(list(fila))

    destino = ruta_indice(cfg.output_folder)
    if es_uri_gcs(destino):
        descriptor, temporal = tempfile.mkstemp(suffix=".xlsx")
        os.close(descriptor)
        try:
            libro.save(temporal)
            publicar_en(temporal, destino)
        finally:
            os.unlink(temporal)
        return destino

    # Temporal en la MISMA carpeta: `os.replace` entre discos falla (EXDEV).
    temporal = destino + ".tmp"
    libro.save(temporal)
    try:
        os.replace(temporal, destino)
        return destino
    except PermissionError:
        marca = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        alternativo = os.path.join(os.path.dirname(destino),
                                   f"INDICE_{nombre_planta(cfg.output_folder)}_{marca}.xlsx")
        os.replace(temporal, alternativo)
        progress_callback.emit(
            f"\nAVISO: {os.path.basename(destino)} está abierto; el índice se ha "
            f"guardado como {os.path.basename(alternativo)}.\n")
        return alternativo
    finally:
        if os.path.exists(temporal):
            os.unlink(temporal)
