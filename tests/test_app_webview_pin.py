"""Endpoints del PIN del kiosco en la capa Api."""

from app_webview import Api
from atom_core.session_store import SessionStore


def _api(tmp_path):
    api = Api()
    api._pin_store = SessionStore(tmp_path / "session.db")
    return api


def test_estado_inicial_sin_pin(tmp_path):
    estado = _api(tmp_path).pin_estado()
    assert estado["ok"] is True
    assert estado["hay_pin"] is False
    assert estado["bloqueado"] is False


def test_fijar_deja_hay_pin_en_true(tmp_path):
    api = _api(tmp_path)
    assert api.pin_fijar("1234")["ok"] is True
    assert api.pin_estado()["hay_pin"] is True


def test_fijar_con_formato_malo_devuelve_error(tmp_path):
    api = _api(tmp_path)
    res = api.pin_fijar("12")
    assert res["ok"] is False
    assert "digitos" in res["error"]
    assert api.pin_estado()["hay_pin"] is False


def test_verificar_correcto_e_incorrecto(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    assert api.pin_verificar("1234")["ok"] is True
    assert api.pin_verificar("0000")["ok"] is False


def test_cinco_fallos_bloquean_y_no_admiten_ni_el_pin_bueno(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    for _ in range(5):
        api.pin_verificar("0000")
    estado = api.pin_estado()
    assert estado["bloqueado"] is True
    assert estado["espera_segundos"] > 0
    res = api.pin_verificar("1234")
    assert res["ok"] is False
    assert res["espera_segundos"] > 0


def test_un_acierto_limpia_los_fallos_previos(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    for _ in range(4):
        api.pin_verificar("0000")
    assert api.pin_verificar("1234")["ok"] is True
    for _ in range(4):
        api.pin_verificar("0000")
    assert api.pin_estado()["bloqueado"] is False


def test_cambiar_pide_el_actual(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    assert api.pin_cambiar("0000", "5678")["ok"] is False
    assert api.pin_verificar("1234")["ok"] is True
    assert api.pin_cambiar("1234", "5678")["ok"] is True
    assert api.pin_verificar("5678")["ok"] is True


def test_ningun_endpoint_filtra_el_hash(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    for res in (api.pin_estado(), api.pin_verificar("1234"), api.pin_cambiar("1234", "5678")):
        assert "scrypt" not in str(res)
        assert "1234" not in str(res)


def test_cerrar_sesion_borra_el_pin(tmp_path, monkeypatch):
    api = _api(tmp_path)
    api.pin_fijar("1234")

    class AuthFalso:
        def logout(self):
            return None

    monkeypatch.setattr(api, "_get_auth", lambda: AuthFalso())
    assert api.cloud_logout()["ok"] is True
    assert api.pin_estado()["hay_pin"] is False


def test_fijar_no_pisa_un_pin_existente(tmp_path):
    """Regresion: `pin_fijar` era un bypass total del PIN.

    Sin esta guarda, quien tuviera el Chromium del kiosco delante --el actor
    del que protege el PIN-- reescribia el PIN vigente sin conocerlo y sin
    pasar por el bloqueo escalado.
    """
    api = _api(tmp_path)
    assert api.pin_fijar("1234")["ok"] is True

    res = api.pin_fijar("0000")
    assert res["ok"] is False
    assert api.pin_verificar("1234")["ok"] is True
    assert api.pin_verificar("0000")["ok"] is False


def test_fijar_respeta_el_bloqueo_por_intentos(tmp_path):
    api = _api(tmp_path)
    api.pin_fijar("1234")
    for _ in range(5):
        api.pin_verificar("9999")

    res = api.pin_fijar("0000")
    assert res["ok"] is False
    assert res.get("espera_segundos", 0) > 0
