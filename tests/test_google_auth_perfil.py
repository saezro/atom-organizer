"""`GoogleAuth._save()` da de alta el perfil en el catálogo, no solo la
sesión activa (ver `session_store.SessionStore.guardar_perfil`): sin esto un
login nuevo no aparecería en la pantalla de "cambiar de cuenta" hasta que
algo más diera de alta el perfil.
"""
from __future__ import annotations

from atom_core.google_auth import GoogleAuth, Identity
from atom_core.session_store import SessionStore

TOKEN = "1//refresh-de-prueba-con-pinta-de-secreto"


def _auth(tmp_path):
    store = SessionStore(tmp_path / "session.db")
    return GoogleAuth("client-id", "client-secret", store=store), store


def test_save_da_de_alta_el_perfil_con_email(tmp_path):
    auth, store = _auth(tmp_path)
    auth._refresh_token = TOKEN
    auth._identity = Identity(email="uno@aerotools.es", domain="aerotools.es",
                              picture="https://example/foto.png", nombre="Uno")

    auth._save()

    perfiles = store.listar_perfiles()
    assert len(perfiles) == 1
    assert perfiles[0].email == "uno@aerotools.es"
    assert perfiles[0].nombre == "Uno"
    assert perfiles[0].picture == "https://example/foto.png"
    assert perfiles[0].tiene_credencial is True
    # También sigue guardando la sesión activa, comportamiento previo intacto.
    sesion = store.leer()
    assert sesion is not None
    assert sesion.email == "uno@aerotools.es"


def test_save_sin_identity_no_da_de_alta_perfil_pero_guarda_sesion(tmp_path):
    auth, store = _auth(tmp_path)
    auth._refresh_token = TOKEN
    auth._identity = None  # p.ej. token guardado sin haber resuelto identidad

    auth._save()

    assert store.listar_perfiles() == []
    sesion = store.leer()
    assert sesion is not None
    assert sesion.refresh_token == TOKEN


def test_save_no_lanza_si_el_catalogo_de_perfiles_peta(tmp_path, monkeypatch):
    auth, store = _auth(tmp_path)
    auth._refresh_token = TOKEN
    auth._identity = Identity(email="uno@aerotools.es", domain="aerotools.es")

    def _guardar_perfil_roto(*args, **kwargs):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(store, "guardar_perfil", _guardar_perfil_roto)

    # No debe lanzar: la sesión activa es lo importante y ya se guardó.
    auth._save()
    sesion = store.leer()
    assert sesion is not None
    assert sesion.email == "uno@aerotools.es"
