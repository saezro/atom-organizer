from atom_core.credencial import ESTADO_OK, ESTADO_SIN_CREDENCIAL, ESTADO_SIN_CONEXION

from app_webview import _ciclo_comprobacion_arranque


class ApiFalsa:
    """Doble mínimo de `Api`: solo `cloud_comprobar` y `cloud_drenar`."""

    def __init__(self, estados):
        self._estados = list(estados)
        self.drenados = 0

    def cloud_comprobar(self):
        estado = self._estados.pop(0) if self._estados else self._estados_final()
        if isinstance(estado, Exception):
            raise estado
        return {"estado": estado, "mensaje": f"estado={estado}"}

    def _estados_final(self):
        raise AssertionError("cloud_comprobar llamado más veces de las esperadas")

    def cloud_drenar(self):
        self.drenados += 1


class DormirFalso:
    def __init__(self):
        self.esperas = []

    def __call__(self, segundos):
        self.esperas.append(segundos)


def _pusher():
    eventos = []
    return eventos, lambda detail: eventos.append(detail)


def test_ok_al_primer_intento_no_espera_y_drena():
    api = ApiFalsa([ESTADO_OK])
    dormir = DormirFalso()
    eventos, push = _pusher()

    _ciclo_comprobacion_arranque(api, dormir=dormir, push=push)

    assert dormir.esperas == []
    assert api.drenados == 1
    assert len(eventos) == 1
    assert eventos[0]["estado"] == ESTADO_OK
    assert eventos[0]["ok"] is True


def test_sin_conexion_tres_veces_luego_ok_hace_backoff_5_10_20():
    api = ApiFalsa([ESTADO_SIN_CONEXION, ESTADO_SIN_CONEXION,
                    ESTADO_SIN_CONEXION, ESTADO_OK])
    dormir = DormirFalso()
    eventos, push = _pusher()

    _ciclo_comprobacion_arranque(api, dormir=dormir, push=push)

    assert dormir.esperas == [5, 10, 20]
    assert api.drenados == 1
    assert len(eventos) == 2
    assert eventos[0]["estado"] == ESTADO_SIN_CONEXION
    assert eventos[1]["estado"] == ESTADO_OK


def test_sin_credencial_para_el_bucle_sin_drenar():
    api = ApiFalsa([ESTADO_SIN_CREDENCIAL])
    dormir = DormirFalso()
    eventos, push = _pusher()

    _ciclo_comprobacion_arranque(api, dormir=dormir, push=push)

    assert dormir.esperas == []
    assert api.drenados == 0
    assert len(eventos) == 1
    assert eventos[0]["estado"] == ESTADO_SIN_CREDENCIAL


def test_excepcion_en_primer_intento_no_propaga_y_reintenta_hasta_ok():
    api = ApiFalsa([RuntimeError("dns roto"), ESTADO_OK])
    dormir = DormirFalso()
    eventos, push = _pusher()

    _ciclo_comprobacion_arranque(api, dormir=dormir, push=push)

    assert dormir.esperas == [5]
    assert api.drenados == 1
    assert len(eventos) == 2
    assert eventos[0]["estado"] == ESTADO_SIN_CONEXION
    assert eventos[1]["estado"] == ESTADO_OK
