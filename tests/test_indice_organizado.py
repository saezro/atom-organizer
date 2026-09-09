"""Índice del organizado: decide sin tocar píxeles.

El índice es la única pasada de metadatos sobre el origen: cruza EXIF con el
estadillo y decide vuelo, nombre, ángulo de giro y % de recorte de CADA
imagen ANTES de que el apply escriba nada. Estos tests sujetan justo eso —que
decide bien y que NO escribe— porque un fallo aquí (una imagen mal asignada,
un ángulo distinto entre el TIFF y su JPG, una colisión de estadillo que
cuela filas fantasma) se propaga en silencio a todo lo que viene después.

Dobles a mano (`_PipelineDePrueba`, `_ExifDePrueba`, `_Signal`), nunca
`unittest.mock`: el estilo de la casa, ver `tests/test_etapas_pipeline.py`.
"""
import datetime as dt
import json
import os

import pytest

from atom_core.indice import STATS_INDICE_PREFIX, ErrorColisionEstadillo, construir_indice
from atom_core.manifiesto import Manifiesto
from utils import SplitImagesConfig


class _Signal:
    """Sustituto Qt-free de un Signal: el índice solo llama `.emit(x)`."""

    def __init__(self):
        self.mensajes = []

    def emit(self, valor):
        self.mensajes.append(valor)


class _PipelineDePrueba:
    """Doble del objeto que agrupa las funciones ya existentes del motor
    viejo que el índice reutiliza (`ventana_horaria_vuelo`, `nombre_destino`,
    `get_percentage_by_model`, `read_auto_rotate_degree`). El índice no
    reimplementa NINGÚN criterio: si esas funciones cambiaran de firma, este
    doble tendría que cambiar con ellas, que es justo la señal que queremos.
    """

    def __init__(self, ventanas: dict, pct_por_modelo: dict | None = None,
                 degree_desde_csv: int | None = None):
        # ventanas: {(fecha, horaInicio, horaFinal): (inicio, fin)}
        self._ventanas = ventanas
        # Expuesto tal cual (no con prefijo `_`): `indice._pct_recorte` lo lee
        # como `pipeline.percentage_by_models`, igual que en producción lo
        # tendrá que exponer el objeto que agrupe estas funciones reutilizadas.
        self.percentage_by_models = pct_por_modelo or {}
        # Si no es None, simula que YA existe el CSV de criterio de giro y
        # que `read_auto_rotate_degree` devuelve este valor directamente.
        self._degree_desde_csv = degree_desde_csv
        self.nombres_pedidos = []

    def ventana_horaria_vuelo(self, fecha, horaInicio, horaFinal,
                              margen_segundos, desfase_horas, desfase_minutos):
        return self._ventanas[(fecha, horaInicio, horaFinal)]

    def nombre_destino(self, image, input_folder, rename, mismatch_hours,
                       mismatch_minutes, ruta_local=None, timestamp=None):
        # `timestamp` lo pasa `indice._construir_fila` para que el real no
        # tenga que releer el EXIF: el doble solo tiene que aceptarlo.
        self.nombres_pedidos.append(image)
        if not rename:
            return ""
        return f"RENOMBRADA_{image}"

    def get_percentage_by_model(self, model, percentage_cropping_dict):
        return percentage_cropping_dict[model.strip().upper()]

    def read_auto_rotate_degree(self, input_folder, progress_callback):
        # El motor viejo nunca revienta aquí (ver pipeline.py:3308): sin CSV
        # legible devuelve 0. El doble replica ese "nunca revienta", con el
        # valor que cada test necesite.
        return self._degree_desde_csv if self._degree_desde_csv is not None else 0


class _ExifDePrueba:
    """Doble de `Exif`: cada método devuelve lo que el test le haya
    precargado para esa ruta, o simula un fallo si la ruta está en
    `fallar_en`. Nunca toca disco de verdad."""

    def __init__(self, timestamps=None, modelos=None, yaws=None, gps=None,
                fallar_en=None):
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
    """Escribe un estadillo mínimo (columnas ES) con las filas dadas.

    `filas` es una lista de tuplas (pb, vuelo, fecha, hora_inicio, hora_final).
    """
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n")
        for pb, vuelo, fecha, hora_inicio, hora_final in filas:
            fh.write(f"{pb};{vuelo};{fecha};{hora_inicio};{hora_final}\n")


def _cfg(tmp_path, **overrides):
    base = dict(
        input_folder=str(tmp_path / "origen"),
        output_folder=str(tmp_path / "destino"),
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="_D",
        estad=str(tmp_path / "estadillo.csv"),
        choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=False,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        # Por defecto SIN recorte: `get_percentage_by_model` (real, sin doble)
        # no perdona un modelo ausente del diccionario (KeyError, ver
        # pipeline.py:4073), así que el recorte solo se activa explícitamente
        # en `test_pct_recorte_sale_del_modelo`, con su propio diccionario.
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


def _crear_imagen(carpeta, nombre):
    """Un fichero vacío basta: `_leer_metadatos` nunca abre disco de verdad
    en estos tests, todo lo resuelve `_ExifDePrueba`."""
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "wb") as fh:
        fh.write(b"")
    return ruta


def _manifiesto(tmp_path, nombre="manifiesto.db"):
    m = Manifiesto(tmp_path / nombre)
    m.crear_esquema()
    return m


def test_asigna_cada_imagen_a_su_vuelo_por_ventana_horaria(tmp_path):
    """Dos imágenes con timestamps dentro de ventanas distintas deben acabar
    en vuelos distintos en el manifiesto. Si el índice mezclara las ventanas
    (por ejemplo comparando solo contra la última vista) dos vuelos de la
    misma jornada se fusionarían en uno, como pasaba en el motor viejo si el
    estadillo no relistaba la carpeta a tiempo."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
        ("1", "2", "2024:06:01", "11:00:00", "11:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta1 = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    ruta2 = _crear_imagen(cfg.input_folder, "DJI_0002_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
        ("2024:06:01", "11:00:00", "11:10:00"): (
            dt.datetime(2024, 6, 1, 11, 0, 0), dt.datetime(2024, 6, 1, 11, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={
        ruta1: dt.datetime(2024, 6, 1, 10, 5, 0),
        ruta2: dt.datetime(2024, 6, 1, 11, 5, 0),
    })
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta1]["vuelo"] == "1"
    assert filas[ruta2]["vuelo"] == "2"
    assert filas[ruta1]["vuelo"] != filas[ruta2]["vuelo"]
    manifiesto.cerrar()


def test_imagen_fuera_de_toda_ventana_queda_unassigned(tmp_path):
    """Una imagen que no cae en ninguna ventana horaria debe quedar
    `unassigned`, con su salida en SIN_ORDENAR. Previene que se pierdan en
    silencio imágenes que hoy rescata un barrido posterior."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")

    ventanas = {
        ("2024:06:01", "10:00:00", "10:10:00"): (
            dt.datetime(2024, 6, 1, 10, 0, 0), dt.datetime(2024, 6, 1, 10, 10, 0)),
    }
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 12, 0, 0)})
    manifiesto = _manifiesto(tmp_path)

    resumen = construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["unassigned"] == 1
    assert "SIN_ORDENAR" in fila["ruta_salida_original"]
    assert resumen["unassigned"] == 1
    manifiesto.cerrar()


def test_la_comparacion_de_ventana_es_estricta(tmp_path):
    """Una imagen con timestamp EXACTAMENTE igual al inicio de la ventana
    queda fuera, igual que `inicio < fecha_hora_imagen < fin` en
    `obtenerListaImagenesVuelo` (pipeline.py:1498). Una comparación con `<=`
    aquí sería una diferencia silenciosa de una imagen frente al motor
    viejo, invisible hasta que alguien cuenta mal un vuelo en producción."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: inicio})  # justo el borde
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["unassigned"] == 1
    manifiesto.cerrar()


def test_todas_las_filas_de_un_vuelo_comparten_angulo(tmp_path):
    """RGB y térmica del mismo PBx_Vy deben compartir `angulo_giro`. Este es
    el test del bug que motiva el proyecto entero: en el motor viejo el TIFF
    y su JPG podían acabar con criterios de giro distintos porque cada
    carpeta (RGB, TERMICA) calculaba su propio consenso por separado."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta_rgb = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    ruta_termica = _crear_imagen(cfg.input_folder, "DJI_0001_T.JPG")

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    ts = dt.datetime(2024, 6, 1, 10, 5, 0)
    # Los dos yaw caen dentro del margen de rotación de 90º (lim 85-95, ver
    # `_cfg`: add_to_angle=5, subs_to_angle=5), así que el consenso del
    # vuelo debe salir 90 para AMBAS imágenes.
    exif = _ExifDePrueba(
        timestamps={ruta_rgb: ts, ruta_termica: ts},
        yaws={ruta_rgb: 90.0, ruta_termica: 91.0},
    )
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    filas = {fila["ruta_origen"]: fila for fila in manifiesto.filas_por_vuelo("1", "1")}
    assert filas[ruta_rgb]["angulo_giro"] == 90
    assert filas[ruta_termica]["angulo_giro"] == 90
    assert filas[ruta_rgb]["angulo_giro"] == filas[ruta_termica]["angulo_giro"]
    manifiesto.cerrar()


def test_colision_pb_vuelo_aborta_sin_escribir_nada(tmp_path):
    """Con una colisión (mismo PB+Vuelo, fecha distinta tras fusionar
    estadillos) `construir_indice` debe lanzar `ErrorColisionEstadillo` ANTES
    de escribir ninguna fila, no a mitad como hacía el motor viejo."""
    ruta_a = tmp_path / "estadillo_a.csv"
    ruta_b = tmp_path / "estadillo_b.csv"
    _escribir_estadillo(ruta_a, [("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    _escribir_estadillo(ruta_b, [("1", "01", "2024:06:02", "10:00:00", "10:10:00")])

    from atom_core import estadillo as estadillo_mod
    estad_empaquetado = estadillo_mod.empaquetar_rutas([str(ruta_a), str(ruta_b)])
    cfg = _cfg(tmp_path, estad=estad_empaquetado)
    _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")

    pipeline = _PipelineDePrueba({})
    exif = _ExifDePrueba()
    manifiesto = _manifiesto(tmp_path)

    with pytest.raises(ErrorColisionEstadillo):
        construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    assert manifiesto.todas() == []
    manifiesto.cerrar()


def test_imagen_sin_timestamp_no_revienta_el_indice(tmp_path):
    """Una imagen sin EXIF legible se contabiliza en `sin_timestamp` y queda
    `unassigned`, pero el resto del índice se completa. Que una sola imagen
    corrupta tumbe el índice entero dejaría sin decidir a todas las demás."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta_ok = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    ruta_sin_ts = _crear_imagen(cfg.input_folder, "DJI_0002_D.JPG")

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={
        ruta_ok: dt.datetime(2024, 6, 1, 10, 5, 0),
        ruta_sin_ts: None,
    })
    manifiesto = _manifiesto(tmp_path)

    resumen = construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    assert resumen["total"] == 2
    assert resumen["sin_timestamp"] == 1
    filas = {fila["ruta_origen"]: fila for fila in manifiesto.todas()}
    assert filas[ruta_sin_ts]["unassigned"] == 1
    assert filas[ruta_ok]["unassigned"] == 0
    manifiesto.cerrar()


def test_pct_recorte_sale_del_modelo(tmp_path):
    """Con `cropping_mode_auto=True` la fila lleva el % del diccionario del
    modelo (vía `get_percentage_by_model`); con `False`, el manual de la
    interfaz. Confundir los dos deja recortes con el % de OTRO modelo,
    silenciosamente, porque `get_percentage_by_model` nunca revienta si el
    modelo no está en el diccionario a mano del test.

    `pct_recorte` se guarda como FRACCIÓN 0-1 (80% -> 0.80): es lo que
    `ImageProcessConfig.crop_centered_pct` multiplica por el ancho y el alto.
    Guardarlo como porcentaje crudo pedía recortes de 8000*80 px y Pillow
    abortaba con `DecompressionBombError`."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    ts = dt.datetime(2024, 6, 1, 10, 5, 0)

    # --- modo automático: el % sale del modelo ---
    cfg_auto = _cfg(tmp_path, cropping_rgb=True, cropping_mode_auto=True, crop_percentage="50")
    ruta_auto = _crear_imagen(cfg_auto.input_folder, "DJI_0001_D.JPG")
    pipeline_auto = _PipelineDePrueba(ventanas, pct_por_modelo={"M3T": 80})
    exif_auto = _ExifDePrueba(timestamps={ruta_auto: ts}, modelos={ruta_auto: "M3T"})
    manifiesto_auto = _manifiesto(tmp_path, nombre="auto.db")
    construir_indice(cfg_auto, pipeline_auto, exif_auto, manifiesto_auto, _Signal(), _Signal(), _Signal())
    fila_auto = manifiesto_auto.todas()[0]
    assert fila_auto["pct_recorte"] == 0.80
    manifiesto_auto.cerrar()

    # --- modo manual: el % sale de la interfaz, ignora el diccionario ---
    cfg_manual = _cfg(tmp_path, cropping_rgb=True, cropping_mode_auto=False, crop_percentage="35")
    ruta_manual = _crear_imagen(cfg_manual.input_folder, "DJI_0002_D.JPG")
    pipeline_manual = _PipelineDePrueba(ventanas, pct_por_modelo={"M3T": 80})
    exif_manual = _ExifDePrueba(timestamps={ruta_manual: ts}, modelos={ruta_manual: "M3T"})
    manifiesto_manual = _manifiesto(tmp_path, nombre="manual.db")
    construir_indice(cfg_manual, pipeline_manual, exif_manual, manifiesto_manual, _Signal(), _Signal(), _Signal())
    fila_manual = manifiesto_manual.todas()[0]
    assert fila_manual["pct_recorte"] == 0.35
    manifiesto_manual.cerrar()


def test_emite_stats_marker_con_desglose_rgb_extra_separado(tmp_path):
    """El modal de progreso necesita el desglose del índice por tipo (ver
    LEDGER-metricas-progreso.md): `RGB_Extra` cuenta APARTE de `RGB` aunque
    el apply trate a ambos igual (`indice.TIPOS_RGB`). Si aquí se fundieran,
    el modal no podría mostrar cuántas imágenes van por el tercer grupo de
    sufijos — justo lo que pide el ledger."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path, end_rgb_extra_files="_E")
    ruta_rgb = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    ruta_extra = _crear_imagen(cfg.input_folder, "DJI_0002_E.JPG")
    ruta_termica = _crear_imagen(cfg.input_folder, "DJI_0003_T.JPG")

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    ts = dt.datetime(2024, 6, 1, 10, 5, 0)
    exif = _ExifDePrueba(timestamps={ruta_rgb: ts, ruta_extra: ts, ruta_termica: ts})
    manifiesto = _manifiesto(tmp_path)
    psum = _Signal()

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), psum)

    marcadores = [m for m in psum.mensajes if str(m).startswith(STATS_INDICE_PREFIX)]
    assert len(marcadores) == 1, "el índice debe emitir el desglose UNA sola vez, al cerrar"
    payload = json.loads(marcadores[0][len(STATS_INDICE_PREFIX):])
    assert payload == {
        "fase": "Índice", "total": 3, "rgb": 1, "termica": 1, "rgb_extra": 1,
        "sin_asignar": 0, "sin_timestamp": 0, "vuelos": 1,
    }
    manifiesto.cerrar()


def test_el_indice_no_escribe_ninguna_imagen(tmp_path):
    """Tras `construir_indice` la carpeta de destino no debe contener NINGÚN
    fichero de imagen. El índice DECIDE, no escribe: si esto se rompe se
    pierde la propiedad que hace seguro inspeccionar el plan (el manifiesto)
    antes de tocar nada del origen ni del destino."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    destino = cfg.output_folder
    ficheros_imagen = []
    if os.path.isdir(destino):
        for raiz, _dirs, ficheros in os.walk(destino):
            ficheros_imagen.extend(ficheros)
    assert ficheros_imagen == []
    # La imagen de origen tampoco se ha movido ni tocado.
    assert os.path.isfile(ruta)
    manifiesto.cerrar()


def test_bytes_origen_es_el_tamano_real_del_fichero(tmp_path):
    """`bytes_origen` se rellena al indexar con el tamaño REAL del fichero de
    origen, no un valor inventado: es lo que luego suma `balance_bytes()`
    para comparar la entrega contra lo que entró. Si el índice lo dejara a 0
    (el default de la columna) el balance final mentiría diciendo que no
    entró nada."""
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00"),
    ])
    cfg = _cfg(tmp_path)
    ruta = _crear_imagen(cfg.input_folder, "DJI_0001_D.JPG")
    # `_crear_imagen` escribe un fichero vacío; le metemos contenido real
    # para que el tamaño no coincida por casualidad con el default 0 de la
    # columna.
    with open(ruta, "wb") as fh:
        fh.write(b"contenido de prueba, no vacio" * 100)
    tamano_real = os.path.getsize(ruta)

    inicio = dt.datetime(2024, 6, 1, 10, 0, 0)
    fin = dt.datetime(2024, 6, 1, 10, 10, 0)
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (inicio, fin)}
    pipeline = _PipelineDePrueba(ventanas)
    exif = _ExifDePrueba(timestamps={ruta: dt.datetime(2024, 6, 1, 10, 5, 0)})
    manifiesto = _manifiesto(tmp_path)

    construir_indice(cfg, pipeline, exif, manifiesto, _Signal(), _Signal(), _Signal())

    fila = manifiesto.todas()[0]
    assert fila["bytes_origen"] == tamano_real
    manifiesto.cerrar()
