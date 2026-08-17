"""Publicacion del estadillo en el bucket de PLANTAS.

El estadillo se SUBE al bucket de ENTRADA (`datos_para_organizar`), porque las
cuentas de la app de escritorio (`group:ofi@aerotools.es`) no tienen ningun
binding IAM en `plantas_pv_nl` y la primera subida real daria 403 (ruling de
Cas, ver `atom_core/estadillo_canonico.py:15`). Quien SI puede escribir en
`plantas_pv_nl` es la SA del Cloud Run Job
(`217557350193-compute@developer.gserviceaccount.com`, con
`roles/storage.admin` a nivel proyecto), asi que la jerarquia definitiva
`<PLANTA>/ESTADILLOS/<timestamp>/` la escribe el Job, aqui (SIN `PREPARACION/`:
ruling de Cas 2026-08-17).

Modulo PURO: calcula rutas, no toca red (el I/O vive en `gcs_publicar.py`).
"""

from __future__ import annotations

from atom_core.inspecciones import SEPARADOR, VACIO

BUCKET_PLANTAS = "plantas_pv_nl"
PREFIJO_PUBLICACION = "ESTADILLOS"  # sin `PREPARACION/`: ruling de Cas 2026-08-17


def planta_desde_prefijo(prefijo: str) -> str | None:
    """`empresa--planta--anio--tipo` -> `planta`. `None` si no es un prefijo.

    En `plantas_pv_nl` las carpetas son el NOMBRE DE PLANTA a secas
    (`CALAMOCHA`, `ANARGIROI_3`), no el prefijo de 4 campos del organizer.
    """
    partes = (prefijo or "").strip("/").split(SEPARADOR)
    if len(partes) != 4:
        return None
    planta = partes[1].strip()
    if not planta or planta == VACIO:
        return None
    return planta


def _nombre_publicado(fichero: dict) -> str:
    """Como se llama el fichero en el bucket de plantas.

    Se publica con el NOMBRE ORIGINAL (`dia1.csv`), no con el `NN__<md5>` del
    bucket de entrada: ese prefijo existe para deduplicar y ordenar la subida,
    y a quien abre `ESTADILLOS/` en el bucket de plantas no le
    dice nada.
    """
    nombre = str(fichero.get("nombre_original") or fichero.get("objeto") or "").strip()
    if not nombre:
        raise ValueError("fichero del manifest sin nombre publicable")
    if "/" in nombre or "\\" in nombre or nombre in (".", ".."):
        raise ValueError(f"nombre de fichero no simple: {nombre!r}")
    return nombre


def plan_publicacion(prefijo: str, manifest: dict) -> list[dict]:
    """Que objetos hay que escribir en `plantas_pv_nl` para este manifest.

    Lista vacia si el prefijo no permite deducir la planta: sin planta no hay
    destino legitimo, y `plantas_pv_nl` es SOLO para plantas — antes no
    escribir nada que inventarse una carpeta.
    """
    planta = planta_desde_prefijo(prefijo)
    if not planta:
        return []
    subido_en = str(manifest.get("subido_en") or "").strip()
    if not subido_en:
        return []
    base = f"{planta}/{PREFIJO_PUBLICACION}/{subido_en}"
    ficheros = sorted(manifest.get("ficheros") or [], key=lambda f: f.get("orden", 0))
    return [
        {
            "objeto_destino": f"{base}/{_nombre_publicado(f)}",
            "nombre_en_manifest": str(f.get("objeto") or ""),
            "orden": int(f.get("orden", 0)),
        }
        for f in ficheros
    ]
