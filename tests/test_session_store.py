"""Tests del almacén local de sesión: `secret_box` + `session_store`.

Lo que se protege aquí es que el `refresh_token` del operador deje de estar en
claro en el perfil **de verdad**, no de nombre. Dos cosas se pueden romper sin
hacer ruido: que el cifrado no cifre (un bug de XOR con clave vacía sigue
«funcionando» de cabo a rabo) y que la migración del JSON antiguo pierda la
sesión o —peor— deje el fichero en claro donde estaba.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

from atom_core import secret_box
from atom_core.session_store import KeyfileProtector, SessionStore


TOKEN = "1//refresh-de-verdad-largo-y-con-pinta-de-secreto"


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "session.db",
                        protector=KeyfileProtector(tmp_path / "session.key"))


# --- secret_box --------------------------------------------------------------

def test_lo_sellado_se_abre_igual():
    clave = secret_box.clave_nueva()
    assert secret_box.abrir(clave, secret_box.sellar(clave, b"hola")) == b"hola"


def test_el_texto_claro_no_aparece_en_el_sobre():
    clave = secret_box.clave_nueva()
    assert b"hola" not in secret_box.sellar(clave, b"hola")


def test_dos_sellados_del_mismo_dato_no_se_parecen():
    """Nonce por sobre: si se repitiera, dos secretos cifrados con la misma
    clave se podrían restar y salir en claro."""
    clave = secret_box.clave_nueva()
    assert secret_box.sellar(clave, b"hola") != secret_box.sellar(clave, b"hola")


def test_otra_clave_no_abre_el_sobre():
    sobre = secret_box.sellar(secret_box.clave_nueva(), b"hola")
    with pytest.raises(secret_box.SobreInvalido):
        secret_box.abrir(secret_box.clave_nueva(), sobre)


def test_un_sobre_manipulado_no_se_abre():
    """Encrypt-then-MAC: tocar un byte del cifrado tiene que dar error, no un
    texto claro distinto. Sin esto, quien edite la BD elige el token."""
    clave = secret_box.clave_nueva()
    sobre = bytearray(secret_box.sellar(clave, b"hola que tal"))
    sobre[-40] ^= 0x01
    with pytest.raises(secret_box.SobreInvalido):
        secret_box.abrir(clave, bytes(sobre))


def test_un_sobre_con_el_tag_tocado_no_se_abre():
    clave = secret_box.clave_nueva()
    sobre = bytearray(secret_box.sellar(clave, b"hola"))
    sobre[-1] ^= 0x01
    with pytest.raises(secret_box.SobreInvalido):
        secret_box.abrir(clave, bytes(sobre))


def test_el_aad_ata_el_sobre_a_su_sitio():
    """Un BLOB cifrado no debe poder moverse de columna sin que se note."""
    clave = secret_box.clave_nueva()
    sobre = secret_box.sellar(clave, b"hola", aad=b"columna-a")
    with pytest.raises(secret_box.SobreInvalido):
        secret_box.abrir(clave, sobre, aad=b"columna-b")


def test_un_sobre_truncado_no_se_abre():
    clave = secret_box.clave_nueva()
    with pytest.raises(secret_box.SobreInvalido):
        secret_box.abrir(clave, secret_box.sellar(clave, b"hola")[:20])


def test_el_sobre_vacio_no_se_abre():
    with pytest.raises(secret_box.SobreInvalido):
        secret_box.abrir(secret_box.clave_nueva(), b"")


def test_sellar_un_secreto_vacio_sigue_siendo_reversible():
    clave = secret_box.clave_nueva()
    assert secret_box.abrir(clave, secret_box.sellar(clave, b"")) == b""


# --- el almacén --------------------------------------------------------------

def test_sin_sesion_guardada_no_hay_nada_que_leer(store):
    assert store.leer() is None


def test_preguntar_no_crea_ficheros(tmp_path, store):
    """Abrir la app sin haber entrado nunca no debe sembrar el perfil con una
    BD vacía ni con un fichero de clave."""
    store.leer()
    assert not (tmp_path / "session.db").exists()
    assert not (tmp_path / "session.key").exists()


def test_lo_guardado_se_recupera(store):
    store.guardar("piloto@aerotools.es", TOKEN)
    sesion = store.leer()
    assert sesion.email == "piloto@aerotools.es"
    assert sesion.refresh_token == TOKEN


def test_el_token_no_esta_en_claro_en_la_bd(tmp_path, store):
    """El motivo entero del cambio: un `strings session.db` no puede dar el
    refresh token."""
    store.guardar("piloto@aerotools.es", TOKEN)
    assert TOKEN.encode() not in (tmp_path / "session.db").read_bytes()


def test_guardar_dos_veces_no_acumula_sesiones(tmp_path, store):
    store.guardar("uno@aerotools.es", TOKEN)
    store.guardar("dos@aerotools.es", TOKEN + "-nuevo")

    con = sqlite3.connect(tmp_path / "session.db")
    try:
        assert con.execute("SELECT COUNT(*) FROM sesion").fetchone()[0] == 1
    finally:
        con.close()
    assert store.leer().email == "dos@aerotools.es"


def test_borrar_deja_el_almacen_sin_sesion(store):
    store.guardar("piloto@aerotools.es", TOKEN)
    store.borrar()
    assert store.leer() is None


def test_no_se_guarda_una_sesion_sin_token(store):
    with pytest.raises(ValueError):
        store.guardar("piloto@aerotools.es", "")


def test_marcar_validada_deja_constancia(store):
    store.guardar("piloto@aerotools.es", TOKEN)
    assert store.leer().validada_en is None
    store.marcar_validada(1_700_000_000.0)
    assert store.leer().validada_en == 1_700_000_000.0


@pytest.mark.skipif(sys.platform.startswith("win"), reason="permisos POSIX")
def test_ni_la_bd_ni_la_clave_quedan_legibles_para_todos(tmp_path, store):
    store.guardar("piloto@aerotools.es", TOKEN)
    for nombre in ("session.db", "session.key"):
        modo = oct(os.stat(tmp_path / nombre).st_mode & 0o777)
        assert modo == "0o600", f"{nombre} quedó en {modo}"


def test_una_bd_corrupta_no_impide_arrancar(tmp_path):
    """Peor caso aceptable: pedir login otra vez. Inaceptable: no abrir."""
    db = tmp_path / "session.db"
    db.write_bytes(b"esto no es una base de datos")
    store = SessionStore(db, protector=KeyfileProtector(tmp_path / "session.key"))
    assert store.leer() is None
    assert store.error_lectura


def test_si_la_clave_se_pierde_la_sesion_se_descarta_y_se_explica(tmp_path):
    """Perfil copiado a otro equipo (o keyfile borrado): la sesión ya no se
    puede abrir. Dejarla ahí produciría un «tienes sesión» que falla en cada
    uso; se retira y se dice por qué."""
    db = tmp_path / "session.db"
    store = SessionStore(db, protector=KeyfileProtector(tmp_path / "session.key"))
    store.guardar("piloto@aerotools.es", TOKEN)

    (tmp_path / "session.key").unlink()
    otro = SessionStore(db, protector=KeyfileProtector(tmp_path / "session.key"))

    assert otro.leer() is None
    assert "no se puede descifrar" in otro.error_lectura
    # Y ya no queda rastro que reintentar en el siguiente arranque.
    assert otro.leer() is None


# --- migración desde el JSON en claro ----------------------------------------

def test_la_sesion_del_json_antiguo_se_importa(tmp_path, store):
    legacy = tmp_path / "google_auth.json"
    legacy.write_text(json.dumps({"refresh_token": TOKEN, "email": "piloto@aerotools.es"}))

    assert store.importar_legacy(legacy) is True

    sesion = store.leer()
    assert sesion.refresh_token == TOKEN
    assert sesion.email == "piloto@aerotools.es"


def test_el_json_en_claro_deja_de_estar_donde_estaba(tmp_path, store):
    """Migrar sin retirar el fichero sería no haber migrado: el secreto en
    claro seguiría en el perfil, que es justo lo que se venía a quitar."""
    legacy = tmp_path / "google_auth.json"
    legacy.write_text(json.dumps({"refresh_token": TOKEN, "email": "p@aerotools.es"}))

    store.importar_legacy(legacy)

    assert not legacy.exists()


def test_un_json_sin_token_no_se_mira_en_cada_arranque(tmp_path, store):
    legacy = tmp_path / "google_auth.json"
    legacy.write_text(json.dumps({"email": "p@aerotools.es"}))

    assert store.importar_legacy(legacy) is False
    assert not legacy.exists()
    assert store.leer() is None


def test_un_json_ilegible_no_rompe_la_migracion(tmp_path, store):
    legacy = tmp_path / "google_auth.json"
    legacy.write_text("{roto")
    assert store.importar_legacy(legacy) is False
    assert store.leer() is None


def test_una_bd_danada_se_aparta_para_no_bloquear_el_siguiente_guardado(tmp_path, store):
    """Si la BD dañada se quedara donde está, cada guardado siguiente fallaría
    contra ella: ni login nuevo ni migración del JSON heredado, para siempre."""
    db = tmp_path / "session.db"
    db.write_bytes(b"esto no es una base de datos")

    assert store.leer() is None

    store.guardar("piloto@aerotools.es", TOKEN)
    assert store.leer().refresh_token == TOKEN
    assert (tmp_path / "session.db.dañado").exists()


# --- migración de esquema: columna `modo` (llegó con el broker) -------------

def test_una_bd_sin_columna_modo_se_migra_sola_sin_perder_la_sesion(tmp_path):
    """`modo` la añadió el broker de la Raspberry Pi. Un perfil de escritorio
    ya en uso tiene una BD creada con el `CREATE TABLE` de antes de esa
    columna: abrirla no puede perder la sesión que ya tenía guardada."""
    db_path = tmp_path / "session.db"
    protector = KeyfileProtector(tmp_path / "session.key")
    cifrado = protector.proteger(TOKEN.encode("utf-8"))

    # El CREATE TABLE tal cual era antes de que existiera `modo`.
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE sesion (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            email           TEXT,
            refresh_cifrado BLOB NOT NULL,
            backend         TEXT NOT NULL,
            creado_en       REAL NOT NULL,
            actualizado_en  REAL NOT NULL,
            validada_en     REAL
        )""")
    con.execute(
        "INSERT INTO sesion (id, email, refresh_cifrado, backend, creado_en, "
        "actualizado_en, validada_en) VALUES (1, ?, ?, ?, ?, ?, NULL)",
        ("piloto@aerotools.es", cifrado, protector.nombre, 1.0, 1.0))
    con.commit()
    con.close()

    store = SessionStore(db_path, protector=protector)
    sesion = store.leer()

    assert sesion is not None
    assert sesion.refresh_token == TOKEN
    assert sesion.email == "piloto@aerotools.es"
    # Sin `pair()`, una fila que ya existía antes del broker es una sesión
    # 'google' de toda la vida: el `DEFAULT 'google'` del ALTER TABLE es lo
    # que lo garantiza.
    assert sesion.modo == "google"

    columnas = {fila[1] for fila in
                sqlite3.connect(db_path).execute("PRAGMA table_info(sesion)")}
    assert "modo" in columnas


# --- migración de esquema: columna `picture` (llegó con el avatar del kiosco)

def test_una_bd_sin_columna_picture_se_migra_sola_sin_perder_la_sesion(tmp_path):
    """`picture` llegó después de `modo`: una BD del broker ya en uso (con
    `modo` pero sin `picture`) tiene que seguir abriendo sin perder la sesión
    y sin excepción, con la foto en blanco hasta el próximo emparejamiento."""
    db_path = tmp_path / "session.db"
    protector = KeyfileProtector(tmp_path / "session.key")
    cifrado = protector.proteger(TOKEN.encode("utf-8"))

    # El CREATE TABLE tal cual era con `modo` pero antes de `picture`.
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE sesion (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            email           TEXT,
            refresh_cifrado BLOB NOT NULL,
            backend         TEXT NOT NULL,
            creado_en       REAL NOT NULL,
            actualizado_en  REAL NOT NULL,
            validada_en     REAL,
            modo            TEXT NOT NULL DEFAULT 'google'
        )""")
    con.execute(
        "INSERT INTO sesion (id, email, refresh_cifrado, backend, creado_en, "
        "actualizado_en, validada_en, modo) VALUES (1, ?, ?, ?, ?, ?, NULL, ?)",
        ("piloto@aerotools.es", cifrado, protector.nombre, 1.0, 1.0, "broker"))
    con.commit()
    con.close()

    store = SessionStore(db_path, protector=protector)
    sesion = store.leer()

    assert sesion is not None
    assert sesion.refresh_token == TOKEN
    assert sesion.email == "piloto@aerotools.es"
    assert sesion.modo == "broker"
    # Sin la columna nueva no hay foto que rescatar: cae a vacío, no a error.
    assert sesion.picture == ""

    columnas = {fila[1] for fila in
                sqlite3.connect(db_path).execute("PRAGMA table_info(sesion)")}
    assert "picture" in columnas


def test_guardar_y_leer_una_picture_va_y_vuelve(store):
    store.guardar("piloto@aerotools.es", TOKEN, modo="broker",
                  picture="https://lh3.googleusercontent.com/foo")

    sesion = store.leer()

    assert sesion is not None
    assert sesion.picture == "https://lh3.googleusercontent.com/foo"


# --- migración de esquema: columna `nombre` (llegó junto al nombre del kiosco)

def test_una_bd_sin_columna_nombre_se_migra_sola_sin_perder_la_sesion(tmp_path):
    """`nombre` llegó después de `picture`: una BD del broker ya en uso (con
    `modo`/`picture` pero sin `nombre`) tiene que seguir abriendo sin perder
    la sesión y sin excepción, con el nombre en blanco hasta el próximo
    emparejamiento."""
    db_path = tmp_path / "session.db"
    protector = KeyfileProtector(tmp_path / "session.key")
    cifrado = protector.proteger(TOKEN.encode("utf-8"))

    # El CREATE TABLE tal cual era con `modo`/`picture` pero antes de `nombre`.
    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE sesion (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            email           TEXT,
            refresh_cifrado BLOB NOT NULL,
            backend         TEXT NOT NULL,
            creado_en       REAL NOT NULL,
            actualizado_en  REAL NOT NULL,
            validada_en     REAL,
            modo            TEXT NOT NULL DEFAULT 'google',
            picture         TEXT NOT NULL DEFAULT ''
        )""")
    con.execute(
        "INSERT INTO sesion (id, email, refresh_cifrado, backend, creado_en, "
        "actualizado_en, validada_en, modo, picture) VALUES (1, ?, ?, ?, ?, ?, NULL, ?, ?)",
        ("piloto@aerotools.es", cifrado, protector.nombre, 1.0, 1.0, "broker",
         "https://lh3.googleusercontent.com/foo"))
    con.commit()
    con.close()

    store = SessionStore(db_path, protector=protector)
    sesion = store.leer()

    assert sesion is not None
    assert sesion.refresh_token == TOKEN
    assert sesion.email == "piloto@aerotools.es"
    assert sesion.modo == "broker"
    assert sesion.picture == "https://lh3.googleusercontent.com/foo"
    # Sin la columna nueva no hay nombre que rescatar: cae a vacío, no a error.
    assert sesion.nombre == ""

    columnas = {fila[1] for fila in
                sqlite3.connect(db_path).execute("PRAGMA table_info(sesion)")}
    assert "nombre" in columnas


def test_guardar_y_leer_un_nombre_va_y_vuelve(store):
    store.guardar("piloto@aerotools.es", TOKEN, modo="broker",
                  picture="https://lh3.googleusercontent.com/foo",
                  nombre="Piloto Aerotools")

    sesion = store.leer()

    assert sesion is not None
    assert sesion.nombre == "Piloto Aerotools"
