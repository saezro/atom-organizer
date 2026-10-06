"""El filtro "a medio copiar" va fusionado en la apertura de `_leer_metadatos`:
una sola apertura por fichero, con la misma exclusión que `_a_medio_copiar`."""
import builtins
import os

import pytest

import exif as exif_mod
from atom_core import indice
from atom_core.indice import _BYTES_CABECERA, _leer_metadatos
from tests.test_indice_una_lectura import _Callback, _crear_jpeg


@pytest.fixture
def exif_obj(organizer_logger_stub):
    return exif_mod.GeneralInformationFromImage(organizer_logger_stub)


@pytest.fixture(autouse=True)
def _filtro_activo(monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)


def _contar_aperturas(monkeypatch, ruta):
    aperturas = []
    open_real = builtins.open

    def open_contador(archivo, *a, **k):
        if isinstance(archivo, (str, os.PathLike)) and os.fspath(archivo) == ruta:
            aperturas.append(archivo)
        return open_real(archivo, *a, **k)

    monkeypatch.setattr(builtins, "open", open_contador)
    return aperturas


def test_a_una_apertura_con_filtro_activo(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_jpeg(str(tmp_path / "ok.jpg"))
    aperturas = _contar_aperturas(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert len(aperturas) == 1
    assert r.a_medias is False and r.modelo == "M3T"


def test_jpg_grande_una_apertura_y_cola_por_seek(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_jpeg(str(tmp_path / "grande.jpg"))
    with open(ruta, "ab") as fh:
        fh.write(b"\x01" * (_BYTES_CABECERA + 1000))
    aperturas = _contar_aperturas(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert len(aperturas) == 1 and r.a_medias is False


def test_jpg_grande_cola_de_ceros_excluido(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_jpeg(str(tmp_path / "ceros.jpg"))
    with open(ruta, "ab") as fh:
        fh.write(b"\x01" * (_BYTES_CABECERA + 1000) + bytes(16))
    aperturas = _contar_aperturas(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert r.a_medias is True and len(aperturas) == 1
    assert indice._a_medio_copiar(ruta) is True  # misma decisión que el filtro clásico


def test_jpg_pequeno_cola_de_ceros_excluido(tmp_path, exif_obj):
    ruta = str(tmp_path / "t.jpeg")
    with open(ruta, "wb") as fh:
        fh.write(b"\xff\xd8abc" + bytes(16))  # < 256 KB: cola del buffer ya leído
    assert _leer_metadatos(ruta, exif_obj, _Callback()).a_medias is True
    assert indice._a_medio_copiar(ruta) is True


def test_pequeno_valido_y_tiff_no_se_excluyen(tmp_path, exif_obj):
    rjpeg = str(tmp_path / "r.JPG")
    with open(rjpeg, "wb") as fh:
        fh.write(b"\xff\xd8abc\xff\xd9" + bytes(range(1, 40)))
    tiff = str(tmp_path / "x.tif")
    with open(tiff, "wb") as fh:
        fh.write(b"abc" + bytes(16))
    assert _leer_metadatos(rjpeg, exif_obj, _Callback()).a_medias is False
    assert _leer_metadatos(tiff, exif_obj, _Callback()).a_medias is False


def test_tamano_cero_excluido(tmp_path, exif_obj):
    ruta = str(tmp_path / "v.JPG")
    open(ruta, "wb").close()
    assert _leer_metadatos(ruta, exif_obj, _Callback()).a_medias is True


def test_bloqueado_por_escritor_windows_excluido_sin_abrir_normal(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_jpeg(str(tmp_path / "w.jpg"))
    monkeypatch.setattr(indice, "_es_windows", lambda: True)
    monkeypatch.setattr(indice, "_abrir_compartido_windows", lambda r: (None, 32))
    aperturas = _contar_aperturas(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert r.a_medias is True and aperturas == []


def test_windows_otro_error_no_excluye_y_abre_normal(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_jpeg(str(tmp_path / "w.jpg"))
    monkeypatch.setattr(indice, "_es_windows", lambda: True)
    monkeypatch.setattr(indice, "_abrir_compartido_windows", lambda r: (None, 5))
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert r.a_medias is False and r.modelo == "M3T"


def test_windows_apertura_compartida_ok_es_la_unica_apertura(tmp_path, exif_obj, monkeypatch):
    ruta = _crear_jpeg(str(tmp_path / "w.jpg"))
    monkeypatch.setattr(indice, "_es_windows", lambda: True)
    open_real = builtins.open
    monkeypatch.setattr(indice, "_abrir_compartido_windows",
                        lambda r: (open_real(r, "rb"), 0))
    aperturas = _contar_aperturas(monkeypatch, ruta)
    r = _leer_metadatos(ruta, exif_obj, _Callback())
    assert aperturas == [] and r.a_medias is False and r.modelo == "M3T"


def test_filtro_desactivado_no_excluye(tmp_path, exif_obj, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", False)
    ruta = str(tmp_path / "v.JPG")
    open(ruta, "wb").close()
    assert _leer_metadatos(ruta, exif_obj, _Callback()).a_medias is False


def test_ejecutar_con_limite_usa_todos_los_hilos_configurados():
    """El pool del Índice no impone aforo propio: con N hilos hay N llamadas
    simultáneas (barrera de N partes que solo se abre si coinciden)."""
    import threading
    n = 12
    barrera = threading.Barrier(n, timeout=10)
    pico = {"max": 0, "act": 0}
    cerrojo = threading.Lock()

    def f(x):
        with cerrojo:
            pico["act"] += 1
            pico["max"] = max(pico["max"], pico["act"])
        barrera.wait()
        with cerrojo:
            pico["act"] -= 1
        return x

    res = indice._ejecutar_con_limite(list(range(n * 2)), f, n, 30.0, None, 30.0)
    assert res == list(range(n * 2)) and pico["max"] == n


# --- Integración: construir_indice y _listar_imagenes -------------------------

class _Grabador:
    def __init__(self):
        self.mensajes: list[str] = []

    def emit(self, *args, **kwargs):
        if args:
            self.mensajes.append(str(args[0]))


class _EmisorNulo:
    def iniciar(self, *a, **k):
        pass

    def progreso(self, *a, **k):
        pass


def _indice(cfg, tmp_path, ruta_ts, log, **kwargs):
    from datetime import datetime
    from tests.test_estadillo_scoping_carpeta import (
        _ExifDePrueba, _manifiesto, _PipelineDePrueba, _Signal)
    from atom_core.indice import construir_indice
    ventanas = {("2024:06:01", "10:00:00", "10:10:00"): (
        datetime(2024, 6, 1, 10, 0, 0), datetime(2024, 6, 1, 10, 10, 0))}
    manifiesto = _manifiesto(tmp_path)
    resumen = construir_indice(
        cfg, _PipelineDePrueba(ventanas),
        _ExifDePrueba(timestamps={ruta_ts: datetime(2024, 6, 1, 10, 5, 0)}),
        manifiesto, log, _Signal(), _Signal(), **kwargs)
    manifiesto.cerrar()
    return resumen


def test_estadillo_autodetectado_con_solo_imagenes_a_medias_avisa_sin_imagenes(tmp_path):
    from tests.test_estadillo_scoping_carpeta import (
        _cfg, _crear_imagen, _escribir_estadillo)
    from atom_core import estadillo as estadillo_mod
    sel = tmp_path / "sel"
    solo_medias = sel / "A"
    con_fotos = sel / "B"
    _escribir_estadillo(solo_medias / "estadillo.csv", [
        ("9", "9", "2024:06:01", "10:00:00", "10:10:00")])
    _escribir_estadillo(con_fotos / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    estad = estadillo_mod.empaquetar_rutas([
        str(solo_medias / "estadillo.csv"), str(con_fotos / "estadillo.csv")])
    cfg = _cfg(tmp_path, sel, estad)
    _crear_imagen(str(solo_medias / "DCIM"), "DJI_0001_D.JPG")  # tamaño 0: a medio copiar
    buena = _crear_imagen(str(con_fotos / "DCIM"), "DJI_0002_D.JPG",
                          b"\xff\xd8abc\xff\xd9" + bytes(range(1, 40)))
    log = _Grabador()
    _indice(cfg, tmp_path, buena, log)
    assert any("no tiene imágenes en su carpeta" in m and "A" in m for m in log.mensajes)
    assert sum("no tiene imágenes en su carpeta" in m for m in log.mensajes) == 1
    assert any("1 imágenes omitidas" in m for m in log.mensajes)


def test_estadillo_manual_con_solo_imagenes_a_medias_va_a_sin_carpeta(tmp_path):
    """Estadillo manual (no autodetectado) sin imágenes legibles bajo su
    carpeta: cae en `ventanas_sin_carpeta`, sin WARNING."""
    from tests.test_estadillo_scoping_carpeta import (
        _cfg, _crear_imagen, _escribir_estadillo)
    origen = tmp_path / "origen"
    ext = tmp_path / "ext"
    _escribir_estadillo(ext / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    cfg = _cfg(tmp_path, origen, str(ext / "estadillo.csv"))
    medias = _crear_imagen(str(origen), "DJI_0001_D.JPG")
    log = _Grabador()
    _indice(cfg, tmp_path, medias, log)
    assert not any("no tiene imágenes en su carpeta" in m for m in log.mensajes)


def test_reintento_de_lentas_a_medias_cuenta_una_vez_en_omitidas(tmp_path, monkeypatch):
    import threading
    from tests.test_estadillo_scoping_carpeta import (
        _cfg, _crear_imagen, _escribir_estadillo)
    _escribir_estadillo(tmp_path / "estadillo.csv", [
        ("1", "1", "2024:06:01", "10:00:00", "10:10:00")])
    cfg = _cfg(tmp_path, tmp_path / "origen", str(tmp_path / "estadillo.csv"))
    ruta = _crear_imagen(str(tmp_path / "origen"), "DJI_0001_D.JPG")  # tamaño 0
    original = indice._leer_metadatos
    llamadas = {"n": 0}

    def falsa(r, exif_, cb):
        if r == ruta:
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                threading.Event().wait(1.0)  # vence en la 1ª pasada
        return original(r, exif_, cb)

    monkeypatch.setattr(indice, "_leer_metadatos", falsa)
    log = _Grabador()
    resumen = _indice(cfg, tmp_path, ruta, log, max_hilos=2, timeout_s=0.2,
                      timeout_reintento_s=5, watchdog_s=5, intervalo_progreso_s=0.05)
    assert llamadas["n"] == 2
    assert resumen["no_disponibles"] == []
    avisos = [m for m in log.mensajes if "imágenes omitidas" in m]
    assert len(avisos) == 1 and "1 imágenes omitidas" in avisos[0]


def test_listar_imagenes_con_y_sin_limite(tmp_path):
    (tmp_path / "ok.jpg").write_bytes(b"\xff\xd8abc\xff\xd9" + bytes(range(1, 40)))
    (tmp_path / "vacia.jpg").write_bytes(b"")
    (tmp_path / "nota.txt").write_bytes(b"x")
    omitidas: list[str] = []
    sin = indice._listar_imagenes(str(tmp_path), omitidas)
    assert [os.path.basename(r) for r in sin] == ["ok.jpg"]
    assert [os.path.basename(r) for r in omitidas] == ["vacia.jpg"]
    omitidas_l: list[str] = []
    con = indice._listar_imagenes(str(tmp_path), omitidas_l, indice._Limite(_EmisorNulo()))
    assert [os.path.basename(r) for r in con] == ["ok.jpg", "vacia.jpg"]  # no filtra
    assert omitidas_l == []
