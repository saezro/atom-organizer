"""El arranque de un task NUNCA puede dejar al front sin noticias.

Dos fallos reales de la 3.4.72 dejaban el modal de progreso clavado en
"Preparando…" sin un solo evento y sin botón para salir:

1. `run_task` llamaba a `cloud_asegurar_estado()` en línea. Eso hace red (refresh
   del token contra Google, con su plazo, y un lock que la comprobación de
   arranque puede tener tomado), y como el bridge de Qt es un hilo único, la
   llamada no devolvía: la promesa del front no resolvía JAMÁS.
2. El `import` de `atom_core.organize` estaba FUERA del `try` de
   `_run_task_worker`. Si fallaba (una dependencia que PyInstaller no empaquetó
   en el .exe), el hilo moría en silencio: cero eventos y `_running` clavado en
   `True`, así que ni siquiera se podía reintentar.

Convenciones tomadas de `test_app_webview_run_progreso.py`.
"""
from __future__ import annotations

import time

import pytest

import app_webview as aw


@pytest.fixture(autouse=True)
def sin_cliente_oauth(monkeypatch):
    from atom_core import cloud_config

    monkeypatch.setattr(cloud_config, "load_client", lambda base_dir=None: None)


def _esperar(cond, plazo=5.0):
    fin = time.time() + plazo
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_run_task_devuelve_aunque_la_nube_no_conteste(monkeypatch):
    """El indicador de la nube es cortesía: no puede retrasar el arranque."""
    api = aw.Api(broker=True)
    monkeypatch.setattr(api, "_run_task_worker", lambda *a, **k: None)

    # Red que tarda un mundo. Acotada a 3 s (y no infinita) para que la
    # regresión se manifieste como un fallo por lento, no como un test colgado.
    monkeypatch.setattr(api, "cloud_asegurar_estado", lambda: time.sleep(3.0))

    inicio = time.time()
    assert api.run_task("split_images", {"origen": "/x", "destino": "/y"}) == {"started": True}
    assert time.time() - inicio < 1.0


def test_un_fallo_de_import_se_reporta_como_error(monkeypatch):
    """Si el pipeline ni se puede cargar, el front tiene que enterarse."""
    api = aw.Api(broker=True)
    monkeypatch.setattr(api, "cloud_asegurar_estado", lambda: None)

    eventos: list[dict] = []
    monkeypatch.setattr(api, "_push", lambda detail: eventos.append(detail))
    monkeypatch.setattr(api, "_flush_push", lambda: None)

    import builtins

    real_import = builtins.__import__

    def import_roto(nombre, *args, **kwargs):
        if nombre == "atom_core.organize":
            raise ImportError("DLL load failed: falta el SDK")
        return real_import(nombre, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_roto)

    api._run_task_worker("split_images", {}, None)

    # El error viaja al modal...
    assert any(e.get("kind") == "error" and "DLL load failed" in e.get("text", "")
               for e in eventos), eventos
    # ...y el flag se libera, o no se podría reintentar sin reabrir la app.
    assert api._running is False
