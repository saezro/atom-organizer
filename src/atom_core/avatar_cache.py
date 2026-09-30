"""Caché local del avatar de Google del operador.

`<img src={cuenta.picture}>` apuntando directo a `lh3.googleusercontent.com`
falla a menudo dentro del WebView de Windows: sin `Referer` de navegador real
Google devuelve 429 con cierta frecuencia, y la red del WebView (proxy/firewall
corporativo) no siempre resuelve bien ese host. Además la pantalla de
selección de perfiles necesita pintar las caras **antes** de que exista sesión
activa, o sea, antes de tener un token con el que reintentar nada.

La solución es la de siempre para "un recurso remoto que casi nunca cambia":
Python lo descarga una vez, lo guarda en disco como `data:` URI ya lista para
un `<img src>`, y de ahí en adelante la UI no vuelve a tocar la red salvo que
la copia caduque. Un fallo de descarga nunca debe tumbar la UI: por eso esta
API no lanza excepciones — devuelve `""` y deja que el frontend haga su
fallback de siempre (la inicial del nombre sobre un círculo de color).

Solo stdlib (`urllib.request`), misma razón que el resto de `atom_core`: no
engordar el bundle de PyInstaller con una dependencia nueva solo para esto.
"""
from __future__ import annotations

import base64
import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

__all__ = ["ruta_cache", "obtener", "limpiar"]

NOMBRE_SUBCARPETA = "avatares"
TIMEOUT_SEGUNDOS = 5
LIMITE_BYTES = 2 * 1024 * 1024  # 2 MiB — un avatar real pesa ~10 KB; por encima, algo va mal
USER_AGENT = "atom-organizer-avatar-cache/1"

# Firma del hook inyectable de test: recibe la URL y devuelve (bytes, content_type).
Descargador = Callable[[str], "tuple[bytes, str]"]


def ruta_cache(dir_datos: Path) -> Path:
    """Carpeta donde viven los avatares cacheados (`<dir_datos>/avatares`)."""
    return Path(dir_datos) / NOMBRE_SUBCARPETA


def _nombre_fichero(email: str) -> str:
    """Deriva el nombre de fichero del email con un hash.

    No usamos el email crudo como nombre: en Windows hay caracteres de un
    email (nada frecuente, pero posible en dominios raros) que no son válidos
    en rutas, y además así no queda la cuenta de Google legible a simple
    vista en el listado de la carpeta de datos.
    """
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]


def _descarga_real(url: str) -> "tuple[bytes, str]":
    """Implementación real del hook `descargador`, con timeout y User-Agent propios."""
    peticion = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(peticion, timeout=TIMEOUT_SEGUNDOS) as respuesta:
        content_type = respuesta.headers.get("Content-Type", "")
        # Leemos como mucho el límite + 1 byte: así detectamos "se pasa" sin
        # tener que descargar potencialmente varios MB de más para saberlo.
        datos = respuesta.read(LIMITE_BYTES + 1)
        return datos, content_type


def _leer_data_uri_cacheada(ruta: Path, max_edad_dias: int) -> str:
    """Devuelve la data URI del fichero cacheado si existe y no ha caducado."""
    if not ruta.is_file():
        return ""
    edad_segundos = time.time() - ruta.stat().st_mtime
    if edad_segundos > max_edad_dias * 86400:
        return ""
    try:
        contenido = ruta.read_bytes()
    except OSError:
        return ""
    if not contenido:
        return ""
    # El content-type va como primera línea del propio fichero (ver _guardar);
    # el resto son los bytes crudos de la imagen.
    separador = contenido.find(b"\n")
    if separador == -1:
        return ""
    content_type = contenido[:separador].decode("ascii", errors="ignore")
    imagen = contenido[separador + 1:]
    b64 = base64.b64encode(imagen).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def _guardar(ruta: Path, imagen: bytes, content_type: str) -> None:
    """Escribe el fichero cacheado: primera línea el content-type, luego los bytes."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("wb") as f:
        f.write(content_type.encode("ascii", errors="ignore") + b"\n")
        f.write(imagen)


def obtener(
    url: str,
    email: str,
    dir_datos: Path,
    *,
    max_edad_dias: int = 30,
    descargador: Optional[Descargador] = None,
) -> str:
    """Devuelve una data URI del avatar, usando la copia local si es reciente.

    Si no hay copia local (o ha caducado) descarga con `descargador` (o la
    implementación real por defecto) y la cachea. Nunca lanza: cualquier fallo
    de red, DNS, timeout, disco o validación devuelve `""`, para que la UI
    haga su fallback de siempre (inicial sobre círculo de color).
    """
    if not url:
        return ""

    ruta = ruta_cache(dir_datos) / _nombre_fichero(email)

    cacheada = _leer_data_uri_cacheada(ruta, max_edad_dias)
    if cacheada:
        return cacheada

    descarga = descargador or _descarga_real
    try:
        imagen, content_type = descarga(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return ""

    if not imagen or len(imagen) > LIMITE_BYTES:
        return ""
    if not content_type or not content_type.startswith("image/"):
        return ""

    try:
        _guardar(ruta, imagen, content_type)
    except OSError:
        # No pudimos cachear (disco lleno, permisos...) pero sí tenemos los
        # bytes: devolvemos la data URI igualmente, solo que sin persistir.
        pass

    b64 = base64.b64encode(imagen).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def limpiar(dir_datos: Path, emails_vivos: set) -> None:
    """Borra del disco los avatares de perfiles que ya no existen."""
    carpeta = ruta_cache(dir_datos)
    if not carpeta.is_dir():
        return
    nombres_vivos = {_nombre_fichero(email) for email in emails_vivos}
    for fichero in carpeta.iterdir():
        if fichero.is_file() and fichero.name not in nombres_vivos:
            try:
                fichero.unlink()
            except OSError:
                pass
