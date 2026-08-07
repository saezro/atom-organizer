"""Reparto de la etapa `struct` entre N tareas (`sharding.toca_imagen`).

La clase de fallo que cubren estos tests no se ve en ningún log: si el reparto
deja una imagen sin dueña, esa foto se queda en la raíz y `checking_results` la
aparta a SIN_ORDENAR — el run termina en ÁMBAR y el vuelo se entrega incompleto.
Si le pone dos dueñas, se mueve dos veces. Ninguna de las dos cosas rompe el
proceso: salen con exit code 0.
"""
import datetime as _dt
import os
import zlib

import pytest

from atom_core import sharding
from pipeline import GenStructFolder
from utils import OrganizerLogger as ol


# Andamiaje equivalente al de `test_gen_struct_folder`. Duplicado y no importado:
# `tests/` no es un paquete ni está en sys.path, y meterlo ahí para compartir
# cinco líneas acopla dos ficheros de test que no tienen por qué caer juntos.
class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _utils(tmp_path):
    from utils import Utils
    return Utils(ol("test_reparto_struct", log_dir=str(tmp_path / "Logs"),
                    create_file_handler=False))


def _make_gsf(tmp_path):
    logger = ol("test_reparto_struct", log_dir=str(tmp_path / "Logs"),
                create_file_handler=False)
    return GenStructFolder(logger)


def _estadillo(path, filas):
    lineas = ["PB;Vuelo;Fecha;Hora_de_inicio;Hora_final"]
    lineas += [";".join(str(c) for c in fila) for fila in filas]
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(path)


def _destino_plano(root, imagenes):
    """Destino tal y como lo deja `split`: RGB/ y TERMICA/ planas."""
    for sub in ("RGB", "TERMICA"):
        (root / sub).mkdir(parents=True)
        for nombre in imagenes:
            (root / sub / nombre).write_bytes(b"X")


def _con_timestamps(gsf, horas):
    def _fake(ruta):
        return _dt.datetime.strptime("2026:03:17_" + horas[os.path.basename(ruta)],
                                     "%Y:%m:%d_%H:%M:%S")
    gsf.exif_management_obj.get_timestamp_from_image = _fake


NOMBRES = [f"DJI_{i:04d}.JPG" for i in range(1, 2517)]  # el vuelo de referencia


@pytest.mark.parametrize("count", [1, 2, 3, 4, 8, 16])
def test_cada_imagen_tiene_exactamente_una_duena(count):
    """Lo único que de verdad importa: partición, no solape ni hueco."""
    duenas = {}
    for index in range(count):
        for nombre in NOMBRES:
            if sharding.toca_imagen(nombre, index, count):
                duenas.setdefault(nombre, []).append(index)

    sin_duena = [n for n in NOMBRES if n not in duenas]
    con_varias = {n: d for n, d in duenas.items() if len(d) > 1}
    assert not sin_duena, f"{len(sin_duena)} imágenes sin dueña (acabarían en SIN_ORDENAR)"
    assert not con_varias, f"{len(con_varias)} imágenes con más de una dueña (se moverían dos veces)"


def test_no_depende_del_estado_de_la_carpeta():
    """El reparto se decide con el NOMBRE y nada más.

    Es la razón de ser de `toca_imagen`: durante `struct` la carpeta que se
    reparte es la misma que se está vaciando, así que dos tareas que listen en
    momentos distintos ven listas distintas. Un reparto por posición (`i % N`)
    daría respuestas distintas para la misma foto; este no.
    """
    completa = list(NOMBRES)
    medio_vaciada = [n for i, n in enumerate(NOMBRES) if i % 3]  # otras tareas ya movieron lo suyo

    for index in range(8):
        de_completa = [n for n in completa if sharding.toca_imagen(n, index, 8)]
        de_vaciada = [n for n in medio_vaciada if sharding.toca_imagen(n, index, 8)]
        # Lo que sigue presente se decide igual, mire cuando mire.
        assert de_vaciada == [n for n in de_completa if n in set(medio_vaciada)]


def test_es_estable_entre_procesos():
    """No puede usar `hash()` de Python: está aleatorizado por proceso
    (PYTHONHASHSEED) y cada tarea del Job es un proceso distinto — el reparto
    saldría diferente en cada una."""
    for nombre in NOMBRES[:50]:
        esperado = zlib.crc32(nombre.encode("utf-8")) % 8
        mios = [i for i in range(8) if sharding.toca_imagen(nombre, i, 8)]
        assert mios == [esperado]


def test_reparte_parejo():
    """Los nombres de un vuelo son correlativos; el CRC los distribuye uniforme.
    Si una tarea se llevara el doble que otra, el reloj de pared no bajaría."""
    cuentas = [sum(1 for n in NOMBRES if sharding.toca_imagen(n, i, 8)) for i in range(8)]
    media = len(NOMBRES) / 8
    assert max(cuentas) < media * 1.25, f"reparto desequilibrado: {cuentas}"
    assert min(cuentas) > media * 0.75, f"reparto desequilibrado: {cuentas}"


def test_sin_reparto_le_tocan_todas():
    """`(0, 1)` es el par neutro: la corrida de la app de escritorio no cambia."""
    assert all(sharding.toca_imagen(n, 0, 1) for n in NOMBRES)


def test_los_nombres_no_ascii_no_revientan():
    """Los ficheros llegan de tarjetas SD con nombres que no controlamos."""
    for nombre in ["ÑOÑO_01.JPG", "foto con espacio.JPG", "日本_2.JPG", ""]:
        assert isinstance(sharding.toca_imagen(nombre, 0, 4), bool)


# --- de extremo a extremo: N tareas tienen que dar lo mismo que una -----------

_FILAS = [("1", "1", "2026:03:17", "10:00:00", "10:20:00"),
          ("2", "1", "2026:03:17", "11:00:00", "11:20:00"),
          ("3", "1", "2026:03:17", "12:00:00", "12:20:00")]

_IMAGENES = [f"DJI_{i:04d}.jpg" for i in range(1, 61)]


def _horas():
    """20 fotos en cada uno de los tres vuelos, más 3 fuera de toda ventana."""
    horas = {}
    for i, nombre in enumerate(_IMAGENES):
        if i < 19:
            horas[nombre] = f"10:{i % 20:02d}:30"
        elif i < 38:
            horas[nombre] = f"11:{i % 20:02d}:30"
        elif i < 57:
            horas[nombre] = f"12:{i % 20:02d}:30"
        else:
            horas[nombre] = "23:00:00"
    return horas


def _arbol(root):
    """{ruta relativa: contenido} de todo lo que cuelga de `root`."""
    salida = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for f in filenames:
            completa = os.path.join(dirpath, f)
            salida[os.path.relpath(completa, root)] = open(completa, "rb").read()
    return salida


def _corre_struct(tmp_path, nombre_caso, shard_count):
    """Ejecuta `struct` con `shard_count` tareas sobre un mismo destino y
    devuelve el árbol resultante.

    Las tareas se ejecutan una tras otra a propósito, y eso NO es una
    simplificación que debilite el test: es el caso que rompe el reparto por
    posición. Cuando arranca la tarea 1, la 0 ya ha movido sus fotos, así que
    lista una carpeta a medio vaciar — exactamente lo que le pasa a una tarea
    lenta en Cloud Run, y donde `i % N` le asignaría otras imágenes.
    """
    caso = tmp_path / nombre_caso
    caso.mkdir()
    root = caso / "destino"
    _destino_plano(root, _IMAGENES)
    # El estadillo se llama igual en todos los casos: su NOMBRE acaba dentro del
    # árbol (se copia a ESTADILLOS/) y compararíamos casos que difieren solo en
    # eso.
    estadillo = _estadillo(caso / "est.csv", _FILAS)

    for index in range(shard_count):
        gsf = _make_gsf(tmp_path)
        gsf.total_images_number = len(_IMAGENES) * 2
        _con_timestamps(gsf, _horas())
        gsf.gen_folder_struct(estadillo, str(root), str(root), True, 0, 0, 0,
                              FakeSignal(), FakeSignal(),
                              shard_index=index, shard_count=shard_count)
    return _arbol(root)


@pytest.mark.parametrize("count", [2, 3, 8])
def test_repartido_da_el_mismo_arbol_que_una_sola_tarea(tmp_path, count):
    """El contrato entero en un assert: mismo conjunto de ficheros, en las mismas
    rutas y con el mismo contenido. Si el reparto pierde una foto, la duplica o
    la manda a otro vuelo, esto lo caza."""
    una = _corre_struct(tmp_path, "una", 1)
    varias = _corre_struct(tmp_path, f"varias{count}", count)
    assert varias == una


@pytest.mark.parametrize("count", [2, 8])
def test_no_se_pierde_ninguna_imagen_en_el_reparto(tmp_path, count):
    """Redundante con el anterior a propósito: si el árbol cambiara por otro
    motivo (un fichero de log, otro estadillo), el assert de arriba fallaría sin
    decir si además se perdieron fotos. Esto lo dice."""
    arbol = _corre_struct(tmp_path, f"cuenta{count}", count)
    jpgs = [r for r in arbol if r.endswith(".jpg")]
    # Cada imagen existe en RGB y en TERMICA: 60 * 2.
    assert len(jpgs) == len(_IMAGENES) * 2


def test_el_estadillo_se_copia_una_sola_vez(tmp_path):
    """Con N tareas copiándolo, el desempate por contador dejaría `est_1.csv`…
    `est_7.csv` en ESTADILLOS/: ocho copias idénticas del mismo fichero."""
    arbol = _corre_struct(tmp_path, "estadillo", 8)
    copias = [r for r in arbol if "ESTADILLOS" in r]
    assert len(copias) == 1, f"el estadillo se copió {len(copias)} veces: {copias}"


# --- cuadre de contadores por tarea ------------------------------------------
#
# El reparto movía bien las imágenes, pero las OCHO tareas salían con exit 1 en
# la primera corrida real (v3.4.31, 2026-08-07): `get_summarize` compara el
# total esperado con las procesadas, y ambos números habían dejado de ser
# comparables por tarea. Es un fallo de contabilidad, no de datos —pero tiñe el
# run de rojo y aborta el pipeline, así que vale lo mismo que uno de datos.

def test_el_total_esperado_de_cada_tarea_es_solo_el_suyo(tmp_path):
    """Contar el destino ENTERO deja a las ocho comparando ~300 contra 2.516."""
    utils_obj = _utils(tmp_path)

    destino = tmp_path / "destino"
    _destino_plano(destino, _IMAGENES)

    global_ = utils_obj.contar_imagenes_or_tmc(str(destino))
    por_shard = [
        utils_obj.contar_imagenes_or_tmc(
            str(destino),
            filtro_nombre=lambda n, i=i: sharding.toca_imagen(n, i, 8))
        for i in range(8)]

    assert global_ == len(_IMAGENES) * 2
    # Partición: ninguna tarea espera de más ni de menos, y entre todas suman el total.
    assert sum(por_shard) == global_
    assert max(por_shard) < global_, "el filtro no está filtrando nada"


def test_las_que_no_caen_en_ningun_vuelo_cuentan_como_procesadas(tmp_path):
    """Sin barrido en `struct`, las imágenes fuera del estadillo se quedan en la
    raíz sin pasar por `_mover_pares`. Si no se cuentan, el cuadre falla aunque
    no se haya perdido nada: es exactamente el falso rojo de v3.4.31."""
    from atom_core.phases import PipelinePhasesMixin

    destino = tmp_path / "destino"
    _destino_plano(destino, _IMAGENES)

    host = PipelinePhasesMixin.__new__(PipelinePhasesMixin)
    host.utils_obj = _utils(tmp_path)

    sobrantes = [host._contar_sobrantes_propios(str(destino), i, 8) for i in range(8)]
    assert sum(sobrantes) == len(_IMAGENES) * 2, (
        "los sobrantes de las ocho tareas tienen que sumar todo lo que quedó suelto")

    # Y solo los propios: lo que cuenta una tarea no lo cuenta ninguna otra.
    una_sola = host._contar_sobrantes_propios(str(destino), 0, 1)
    assert una_sola == len(_IMAGENES) * 2
    assert sobrantes[0] < una_sola


def test_una_imagen_perdida_de_verdad_sigue_descuadrando(tmp_path):
    """El fix no puede convertir el cuadre en un adorno que siempre da verde:
    si falta una imagen de las suyas, la tarea tiene que seguir viéndolo."""
    from atom_core.phases import PipelinePhasesMixin

    destino = tmp_path / "destino"
    _destino_plano(destino, _IMAGENES)
    host = PipelinePhasesMixin.__new__(PipelinePhasesMixin)
    host.utils_obj = _utils(tmp_path)

    mias = [n for n in _IMAGENES if sharding.toca_imagen(n, 0, 8)]
    assert mias, "el shard 0 tiene que tener alguna imagen para que el test valga"

    antes = host._contar_sobrantes_propios(str(destino), 0, 8)
    (destino / "RGB" / mias[0]).unlink()
    despues = host._contar_sobrantes_propios(str(destino), 0, 8)
    assert despues == antes - 1
