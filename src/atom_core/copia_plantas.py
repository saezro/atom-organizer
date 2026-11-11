"""Fase `copiar`: pasa una carpeta SALIDA organizada a la estructura de plantas.

    <bucket>/SALIDA--<empresa>--<planta>--<año>--<tipo>/<rel>
        -> <raiz_plantas>/<PLANTA>/INSPECCIONES/<TIPO>/<AÑO>/<rel>

(también con el prefijo de dev `SALIDA-PRUEBA-`). Reglas, sin excepción:

- NO-CLOBBER: un destino que existe NUNCA se sobrescribe. Mismo md5 ->
  `identicos`; md5 distinto (o no comparable) -> `choques`, visible en el informe.
- NUNCA borra nada (el backend ni siquiera expone `borrar`).
- La copia es server-side (`copy_blob` con `if_generation_match=0`): sin
  descargar, y atómicamente no-clobber aunque alguien cree el destino en medio.
- Secuencial, objeto a objeto.
- Un nombre de SALIDA que no encaja con el mapeo es un ERROR, nunca se adivina.

El acceso al bucket va por un `backend` con 4 métodos (`listar`, `meta`,
`copiar`, `escribir_texto`) para poder probarlo con un almacén en memoria.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from atom_core.almacen import abrir_almacen

PREFIJOS_SALIDA = ("SALIDA-PRUEBA-", "SALIDA--")  # el más específico primero
MOTIVO_DISTINTO = "contenido-distinto"
MOTIVO_SIN_MD5 = "md5-no-disponible"
MUESTRA_MAX = 20


@dataclass(frozen=True)
class CopiaItem:
    origen: str
    destino: str
    size: int
    md5: str  # base64 de GCS; "" si el origen no lo trae (objeto compuesto)


@dataclass
class InformeCopia:
    copiados: int = 0
    identicos: int = 0
    choques: list = field(default_factory=list)  # [{ruta, motivo}]
    bytes: int = 0
    errores: list = field(default_factory=list)  # [{ruta, motivo}] fallos reales
    solo_plan: bool = False
    muestra: list = field(default_factory=list)  # [{origen, destino}] primeros items
    excluidos: int = 0  # objetos de la SALIDA que NO se copian (.organizado/, marcadores)
    muestra_excluidos: list = field(default_factory=list)  # primeras rutas excluidas

    def a_dict(self) -> dict:
        return asdict(self)


def _partir_uri(uri: str) -> tuple[str, str]:
    from atom_core.almacen import es_uri_gcs
    if not es_uri_gcs(uri):
        raise ValueError(f"se esperaba una URI gs://, no {uri!r}")
    resto = uri[len("gs://"):].strip("/")
    bucket, _, clave = resto.partition("/")
    if not bucket:
        raise ValueError(f"URI sin bucket: {uri!r}")
    return bucket, clave


def _mapeo(destino_salida: str, raiz_plantas: str) -> tuple[str, str]:
    """`(prefijo_origen, prefijo_destino)` sin barra final. ValueError si el
    nombre de la SALIDA no encaja."""
    _bucket, clave = _partir_uri(destino_salida)
    if "/" in clave or not clave:
        raise ValueError(f"la SALIDA debe ser gs://<bucket>/<carpeta> (una sola carpeta): {destino_salida!r}")
    for pref in PREFIJOS_SALIDA:
        if clave.startswith(pref):
            resto = clave[len(pref):]
            break
    else:
        raise ValueError(f"la carpeta {clave!r} no empieza por {' ni '.join(PREFIJOS_SALIDA)}")
    partes = resto.split("--")
    if len(partes) != 4 or not all(p.strip() for p in partes):
        raise ValueError(
            f"nombre de SALIDA no válido: {clave!r}; se esperaba <prefijo><empresa>--<planta>--<año>--<tipo>")
    _empresa, planta, anio, tipo = (p.strip() for p in partes)
    for nombre, valor in (("planta", planta), ("anio", anio), ("tipo", tipo)):
        if not valor or ".." in valor or "/" in valor or "\\" in valor:
            raise ValueError(f"{nombre} no válido en el nombre de SALIDA {clave!r}: {valor!r}")
    raiz = raiz_plantas.rstrip("/")
    _partir_uri(raiz)
    salida = destino_salida.rstrip("/")
    if raiz == salida or raiz.startswith(salida + "/"):
        raise ValueError(f"--destino-copia {raiz!r} está dentro de la propia SALIDA {salida!r}")
    return destino_salida.rstrip("/"), f"{raiz}/{planta}/INSPECCIONES/{tipo}/{anio}"


def _es_organizado(rel: str) -> bool:
    return rel.startswith(".organizado/") or "/.organizado/" in rel


def _planificar(destino_salida, raiz_plantas, backend):
    """`(plan, excluidos)`; `excluidos` = URIs que no se copian. ValueError si
    no queda nada copiable."""
    pref_o, pref_d = _mapeo(destino_salida, raiz_plantas)
    plan, excluidos = [], []
    for uri, size, md5 in backend.listar(pref_o):
        rel = uri[len(pref_o):].lstrip("/")
        if not rel or rel.endswith("/") or _es_organizado(rel):  # marcador de carpeta / metadatos
            excluidos.append(uri)
            continue
        plan.append(CopiaItem(uri, f"{pref_d}/{rel}", int(size), md5 or ""))
    if not plan:
        raise ValueError(f"nada que copiar en {pref_o!r}: 0 objetos copiables "
                         f"({len(excluidos)} excluidos); ¿SALIDA vacía o mal indicada?")
    return plan, excluidos


def planificar_copia(destino_salida: str, raiz_plantas: str, *, backend=None) -> list[CopiaItem]:
    return _planificar(destino_salida, raiz_plantas, backend or _backend_gcs())[0]


def ejecutar_copia(plan: list[CopiaItem], *, solo_plan: bool, backend=None) -> InformeCopia:
    backend = backend or _backend_gcs()
    inf = InformeCopia(solo_plan=solo_plan)
    inf.muestra = [{"origen": i.origen, "destino": i.destino} for i in plan[:MUESTRA_MAX]]

    def clasificar_existente(item, meta_dest):
        _size, md5_d = meta_dest
        if not item.md5 or not md5_d:
            inf.choques.append({"ruta": item.destino, "motivo": MOTIVO_SIN_MD5})
        elif item.md5 == md5_d:
            inf.identicos += 1
        else:
            inf.choques.append({"ruta": item.destino, "motivo": MOTIVO_DISTINTO})

    for item in plan:
        try:
            meta_dest = backend.meta(item.destino)
            if meta_dest is not None:
                clasificar_existente(item, meta_dest)
                continue
            if solo_plan:
                inf.copiados += 1
                inf.bytes += item.size
                continue
            try:
                backend.copiar(item.origen, item.destino)
            except FileExistsError:  # alguien lo creó entre la comprobación y la copia
                meta_dest = backend.meta(item.destino)
                if meta_dest is None:
                    raise
                clasificar_existente(item, meta_dest)
                continue
            inf.copiados += 1
            inf.bytes += item.size
        except Exception as exc:  # noqa: BLE001 - se registra visible y se sigue con el resto
            inf.errores.append({"ruta": item.destino, "motivo": f"{type(exc).__name__}: {exc}"})
    return inf


def ruta_informe(destino_salida: str, ejecucion: str) -> str:
    return f"{destino_salida.rstrip('/')}/.organizado/copias/{ejecucion}.json"


def ejecutar_fase(destino_salida: str, destino_copia: str, *, solo_plan: bool, ejecucion: str,
                  backend=None) -> InformeCopia:
    """Planifica, ejecuta y deja el informe en `<salida>/.organizado/copias/<ejecucion>.json`.
    El informe se escribe SIEMPRE (también con errores) antes de devolver."""
    import json
    backend = backend or _backend_gcs()
    plan, excluidos = _planificar(destino_salida, destino_copia, backend)
    inf = ejecutar_copia(plan, solo_plan=solo_plan, backend=backend)
    inf.excluidos = len(excluidos)
    inf.muestra_excluidos = excluidos[:MUESTRA_MAX]
    backend.escribir_texto(ruta_informe(destino_salida, ejecucion),
                           json.dumps(inf.a_dict(), ensure_ascii=False, indent=2))
    return inf


class _BackendGCS:
    """Backend real sobre `AlmacenGCS` (un cliente por bucket, vía `abrir_almacen`)."""

    @staticmethod
    def _bucket_clave(uri):
        almacen, prefijo = abrir_almacen(uri)
        return almacen.bucket, almacen._clave(prefijo)

    def listar(self, prefijo):
        bucket, clave = self._bucket_clave(prefijo)
        out = []
        for blob in bucket.client.list_blobs(bucket, prefix=clave + "/"):
            out.append((f"gs://{bucket.name}/{blob.name}", blob.size, blob.md5_hash))
        return out

    def meta(self, uri):
        bucket, clave = self._bucket_clave(uri)
        blob = bucket.get_blob(clave)
        if blob is None:
            return None
        return blob.size, blob.md5_hash

    def copiar(self, origen, destino):
        b_o, k_o = self._bucket_clave(origen)
        b_d, k_d = self._bucket_clave(destino)
        origen_blob, destino_blob = b_o.blob(k_o), b_d.blob(k_d)
        try:
            # rewrite (no copy_blob): troceado, vale entre buckets/clases de
            # almacenamiento y objetos grandes; hay que repetir hasta token None.
            token = None
            while True:
                token, _hechos, _total = destino_blob.rewrite(
                    origen_blob, token=token, if_generation_match=0)
                if token is None:
                    break
        except Exception as exc:
            if getattr(exc, "code", None) == 412:
                raise FileExistsError(destino) from exc
            raise

    def escribir_texto(self, uri, texto):
        bucket, clave = self._bucket_clave(uri)
        bucket.blob(clave).upload_from_string(texto, content_type="application/json")


def _backend_gcs():
    return _BackendGCS()
