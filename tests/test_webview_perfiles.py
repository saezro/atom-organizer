"""Endpoints del catálogo de perfiles (`listar_perfiles`, `activar_perfil`,
`borrar_perfil`) en la capa `Api`. La lógica de datos ya está cubierta en
`test_session_store_perfiles.py`; aquí solo se cubre el pegamento: la
resolución del avatar vía `avatar_cache`, la invalidación de `self._auth`
cacheado y que un store roto nunca tumba la pantalla de entrada.
"""
from __future__ import annotations

from app_webview import Api
from atom_core.session_store import SessionStore

TOKEN = "1//refresh-de-prueba-con-pinta-de-secreto"


def _api(tmp_path):
    api = Api()
    api._perfiles_store = SessionStore(tmp_path / "session.db")
    return api


def test_listar_perfiles_devuelve_las_claves_del_contrato_con_avatar_resuelto(tmp_path, monkeypatch):
    api = _api(tmp_path)
    api._perfiles_store.guardar_perfil(
        "uno@aerotools.es", TOKEN, modo="google",
        picture="https://lh3.googleusercontent.com/foto-uno", nombre="Uno")

    # `listar_perfiles` importa `user_data_dir` y `avatar_cache` dentro del
    # método (import perezoso, como el resto de `Api`): se parchean ahí,
    # sobre los módulos reales, para no depender del orden de import.
    import atom_core.google_auth as google_auth_mod
    import atom_core.avatar_cache as avatar_cache_mod

    monkeypatch.setattr(google_auth_mod, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(avatar_cache_mod, "obtener",
                        lambda url, email, dir_datos: f"data:image/png;base64,FAKE-{email}")
    monkeypatch.setattr(avatar_cache_mod, "limpiar", lambda dir_datos, emails_vivos: None)

    perfiles = api.listar_perfiles()

    assert perfiles == [{
        "email": "uno@aerotools.es",
        "nombre": "Uno",
        "picture": "data:image/png;base64,FAKE-uno@aerotools.es",
        "modo": "google",
        "tiene_credencial": True,
    }]


def test_listar_perfiles_sin_picture_no_llama_a_la_cache(tmp_path, monkeypatch):
    api = _api(tmp_path)
    api._perfiles_store.guardar_perfil("uno@aerotools.es", TOKEN, nombre="Uno", picture="")

    import atom_core.google_auth as google_auth_mod
    import atom_core.avatar_cache as avatar_cache_mod

    monkeypatch.setattr(google_auth_mod, "user_data_dir", lambda: tmp_path)

    def _obtener_no_debe_llamarse(*args, **kwargs):
        raise AssertionError("no debe consultarse la caché sin picture")

    monkeypatch.setattr(avatar_cache_mod, "obtener", _obtener_no_debe_llamarse)
    monkeypatch.setattr(avatar_cache_mod, "limpiar", lambda dir_datos, emails_vivos: None)

    perfiles = api.listar_perfiles()
    assert perfiles[0]["picture"] == ""


def test_listar_perfiles_devuelve_lista_vacia_si_el_store_peta(tmp_path, monkeypatch):
    api = _api(tmp_path)

    def _listar_perfiles_roto():
        raise RuntimeError("BD corrupta")

    monkeypatch.setattr(api._perfiles_store, "listar_perfiles", _listar_perfiles_roto)

    assert api.listar_perfiles() == []


def test_activar_perfil_valido_devuelve_ok_y_limpia_el_auth_cacheado(tmp_path):
    api = _api(tmp_path)
    api._perfiles_store.guardar_perfil("uno@aerotools.es", TOKEN, nombre="Uno")
    api._auth = object()  # simula una credencial ya cacheada de otra cuenta

    res = api.activar_perfil("uno@aerotools.es")

    assert res == {"ok": True}
    assert api._auth is None


def test_activar_perfil_inexistente_devuelve_ok_false(tmp_path):
    api = _api(tmp_path)

    res = api.activar_perfil("fantasma@aerotools.es")

    assert res == {"ok": False}


def test_activar_perfil_con_store_roto_devuelve_ok_false(tmp_path, monkeypatch):
    api = _api(tmp_path)

    def _activar_perfil_roto(email):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(api._perfiles_store, "activar_perfil", _activar_perfil_roto)

    assert api.activar_perfil("uno@aerotools.es") == {"ok": False}


def test_borrar_perfil_lo_quita_del_catalogo_y_limpia_el_auth_cacheado(tmp_path):
    api = _api(tmp_path)
    api._perfiles_store.guardar_perfil("uno@aerotools.es", TOKEN, nombre="Uno")
    api._auth = object()

    api.borrar_perfil("uno@aerotools.es")

    assert api._perfiles_store.listar_perfiles() == []
    assert api._auth is None


def test_borrar_perfil_con_store_roto_no_lanza(tmp_path, monkeypatch):
    api = _api(tmp_path)

    def _borrar_perfil_roto(email):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(api._perfiles_store, "borrar_perfil", _borrar_perfil_roto)

    # No debe lanzar; el método no devuelve nada útil en este caso.
    api.borrar_perfil("uno@aerotools.es")
