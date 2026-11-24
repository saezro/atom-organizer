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

**Fuente y caché.** La fuente es la API. Desde 3.5.3 (decisión de Cas,
revoca la del 2026-08-06 / 3.4.22) se vuelve a guardar en local el último
catálogo bajado con éxito (`catalogo_inspecciones.json`, con `bajado_en`), para
que sin conexión se pueda **organizar**. La caché solo se usa si la red falla
(URLError, timeout, OSError, 5xx) y siempre se dice: `origen: "cache"` más el
momento en que se bajó, para que la UI no la presente como lista al día. Un
401/403 NO usa la caché (la sesión no vale). No se guarda nunca una lista
vacía ni un fallo. El bucket (`_inspecciones.json`) sigue sin ser fuente.

**El prefijo se monta y se desmonta.** Regla de Cas (2026-08-06): «con el
nombre se debería poder sacar igual que montamos el nombre lo podemos
desmontar». El prefijo son cuatro campos en orden fijo separados por `--`::

    PLANTA_B--PLANTA_J--2026--T_Modulos
    <empresa>--<planta>---<año>--<tipo>

`--` es separador seguro porque `_campo()` deja sólo `[A-Za-z0-9_]`: ningún
campo puede contener un guion, así que dos guiones seguidos sólo aparecen
entre campos. Un campo que falte se escribe `_`, para que el número de piezas
sea siempre cuatro y `parse_prefijo()` no tenga que adivinar.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

__all__ = [
    "Inspeccion",
    "API_BASE",
    "RUTA_CATALOGO",
    "SEPARADOR",
    "prefijo_de_inspeccion",
    "parse_prefijo",
    "descargar_catalogo_api",
    "cargar_catalogo",
    "guardar_cache",
    "leer_cache",
    "borrar_cache",
    "es_fallo_de_red",
    "sugerir_inspeccion",
]

# Backend de ATOM Suite. La env existe para poder apuntar a otro
# entorno y validar el endpoint sin recompilar
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
    # orientación de la planta ("Horizontal", "Vertical", "Varias"). Regla de
    # el responsable (2026-09-28): Horizontal NUNCA se gira (ver
    # `atom_core.indice._consenso_de_angulo_por_vuelo`). Vacío mientras
    # `lib/organizer-catalogo.js` de Atom-suite no mande el campo -el backend
    # de hoy no lo selecciona ni proyecta (server.js:2423,
    # lib/organizer-catalogo.js:28-65)-.
    orientacion: str = ""

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

    Los nombres reales traen eñes y espacios (`PLANTA_J`, `OCAÑA`).
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
    inspección nueva a mano, y los prefijos anteriores a este cambio (`PLANTA_B`,
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
            orientacion=str(fila.get("orientacion") or ""),
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
    # En modo broker (Raspberry Pi emparejada por QR) no hay id_token: Google
    # nunca se lo dio a este equipo, solo el device_token que custodia la
    # Suite. `auth.id_token()` lanzaría siempre en ese modo, así que aquí se
    # autentica con el device_token en su propia cabecera; fuera de broker el
    # comportamiento no cambia (Authorization: Bearer <id_token>, como antes).
    device_token = getattr(auth, "device_token", None)
    if device_token:
        req.add_header("X-Organizer-Device", device_token)
    elif getattr(auth, "es_password", False):
        # Modo usuario/contraseña: la Suite acepta su `sesion` como Bearer.
        sesion = auth.sesion_token
        if not sesion:
            from atom_core.google_auth import AuthError
            raise AuthError("Sesión caducada, vuelve a entrar con tu usuario")
        req.add_header("Authorization", f"Bearer {sesion}")
    else:
        req.add_header("Authorization", f"Bearer {auth.id_token()}")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8") or "{}")
    return _desde_json(data)


NOMBRE_CACHE = "catalogo_inspecciones.json"


def _ruta_cache():
    from atom_core.google_auth import user_data_dir
    return user_data_dir() / NOMBRE_CACHE


def _ahora_madrid_iso() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Madrid")).isoformat(timespec="seconds")


def _usuario_de(auth) -> str:
    """Email de la sesión (minúsculas) o "" si no se puede saber. No hace red."""
    try:
        ident = getattr(auth, "identity", None)
        return str(getattr(ident, "email", "") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def borrar_cache() -> None:
    """Borra la caché del catálogo (al cerrar sesión). No lanza."""
    try:
        _ruta_cache().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("No se pudo borrar la caché del catálogo", exc_info=True)


def guardar_cache(inspecciones: list[dict], usuario: str = "") -> None:
    """Guarda el catálogo (atómico), ligado a `usuario`. Lista vacía: no se toca."""
    if not inspecciones:
        return
    ruta = _ruta_cache()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = {"usuario": usuario, "bajado_en": _ahora_madrid_iso(),
             "inspecciones": inspecciones}
    fd, tmp = tempfile.mkstemp(dir=str(ruta.parent), prefix=NOMBRE_CACHE + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, ruta)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def leer_cache(usuario: str = "") -> dict | None:
    """`{bajado_en, inspecciones}` o None si no hay caché válida o es de otro usuario.
    Con `usuario` vacío (identidad desconocida) NUNCA se sirve: podría ser de otro."""
    if not usuario:
        return None
    try:
        datos = json.loads(_ruta_cache().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(datos, dict) or str(datos.get("usuario") or "") != usuario:
        return None
    lista = datos.get("inspecciones")
    if not isinstance(lista, list) or not lista:
        return None
    return {"bajado_en": str(datos.get("bajado_en") or ""), "inspecciones": lista}


def es_fallo_de_red(exc: BaseException) -> bool:
    """Fallo de transporte (reintentable): URLError, timeout, DNS, 5xx/408/429.
    Un OSError de disco local NO lo es. Criterio único de la app y del catálogo."""
    import socket
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code in (408, 429)
    return isinstance(exc, (ConnectionError, TimeoutError, socket.timeout, socket.gaierror,
                            urllib.error.URLError))


_es_fallo_de_red = es_fallo_de_red


def cargar_catalogo(bucket: str, auth) -> dict:
    """Catálogo para la UI: la API de ATOM Suite, con la caché local de respaldo.

    Devuelve `{"ok", "inspecciones", "origen": "api"|"cache", "bajado_en",
    "error"}`. No lanza: la pantalla tiene que poder dibujarse aunque falle.

    Con la API ok y lista no vacía se guarda la caché. Si la red falla
    (URLError, timeout, OSError, 5xx) y hay caché, se sirve con
    `origen: "cache"`, `bajado_en` (ISO Madrid) y `aviso`: así se puede
    organizar sin conexión, y la UI dice de cuándo es la lista. Un 401/403
    nunca usa la caché (`auth_invalida` en el 401). Sin caché, el error se dice
    tal cual y el operador puede teclear la inspección a mano.

    `bucket` se mantiene en la firma por compatibilidad; no se usa.
    """
    try:
        inspecciones = descargar_catalogo_api(auth)
    except Exception as exc:  # noqa: BLE001 - se enseña, no se traga
        logger.warning("No se pudo descargar el catálogo de la API: %r", exc)
        if es_fallo_de_red(exc):
            cache = leer_cache(_usuario_de(auth))
            if cache:
                return {"ok": True, "inspecciones": cache["inspecciones"],
                        "origen": "cache", "bajado_en": cache["bajado_en"],
                        "aviso": "Sin conexión con la Suite: lista guardada.",
                        "error": None}
        res = {"ok": False, "inspecciones": [], "origen": "api",
               "bajado_en": 0.0, "error": f"api: {exc}"}
        # 401 = la sesión local no vale contra la Suite: quien llama debe
        # invalidarla en vez de dejar al operador con una lista vacía muda.
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 401:
            res["auth_invalida"] = True
        return res
    lista = [i.to_dict() for i in inspecciones]
    try:
        guardar_cache(lista, _usuario_de(auth))
    except Exception:  # noqa: BLE001 - la caché es un extra, no rompe la carga
        logger.exception("No se pudo guardar la caché del catálogo")
    return {"ok": True, "inspecciones": lista,
            "origen": "api", "bajado_en": time.time(), "error": None}


# --------------------------------------------------------------------------
# Sugerencia automática de inspección (carpeta del vuelo + estadillo)
# --------------------------------------------------------------------------

_RE_CARPETA = re.compile(
    r"^(\d{4})[\s_.\-]+(\d{1,2})[\s_.\-]+(\d{1,2})[\s_.\-]+(.+)$")
_RE_ANIO = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def _norm(texto) -> str:
    """Texto comparable: ASCII, mayúsculas, todo no alfanumérico -> `_`."""
    base = unicodedata.normalize("NFKD", str(texto if texto is not None else ""))
    base = base.encode("ascii", "ignore").decode("ascii").upper()
    return re.sub(r"[^A-Z0-9]+", "_", base).strip("_")


def _planta_en_texto(planta: str, texto: str) -> bool:
    """`planta` (ya normalizada) es igual a `texto` o aparece como token(s)."""
    if not planta or not texto:
        return False
    if planta == texto:
        return True
    if len(planta) < 3:
        return False
    return f"_{planta}_" in f"_{texto}_"


def _get(it, campo):
    return it.get(campo, "") if isinstance(it, dict) else getattr(it, campo, "")


def sugerir_inspeccion(carpeta: str, info_estadillo: dict | None,
                       catalogo) -> dict:
    """Propone la inspección a partir de la carpeta del vuelo y el estadillo.

    Función pura (sin red ni disco). `catalogo` son dicts de `to_dict()` o
    `Inspeccion`. La carpeta suele llamarse `YYYY_MM_DD_<Planta>`; del estadillo
    se usan `trabajo`/`empresa` (planta) y `fecha` (año). Coincide una
    inspección si su planta normalizada es igual o token contenido en el texto
    Y su año es el detectado.

    Devuelve `{"estado", "candidatos": [prefijos], "prefijo"?, "motivo"?}`:
    - `unica`: un solo prefijo candidato (`prefijo` lo trae).
    - `varias`: más de uno (p. ej. misma planta/año en dos fases): no se elige.
    - `ninguna`: sin planta o año detectables, o sin coincidencias.
    - `conflicto`: carpeta y estadillo apuntan a plantas (o años) distintos.
    """
    info = info_estadillo if isinstance(info_estadillo, dict) else {}
    if info.get("error"):
        info = {}
    cat = list(catalogo or [])

    base = os.path.basename(str(carpeta or "").replace("\\", "/").rstrip("/")).strip()
    anio_c, texto_c = "", _norm(base)
    m = _RE_CARPETA.match(base)
    if m:
        anio_c, texto_c = m.group(1), _norm(m.group(4))
    texto_e = " ".join(str(info.get(k) or "") for k in ("trabajo", "empresa"))
    texto_e = _norm(texto_e)
    fechas = [str(info.get("fecha") or "")] + [str(f) for f in (info.get("fechas") or [])]
    anio_e = next((a.group(1) for a in (_RE_ANIO.search(f) for f in fechas) if a), "")

    if anio_c and anio_e and anio_c != anio_e:
        return {"estado": "conflicto", "candidatos": [],
                "motivo": f"La carpeta es de {anio_c} y el estadillo de {anio_e}."}
    anio = anio_c or anio_e
    if not anio:
        return {"estado": "ninguna", "candidatos": [],
                "motivo": "No se detecta el año en la carpeta ni en el estadillo."}

    def plantas_que_casan(texto):
        return {_norm(_get(i, "planta")) for i in cat
                if _planta_en_texto(_norm(_get(i, "planta")), texto)}

    pc, pe = plantas_que_casan(texto_c), plantas_que_casan(texto_e)
    if pc and pe:
        plantas = pc & pe
        if not plantas:
            return {"estado": "conflicto", "candidatos": [],
                    "motivo": "La carpeta y el estadillo apuntan a plantas distintas."}
    else:
        plantas = pc or pe

    candidatos: list[str] = []
    n_entradas = 0
    for i in cat:
        if (_norm(_get(i, "planta")) in plantas
                and str(_get(i, "anio")).strip() == anio):
            pref = _get(i, "prefijo") or (
                prefijo_de_inspeccion(i) if isinstance(i, Inspeccion) else "")
            if pref:
                n_entradas += 1
                if pref not in candidatos:
                    candidatos.append(pref)
    if not candidatos:
        return {"estado": "ninguna", "candidatos": [],
                "motivo": "Ninguna inspección del catálogo coincide con planta y año."}
    # Dos entradas del catálogo que solo difieren en la fase comparten prefijo,
    # pero son inspecciones distintas: no se elige sola (decide el operador).
    if len(candidatos) == 1 and n_entradas == 1:
        return {"estado": "unica", "prefijo": candidatos[0], "candidatos": candidatos}
    return {"estado": "varias", "candidatos": candidatos}
