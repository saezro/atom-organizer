from utils import StopGate


def test_arm_resetea_stop_la_primera_vez():
    gate = StopGate()
    assert gate.is_stopped() is False
    gate.request_stop()
    assert gate.is_stopped() is True
    gate.disarm()
    gate.arm()
    assert gate.is_stopped() is False


def test_arm_repetido_durante_el_mismo_procesado_no_pisa_un_stop_pendiente():
    """Reproduce el bug real: prepare_log llama a enable_process() una vez al arrancar
    (arm inicial), y una subfase intermedia (p.ej. do_rgb_aerotools_processing) vuelve a
    llamar a enable_process() antes de lanzar la siguiente subfase. Si el usuario pulsó
    "Parar" entre medias, ese segundo arm() NO debe resetear el flag."""
    gate = StopGate()
    gate.arm()  # equivalente a prepare_log:1932, arranque del run_thread
    assert gate.is_stopped() is False

    gate.request_stop()  # usuario pulsa "Parar" a mitad de la primera subfase
    assert gate.is_stopped() is True

    gate.arm()  # llamada intermedia legacy (p.ej. main_app.py:1582), NO debe pisar el stop
    assert gate.is_stopped() is True, "arm() intermedio ha pisado un stop=True pendiente"


def test_disarm_permite_un_arm_limpio_en_el_siguiente_procesado():
    gate = StopGate()
    gate.arm()
    gate.request_stop()
    assert gate.is_stopped() is True

    gate.disarm()  # equivalente a thread_complete al terminar todo el run_thread
    gate.arm()  # siguiente pulsación de un botón de lanzamiento -> prepare_log de nuevo
    assert gate.is_stopped() is False


def test_arm_sin_disarm_previo_es_idempotente():
    gate = StopGate()
    gate.arm()
    gate.arm()
    gate.arm()
    assert gate.is_stopped() is False
