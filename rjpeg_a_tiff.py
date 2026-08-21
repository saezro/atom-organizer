"""
rjpeg_a_tiff.py -- Convierte a TIFF radiometrico (float32, grados C) todas las
imagenes termicas R-JPEG de una carpeta y sus subcarpetas, con CUALQUIER extension.

Standalone: no depende del Organizer. Usa el mismo SDK de DJI que el pipeline
(dji_irp.exe en Windows, libdirp.so en Linux) y copia el EXIF/GPS con exiftool.

Diferencias con el pipeline actual del Organizer (a proposito):
  * Acepta cualquier extension de imagen (.jpg .jpeg .JPG .png ...), no solo .JPG.
  * Deduce la resolucion termica del TAMANO DEL .raw, no de la del JPG.
    El pipeline hardcodea "si el JPG es 1280x1024 -> reshape(512,640)", que es
    cierto para la H20T pero FALSO para la H30T (termico real 1280x1024) y hace
    que la conversion reviente en esa camara.

Uso:
    python rjpeg_a_tiff.py "D:\\ruta\\del\\vuelo"
    python rjpeg_a_tiff.py "D:\\vuelo" --salida "D:\\tiffs" --emisividad 0.95 --humedad 70 --hilos 8

Requisitos: Python 3.9+, numpy, Pillow. El SDK de DJI y exiftool se autodetectan
si el script esta dentro del Organizer; si no, pasalos con --sdk y --exiftool.
"""
import argparse
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from PIL import Image

ES_WINDOWS = os.name == "nt"

# Resoluciones de sensor termico conocidas de DJI, para desambiguar el .raw.
RESOLUCIONES = [
    (1280, 1024), (640, 512), (640, 480), (1024, 768),
    (336, 256), (384, 288), (320, 256), (160, 120),
]

EXTS_IMAGEN = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".tif", ".tiff", ".dng"}


def localizar(nombre_win, nombre_nix, explicito, subcarpeta):
    """Devuelve la ruta de una herramienta externa: --flag > junto al script > PATH."""
    if explicito:
        return explicito
    base = os.path.dirname(os.path.abspath(__file__))
    objetivo = nombre_win if ES_WINDOWS else nombre_nix
    for raiz in (base, os.path.dirname(base)):
        for rel in ((subcarpeta, objetivo), (objetivo,)):
            cand = os.path.join(raiz, "programas_externos", *rel)
            if os.path.exists(cand):
                return cand
    return shutil.which(objetivo) or ""


# El payload radiometrico de DJI viaja en segmentos APP3/APP4/APP5 del JPEG.
# Un JPEG que los perdio se sigue viendo igual pero ya no tiene temperaturas.
# (mismo criterio que atom_core/rjpeg.py del Organizer)
MARCADORES_PAYLOAD = (0xE3, 0xE4, 0xE5)
MIN_PAYLOAD_BYTES = 64 * 1024
_SIN_LONGITUD = frozenset({0xD8, 0xD9, 0x01} | set(range(0xD0, 0xD8)))


def _payload_radiometrico(datos):
    """Suma los bytes de los segmentos APPn donde DJI mete los datos termicos."""
    if len(datos) < 2 or datos[0] != 0xFF or datos[1] != 0xD8:
        return 0
    total, i, n = 0, 2, len(datos)
    while i + 1 < n:
        if datos[i] != 0xFF:
            i += 1
            continue
        marcador = datos[i + 1]
        if marcador == 0xFF:
            i += 1
            continue
        if marcador == 0xDA:            # SOS: empiezan los datos comprimidos
            break
        if marcador in _SIN_LONGITUD:
            i += 2
            continue
        if i + 3 >= n:
            break
        longitud = (datos[i + 2] << 8) | datos[i + 3]
        if longitud < 2:
            break
        if marcador in MARCADORES_PAYLOAD:
            total += longitud
        i += 2 + longitud
    return total


def es_rjpeg(ruta):
    """True si el fichero conserva payload radiometrico DJI suficiente."""
    try:
        with open(ruta, "rb") as f:
            datos = f.read()
    except OSError:
        return False
    return _payload_radiometrico(datos) >= MIN_PAYLOAD_BYTES


def resolucion_desde_raw(n_valores, tam_jpg):
    """Deduce (ancho, alto) del termico a partir del numero de float32 del .raw."""
    ancho_jpg, alto_jpg = tam_jpg
    if ancho_jpg * alto_jpg == n_valores:          # termico = tamano del JPG (H30T)
        return ancho_jpg, alto_jpg
    for w, h in RESOLUCIONES:                      # resolucion conocida (H20T: 640x512)
        if w * h == n_valores:
            return w, h
    # Ultimo recurso: misma relacion de aspecto que el JPG.
    ar = ancho_jpg / float(alto_jpg)
    h = int(round(math.sqrt(n_valores / ar)))
    if h and n_valores % h == 0:
        return n_valores // h, h
    raise ValueError("no se puede deducir la resolucion termica de {0} valores".format(n_valores))


def medir_a_raw(img, raw, sdk, humedad, emisividad):
    """Invoca el SDK de DJI para volcar las temperaturas (float32, C) a `raw`."""
    if ES_WINDOWS:
        cmd = '"{0}" -s "{1}" -a measure --humidity {2} --emissivity {3} --measurefmt float32 -o "{4}"'.format(
            sdk, img, humedad, emisividad, raw)
        proc = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            stdin=subprocess.DEVNULL, cwd=os.path.dirname(sdk) or None,
            creationflags=0x08000000)                       # CREATE_NO_WINDOW
    else:
        wrapper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dji_irp_linux.py")
        proc = subprocess.run(
            [sys.executable, wrapper, img, raw, str(humedad), str(emisividad),
             os.path.dirname(sdk)],
            capture_output=True, text=True, errors="replace")
    rc = struct.unpack("i", struct.pack("I", proc.returncode & 0xFFFFFFFF))[0]
    if rc != 0:
        salida = (proc.stderr or proc.stdout or "").strip()[:200]
        raise RuntimeError("SDK DJI rc={0} {1}".format(rc, salida or "(sin salida)"))
    if not os.path.exists(raw):
        raise RuntimeError("el SDK termino en 0 pero no escribio el .raw (fallo silencioso)")


def convertir(img, destino, sdk, exiftool, humedad, emisividad):
    with Image.open(img) as im:
        tam_jpg = im.size
    tmp = tempfile.NamedTemporaryFile(suffix=".raw", delete=False)
    tmp.close()
    try:
        medir_a_raw(img, tmp.name, sdk, humedad, emisividad)
        with open(tmp.name, "rb") as f:
            datos = f.read()
        arr = np.frombuffer(datos, dtype="<f4")
        ancho, alto = resolucion_desde_raw(arr.size, tam_jpg)
        Image.fromarray(arr.reshape(alto, ancho)).save(destino, format="TIFF")
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    if exiftool:
        # 1a pasada: la copia estandar, identica a la del Organizer (pipeline.py:3255).
        subprocess.run([exiftool, "-tagsfromfile", img, destino,
                        "-overwrite_original_in_place"],
                       capture_output=True, stdin=subprocess.DEVNULL)
        # 2a pasada: el bloque XMP COMPLETO. La copia estandar deja fuera ~15 tags
        # XMP-drone-dji (DroneModel, SurveyingMode, RtkDiffAge, UTCAtExposure,
        # LRFTarget*, SensorTemperature...) porque exiftool no los sabe escribir uno
        # a uno. Copiando el bloque entero sin interpretarlo si viajan: 107 -> 115 tags.
        subprocess.run([exiftool, "-tagsfromfile", img, "-xmp>xmp", destino,
                        "-overwrite_original_in_place"],
                       capture_output=True, stdin=subprocess.DEVNULL)
    return destino


def recorrer(entrada):
    for raiz, _, ficheros in os.walk(entrada):
        for fichero in sorted(ficheros):
            if os.path.splitext(fichero)[1].lower() in EXTS_IMAGEN:
                yield os.path.join(raiz, fichero)


def main():
    p = argparse.ArgumentParser(description="R-JPEG -> TIFF radiometrico, recursivo.")
    p.add_argument("entrada", help="carpeta a recorrer (incluye subcarpetas)")
    p.add_argument("--salida", help="carpeta destino (por defecto: junto a cada original)")
    p.add_argument("--emisividad", type=float, default=0.9)
    p.add_argument("--humedad", type=float, default=50.0)
    p.add_argument("--hilos", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    p.add_argument("--sdk", help="ruta a dji_irp.exe / libdirp.so")
    p.add_argument("--exiftool", help="ruta a exiftool.exe (vacio = no copiar EXIF)")
    p.add_argument("--sobrescribir", action="store_true", help="rehacer TIFFs ya existentes")
    args = p.parse_args()

    if not os.path.isdir(args.entrada):
        p.error("no existe la carpeta: {0}".format(args.entrada))

    sdk = localizar("dji_irp.exe", "libdirp.so", args.sdk, "DJI")
    if not sdk or not os.path.exists(sdk):
        p.error("no encuentro el SDK de DJI. Pasalo con --sdk")
    exiftool = localizar("exiftool.exe", "exiftool", args.exiftool, "exiftool")
    if not exiftool:
        print("AVISO: sin exiftool, los TIFF saldran sin EXIF/GPS.")

    imagenes = list(recorrer(args.entrada))
    termicas, descartadas = [], 0
    for img in imagenes:
        if es_rjpeg(img):
            termicas.append(img)
        else:
            descartadas += 1
    print("{0} ficheros de imagen | {1} termicas R-JPEG | {2} no termicas (se ignoran)".format(
        len(imagenes), len(termicas), descartadas))
    if not termicas:
        return 0

    def destino_de(img):
        if args.salida:
            rel = os.path.relpath(os.path.dirname(img), args.entrada)
            carpeta = os.path.join(args.salida, rel) if rel != "." else args.salida
            os.makedirs(carpeta, exist_ok=True)
        else:
            carpeta = os.path.dirname(img)
        return os.path.join(carpeta, os.path.splitext(os.path.basename(img))[0] + ".tiff")

    pendientes = []
    saltadas = 0
    for img in termicas:
        dst = destino_de(img)
        if os.path.exists(dst) and not args.sobrescribir:
            saltadas += 1
            continue
        pendientes.append((img, dst))
    if saltadas:
        print("{0} ya tenian TIFF (usa --sobrescribir para rehacerlos)".format(saltadas))

    ok, errores = 0, []
    with ThreadPoolExecutor(max_workers=max(1, args.hilos)) as ex:
        futuros = {ex.submit(convertir, i, d, sdk, exiftool, args.humedad, args.emisividad): i
                   for i, d in pendientes}
        for n, fut in enumerate(as_completed(futuros), 1):
            img = futuros[fut]
            try:
                fut.result()
                ok += 1
            except Exception as e:
                errores.append((img, str(e)))
            print("\r  {0}/{1}  ok={2}  error={3}".format(n, len(pendientes), ok, len(errores)),
                  end="", flush=True)
    print()

    if errores:
        print("\n{0} imagenes con error:".format(len(errores)))
        for img, motivo in errores[:20]:
            print("  {0}: {1}".format(os.path.basename(img), motivo))
        if len(errores) > 20:
            print("  ... y {0} mas".format(len(errores) - 20))
    print("\nHecho: {0} TIFF generados.".format(ok))
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
