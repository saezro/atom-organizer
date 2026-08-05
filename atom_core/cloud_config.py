"""Configuración de la subida al bucket: bucket destino y credenciales OAuth.

El **client_secret no vive en el repo**: `atom-organizer` es PÚBLICO y el
secret-scanning de GitHub avisa a Google, que revoca el cliente — la app se
quedaría sin login de un día para otro sin que nadie hubiera tocado nada.
(Para un cliente "installed" el secret no protege gran cosa —el permiso real
lo pone IAM sobre el bucket y el flujo usa PKCE— pero eso no evita la
revocación automática.)

Se busca en tres sitios, en orden, y gana el primero que traiga las dos piezas:

1. **Entorno** `ATOM_GOOGLE_CLIENT_ID` / `ATOM_GOOGLE_CLIENT_SECRET` — lo que
   usan los tests y el desarrollo.
2. **`google_client.json`** junto al ejecutable o en el perfil del usuario.
   Acepta tal cual el JSON que descarga la consola de Google (bloque
   `installed`), sin editarlo: es lo que se le pide a quien monta un equipo.
3. **`_BUILD_*`** — lo que inyecta el workflow de release desde los secrets del
   repo, para que el `.exe` publicado funcione sin fichero suelto.

Si no aparece por ninguna vía, `load_client()` devuelve `None` y la UI enseña
el motivo en vez de fallar con un traceback.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Bucket de ENTRADA: aquí deja el operador lo que hay que organizar. El Cloud
# Run que organiza sólo LEE de aquí y escribe en `plantas_pv_nl`; nunca toca
# estos objetos. Regla fijada por Cas (2026-08-06).
BUCKET_DATOS = "datos_para_organizar"

# Sólo cuentas de Aerotools. Es un filtro de UI (evita que alguien se loguee con
# su Gmail personal y no entienda el 403); el permiso de verdad es el IAM del
# bucket, que sólo tiene `group:ofi@aerotools.es`.
HOSTED_DOMAIN = "aerotools.es"

CLIENT_FILENAME = "google_client.json"

# Rellenados en build por el workflow de release (sustitución literal). Vacíos
# en el repo a propósito.
_BUILD_CLIENT_ID = ""
_BUILD_CLIENT_SECRET = ""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: str
    origin: str  # de dónde salió, para poder decirlo en la UI


def _from_env() -> OAuthClient | None:
    cid = os.environ.get("ATOM_GOOGLE_CLIENT_ID", "").strip()
    sec = os.environ.get("ATOM_GOOGLE_CLIENT_SECRET", "").strip()
    if cid and sec:
        return OAuthClient(cid, sec, "entorno")
    return None


def client_file_candidates(base_dir: Path | None = None) -> list[Path]:
    """Dónde se busca `google_client.json`, en orden."""
    out: list[Path] = []
    if base_dir is not None:
        out.append(Path(base_dir) / CLIENT_FILENAME)
    out.append(Path(__file__).resolve().parent.parent / CLIENT_FILENAME)
    try:
        from atom_core.google_auth import user_data_dir

        out.append(user_data_dir() / CLIENT_FILENAME)
    except Exception:  # noqa: BLE001 - si no se puede resolver, se omite
        pass
    # Sin duplicados y conservando el orden.
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        k = str(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def _from_file(base_dir: Path | None) -> OAuthClient | None:
    for path in client_file_candidates(base_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # El JSON de la consola de Google envuelve todo en `installed`.
        blob = data.get("installed") or data.get("web") or data
        cid = str(blob.get("client_id") or "").strip()
        sec = str(blob.get("client_secret") or "").strip()
        if cid and sec:
            return OAuthClient(cid, sec, str(path))
    return None


def _from_build() -> OAuthClient | None:
    cid = _BUILD_CLIENT_ID.strip()
    sec = _BUILD_CLIENT_SECRET.strip()
    if cid and sec:
        return OAuthClient(cid, sec, "build")
    return None


def load_client(base_dir: Path | None = None) -> OAuthClient | None:
    """Credenciales del cliente OAuth, o `None` si no hay ninguna configurada."""
    return _from_env() or _from_file(base_dir) or _from_build()


def prefijo_desde_carpeta(nombre: str) -> str:
    """Nombre de carpeta → prefijo válido dentro del bucket.

    Los nombres reales traen espacios y eñes (`MARISOLES_LOS MANGOS`, `OCAÑA`):
    se pasan a ASCII y los huecos a `_` para que la ruta del objeto sea legible
    y no dependa del sistema de ficheros de quien sube.
    """
    import unicodedata

    base = unicodedata.normalize("NFKD", (nombre or "").strip())
    base = base.encode("ascii", "ignore").decode("ascii")
    limpio = []
    for ch in base:
        if ch.isalnum() or ch in "-_":
            limpio.append(ch)
        elif ch in " ./\\":
            limpio.append("_")
    out = "".join(limpio).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out


def missing_client_help() -> str:
    """Qué contarle al operador cuando no hay credenciales."""
    rutas = "\n".join(f"  · {p}" for p in client_file_candidates())
    return (
        "No hay credenciales de Google configuradas, así que no se puede iniciar "
        "sesión.\n\nDeja el fichero «" + CLIENT_FILENAME + "» (el que descarga la "
        "consola de Google para un cliente de escritorio) en una de estas rutas:\n"
        + rutas
    )
