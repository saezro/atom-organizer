"""El .exe windowed tiene que dejar rastro en fichero.

Hasta la v3.4.76 `logging.basicConfig` solo corría con `--server`: en Windows
el logger raíz no tenía ningún handler y cada `logger.exception(...)` se
perdía. Por eso, cuando organizar reventaba, no había traceback que pedirle al
usuario. Estos tests fijan el comportamiento nuevo.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import app_webview
import external_tools


def _limpiar_handlers():
    raiz = logging.getLogger()
    for h in list(raiz.handlers):
        if getattr(h, "atom_log_app", False):
            raiz.removeHandler(h)
            h.close()


def test_configurar_log_a_fichero_escribe_el_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(external_tools, "user_log_dir", lambda: str(tmp_path / "Logs"))
    _limpiar_handlers()
    try:
        ruta = app_webview.configurar_log_a_fichero()
        assert ruta is not None
        try:
            raise ValueError("pandas roto de mentira")
        except ValueError:
            logging.getLogger("atom_core.precarga").exception("la precarga falló")
        contenido = (tmp_path / "Logs" / app_webview.LOG_APP_NOMBRE).read_text(encoding="utf-8")
    finally:
        _limpiar_handlers()
    # El mensaje Y el traceback: sin la traza el log no sirve para diagnosticar.
    assert "la precarga falló" in contenido
    assert "ValueError: pandas roto de mentira" in contenido
    assert "Traceback" in contenido


def test_configurar_log_a_fichero_no_apila_handlers(monkeypatch, tmp_path):
    """Dos llamadas no pueden duplicar cada línea del log."""
    monkeypatch.setattr(external_tools, "user_log_dir", lambda: str(tmp_path / "Logs"))
    _limpiar_handlers()
    try:
        app_webview.configurar_log_a_fichero()
        app_webview.configurar_log_a_fichero()
        propios = [h for h in logging.getLogger().handlers
                   if isinstance(h, RotatingFileHandler) and getattr(h, "atom_log_app", False)]
        assert len(propios) == 1
    finally:
        _limpiar_handlers()


def test_configurar_log_a_fichero_no_revienta_si_no_se_puede_escribir(monkeypatch):
    """Quedarse sin log es malo; no arrancar es peor."""
    def sin_carpeta():
        raise OSError("acceso denegado")

    monkeypatch.setattr(external_tools, "user_log_dir", sin_carpeta)
    _limpiar_handlers()
    assert app_webview.configurar_log_a_fichero() is None


def test_instalar_capturas_registra_excepcion_de_hilo(monkeypatch, caplog):
    """Lo que muere en un hilo nuevo tiene que acabar en el log, no en stderr."""
    import threading

    hook_previo = threading.excepthook
    sys_previo = __import__("sys").excepthook
    try:
        app_webview.instalar_capturas_de_excepcion()
        with caplog.at_level(logging.ERROR, logger="atom.excepthook"):
            hilo = threading.Thread(target=lambda: 1 / 0, name="hilo-de-prueba")
            hilo.start()
            hilo.join()
        assert "hilo-de-prueba" in caplog.text
        assert "ZeroDivisionError" in caplog.text
    finally:
        threading.excepthook = hook_previo
        __import__("sys").excepthook = sys_previo
