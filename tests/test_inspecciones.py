"""Elegir la inspección: el prefijo se monta desde ella y se puede desmontar.

Lo que se protege aquí es la regla que fijó Cas (2026-08-06): «con el nombre se
debería poder sacar igual que montamos el nombre lo podemos desmontar». Si eso
se rompe, el Cloud Run que organiza deja de poder saber a qué inspección
pertenece lo que hay en el bucket.
"""
from __future__ import annotations

import email.message
import io
import json
import os
import urllib.error

import pytest

from atom_core import inspecciones as ins

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _fuente(nombre: str) -> str:
    # Código de la app en src/; el resto (webui/...) cuelga de la raíz.
    base = REPO if nombre.startswith("webui") else os.path.join(REPO, "src")
    with open(os.path.join(base, nombre), encoding="utf-8") as fh:
        return fh.read()


UNA = ins.Inspeccion(empresa="Planta_B", planta="Planta J", anio="2026",
                     tipo="T_Modulos", id=416, fase="Confirmada")


# --- montar y desmontar ------------------------------------------------------

def test_el_prefijo_sale_de_los_campos_de_la_inspeccion():
    assert UNA.prefijo == "Planta_B--Planta_J--2026--T_Modulos"


def test_el_prefijo_se_desmonta_en_los_mismos_campos():
    v = ins.parse_prefijo(UNA.prefijo)
    assert (v.empresa, v.planta, v.anio, v.tipo) == (
        "Planta_B", "Planta_J", "2026", "T_Modulos")


def test_acentos_y_enes_no_llegan_al_bucket():
    """Los nombres reales traen «OCAÑA» y espacios; el objeto no puede depender
    del sistema de ficheros de quien sube."""
    p = ins.Inspeccion(empresa="Ocaña", planta="El Niño", anio="2025",
                       tipo="N_serie").prefijo
    assert p == "Ocana--El_Nino--2025--N_serie"
    assert p.isascii()


def test_un_guion_dentro_de_un_campo_no_rompe_el_desmontaje():
    """`-` es el separador: si sobreviviera dentro de un campo, el prefijo
    tendría más de cuatro piezas y dejaría de ser reversible."""
    p = ins.Inspeccion(empresa="Sur-Este", planta="PV-1", anio="2026",
                       tipo="T_Modulos").prefijo
    assert p == "Sur_Este--PV_1--2026--T_Modulos"
    v = ins.parse_prefijo(p)
    assert (v.empresa, v.planta) == ("Sur_Este", "PV_1")


def test_un_campo_que_falta_deja_hueco_marcado():
    """Siempre cuatro piezas: si se colapsaran, `parse` tendría que adivinar
    cuál falta."""
    p = ins.Inspeccion(empresa="Planta_B", anio="2026", tipo="T_Modulos").prefijo
    assert p == "Planta_B--_--2026--T_Modulos"
    assert ins.parse_prefijo(p).planta == ""


def test_una_inspeccion_sin_nada_no_da_prefijo():
    assert ins.Inspeccion().prefijo == ""


def test_un_prefijo_ajeno_no_se_inventa_una_inspeccion():
    """`PLANTA_B/` ya existe en el bucket de antes de este cambio, y el operador
    puede teclear una inspección a mano: eso no es una inspección parseable, y
    fingir que sí daría datos falsos."""
    assert ins.parse_prefijo("PLANTA_B") is None
    assert ins.parse_prefijo("a--b--c") is None
    assert ins.parse_prefijo("") is None


def test_el_id_no_forma_parte_del_prefijo():
    assert "416" not in UNA.prefijo


# --- catálogo ----------------------------------------------------------------

class _Resp(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.status = 200
        self.headers = email.message.Message()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Auth:
    def access_token(self, *, force_refresh: bool = False) -> str:
        return "tok"

    def id_token(self) -> str:
        return "idtok"


class _AuthUsuario(_Auth):
    def __init__(self, email):
        class _I:
            pass
        self.identity = _I()
        self.identity.email = email


def _falsa_descarga(payload, capturado: dict):
    def urlopen(req, timeout=None):
        capturado["url"] = req.full_url
        capturado["auth"] = req.get_header("Authorization")
        return _Resp(json.dumps(payload).encode())
    return urlopen


def test_el_catalogo_se_pide_a_la_suite_con_el_id_token(monkeypatch):
    """La app se autentica con el `id_token`, NO con el access token de Storage:
    el backend verifica el JWT contra Google y exige `hd=ejemplo.com`. Mandar
    aquí el access token daría 401 y nadie sabría por qué."""
    cap: dict = {}
    monkeypatch.setattr(ins.urllib.request, "urlopen", _falsa_descarga(
        {"inspecciones": [{"id": 184, "empresa": "METLEN", "planta": "CLEVE_HILL",
                           "anio": "2027", "tipo": "T_Modulos",
                           "fase": "Confirmada"}]}, cap))
    lista = ins.descargar_catalogo_api(_Auth())
    assert [i.prefijo for i in lista] == ["METLEN--CLEVE_HILL--2027--T_Modulos"]
    assert cap["url"].endswith("/api/organizer/inspecciones")
    assert cap["auth"] == "Bearer idtok"


def test_el_catalogo_sale_de_la_api_y_de_ningun_otro_sitio(monkeypatch):
    """Ni el `_inspecciones.json` del bucket ni la caché en disco: los dos se
    generaban aparte y envejecían en silencio, y de esta lista sale el destino
    de la subida."""
    def urlopen(req, timeout=None):
        assert "/api/organizer/inspecciones" in req.full_url, "no debe tocar el bucket"
        return _Resp(json.dumps(
            {"inspecciones": [{"id": 9, "empresa": "E", "planta": "P",
                               "anio": "2026", "tipo": "T_Modulos"}]}).encode())

    monkeypatch.setattr(ins.urllib.request, "urlopen", urlopen)
    out = ins.cargar_catalogo("b", _Auth())
    assert out["origen"] == "api"
    assert out["ok"] and out["error"] is None
    assert [i["id"] for i in out["inspecciones"]] == [9]


def test_la_api_devuelve_campos_crudos_y_el_slug_lo_monta_la_app(monkeypatch):
    """Contrato con el backend: el endpoint NO manda `prefijo`. Si algún día lo
    mandara, seguiría mandando la regla local — una implementación, no dos."""
    cap: dict = {}
    monkeypatch.setattr(ins.urllib.request, "urlopen", _falsa_descarga(
        {"inspecciones": [{"id": 1, "empresa": "Ocaña", "planta": "El Niño",
                           "anio": "2025", "tipo": "N_serie",
                           "prefijo": "BASURA--QUE--NO--MANDA"}]}, cap))
    assert ins.descargar_catalogo_api(_Auth())[0].prefijo == "Ocana--El_Nino--2025--N_serie"


def test_el_anio_llega_como_string_aunque_postgres_lo_de_numerico(monkeypatch):
    """`a_o` es bigint y el driver `pg` lo devuelve como string, pero eso puede
    cambiar. El prefijo tiene que salir igual en los dos casos."""
    cap: dict = {}
    monkeypatch.setattr(ins.urllib.request, "urlopen", _falsa_descarga(
        {"inspecciones": [{"id": 1, "empresa": "E", "planta": "P",
                           "anio": 2026, "tipo": "T"}]}, cap))
    assert ins.descargar_catalogo_api(_Auth())[0].anio == "2026"


def test_un_fallo_de_descarga_no_pasa_por_lista_vacia(monkeypatch):
    """«No hay inspecciones» y «no he podido mirar» llevan a decisiones
    distintas, así que `descargar_catalogo_api` propaga."""
    def revienta(req, timeout=None):
        raise urllib.error.HTTPError("u", 404, "Not Found",
                                     email.message.Message(), None)

    monkeypatch.setattr(ins.urllib.request, "urlopen", revienta)
    with pytest.raises(urllib.error.HTTPError):
        ins.descargar_catalogo_api(_Auth())


_FILA = {"id": 9, "empresa": "E", "planta": "P", "anio": "2026", "tipo": "T_Modulos"}


@pytest.fixture
def cache_tmp(tmp_path, monkeypatch):
    ruta = tmp_path / "catalogo_inspecciones.json"
    monkeypatch.setattr(ins, "_ruta_cache", lambda: ruta)
    return ruta


def _api_ok(monkeypatch, filas):
    monkeypatch.setattr(ins.urllib.request, "urlopen", lambda req, timeout=None:
                        _Resp(json.dumps({"inspecciones": filas}).encode()))


def _api_falla(monkeypatch, exc):
    def revienta(req, timeout=None):
        raise exc
    monkeypatch.setattr(ins.urllib.request, "urlopen", revienta)


def _http(code):
    return urllib.error.HTTPError("u", code, "x", email.message.Message(), None)


def test_ok_con_lista_guarda_la_cache(monkeypatch, cache_tmp):
    _api_ok(monkeypatch, [_FILA])
    out = ins.cargar_catalogo("b", _Auth())
    assert out["origen"] == "api"
    datos = json.loads(cache_tmp.read_text(encoding="utf-8"))
    assert [i["id"] for i in datos["inspecciones"]] == [9]
    assert datos["bajado_en"].endswith(("+01:00", "+02:00"))  # Europe/Madrid
    assert "\n  " in cache_tmp.read_text(encoding="utf-8")  # indent=2
    assert not list(cache_tmp.parent.glob("*.tmp"))


@pytest.mark.parametrize("exc", [
    urllib.error.URLError("sin red"), TimeoutError("t"), ConnectionResetError("r"), None])
def test_fallo_de_red_sirve_la_cache(monkeypatch, cache_tmp, exc):
    _api_ok(monkeypatch, [_FILA])
    primera = ins.cargar_catalogo("b", _AuthUsuario("a@x.com"))
    _api_falla(monkeypatch, exc or _http(502))
    out = ins.cargar_catalogo("b", _AuthUsuario("a@x.com"))
    assert out["ok"] is True and out["origen"] == "cache"
    assert out["inspecciones"] == primera["inspecciones"]
    assert out["bajado_en"] and out["aviso"]


def test_401_no_usa_la_cache_y_marca_auth_invalida(monkeypatch, cache_tmp):
    _api_ok(monkeypatch, [_FILA])
    ins.cargar_catalogo("b", _Auth())
    _api_falla(monkeypatch, _http(401))
    out = ins.cargar_catalogo("b", _Auth())
    assert out["ok"] is False and out["inspecciones"] == []
    assert out["auth_invalida"] is True


def test_403_no_usa_la_cache(monkeypatch, cache_tmp):
    _api_ok(monkeypatch, [_FILA])
    ins.cargar_catalogo("b", _Auth())
    _api_falla(monkeypatch, _http(403))
    out = ins.cargar_catalogo("b", _Auth())
    assert out["ok"] is False and out["inspecciones"] == []


def test_ok_con_lista_vacia_no_pisa_la_cache(monkeypatch, cache_tmp):
    _api_ok(monkeypatch, [_FILA])
    ins.cargar_catalogo("b", _Auth())
    _api_ok(monkeypatch, [])
    out = ins.cargar_catalogo("b", _Auth())
    assert out["ok"] is True and out["inspecciones"] == []
    assert [i["id"] for i in json.loads(cache_tmp.read_text("utf-8"))["inspecciones"]] == [9]


def test_sin_cache_y_offline_es_error(monkeypatch, cache_tmp):
    _api_falla(monkeypatch, urllib.error.URLError("sin red"))
    out = ins.cargar_catalogo("b", _Auth())
    assert out["ok"] is False and out["inspecciones"] == []
    assert out["origen"] == "api" and "sin red" in out["error"]
    assert not cache_tmp.exists()


def test_un_fallo_no_escribe_la_cache(monkeypatch, cache_tmp):
    _api_falla(monkeypatch, _http(502))
    ins.cargar_catalogo("b", _Auth())
    assert not cache_tmp.exists()


def test_la_ui_recibe_prefijo_y_etiqueta_ya_hechos():
    """El desplegable no tiene que saber montar prefijos: sólo hay una
    implementación de la regla."""
    d = UNA.to_dict()
    assert d["prefijo"] == UNA.prefijo
    assert "Planta_B" in d["etiqueta"] and "Confirmada" in d["etiqueta"]


# --- cableado ----------------------------------------------------------------

def test_el_destino_ya_no_sale_del_nombre_de_la_carpeta():
    """La regresión que importa: si `cloud_upload` volviera a derivar el
    prefijo de `root.name`, dos vuelos distintos podrían pisarse otra vez."""
    src = _fuente("app_webview.py")
    assert "prefijo_desde_carpeta(root.name)" not in src
    assert "def _destino(" in src
    # Y tampoco por la puerta de atrás: sin inspección no puede haber destino.
    assert "or root.name" not in src


def test_el_bridge_pasa_el_prefijo_en_las_dos_llamadas():
    src = _fuente(os.path.join("webui", "src", "bridge.js"))
    assert "call('cloud_prepare', folder, prefix" in src
    assert "prefix ?? null" in src


def test_la_app_no_lleva_credenciales_de_la_base_de_datos():
    """El `.exe` es público: si alguna vez alguien mete aquí el usuario de la
    BD de producción, este test tiene que ponerse rojo."""
    for fichero in ("atom_core/inspecciones.py", "app_webview.py"):
        src = _fuente(fichero)
        assert "PGPASSWORD" not in src
        assert "psql" not in src


def test_cache_de_otro_usuario_no_se_sirve(monkeypatch, cache_tmp):
    _api_ok(monkeypatch, [_FILA])
    ins.cargar_catalogo("b", _AuthUsuario("A@x.com"))
    _api_falla(monkeypatch, urllib.error.URLError("sin red"))
    assert ins.cargar_catalogo("b", _AuthUsuario("b@x.com"))["ok"] is False
    out = ins.cargar_catalogo("b", _AuthUsuario("a@x.com"))
    assert out["ok"] is True and out["origen"] == "cache"


def test_borrar_cache_al_cerrar_sesion(monkeypatch, cache_tmp):
    _api_ok(monkeypatch, [_FILA])
    ins.cargar_catalogo("b", _AuthUsuario("a@x.com"))
    assert cache_tmp.exists()
    ins.borrar_cache()
    assert not cache_tmp.exists() and ins.leer_cache("a@x.com") is None
    ins.borrar_cache()  # idempotente


@pytest.mark.parametrize("code,esperado", [(408, True), (429, True), (503, True), (401, False), (404, False)])
def test_es_fallo_de_red_incluye_408_429(code, esperado):
    assert ins.es_fallo_de_red(_http(code)) is esperado
    assert not ins.es_fallo_de_red(PermissionError("disco"))


def test_app_y_catalogo_comparten_criterio_de_red():
    import app_webview as aw
    assert aw._es_error_de_red(_http(429)) and aw._es_error_de_red(_http(408))


def test_cache_con_usuario_vacio_no_se_sirve(monkeypatch, cache_tmp):
    ins.guardar_cache([_FILA], "")
    assert ins.leer_cache("") is None
    _api_falla(monkeypatch, urllib.error.URLError("sin red"))
    out = ins.cargar_catalogo("b", _Auth())  # identidad desconocida
    assert out["ok"] is False and out["inspecciones"] == []
