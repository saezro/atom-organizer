"""Ubicación canónica de los estadillos en el bucket.

Funciones puras: calculan rutas, nombres y manifest. No tocan red ni disco,
para que la ruta de un estadillo sea reproducible y testeable sin bucket.
"""

import base64
from datetime import datetime, timezone

from atom_core.cloud_config import prefijo_desde_carpeta

# Cuelga de la planta en el bucket de ENTRADA (`BUCKET_DATOS`), que es el único
# donde escriben las cuentas de oficina: `ofi@aerotools.es` no tiene ningún
# binding IAM en `plantas_pv_nl`, así que la app de escritorio no puede dejar
# nada ahí. Sin `PREPARACION/` a propósito: esa jerarquía es del bucket de
# destino, y el de entrada ya usa `<PLANTA>/ESTADILLOS/` (regla de Cas).
PREFIJO_ESTADILLOS = "ESTADILLOS"
NOMBRE_MANIFEST = "manifest.json"
NOMBRE_NORMALIZADO = "estadillo.json"
CARPETA_ACTUAL = "actual"

VERSION_MANIFEST = 1
VERSION_NORMALIZADO = 1


def carpeta_subida(ahora: datetime) -> str:
    """Identificador de subida: fecha+hora UTC, ordenable alfabéticamente.

    No se usa el run_id de la Suite a propósito: lo genera el servidor y sin
    login no existe, así que la ruta no puede depender de él.

    Exige un `datetime` con zona y lo pasa a UTC. Un naive se RECHAZA en vez
    de asumirle UTC: el resto del repo usa `datetime.now()` naive (utils.py,
    updater.py, organize.py), y formatear una hora local con el sufijo `Z`
    daría una ruta mentirosa y no comparable entre subidas, sin avisar. Aquí
    el fallo sale en un test; asumiendo UTC saldría dentro del bucket.
    """
    if ahora.tzinfo is None:
        raise ValueError(
            "carpeta_subida() exige un datetime con zona horaria: "
            "usa datetime.now(timezone.utc), no datetime.now().")
    return ahora.astimezone(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def prefijo_planta(planta: str) -> str:
    return f"{prefijo_desde_carpeta(planta)}/{PREFIJO_ESTADILLOS}"


def nombre_objeto(orden: int, md5_hex: str, ext: str) -> str:
    sufijo = ext if ext.startswith(".") else f".{ext}"
    return f"{orden:02d}__{md5_hex[:8]}{sufijo}"


def md5_hex_desde_b64(md5_b64: str) -> str:
    return base64.b64decode(md5_b64).hex()


def construir_manifest(
    planta: str,
    subido_en: datetime,
    subido_por: str | None,
    ficheros: list[dict],
    validacion: dict,
) -> dict:
    """Manifest de una subida. Es la fuente de verdad del orden de prioridad.

    El nombre que puso el operario se conserva como metadato, nunca como
    identidad: la identidad es `objeto`.
    """
    return {
        "version": VERSION_MANIFEST,
        "planta": prefijo_desde_carpeta(planta),
        "subido_en": carpeta_subida(subido_en),
        "subido_por": subido_por,
        "ficheros": sorted(ficheros, key=lambda f: f["orden"]),
        "validacion": {
            "vuelos_detectados": validacion.get("vuelos_detectados", 0),
            "filas_con_problemas": validacion.get("filas_con_problemas", 0),
        },
    }


def construir_normalizado(vuelos: list[dict]) -> dict:
    """Formato estable del estadillo, independiente de si el crudo es xlsx o csv."""
    return {"version": VERSION_NORMALIZADO, "vuelos": vuelos}


def plan_subida(
    planta: str,
    ficheros_locales: list[dict],
    vuelos: list[dict],
    validacion: dict,
    ahora: datetime,
    subido_por: str | None = None,
) -> list[dict]:
    """Objetos a escribir, EN ORDEN.

    El manifest va último en cada carpeta: su presencia es la señal de
    "subida completa", así que una subida cortada deja la carpeta inválida
    sin necesidad de estado extra.
    """
    base = prefijo_planta(planta)
    carpeta = carpeta_subida(ahora)

    ficheros_manifest = []
    entradas_crudo = []
    for f in sorted(ficheros_locales, key=lambda x: x["orden"]):
        objeto = nombre_objeto(f["orden"], md5_hex_desde_b64(f["md5_b64"]), f["ext"])
        entradas_crudo.append({"objeto": objeto, "ruta_local": f["ruta"]})
        ficheros_manifest.append(
            {
                "orden": f["orden"],
                "objeto": objeto,
                "nombre_original": f["nombre_original"],
                "md5_b64": f["md5_b64"],
                "bytes": f["bytes"],
            }
        )

    manifest = construir_manifest(
        planta=planta,
        subido_en=ahora,
        subido_por=subido_por,
        ficheros=ficheros_manifest,
        validacion=validacion,
    )
    normalizado = construir_normalizado(vuelos)

    plan = []
    for destino in (carpeta, CARPETA_ACTUAL):
        for entrada in entradas_crudo:
            plan.append(
                {
                    "remoto": f"{base}/{destino}/{entrada['objeto']}",
                    "ruta_local": entrada["ruta_local"],
                }
            )
        plan.append(
            {
                "remoto": f"{base}/{destino}/{NOMBRE_NORMALIZADO}",
                "contenido": normalizado,
            }
        )
        plan.append(
            {
                "remoto": f"{base}/{destino}/{NOMBRE_MANIFEST}",
                "contenido": manifest,
            }
        )

    return plan


def ejecutar_plan(plan: list[dict], subir_fichero, subir_json) -> dict:
    """Ejecuta el plan en orden y aborta en el primer fallo.

    Abortar deja la carpeta sin manifest, que es exactamente lo que queremos:
    ningún consumidor la considerará válida.
    """
    subidos = 0
    ruta_manifest = None

    for entrada in plan:
        remoto = entrada["remoto"]
        try:
            if "ruta_local" in entrada:
                subir_fichero(remoto, entrada["ruta_local"])
            else:
                subir_json(remoto, entrada["contenido"])
        except Exception as exc:
            return {
                "ok": False,
                "subidos": subidos,
                "ruta_manifest": None,
                "error": f"Fallo subiendo {remoto}: {exc}",
            }

        subidos += 1
        if remoto.endswith(f"/{NOMBRE_MANIFEST}") and ruta_manifest is None:
            ruta_manifest = remoto

    return {"ok": True, "subidos": subidos, "ruta_manifest": ruta_manifest, "error": None}
