"""Compara dos árboles -local o `gs://…`- fichero a fichero por sha256.

SOLO LECTURA, sin excepción: solo llama a `Almacen.listar` (equivalente a
`os.walk`/`list_blobs`) y a `abrir_para_lectura` (que en `gs://…` descarga a un
temporal y lo borra al salir del `with`, nunca toca el origen). No importa ni
usa `publicar`, `mover` ni `borrar` de `atom_core.almacen`: es imposible que
este script escriba o borre nada, ni en disco ni en el bucket.

Pensado para verificar la paridad de una migración/backfill contra el bucket
real `gs://datos_para_organizar` sin arriesgar su contenido.

Uso:
    python scripts/verificar_paridad_bucket.py <ruta_a> <ruta_b>

`ruta_a` / `ruta_b` son cada una una carpeta local o una URI `gs://bucket/prefijo`
(se pueden mezclar: una local y otra `gs://…`). Sale con código 0 si son
idénticos (mismas rutas relativas, mismo sha256 cada una) y 1 si difieren,
imprimiendo el detalle. No se ejecuta nada de esto contra el bucket real desde
aquí: es cosa de quien lo invoque.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from atom_core.almacen import abrir_almacen, abrir_para_lectura, unir  # noqa: E402

_TAMANO_BLOQUE = 1024 * 1024


def _sha256_de(ruta: str) -> str:
    """sha256 del contenido de `ruta` (local o `gs://…`). SOLO LECTURA: usa
    `abrir_para_lectura`, que en `gs://…` descarga a un temporal de solo
    lectura y lo borra al salir; el origen no se toca en ningún caso."""
    digest = hashlib.sha256()
    with abrir_para_lectura(ruta) as ruta_local:
        with open(ruta_local, "rb") as fh:
            for bloque in iter(lambda: fh.read(_TAMANO_BLOQUE), b""):
                digest.update(bloque)
    return digest.hexdigest()


def _listar_recursivo(raiz: str) -> list[str]:
    """Rutas relativas (recursivo) de todo lo que cuelga de `raiz`, sea local
    o `gs://…`. SOLO LECTURA: `Almacen.listar` es un `os.walk`/`list_blobs` de
    solo lectura en ambos backends.

    `Almacen.listar(prefijo)` no devuelve lo mismo en los dos backends: en
    `AlmacenLocal` ya sale relativo a `raiz` (que es toda la raíz del
    `Almacen`); en `AlmacenGCS` sale relativo al BUCKET entero (el prefijo se
    pasa aparte y no se descuenta solo, ver `atom_core/almacen.py:listar_ficheros`
    para el mismo patrón). Se descuenta aquí el `prefijo` a mano para que el
    resultado sea comparable entre los dos backends sin más.
    """
    almacen, prefijo = abrir_almacen(raiz)
    encontradas = []
    for relativo in almacen.listar(prefijo):
        if prefijo:
            if relativo != prefijo and not relativo.startswith(prefijo + "/"):
                continue
            resto = relativo[len(prefijo):].strip("/")
        else:
            resto = relativo
        if resto:
            encontradas.append(resto)
    return sorted(encontradas)


def comparar(raiz_a: str, raiz_b: str) -> tuple[bool, list[str]]:
    """Compara `raiz_a` contra `raiz_b`. SOLO LECTURA. Devuelve
    `(iguales, detalle)`: `detalle` son líneas listas para imprimir con cada
    discrepancia (solo en A, solo en B, o mismo nombre con distinto sha256)."""
    rutas_a = set(_listar_recursivo(raiz_a))
    rutas_b = set(_listar_recursivo(raiz_b))

    detalle: list[str] = []
    for relativo in sorted(rutas_a - rutas_b):
        detalle.append(f"SOLO EN A: {relativo}")
    for relativo in sorted(rutas_b - rutas_a):
        detalle.append(f"SOLO EN B: {relativo}")

    for relativo in sorted(rutas_a & rutas_b):
        sha_a = _sha256_de(unir(raiz_a, relativo))
        sha_b = _sha256_de(unir(raiz_b, relativo))
        if sha_a != sha_b:
            detalle.append(
                f"DISTINTO CONTENIDO: {relativo} (A={sha_a[:12]}… B={sha_b[:12]}…)"
            )

    return not detalle, detalle


def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("Uso: python scripts/verificar_paridad_bucket.py <ruta_a> <ruta_b>",
              file=sys.stderr)
        print("     <ruta_a>/<ruta_b>: carpeta local o gs://bucket/prefijo. "
              "Script SOLO LECTURA.", file=sys.stderr)
        return 2

    raiz_a, raiz_b = argv
    print(f"[paridad] comparando (solo lectura):\n  A: {raiz_a}\n  B: {raiz_b}")
    iguales, detalle = comparar(raiz_a, raiz_b)
    if iguales:
        print("[paridad] OK: mismas rutas relativas, mismo sha256 en ambos lados.")
        return 0

    print(f"[paridad] DIFERENCIAS ({len(detalle)}):")
    for linea in detalle:
        print(f"  {linea}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
