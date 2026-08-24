
from atom_core.credencial import (
    ESTADO_OK,
    ESTADO_SIN_CREDENCIAL,
    ESTADO_SIN_CONEXION,
    EstadoCredencial,
    clasificar,
)


def test_clasificar_valida_es_ok():
    assert clasificar(True, "Sesión válida.", hubo_red=True) == ESTADO_OK


def test_clasificar_rechazo_del_backend_es_sin_credencial():
    assert clasificar(False, "Dispositivo no autorizado", hubo_red=True) == ESTADO_SIN_CREDENCIAL


def test_clasificar_sin_red_es_sin_conexion():
    # Si no se llegó a hablar con el backend no se puede afirmar que la
    # credencial esté mal: eso mandaría al operario a re-emparejar sin motivo.
    assert clasificar(False, "timeout", hubo_red=False) == ESTADO_SIN_CONEXION


def test_estado_arranca_sin_comprobar():
    e = EstadoCredencial(reloj=lambda: 1000.0)
    assert e.actual()["comprobado_en"] is None
    assert e.necesita_comprobar() is True


def test_registrar_guarda_estado_y_momento():
    e = EstadoCredencial(reloj=lambda: 1000.0)
    e.registrar(ESTADO_OK, "Sesión válida.")
    assert e.actual() == {"estado": ESTADO_OK, "mensaje": "Sesión válida.", "comprobado_en": 1000.0}
    assert e.necesita_comprobar() is False


def test_necesita_comprobar_tras_el_latido():
    ahora = {"t": 1000.0}
    e = EstadoCredencial(latido=100.0, reloj=lambda: ahora["t"])
    e.registrar(ESTADO_OK)
    ahora["t"] = 1099.0
    assert e.necesita_comprobar() is False
    ahora["t"] = 1101.0
    assert e.necesita_comprobar() is True


def test_invalidar_fuerza_sin_credencial_y_recomprobacion():
    e = EstadoCredencial(reloj=lambda: 1000.0)
    e.registrar(ESTADO_OK)
    e.invalidar("401 del backend")
    assert e.actual()["estado"] == ESTADO_SIN_CREDENCIAL
    assert e.necesita_comprobar() is True


def test_sin_conexion_no_pisa_un_ok_previo_como_sin_credencial():
    # Perder la red no debe hacer creer que hay que re-emparejar.
    e = EstadoCredencial(reloj=lambda: 1000.0)
    e.registrar(ESTADO_OK)
    e.registrar(ESTADO_SIN_CONEXION, "sin red")
    assert e.actual()["estado"] == ESTADO_SIN_CONEXION
