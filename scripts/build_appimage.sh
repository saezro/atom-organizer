#!/usr/bin/env bash
# Monta el AppImage de ATOM Organizer (UI webview Qt/QtWebEngine) a partir del
# onedir que deja PyInstaller en dist/atom_organizer/.
#
# Prerrequisitos (ya ejecutados por el workflow / build local, en este orden):
#   1. cd webui && npm ci && npm run build      → webui/dist
#   2. pyinstaller --clean --noconfirm atom_organizer_webview_linux.spec
#   3. python inject_ipaddress.py               → ipaddress en base_library.zip
#
# Uso:  scripts/build_appimage.sh <version>     (p.ej. v3.3)
# Sale: ATOM_Organizer-<version>-x86_64.AppImage en la raíz del repo.
set -euo pipefail

VERSION="${1:?uso: build_appimage.sh <version, p.ej. v3.3>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d dist/atom_organizer ]; then
  echo "ERROR: falta dist/atom_organizer (¿corriste PyInstaller con atom_organizer_webview_linux.spec?)" >&2
  exit 1
fi

APP=ATOM.AppDir
rm -rf "$APP"
mkdir -p "$APP/usr/lib"
cp -r dist/atom_organizer "$APP/usr/lib/atom_organizer"

cp packaging/AppRun "$APP/AppRun"
chmod +x "$APP/AppRun"
cp packaging/ATOM-Organizer.desktop "$APP/ATOM-Organizer.desktop"
cp packaging/atom-organizer.png "$APP/atom-organizer.png"
ln -sf atom-organizer.png "$APP/.DirIcon"

# appimagetool: usa el del PATH si existe (build local), si no baja el continuous.
if command -v appimagetool >/dev/null 2>&1; then
  AT=(appimagetool)
else
  if [ ! -x ./appimagetool-x86_64.AppImage ]; then
    wget -q https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x appimagetool-x86_64.AppImage
  fi
  # --appimage-extract-and-run: los runners de CI no tienen FUSE.
  AT=(./appimagetool-x86_64.AppImage --appimage-extract-and-run)
fi

OUT="ATOM_Organizer-${VERSION}-x86_64.AppImage"
ARCH=x86_64 "${AT[@]}" "$APP" "$OUT"
echo "OK -> $OUT"
