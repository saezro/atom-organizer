"""Tests del catálogo de perfiles (`perfiles`), para la pantalla de entrada
tipo Netflix. La sesión activa (`sesion`) sigue viva y con su propio test en
`test_session_store.py`; aquí solo se cubre la capa nueva por encima.
"""
from __future__ import annotations

import sqlite3

import pytest

from atom_core.session_store import EMAIL_INVITADO, KeyfileProtector, SessionStore

TOKEN_UNO = "1//refresh-uno-largo-y-con-pinta-de-secreto"
TOKEN_DOS = "1//refresh-dos-largo-y-con-pinta-de-secreto"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "session.db",
                        protector=KeyfileProtector(tmp_path / "session.key"))


def test_alta_de_perfiles_google_e_invitado(store):
    store.guardar_perfil("uno@aerotools.es", TOKEN_UNO, nombre="Uno")
    store.guardar_perfil("dos@aerotools.es", TOKEN_DOS, nombre="Dos")
    store.guardar_perfil(EMAIL_INVITADO, None, modo="invitado")

    perfiles = store.listar_perfiles()
    emails = {p.email for p in perfiles}
    assert emails == {"uno@aerotools.es", "dos@aerotools.es", EMAIL_INVITADO}

    invitado = next(p for p in perfiles if p.email == EMAIL_INVITADO)
    assert invitado.modo == "invitado"
    assert invitado.tiene_credencial is False
    assert invitado.picture == ""


def test_listar_perfiles_ordena_por_ultimo_uso_desc_y_no_expone_el_token(store):
    store.guardar_perfil("viejo@aerotools.es", TOKEN_UNO)
    store.guardar_perfil("nuevo@aerotools.es", TOKEN_DOS)
    # Reactivar el primero lo sube al frente.
    store.guardar_perfil("viejo@aerotools.es", TOKEN_UNO)

    perfiles = store.listar_perfiles()
    assert perfiles[0].email == "viejo@aerotools.es"
    assert perfiles[1].email == "nuevo@aerotools.es"

    for p in perfiles:
        assert not hasattr(p, "refresh_token")
        assert not hasattr(p, "refresh_cifrado")


def test_activar_perfil_deja_la_sesion_activa_correcta(store):
    store.guardar_perfil("uno@aerotools.es", TOKEN_UNO, nombre="Uno")
    store.guardar_perfil("dos@aerotools.es", TOKEN_DOS, nombre="Dos")

    assert store.activar_perfil("dos@aerotools.es") is True

    sesion = store.leer()
    assert sesion is not None
    assert sesion.email == "dos@aerotools.es"
    assert sesion.refresh_token == TOKEN_DOS
    assert sesion.nombre == "Dos"


def test_activar_perfil_inexistente_no_toca_nada(store):
    store.guardar_perfil("uno@aerotools.es", TOKEN_UNO)
    store.activar_perfil("uno@aerotools.es")

    assert store.activar_perfil("fantasma@aerotools.es") is False
    assert store.leer().email == "uno@aerotools.es"


def test_activar_perfil_invitado_no_activa_sesion(store):
    store.guardar_perfil(EMAIL_INVITADO, None, modo="invitado")
    assert store.activar_perfil(EMAIL_INVITADO) is False
    assert store.leer() is None


def test_borrar_perfil_activo_borra_tambien_la_sesion(store):
    store.guardar_perfil("uno@aerotools.es", TOKEN_UNO)
    store.activar_perfil("uno@aerotools.es")
    assert store.leer() is not None

    store.borrar_perfil("uno@aerotools.es")

    assert store.leer() is None
    assert "uno@aerotools.es" not in {p.email for p in store.listar_perfiles()}


def test_borrar_perfil_no_activo_no_toca_la_sesion(store):
    store.guardar_perfil("uno@aerotools.es", TOKEN_UNO)
    store.guardar_perfil("dos@aerotools.es", TOKEN_DOS)
    store.activar_perfil("uno@aerotools.es")

    store.borrar_perfil("dos@aerotools.es")

    assert store.leer().email == "uno@aerotools.es"
    assert "dos@aerotools.es" not in {p.email for p in store.listar_perfiles()}


def test_migracion_desde_una_bd_que_solo_tiene_sesion(tmp_path):
    """Una BD creada por la versión actual (sin `perfiles` tocado nunca, solo
    `sesion` con datos) debe mostrar ese usuario como perfil sin volver a
    loguearse."""
    protector = KeyfileProtector(tmp_path / "session.key")
    store = SessionStore(tmp_path / "session.db", protector=protector)
    store.guardar("piloto@aerotools.es", TOKEN_UNO, nombre="Piloto")

    # Nueva instancia, como si fuera un arranque distinto de la app.
    store2 = SessionStore(tmp_path / "session.db", protector=protector)
    perfiles = store2.listar_perfiles()

    assert len(perfiles) == 1
    assert perfiles[0].email == "piloto@aerotools.es"
    assert perfiles[0].tiene_credencial is True
    assert perfiles[0].nombre == "Piloto"

    # La fila de `sesion` original no se ha tocado ni borrado.
    sesion = store2.leer()
    assert sesion is not None
    assert sesion.email == "piloto@aerotools.es"
    assert sesion.refresh_token == TOKEN_UNO


def test_un_perfil_con_credencial_ilegible_no_rompe_listar_perfiles(tmp_path):
    """Perfil copiado de otra máquina, o keyfile perdido: `listar_perfiles`
    debe seguir devolviendo el resto sin reventar, solo con
    `tiene_credencial=True` pero sin poder activarlo."""
    protector = KeyfileProtector(tmp_path / "session.key")
    store = SessionStore(tmp_path / "session.db", protector=protector)
    store.guardar_perfil("bueno@aerotools.es", TOKEN_UNO)
    store.guardar_perfil("roto@aerotools.es", TOKEN_DOS)

    con = sqlite3.connect(tmp_path / "session.db")
    try:
        con.execute("UPDATE perfiles SET refresh_cifrado = ? WHERE email = ?",
                    (b"esto no es un sobre valido", "roto@aerotools.es"))
        con.commit()
    finally:
        con.close()

    perfiles = store.listar_perfiles()
    emails = {p.email for p in perfiles}
    assert emails == {"bueno@aerotools.es", "roto@aerotools.es"}

    # El de verdad se puede activar; el corrupto no rompe nada y solo falla
    # al intentar activarlo (mismo criterio que `leer()` con la sesión).
    assert store.activar_perfil("bueno@aerotools.es") is True
    assert store.activar_perfil("roto@aerotools.es") is False
