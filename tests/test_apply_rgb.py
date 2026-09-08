"""Tests del apply de imágenes RGB (`atom_core.apply.aplicar_rgb`).

Hoy una RGB pasa por ~3 ciclos decode+encode (compresión, recorte, rotación)
más una copia y un move. El apply nuevo abre cada imagen UNA sola vez y
escribe directo a su carpeta final. Lo que estos tests sujetan:

1. Que de verdad sea un solo decode (si no, el ahorro de la Tarea 4 no
   existe y el organizado sigue tan lento como antes).
2. Que el EXIF (fecha, GPS) sobreviva al viaje — sin eso se entregan
   imágenes sin metadatos, inservibles para georreferenciar.
3. Que la calidad de guardado replique EXACTAMENTE al motor viejo (decisión
   de Rodrigo 2026-09-08, Correcciones §4): no se mejora por sorpresa.
4. Que un fallo en una fila (origen borrado, disco lleno) no tumbe el run
   entero ni deje basura a medias en la carpeta de entrega.
5. Que sea reanudable: un segundo `aplicar_rgb` sobre el mismo manifiesto
   no reescribe lo que ya está `hecho`.
6. Que el origen — la imagen que trajo el dron — quede intacto siempre.
"""
import datetime as dt
import hashlib
import threading
import types

import piexif
import pytest
from PIL import Image as PILImage

import pipeline as pipeline_real
from atom_core import apply
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from utils import SplitImagesConfig


class _SignalFalsa:
    """Doble de las señales `progress_callback`/`progress_bar`/
    `progress_summarize`: solo apunta lo que se le emite, sin depender de
    Qt ni de ningún runtime de GUI."""

    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *args, **kwargs):
        self.mensajes.append(valor)


class _ImagenConContador:
    """Envoltorio de `PIL.Image` que cuenta cuántas veces se abre un
    fichero. Sin este doble no hay forma de demostrar que el apply hace UN
    solo decode: PIL abre de forma perezosa y un segundo `Image.open`
    silencioso pasaría inadvertido mirando solo el resultado final."""

    def __init__(self):
        self.aperturas = 0

    def open(self, *args, **kwargs):
        self.aperturas += 1
        return pipeline_real.Image.open(*args, **kwargs)

    def __getattr__(self, nombre):
        # Constantes como ROTATE_90/ROTATE_270 se piden sobre este objeto
        # (`pipeline_mod.Image.ROTATE_270`): se delegan al PIL real.
        return getattr(pipeline_real.Image, nombre)


def _pipeline_doble(imagen=None):
    """Doble del módulo `pipeline`: delega en el real todo lo que el apply
    reutiliza por contrato (`_procesar_y_guardar_imagen`,
    `ImageProcessConfig`, `_ROTATION_JPEG_QUALITY`) y solo permite sustituir
    `.Image` para contar aperturas o forzar fallos."""
    return types.SimpleNamespace(
        Image=imagen if imagen is not None else pipeline_real.Image,
        ImageProcessConfig=pipeline_real.ImageProcessConfig,
        _procesar_y_guardar_imagen=pipeline_real._procesar_y_guardar_imagen,
        _ROTATION_JPEG_QUALITY=pipeline_real._ROTATION_JPEG_QUALITY,
    )


class _ControladorFalso:
    """Doble mínimo de `ControladorAdaptativo`: un número de trabajadores fijo
    (sin historial ni ventanas), suficiente para forzar un aforo pequeño y
    determinista en el camino paralelo de `aplicar_rgb`."""

    def __init__(self, trabajadores):
        self.trabajadores = trabajadores
        self.maximo = trabajadores

    def registrar(self, mb):
        pass

    def revisar(self):
        pass


class _ManifiestoQueRevientaAlMarcarHecha(Manifiesto):
    """Doble sobre un `Manifiesto` real: `marcar_hecha` revienta SIEMPRE
    (simula sqlite ocupada tras agotar el `busy_timeout`), todo lo demás se
    delega intacto a la clase real. No hace falta que sea picklable: en el
    camino paralelo el manifiesto solo vive en el hilo padre — nunca se envía
    al `ProcessPoolExecutor`, eso es cosa de `_trabajo_fila`."""

    def marcar_hecha(self, id_fila, verificacion):
        raise RuntimeError("sqlite ocupada (simulado)")


def _cfg(**overrides):
    """`SplitImagesConfig` mínima; solo `compress_level` importa a estos
    tests, el resto son valores neutros para poder construir el dataclass."""
    base = dict(
        input_folder="/origen", output_folder="/destino",
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad="/estadillo.csv", choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=85, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=True, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=True, gen_thumbnails=True, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5.0,
        gen_thumbnails_max_error=80, gen_thumbnails_subs_to_angle=5.0,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=True, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    base.update(overrides)
    return SplitImagesConfig(**base)


def _fila_dict(origen, salida_original, salida_crop=None, angulo_giro=0, pct_recorte=None):
    """Fila mínima para llamar a `_escribir_salidas_de_fila` directamente,
    sin pasar por el manifiesto."""
    return {
        "ruta_origen": origen,
        "angulo_giro": angulo_giro,
        "pct_recorte": pct_recorte,
        "ruta_salida_original": salida_original,
        "ruta_salida_crop": salida_crop,
    }


def _fila_manifiesto(origen, salida_original, salida_crop=None, angulo_giro=0,
                     pct_recorte=None):
    """Fila completa para insertar en un `Manifiesto` de verdad (tests que
    cruzan `aplicar_rgb`, con estado y reanudación de por medio)."""
    return FilaManifiesto(
        ruta_origen=str(origen),
        tipo="RGB",
        timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T",
        pb="PB1",
        vuelo="V01",
        nombre_nuevo="20260501_100000_" + str(origen).rsplit("/", 1)[-1],
        angulo_giro=angulo_giro,
        pct_recorte=pct_recorte,
        comprime=True,
        ruta_salida_original=str(salida_original),
        ruta_salida_crop=str(salida_crop) if salida_crop else None,
        ruta_salida_tiff=None,
        unassigned=False,
    )


def test_escribe_original_y_crop_desde_un_solo_decode(tmp_path, make_dji_jpeg):
    """El motor viejo decodifica la misma imagen varias veces (compresión,
    recorte, rotación por separado). Si `_escribir_salidas_de_fila` volviera
    a abrir el fichero para el `_CROP`, el ahorro que promete la Tarea 4 no
    existiría y el apply seguiría tan lento como el motor de 7 fases."""
    origen = tmp_path / "origen" / "DJI_0001.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen))

    contador = _ImagenConContador()
    doble = _pipeline_doble(contador)

    salida_original = tmp_path / "salida" / "20260501_100000_DJI_0001.JPG"
    salida_crop = tmp_path / "salida" / "20260501_100000_DJI_0001_CROP.JPG"
    fila = _fila_dict(str(origen), str(salida_original), str(salida_crop),
                      angulo_giro=90, pct_recorte=0.8)

    apply._escribir_salidas_de_fila(fila, _cfg(), doble)

    assert contador.aperturas == 1
    assert salida_original.exists()
    assert salida_crop.exists()


def test_conserva_el_exif_en_la_salida(tmp_path, make_dji_jpeg):
    """Sin `DateTimeOriginal` y GPS en la salida, la imagen entregada no se
    puede georreferenciar: el pipeline de análisis posterior (INDAI,
    georreferenciado de defectos) depende de ese EXIF."""
    dt_val = dt.datetime(2026, 5, 1, 10, 30, 0)
    origen = tmp_path / "origen" / "DJI_0002.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen), lat=40.0, lon=-3.0, dt_val=dt_val)

    salida = tmp_path / "salida" / "DJI_0002.JPG"
    fila = _fila_dict(str(origen), str(salida), angulo_giro=0)

    apply._escribir_salidas_de_fila(fila, _cfg(), pipeline_real)

    exif_salida = piexif.load(str(salida))
    fecha = exif_salida["Exif"][piexif.ExifIFD.DateTimeOriginal].decode()
    assert fecha == "2026:05:01 10:30:00"
    assert piexif.GPSIFD.GPSLatitude in exif_salida["GPS"]


def test_la_rotacion_usa_la_calidad_del_motor_viejo(tmp_path, make_dji_jpeg, monkeypatch):
    """Decisión de Rodrigo (2026-09-08, Correcciones §4): replicar el
    criterio del motor viejo, no mejorarlo por sorpresa. Una RGB girada se
    guarda con `_ROTATION_JPEG_QUALITY` (40) SIEMPRE, pisando la calidad de
    la interfaz; una RGB recta respeta `cfg.compress_level`."""
    origen = tmp_path / "origen" / "DJI_0003.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen))

    calidades = []
    guardado_real = PILImage.Image.save

    def _save_que_apunta(self, fp, *args, **kwargs):
        calidades.append(kwargs.get("quality"))
        return guardado_real(self, fp, *args, **kwargs)

    monkeypatch.setattr(PILImage.Image, "save", _save_que_apunta)

    cfg = _cfg(compress_level=77)

    fila_girada = _fila_dict(str(origen), str(tmp_path / "girada.jpg"), angulo_giro=90)
    apply._escribir_salidas_de_fila(fila_girada, cfg, pipeline_real)
    assert calidades == [40]

    calidades.clear()
    fila_recta = _fila_dict(str(origen), str(tmp_path / "recta.jpg"), angulo_giro=0)
    apply._escribir_salidas_de_fila(fila_recta, cfg, pipeline_real)
    assert calidades == [77]


def test_un_fallo_marca_la_fila_y_no_para_el_run(tmp_path, make_dji_jpeg):
    """Si el origen de una imagen desapareció entre el índice y el apply
    (borrado a mano, un disco de red que parpadeó), esa fila no puede
    tumbar el resto del vuelo: se marca fallida con motivo y el apply
    sigue con las demás."""
    origen_ok = tmp_path / "origen" / "DJI_0001.JPG"
    origen_ok.parent.mkdir()
    make_dji_jpeg(str(origen_ok))
    origen_roto = tmp_path / "origen" / "NO_EXISTE.JPG"

    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        _fila_manifiesto(origen_ok, tmp_path / "salida" / "DJI_0001.JPG"),
        _fila_manifiesto(origen_roto, tmp_path / "salida" / "NO_EXISTE.JPG"),
    ])

    resultado = apply.aplicar_rgb(
        manifiesto, _cfg(), pipeline_real,
        _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert resultado == {"hecho": 1, "fallido": 1}
    filas = manifiesto.todas()
    fallida = next(f for f in filas if f["estado"] == "fallido")
    hecha = next(f for f in filas if f["estado"] == "hecho")
    assert fallida["motivo_fallo"]
    assert hecha["motivo_fallo"] is None
    manifiesto.cerrar()


def test_no_deja_ficheros_parciales(tmp_path, make_dji_jpeg, monkeypatch):
    """Un fallo a mitad de un `save()` (disco lleno, JPEG corrupto al
    escribir) no puede dejar un `.parcial` ni un fichero final a medias en
    la carpeta de entrega: el siguiente run los confundiría con una salida
    válida y nunca los regeneraría."""
    origen = tmp_path / "origen" / "DJI_0001.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen))

    carpeta_salida = tmp_path / "salida"
    destino = carpeta_salida / "DJI_0001.JPG"
    fila = _fila_dict(str(origen), str(destino), angulo_giro=0)

    def _save_que_revienta(self, fp, *args, **kwargs):
        raise OSError("disco lleno (simulado)")

    monkeypatch.setattr(PILImage.Image, "save", _save_que_revienta)

    with pytest.raises(OSError):
        apply._escribir_salidas_de_fila(fila, _cfg(), pipeline_real)

    assert not destino.exists()
    assert list(carpeta_salida.glob("*.parcial*")) == []


def test_reanudacion_salta_las_filas_hechas(tmp_path, make_dji_jpeg):
    """Si un segundo `aplicar_rgb` reescribiera una fila ya `hecho`, un run
    interrumpido y relanzado repetiría trabajo cada vez más caro. Se
    comprueba por `mtime`: si se tocara la salida, el `mtime` cambiaría."""
    origen = tmp_path / "origen" / "DJI_0001.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen))
    destino = tmp_path / "salida" / "DJI_0001.JPG"

    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([_fila_manifiesto(origen, destino)])

    primer_resultado = apply.aplicar_rgb(
        manifiesto, _cfg(), pipeline_real,
        _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )
    assert primer_resultado == {"hecho": 1, "fallido": 0}
    assert destino.exists()
    mtime_primero = destino.stat().st_mtime_ns

    segundo_resultado = apply.aplicar_rgb(
        manifiesto, _cfg(), pipeline_real,
        _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
    )

    assert segundo_resultado == {"hecho": 0, "fallido": 0}
    assert destino.stat().st_mtime_ns == mtime_primero
    manifiesto.cerrar()


def test_el_origen_queda_intacto(tmp_path, make_dji_jpeg):
    """El apply escribe siempre en carpetas de destino nuevas: el original
    que trajo el dron no se toca. Un hash distinto tras el apply sería una
    imagen de campo alterada sin que nadie lo pidiera — y sin origen íntegro
    no hay forma de reprocesar si algo sale mal más adelante."""
    origen = tmp_path / "origen" / "DJI_0001.JPG"
    origen.parent.mkdir()
    make_dji_jpeg(str(origen))
    hash_antes = hashlib.sha256(origen.read_bytes()).hexdigest()

    fila = _fila_dict(
        str(origen),
        str(tmp_path / "salida" / "DJI_0001.JPG"),
        str(tmp_path / "salida" / "DJI_0001_CROP.JPG"),
        angulo_giro=90, pct_recorte=0.5,
    )
    apply._escribir_salidas_de_fila(fila, _cfg(), pipeline_real)

    hash_despues = hashlib.sha256(origen.read_bytes()).hexdigest()
    assert hash_antes == hash_despues


def test_camino_paralelo_no_se_cuelga_si_marcar_hecha_revienta_siempre(
    tmp_path, make_dji_jpeg
):
    """Regresión: antes del fix, `aforo.liberar()` en `_al_terminar` vivía
    FUERA del `finally` que envuelve a `_cerrar_fila`. Si `marcar_hecha`
    reventaba (sqlite ocupada, busy_timeout agotado) el permiso del aforo
    nunca volvía al semáforo, y con trabajadores pequeño (2) la 3ª fila se
    bloqueaba para siempre en `aforo.adquirir()` — el run quedaba colgado sin
    ningún traceback visible (`ProcessPoolExecutor` se traga las excepciones
    de un callback, solo las loguea).

    Con el fix, `aforo.liberar()` va en el `finally` de `_al_terminar`: el
    permiso vuelve al semáforo pase lo que pase en `_cerrar_fila`, y
    `aplicar_rgb` termina igual aunque TODAS las filas revienten al
    marcarse (el fallo de `marcar_hecha` aborta el contaje de esa fila —
    no llega a incrementar ni `hecho` ni `fallido`, porque ocurre antes de
    esa línea en `_cerrar_fila` — pero nunca cuelga el run).
    """
    manifiesto = _ManifiestoQueRevientaAlMarcarHecha(tmp_path / "m.db")
    manifiesto.crear_esquema()

    filas = []
    for indice in range(6):
        origen = tmp_path / "origen" / f"DJI_{indice:04d}.JPG"
        origen.parent.mkdir(exist_ok=True)
        make_dji_jpeg(str(origen))
        filas.append(
            _fila_manifiesto(origen, tmp_path / "salida" / f"DJI_{indice:04d}.JPG")
        )
    manifiesto.insertar_muchas(filas)

    controlador = _ControladorFalso(trabajadores=2)
    resultado = {}

    def _lanzar():
        resultado["valor"] = apply.aplicar_rgb(
            manifiesto, _cfg(), pipeline_real,
            _SignalFalsa(), _SignalFalsa(), _SignalFalsa(),
            controlador=controlador,
        )

    hilo = threading.Thread(target=_lanzar, daemon=True)
    hilo.start()
    hilo.join(timeout=60)

    assert not hilo.is_alive(), "aplicar_rgb se colgó: el aforo perdió permisos"
    # El apply de cada fila SÍ tuvo éxito (solo revienta el `marcar_hecha` de
    # bookkeeping, antes de incrementar ningún contador): lo que sujeta este
    # test es que el hilo termina solo, no los contadores.
    assert resultado["valor"] == {"hecho": 0, "fallido": 0}
    manifiesto.cerrar()
