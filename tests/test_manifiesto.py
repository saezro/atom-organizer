"""Tests del manifiesto SQLite del organizado.

El manifiesto es la memoria del run: si pierde filas, se corrompe al
escribirlo desde varios procesos a la vez, o no sabe distinguir lo hecho de
lo pendiente, el organizado deja imágenes sin procesar SIN QUE NADIE SE
ENTERE. Por eso se prueba la concurrencia y la reanudación, no solo el CRUD.
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from atom_core.manifiesto import FilaManifiesto, Manifiesto


def _fila(ruta_origen="/origen/DJI_0001.JPG", **cambios):
    """Construye una fila válida; los tests solo sobrescriben lo que les importa."""
    base = dict(
        ruta_origen=ruta_origen,
        tipo="RGB",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb="PB1",
        vuelo="V01",
        nombre_nuevo="20260501_100000_DJI_0001.JPG",
        angulo_giro=90,
        pct_recorte=0.8,
        comprime=True,
        ruta_salida_original="/destino/PB1_V01/RGB/20260501_100000_DJI_0001.JPG",
        ruta_salida_crop="/destino/PB1_V01/RGB/20260501_100000_DJI_0001_CROP.JPG",
        ruta_salida_tiff=None,
        unassigned=False,
    )
    base.update(cambios)
    return FilaManifiesto(**base)


def test_esquema_en_modo_wal(tmp_path):
    """El manifiesto se escribe desde varios workers a la vez. Sin WAL, SQLite
    serializa con bloqueo de fichero entero y el apply se convierte en un
    cuello de botella (o revienta con 'database is locked')."""
    manifiesto = Manifiesto(tmp_path / "manifiesto.db")
    manifiesto.crear_esquema()
    with sqlite3.connect(tmp_path / "manifiesto.db") as conexion:
        modo = conexion.execute("PRAGMA journal_mode").fetchone()[0]
    assert modo.lower() == "wal"
    manifiesto.cerrar()


def test_insertar_y_contar_pendientes(tmp_path):
    """Toda fila recién insertada nace 'pendiente': el apply no puede saltarse
    ninguna imagen por un default mal puesto."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    insertadas = manifiesto.insertar_muchas(
        [_fila("/origen/A.JPG"), _fila("/origen/B.JPG")]
    )
    assert insertadas == 2
    assert len(manifiesto.pendientes()) == 2
    assert manifiesto.resumen() == {
        "pendiente": 2, "en_curso": 0, "hecho": 0, "fallido": 0
    }
    manifiesto.cerrar()


def test_ruta_origen_es_unica(tmp_path):
    """Insertar dos veces la misma imagen la duplicaría en el destino. Un
    re-run del índice sobre el mismo manifiesto no debe multiplicar filas."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("/origen/A.JPG")])
    manifiesto.insertar_muchas([_fila("/origen/A.JPG")])
    assert len(manifiesto.todas()) == 1
    manifiesto.cerrar()


def test_transiciones_de_estado(tmp_path):
    """Una fila hecha guarda su verificación; una fallida guarda el motivo.
    Sin motivo no se puede reintentar con criterio ni explicar el fallo."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("/origen/A.JPG"), _fila("/origen/B.JPG")])
    primera, segunda = manifiesto.todas()

    manifiesto.marcar_en_curso(primera["id"])
    manifiesto.marcar_hecha(primera["id"], verificacion="1234")
    manifiesto.marcar_fallida(segunda["id"], motivo="EXIF ilegible")

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas["/origen/A.JPG"]["estado"] == "hecho"
    assert filas["/origen/A.JPG"]["verificacion"] == "1234"
    assert filas["/origen/B.JPG"]["estado"] == "fallido"
    assert filas["/origen/B.JPG"]["motivo_fallo"] == "EXIF ilegible"
    manifiesto.cerrar()


def test_reabrir_huerfanas(tmp_path):
    """Si el proceso muere a mitad, quedan filas 'en_curso' que nadie está
    procesando. Al reanudar hay que devolverlas a 'pendiente' o esas imágenes
    no se escriben nunca."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("/origen/A.JPG")])
    id_fila = manifiesto.todas()[0]["id"]
    manifiesto.marcar_en_curso(id_fila)

    assert manifiesto.reabrir_huerfanas() == 1
    assert manifiesto.todas()[0]["estado"] == "pendiente"
    manifiesto.cerrar()


def test_escrituras_concurrentes_no_pierden_filas(tmp_path):
    """El apply marca filas desde muchos workers a la vez. Este test reproduce
    esa concurrencia: si el manifiesto perdiera actualizaciones, el resumen
    final diría que quedan pendientes imágenes que sí se escribieron (o al
    revés, que está todo hecho cuando no lo está)."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila(f"/origen/{indice:03d}.JPG") for indice in range(60)])
    ids = [fila["id"] for fila in manifiesto.todas()]

    def marcar(id_fila):
        manifiesto.marcar_en_curso(id_fila)
        manifiesto.marcar_hecha(id_fila, verificacion=str(id_fila))

    with ThreadPoolExecutor(max_workers=8) as ejecutor:
        list(ejecutor.map(marcar, ids))

    assert manifiesto.resumen()["hecho"] == 60
    assert manifiesto.pendientes() == []
    manifiesto.cerrar()


def test_filas_por_vuelo(tmp_path):
    """El cierre emite un CSV de criterio por vuelo. Necesita pedir las filas
    de un (PB, vuelo) concreto sin releer el manifiesto entero."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/A.JPG", pb="PB1", vuelo="V01"),
        _fila("/origen/B.JPG", pb="PB1", vuelo="V02"),
        _fila("/origen/C.JPG", pb="PB1", vuelo="V01"),
    ])
    rutas = sorted(fila["ruta_origen"] for fila in manifiesto.filas_por_vuelo("PB1", "V01"))
    assert rutas == ["/origen/A.JPG", "/origen/C.JPG"]
    manifiesto.cerrar()


def test_fila_unassigned_admite_pb_y_vuelo_vacios(tmp_path):
    """Las imágenes que no casan con ninguna ventana horaria van a
    SIN_ORDENAR: no tienen PB ni vuelo, y el esquema tiene que aceptarlo en
    vez de reventar con NOT NULL."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/X.JPG", pb=None, vuelo=None, unassigned=True,
              ruta_salida_original="/destino/SIN_ORDENAR/RGB/X.JPG",
              ruta_salida_crop=None)
    ])
    fila = manifiesto.todas()[0]
    assert fila["unassigned"] == 1
    assert fila["pb"] is None
    manifiesto.cerrar()
