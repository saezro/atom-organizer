"""Catálogo de inspecciones: de dónde salen y cómo se convierten en un prefijo.

El destino dentro del bucket ya no se deriva del nombre de la carpeta local
(dos «Nueva carpeta» de vuelos distintos colisionaban, y la misma inspección
repartida en dos carpetas quedaba partida en dos prefijos). Ahora lo elige el
operador de una lista, y esa elección es lo que nombra el prefijo.

**De dónde sale la lista.** La fuente de verdad es la BD de Aerotools
(`aerotools-db` → `public.inspecciones_pv`, con join a `plantas_pv`/`empresas`
porque la tabla no tiene columna de nombre). Pero el organizer es un `.exe`
público que corre en el PC del operador: meter ahí las credenciales de una BD
de PRODUCCIÓN sería regalarlas a cualquiera que abra el binario con un editor
hexadecimal — el mismo razonamiento que ya llevó a no meter una service
account key en `cloud_upload`. Así que la app **no habla con la BD**: se lo
pregunta a ATOM Suite, que sí la tiene delante.

Desde 3.4.20 el catálogo se pide a `GET /api/organizer/inspecciones` (backend
Atom-suite), autenticándose con el `id_token` de la misma sesión de Google que
ya usa la subida — ningún secreto nuevo en el binario, y el backend aplica el
filtro de visibilidad por rol, así que un externo no ve el catálogo entero.

Se conservan dos respaldos por debajo, en este orden:

1. `_inspecciones.json` en el bucket — el mecanismo anterior. Sigue vivo como
   red de seguridad para el día que la Suite esté caída y el operador tenga que
   subir igual. Lo genera `tools/exportar_inspecciones.py`, fuera de la app.
2. La caché local del último catálogo bajado, para trabajar sin red.

Un fallo de la API no es «no hay inspecciones»: se cae al siguiente escalón y
se dice en `origen` de dónde salió la lista.

**El prefijo se monta y se desmonta.** Regla de Cas (2026-08-06): «con el
nombre se debería poder sacar igual que montamos el nombre lo podemos
desmontar». El prefijo son cuatro campos en orden fijo separados por `--`::

    ANTOLIN--LOS_MANGOS--2026--T_Modulos
    <empresa>--<planta>---<año>--<tipo>

`--` es separador seguro porque `_campo()` deja sólo `[A-Za-z0-9_]`: ningún
campo puede contener un guion, así que dos guiones seguidos sólo aparecen
entre campos. Un campo que falte se escribe `_`, para que el número de piezas
sea siempre cuatro y `parse_prefijo()` no tenga que adivinar.
"""
from __future__ import annotations

import json
import os
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path

__all__ = [
    "Inspeccion",
    "OBJETO_CATALOGO",
    "API_BASE",
    "RUTA_CATALOGO",
    "SEPARADOR",
    "prefijo_de_inspeccion",
    "parse_prefijo",
    "descargar_catalogo_api",
    "descargar_catalogo",
    "leer_cache",
    "guardar_cache",
    "cargar_catalogo",
]

# Objeto del bucket con el catálogo. Empieza por `_` a propósito: así no se
# confunde con un prefijo de inspección al listar el bucket a ojo, y el Cloud
# Run que organiza puede saltárselo con una regla trivial.
OBJETO_CATALOGO = "_inspecciones.json"

# Backend de ATOM Suite. La env existe para poder apuntar a dev
# (`https://saez.dev.suite.atom-uas.com`) y validar el endpoint sin recompilar
# el `.exe`; en el PC del operador nunca está definida y vale el default.
API_BASE = os.environ.get("ATOM_SUITE_API_BASE") or "https://suite.atom-uas.com"
RUTA_CATALOGO = "/api/organizer/inspecciones"

SEPARADOR = "--"
VACIO = "_"

TIMEOUT = 30
USER_AGENT = "ATOM-Organizer-Uploader"


@dataclass(frozen=True)
class Inspeccion:
    """Una inspección tal y como la ve el operador.

    `id` viene de la BD y se conserva para poder cruzar de vuelta, pero **no
    forma parte del prefijo**: el nombre tiene que bastar para reconstruir la
    inspección (decisión de Cas), y un id no se lee.
    """

    empresa: str = ""
    planta: str = ""
    anio: str = ""
    tipo: str = ""
    id: int | None = None
    fase: str = ""

    @property
    def prefijo(self) -> str:
        return prefijo_de_inspeccion(self)

    @property
    def etiqueta(self) -> str:
        """Cómo se enseña en el desplegable."""
        partes = [p for p in (self.empresa, self.planta, self.anio, self.tipo) if p]
        base = " · ".join(partes) or f"inspección {self.id}"
        return f"{base} ({self.fase})" if self.fase else base

    def to_dict(self) -> dict:
        """Lo que consume la UI. Incluye `prefijo` y `etiqueta` ya calculados:
        el desplegable no tiene por qué saber cómo se monta un prefijo, y así
        sólo hay una implementación de la regla (aquí)."""
        d = asdict(self)
        d["prefijo"] = self.prefijo
        d["etiqueta"] = self.etiqueta
        return d


def _campo(valor) -> str:
    """Un campo del prefijo: ASCII, sin espacios y sin guiones.

    Los nombres reales traen eñes y espacios (`MARISOLES_LOS MANGOS`, `OCAÑA`).
    Se quitan los guiones además de lo que ya quitaba
    `cloud_config.prefijo_desde_carpeta`, porque `-` es el separador entre
    campos y dejarlo dentro haría el prefijo ambiguo al desmontarlo.
    """
    base = unicodedata.normalize("NFKD", str(valor if valor is not None else "").strip())
    base = base.encode("ascii", "ignore").decode("ascii")
    limpio = []
    for ch in base:
        if ch.isalnum() or ch == "_":
            limpio.append(ch)
        elif ch in " ./\\-":
            limpio.append("_")
    out = "".join(limpio).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    return out


def prefijo_de_inspeccion(insp: Inspeccion) -> str:
    """Inspección → prefijo del bucket. Cadena vacía si no queda nada usable."""
    campos = [_campo(insp.empresa), _campo(insp.planta),
              _campo(insp.anio), _campo(insp.tipo)]
    if not any(campos):
        return ""
    return SEPARADOR.join(c or VACIO for c in campos)


def parse_prefijo(prefijo: str) -> Inspeccion | None:
    """Prefijo → inspección. `None` si no lo montó `prefijo_de_inspeccion`.

    Devolver `None` no es un error: el operador puede haber tecleado una
    inspección nueva a mano, y los prefijos anteriores a este cambio (`ANTOLIN`,
    ya en el bucket con 2518 objetos) tampoco tienen esta forma. Quien llama
    decide qué hacer con eso.
    """
    partes = (prefijo or "").strip("/").split(SEPARADOR)
    if len(partes) != 4:
        return None
    empresa, planta, anio, tipo = (("" if p == VACIO else p) for p in partes)
    return Inspeccion(empresa=empresa, planta=planta, anio=anio, tipo=tipo)


# --------------------------------------------------------------------------
# Catálogo: bucket → caché local
# --------------------------------------------------------------------------

def _desde_json(data: dict) -> list[Inspeccion]:
    out: list[Inspeccion] = []
    for fila in (data or {}).get("inspecciones") or []:
        if not isinstance(fila, dict):
            continue
        ins = Inspeccion(
            empresa=str(fila.get("empresa") or ""),
            planta=str(fila.get("planta") or ""),
            anio=str(fila.get("anio") or fila.get("a_o") or ""),
            tipo=str(fila.get("tipo") or ""),
            id=fila.get("id") if isinstance(fila.get("id"), int) else None,
            fase=str(fila.get("fase") or ""),
        )
        if ins.prefijo:
            out.append(ins)
    # Las más recientes primero: es lo que el operador va a subir.
    out.sort(key=lambda i: (i.anio, i.empresa, i.planta), reverse=True)
    return out


def descargar_catalogo_api(auth, *,
                           base: str | None = None,
                           timeout: int = TIMEOUT) -> list[Inspeccion]:
    """Pide el catálogo vivo a ATOM Suite con el `id_token` del operador.

    Es la fuente preferente desde 3.4.20: el `_inspecciones.json` del bucket se
    generaba a mano y envejecía en silencio mientras las inspecciones se crean
    a diario.

    El backend devuelve **campos crudos** (sin `prefijo`): el slug lo sigue
    montando `prefijo_de_inspeccion`, aquí, para que haya una sola
    implementación de la regla y no dos que puedan divergir.

    Propaga si falla, igual que `descargar_catalogo`: quien llama decide si baja
    al siguiente escalón. Un 403 aquí es informativo — significa que la cuenta
    no está dada de alta en la Suite, no que no haya inspecciones.
    """
    url = f"{(base or API_BASE).rstrip('/')}{RUTA_CATALOGO}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {auth.id_token()}")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return _desde_json(data)


def descargar_catalogo(bucket: str, auth, *,
                       base: str = "https://storage.googleapis.com",
                       timeout: int = TIMEOUT) -> list[Inspeccion]:
    """Baja `_inspecciones.json` del bucket con la sesión del operador.

    Propaga si falla (red, permisos, objeto ausente): quien llama decide si
    tira de caché. No se devuelve lista vacía ante un fallo, porque «no hay
    inspecciones» y «no he podido mirar» llevan a decisiones distintas.
    """
    url = (f"{base.rstrip('/')}/storage/v1/b/{urllib.parse.quote(bucket)}"
           f"/o/{urllib.parse.quote(OBJETO_CATALOGO, safe='')}?alt=media")
    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {auth.access_token()}")
    req.add_header("User-Agent", USER_AGENT)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return _desde_json(data)


def _ruta_cache() -> Path:
    from atom_core.google_auth import user_data_dir

    return user_data_dir() / "inspecciones_cache.json"


def leer_cache() -> tuple[list[Inspeccion], float]:
    """Último catálogo bajado y cuándo (epoch). `([], 0)` si no hay."""
    try:
        data = json.loads(_ruta_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], 0.0
    return _desde_json(data), float(data.get("bajado_en") or 0)


def guardar_cache(inspecciones: list[Inspeccion]) -> None:
    """Guarda el catálogo para poder trabajar con la lista aunque caiga la red.

    Escritura atómica: un corte a mitad dejaría un JSON truncado que luego no
    parsea, y el operador se quedaría sin lista sin entender por qué.
    """
    ruta = _ruta_cache()
    tmp = ruta.with_suffix(".tmp")
    payload = {"bajado_en": time.time(),
               "inspecciones": [i.to_dict() for i in inspecciones]}
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(ruta)
    except OSError:
        pass  # la caché es una comodidad, no puede tumbar la subida


def cargar_catalogo(bucket: str, auth) -> dict:
    """Catálogo para la UI, con tres escalones: API → bucket → caché.

    Devuelve `{"ok", "inspecciones": [...], "origen": "api"|"bucket"|"cache",
    "error": str|None}`. Nunca lanza: la pantalla de subida tiene que poder
    dibujarse aunque el catálogo falle, y el operador siempre puede teclear
    una inspección a mano.

    El orden importa: la API es la única fuente que está al día. El bucket es
    la red de seguridad para cuando la Suite no responde, y la caché para
    cuando no hay red en absoluto. Se cachea lo que venga de los dos primeros,
    para que el escalón de abajo siga sirviendo mañana.

    Cuando se llega a la caché, `error` lleva **los dos** fallos encadenados: si
    solo se contase el último, un 403 de la API quedaría tapado por un «objeto
    no encontrado» del bucket y nadie sabría que el operador no está dado de
    alta en la Suite.
    """
    errores: list[str] = []
    for origen, bajar in (
        ("api", lambda: descargar_catalogo_api(auth)),
        ("bucket", lambda: descargar_catalogo(bucket, auth)),
    ):
        try:
            inspecciones = bajar()
        except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
            errores.append(f"{origen}: {exc}")
            continue
        guardar_cache(inspecciones)
        return {"ok": True, "inspecciones": [i.to_dict() for i in inspecciones],
                "origen": origen, "bajado_en": time.time(), "error": None}

    cacheadas, cuando = leer_cache()
    return {"ok": bool(cacheadas), "inspecciones": [i.to_dict() for i in cacheadas],
            "origen": "cache", "bajado_en": cuando, "error": " · ".join(errores)}
