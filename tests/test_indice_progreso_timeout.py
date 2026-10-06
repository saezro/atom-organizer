"""Fase de Índice: progreso visible y tiempo máximo por fichero.

Origen en Drive for Desktop: un JPG sin descargar bloquea el `open()` minutos.
El índice no puede esperar sin límite ni quedarse mudo."""
import datetime as dt
import os
import threading

from atom_core import indice
from atom_core.indice import construir_indice

from .test_indice_organizado import (
    _ExifDePrueba, _PipelineDePrueba, _Signal, _cfg, _crear_imagen,
    _escribir_estadillo, _manifiesto)
from .test_tandas_mismo_destino import V1, VENTANAS


def _preparar(tmp_path, n=20):
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", *V1)])
    cfg = _cfg(tmp_path)
    rutas = [_crear_imagen(cfg.input_folder, f"DJI_{i:04d}_D.JPG") for i in range(n)]
    exif = _ExifDePrueba(timestamps={r: dt.datetime(2024, 6, 1, 10, 5, 0) for r in rutas})
    return cfg, rutas, exif


def _bloqueando(monkeypatch, bloqueada, liberar):
    original = indice._leer_metadatos

    def falsa(ruta, exif, cb):
        if ruta == bloqueada:
            liberar.wait(30)
        return original(ruta, exif, cb)

    monkeypatch.setattr(indice, "_leer_metadatos", falsa)


def test_un_fichero_colgado_no_cuelga_la_fase_y_se_informa(tmp_path, monkeypatch):
    cfg, rutas, exif = _preparar(tmp_path)
    liberar = threading.Event()
    _bloqueando(monkeypatch, rutas[7], liberar)
    m = _manifiesto(tmp_path)
    log, resumen_canal, barra = _Signal(), _Signal(), _Signal()
    try:
        resumen = construir_indice(
            cfg, _PipelineDePrueba(VENTANAS), exif, m, log, barra, resumen_canal,
            max_hilos=4, timeout_s=0.3, timeout_reintento_s=0.5, watchdog_s=0.1,
            intervalo_progreso_s=0.05)
    finally:
        liberar.set()

    assert resumen["no_disponibles"] == [rutas[7]]
    assert resumen["total"] == 19
    assert len(m.todas()) == 19
    texto_log = "\n".join(str(x) for x in log.mensajes)
    texto_resumen = "\n".join(str(x) for x in resumen_canal.mensajes)
    assert "Ha habido 1 error(es)" in texto_resumen and rutas[7] in texto_resumen
    assert f"esperando a {rutas[7]}" in texto_log          # watchdog
    assert "Reintentar lentas" in texto_log
    m.cerrar()


def test_el_fichero_lento_que_responde_en_el_reintento_se_procesa(tmp_path, monkeypatch):
    cfg, rutas, exif = _preparar(tmp_path)
    original = indice._leer_metadatos
    llamadas = {"n": 0}

    def falsa(ruta, exif_, cb):
        if ruta == rutas[3]:
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                threading.Event().wait(1.0)     # vence en la 1ª pasada
        return original(ruta, exif_, cb)

    monkeypatch.setattr(indice, "_leer_metadatos", falsa)
    m = _manifiesto(tmp_path)
    resumen = construir_indice(
        cfg, _PipelineDePrueba(VENTANAS), exif, m, _Signal(), _Signal(), _Signal(),
        max_hilos=4, timeout_s=0.2, timeout_reintento_s=5, watchdog_s=5,
        intervalo_progreso_s=0.05)
    assert resumen["no_disponibles"] == []
    assert resumen["total"] == 20
    m.cerrar()


def test_el_progreso_se_emite_por_subpaso_con_n_de_total_y_velocidad(tmp_path, monkeypatch):
    cfg, rutas, exif = _preparar(tmp_path)
    original = indice._leer_metadatos

    def lenta(ruta, exif_, cb):
        threading.Event().wait(0.02)
        return original(ruta, exif_, cb)

    monkeypatch.setattr(indice, "_leer_metadatos", lenta)
    m = _manifiesto(tmp_path)
    log, resumen_canal = _Signal(), _Signal()
    construir_indice(cfg, _PipelineDePrueba(VENTANAS), exif, m, log, _Signal(), resumen_canal,
                     max_hilos=2, intervalo_progreso_s=0.05)
    texto_log = "\n".join(str(x) for x in log.mensajes)
    for subpaso in ("Listar", "Leer EXIF/XMP", "Asignar vuelos"):
        assert f"Índice · {subpaso}" in texto_log
    assert "20/20" in texto_log and "img/s" in texto_log
    stats = [x for x in resumen_canal.mensajes if indice.STATS_INDICE_PREFIX in str(x)]
    intermedios = [s for s in stats if '"subpaso"' in str(s)]
    assert intermedios and any('"done": 20' in str(s) for s in intermedios)
    m.cerrar()
