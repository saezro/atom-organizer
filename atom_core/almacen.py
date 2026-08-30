"""Capa de almacenamiento intercambiable, con `pathlib`/`shutil` puros.

Motivo: hoy el pipeline hace todo su I/O contra un bucket GCS montado por
gcsfuse en `/gcs`, tratándolo como un filesystem POSIX cualquiera. Eso funciona,
pero acopla cada fase al mount: para poder sustituirlo algún día por acceso
directo a la API de GCS (sin gcsfuse de por medio) hace falta una frontera
explícita entre «qué operaciones necesita el pipeline» y «cómo se cumplen».

Esta es esa frontera. `Almacen` es el contrato; `AlmacenLocal` es la única
implementación de esta fase, y replica EXACTAMENTE la semántica que ya tiene el
pipeline contra el mount (directorios padre creados solos al publicar/mover,
`mover` sobrescribe el destino). El backend GCS por API — que sí necesitará
`google-cloud-storage` — llega en una fase posterior; este módulo se queda solo
con la stdlib a propósito para poder usarse en cualquier imagen sin arrastrar
ese SDK.

Las rutas de la API pública son siempre RELATIVAS al raíz del almacén y usan
`/` como separador (son claves, no rutas de disco); `AlmacenLocal` es quien las
traduce a rutas del sistema de ficheros.
"""
from __future__ import annotations

import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class Almacen(Protocol):
    """Operaciones de almacenamiento que necesita el pipeline, sin más.

    Cualquier backend (local, GCS por API, lo que sea) implementa esto y el
    resto del pipeline deja de saber si hay un mount, un bucket o un disco
    debajo.
    """

    def listar(self, prefijo: str) -> list[str]:
        """Rutas relativas al raíz, recursivo, de todo lo que cuelga de
        `prefijo`. `prefijo=""` lista el almacén entero."""
        ...

    def existe(self, ruta: str) -> bool:
        """True si `ruta` existe en el almacén."""
        ...

    @contextmanager
    def abrir_local(self, ruta: str):
        """Context manager que cede una `pathlib.Path` a un fichero LOCAL
        legible con el contenido de `ruta`. En un backend que ya es local
        (`AlmacenLocal`) no copia nada: cede la ruta real, coste cero. Un
        backend remoto sí podría necesitar descargar a un temporal aquí."""
        ...

    def publicar(self, ruta_local: Path, destino: str) -> None:
        """Deja el fichero local `ruta_local` en el almacén, en `destino`."""
        ...

    def mover(self, origen: str, destino: str) -> None:
        """Mueve `origen` a `destino` DENTRO del almacén, sin pasar por un
        cliente/descarga intermedia."""
        ...

    def borrar(self, ruta: str) -> None:
        """Borra `ruta` del almacén."""
        ...

    def tamano(self, ruta: str) -> int:
        """Tamaño en bytes de `ruta`, leído por METADATOS: en un backend
        remoto (GCS) no debe descargar el objeto para saber cuánto ocupa."""
        ...


def _normalizar(ruta: str) -> str:
    """Quita separadores sobrantes y normaliza a `/`, para que las claves que
    entran y las que salen de `listar` sean comparables tal cual."""
    return str(ruta).replace("\\", "/").strip("/")


class AlmacenLocal:
    """`Almacen` sobre un directorio real del disco, vía `pathlib`/`shutil`.

    Es el backend de esta fase: el pipeline sigue viendo el mismo filesystem
    de siempre (el mount de gcsfuse hoy, un disco cualquiera en tests), solo
    que a través del contrato `Almacen` en vez de llamadas sueltas a `os`/
    `shutil` repartidas por el código.
    """

    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)

    def _ruta(self, relativo: str) -> Path:
        relativo = _normalizar(relativo)
        if not relativo:
            return self.raiz
        return self.raiz.joinpath(*relativo.split("/"))

    def listar(self, prefijo: str) -> list[str]:
        base = self._ruta(prefijo)
        if not base.exists():
            return []
        if base.is_file():
            return [_normalizar(prefijo)]
        encontradas = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for nombre in filenames:
                ruta_abs = Path(dirpath) / nombre
                relativo = ruta_abs.relative_to(self.raiz).as_posix()
                encontradas.append(relativo)
        return sorted(encontradas)

    def existe(self, ruta: str) -> bool:
        return self._ruta(ruta).exists()

    @contextmanager
    def abrir_local(self, ruta: str):
        # Ya es local: se cede la ruta real, sin copiar nada.
        yield self._ruta(ruta)

    def publicar(self, ruta_local: Path, destino: str) -> None:
        ruta_destino = self._ruta(destino)
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ruta_local, ruta_destino)

    def mover(self, origen: str, destino: str) -> None:
        ruta_origen = self._ruta(origen)
        ruta_destino = self._ruta(destino)
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)
        os.replace(ruta_origen, ruta_destino)

    def borrar(self, ruta: str) -> None:
        self._ruta(ruta).unlink()

    def tamano(self, ruta: str) -> int:
        return self._ruta(ruta).stat().st_size


# --- Capa de rutas URI-aware ------------------------------------------------
#
# El pipeline maneja `input_folder`/`output_folder` como strings sueltos y los
# combina con `os.path.join`/`os.walk`/`os.listdir`/`shutil.copy2` a lo largo
# de miles de líneas. Reescribir todo eso a `Almacen.listar`/`publicar`/etc de
# golpe es demasiado cambio para una sola fase. Estas funciones son la rampa:
# aceptan la MISMA string de siempre (una ruta de disco) o una URI `gs://…`, y
# por debajo despachan al `Almacen` que corresponda. Así el pipeline puede irse
# migrando llamada a llamada sin que cambie su forma de pasar rutas.

_ESQUEMA_GCS = "gs://"

# Un `Almacen` por raíz (bucket+prefijo o carpeta local), cacheado a nivel de
# módulo. Es necesario porque `abrir_almacen` se llama una vez POR IMAGEN, y
# dentro de un `ProcessPoolExecutor` cada proceso hijo debe construir el
# cliente de GCS (y su pool de conexiones) una sola vez, no en cada llamada:
# crearlo por fichero sería carísimo (handshake TLS + auth en cada uno) y es
# innecesario, porque el propio SDK ya es seguro de reutilizar dentro de un
# mismo proceso.
_ALMACENES: dict[str, Almacen] = {}


def _limpiar_cache_almacenes() -> None:
    """Vacía la caché de `abrir_almacen`. Solo para tests: cada proceso real
    (o hijo del pool) vive lo suficiente como para no necesitar limpiarla."""
    _ALMACENES.clear()


def es_uri_gcs(ruta: str) -> bool:
    """True si `ruta` es una URI `gs://…` (esquema insensible a mayúsculas,
    igual que el resto de esquemas de URI)."""
    return str(ruta).lower().startswith(_ESQUEMA_GCS)


def abrir_almacen(ruta: str) -> tuple[Almacen, str]:
    """Traduce una ruta/URI del pipeline a `(almacen, prefijo_relativo)`.

    `prefijo_relativo` es lo que queda de `ruta` una vez separada la raíz del
    almacén (bucket, o carpeta local si `ruta` cae dentro de una ya vista);
    ambos valores están pensados para pasarse tal cual a los métodos de
    `Almacen`. La raíz se cachea por proceso (ver `_ALMACENES` arriba).
    """
    if es_uri_gcs(ruta):
        resto = ruta[len(_ESQUEMA_GCS):].strip("/")
        bucket, _sep, prefijo = resto.partition("/")
        clave_cache = f"{_ESQUEMA_GCS}{bucket}"
        almacen = _ALMACENES.get(clave_cache)
        if almacen is None:
            # Import perezoso: `almacen.py` debe seguir importable sin el SDK
            # de Google (se usa también en el `.exe` de PyInstaller, que no lo
            # incluye). Solo se paga el import al tocar de verdad una ruta gs://.
            from atom_core.almacen_gcs import AlmacenGCS

            almacen = AlmacenGCS(bucket)
            _ALMACENES[clave_cache] = almacen
        return almacen, _normalizar(prefijo)

    raiz = str(ruta)
    almacen = _ALMACENES.get(raiz)
    if almacen is None:
        almacen = AlmacenLocal(Path(raiz))
        _ALMACENES[raiz] = almacen
    return almacen, ""


def unir(ruta: str, *partes: str) -> str:
    """`os.path.join`, pero consciente del esquema: en `gs://` las rutas son
    claves con `/` como separador siempre (incluso en Windows, donde
    `os.path.join` metería `\\`), así que se concatenan a mano."""
    if es_uri_gcs(ruta):
        segmentos = [ruta.rstrip("/")] + [str(p).strip("/") for p in partes if str(p).strip("/")]
        return "/".join(segmentos)
    return os.path.join(ruta, *partes)


@contextmanager
def abrir_para_lectura(ruta: str):
    """Cede una `Path` LOCAL legible con el contenido de `ruta`.

    En local es la propia ruta (coste cero, igual que hoy); en `gs://` delega
    en `Almacen.abrir_local`, que descarga a un temporal y lo limpia al salir
    del bloque `with`."""
    if es_uri_gcs(ruta):
        almacen, prefijo = abrir_almacen(ruta)
        with almacen.abrir_local(prefijo) as ruta_local:
            yield ruta_local
    else:
        yield Path(ruta)


def publicar_en(origen_local: "str | Path", destino: str) -> None:
    """Deja el fichero local `origen_local` en `destino` (ruta o URI).

    En local reutiliza `_reflink_or_copy` de `pipeline.py` (import perezoso:
    `pipeline.py` importa medio mundo y `almacen.py` debe poder cargarse
    suelto, p. ej. en tests) para conservar la copia CoW que ya usa el
    escritorio; si esa función no está disponible cae a `shutil.copy2`, igual
    que hacía el pipeline antes de tener reflink."""
    if es_uri_gcs(destino):
        almacen, prefijo = abrir_almacen(destino)
        almacen.publicar(Path(origen_local), prefijo)
        return

    try:
        from pipeline import _reflink_or_copy

        _reflink_or_copy(str(origen_local), str(destino))
    except ImportError:
        destino_final = destino
        if os.path.isdir(destino_final):
            destino_final = os.path.join(destino_final, os.path.basename(str(origen_local)))
        shutil.copy2(origen_local, destino_final)


def listar_ficheros(ruta: str) -> list[str]:
    """Basenames de los ficheros que cuelgan DIRECTAMENTE de `ruta` (no
    recursivo), en orden alfabético."""
    if es_uri_gcs(ruta):
        almacen, prefijo = abrir_almacen(ruta)
        vistos = set()
        for relativo in almacen.listar(prefijo):
            resto = relativo[len(prefijo):].strip("/") if prefijo else relativo
            if resto and "/" not in resto:
                vistos.add(resto)
        return sorted(vistos)

    return sorted(
        nombre for nombre in os.listdir(ruta)
        if os.path.isfile(os.path.join(ruta, nombre))
    )


def listar_subcarpetas(ruta: str) -> list[str]:
    """Nombres de las subcarpetas DIRECTAS de `ruta` (no recursivo), en orden
    alfabético."""
    if es_uri_gcs(ruta):
        almacen, prefijo = abrir_almacen(ruta)
        vistos = set()
        for relativo in almacen.listar(prefijo):
            resto = relativo[len(prefijo):].strip("/") if prefijo else relativo
            if "/" in resto:
                vistos.add(resto.split("/", 1)[0])
        return sorted(vistos)

    return sorted(
        nombre for nombre in os.listdir(ruta)
        if os.path.isdir(os.path.join(ruta, nombre))
    )


def existe_ruta(ruta: str) -> bool:
    """True si `ruta` (fichero o carpeta, local o `gs://…`) existe."""
    if es_uri_gcs(ruta):
        almacen, prefijo = abrir_almacen(ruta)
        if almacen.existe(prefijo):
            return True
        # Una "carpeta" en GCS no es un objeto propio: existe si hay algo
        # colgando de ese prefijo, igual que un directorio local existe sin
        # necesidad de que haya un fichero con ese nombre exacto.
        return bool(almacen.listar(prefijo))
    return os.path.exists(ruta)


@contextmanager
def editar_en_sitio(ruta: str):
    """Cede una `Path` LOCAL para editarla IN-PLACE con un binario externo
    (exiftool, dji_irp, pyexiv2, PIL...) que exige una ruta de fichero real y
    modifica su contenido sin devolver nada.

    En local es la propia ruta, sin copiar ni republicar nada al salir (coste
    cero, paridad exacta con el binario editando el fichero original de
    siempre); en `gs://` descarga a un temporal (`Almacen.abrir_local`), cede
    esa `Path`, y solo si el bloque `with` termina SIN excepción republica el
    temporal sobre la MISMA clave (`Almacen.publicar`; el upload de GCS es
    atómico, no hace falta clave temporal + swap). Si el bloque lanza, el
    objeto remoto queda intacto y la excepción sube tal cual. En ambos casos
    el temporal se limpia solo (lo hace el `finally` de `AlmacenGCS.abrir_local`).

    En `gs://` el objeto DEBE EXISTIR previamente (se descarga primero); no
    sirve para CREAR un fichero nuevo en el almacén — para eso, `publicar_en`."""
    if es_uri_gcs(ruta):
        almacen, prefijo = abrir_almacen(ruta)
        with almacen.abrir_local(prefijo) as ruta_local:
            yield ruta_local
            if not ruta_local.exists() or ruta_local.stat().st_size == 0:
                raise RuntimeError(
                    f"editar_en_sitio: el temporal de '{ruta}' quedó vacío o "
                    "borrado tras el bloque; no se publica para no dejar un "
                    "objeto truncado en el almacén."
                )
            almacen.publicar(ruta_local, prefijo)
    else:
        yield Path(ruta)


def tamano_de(ruta: str) -> int:
    """Tamaño en bytes de `ruta` (fichero, local o `gs://…`).

    A diferencia de `abrir_para_lectura`, en `gs://…` NO descarga el objeto:
    delega en `Almacen.tamano`, que en `AlmacenGCS` lee el tamaño de los
    metadatos del blob. Para saber cuánto ocupa algo sin querer ya una copia
    local, usar esto en vez de `abrir_para_lectura(...).stat().st_size`."""
    almacen, prefijo = abrir_almacen(ruta)
    return almacen.tamano(prefijo)
