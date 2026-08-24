import pytest

from atom_core.credencial import ESTADO_OK, ESTADO_SIN_CREDENCIAL, ESTADO_SIN_CONEXION
from atom_core.google_auth import AuthError


class AuthFalso:
    def __init__(self, resultado=None, excepcion=None, logueado=True):
        self._resultado = resultado
        self._excepcion = excepcion
        self._logueado = logueado
        self.identity = None
        self.validada_en = None
        self.aviso_store = None
        self.broker_only = True

    def is_logged_in(self):
        return self._logueado

    def verificar(self):
        if self._excepcion:
            raise self._excepcion
        return self._resultado


def _api(monkeypatch, auth):
    from app_webview import Api
    api = Api()
    monkeypatch.setattr(api, "_get_auth", lambda: auth)
    return api


def test_credencial_valida_deja_estado_ok(monkeypatch):
    api = _api(monkeypatch, AuthFalso(resultado=(True, "Sesión válida.")))
    assert api.cloud_comprobar()["estado"] == ESTADO_OK
    assert api.cloud_status()["estado"] == ESTADO_OK


def test_rechazo_del_backend_deja_sin_credencial(monkeypatch):
    api = _api(monkeypatch, AuthFalso(excepcion=AuthError("Este dispositivo ya no está autorizado")))
    assert api.cloud_comprobar()["estado"] == ESTADO_SIN_CREDENCIAL


def test_fallo_de_red_deja_sin_conexion(monkeypatch):
    # OSError es lo que sube desde urllib cuando no hay red.
    api = _api(monkeypatch, AuthFalso(excepcion=OSError("Network is unreachable")))
    assert api.cloud_comprobar()["estado"] == ESTADO_SIN_CONEXION


def test_sin_sesion_local_es_sin_credencial(monkeypatch):
    api = _api(monkeypatch, AuthFalso(logueado=False))
    assert api.cloud_comprobar()["estado"] == ESTADO_SIN_CREDENCIAL


def test_fallo_inesperado_de_verificar_no_propaga_y_queda_sin_conexion(monkeypatch):
    # Cualquier excepción que no sea AuthError/OSError no puede tumbar
    # run_task: organizar es local y nunca se bloquea por la nube.
    api = _api(monkeypatch, AuthFalso(excepcion=RuntimeError("boom")))
    assert api.cloud_comprobar()["estado"] == ESTADO_SIN_CONEXION


def test_run_task_no_se_bloquea_si_verificar_falla_de_forma_inesperada(monkeypatch, tmp_path):
    api = _api(monkeypatch, AuthFalso(excepcion=RuntimeError("boom")))
    r = api.run_task("split_images", {"origen": str(tmp_path), "destino": str(tmp_path)})
    assert r["started"] is True
    assert api.cloud_status()["estado"] == ESTADO_SIN_CONEXION


def test_subir_sin_credencial_encola_en_vez_de_rechazar(monkeypatch, tmp_path):
    from atom_core import cola_subidas
    ruta = tmp_path / "cola.json"
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: ruta)

    api = _api(monkeypatch, AuthFalso(logueado=False))
    carpeta = tmp_path / "vuelo1"
    carpeta.mkdir()
    r = api.cloud_upload(str(carpeta), prefix="PLANTA/2026")

    assert r["started"] is False
    assert r["encolado"] is True
    assert len(cola_subidas.pendientes(ruta=ruta)) == 1


def test_status_reporta_cuantas_pendientes_hay(monkeypatch, tmp_path):
    from atom_core import cola_subidas
    ruta = tmp_path / "cola.json"
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: ruta)
    cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026", None, ruta=ruta)

    api = _api(monkeypatch, AuthFalso(resultado=(True, "ok")))
    assert api.cloud_status()["pendientes"] == 1


def test_asegurar_estado_no_recomprueba_dentro_del_latido(monkeypatch):
    llamadas = {"n": 0}

    class AuthContador(AuthFalso):
        def verificar(self):
            llamadas["n"] += 1
            return (True, "ok")

    api = _api(monkeypatch, AuthContador())
    api.cloud_asegurar_estado()
    api.cloud_asegurar_estado()
    api.cloud_asegurar_estado()
    assert llamadas["n"] == 1


def test_asegurar_estado_recomprueba_tras_el_latido(monkeypatch):
    from atom_core.credencial import EstadoCredencial
    llamadas = {"n": 0}
    ahora = {"t": 1000.0}

    class AuthContador(AuthFalso):
        def verificar(self):
            llamadas["n"] += 1
            return (True, "ok")

    api = _api(monkeypatch, AuthContador())
    api._credencial = EstadoCredencial(latido=10.0, reloj=lambda: ahora["t"])
    api.cloud_asegurar_estado()
    ahora["t"] = 1100.0
    api.cloud_asegurar_estado()
    assert llamadas["n"] == 2


def test_drenar_lanza_solo_uno_por_llamada_con_varios_pendientes(monkeypatch, tmp_path):
    from atom_core import cola_subidas
    ruta = tmp_path / "cola.json"
    monkeypatch.setattr(cola_subidas, "_ruta_cola", lambda: ruta)
    cola_subidas.encolar("/datos/vuelo1", "PLANTA/2026/vuelo1", None, ruta=ruta)
    cola_subidas.encolar("/datos/vuelo2", "PLANTA/2026/vuelo2", None, ruta=ruta)

    api = _api(monkeypatch, AuthFalso(resultado=(True, "ok")))
    api.cloud_comprobar()
    monkeypatch.setattr(api, "cloud_upload", lambda *a, **k: {"started": True})

    r = api.cloud_drenar()

    assert r["lanzados"] == 1
    assert len(cola_subidas.pendientes(ruta=ruta)) == 1
