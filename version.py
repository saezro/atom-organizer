"""Versión de ATOM Organizer — FUENTE ÚNICA.

Regla (igual que en atom-migrador): el tag git DEBE coincidir con este valor,
con la `v` delante → versión `3.2.0` ⇒ tag `v3.2.0`. El workflow de release
falla a propósito si no coinciden (evita publicar un instalador cuya versión
interna miente, que rompería el updater: compara este número con el de la
release publicada).

Se propaga a:
  - los metadatos VERSIONINFO del .exe (atom_organizer_webview.spec)
  - el instalador Inno Setup (packaging/windows/ATOM-Organizer.iss, vía /DMyVersion)
  - el chequeo de actualizaciones en runtime (atom_core/updater.py)
"""

__version__ = "3.4.15"
