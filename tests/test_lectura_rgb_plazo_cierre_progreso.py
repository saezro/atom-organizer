"""Arreglos del run 2026-11-25 (origen DriveFS `G:\\`, 5587 RGB): lectura RGB con plazo y
reintento (os error 1450), cierre del pool = `pendiente` (no `fallido`), img/s solo con filas
`hecho` y `_T` leída entera en la primera apertura del índice. `atom_rs` y el origen real se
sustituyen por dobles: el comportamiento de Rust lo cubre `cargo test` (módulo `lectura`)."""
import builtins
import json
import logging
import threading
import types

import pytest

from atom_core import apply, cancelacion, indice, lectura_segura
from atom_core.manifiesto import FilaManifiesto, Manifiesto


class _Senal:
    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *a, **k):
        self.mensajes.append(valor)


class _Controlador:
    def __init__(self, trabajadores):
        self.trabajadores = trabajadores
        self.maximo = trabajadores

    def registrar(self, mb):
        pass

    def revisar(self):
        pass


def _error_1450(ruta="G:/x.JPG"):
    return OSError(22, "Recursos insuficientes para completar el servicio (os error 1450)", ruta)


@pytest.fixture
def sin_esperas(monkeypatch):
    dormidas = []
    monkeypatch.setattr(apply, "_dormir_reintento", dormidas.append)
    return dormidas


# --- 1. Plazo + reintento ---------------------------------------------------


def test_lectura_bloqueada_vence_reintenta_y_acierta_al_segundo(monkeypatch, sin_esperas, caplog):
    monkeypatch.setenv("ORGANIZER_TIMEOUT_LECTURA_RGB_S", "0.2")
    liberar = threading.Event()
    llamadas = []

    def lector(ruta, **kw):
        llamadas.append(ruta)
        if len(llamadas) == 1:
            liberar.wait(10)  # DriveFS colgado: el hilo se abandona
            return b"tarde"
        return b"datos"

    monkeypatch.setattr(lectura_segura, "leer_completo", lector)
    with caplog.at_level(logging.WARNING, logger="atom_core.apply"):
        try:
            assert apply._leer_rgb_con_reintento("G:/a.JPG", 5) == b"datos"
        finally:
            liberar.set()
    assert len(llamadas) == 2 and sin_esperas == [2.0]
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1 and "reintento en 2" in avisos[0].getMessage()
    assert "timeout lectura RGB" in avisos[0].getMessage()


def test_1450_se_reintenta_con_backoff_y_acierta(monkeypatch, sin_esperas):
    estados = [_error_1450(), _error_1450(), b"ok"]

    def lector(ruta, **kw):
        e = estados.pop(0)
        if isinstance(e, Exception):
            raise e
        return e

    monkeypatch.setattr(lectura_segura, "leer_completo", lector)
    assert apply._leer_rgb_con_reintento("G:/a.JPG", 5) == b"ok"
    assert sin_esperas == [2.0, 5.0]


def test_1450_persistente_agota_3_intentos_y_relanza_el_error_real(monkeypatch, sin_esperas, caplog):
    n = []

    def lector(ruta, **kw):
        n.append(1)
        raise _error_1450(ruta)

    monkeypatch.setattr(lectura_segura, "leer_completo", lector)
    with caplog.at_level(logging.WARNING, logger="atom_core.apply"):
        with pytest.raises(OSError, match="os error 1450"):
            apply._leer_rgb_con_reintento("G:/a.JPG", 5)
    assert len(n) == 3 and sin_esperas == [2.0, 5.0]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3


def test_error_no_transitorio_no_se_reintenta(monkeypatch, sin_esperas):
    n = []

    def lector(ruta, **kw):
        n.append(1)
        raise FileNotFoundError(2, "No such file", ruta)

    monkeypatch.setattr(lectura_segura, "leer_completo", lector)
    with pytest.raises(FileNotFoundError):
        apply._leer_rgb_con_reintento("G:/a.JPG", 5)
    assert len(n) == 1 and sin_esperas == []


def test_clasifica_1450_en_sus_formas():
    assert apply._es_error_transitorio_lectura(_error_1450())
    assert apply._es_error_transitorio_lectura(OSError("x (os error 1450)"))
    w = OSError("recursos")
    w.winerror = 1450  # en Windows lo rellena `OSError(None, msg, ruta, 1450)`
    assert apply._es_error_transitorio_lectura(w)
    assert apply._es_error_transitorio_lectura(apply.LecturaRgbTimeout("t"))
    assert not apply._es_error_transitorio_lectura(OSError(22, "otro (os error 87)", "r"))
    assert not apply._es_error_transitorio_lectura(lectura_segura.LecturaIncompleta("r", 1, 2, 3))


# --- Montaje de manifiesto ---------------------------------------------------


def _montar(tmp_path, n):
    manifiesto = Manifiesto(tmp_path / "m.db")
    manifiesto.crear_esquema()
    filas = []
    for i in range(n):
        origen = tmp_path / "origen" / f"DJI_{i:04d}.JPG"
        filas.append(FilaManifiesto(
            ruta_origen=str(origen), tipo="RGB", timestamp_exif="2026-05-01T10:00:00",
            modelo="M3T", pb="PB1", vuelo="V01", nombre_nuevo=f"n_{i}.JPG", angulo_giro=0,
            pct_recorte=None, comprime=True,
            ruta_salida_original=str(tmp_path / "salida" / f"n_{i}.JPG"),
            ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=False))
    manifiesto.insertar_muchas(filas)
    return manifiesto


def _estados(manifiesto):
    return [r[0] for r in manifiesto._conexion().execute(
        "SELECT estado FROM imagenes ORDER BY ruta_origen")]


def _sin_validacion(monkeypatch):
    monkeypatch.setattr(apply, "_vetar_sobrescrituras", lambda m, f, cb: f)
    monkeypatch.setattr(apply, "_validar_jpeg_origen", lambda m, f, cb: f)


def test_1450_persistente_deja_la_fila_fallida_con_el_error(tmp_path, monkeypatch, sin_esperas):
    _sin_validacion(monkeypatch)
    monkeypatch.setattr(lectura_segura, "leer_completo",
                        lambda ruta, **kw: (_ for _ in ()).throw(_error_1450(ruta)))
    monkeypatch.setattr(apply, "_escribir_salidas_de_fila",
                        lambda fila, cfg, pipe: apply._leer_origen_verificado(fila, 0))
    manifiesto = _montar(tmp_path, 1)
    log = _Senal()
    res = apply.aplicar_rgb(manifiesto, None, None, log, _Senal(), _Senal())
    assert res == {"hecho": 0, "fallido": 1}
    assert _estados(manifiesto) == ["fallido"]
    assert any("os error 1450" in str(m) for m in log.mensajes)
    err = manifiesto._conexion().execute("SELECT * FROM imagenes").fetchone()
    assert "1450" in " ".join(str(c) for c in err)
    assert sin_esperas == [2.0, 5.0]


# --- 2. Cierre del pool = pendiente -----------------------------------------


class _PoolCerrado:
    """`ProcessPoolExecutor` ya cerrado por el apagado de la app."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def submit(self, *a, **k):
        raise RuntimeError("cannot schedule new futures after shutdown")


def test_cierre_con_futures_sin_enviar_deja_pendiente(tmp_path, monkeypatch):
    _sin_validacion(monkeypatch)
    monkeypatch.setattr(apply, "ProcessPoolExecutor", _PoolCerrado)
    manifiesto = _montar(tmp_path, 5)
    log = _Senal()
    with pytest.raises(cancelacion.RunCancelado):
        apply.aplicar_rgb(manifiesto, None, None, log, _Senal(), _Senal(),
                          controlador=_Controlador(2))
    assert _estados(manifiesto) == ["pendiente"] * 5
    assert any("pendientes (no fallidas)" in str(m) for m in log.mensajes)


def test_error_de_submit_distinto_al_cierre_sigue_siendo_fallido(tmp_path, monkeypatch):
    _sin_validacion(monkeypatch)

    class _PoolRoto(_PoolCerrado):
        def submit(self, *a, **k):
            raise RuntimeError("otro problema")

    monkeypatch.setattr(apply, "ProcessPoolExecutor", _PoolRoto)
    manifiesto = _montar(tmp_path, 2)
    res = apply.aplicar_rgb(manifiesto, None, None, _Senal(), _Senal(), _Senal(),
                            controlador=_Controlador(1))
    assert res["fallido"] == 2 and _estados(manifiesto) == ["fallido"] * 2


# --- 3. Progreso honesto ----------------------------------------------------


def test_img_por_segundo_solo_cuenta_filas_hecho(tmp_path, monkeypatch):
    _sin_validacion(monkeypatch)
    reloj = {"t": 0.0}
    monkeypatch.setattr(apply.time, "monotonic", lambda: reloj["t"])
    llamada = {"n": 0}

    def escribir(fila, cfg, pipe):
        llamada["n"] += 1
        if llamada["n"] <= 4:  # fallos instantáneos (caso «404 img/s»)
            reloj["t"] += 0.001
            raise OSError(22, "x (os error 1450)", fila["ruta_origen"])
        reloj["t"] += 2.0
        return "ok"

    monkeypatch.setattr(apply, "_escribir_salidas_de_fila", escribir)
    manifiesto = _montar(tmp_path, 12)
    cb = _Senal()
    res = apply.aplicar_rgb(manifiesto, None, None, cb, _Senal(), _Senal())
    assert res == {"hecho": 8, "fallido": 4}
    stats = [json.loads(m[len(apply.STATS_APPLY_PREFIX):]) for m in cb.mensajes
             if str(m).startswith(apply.STATS_APPLY_PREFIX)]
    assert [s["done"] for s in stats] == [10, 12]
    assert stats[-1]["fallidas"] == 4
    assert stats[-1]["img_por_segundo"] == 0.5  # 7 intervalos de 2 s, sin los fallos
    assert stats[0]["fallidas"] == 4 and stats[0]["eta_segundos"] == 4


def test_sin_ningun_hecho_no_hay_velocidad_ni_eta(tmp_path, monkeypatch):
    _sin_validacion(monkeypatch)
    monkeypatch.setattr(apply, "_escribir_salidas_de_fila",
                        lambda f, c, p: (_ for _ in ()).throw(OSError("fallo")))
    manifiesto = _montar(tmp_path, 3)
    cb = _Senal()
    apply.aplicar_rgb(manifiesto, None, None, cb, _Senal(), _Senal())
    stats = [json.loads(m[len(apply.STATS_APPLY_PREFIX):]) for m in cb.mensajes
             if str(m).startswith(apply.STATS_APPLY_PREFIX)]
    assert stats[-1]["img_por_segundo"] is None and stats[-1]["eta_segundos"] is None
    assert stats[-1]["fallidas"] == 3


# --- 4. `_T` en una sola apertura -------------------------------------------


def _cab(necesita_completo):
    return types.SimpleNamespace(
        formato="jpeg", necesita_completo=necesita_completo, xmp_presente=True,
        timestamp_iso="2026:08:20 10:11:12", timestamp_cabecera="2026:08:20 10:11:12",
        modelo="M3T", make="DJI", make_ilegible=False, gps=None, ancho=None, alto=None,
        gimbal_yaw="-45.5", gimbal_pitch="-88.0", flight_yaw_en_xmp=False, xmp=["0"] * 9)


class _Rs:
    def __init__(self):
        self.llamadas = []

    def parsear_cabecera(self, buf, completo=False):
        self.llamadas.append((len(buf), completo))
        return _cab(False) if completo else _cab(True)


def _aperturas(monkeypatch, ruta):
    abiertas = []
    real = builtins.open

    def espia(f, *a, **k):
        if str(f) == str(ruta):
            abiertas.append(f)
        return real(f, *a, **k)

    monkeypatch.setattr(builtins, "open", espia)
    return abiertas


def _fichero(tmp_path, nombre, tam=400_000):
    p = tmp_path / nombre
    p.write_bytes(b"\xff\xd8" + b"\x11" * (tam - 4) + b"\xff\xd9")
    return p


def test_termica_se_lee_entera_en_la_primera_apertura(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)  # conftest lo apaga
    p = _fichero(tmp_path, "DJI_0001_T.JPG")
    rs = _Rs()
    monkeypatch.setattr(indice.rust_bridge, "exigir", lambda fase: rs)
    abiertas = _aperturas(monkeypatch, p)
    dato = indice._leer_metadatos(str(p), None, _Senal())
    assert len(abiertas) == 1
    # Mismo parse: cabecera y luego el fichero ENTERO (400 000 bytes) con completo=True.
    assert rs.llamadas[0][1] is False and rs.llamadas[-1] == (400_000, True)
    assert dato.modelo == "M3T"


def test_rgb_y_solo_timestamp_no_leen_entero_de_mas(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)  # conftest lo apaga
    rgb = _fichero(tmp_path, "DJI_0002_W.JPG")
    rs = _Rs()
    monkeypatch.setattr(indice.rust_bridge, "exigir", lambda fase: rs)
    leidos = []
    monkeypatch.setattr(indice, "_leer_fichero_entero", lambda r: leidos.append(r) or b"\xff\xd8" * 10)
    pedido = []
    original = indice._leer_cabecera_cola_entero
    monkeypatch.setattr(indice, "_leer_cabecera_cola_entero",
                        lambda r, out: pedido.append(out) or original(r, out))
    indice._leer_metadatos(str(rgb), None, _Senal())
    assert pedido == [None]  # la RGB no pide el entero por adelantado (relee solo si Rust lo exige)
    termica = _fichero(tmp_path, "DJI_0003_T.JPG")
    indice._leer_metadatos(str(termica), None, _Senal(), solo_timestamp=True)
    assert pedido[-1] is None  # calendario previo: sin lectura entera adelantada (v3.5.5)


def test_entero_leido_con_la_cabecera_es_identico_a_reabrir(tmp_path, monkeypatch):
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)  # conftest lo apaga
    p = _fichero(tmp_path, "DJI_0004_T.JPG")
    out = []
    cab, a_medias, sin_eoi = indice._leer_cabecera_cola_entero(str(p), out)
    assert a_medias is False and out and out[0] == p.read_bytes()
    assert indice._leer_cabecera_y_cola(str(p)) == (cab, a_medias, sin_eoi)
