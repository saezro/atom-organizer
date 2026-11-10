"""Fixes de la auditoría de 5622b4b (v3.4.159): escritura gs:// sin restos,
tamaños de origen por ejecución, aviso en lectura de cabecera por rango y
`_revisar_ruta` con rutas normalizadas."""
import logging
from pathlib import Path

import pytest

from atom_core import almacen, cierre, indice
from tests.test_almacen_gcs import BlobFalso, BucketFalso, _almacen


# --- Fix 1: ningún objeto parcial/0 bytes en destino ---------------------------

def test_mover_copia_inconsistente_borra_el_destino_y_conserva_origen():
    alm, bucket = _almacen(prefijo_raiz="raiz")
    bucket.objetos["raiz/o.txt"] = b"contenido"
    original = BucketFalso.copy_blob

    def mala(self, blob_origen, bucket_destino, nombre_destino):
        bucket_destino.objetos[nombre_destino] = b""  # 0 bytes
        return BlobFalso(bucket_destino, nombre_destino)

    BucketFalso.copy_blob = mala
    try:
        with pytest.raises(OSError, match="origen conservado"):
            alm.mover("o.txt", "d.txt")
    finally:
        BucketFalso.copy_blob = original
    assert "raiz/d.txt" not in bucket.objetos
    assert bucket.objetos["raiz/o.txt"] == b"contenido"


def test_subida_corrupta_con_borrado_fallido_no_se_silencia(tmp_path, caplog):
    alm, bucket = _almacen(prefijo_raiz="raiz")
    o = tmp_path / "o.txt"
    o.write_text("contenido")
    orig_up, orig_del = BlobFalso.upload_from_filename, BlobFalso.delete

    def truncada(self, ruta_local):
        self.bucket.objetos[self.name] = b""

    def no_borra(self):
        raise RuntimeError("503")

    BlobFalso.upload_from_filename, BlobFalso.delete = truncada, no_borra
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(OSError, match="no se pudo borrar el objeto corrupto"):
                alm.publicar(o, "d.txt")
    finally:
        BlobFalso.upload_from_filename, BlobFalso.delete = orig_up, orig_del
    assert any("No se pudo borrar" in r.message for r in caplog.records)


# --- Fix 2: tamaños de origen por ejecución, sin global ------------------------

def test_tamanos_origen_ya_no_es_global_ni_se_filtra_entre_listados(monkeypatch):
    assert not hasattr(indice, "_TAMANOS_ORIGEN_GCS")
    monkeypatch.setattr(indice.almacen, "listar_con_tamanos",
                        lambda c: {"gs://b/a.JPG": 5, "gs://b/n.JPG": None, "gs://b/x.txt": 9})
    t1: dict = {}
    assert indice._listar_imagenes_gcs("gs://b", t1) == ["gs://b/a.JPG", "gs://b/n.JPG"]
    assert t1 == {"gs://b/a.JPG": 5}
    t2: dict = {}
    monkeypatch.setattr(indice.almacen, "listar_con_tamanos", lambda c: {"gs://b/z.JPG": 1})
    indice._listar_imagenes_gcs("gs://b", t2)
    assert t2 == {"gs://b/z.JPG": 1} and t1 == {"gs://b/a.JPG": 5}
    indice._listar_imagenes_gcs("gs://b")  # sin mapa: no falla ni guarda nada


# --- Fix 3: cabecera por rango, excepciones estrechas + aviso ------------------

class _ExifVacio:
    def __getattr__(self, nombre):
        return lambda *a, **k: None


def test_cabecera_rango_fallida_avisa_con_ruta_y_motivo(monkeypatch, caplog):
    def falla(ruta, n):
        raise OSError("rango roto")
    monkeypatch.setattr(indice.almacen, "leer_cabecera", falla)
    with caplog.at_level(logging.WARNING):
        try:
            indice._leer_metadatos("gs://b/p/a.JPG", _ExifVacio(), None)
        except Exception:
            pass  # lo que haga el camino alternativo no es objeto de este test
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("gs://b/p/a.JPG" in m and "rango roto" in m for m in msgs), msgs


def test_cabecera_rango_error_inesperado_no_se_traga(monkeypatch):
    def bug(ruta, n):
        raise KeyError("bug de programación")
    monkeypatch.setattr(indice.almacen, "leer_cabecera", bug)
    with pytest.raises(KeyError):
        indice._leer_metadatos("gs://b/p/a.JPG", _ExifVacio(), None)


def test_almacen_leer_cabecera_aviso_al_caer_a_objeto_entero(monkeypatch, caplog):
    class Alm:
        def leer_rango(self, p, n):
            raise ValueError("416")

        def abrir_local(self, p):
            from contextlib import contextmanager

            @contextmanager
            def cm():
                yield Path(__file__)
            return cm()

    monkeypatch.setattr(almacen, "abrir_almacen", lambda r: (Alm(), "k"))
    with caplog.at_level(logging.WARNING):
        assert len(almacen.leer_cabecera("gs://b/k", 10)) == 10
    assert any("gs://b/k" in r.getMessage() and "416" in r.getMessage() for r in caplog.records)


def test_almacen_leer_cabecera_error_inesperado_se_propaga(monkeypatch):
    class Alm:
        def leer_rango(self, p, n):
            raise KeyError("bug")

    monkeypatch.setattr(almacen, "abrir_almacen", lambda r: (Alm(), "k"))
    with pytest.raises(KeyError):
        almacen.leer_cabecera("gs://b/k", 10)


# --- Fix 4: _revisar_ruta normaliza -------------------------------------------

def test_normalizar_ruta():
    assert almacen.normalizar_ruta("GS://b//p\\q/a.JPG/") == "gs://b/p/q/a.JPG"
    assert almacen.normalizar_ruta("/tmp//x/./a.jpg") == "/tmp/x/a.jpg"


def test_revisar_ruta_gcs_con_doble_barra_encuentra_el_objeto():
    listado = {"gs://b/p/a.JPG": 10, "gs://b/p/v.JPG": 0}
    assert cierre._revisar_ruta("o", "gs://b//p/a.JPG", listado) is None
    assert "vacío" in cierre._revisar_ruta("o", "gs://b/p//v.JPG", listado)
    assert "no existe" in cierre._revisar_ruta("o", "gs://b/p//zz.JPG", listado)


def test_revisar_ruta_local_normaliza(tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"x")
    assert cierre._revisar_ruta("o", str(tmp_path) + "//./a.jpg") is None
