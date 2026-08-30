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
