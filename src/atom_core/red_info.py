"""IPs/host locales de la máquina, para el modo "esperando estadillo"
(`atom_core/webserver.py` / `app_webview.Api.estadillo_espera_estado`).

El kiosco las enseña para que la app "Estadillo Digital" (Christian) se
conecte por IP si el mDNS (`organizer.local`) falla o aún no está montado
(ver LEDGER, pendiente "Pi hostname+avahi").

Solo stdlib: `ip -4 -o addr` vía `subprocess` con timeout (nunca cuelga el
hilo) + `socket.gethostname()`. Tolerante a fallos: ante cualquier problema
devuelve listas/valores vacíos, nunca lanza — es puramente informativo, no
debe tumbar el modo espera.
"""
from __future__ import annotations

import re
import socket
import subprocess

# `N: iface    inet 10.42.0.1/24 ...` (formato de `ip -4 -o addr`).
_LINEA_IP = re.compile(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/")

# La Pi expone el modo servidor en el 8765, pero en la LAN se llega por el 80
# (un `iptables`/`nginx` en la propia Pi hace el reenvío 80->8765): es el
# puerto que debe sugerirse a un cliente externo, no el de bind real del
# `ThreadingHTTPServer`.
PUERTO_PUBLICO = 80


def hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:  # noqa: BLE001 — informativo, nunca debe reventar
        return ""


def ips_locales(timeout: float = 2.0) -> list[dict]:
    """`[{"interfaz": "wlan0", "ip": "10.42.0.1"}, ...]`: todas las IPv4
    no-loopback, INCLUYE `169.254.x` (link-local sin DHCP: es la que queda
    si el AP aún no ha asignado nada). Vacía si `ip` no existe o falla."""
    try:
        proceso = subprocess.run(
            ["ip", "-4", "-o", "addr"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        salida = proceso.stdout or ""
    except Exception:  # noqa: BLE001 — sin `ip` en PATH, timeout, lo que sea
        return []

    ips: list[dict] = []
    for linea in salida.splitlines():
        m = _LINEA_IP.match(linea.strip())
        if not m:
            continue
        interfaz, ip = m.group(1), m.group(2)
        if interfaz == "lo" or ip.startswith("127."):
            continue
        ips.append({"interfaz": interfaz, "ip": ip})
    return ips


_RE_IP_SIMPLE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def _con_sufijo_local(destino: str) -> str:
    """Añade `.local` (mDNS) al hostname, salvo que ya lo traiga o sea una
    IP -una IP no se resuelve por mDNS, añadirle `.local` la rompería-."""
    if not destino or destino.lower().endswith(".local") or _RE_IP_SIMPLE.match(destino):
        return destino
    return f"{destino}.local"


def info_red() -> dict:
    """`{hostname, puerto, ips, url}` para el modo espera. `url` es la
    sugerencia SIN puerto (el 80 es el HTTP por defecto): con hostname si lo
    hay (con sufijo `.local` de mDNS), si no con la primera IP encontrada."""
    host = hostname()
    ips = ips_locales()
    destino = _con_sufijo_local(host) if host else (ips[0]["ip"] if ips else "")
    return {
        "hostname": host,
        "puerto": PUERTO_PUBLICO,
        "ips": ips,
        "url": f"http://{destino}" if destino else "",
    }
