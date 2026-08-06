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
    with open(os.path.join(REPO, nombre), encoding="utf-8") as fh:
        return fh.read()


UNA = ins.Inspeccion(empresa="Antolin", planta="Los Mangos", anio="2026",
                     tipo="T_Modulos", id=416, fase="Confirmada")


# --- montar y desmontar ------------------------------------------------------

def test_el_prefijo_sale_de_los_campos_de_la_inspeccion():
    assert UNA.prefijo == "Antolin--Los_Mangos--2026--T_Modulos"


def test_el_prefijo_se_desmonta_en_los_mismos_campos():
    v = ins.parse_prefijo(UNA.prefijo)
    assert (v.empresa, v.planta, v.anio, v.tipo) == (
        "Antolin", "Los_Mangos", "2026", "T_Modulos")


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
    p = ins.Inspeccion(empresa="Antolin", anio="2026", tipo="T_Modulos").prefijo
    assert p == "Antolin--_--2026--T_Modulos"
    assert ins.parse_prefijo(p).planta == ""


def test_una_inspeccion_sin_nada_no_da_prefijo():
    assert ins.Inspeccion().prefijo == ""


def test_un_prefijo_ajeno_no_se_inventa_una_inspeccion():
    """`ANTOLIN/` ya existe en el bucket de antes de este cambio, y el operador
    puede teclear una inspección a mano: eso no es una inspección parseable, y
    fingir que sí daría datos falsos."""
    assert ins.parse_prefijo("ANTOLIN") is None
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


def _falsa_descarga(payload, capturado: dict):
    def urlopen(req, timeout=None):
        capturado["url"] = req.full_url
        capturado["auth"] = req.get_header("Authorization")
        return _Resp(json.dumps(payload).encode())
    return urlopen


def test_el_catalogo_se_baja_del_bucket_con_la_sesion_del_operador(monkeypatch):
    cap: dict = {}
    monkeypatch.setattr(ins.urllib.request, "urlopen", _falsa_descarga(
        {"inspecciones": [{"id": 1, "empresa": "E", "planta": "P",
                           "anio": 2026, "tipo": "T_Modulos"}]}, cap))
    lista = ins.descargar_catalogo("datos_para_organizar", _Auth())
    assert [i.prefijo for i in lista] == ["E--P--2026--T_Modulos"]
    assert ins.OBJETO_CATALOGO.replace("_", "_") in cap["url"]
    assert "alt=media" in cap["url"]
    assert cap["auth"] == "Bearer tok"


def test_una_fila_sin_datos_utiles_no_entra_en_la_lista(monkeypatch):
    """Una inspección sin prefijo no se puede elegir: enseñarla sería ofrecer
    un destino que no existe."""
    cap: dict = {}
    monkeypatch.setattr(ins.urllib.request, "urlopen", _falsa_descarga(
        {"inspecciones": [{"id": 2}, "basura", {"id": 3, "planta": "P"}]}, cap))
    assert [i.id for i in ins.descargar_catalogo("b", _Auth())] == [3]


def test_si_el_bucket_falla_se_tira_de_la_ultima_lista_bajada(monkeypatch, tmp_path):
    """Sin red el operador tiene que poder seguir subiendo: la lista de ayer
    sirve, pero se dice de dónde sale."""
    monkeypatch.setattr(ins, "_ruta_cache", lambda: tmp_path / "cache.json")
    ins.guardar_cache([UNA])

    def revienta(req, timeout=None):
        raise urllib.error.URLError("sin red")

    monkeypatch.setattr(ins.urllib.request, "urlopen", revienta)
    out = ins.cargar_catalogo("b", _Auth())
    assert out["origen"] == "cache"
    assert out["inspecciones"][0]["prefijo"] == UNA.prefijo
    assert "sin red" in out["error"]


# --- catálogo vivo: API de ATOM Suite ---------------------------------------

def test_el_catalogo_se_pide_a_la_suite_con_el_id_token(monkeypatch):
    """La app se autentica con el `id_token`, NO con el access token de Storage:
    el backend verifica el JWT contra Google y exige `hd=aerotools.es`. Mandar
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


def test_el_catalogo_vivo_manda_sobre_el_bucket(monkeypatch, tmp_path):
    """El `_inspecciones.json` del bucket se generaba a mano y envejecía en
    silencio. Si la API responde, es la que vale."""
    monkeypatch.setattr(ins, "_ruta_cache", lambda: tmp_path / "cache.json")

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


def test_si_la_suite_no_responde_se_cae_al_bucket(monkeypatch, tmp_path):
    """La Suite caída no puede dejar al operador sin poder subir: el bucket
    sigue siendo la red de seguridad."""
    monkeypatch.setattr(ins, "_ruta_cache", lambda: tmp_path / "cache.json")

    def urlopen(req, timeout=None):
        if "/api/organizer/" in req.full_url:
            raise urllib.error.HTTPError("u", 502, "Bad Gateway",
                                         email.message.Message(), None)
        return _Resp(json.dumps(
            {"inspecciones": [{"id": 7, "empresa": "E", "planta": "P",
                               "anio": "2025", "tipo": "T_Modulos"}]}).encode())

    monkeypatch.setattr(ins.urllib.request, "urlopen", urlopen)
    out = ins.cargar_catalogo("b", _Auth())
    assert out["origen"] == "bucket"
    assert [i["id"] for i in out["inspecciones"]] == [7]


def test_al_llegar_a_la_cache_se_conservan_los_dos_fallos(monkeypatch, tmp_path):
    """Un 403 de la API (cuenta no registrada en la Suite) no puede quedar
    tapado por el fallo posterior del bucket: son problemas distintos y el de
    arriba es el que hay que arreglar."""
    monkeypatch.setattr(ins, "_ruta_cache", lambda: tmp_path / "cache.json")
    ins.guardar_cache([UNA])

    def urlopen(req, timeout=None):
        if "/api/organizer/" in req.full_url:
            raise urllib.error.HTTPError("u", 403, "usuario-no-registrado",
                                         email.message.Message(), None)
        raise urllib.error.URLError("sin red")

    monkeypatch.setattr(ins.urllib.request, "urlopen", urlopen)
    out = ins.cargar_catalogo("b", _Auth())
    assert out["origen"] == "cache"
    assert "403" in out["error"] and "sin red" in out["error"]


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
    distintas, así que `descargar_catalogo` propaga."""
    def revienta(req, timeout=None):
        raise urllib.error.HTTPError("u", 404, "Not Found",
                                     email.message.Message(), None)

    monkeypatch.setattr(ins.urllib.request, "urlopen", revienta)
    with pytest.raises(urllib.error.HTTPError):
        ins.descargar_catalogo("b", _Auth())


def test_la_ui_recibe_prefijo_y_etiqueta_ya_hechos():
    """El desplegable no tiene que saber montar prefijos: sólo hay una
    implementación de la regla."""
    d = UNA.to_dict()
    assert d["prefijo"] == UNA.prefijo
    assert "Antolin" in d["etiqueta"] and "Confirmada" in d["etiqueta"]


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
