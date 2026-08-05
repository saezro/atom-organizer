"""Tests de `atom_core.google_auth` — login con Google desde la app.

Lo que se protege aquí es lo que decide **quién puede escribir en el bucket**.
Un fallo silencioso en la comprobación de dominio o en el manejo del token
caducado no da una excepción bonita: da una subida que muere a la hora, o una
cuenta ajena escribiendo en el bucket de Aerotools.

No se habla con Google: se sustituye el transporte HTTP y el navegador. El
flujo de loopback sí se ejercita de verdad (servidor real en 127.0.0.1, la
redirección llega por HTTP), porque es donde están los detalles que se rompen.
"""
from __future__ import annotations

import base64
import http.client
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from atom_core import cloud_upload as cu
from atom_core import google_auth as ga


CLIENT_ID = "123.apps.googleusercontent.com"
CLIENT_SECRET = "secreto-que-no-es-secreto"


def _id_token(email: str, hd: str | None = None) -> str:
    """Un id_token con la firma sin rellenar: el módulo solo lee los claims."""
    claims = {"email": email}
    if hd:
        claims["hd"] = hd
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"cabecera.{payload}.firma"


class FakeGoogle:
    """Endpoint de tokens de Google, en memoria."""

    def __init__(self, *, email="ofi@aerotools.es", hd="aerotools.es",
                 refresh_token="refresh-1", expires_in=3600):
        self.email = email
        self.hd = hd
        self.refresh_token = refresh_token
        self.expires_in = expires_in
        self.peticiones: list[dict] = []
        self.refrescos = 0
        self.revocados: list[str] = []
        self.error_en_refresh: int | None = None

    def urlopen(self, req, timeout=None):
        campos = dict(urllib.parse.parse_qsl(req.data.decode()))
        self.peticiones.append(campos)

        if req.full_url == ga.REVOKE_URI:
            self.revocados.append(campos.get("token", ""))
            return _Resp(b"")

        if campos.get("grant_type") == "refresh_token":
            self.refrescos += 1
            if self.error_en_refresh is not None:
                raise urllib.error.HTTPError(
                    ga.TOKEN_URI, self.error_en_refresh, "err", None,
                    io.BytesIO(json.dumps(
                        {"error": "invalid_grant",
                         "error_description": "Token has been expired or revoked."}
                    ).encode()))
            return _Resp(json.dumps({
                "access_token": f"acceso-{self.refrescos}",
                "expires_in": self.expires_in,
            }).encode())

        return _Resp(json.dumps({
            "access_token": "acceso-0",
            "refresh_token": self.refresh_token,
            "expires_in": self.expires_in,
            "id_token": _id_token(self.email, self.hd),
        }).encode())


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _navegador_que_responde(server_holder, *, state_override=None, error=None):
    """Simula al usuario dando su consentimiento: llama al loopback."""

    def _abrir(url: str) -> bool:
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        server_holder["auth_url"] = url
        server_holder["params"] = params
        destino = params["redirect_uri"]
        if error:
            query = urllib.parse.urlencode({"error": error, "state": params["state"]})
        else:
            query = urllib.parse.urlencode({
                "code": "codigo-de-un-solo-uso",
                "state": state_override or params["state"],
            })

        def _llamar():
            # `http.client` y no `urllib.request.urlopen`: ese está sustituido
            # por el Google falso, y la redirección tiene que llegar al
            # servidor de loopback de verdad.
            partes = urllib.parse.urlparse(destino)
            try:
                con = http.client.HTTPConnection(partes.hostname, partes.port, timeout=5)
                con.request("GET", f"/?{query}")
                con.getresponse().read()
                con.close()
            except Exception:  # noqa: BLE001 - el test comprueba el efecto, no esto
                pass

        threading.Thread(target=_llamar, daemon=True).start()
        return True

    return _abrir


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake.urlopen)
    return fake


@pytest.fixture
def auth(tmp_path, google):
    return ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET,
                         hosted_domain="aerotools.es",
                         store_path=tmp_path / "google_auth.json")


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------

def test_login_completo_guarda_la_sesion(auth, google, tmp_path):
    holder: dict = {}

    identidad = auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    assert identidad.email == "ofi@aerotools.es"
    assert auth.is_logged_in()
    # Y sobrevive a cerrar la app: no hay que volver a loguearse cada arranque.
    otro = ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET,
                         store_path=tmp_path / "google_auth.json")
    assert otro.is_logged_in()
    assert otro.identity.email == "ofi@aerotools.es"


def test_el_login_usa_pkce_y_no_manda_el_verifier_al_navegador(auth):
    """Si el `code_verifier` viajase en la URL, PKCE no protegería nada."""
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    params = holder["params"]
    assert params["code_challenge_method"] == "S256"
    assert "code_verifier" not in params
    assert params["response_type"] == "code"


def test_el_verifier_se_manda_en_el_canje_y_cuadra_con_el_challenge(auth, google):
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    canje = google.peticiones[0]
    verifier = canje["code_verifier"]
    esperado = base64.urlsafe_b64encode(
        __import__("hashlib").sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert esperado == holder["params"]["code_challenge"]


def test_redirect_uri_es_loopback_y_no_un_dominio(auth):
    """RFC 8252: el `code` vuelve a esta máquina, no a un servidor de nadie."""
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)
    assert holder["params"]["redirect_uri"].startswith("http://127.0.0.1:")


def test_pide_refresh_token_explicitamente(auth):
    """Sin `access_type=offline` Google no lo da, y la sesión moriría en 1 h."""
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)
    assert holder["params"]["access_type"] == "offline"
    assert holder["params"]["prompt"] == "consent"


def test_una_respuesta_con_state_ajeno_se_rechaza(auth):
    holder: dict = {}
    with pytest.raises(ga.AuthError, match="no corresponde"):
        auth.login(open_browser=_navegador_que_responde(holder, state_override="otro"),
                   timeout=10)
    assert not auth.is_logged_in()


def test_si_el_usuario_cancela_no_queda_sesion(auth):
    holder: dict = {}
    with pytest.raises(ga.AuthError, match="rechazó"):
        auth.login(open_browser=_navegador_que_responde(holder, error="access_denied"),
                   timeout=10)
    assert not auth.is_logged_in()


def test_el_login_no_espera_indefinidamente(auth):
    """Si el usuario cierra el navegador, la app no puede quedarse colgada."""
    inicio = time.monotonic()
    with pytest.raises(ga.AuthError, match="tiempo"):
        auth.login(open_browser=lambda _url: True, timeout=1)
    assert time.monotonic() - inicio < 5


# --------------------------------------------------------------------------
# Restricción de dominio
# --------------------------------------------------------------------------

def test_una_cuenta_de_otro_dominio_no_entra(tmp_path, monkeypatch):
    """`hd` en la URL es solo una sugerencia de UI: la puerta la cierra esto."""
    fake = FakeGoogle(email="cualquiera@gmail.com", hd=None)
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake.urlopen)
    auth = ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET, hosted_domain="aerotools.es",
                         store_path=tmp_path / "s.json")

    holder: dict = {}
    with pytest.raises(ga.AuthError, match="no es de aerotools.es"):
        auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    assert not auth.is_logged_in()
    assert not (tmp_path / "s.json").exists()


def test_sin_hosted_domain_entra_cualquier_cuenta(tmp_path, monkeypatch):
    fake = FakeGoogle(email="cualquiera@gmail.com", hd=None)
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake.urlopen)
    auth = ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET, store_path=tmp_path / "s.json")

    holder: dict = {}
    assert auth.login(open_browser=_navegador_que_responde(holder),
                      timeout=10).email == "cualquiera@gmail.com"


def test_el_hint_hd_se_manda_para_ahorrarle_el_selector_al_usuario(auth):
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)
    assert holder["params"]["hd"] == "aerotools.es"


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

def test_el_token_se_reutiliza_mientras_sea_valido(auth, google):
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    assert auth.access_token() == "acceso-0"
    assert auth.access_token() == "acceso-0"
    assert google.refrescos == 0  # no se pide uno nuevo por gusto


def test_el_token_se_refresca_solo_al_caducar(tmp_path, monkeypatch):
    """El caso que hace viable una subida de horas: el token vive 1 h."""
    fake = FakeGoogle(expires_in=1)
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake.urlopen)
    auth = ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET, store_path=tmp_path / "s.json")

    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    # `expires_in=1` está dentro del margen de seguridad → ya se considera muerto.
    assert auth.access_token() == "acceso-1"
    assert fake.refrescos == 1


def test_force_refresh_pide_uno_nuevo_aunque_el_actual_valga(auth, google):
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    assert auth.access_token() == "acceso-0"
    assert auth.access_token(force_refresh=True) == "acceso-1"
    assert google.refrescos == 1


def test_sin_sesion_el_token_falla_con_un_mensaje_util(tmp_path):
    auth = ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET, store_path=tmp_path / "s.json")
    with pytest.raises(ga.AuthError, match="No hay sesión iniciada"):
        auth.access_token()


def test_si_el_usuario_revoca_el_acceso_se_olvida_la_sesion(tmp_path, monkeypatch):
    """`invalid_grant`: seguir intentando con ese refresh token no arregla nada,
    y dejarlo guardado haría que la app pareciera logueada sin estarlo."""
    fake = FakeGoogle(expires_in=1)
    monkeypatch.setattr(ga.urllib.request, "urlopen", fake.urlopen)
    store = tmp_path / "s.json"
    auth = ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET, store_path=store)

    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)
    fake.error_en_refresh = 400

    with pytest.raises(ga.AuthError, match="Vuelve a iniciar sesión"):
        auth.access_token()

    assert not auth.is_logged_in()
    assert not store.exists()


def test_logout_borra_y_revoca(auth, google, tmp_path):
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)

    auth.logout()

    assert not auth.is_logged_in()
    assert not auth.store_path.exists()
    assert google.revocados == ["refresh-1"]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="permisos POSIX")
def test_el_refresh_token_no_queda_legible_para_todo_el_mundo(auth):
    holder: dict = {}
    auth.login(open_browser=_navegador_que_responde(holder), timeout=10)
    assert oct(os.stat(auth.store_path).st_mode & 0o777) == "0o600"


def test_un_store_corrupto_no_impide_arrancar(tmp_path):
    """Peor caso aceptable: pedir login otra vez. Inaceptable: no abrir."""
    store = tmp_path / "s.json"
    store.write_text("{roto", encoding="utf-8")
    auth = ga.GoogleAuth(CLIENT_ID, CLIENT_SECRET, store_path=store)
    assert not auth.is_logged_in()


def test_client_id_vacio_es_un_error_de_programacion(tmp_path):
    with pytest.raises(ValueError):
        ga.GoogleAuth("", CLIENT_SECRET, store_path=tmp_path / "s.json")


# --------------------------------------------------------------------------
# Integración con la subida
# --------------------------------------------------------------------------

class _AuthFalso:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.emitidos: list[str] = []

    def access_token(self, *, force_refresh: bool = False):
        if force_refresh and len(self.tokens) > 1:
            self.tokens.pop(0)
        self.emitidos.append(self.tokens[0])
        return self.tokens[0]


def test_el_provider_pone_el_bearer_en_cada_peticion():
    prov = cu.GcsOAuthProvider("aerotools-vuelos", _AuthFalso(["tok-1"]))
    assert prov.headers() == {"Authorization": "Bearer tok-1"}
    assert prov.upload_url("vuelos/a/DJI_0001_T.JPG", 10) == (
        "https://storage.googleapis.com/aerotools-vuelos/vuelos/a/DJI_0001_T.JPG")


def test_el_provider_escapa_nombres_con_espacios():
    prov = cu.GcsOAuthProvider("b", _AuthFalso(["t"]))
    assert prov.upload_url("vuelos/mi vuelo/a b.jpg", 1) == (
        "https://storage.googleapis.com/b/vuelos/mi%20vuelo/a%20b.jpg")


def test_el_provider_exige_bucket():
    with pytest.raises(ValueError):
        cu.GcsOAuthProvider("", _AuthFalso(["t"]))


def test_un_token_caducado_a_mitad_no_pierde_los_bytes_ya_subidos(tmp_path, monkeypatch):
    """Con OAuth, el 403 se arregla refrescando: la sesión resumable sigue viva
    y GCS conserva lo confirmado. Reabrirla resubiría el fichero entero."""
    from tests.test_cloud_upload import FakeGCS, _vuelo

    server = FakeGCS(expire_after=2)
    monkeypatch.setattr(cu.urllib.request, "urlopen", server.urlopen)
    monkeypatch.setattr(cu, "CHUNK_SIZE", 1024)
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)

    fake_auth = _AuthFalso(["tok-viejo", "tok-nuevo"])
    prov = cu.GcsOAuthProvider("b", fake_auth)

    root = _vuelo(tmp_path, {"a.jpg": b"z" * 3000})
    item = cu.build_plan(root).items[0]

    cu.upload_file(item, prov)

    # FakeGCS nombra el objeto por el último segmento de la URL.
    assert server.objects["a.jpg"] == b"z" * 3000
    assert server.opened == 1          # la sesión NO se reabrió
    assert "tok-nuevo" in fake_auth.emitidos  # pero el token sí se renovó
