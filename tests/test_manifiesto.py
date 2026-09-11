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


def test_fuse_cae_a_delete(tmp_path, monkeypatch):
    """En NTFS por fuse (SSD de la Pi) WAL corrompía la base con varios
    workers: ahí el manifiesto tiene que ir en journal DELETE."""
    import atom_core.manifiesto as m
    monkeypatch.setattr(m.sys, "platform", "linux")
    monkeypatch.setattr(m, "_tipo_fs_linux", lambda ruta: "fuseblk")
    manifiesto = Manifiesto(tmp_path / "manifiesto.db")
    manifiesto.crear_esquema()
    with sqlite3.connect(tmp_path / "manifiesto.db") as conexion:
        modo = conexion.execute("PRAGMA journal_mode").fetchone()[0]
    assert modo.lower() == "delete"
    manifiesto.cerrar()


def test_tipo_fs_linux_elige_montaje_mas_largo(tmp_path, monkeypatch):
    import atom_core.manifiesto as m
    montajes = "/dev/a / ext4 rw 0 0\n/dev/sdc1 /media/pi/USB_HDD fuseblk rw 0 0\n"
    import io
    monkeypatch.setattr("builtins.open", lambda *a, **k: io.StringIO(montajes))
    monkeypatch.setattr(m.os.path, "realpath", lambda r: r)
    assert m._tipo_fs_linux("/media/pi/USB_HDD/KL19/.organizado") == "fuseblk"
    assert m._tipo_fs_linux("/media/pi/USB_HDDX") == "ext4"
    assert m._tipo_fs_linux("/home/pi") == "ext4"


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


def test_balance_de_bytes_solo_cuenta_las_hechas(tmp_path):
    """La pregunta de Rodrigo (2026-09-09) era por qué el resumen decía 28 GB
    leídos y 9 escritos: aquello es I/O de disco de toda la máquina, no el
    tamaño de los datos. Esto sí compara lo entregado con lo que entró, y solo
    de las filas terminadas — una pendiente todavía no ha entregado nada."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/A.JPG", bytes_origen=10_000_000),
        _fila("/origen/B.JPG", bytes_origen=20_000_000),
        _fila("/origen/C.JPG", bytes_origen=30_000_000),
    ])
    filas = {fila["ruta_origen"]: fila["id"] for fila in manifiesto.todas()}
    manifiesto.marcar_hecha(filas["/origen/A.JPG"], "/destino/A.JPG:3000000")
    manifiesto.marcar_hecha(filas["/origen/B.JPG"],
                            "/destino/B.JPG:5000000; /destino/B_CROP.JPG:1000000")
    # C se queda pendiente a propósito.

    assert manifiesto.balance_bytes() == {
        "entrada": 30_000_000, "salida": 9_000_000, "imagenes": 2,
    }


def test_balance_de_bytes_aguanta_rutas_de_windows(tmp_path):
    """`verificacion` se parte por el ÚLTIMO `:`, no por el primero: una ruta
    de Windows lleva `C:\\...` y partir por el primero daría la letra de unidad
    como tamaño (`ValueError`, y el run acabaría sin resumen)."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("C:\\origen\\A.JPG", bytes_origen=8_000_000)])
    id_fila = manifiesto.todas()[0]["id"]
    manifiesto.marcar_hecha(id_fila, "C:\\destino\\A.JPG:2500000")

    assert manifiesto.balance_bytes()["salida"] == 2_500_000


def test_balance_de_bytes_ignora_verificaciones_ilegibles(tmp_path):
    """Una entrada corrupta se salta; no puede tumbar el resumen final de un
    run que por lo demás terminó bien."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila("/origen/A.JPG", bytes_origen=1_000)])
    id_fila = manifiesto.todas()[0]["id"]
    manifiesto.marcar_hecha(id_fila, "/destino/A.JPG:no-es-un-numero; /destino/B.JPG:700")

    assert manifiesto.balance_bytes()["salida"] == 700


def test_manifiesto_viejo_gana_la_columna_nueva(tmp_path):
    """Reanudar un run empezado con una versión anterior del Organizer: la
    tabla ya existe, así que `CREATE TABLE IF NOT EXISTS` no la toca y sin
    migración el primer INSERT reventaría con `no such column`."""
    ruta = tmp_path / "m.db"
    antiguo = sqlite3.connect(ruta)
    antiguo.execute("""
        CREATE TABLE imagenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ruta_origen TEXT NOT NULL UNIQUE, tipo TEXT NOT NULL,
            timestamp_exif TEXT, modelo TEXT, pb TEXT, vuelo TEXT,
            nombre_nuevo TEXT NOT NULL DEFAULT '',
            angulo_giro INTEGER NOT NULL DEFAULT 0, pct_recorte REAL,
            comprime INTEGER NOT NULL DEFAULT 0,
            ruta_salida_original TEXT NOT NULL, ruta_salida_crop TEXT,
            ruta_salida_tiff TEXT, unassigned INTEGER NOT NULL DEFAULT 0,
            estado TEXT NOT NULL DEFAULT 'pendiente', motivo_fallo TEXT,
            verificacion TEXT)
    """)
    antiguo.commit()
    antiguo.close()

    manifiesto = Manifiesto(ruta)
    manifiesto.crear_esquema()
    assert manifiesto.insertar_muchas([_fila(bytes_origen=4_000)]) == 1
    assert manifiesto.todas()[0]["bytes_origen"] == 4_000
