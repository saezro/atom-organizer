"""Post-build: garantiza que ipaddress.pyc esté en base_library.zip.

PyInstaller con Python 3.11 no incluye `ipaddress` en base_library.zip; el
bootstrap (urllib.parse) lo importa en cabecera y la app peta con
ModuleNotFoundError antes de arrancar. hiddenimports NO lo cura (va al PYZ,
no al base_library.zip que se carga en el bootstrap). Este script lo inyecta
de forma idempotente tras el build. Vale para Linux y Windows.
"""
import os
import sys
import zipfile
import py_compile

zippath = os.path.join("dist", "atom_organizer", "base_library.zip")
if not os.path.exists(zippath):
    print(f"ERROR: no existe {zippath}", file=sys.stderr)
    sys.exit(1)

with zipfile.ZipFile(zippath) as z:
    if "ipaddress.pyc" in z.namelist():
        print("ipaddress.pyc ya presente, nada que hacer")
        sys.exit(0)

import ipaddress  # noqa: E402  -- para localizar el .py del stdlib del runner

pyc = os.path.join(os.getcwd(), "ipaddress.pyc")
py_compile.compile(ipaddress.__file__, cfile=pyc, optimize=0)
with zipfile.ZipFile(zippath, "a", zipfile.ZIP_DEFLATED) as z:
    z.write(pyc, "ipaddress.pyc")
print("ipaddress.pyc inyectado en base_library.zip")
