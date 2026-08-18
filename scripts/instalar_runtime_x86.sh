#!/usr/bin/env bash
# Instala el runtime x86-64 que permite ejecutar el DJI Thermal SDK en máquinas ARM
# (Raspberry Pi). DJI solo publica el SDK para x86-64, así que la conversión térmica
# se emula con box64: hace falta un intérprete Python x86-64 mínimo y la libgomp x86
# real (el wrapper nativo de box64 no trae GOMP_critical_start/end, que libdirp usa).
#
# Idempotente. En una máquina x86-64 no hace nada: ahí el SDK corre nativo.
#
#   Uso:  scripts/instalar_runtime_x86.sh [destino]
#   Destino por defecto: <repo>/programas_externos/x86-runtime
set -euo pipefail

PY_VER="3.11.12"
PY_TAG="20250521"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_TAG}/cpython-${PY_VER}+${PY_TAG}-x86_64-unknown-linux-gnu-install_only.tar.gz"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destino="${1:-$repo_dir/programas_externos/x86-runtime}"

if [ "$(uname -m)" = "x86_64" ]; then
  echo "Esta máquina ya es x86-64: el SDK de DJI corre nativo, no hace falta runtime emulado."
  exit 0
fi

if ! command -v box64 >/dev/null 2>&1; then
  echo "ERROR: falta box64. Instálalo con:  sudo apt install box64" >&2
  exit 1
fi

if [ -x "$destino/bin/python3" ] && [ -f "$destino/lib-x86/libgomp.so.1" ]; then
  echo "Runtime x86 ya instalado en $destino"
  exit 0
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "==> Descargando Python ${PY_VER} x86-64..."
curl -fsSL --retry 3 -o "$tmp/py.tgz" "$PY_URL"
mkdir -p "$tmp/py" && tar xzf "$tmp/py.tgz" -C "$tmp/py" --strip-components=1

# Poda: dji_irp_linux.py solo usa ctypes/os/sys. Todo lo demás (tests, tkinter,
# idlelib, pip, cabeceras...) son ~100 MB que no se van a ejecutar jamás.
echo "==> Podando el intérprete..."
lib="$tmp/py/lib/python${PY_VER%.*}"
rm -rf "$tmp/py/include" "$tmp/py/share" "$lib/test" "$lib/idlelib" "$lib/tkinter" \
       "$lib/lib2to3" "$lib/ensurepip" "$lib/pydoc_data" "$lib/site-packages" \
       "$lib/config-"* "$lib/turtledemo" "$lib/distutils" 2>/dev/null || true
# tcl/tk solo servían a tkinter, que acabamos de borrar.
rm -rf "$tmp/py/lib/tcl8.6" "$tmp/py/lib/tk8.6" "$tmp/py/lib/Tix8.4.3" "$tmp/py/lib/itcl4.2.4" \
       "$tmp/py/lib/tcl8" "$tmp/py/lib/thread2.8.9" "$tmp/py/lib/tdbc"* "$tmp/py/lib/sqlite3"* 2>/dev/null || true
find "$tmp/py" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
# De los módulos de extensión solo hacen falta los que arrastra ctypes.
if [ -d "$lib/lib-dynload" ]; then
  find "$lib/lib-dynload" -name "*.so" ! -name "_ctypes*" ! -name "_struct*" \
       ! -name "math*" ! -name "_posixsubprocess*" -delete
fi

# El intérprete viene con símbolos de debug: 114 MB de los 156 son tabla de símbolos
# que nunca se van a leer. strip los deja en 48 MB. Verificado: la salida térmica es
# byte-idéntica con y sin strip. Requiere un strip que entienda ELF x86-64 — el de
# binutils aarch64 es single-target y no vale; si no hay ninguno se salta (solo cuesta
# disco, no funcionalidad).
x86strip=""
for cand in llvm-strip x86_64-linux-gnu-strip strip; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" "$tmp/py/bin/python3.11" 2>/dev/null; then
    x86strip="$cand"; break
  fi
done
if [ -n "$x86strip" ]; then
  "$x86strip" "$tmp/py/lib/libpython${PY_VER%.*}.so.1.0" 2>/dev/null || true
  echo "    (strip aplicado con $x86strip)"
else
  echo "    (sin strip: no hay binutils para x86-64. Opcional: sudo apt install binutils-x86-64-linux-gnu)"
fi

echo "==> Obteniendo libgomp x86-64..."
mkdir -p "$tmp/gomp"
pool="https://deb.debian.org/debian/pool/main/g/gcc-14/"
deb="$(curl -fsSL "$pool" | grep -o 'libgomp1_[^"]*_amd64\.deb' | sort -V | tail -1)"
if [ -z "$deb" ]; then
  echo "ERROR: no se encontró libgomp1 amd64 en $pool" >&2
  exit 1
fi
curl -fsSL --retry 3 -o "$tmp/gomp/libgomp1.deb" "${pool}${deb}"
( cd "$tmp/gomp" && ar x libgomp1.deb && tar xf data.tar.* )
gomp="$(find "$tmp/gomp" -name "libgomp.so.1.*" | head -1)"
[ -n "$gomp" ] || { echo "ERROR: el .deb no traía libgomp.so.1" >&2; exit 1; }

echo "==> Instalando en $destino"
rm -rf "$destino"
mkdir -p "$destino/lib-x86"
cp -a "$tmp/py/." "$destino/"
cp -L "$gomp" "$destino/lib-x86/libgomp.so.1"

echo "==> Verificando..."
if BOX64_EMULATED_LIBS=libgomp.so.1 LD_LIBRARY_PATH="$destino/lib-x86" \
   box64 "$destino/bin/python3" -c "import ctypes,platform;assert platform.machine()=='x86_64'" 2>/dev/null; then
  echo "OK — runtime x86 listo ($(du -sh "$destino" | cut -f1)) en $destino"
else
  echo "ERROR: el runtime instalado no arranca bajo box64" >&2
  exit 1
fi
