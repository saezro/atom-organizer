"""Login con Google desde la app de escritorio (OAuth 2.0, flujo de app instalada).

Sustituye al backend firmador de URLs: en vez de mantener un servicio propio que
firme cada subida, el usuario se identifica con su cuenta de Google y **los
permisos los decide IAM en el bucket**. Quitar acceso a alguien es sacarlo del
grupo de Google; no hay que tocar la app ni publicar versión.

Cómo funciona el flujo (RFC 8252, "OAuth for Native Apps"):

1. La app levanta un servidor HTTP efímero en `127.0.0.1:<puerto libre>`.
2. Abre el navegador del sistema en la pantalla de consentimiento de Google.
   El navegador del sistema, no uno embebido: es donde el usuario ya tiene la
   sesión iniciada y donde puede comprobar que la URL es de verdad de Google.
3. Google redirige a ese loopback con un `code`, que la app canjea por tokens.

**El `client_secret` viaja dentro del ejecutable y se puede extraer.** No es un
descuido: Google lo da por público en apps instaladas (no hay forma de esconder
un secreto en binario que corre en casa del usuario). Lo que impide que sirva de
nada es **PKCE** — el `code` solo se canjea presentando el `code_verifier` que
generó *esta* ejecución — más el hecho de que sin una cuenta con permiso IAM el
token no abre ninguna puerta.

`hosted_domain` cierra la puerta a cuentas ajenas: se manda como `hd` a Google y,
además, **se vuelve a comprobar sobre el `id_token` recibido**. El parámetro `hd`
por sí solo es una sugerencia de UI, no una garantía; la comprobación de verdad
es la de vuelta.

La sesión (el `refresh_token`) se guarda **cifrada en una BD local**; el cómo
está en `session_store.py`, no aquí.

Solo stdlib, misma razón que en `updater.py` y `cloud_upload.py`: no engordar el
bundle de PyInstaller con `google-auth` y sus dependencias.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .host_env import abrir_en_navegador
from .session_store import LEGACY_STORE_NAME, STORE_NAME, SessionStore

__all__ = [
    "GoogleAuth",
    "AuthError",
    "Identity",
    "SCOPE_STORAGE_RW",
    "user_data_dir",
]

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"

# No existe un scope "solo escribir" en Cloud Storage: `read_write` es el mínimo
# práctico. Quien acota de verdad es IAM — con `roles/storage.objectCreator`
# sobre el bucket, este mismo token puede crear objetos y nada más.
SCOPE_STORAGE_RW = "https://www.googleapis.com/auth/devstorage.read_write"
SCOPES = ("openid", "email", SCOPE_STORAGE_RW)

# Margen antes de dar un token por caducado. Una subida de un fichero grande
# puede empezar con 20 s de vida por delante y morir a mitad.
EXPIRY_MARGIN = 120

TIMEOUT = 30
LOGIN_TIMEOUT = 300  # 5 min para completar el consentimiento en el navegador


class AuthError(RuntimeError):
    """Fallo de autenticación que el usuario puede entender y corregir."""


@dataclass
class Identity:
    email: str
    domain: str

    def __str__(self) -> str:  # lo que se enseña en la UI
        return self.email


def user_data_dir() -> Path:
    """Misma base que `external_tools._user_config_path`, sin importarlo.

    `atom_core` es el core headless y no depende del módulo de la GUI; se
    replica el criterio (perfil del usuario, nunca `_MEIPASS`, que bajo el
    onefile de Windows se borra al cerrar la app).
    """
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", os.path.expanduser("~"))) / "ATOM-Organizer"
    else:
        base = Path(os.path.expanduser("~")) / ".config" / "atom-organizer"
    return base


# --------------------------------------------------------------------------
# Servidor de loopback que recoge el `code`
# --------------------------------------------------------------------------

_PAGINA_OK = b"""<!doctype html><meta charset="utf-8">
<title>ATOM Organizer</title>
<body style="font-family:system-ui;text-align:center;padding-top:4rem">
<h2>Sesion iniciada</h2><p>Ya puedes volver a ATOM Organizer.</p></body>"""

_PAGINA_ERR = b"""<!doctype html><meta charset="utf-8">
<title>ATOM Organizer</title>
<body style="font-family:system-ui;text-align:center;padding-top:4rem">
<h2>No se pudo iniciar sesion</h2><p>Vuelve a ATOM Organizer e intentalo de nuevo.</p></body>"""


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Recoge una sola petición: la redirección de Google con el `code`."""

    def do_GET(self):  # noqa: N802 - lo impone BaseHTTPRequestHandler
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.resultado = {k: v[0] for k, v in params.items()}  # type: ignore[attr-defined]
        ok = "code" in self.server.resultado  # type: ignore[attr-defined]
        cuerpo = _PAGINA_OK if ok else _PAGINA_ERR
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_args):  # el servidor no debe ensuciar la consola
        pass


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------
# Cliente OAuth
# --------------------------------------------------------------------------

class GoogleAuth:
    """Mantiene la sesión de Google del usuario y entrega access tokens.

    El `refresh_token` se guarda en el perfil del usuario para no obligar a
    repetir el login en cada arranque, **cifrado** en la BD local que gestiona
    `session_store` (DPAPI en Windows, keyfile 0600 en Linux). El permiso de
    verdad sigue estando en IAM: esto acota el daño de que el fichero se pasee
    por un backup, no sustituye al control de acceso.
    """

    # Se reexpone como atributo de clase porque `cloud_upload` y los tests lo
    # usan como «el nombre del almacén»; el valor lo manda `session_store`.
    STORE_NAME = STORE_NAME  # noqa: PLW0127 - alias deliberado del módulo

    def __init__(self, client_id: str, client_secret: str, *,
                 hosted_domain: str | None = None,
                 scopes: tuple[str, ...] = SCOPES,
                 store_path: Path | None = None,
                 store: SessionStore | None = None):
        if not client_id:
            raise ValueError("client_id vacío")
        self.client_id = client_id
        self.client_secret = client_secret
        self.hosted_domain = hosted_domain
        self.scopes = tuple(scopes)
        self.store_path = Path(store_path) if store_path else user_data_dir() / self.STORE_NAME
        self._store = store if store is not None else SessionStore(self.store_path)

        # Reentrante a propósito: `access_token` sostiene el lock mientras llama
        # a `_token_request`, y esa, al ver un `invalid_grant`, vuelve a tomarlo
        # para borrar la sesión. Con un Lock normal eso es un deadlock — la app
        # se queda colgada para siempre justo cuando el usuario revoca el acceso.
        self._lock = threading.RLock()
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._refresh_token: str | None = None
        # No se persiste a propósito: vive lo mismo que el access token (1 h) y
        # guardarlo en disco añadiría un secreto en reposo sin ganar nada, porque
        # al arrancar habría caducado igual. Se obtiene del refresh, que devuelve
        # `id_token` mientras el scope `openid` siga concedido.
        self._id_token: str | None = None
        self._identity: Identity | None = None
        self._validada_en: float | None = None
        self._aviso_store: str | None = None
        self._load()

    # -- estado persistido ------------------------------------------------
    def _load(self) -> None:
        sesion = self._store.leer()
        if sesion is None:
            # Sin sesión en la BD: puede ser un perfil que viene de la versión
            # anterior, con el JSON en claro. Se importa una sola vez y el
            # fichero se retira; el operador no nota el cambio de formato.
            #
            # Se intenta **aunque la BD haya dado error**: si no, un `session.db`
            # dañado dejaría la sesión heredada sin migrar para siempre, con el
            # JSON bueno ahí al lado y ninguna forma de recuperarlo desde la app.
            aviso_lectura = self._store.error_lectura
            if self._migrar_legacy():
                sesion = self._store.leer()
            if sesion is None:
                # El aviso de la migración, si lo hubo, manda sobre el de lectura:
                # es el más cercano a lo que el operador acaba de perder.
                if self._aviso_store is None:
                    self._aviso_store = aviso_lectura
                return
        self._refresh_token = sesion.refresh_token
        self._validada_en = sesion.validada_en
        if sesion.email:
            self._identity = Identity(email=sesion.email,
                                      domain=sesion.email.rsplit("@", 1)[-1])

    def _migrar_legacy(self) -> bool:
        """Importa el `google_auth.json` de versiones anteriores, si lo hay.

        Nada de lo que pase aquí puede impedir que la app abra: `_load` corre
        dentro del constructor, y una excepción aquí dejaría `cloud_status` —y
        con él la pestaña entera— lanzando un traceback en cada intento. El
        peor caso aceptable es pedir login otra vez.
        """
        legacy = self.store_path.parent / LEGACY_STORE_NAME
        # `store_path` es configurable (tests, instalaciones a medida): si
        # apuntara al propio nombre heredado, migrar sería leerse a sí mismo.
        if legacy == self.store_path or not legacy.exists():
            return False
        try:
            return self._store.importar_legacy(legacy)
        except Exception as exc:  # noqa: BLE001 - disco lleno, permisos, DPAPI…
            self._aviso_store = (
                "No se pudo recuperar la sesión anterior de este equipo "
                f"({exc}). Inicia sesión de nuevo.")
            return False

    def _save(self) -> None:
        if not self._refresh_token:
            return
        self._store.guardar(self._identity.email if self._identity else None,
                            self._refresh_token)

    def _olvidar_local(self) -> None:
        """Tira la sesión de memoria y de disco. No habla con Google."""
        self._refresh_token = None
        self._access_token = None
        self._id_token = None
        self._expires_at = 0.0
        self._validada_en = None
        self._store.borrar()

    # -- consultas --------------------------------------------------------
    @property
    def identity(self) -> Identity | None:
        return self._identity

    def is_logged_in(self) -> bool:
        """Hay sesión recordada. No garantiza que siga siendo válida: eso solo
        se sabe al usarla (el usuario pudo revocar el acceso desde su cuenta)."""
        return bool(self._refresh_token)

    @property
    def validada_en(self) -> float | None:
        """Cuándo se comprobó por última vez contra Google que la sesión vive."""
        return self._validada_en

    @property
    def aviso_store(self) -> str | None:
        """Problema del almacén local que el usuario debería ver, si lo hay.

        Caso típico: perfil copiado a otro equipo, donde DPAPI ya no puede
        descifrar. La sesión se descarta sola, pero conviene decir por qué en
        vez de dejar un «sin iniciar sesión» inexplicable.
        """
        return self._aviso_store

    def verificar(self) -> tuple[bool, str]:
        """¿Sigue viva la sesión? Pregunta a Google — hace red.

        Devuelve `(válida, mensaje)`. Es lo que convierte «hay un token
        guardado» en «tu sesión funciona», que es lo que el operador quiere
        saber antes de empezar una subida de dos horas.
        """
        if not self._refresh_token:
            return False, "No hay sesión iniciada."
        try:
            self.access_token(force_refresh=True)
        except AuthError as exc:
            return False, str(exc)
        with self._lock:
            # El refresh suelta el lock al terminar, así que entre medias el
            # usuario ha podido darle a «Cerrar sesión». Decir «sesión válida»
            # de algo que ya no existe dejaría la UI mintiendo hasta el
            # siguiente refresco.
            if not self._refresh_token:
                return False, "No hay sesión iniciada."
            self._validada_en = time.time()
        try:
            self._store.marcar_validada(self._validada_en)
        except Exception:  # noqa: BLE001 - anotar la fecha es cortesía, no requisito
            pass
        return True, "Sesión válida."

    # -- login ------------------------------------------------------------
    def login(self, *, open_browser=abrir_en_navegador,
              timeout: int = LOGIN_TIMEOUT) -> Identity:
        """Abre el navegador, espera el consentimiento y guarda la sesión."""
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)

        puerto = _puerto_libre()
        redirect_uri = f"http://127.0.0.1:{puerto}"

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            # `consent` + `offline` para que Google entregue refresh_token
            # también cuando el usuario ya habia autorizado antes la app.
            "access_type": "offline",
            "prompt": "consent",
        }
        if self.hosted_domain:
            params["hd"] = self.hosted_domain

        servidor = http.server.HTTPServer(("127.0.0.1", puerto), _CallbackHandler)
        servidor.resultado = None  # type: ignore[attr-defined]
        servidor.timeout = timeout

        hilo = threading.Thread(target=servidor.handle_request, daemon=True)
        hilo.start()
        try:
            open_browser(f"{AUTH_URI}?{urllib.parse.urlencode(params)}")
            hilo.join(timeout)
            resultado = servidor.resultado  # type: ignore[attr-defined]
        finally:
            servidor.server_close()

        if not resultado:
            raise AuthError("Se agotó el tiempo esperando el inicio de sesión.")
        if "error" in resultado:
            raise AuthError(f"Google rechazó el inicio de sesión: {resultado['error']}")
        if resultado.get("state") != state:
            # Otra pestaña o algo peor apuntando a nuestro loopback.
            raise AuthError("La respuesta de Google no corresponde a esta petición.")

        tokens = self._token_request({
            "code": resultado["code"],
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        })

        identidad = _identidad_de_id_token(tokens.get("id_token", ""))
        if identidad is None:
            raise AuthError("Google no devolvió la identidad de la cuenta.")
        if self.hosted_domain and identidad.domain.lower() != self.hosted_domain.lower():
            raise AuthError(
                f"La cuenta {identidad.email} no es de {self.hosted_domain}. "
                "Inicia sesión con tu cuenta corporativa.")

        refresh = tokens.get("refresh_token")
        if not refresh:
            raise AuthError("Google no entregó refresh token; revisa el cliente OAuth.")

        with self._lock:
            self._refresh_token = refresh
            self._identity = identidad
            self._aviso_store = None
            self._aplicar_access(tokens)
            # Acabamos de canjear el código: la sesión está viva por definición,
            # y anotarlo evita que la UI salga preguntando a Google otra vez
            # justo después de entrar.
            self._validada_en = time.time()
            self._save()
            try:
                self._store.marcar_validada(self._validada_en)
            except Exception:  # noqa: BLE001
                pass
        return identidad

    def logout(self) -> None:
        """Olvida la sesión local y revoca el refresh token en Google."""
        with self._lock:
            token = self._refresh_token
            self._identity = None
            self._aviso_store = None
            self._olvidar_local()
        if token:
            try:
                self._post(REVOKE_URI, {"token": token})
            except Exception:  # noqa: BLE001 - revocar es cortesía, no requisito
                pass

    # -- tokens -----------------------------------------------------------
    def access_token(self, *, force_refresh: bool = False) -> str:
        """Token válido para llamar a la API de Cloud Storage.

        Se refresca solo. Es lo que hace viable una subida de horas: el token
        de acceso de Google vive 1 h, bastante menos que un vuelo grande.
        """
        with self._lock:
            vigente = (self._access_token
                       and time.time() < self._expires_at - EXPIRY_MARGIN)
            if vigente and not force_refresh:
                return self._access_token  # type: ignore[return-value]
            if not self._refresh_token:
                raise AuthError("No hay sesión iniciada. Inicia sesión con Google.")

            tokens = self._token_request({
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            })
            self._aplicar_access(tokens)
            return self._access_token  # type: ignore[return-value]

    def id_token(self) -> str:
        """`id_token` vigente de Google, para autenticarse ante ATOM Suite.

        El backend (`GET /api/organizer/inspecciones`) verifica este JWT contra
        las claves públicas de Google y exige `hd=aerotools.es`. Es lo que evita
        meter un secreto de larga vida en un `.exe` público: el id_token caduca
        en 1 h y se renueva con el mismo refresh que ya usa la subida.

        Se pide junto al access token: el grant de refresh devuelve ambos
        mientras el scope `openid` siga concedido, así que renovarlo no cuesta
        una llamada de red extra.
        """
        with self._lock:
            vigente = (self._id_token
                       and time.time() < self._expires_at - EXPIRY_MARGIN)
            if vigente:
                return self._id_token  # type: ignore[return-value]
        # Fuera del `with` no por el lock (es reentrante), sino porque
        # `access_token` ya sabe pedir y guardar ambos tokens: duplicar aquí la
        # llamada a `_token_request` sería una segunda implementación del
        # refresh, con su propio manejo de `invalid_grant`.
        self.access_token(force_refresh=True)
        with self._lock:
            if not self._id_token:
                # Sesión iniciada antes de que la app pidiera `openid`, o scope
                # retirado en la cuenta de Google. Se arregla volviendo a entrar.
                raise AuthError(
                    "Google no devolvió identidad (id_token). "
                    "Cierra sesión y vuelve a iniciarla con Google.")
            return self._id_token

    def _aplicar_access(self, tokens: dict) -> None:
        token = tokens.get("access_token")
        if not token:
            raise AuthError("Google no devolvió access token.")
        self._access_token = token
        # Se reasigna siempre, también a None: un id_token de la tanda anterior
        # caduca a la vez que el access token que sustituimos, así que dejarlo
        # colgado sería servir un JWT muerto que el backend rechaza con 401.
        self._id_token = tokens.get("id_token") or None
        self._expires_at = time.time() + float(tokens.get("expires_in", 3600))

    # -- HTTP -------------------------------------------------------------
    def _token_request(self, campos: dict) -> dict:
        try:
            return self._post(TOKEN_URI, campos)
        except urllib.error.HTTPError as exc:
            detalle = _mensaje_de_error(exc)
            if exc.code in (400, 401):
                # `invalid_grant` = el usuario revocó el acceso o cambió la
                # contraseña. La sesión guardada ya no sirve para nada.
                with self._lock:
                    self._olvidar_local()
                raise AuthError(
                    f"La sesión de Google ya no es válida ({detalle}). "
                    "Vuelve a iniciar sesión.") from exc
            raise AuthError(f"Google devolvió un error ({exc.code}): {detalle}") from exc

    @staticmethod
    def _post(url: str, campos: dict) -> dict:
        datos = urllib.parse.urlencode(campos).encode()
        req = urllib.request.Request(
            url, data=datos, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            cuerpo = resp.read().decode()
        return json.loads(cuerpo) if cuerpo else {}


def _mensaje_de_error(exc: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(exc.read().decode())
        return data.get("error_description") or data.get("error") or str(exc.code)
    except Exception:  # noqa: BLE001
        return str(exc.code)


def _identidad_de_id_token(id_token: str) -> Identity | None:
    """Lee el email del `id_token`.

    **No se valida la firma a propósito.** El token llega por TLS directamente
    del endpoint de Google, en respuesta a una petición que sale de aquí: no hay
    intermediario que lo pueda falsificar, y verificar la firma exigiría traerse
    las claves públicas de Google y una librería de JWT. La comprobación de
    dominio que se hace encima es defensa contra el usuario equivocándose de
    cuenta, no contra un atacante en el canal.
    """
    partes = id_token.split(".")
    if len(partes) != 3:
        return None
    payload = partes[1]
    payload += "=" * (-len(payload) % 4)  # base64url sin padding
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload).decode())
    except Exception:  # noqa: BLE001
        return None
    email = claims.get("email")
    if not email:
        return None
    return Identity(email=email, domain=claims.get("hd") or email.rsplit("@", 1)[-1])
