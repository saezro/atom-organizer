#!/usr/bin/env python3
"""Compara dos árboles de salida del "organizado" de ATOM Organizer.

Es la red de seguridad antes de sustituir el motor viejo por uno nuevo: se le
dan las dos carpetas de salida (misma inspección procesada por ambos motores)
y dice si son equivalentes, listando cualquier discrepancia.

    python tools/comparar_organizados.py <dir_a> <dir_b> [--json salida.json]

Reglas de comparación (por qué no todo es "byte a byte"):

- `.tiff`/`.tif` (imagen radiométrica cruda): byte a byte. Aquí cualquier
  diferencia es un fallo real, no hay recompresión de por medio.
- `.jpg`/`.jpeg`/`.png`: los bytes del fichero NO sirven de comparación,
  porque una recompresión JPEG con distinta calidad cambia los bytes sin
  cambiar la imagen. Se compara en su lugar el hash de los píxeles
  decodificados (fast-path exacto) y, si no coincide, con tolerancia de
  recompresión: el motor nuevo hace menos encodes que el viejo (menos
  pasos de guardado/recompresión de la misma imagen), así que sus JPEG
  finales nunca serán byte a byte ni píxel a píxel idénticos a los del
  motor viejo aunque el contenido sea el mismo. Se admite una diferencia
  máxima de píxel y un RMSE por debajo de un umbral (`--tolerancia-pixel`
  / `--tolerancia-rmse`, o `--estricto` para exigir píxeles idénticos) y
  el EXIF normalizado (ignorando el thumbnail, que se regenera en cada
  guardado y no forma parte del contenido real).
- `.csv` (estadillos, listados): fila a fila, para poder señalar exactamente
  en qué fila difieren en vez de un simple "distintos".
- cualquier otra extensión: byte a byte, por defecto conservador.

Código de salida: 0 si son equivalentes, 1 si hay cualquier discrepancia.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MAX_DISCREPANCIAS_POR_TIPO = 50

EXTENSIONES_TIFF = {".tiff", ".tif"}
EXTENSIONES_IMAGEN_COMPRIMIDA = {".jpg", ".jpeg", ".png"}
EXTENSIONES_CSV = {".csv"}

# Claves del bloque EXIF que se regeneran en cada guardado y no forman parte
# del contenido real de la imagen: comparar el thumbnail haría fallar
# ficheros idénticos solo porque una librería u otra lo recodifica distinto.
CLAVES_EXIF_IGNORADAS = {"1st", "thumbnail"}


def listar_arbol(directorio: Path) -> set:
    """Devuelve el conjunto de rutas relativas (str, con '/') de todos los
    ficheros bajo `directorio`, recursivo. Incluye SIN_ORDENAR como cualquier
    otra carpeta: no hay tratamiento especial, si está en el árbol se compara.
    """
    rutas = set()
    for ruta in directorio.rglob("*"):
        if ruta.is_file():
            rutas.add(ruta.relative_to(directorio).as_posix())
    return rutas


def _sha256_fichero(ruta: Path) -> str:
    """Hash sha256 de los bytes crudos del fichero."""
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _sha256_pixeles(ruta: Path) -> str:
    """Hash sha256 de los píxeles decodificados (no de los bytes del fichero):
    así una recompresión JPEG con distinta calidad no cuenta como
    discrepancia si la imagen resultante es la misma.
    """
    from PIL import Image

    with Image.open(ruta) as img:
        datos = img.convert("RGB").tobytes()
    return hashlib.sha256(datos).hexdigest()


def _exif_normalizado(ruta: Path):
    """Carga el EXIF con piexif y descarta las claves que legítimamente
    cambian al reescribir el fichero (thumbnail e IFD '1st', que se
    regeneran). Si el EXIF es ilegible, devuelve `None` junto con el motivo:
    en sí mismo ya es una discrepancia si el otro lado sí lo tiene legible.
    """
    import piexif

    try:
        exif_dict = piexif.load(str(ruta))
    except Exception as exc:  # noqa: BLE001 - se reporta como fallo, no se relanza
        return None, str(exc)

    normalizado = {
        clave: valor
        for clave, valor in exif_dict.items()
        if clave not in CLAVES_EXIF_IGNORADAS
    }
    return normalizado, None


def _comparar_csv(ruta_a: Path, ruta_b: Path):
    """Compara dos CSV fila a fila. Devuelve `None` si son iguales, o un
    `dict` con el índice de la primera fila que difiere (y su contenido en
    cada lado) en caso contrario. También detecta distinto número de filas.
    """
    with open(ruta_a, newline="", encoding="utf-8", errors="replace") as fa:
        filas_a = list(csv.reader(fa))
    with open(ruta_b, newline="", encoding="utf-8", errors="replace") as fb:
        filas_b = list(csv.reader(fb))

    for indice, (fila_a, fila_b) in enumerate(zip(filas_a, filas_b)):
        if fila_a != fila_b:
            return {
                "fila": indice,
                "contenido_a": fila_a,
                "contenido_b": fila_b,
            }

    if len(filas_a) != len(filas_b):
        return {
            "fila": min(len(filas_a), len(filas_b)),
            "contenido_a": f"<{len(filas_a)} filas en total>",
            "contenido_b": f"<{len(filas_b)} filas en total>",
        }

    return None


def _comparar_pixeles_con_tolerancia(
    ruta_a: Path, ruta_b: Path, tolerancia_max: int, tolerancia_rmse: float
):
    """Compara los píxeles decodificados de dos imágenes con tolerancia de
    recompresión. Devuelve `None` si son equivalentes (hash exacto o dentro
    de tolerancia), o el motivo en caso de discrepancia.
    """
    import numpy as np
    from PIL import Image

    if _sha256_pixeles(ruta_a) == _sha256_pixeles(ruta_b):
        return None

    with Image.open(ruta_a) as img_a:
        array_a = np.array(img_a.convert("RGB"), dtype=np.int16)
    with Image.open(ruta_b) as img_b:
        array_b = np.array(img_b.convert("RGB"), dtype=np.int16)

    if array_a.shape != array_b.shape:
        return (
            "dimensiones distintas "
            f"({array_a.shape[1]}x{array_a.shape[0]} vs "
            f"{array_b.shape[1]}x{array_b.shape[0]})"
        )

    diferencia = array_a - array_b
    diferencia_maxima = int(np.abs(diferencia).max())
    rmse = float(np.sqrt(np.mean(np.square(diferencia.astype(np.float64)))))

    if diferencia_maxima > tolerancia_max or rmse > tolerancia_rmse:
        return (
            "píxeles distintos más allá de la tolerancia "
            f"(dif_max={diferencia_maxima}, rmse={rmse:.2f})"
        )

    return None


def _comparar_imagen_comprimida(
    ruta_a: Path,
    ruta_b: Path,
    tolerancia_max: int = 8,
    tolerancia_rmse: float = 2.0,
):
    """Compara un JPEG/PNG por píxeles decodificados (con tolerancia de
    recompresión) + EXIF normalizado.
    """
    motivos = []

    motivo_pixeles = _comparar_pixeles_con_tolerancia(
        ruta_a, ruta_b, tolerancia_max, tolerancia_rmse
    )
    if motivo_pixeles is not None:
        motivos.append(motivo_pixeles)

    exif_a, error_a = _exif_normalizado(ruta_a)
    exif_b, error_b = _exif_normalizado(ruta_b)
    if error_a or error_b:
        if error_a != error_b or (error_a is None) != (error_b is None):
            motivos.append(
                f"exif ilegible en un lado (a: {error_a!r}, b: {error_b!r})"
            )
    elif exif_a != exif_b:
        motivos.append("exif normalizado distinto")

    return motivos or None


def comparar_fichero(
    ruta_relativa: str,
    dir_a: Path,
    dir_b: Path,
    *,
    tolerancia_max: int = 8,
    tolerancia_rmse: float = 2.0,
):
    """Compara un único fichero presente en ambos árboles. Devuelve `None` si
    son equivalentes, o un `dict` describiendo la discrepancia.
    """
    ruta_a = dir_a / ruta_relativa
    ruta_b = dir_b / ruta_relativa
    extension = Path(ruta_relativa).suffix.lower()

    try:
        if extension in EXTENSIONES_TIFF:
            if _sha256_fichero(ruta_a) != _sha256_fichero(ruta_b):
                return {"ruta": ruta_relativa, "motivo": "byte a byte distinto"}
            return None

        if extension in EXTENSIONES_IMAGEN_COMPRIMIDA:
            motivos = _comparar_imagen_comprimida(
                ruta_a,
                ruta_b,
                tolerancia_max=tolerancia_max,
                tolerancia_rmse=tolerancia_rmse,
            )
            if motivos:
                return {"ruta": ruta_relativa, "motivo": "; ".join(motivos)}
            return None

        if extension in EXTENSIONES_CSV:
            resultado_csv = _comparar_csv(ruta_a, ruta_b)
            if resultado_csv is not None:
                return {
                    "ruta": ruta_relativa,
                    "motivo": (
                        f"csv distinto en fila {resultado_csv['fila']}: "
                        f"a={resultado_csv['contenido_a']!r} "
                        f"b={resultado_csv['contenido_b']!r}"
                    ),
                    "fila": resultado_csv["fila"],
                    "contenido_a": resultado_csv["contenido_a"],
                    "contenido_b": resultado_csv["contenido_b"],
                }
            return None

        # Cualquier otra extensión: byte a byte, por defecto conservador.
        if _sha256_fichero(ruta_a) != _sha256_fichero(ruta_b):
            return {"ruta": ruta_relativa, "motivo": "byte a byte distinto"}
        return None
    except Exception as exc:  # noqa: BLE001 - un fichero roto es una discrepancia, no un crash
        return {"ruta": ruta_relativa, "motivo": f"error al comparar: {exc}"}


def comparar_arboles(
    dir_a: Path,
    dir_b: Path,
    *,
    tolerancia_max: int = 8,
    tolerancia_rmse: float = 2.0,
) -> dict:
    """Compara los árboles completos y devuelve el informe determinista
    (todas las listas ordenadas).
    """
    arbol_a = listar_arbol(dir_a)
    arbol_b = listar_arbol(dir_b)

    solo_en_a = sorted(arbol_a - arbol_b)
    solo_en_b = sorted(arbol_b - arbol_a)
    comunes = sorted(arbol_a & arbol_b)

    discrepancias_contenido = []
    with ThreadPoolExecutor() as executor:
        resultados = executor.map(
            lambda ruta: comparar_fichero(
                ruta,
                dir_a,
                dir_b,
                tolerancia_max=tolerancia_max,
                tolerancia_rmse=tolerancia_rmse,
            ),
            comunes,
        )
        for resultado in resultados:
            if resultado is not None:
                discrepancias_contenido.append(resultado)

    discrepancias_contenido.sort(key=lambda item: item["ruta"])

    equivalentes = not solo_en_a and not solo_en_b and not discrepancias_contenido

    return {
        "equivalentes": equivalentes,
        "solo_en_a": solo_en_a,
        "solo_en_b": solo_en_b,
        "discrepancias_contenido": discrepancias_contenido,
        "total_ficheros_comunes": len(comunes),
    }


def _formatear_lista(titulo: str, elementos: list) -> list:
    lineas = [f"{titulo} ({len(elementos)}):"]
    for elemento in elementos[:MAX_DISCREPANCIAS_POR_TIPO]:
        lineas.append(f"  - {elemento}")
    restantes = len(elementos) - MAX_DISCREPANCIAS_POR_TIPO
    if restantes > 0:
        lineas.append(f"  ... y {restantes} más")
    return lineas


def imprimir_resumen(informe: dict) -> None:
    """Imprime por stdout un resumen legible en español del informe."""
    if informe["equivalentes"]:
        print("Los dos árboles son equivalentes.")
        print(f"Ficheros comunes comparados: {informe['total_ficheros_comunes']}")
        return

    print("Se han encontrado discrepancias entre los dos árboles.")
    print()

    if informe["solo_en_a"]:
        for linea in _formatear_lista(
            "Ficheros solo en A", informe["solo_en_a"]
        ):
            print(linea)
        print()

    if informe["solo_en_b"]:
        for linea in _formatear_lista(
            "Ficheros solo en B", informe["solo_en_b"]
        ):
            print(linea)
        print()

    if informe["discrepancias_contenido"]:
        descripciones = [
            f"{item['ruta']}: {item['motivo']}"
            for item in informe["discrepancias_contenido"]
        ]
        for linea in _formatear_lista(
            "Ficheros con contenido distinto", descripciones
        ):
            print(linea)
        print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compara dos árboles de salida del organizado de ATOM Organizer "
            "(motor viejo vs. motor nuevo) y reporta cualquier discrepancia."
        )
    )
    parser.add_argument("dir_a", type=Path, help="Directorio del organizado A")
    parser.add_argument("dir_b", type=Path, help="Directorio del organizado B")
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Ruta donde escribir el informe completo en JSON",
    )
    parser.add_argument(
        "--tolerancia-pixel",
        type=int,
        default=8,
        help=(
            "Diferencia máxima de valor de píxel admitida entre JPEG/PNG "
            "recomprimidos (por defecto 8)"
        ),
    )
    parser.add_argument(
        "--tolerancia-rmse",
        type=float,
        default=2.0,
        help=(
            "RMSE máximo admitido entre JPEG/PNG recomprimidos "
            "(por defecto 2.0)"
        ),
    )
    parser.add_argument(
        "--estricto",
        action="store_true",
        help=(
            "Exige píxeles idénticos en JPEG/PNG (tolerancias a 0), "
            "ignorando --tolerancia-pixel/--tolerancia-rmse"
        ),
    )
    args = parser.parse_args(argv)

    tolerancia_max = 0 if args.estricto else args.tolerancia_pixel
    tolerancia_rmse = 0 if args.estricto else args.tolerancia_rmse

    informe = comparar_arboles(
        args.dir_a,
        args.dir_b,
        tolerancia_max=tolerancia_max,
        tolerancia_rmse=tolerancia_rmse,
    )
    imprimir_resumen(informe)

    if args.json is not None:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)

    return 0 if informe["equivalentes"] else 1


if __name__ == "__main__":
    sys.exit(main())
