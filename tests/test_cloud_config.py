"""Credenciales del cliente OAuth y prefijo destino dentro del bucket."""
from __future__ import annotations

import json

import pytest

from atom_core import cloud_config


@pytest.fixture(autouse=True)
def _sin_entorno(monkeypatch, tmp_path):
    """Aísla las credenciales del entorno REAL de quien corre los tests.

    No basta con vaciar las variables: `client_file_candidates` mira también la
    raíz del árbol de código y `~/.config/atom-organizer`. Con un
    `google_client.json` de verdad en cualquiera de esas dos (el caso cuando se
    ejecuta la app desde fuente), los tests de «no hay credenciales» encuentran
    las del operador y fallan sin que nada esté roto.
    """
    monkeypatch.delenv("ATOM_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("ATOM_GOOGLE_CLIENT_SECRET", raising=False)

    vacio = tmp_path / "sin-credenciales"
    vacio.mkdir()
    monkeypatch.setattr(cloud_config, "_REPO_DIR", vacio)

    from atom_core import google_auth

    monkeypatch.setattr(google_auth, "user_data_dir", lambda: vacio)


def test_entorno_gana(monkeypatch, tmp_path):
    (tmp_path / cloud_config.CLIENT_FILENAME).write_text(
        json.dumps({"installed": {"client_id": "de-fichero", "client_secret": "s"}}),
        encoding="utf-8")
    monkeypatch.setenv("ATOM_GOOGLE_CLIENT_ID", "de-entorno")
    monkeypatch.setenv("ATOM_GOOGLE_CLIENT_SECRET", "secreto")

    c = cloud_config.load_client(tmp_path)
    assert c.client_id == "de-entorno"
    assert c.origin == "entorno"


def test_lee_el_json_tal_cual_lo_da_google(tmp_path):
    """El operador deja el fichero descargado sin editar: bloque `installed`."""
    (tmp_path / cloud_config.CLIENT_FILENAME).write_text(
        json.dumps({"installed": {"client_id": "abc.apps.googleusercontent.com",
                                  "client_secret": "GOCSPX-x"}}),
        encoding="utf-8")

    c = cloud_config.load_client(tmp_path)
    assert c.client_id == "abc.apps.googleusercontent.com"
    assert c.client_secret == "GOCSPX-x"


def test_json_incompleto_no_cuenta(tmp_path):
    (tmp_path / cloud_config.CLIENT_FILENAME).write_text(
        json.dumps({"installed": {"client_id": "solo-id"}}), encoding="utf-8")
    assert cloud_config.load_client(tmp_path) is None


def test_sin_credenciales_devuelve_none_y_ayuda(tmp_path):
    assert cloud_config.load_client(tmp_path) is None
    ayuda = cloud_config.missing_client_help()
    assert cloud_config.CLIENT_FILENAME in ayuda


def test_el_secret_no_esta_en_el_repo():
    """El repo es PÚBLICO: un secret commiteado lo revoca Google vía
    secret-scanning y la app se queda sin login sin que nadie toque nada."""
    assert cloud_config._BUILD_CLIENT_SECRET == ""
    assert cloud_config._BUILD_CLIENT_ID == ""


@pytest.mark.parametrize("nombre,esperado", [
    ("ANTOLIN", "ANTOLIN"),
    ("MARISOLES_LOS MANGOS", "MARISOLES_LOS_MANGOS"),   # espacio real en BD
    ("OCAÑA", "OCANA"),                                  # eñe real en BD
    ("  vuelo 2025  ", "vuelo_2025"),
    ("a//b", "a_b"),
    ("...", ""),
])
def test_prefijo_desde_carpeta(nombre, esperado):
    assert cloud_config.prefijo_desde_carpeta(nombre) == esperado


def test_bucket_es_el_de_entrada():
    """Cloud Run sólo LEE de este bucket; escribe en plantas_pv_nl."""
    assert cloud_config.BUCKET_DATOS == "datos_para_organizar"
