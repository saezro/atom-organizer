import json
import threading
import urllib.error
import urllib.request

import pytest

from atom_core import pin_kiosco, webserver
from atom_core.event_sink import QueueSink
from atom_core.webserver import crear_servidor

PIN_CABECERA = webserver._CABECERA_CONTROL_PIN


class _FakeStorePin:
    """Sustituye a `SessionStore`: solo necesita `meta_get`/`meta_set`."""

    def __init__(self):
        self._meta = {}

    def meta_get(self, clave):
        return self._meta.get(clave)

    def meta_set(self, clave, valor):
        self._meta[clave] = valor


class _ApiControl:
    def __init__(self):
        self._store = _FakeStorePin()
        self._running = False
        self.discos = [{"name": "USB1", "path": "/media/usb1", "libre_gb": 10, "total_gb": 20}]
        self.carpeta_resultado_ok = {
            "ok": True, "dirs": [{"name": "sub", "path": "/media/usb1/vuelo/sub"}], "files": [],
            "is_root": False, "disk_name": "USB1", "rel_parts": ["vuelo"],
        }
        self.espera_carpeta_llamadas = []
        self.run_organize_llamadas = []
        self.run_organize_resultado = {"started": True}
        self.analisis_cancel_llamadas = 0
        self.estadillo_en_carpeta_resultado = {"encontrado": True, "nombre": "estadillo.csv", "buscando": False}
        # Estado unico de "carpeta de trabajo" (ver `Api.carpeta_trabajo_fijar`
        # real en `app_webview.py`): esta version fake replica la misma forma
        # (fijar reusa `estadillo_espera_carpeta`) para que el handler HTTP
        # trate a esta fake exactamente igual que a la `Api` real.
        self._carpeta_trabajo = None
        # `estadillos_detectar`/`estadillo_espera_estado`: por defecto sin
        # estadillo detectado ni espera activa; cada test ajusta lo que
        # necesite.
        self.estadillos_detectar_resultado = {"rutas": [], "n_estadillos": 0, "info": None, "error": None}
        self.estadillo_espera_estado_resultado = {"esperando": False, "caducado": False}
        # Ultimo evento de progreso/fase/error (`Api._push` real): cada test
        # de `/estado` lo ajusta a mano, como si un run hubiera emitido algo.
        self._control_fase = None
        self._control_progreso = None
        self._control_ultimo_error = None

    def _store_pin(self):
        return self._store

    def list_dir(self, path=None):
        assert path is None
        return {"ok": True, "path": None, "dirs": self.discos, "files": []}

    def _list_dir_confinado_pi(self, path):
        if path is None:
            return {"ok": True, "path": None, "dirs": self.discos, "files": []}
        if path == "/fuera/de/discos":
            return {"ok": False, "error": "Fuera de los discos externos montados."}
        return {**self.carpeta_resultado_ok, "path": path}

    def estadillo_espera_carpeta(self, carpeta):
        self.espera_carpeta_llamadas.append(carpeta)
        return {"ok": True}

    def carpeta_trabajo_fijar(self, path):
        self._carpeta_trabajo = path or None
        self.estadillo_espera_carpeta(self._carpeta_trabajo)
        return {"ok": True, "carpeta": self._carpeta_trabajo}

    def _carpeta_trabajo_actual(self):
        return self._carpeta_trabajo

    def estadillos_detectar(self, carpeta):
        return self.estadillos_detectar_resultado

    def estadillo_espera_estado(self):
        return self.estadillo_espera_estado_resultado

    def run_organize(self, params, advanced):
        self.run_organize_llamadas.append((params, advanced))
        return self.run_organize_resultado

    def analisis_cancel(self):
        self.analisis_cancel_llamadas += 1
        return {"ok": True}

    def _estadillo_en_carpeta(self, carpeta):
        return self.estadillo_en_carpeta_resultado


@pytest.fixture
def servidor(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head>ATOM UI</html>", encoding="utf-8")
    api = _ApiControl()
    sink = QueueSink()
    srv = crear_servidor(api, str(tmp_path), "127.0.0.1", 0, sink)
    srv.sink = sink  # expuesto solo para que los tests suscriban la cola SSE
    hilo = threading.Thread(target=srv.serve_forever, daemon=True)
    hilo.start()
    yield srv, api, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _reset_control_pin_lockout():
    webserver._control_pin_fallos.clear()
    webserver._control_pin_bloqueada_hasta.clear()
    yield
    webserver._control_pin_fallos.clear()
    webserver._control_pin_bloqueada_hasta.clear()


def _get(base, ruta, headers=None):
    req = urllib.request.Request(f"{base}{ruta}", headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def _post_json(base, ruta, payload, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(
        f"{base}{ruta}", data=json.dumps(payload).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read()), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def _fijar_pin(api, pin="1234"):
    pin_kiosco.fijar(api._store, pin)


# ---- autenticacion por PIN -------------------------------------------------

def test_sin_pin_configurado_da_403(servidor):
    _, api, base = servidor
    status, body, headers = _get(base, "/api/control/discos", {PIN_CABECERA: "1234"})
    assert status == 403
    assert body == {"ok": False, "codigo": "pin_no_configurado"}
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_sin_cabecera_pin_da_401(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _get(base, "/api/control/discos")
    assert status == 401
    assert body == {"ok": False, "codigo": "pin_invalido"}


def test_pin_incorrecto_da_401(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _get(base, "/api/control/discos", {PIN_CABECERA: "0000"})
    assert status == 401
    assert body == {"ok": False, "codigo": "pin_invalido"}


def test_5_fallos_bloquea_60s(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    for _ in range(5):
        status, body, _ = _get(base, "/api/control/discos", {PIN_CABECERA: "0000"})
        assert status == 401
    status, body, _ = _get(base, "/api/control/discos", {PIN_CABECERA: "0000"})
    assert status == 429
    assert body == {"ok": False, "codigo": "pin_bloqueado"}
    # Incluso con el PIN correcto, sigue bloqueada mientras dure la espera.
    status, body, _ = _get(base, "/api/control/discos", {PIN_CABECERA: "1234"})
    assert status == 429
    assert body["codigo"] == "pin_bloqueado"
    assert webserver._control_pin_espera_segundos("127.0.0.1") == pytest.approx(60, abs=1)


# ---- GET /api/control/discos -----------------------------------------------

def test_discos_happy_path(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, headers = _get(base, "/api/control/discos", {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {"discos": api.discos}
    assert headers["Access-Control-Allow-Origin"] == "*"


def test_discos_get_emite_evento_atom_control_ui_api_remota(servidor):
    """No solo las acciones POST (login/carpeta/organizar/cancelar): CUALQUIER
    peticion a `/api/control/*`, incluidas las GET de solo lectura, enciende
    el marco azul del kiosco (`App.jsx` `encenderControlRemoto`)."""
    srv, api, base = servidor
    _fijar_pin(api)
    q = srv.sink.subscribe()
    status, _, _ = _get(base, "/api/control/discos", {PIN_CABECERA: "1234"})
    assert status == 200
    evento, detalle = q.get(timeout=5)
    assert evento == "atom:control_ui"
    assert detalle == {"accion": "api_remota"}


# ---- GET /api/control/carpetas ---------------------------------------------

def test_carpetas_happy_path(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _get(base, "/api/control/carpetas?path=/media/usb1/vuelo", {PIN_CABECERA: "1234"})
    assert status == 200
    assert body["ok"] is True
    assert body["dirs"] == api.carpeta_resultado_ok["dirs"]


def test_carpetas_path_fuera_da_400(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _get(base, "/api/control/carpetas?path=/fuera/de/discos", {PIN_CABECERA: "1234"})
    assert status == 400
    assert body["codigo"] == "path_no_permitido"


# ---- POST /api/control/carpeta ---------------------------------------------

def test_fijar_carpeta_happy_path(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"},
                                  {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {"ok": True, "path": "/media/usb1/vuelo"}
    assert api.espera_carpeta_llamadas == ["/media/usb1/vuelo"]


def test_fijar_carpeta_fuera_da_400(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _post_json(base, "/api/control/carpeta", {"path": "/fuera/de/discos"},
                                  {PIN_CABECERA: "1234"})
    assert status == 400
    assert body["codigo"] == "path_no_permitido"
    assert api.espera_carpeta_llamadas == []


# ---- POST /api/control/organizar -------------------------------------------

def test_organizar_sin_carpeta_da_409(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _post_json(base, "/api/control/organizar", {}, {PIN_CABECERA: "1234"})
    assert status == 409
    assert body == {"ok": False, "codigo": "sin_carpeta"}


def test_organizar_happy_path_sin_estadillo(servidor):
    """Sin estadillo detectado en la carpeta ni espera LAN pendiente, organiza
    igual que el kiosco cuando no hay estadillo: string vacio, nunca `[]`."""
    _, api, base = servidor
    _fijar_pin(api)
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    status, body, _ = _post_json(base, "/api/control/organizar", {}, {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {"ok": True, "started": True}
    assert len(api.run_organize_llamadas) == 1
    params, advanced = api.run_organize_llamadas[0]
    assert params == {
        "origen": "/media/usb1/vuelo",
        "destino": "/media/usb1/vuelo_ORGANIZADO",
        "estadillo": "",
        "rename": True,
    }
    assert advanced is None


def test_organizar_pasa_estadillo_detectado_en_carpeta(servidor):
    """El estadillo YA presente en la carpeta (lo que veria el kiosco via
    `estadillos_detectar`) viaja como el path empaquetado, no vacio."""
    _, api, base = servidor
    _fijar_pin(api)
    api.estadillos_detectar_resultado = {
        "rutas": ["/media/usb1/vuelo/estadillo.csv"], "n_estadillos": 1, "info": {}, "error": None,
    }
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    status, body, _ = _post_json(base, "/api/control/organizar", {}, {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {"ok": True, "started": True}
    params, _advanced = api.run_organize_llamadas[0]
    assert params["estadillo"] == "/media/usb1/vuelo/estadillo.csv"


def test_organizar_pasa_estadillo_recibido_por_lan_pendiente_de_mover(servidor):
    """Estadillo recibido por LAN (modo espera) pero aun no movido a la
    carpeta (`estadillos_detectar` no lo ve): se incluye igualmente, para que
    `_mover_estadillo_espera_si_toca` lo encuentre al arrancar el run."""
    _, api, base = servidor
    _fijar_pin(api)
    api.estadillos_detectar_resultado = {"rutas": [], "n_estadillos": 0, "info": None, "error": None}
    api.estadillo_espera_estado_resultado = {
        "esperando": True, "caducado": False, "recibido": True,
        "carpeta": "/media/usb1/vuelo", "rutas": ["/tmp/estadillos_recibidos/estadillo.csv"],
    }
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    status, body, _ = _post_json(base, "/api/control/organizar", {}, {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {"ok": True, "started": True}
    params, _advanced = api.run_organize_llamadas[0]
    assert params["estadillo"] == "/tmp/estadillos_recibidos/estadillo.csv"


def test_organizar_ignora_estadillo_lan_pendiente_de_otra_carpeta(servidor):
    """La espera LAN es de OTRA carpeta: no se cuela su ruta en este run."""
    _, api, base = servidor
    _fijar_pin(api)
    api.estadillo_espera_estado_resultado = {
        "esperando": True, "caducado": False, "recibido": True,
        "carpeta": "/media/usb1/otro_vuelo", "rutas": ["/tmp/estadillos_recibidos/estadillo.csv"],
    }
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    status, body, _ = _post_json(base, "/api/control/organizar", {}, {PIN_CABECERA: "1234"})
    assert status == 200
    params, _advanced = api.run_organize_llamadas[0]
    assert params["estadillo"] == ""


def test_organizar_en_curso_da_409(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    api.run_organize_resultado = {"started": False, "reason": "Ya hay un proceso en curso."}
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    status, body, _ = _post_json(base, "/api/control/organizar", {}, {PIN_CABECERA: "1234"})
    assert status == 409
    assert body == {"ok": False, "codigo": "en_curso"}


# ---- GET /api/control/estado -----------------------------------------------

def test_estado_happy_path(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    api._running = True
    status, body, _ = _get(base, "/api/control/estado", {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {
        "carpeta": "/media/usb1/vuelo",
        "estadillo": api.estadillo_en_carpeta_resultado,
        "en_curso": True,
        "fase": None,
        "progreso": None,
        "ultimo_error": None,
    }


def test_estado_sin_carpeta_fijada(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _get(base, "/api/control/estado", {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {
        "carpeta": None, "estadillo": None, "en_curso": False,
        "fase": None, "progreso": None, "ultimo_error": None,
    }


def test_estado_ve_la_carpeta_fijada_directamente_en_api(servidor):
    """`carpeta_trabajo_fijar` es un unico estado en `Api` (no una closure del
    handler HTTP): fijarla por fuera de `POST /api/control/carpeta` (como
    haria el kiosco via `METODOS_EXPUESTOS` el dia que la llame el) ya la deja
    visible en `/api/control/estado`."""
    _, api, base = servidor
    _fijar_pin(api)
    api.carpeta_trabajo_fijar("/media/usb1/vuelo")
    status, body, _ = _get(base, "/api/control/estado", {PIN_CABECERA: "1234"})
    assert status == 200
    assert body["carpeta"] == "/media/usb1/vuelo"


def test_estado_refleja_ultimo_progreso_fase_y_error(servidor):
    """`/api/control/estado` devuelve el ultimo evento que ya emitiria
    `Api._push` (`atom:progress`), sin necesitar el SSE."""
    _, api, base = servidor
    _fijar_pin(api)
    api._running = True
    api._control_fase = {"index": 2, "total": 5}
    api._control_progreso = 47
    api._control_ultimo_error = "RuntimeError: algo fallo"
    status, body, _ = _get(base, "/api/control/estado", {PIN_CABECERA: "1234"})
    assert status == 200
    assert body["en_curso"] is True
    assert body["fase"] == {"index": 2, "total": 5}
    assert body["progreso"] == 47
    assert body["ultimo_error"] == "RuntimeError: algo fallo"


# ---- POST /api/control/cancelar --------------------------------------------

def test_cancelar_happy_path(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _post_json(base, "/api/control/cancelar", {}, {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {"ok": True}
    assert api.analisis_cancel_llamadas == 1


# ---- POST /api/control/login ------------------------------------------------

def test_login_sin_pin_configurado_da_403(servidor):
    srv, api, base = servidor
    status, body, _ = _post_json(base, "/api/control/login", {}, {PIN_CABECERA: "1234"})
    assert status == 403
    assert body == {"ok": False, "codigo": "pin_no_configurado"}


def test_login_pin_malo_da_401(servidor):
    _, api, base = servidor
    _fijar_pin(api)
    status, body, _ = _post_json(base, "/api/control/login", {}, {PIN_CABECERA: "0000"})
    assert status == 401
    assert body == {"ok": False, "codigo": "pin_invalido"}


def test_login_pin_ok_200_y_evento_atom_control_ui(servidor):
    srv, api, base = servidor
    _fijar_pin(api)
    q = srv.sink.subscribe()
    status, body, _ = _post_json(base, "/api/control/login", {}, {PIN_CABECERA: "1234"})
    assert status == 200
    assert body == {"ok": True}
    evento, detalle = q.get(timeout=5)
    assert evento == "atom:control_ui"
    assert detalle == {"accion": "login"}
    assert "1234" not in json.dumps(detalle)


# ---- evento unificado atom:control_ui en carpeta/organizar/cancelar --------

def test_carpeta_emite_evento_atom_control_ui(servidor):
    srv, api, base = servidor
    _fijar_pin(api)
    q = srv.sink.subscribe()
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    eventos = [q.get(timeout=5), q.get(timeout=5)]
    assert ("atom:control_carpeta", {"path": "/media/usb1/vuelo"}) in eventos
    assert ("atom:control_ui", {"accion": "carpeta", "path": "/media/usb1/vuelo"}) in eventos


def test_organizar_emite_evento_atom_control_ui(servidor):
    srv, api, base = servidor
    _fijar_pin(api)
    _post_json(base, "/api/control/carpeta", {"path": "/media/usb1/vuelo"}, {PIN_CABECERA: "1234"})
    q = srv.sink.subscribe()
    _post_json(base, "/api/control/organizar", {}, {PIN_CABECERA: "1234"})
    evento, detalle = q.get(timeout=5)
    assert evento == "atom:control_ui"
    assert detalle == {"accion": "organizar"}


def test_cancelar_emite_evento_atom_control_ui(servidor):
    srv, api, base = servidor
    _fijar_pin(api)
    q = srv.sink.subscribe()
    _post_json(base, "/api/control/cancelar", {}, {PIN_CABECERA: "1234"})
    evento, detalle = q.get(timeout=5)
    assert evento == "atom:control_ui"
    assert detalle == {"accion": "cancelar"}
