import sys
import types

import app_webview


def test_app_webview_se_importa_sin_pywebview(monkeypatch):
    # En la Raspberry Pi no hay PySide6/QtWebEngine. Importar el modulo NO
    # puede depender de que `webview` exista.
    monkeypatch.setitem(sys.modules, "webview", None)
    assert hasattr(app_webview, "Api")


def test_parser_acepta_server_host_y_port():
    parser = app_webview._build_parser()
    args = parser.parse_args(["--server", "--port", "9000"])
    assert args.server is True
    assert args.port == 9000
    assert args.host == "127.0.0.1"  # nunca 0.0.0.0 por defecto


def test_parser_sin_flags_mantiene_el_modo_ventana():
    args = app_webview._build_parser().parse_args([])
    assert args.server is False
    assert args.dev is False


def test_import_webview_da_error_claro_si_no_esta(monkeypatch):
    def _boom(name, *a, **kw):
        raise ImportError("No module named 'webview'")

    monkeypatch.setattr(app_webview.importlib, "import_module", _boom)
    try:
        app_webview._import_webview()
    except RuntimeError as exc:
        assert "--server" in str(exc)  # le dice al usuario la salida
    else:
        raise AssertionError("deberia haber saltado RuntimeError")
