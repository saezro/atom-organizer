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

**Esa es la única fuente.** Hasta 3.4.22 había dos respaldos por debajo —el
`_inspecciones.json` del bucket y una caché local del último catálogo bajado— y
se han quitado (decisión de Cas, 2026-08-06): la lista que decide **a qué
inspección se sube** no puede venir de una copia vieja sin que nadie se entere.
Ambos respaldos servían datos de fecha desconocida con el mismo aspecto que los
buenos, y elegir un destino que ya no existe —o que ha cambiado de nombre— no
se nota hasta que los ficheros están arriba, en el sitio equivocado.

Un fallo de la API se dice tal cual. El operador siempre puede teclear la
inspección a mano: eso es una decisión suya y consciente, que es justo lo que
no era fiarse de una lista presentada como si estuviera al día.

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
import urllib.request
from dataclasses import dataclass, asdict

__all__ = [
    "Inspeccion",
    "API_BASE",
    "RUTA_CATALOGO",
    "SEPARADOR",
    "prefijo_de_inspeccion",
    "parse_prefijo",
    "descargar_catalogo_api",
    "cargar_catalogo",
]

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
# Catálogo: la API de ATOM Suite y nada más
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


def cargar_catalogo(bucket: str, auth) -> dict:
    """Catálogo para la UI. **Una sola fuente: la API de ATOM Suite.**

    Devuelve `{"ok", "inspecciones": [...], "origen": "api", "error": str|None}`.
    No lanza: la pantalla de subida tiene que poder dibujarse aunque el catálogo
    falle.

    Antes había dos escalones más —el `_inspecciones.json` del bucket y una
    caché en disco— y por eso se han quitado (decisión de Cas, 2026-08-06): la
    lista que decide **a qué inspección se sube** no puede venir de una copia
    vieja sin que nadie se entere. Los dos respaldos servían datos de fecha
    desconocida con el mismo aspecto que los buenos, y el error de elegir un
    destino que ya no existe —o que ha cambiado de nombre— no se ve hasta que
    los ficheros están arriba, en el sitio equivocado.

    Si la API no responde, se dice y punto. El operador puede teclear la
    inspección a mano, que es una decisión suya y consciente, en vez de fiarse
    de una lista que la app le presenta como si estuviera al día.

    `bucket` se mantiene en la firma por compatibilidad con las llamadas
    existentes; ya no se usa.
    """
    try:
        inspecciones = descargar_catalogo_api(auth)
    except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
        return {"ok": False, "inspecciones": [], "origen": "api",
                "bajado_en": 0.0, "error": f"api: {exc}"}
    return {"ok": True, "inspecciones": [i.to_dict() for i in inspecciones],
            "origen": "api", "bajado_en": time.time(), "error": None}
