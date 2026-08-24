"""PIN local del kiosco: derivacion, verificacion y ciclo de vida."""

import pytest

from atom_core import pin_kiosco
from atom_core.session_store import SessionStore


def _store(tmp_path):
    return SessionStore(tmp_path / "session.db")


def test_sin_pin_al_principio(tmp_path):
    store = _store(tmp_path)
    assert pin_kiosco.hay_pin(store) is False


def test_fijar_y_verificar(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.hay_pin(store) is True
    assert pin_kiosco.verificar(store, "1234") is True


def test_verificar_pin_incorrecto(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.verificar(store, "9999") is False


def test_verificar_sin_pin_fijado_es_falso(tmp_path):
    assert pin_kiosco.verificar(_store(tmp_path), "1234") is False


def test_el_pin_no_se_guarda_en_claro(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    guardado = store.meta_get(pin_kiosco.CLAVE_META)
    assert guardado.startswith("scrypt$")
    assert "1234" not in guardado


def test_dos_pines_iguales_dan_hashes_distintos(tmp_path):
    a, b = _store(tmp_path / "a"), _store(tmp_path / "b")
    pin_kiosco.fijar(a, "1234")
    pin_kiosco.fijar(b, "1234")
    assert a.meta_get(pin_kiosco.CLAVE_META) != b.meta_get(pin_kiosco.CLAVE_META)


@pytest.mark.parametrize("malo", ["123", "12345", "abcd", "12a4", "", "  12", None])
def test_formato_invalido_se_rechaza(tmp_path, malo):
    with pytest.raises(pin_kiosco.PinInvalido):
        pin_kiosco.fijar(_store(tmp_path), malo)


def test_ceros_a_la_izquierda_son_validos(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "0007")
    assert pin_kiosco.verificar(store, "0007") is True
    assert pin_kiosco.verificar(store, "7") is False


def test_cambiar_con_el_actual_correcto(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.cambiar(store, "1234", "5678") is True
    assert pin_kiosco.verificar(store, "5678") is True


def test_cambiar_con_el_actual_incorrecto_no_toca_nada(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    assert pin_kiosco.cambiar(store, "0000", "5678") is False
    assert pin_kiosco.verificar(store, "1234") is True


def test_borrar_deja_el_kiosco_sin_pin(tmp_path):
    store = _store(tmp_path)
    pin_kiosco.fijar(store, "1234")
    pin_kiosco.borrar(store)
    assert pin_kiosco.hay_pin(store) is False


def test_hash_corrupto_se_trata_como_sin_pin(tmp_path):
    store = _store(tmp_path)
    store.meta_set(pin_kiosco.CLAVE_META, "basura-que-no-es-un-hash")
    assert pin_kiosco.hay_pin(store) is False
    assert pin_kiosco.verificar(store, "1234") is False
