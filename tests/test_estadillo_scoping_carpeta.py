"""Scoping por carpeta: cada imagen solo compite contra las ventanas del
estadillo cuyo directorio es su ANCESTRO MÁS CERCANO, no contra TODOS los
estadillos fusionados. Cubre los 3 layouts reales (`sel/{DCIM,estadillo}`,
`sel/DIA1|DIA2/{DCIM,estadillo}`, `sel/PILOTO_A|PILOTO_B/{DCIM,estadillo}`
con horas SOLAPADAS), el fallback de carpeta padre/ajena
(`estadillo.detectar_estadillos`, patrón `KLxx/estadillo.csv` +
`KLxx/FOTOS`), y la resolución de colisión (PB, Vuelo) coherente con
`pipeline.py` (sufijo de fecha si son estadillos distintos con fecha
distinta; error si son estadillos distintos con la MISMA fecha).

Dobles a mano (`_PipelineDePrueba`, `_ExifDePrueba`, `_Signal`), mismo estilo
que `tests/test_indice_organizado.py` -no `unittest.mock`-.
"""
import datetime as dt
import os

import pytest

from atom_core import estadillo as estadillo_mod
from atom_core.indice import ErrorColisionEstadillo, ErrorEstadillosMismaCarpeta, construir_indice
from atom_core.manifiesto import Manifiesto
from utils import SplitImagesConfig


class _Signal:
    def emit(self, *args, **kwargs):
        pass


class _PipelineDePrueba:
    """Mismo doble que `test_indice_organizado.py`: agrupa las funciones ya
    existentes del motor viejo que el índice reutiliza."""

    def __init__(self, ventanas: dict):
        # ventanas: {(fecha, horaInicio, horaFinal): (inicio, fin)}
        self._ventanas = ventanas
        self.percentage_by_models = {}

    def ventana_horaria_vuelo(self, fecha, horaInicio, horaFinal,
                              margen_segundos, desfase_horas, desfase_minutos):
        return self._ventanas[(fecha, horaInicio, horaFinal)]

    def nombre_destino(self, image, input_folder, rename, mismatch_hours,
                       mismatch_minutes, ruta_local=None, timestamp=None):
        return ""

    def get_percentage_by_model(self, model, percentage_cropping_dict):
        return percentage_cropping_dict[model.strip().upper()]

    def read_auto_rotate_degree(self, input_folder, progress_callback):
        return 0


class _ExifDePrueba:
    def __init__(self, timestamps=None, modelos=None, yaws=None, gps=None, fallar_en=None):
        self._timestamps = timestamps or {}
        self._modelos = modelos or {}
        self._yaws = yaws or {}
        self._gps = gps or {}
        self._fallar_en = fallar_en or set()

    def get_timestamp_from_image(self, pathImagen):
        if pathImagen in self._fallar_en:
            raise RuntimeError("EXIF corrupto (simulado)")
        return self._timestamps.get(pathImagen)

    def get_model(self, filename, progress_callback):
        return self._modelos.get(filename, "M3T")

    def get_gimbal_yaw_pitch(self, filename, bloque_xmp=None):
        return [str(self._yaws.get(filename, 0.0)), "0"]

    def leerLatitudLongitudAltitud_exif_DJI(self, pathImagen, progress_callback):
        return self._gps.get(pathImagen)


def _escribir_estadillo(ruta, filas):
    """`filas`: lista de tuplas (pb, vuelo, fecha, hora_inicio, hora_final)."""
    os.makedirs(os.path.dirname(str(ruta)), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n")
        for pb, vuelo, fecha, hora_inicio, hora_final in filas:
            fh.write(f"{pb};{vuelo};{fecha};{hora_inicio};{hora_final}\n")


def _cfg(tmp_path, input_folder, estad, **overrides):
    base = dict(
        input_folder=str(input_folder),
        output_folder=str(tmp_path / "destino"),
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="_D",
        estad=estad,
        choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=False,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=False, cropping_mode_auto=True, crop_percentage="50",
        gen_meta_location=False, gen_thumbnails=False, seconds_range=5.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5,
        gen_thumbnails_max_error=50, gen_thumbnails_subs_to_angle=5,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=False, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    base.update(overrides)
    os.makedirs(base["input_folder"], exist_ok=True)
    return SplitImagesConfig(**base)


def _crear_imagen(carpeta, nombre, contenido=b""):
    """`contenido` por defecto vacío (basta para la mayoría de tests, que no
    tocan disco de verdad vía dobles). Los tests que juntan dos imágenes con
    el MISMO nombre y el MISMO timestamp EXIF (dos pilotos disparando a la
    vez) necesitan tamaños DISTINTOS: `manifiesto.clave_imagen` desempata
    por `nombre|timestamp_exif|bytes_origen`, y dos ficheros vacíos
    colisionarían en la misma clave -perdiendo una de las dos filas-, algo
    que no pasaría con fotos reales (nunca pesan exactamente lo mismo)."""
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "wb") as fh:
        fh.write(contenido)
    return ruta


def _manifiesto(tmp_path, nombre="manifiesto.db"):
    m = Manifiesto(tmp_path / nombre)
    m.crear_esquema()
    return m


# --- Layout 1: sel/{DCIM/..., estadillo.xlsx} --------------------------------

def test_layout1_un_estadillo_en_la_raiz_reclama_todo_el_arbol(tmp_path):
    sel = tmp_path / "sel"
    _escribir_estadillo(sel / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    dcim = sel / "DCIM"
    cfg = _cfg(tmp_path, sel, str(sel / "estadillo.csv"))
    ruta = _crear_imagen(dcim, "DJI_0001_D.JPG")

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["unassigned"] == 0
    assert fila["vuelo"] == "1"
    manifiesto.cerrar()


# --- Layout 2: sel/DIA1/{...}, sel/DIA2/{...} --------------------------------

def test_layout2_cada_dia_aislado_en_su_carpeta(tmp_path):
    """Dos días, cada uno con su propio estadillo y su propio PB1_V1: sin
    scoping, la ventana del DIA1 podría reclamar por error una imagen del
    DIA2 si las horas del reloj coincidieran. Con scoping cada imagen solo
    compite contra la ventana de SU carpeta."""
    sel = tmp_path / "sel"
    dia1 = sel / "DIA1"
    dia2 = sel / "DIA2"
    _escribir_estadillo(dia1 / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(dia2 / "estadillo.csv", [
        ("1", "1", "2024:06:02", "10:00:00", "10:10:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(dia1 / "estadillo.csv"), str(dia2 / "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    ruta1 = _crear_imagen(dia1 / "DCIM", "DJI_0001_D.JPG")
    ruta2 = _crear_imagen(dia2 / "DCIM", "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
        ("2024:06:02", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 2, 10, 0, 0), dt.datetime(2024, 6, 2, 10, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={
        ruta1: dt.datetime(2024, 6, 1, 10, 5, 0),
        ruta2: dt.datetime(2024, 6, 2, 10, 5, 0),
    })
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta1]["unassigned"] == 0
    assert filas[ruta2]["unassigned"] == 0
    assert filas[ruta1]["vuelo"] == "1"
    assert filas[ruta2]["vuelo"] == "1"
    manifiesto.cerrar()


# --- Layout 3: 2 pilotos mismo día, horas SOLAPADAS --------------------------

def test_layout3_dos_pilotos_mismo_dia_horas_solapadas_no_se_contaminan(tmp_path):
    """El caso que motiva la tarea: dos pilotos vuelan el MISMO día con
    ventanas horarias que SOLAPAN. Sin scoping, una imagen del piloto B
    (10:05) cae dentro de la ventana del piloto A (10:00-10:20) y se le
    asignaría el vuelo del piloto A por error -el primero en el orden del
    estadillo fusionado se la quedaría-. Con scoping por carpeta, cada
    imagen solo compite contra la ventana de SU propio piloto."""
    sel = tmp_path / "sel"
    piloto_a = sel / "PILOTO_A"
    piloto_b = sel / "PILOTO_B"
    _escribir_estadillo(piloto_a / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:20:00"),
    ])
    _escribir_estadillo(piloto_b / "estadillo.csv", [
        ("2", "1", "2024:06:01", "10:00:00", "10:20:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(piloto_a / "estadillo.csv"), str(piloto_b / "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    ruta_a = _crear_imagen(piloto_a / "DCIM", "DJI_0001_D.JPG", contenido=b"A")
    ruta_b = _crear_imagen(piloto_b / "DCIM", "DJI_0001_D.JPG", contenido=b"BB")

    # Misma ventana horaria (10:00-10:20) para los dos PB distintos: si el
    # índice NO escopara por carpeta, la primera ventana del estadillo
    # fusionado (piloto A) se llevaría también la imagen del piloto B.
    ventanas = {
        ("2024:06:01", "10:00:00", "10:20:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 20, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    ts_solapado = dt.datetime(2024, 6, 1, 10, 5, 0)
    exif = _ExifDePrueba(timestamps={ruta_a: ts_solapado, ruta_b: ts_solapado})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta_a]["pb"] == "1"
    assert filas[ruta_b]["pb"] == "2"
    manifiesto.cerrar()


# --- Fallback: estadillo en carpeta PADRE/ajena al árbol de imágenes --------

def test_fallback_estadillo_fuera_del_arbol_se_compara_por_timestamp_global(tmp_path):
    """Patrón `KLxx/estadillo.csv` + `KLxx/FOTOS` como origen -el estadillo
    vive en un directorio del que `cfg.input_folder` NO cuelga- (p. ej. un
    `--estadillo` elegido a mano desde otra ubicación cualquiera): ningún
    directorio de estadillo es ancestro de las imágenes, así que cae en el
    pool sin carpeta propia y se compara por timestamp contra todas sus
    filas -el comportamiento de siempre, sin romper ese caso-."""
    origen = tmp_path / "origen_ajeno"
    estad_dir = tmp_path / "otro_lado_cualquiera"
    _escribir_estadillo(estad_dir / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path, origen, str(estad_dir / "estadillo.csv"))
    ruta = _crear_imagen(origen, "DJI_0001_D.JPG")

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["unassigned"] == 0
    assert fila["vuelo"] == "1"
    manifiesto.cerrar()


# --- Colisión (PB, Vuelo) entre carpetas distintas ---------------------------

def test_colision_fecha_distinta_entre_carpetas_resuelve_con_sufijo(tmp_path):
    """Mismo PB+Vuelo en DIA1 y DIA2 (dos carpetas escopadas distintas), con
    fecha distinta: ya no aborta, cada imagen sale con su propio sufijo de
    fecha en la ruta de salida -coherente con `pipeline.py`-."""
    sel = tmp_path / "sel"
    dia1 = sel / "DIA1"
    dia2 = sel / "DIA2"
    _escribir_estadillo(dia1 / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(dia2 / "estadillo.csv", [
        ("1", "1", "2024:06:02", "11:00:00", "11:10:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(dia1 / "estadillo.csv"), str(dia2 / "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    ruta1 = _crear_imagen(dia1 / "DCIM", "DJI_0001_D.JPG")
    ruta2 = _crear_imagen(dia2 / "DCIM", "DJI_0002_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
        ("2024:06:02", "11:00:00", "11:10:00"): (
            dt.datetime(2024, 6, 2, 11, 0, 0), dt.datetime(2024, 6, 2, 11, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={
        ruta1: dt.datetime(2024, 6, 1, 10, 5, 0),
        ruta2: dt.datetime(2024, 6, 2, 11, 5, 0),
    })
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta1]["vuelo"] == "1" and filas[ruta2]["vuelo"] == "1"
    assert "PB1_V1_20240601" in filas[ruta1]["ruta_salida_original"]
    assert "PB1_V1_20240602" in filas[ruta2]["ruta_salida_original"]
    manifiesto.cerrar()


def test_colision_misma_fecha_entre_carpetas_aborta_con_los_dos_ficheros(tmp_path):
    """Mismo PB+Vuelo en DIA1 y DIA2, pero con la MISMA fecha -dos pilotos
    que de verdad chocan-: el sufijo de fecha sería idéntico para los dos,
    así que sigue abortando ANTES de escribir nada, con los dos ficheros de
    origen nombrados en el mensaje."""
    sel = tmp_path / "sel"
    piloto_a = sel / "PILOTO_A"
    piloto_b = sel / "PILOTO_B"
    _escribir_estadillo(piloto_a / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(piloto_b / "estadillo.csv", [
        ("1", "1", "2024:06:01", "11:00:00", "11:10:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(piloto_a / "estadillo.csv"), str(piloto_b / "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    _crear_imagen(piloto_a / "DCIM", "DJI_0001_D.JPG")

    pipeline = _PipelineDePrueba({})
    exif = _ExifDePrueba()
    manifiesto = _manifiesto(tmp_path)

    with pytest.raises(ErrorColisionEstadillo) as excinfo:
        construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    mensaje = str(excinfo.value)
    # Mismo nombre de fichero en las dos carpetas (`estadillo.csv`): el
    # mensaje tiene que distinguirlos por carpeta, no fundirlos en un único
    # "origen" (ver `estadillo.COLUMNA_ORIGEN_RUTA`).
    assert "PILOTO_A" in mensaje
    assert "PILOTO_B" in mensaje
    assert manifiesto.todas() == []
    manifiesto.cerrar()


# --- estadillo.agrupar_rutas_por_carpeta / detectar_colisiones_mismo_dia ----

def test_agrupar_rutas_por_carpeta_agrupa_y_preserva_orden(tmp_path):
    a = tmp_path / "x" / "e1.csv"
    b = tmp_path / "x" / "e2.csv"
    c = tmp_path / "y" / "e3.csv"
    for p in (a, b, c):
        os.makedirs(p.parent, exist_ok=True)
        p.write_text("")

    grupos = estadillo_mod.agrupar_rutas_por_carpeta([str(a), str(c), str(b)])

    claves = list(grupos.keys())
    assert len(claves) == 2
    dir_x = os.path.normcase(os.path.normpath(str(tmp_path / "x")))
    dir_y = os.path.normcase(os.path.normpath(str(tmp_path / "y")))
    assert grupos[dir_x] == [str(a), str(b)]
    assert grupos[dir_y] == [str(c)]


def test_detectar_colisiones_mismo_dia_2_origenes_mismo_dia():
    import pandas as pd
    df = pd.DataFrame({
        "PB": [1, 1], "Vuelo": [1, 1], "Fecha": ["2026:03:17", "2026:03:17"],
        estadillo_mod.COLUMNA_ORIGEN: ["a.csv", "b.csv"],
    })
    cols = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha"}
    colisiones = estadillo_mod.detectar_colisiones_mismo_dia(df, cols)
    assert ("1", "1", "2026:03:17") in colisiones
    assert sorted(colisiones[("1", "1", "2026:03:17")]) == ["a.csv", "b.csv"]


def test_detectar_colisiones_mismo_dia_mismo_origen_no_colisiona():
    """La misma fila repetida (o dos filas del MISMO fichero) no es una
    colisión de origen: solo cuenta cuando el PB+Vuelo+Fecha viene de 2+
    ficheros DISTINTOS."""
    import pandas as pd
    df = pd.DataFrame({
        "PB": [1, 1], "Vuelo": [1, 1], "Fecha": ["2026:03:17", "2026:03:17"],
        estadillo_mod.COLUMNA_ORIGEN: ["a.csv", "a.csv"],
    })
    cols = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha"}
    assert estadillo_mod.detectar_colisiones_mismo_dia(df, cols) == {}


# --- Decisión del responsable 1: 2+ estadillos en la MISMA carpeta bloquea -----------

class _SignalGrabador:
    """Como `_Signal`, pero guarda cada `emit(...)` para poder comprobar que
    salió el aviso esperado (WARNING) sin abortar el run."""

    def __init__(self):
        self.mensajes: list[str] = []

    def emit(self, *args, **kwargs):
        if args:
            self.mensajes.append(str(args[0]))


def test_dos_estadillos_en_la_misma_carpeta_bloquea_antes_de_organizar(tmp_path):
    """Dos ficheros de estadillo sueltos en la MISMA carpeta (el despiste de
    no separar cada piloto en su propia subcarpeta): antes se fusionaban en
    silencio con `combinar_estadillos` (gana el primero); ahora bloquea ANTES
    de leer ninguna imagen, con la carpeta y los dos nombres en el mensaje."""
    sel = tmp_path / "sel"
    _escribir_estadillo(sel / "piloto_a.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(sel / "piloto_b.csv", [
        ("2", "1", "2024:06:01", "11:00:00", "11:10:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(sel / "piloto_a.csv"), str(sel / "piloto_b.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    _crear_imagen(sel / "DCIM", "DJI_0001_D.JPG")

    pipeline = _PipelineDePrueba({})
    exif = _ExifDePrueba()
    manifiesto = _manifiesto(tmp_path)

    with pytest.raises(ErrorEstadillosMismaCarpeta) as excinfo:
        construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    mensaje = str(excinfo.value)
    assert "piloto_a.csv" in mensaje
    assert "piloto_b.csv" in mensaje
    assert str(sel) in mensaje
    assert manifiesto.todas() == []
    manifiesto.cerrar()


def test_dos_estadillos_externos_en_la_misma_carpeta_no_bloquea(tmp_path):
    """Dos ficheros de estadillo en la MISMA carpeta pero FUERA de
    `input_folder` (elegidos a mano vía `--estadillo`, p.ej. una carpeta
    compartida por varios pilotos que ninguno de los dos vive dentro del
    árbol que se está organizando): el bloqueo de `ErrorEstadillosMismaCarpeta`
    (Decisión del responsable 1) solo aplica a estadillos AUTODETECTADOS dentro de
    `input_folder` -estos dos no lo son-, así que el run NO aborta y conserva
    el comportamiento de siempre (fusión con sufijo de fecha si colisionan)."""
    compartida = tmp_path / "compartida"
    _escribir_estadillo(compartida / "piloto_a.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(compartida / "piloto_b.csv", [
        ("1", "1", "2024:06:02", "11:00:00", "11:10:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(compartida / "piloto_a.csv"), str(compartida / "piloto_b.csv")])
    origen = tmp_path / "origen"
    cfg = _cfg(tmp_path, origen, estad)
    ruta_dia1 = _crear_imagen(origen, "DJI_0001_D.JPG")
    ruta_dia2 = _crear_imagen(origen, "DJI_0002_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
        ("2024:06:02", "11:00:00", "11:10:00"): (
            dt.datetime(2024, 6, 2, 11, 0, 0), dt.datetime(2024, 6, 2, 11, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={
        ruta_dia1: dt.datetime(2024, 6, 1, 10, 5, 0),
        ruta_dia2: dt.datetime(2024, 6, 2, 11, 5, 0),
    })
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta_dia1]["vuelo"] == "1"
    assert filas[ruta_dia2]["vuelo"] == "1"
    assert filas[ruta_dia1]["unassigned"] == 0
    assert filas[ruta_dia2]["unassigned"] == 0
    manifiesto.cerrar()


# --- Decisión del responsable 2: estadillo autodetectado sin fotos no reclama ajenas -

def test_estadillo_autodetectado_sin_fotos_no_reclama_imagenes_ajenas(tmp_path):
    """Un estadillo AUTODETECTADO (vive dentro de `input_folder`, lo habría
    ofrecido `estadillo.detectar_estadillos`) sin ninguna foto debajo de su
    propia carpeta: antes caía en `ventanas_sin_carpeta` y podía reclamar
    fotos de cualquier otro sitio del lote por timestamp. Ahora se avisa y
    NO se usa -las fotos de la otra carpeta (con SU propio estadillo) quedan
    con su asignación normal, sin contaminar-."""
    sel = tmp_path / "sel"
    vacio = sel / "ESTADILLO_SIN_FOTOS"
    con_fotos = sel / "PILOTO_A"
    _escribir_estadillo(vacio / "suelto.csv", [
        ("9", "9", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(con_fotos / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(vacio / "suelto.csv"), str(con_fotos / "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    ruta = _crear_imagen(con_fotos / "DCIM", "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)})
    manifiesto = _manifiesto(tmp_path)
    grabador = _SignalGrabador()

    construir_indice(cfg, pipeline, exif, manifiesto, grabador, _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    # La foto de PILOTO_A se asigna con su propio estadillo (PB1_V1), NUNCA
    # con el del estadillo huérfano (PB9_V9) -sin scoping/sin la regla nueva,
    # el pool compartido se la habría llevado por timestamp-.
    assert fila["pb"] == "1"
    assert fila["vuelo"] == "1"
    assert any("no tiene imágenes en su carpeta" in m for m in grabador.mensajes)
    manifiesto.cerrar()


def test_layout3_regresion_pilotos_a_b_horas_solapadas_siguen_separados(tmp_path):
    """Regresión explícita: el layout 3 (PILOTO_A/PILOTO_B, mismo día, horas
    SOLAPADAS, cada uno en su propia subcarpeta con un único estadillo) sigue
    intacto con las dos reglas nuevas -ni el bloqueo de "misma carpeta" (aquí
    cada carpeta tiene solo 1 fichero) ni el aviso de "sin fotos" (las dos
    tienen fotos debajo) deben tocarlo-."""
    sel = tmp_path / "sel"
    piloto_a = sel / "PILOTO_A"
    piloto_b = sel / "PILOTO_B"
    _escribir_estadillo(piloto_a / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:20:00"),
    ])
    _escribir_estadillo(piloto_b / "estadillo.csv", [
        ("2", "1", "2024:06:01", "10:00:00", "10:20:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(piloto_a / "estadillo.csv"), str(piloto_b / "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    ruta_a = _crear_imagen(piloto_a / "DCIM", "DJI_0001_D.JPG", contenido=b"A")
    ruta_b = _crear_imagen(piloto_b / "DCIM", "DJI_0001_D.JPG", contenido=b"BB")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:20:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 20, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    ts_solapado = dt.datetime(2024, 6, 1, 10, 5, 0)
    exif = _ExifDePrueba(timestamps={ruta_a: ts_solapado, ruta_b: ts_solapado})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta_a]["pb"] == "1"
    assert filas[ruta_b]["pb"] == "2"
    manifiesto.cerrar()


# --- Regresión: `cfg.estad` sin normalizar frente a `detectar_estadillos` ----
# (rutas abspath) -bug real: comparar strings crudos hacía que un estadillo
# DENTRO de `input_folder` se tratara como externo si `--estadillo` llegaba
# relativo, con otra capitalización o con separadores distintos-.

def test_estadillo_relativo_en_la_misma_carpeta_bloquea(tmp_path, monkeypatch):
    """`--estadillo` relativo (como llega desde `organize_cli.py`) a un
    fichero que está en la MISMA carpeta que otro estadillo AUTODETECTADO:
    debe seguir bloqueando con `ErrorEstadillosMismaCarpeta` igual que con
    rutas absolutas -antes del fix, al comparar por string crudo contra
    `detectar_estadillos` (abspath), la ruta relativa no calificaba como
    autodetectada y el bloqueo se saltaba."""
    sel = tmp_path / "sel"
    _escribir_estadillo(sel / "piloto_a.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(sel / "piloto_b.csv", [
        ("2", "1", "2024:06:01", "11:00:00", "11:10:00"),
    ])
    monkeypatch.chdir(tmp_path)
    estad = estadillo_mod.empaquetar_rutas([
        os.path.join("sel", "piloto_a.csv"), os.path.join("sel", "piloto_b.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    _crear_imagen(sel / "DCIM", "DJI_0001_D.JPG")

    pipeline = _PipelineDePrueba({})
    exif = _ExifDePrueba()
    manifiesto = _manifiesto(tmp_path)

    with pytest.raises(ErrorEstadillosMismaCarpeta) as excinfo:
        construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    mensaje = str(excinfo.value)
    assert "piloto_a.csv" in mensaje
    assert "piloto_b.csv" in mensaje
    assert manifiesto.todas() == []
    manifiesto.cerrar()


def test_estadillo_relativo_sin_fotos_avisa_y_no_reclama_huerfanas(tmp_path, monkeypatch):
    """Mismo caso que `test_estadillo_autodetectado_sin_fotos_no_reclama_imagenes_ajenas`
    pero con `--estadillo` en rutas RELATIVAS: el WARNING de "sin fotos" debe
    seguir saltando (y sin reclamar imágenes de la otra carpeta) aunque
    `cfg.estad` no llegue en abspath."""
    sel = tmp_path / "sel"
    vacio = sel / "ESTADILLO_SIN_FOTOS"
    con_fotos = sel / "PILOTO_A"
    _escribir_estadillo(vacio / "suelto.csv", [
        ("9", "9", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(con_fotos / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    monkeypatch.chdir(tmp_path)
    estad = estadillo_mod.empaquetar_rutas([
        os.path.join("sel", "ESTADILLO_SIN_FOTOS", "suelto.csv"),
        os.path.join("sel", "PILOTO_A", "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    ruta = _crear_imagen(con_fotos / "DCIM", "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)})
    manifiesto = _manifiesto(tmp_path)
    grabador = _SignalGrabador()

    construir_indice(cfg, pipeline, exif, manifiesto, grabador, _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["pb"] == "1"
    assert fila["vuelo"] == "1"
    assert any("no tiene imágenes en su carpeta" in m for m in grabador.mensajes)
    manifiesto.cerrar()


def test_estadillo_normcase_windows_simulado_bloquea(tmp_path, monkeypatch):
    """Simula Windows (case-insensitive) con `os.path.normcase` parcheado:
    un `--estadillo` con capitalización distinta a la de disco debe seguir
    calificando como "el mismo fichero" que devuelve `detectar_estadillos`
    a efectos del bloqueo de misma carpeta.

    Linux es case-sensitive de verdad, así que se escribe también el
    fichero en mayúsculas para que `combinar_estadillos` pueda leerlo del
    disco -lo que se simula con `normcase` es solo la COMPARACIÓN de rutas,
    no el filesystem-."""
    sel = tmp_path / "sel"
    _escribir_estadillo(sel / "piloto_a.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(sel / "PILOTO_A.CSV", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    _escribir_estadillo(sel / "piloto_b.csv", [
        ("2", "1", "2024:06:01", "11:00:00", "11:10:00"),
    ])
    estad = estadillo_mod.empaquetar_rutas([
        str(sel / "PILOTO_A.CSV"), str(sel / "piloto_b.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    _crear_imagen(sel / "DCIM", "DJI_0001_D.JPG")

    monkeypatch.setattr(estadillo_mod.os.path, "normcase", lambda p: str(p).lower())

    pipeline = _PipelineDePrueba({})
    exif = _ExifDePrueba()
    manifiesto = _manifiesto(tmp_path)

    with pytest.raises(ErrorEstadillosMismaCarpeta):
        construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    manifiesto.cerrar()
