"""La tabla `meta` de session.db como almacen de claves sueltas."""

from atom_core.session_store import SessionStore


def _store(tmp_path):
    return SessionStore(tmp_path / "session.db")


def test_meta_get_devuelve_none_si_no_hay_clave(tmp_path):
    store = _store(tmp_path)
    assert store.meta_get("pin_kiosco") is None


def test_meta_set_y_meta_get_hacen_ida_y_vuelta(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "scrypt$1$2$3$sal$hash")
    assert store.meta_get("pin_kiosco") == "scrypt$1$2$3$sal$hash"


def test_meta_set_sobrescribe_el_valor_anterior(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "viejo")
    store.meta_set("pin_kiosco", "nuevo")
    assert store.meta_get("pin_kiosco") == "nuevo"


def test_meta_set_con_none_borra_la_clave(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "algo")
    store.meta_set("pin_kiosco", None)
    assert store.meta_get("pin_kiosco") is None


def test_meta_no_pisa_la_version_de_esquema(tmp_path):
    store = _store(tmp_path)
    store.meta_set("pin_kiosco", "algo")
    assert store.meta_get("esquema") == "1"
