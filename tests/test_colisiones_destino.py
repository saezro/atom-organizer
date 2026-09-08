"""Test del aviso de colisión de destino (LEDGER-mejoras-postv3483, item 1).

`ruta_origen` es UNIQUE en el manifiesto, pero `ruta_salida_original` no: dos
imágenes de origen distinto pueden resolver al MISMO fichero final. El
`os.replace` de `apply.py` es atómico pero silencioso -- la segunda escritura
pisa a la primera sin error ni aviso -- así que la única forma de que alguien
se entere es detectarlo aparte y avisar. Aquí NO se cambia el comportamiento
de escritura (se sigue pisando igual): solo se prueba la detección + el aviso.
"""
from atom_core.apply import _reportar_colisiones_destino
from atom_core.manifiesto import FilaManifiesto, Manifiesto


def _fila(ruta_origen, ruta_salida_original, **cambios):
    base = dict(
        ruta_origen=ruta_origen,
        tipo="RGB",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb="PB1",
        vuelo="V01",
        nombre_nuevo="20260501_100000.JPG",
        angulo_giro=90,
        pct_recorte=0.8,
        comprime=True,
        ruta_salida_original=ruta_salida_original,
        ruta_salida_crop=None,
        ruta_salida_tiff=None,
        unassigned=False,
    )
    base.update(cambios)
    return FilaManifiesto(**base)


class _SignalFalsa:
    """Doble de `progress_callback`: solo apunta lo que se le emite."""

    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *args, **kwargs):
        self.mensajes.append(valor)


def test_colisiones_ruta_salida_original_detecta_dos_origenes_mismo_destino(tmp_path):
    """Dos filas con `ruta_origen` distinto y el MISMO
    `ruta_salida_original` deben salir en la consulta de colisiones."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/A.JPG", "/destino/PB1_V01/RGB/20260501_100000.JPG"),
        _fila("/origen/B.JPG", "/destino/PB1_V01/RGB/20260501_100000.JPG"),
        _fila("/origen/C.JPG", "/destino/PB1_V01/RGB/20260501_100001.JPG"),
    ])

    colisiones = manifiesto.colisiones_ruta_salida_original()

    assert len(colisiones) == 1
    assert colisiones[0]["ruta_salida_original"] == (
        "/destino/PB1_V01/RGB/20260501_100000.JPG"
    )
    assert colisiones[0]["total"] == 2
    manifiesto.cerrar()


def test_colisiones_ruta_salida_original_vacio_sin_colisiones(tmp_path):
    """Sin destinos repetidos, la consulta no reporta nada (cero coste de
    falsos positivos)."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/A.JPG", "/destino/PB1_V01/RGB/20260501_100000.JPG"),
        _fila("/origen/B.JPG", "/destino/PB1_V01/RGB/20260501_100001.JPG"),
    ])

    assert manifiesto.colisiones_ruta_salida_original() == []
    manifiesto.cerrar()


def test_reportar_colisiones_destino_emite_log_con_prefijo_colision(tmp_path):
    """`_reportar_colisiones_destino` traduce la consulta a una línea de log
    reconocible (`[colisión]`) con cuántas colisiones y qué destinos."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/A.JPG", "/destino/PB1_V01/RGB/20260501_100000.JPG"),
        _fila("/origen/B.JPG", "/destino/PB1_V01/RGB/20260501_100000.JPG"),
    ])
    callback = _SignalFalsa()

    _reportar_colisiones_destino(manifiesto, callback)

    assert len(callback.mensajes) == 1
    assert "[colisión]" in callback.mensajes[0]
    assert "/destino/PB1_V01/RGB/20260501_100000.JPG" in callback.mensajes[0]
    manifiesto.cerrar()


def test_reportar_colisiones_destino_no_emite_nada_sin_colisiones(tmp_path):
    """Sin colisiones, no se emite ninguna línea (no hay que ensuciar el log
    de un run limpio)."""
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila("/origen/A.JPG", "/destino/PB1_V01/RGB/20260501_100000.JPG"),
    ])
    callback = _SignalFalsa()

    _reportar_colisiones_destino(manifiesto, callback)

    assert callback.mensajes == []
    manifiesto.cerrar()
