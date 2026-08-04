"""Comprobación e instalación de actualizaciones (Windows).

Patrón calcado del de atom-migrador, pero SIN electron-updater (esto es
PyInstaller, no Electron): se consulta directamente la GitHub Releases API del
repo público `saezro/atom-organizer`, se compara la versión publicada con
``version.__version__`` y, si hay una nueva, se descarga el instalador Inno
Setup y se lanza en modo silencioso.

Puntos importantes:
  - `/releases/latest` de GitHub IGNORA las pre-releases. Una release marcada
    como pre-release NO se ofrece como actualización (es deliberado: así se
    puede publicar una build de prueba sin empujarla a todo el mundo).
  - Sólo stdlib (urllib): el repo es público, no hace falta token, y añadir
    `requests` al bundle no aporta nada.
  - En Linux el binario es un AppImage y no hay instalador: se avisa de que
    hay versión nueva pero la instalación es manual.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = "saezro/atom-organizer"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# Nombre del asset que publica el CI (packaging/windows/ATOM-Organizer.iss)
ASSET_RE = re.compile(r"^ATOM-Organizer-Setup-v.+\.exe$", re.IGNORECASE)
TIMEOUT = 15
USER_AGENT = "ATOM-Organizer-Updater"


def current_version() -> str:
    try:
        from version import __version__

        return __version__
    except Exception:
        return "0.0.0"


def _parse(v: str) -> tuple:
    """'v3.10.1' -> (3, 10, 1). Ignora sufijos (-rc1, etc.) para el orden."""
    nums = re.findall(r"\d+", (v or "").split("-")[0])
    parts = [int(n) for n in nums[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return _parse(remote) > _parse(local)


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check() -> dict:
    """Consulta la última release ESTABLE. No lanza: devuelve {ok: False, error}."""
    local = current_version()
    try:
        data = _get_json(API_LATEST)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Aún no hay ninguna release estable (todas pre-release)
            return {"ok": True, "update_available": False, "current": local,
                    "latest": local, "reason": "sin releases estables"}
        return {"ok": False, "error": f"HTTP {exc.code} al consultar GitHub"}
    except Exception as exc:  # red caída, DNS, proxy corporativo…
        return {"ok": False, "error": f"No se pudo comprobar: {exc}"}

    latest = (data.get("tag_name") or "").lstrip("v")
    asset = next((a for a in data.get("assets", []) if ASSET_RE.match(a.get("name", ""))), None)

    return {
        "ok": True,
        "update_available": bool(latest) and is_newer(latest, local),
        "current": local,
        "latest": latest,
        "notes": (data.get("body") or "").strip()[:4000],
        "page": data.get("html_url") or RELEASES_PAGE,
        "asset_name": (asset or {}).get("name"),
        "asset_url": (asset or {}).get("browser_download_url"),
        "asset_size": (asset or {}).get("size") or 0,
        # El instalador silencioso sólo existe en Windows; en Linux (AppImage)
        # la actualización es manual desde la página de la release.
        "can_install": platform.system() == "Windows" and asset is not None,
    }


def download(url: str, expected_size: int = 0, progress=None) -> dict:
    """Descarga el instalador a %TEMP%. `progress(pct, done, total)` opcional."""
    if not url:
        return {"ok": False, "error": "La release no trae instalador adjunto."}
    dest_dir = Path(tempfile.gettempdir()) / "atom-organizer-update"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (url.rsplit("/", 1)[-1] or "ATOM-Organizer-Setup.exe")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp, open(dest, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or expected_size or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    pct = int(done * 100 / total) if total else 0
                    progress(pct, done, total)
    except Exception as exc:
        return {"ok": False, "error": f"Descarga fallida: {exc}"}

    # Sanity: el instalador pesa >20 MB; si es menos, algo cortó la descarga
    # (portal cautivo, proxy que devuelve HTML). Mejor fallar aquí que ejecutar basura.
    size = dest.stat().st_size
    if size < 20 * 1024 * 1024 or (expected_size and size != expected_size):
        try:
            dest.unlink()
        except OSError:
            pass
        return {"ok": False, "error": "El fichero descargado está incompleto o corrupto."}

    return {"ok": True, "path": str(dest), "size": size}


# Códigos de salida de Inno Setup que conviene traducir. El 5 es el que salía
# siempre antes de /FORCECLOSEAPPLICATIONS: el Restart Manager no lograba cerrar
# la app, Inno sacaba un Abort/Retry/Ignore y, suprimidos los mensajes, tomaba
# el default (Abort) y cancelaba en silencio.
_INNO_EXIT = {
    1: "El instalador no pudo iniciarse (parámetros incorrectos).",
    2: "La instalación se canceló antes de empezar.",
    3: "Error fatal preparando la instalación.",
    4: "Error fatal durante la instalación.",
    5: "La instalación se canceló: no se pudo cerrar la aplicación en uso.",
    6: "La instalación se detuvo por un cierre de sesión de Windows.",
    8: "Windows necesita reiniciarse para completar la instalación.",
}


def _install_log_path() -> Path:
    """Dónde escribe Inno su `/LOG`.

    En la MISMA carpeta que los logs de corrida (`%APPDATA%\\ATOM-Organizer\\Logs`),
    no en %TEMP%. Es deliberado: esa carpeta es la que el usuario ya sabe abrir y
    la que nos manda cuando algo falla, así que el log de la instalación llega
    solo, sin ningún mecanismo extra de recogida. Y sobrevive a la instalación:
    el proceso que lo escribe es el instalador, no la app (a la que Inno mata).
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"atom-organizer-install_{ts}.log"
    try:
        from external_tools import user_log_dir

        d = Path(user_log_dir())
        d.mkdir(parents=True, exist_ok=True)
        return d / name
    except Exception:
        # Peor sitio, pero mejor que no tener log.
        return Path(tempfile.gettempdir()) / "atom-organizer-update" / name


def install(path: str, on_failure=None) -> dict:
    """Lanza el instalador en silencio y deja que cierre/reabra la app.

    Flags Inno Setup:
      /VERYSILENT             sin UI (salvo el UAC si lo hubiera; el instalador es per-user)
      /SUPPRESSMSGBOXES       sin diálogos bloqueantes
      /CLOSEAPPLICATIONS      cierra la instancia en marcha (la nuestra) para poder sustituir ficheros
      /FORCECLOSEAPPLICATIONS mata las que no responden al cierre limpio. Sin esto,
                              `QtWebEngineProcess` ignora al Restart Manager, Inno aborta
                              con ExitCode 5 y la actualización NO se aplica (verificado
                              en la VM de pruebas, 2026-08-04).
      /RESTARTAPPLICATIONS    intenta reabrirla al terminar. Con force-close el Restart
                              Manager ya no la revive: de relanzarla se encarga la entrada
                              [Run] con `Check: WizardSilent` del .iss.
      /NORESTART              nunca reiniciar Windows
      /LOG="<ruta>"           deja el log de la instalación en la carpeta de logs.

    Sobre el `/LOG` (2026-08-04): sin él la instalación era una caja negra —
    exactamente el mismo agujero que tenía el conversor DJI. Cuando a Daniel le
    saltó el modal y la app no volvió a abrirse, no hubo forma de saber si la
    instalación había ido bien y sólo falló el relanzado, o si había fallado
    entera: `_watch` no sirve para eso, porque cuando la instalación va bien esta
    app está muerta antes de poder informar de nada. El log lo escribe el
    instalador, así que sobrevive a la app y dice si la entrada [Run] llegó a
    ejecutarse.

    `on_failure(codigo, mensaje)` — opcional — se llama desde un hilo si el
    instalador termina con un código != 0. Sólo llega si el proceso sigue vivo
    para verlo: cuando la instalación va bien, esta app muere antes. Un fallo
    silencioso (la UI colgada en «Instalando…» para siempre) es peor que un error.
    """
    if platform.system() != "Windows":
        return {"ok": False, "error": "La instalación automática sólo está disponible en Windows."}
    exe = Path(path)
    if not exe.exists():
        return {"ok": False, "error": "No se encuentra el instalador descargado."}
    log_path = _install_log_path()
    try:
        creationflags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
        proc = subprocess.Popen(
            [str(exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS",
             "/FORCECLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/NORESTART",
             f"/LOG={log_path}"],
            close_fds=True,
            creationflags=creationflags,
            cwd=str(exe.parent),
        )
    except Exception as exc:
        return {"ok": False, "error": f"No se pudo lanzar el instalador: {exc}"}

    if on_failure is not None:
        threading.Thread(target=_watch, args=(proc, on_failure, str(log_path)),
                         daemon=True).start()
    return {"ok": True, "log": str(log_path)}


def _watch(proc: "subprocess.Popen", on_failure, log_path: str = "") -> None:
    try:
        code = proc.wait()
    except Exception:
        return
    if code == 0:
        return
    msg = _INNO_EXIT.get(code, f"El instalador terminó con el código {code}.")
    if log_path:
        msg = f"{msg} Detalle en {log_path}"
    try:
        on_failure(code, msg)
    except Exception:
        pass
