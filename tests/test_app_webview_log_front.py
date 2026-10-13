"""`Api.log_front`: reenvío de errores del front al log, con límite de frecuencia."""
from __future__ import annotations

import logging

from app_webview import Api


def test_log_front_escribe_con_prefijo_y_trunca(caplog):
    api = Api()
    with caplog.at_level(logging.WARNING):
        r = api.log_front("error", "x" * 9000, "d")
    assert r["ok"] is True
    assert caplog.records[-1].getMessage().startswith("[front] ")
    assert len(caplog.records[-1].getMessage()) <= 4000 + 10


def test_log_front_limita_por_minuto():
    api = Api()
    res = [api.log_front("warning", f"m{i}") for i in range(40)]
    assert sum(1 for r in res if r["ok"]) == 30
    assert res[-1].get("limitado") is True


def test_log_front_nunca_lanza():
    api = Api()
    assert api.log_front(None, object(), object())["ok"] in (True, False)
