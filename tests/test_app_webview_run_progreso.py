"""Telemetría en vivo de `Api.cloud_upload()` hacia el `RunReporter`.

Antes de este cableado, `/organizer` solo se enteraba de una subida al
terminar (`_reportar_subida`, vía `organizer_subidas`): una subida de horas
era invisible mientras corría. Ahora `cloud_upload` da de alta un run
(`reporter.iniciar`), late su progreso en cada `on_stats`
(`_on_stats_subida`) y lo cierra siempre (`_cerrar_run`), sea éxito, fallo o
cancelación — todo fail-open: sin reporter (sin login, o la Suite rechazó el
alta) la subida debe funcionar exactamente igual.

Sigue las convenciones de `test_app_webview_broker.py` (import directo de
`app_webview`, sin `webview` real) y de `test_app_webview_inventario_cache.py`
(`Api(broker=True)` + auth falsa). El hilo de `cloud_upload` sí se lanza de
verdad (no se sustituye `threading.Thread`, a diferencia del test de
inventario): lo que se espera es a que `api._uploading` vuelva a `False`, con
polling corto — nunca un `sleep` fijo largo.
"""
from __future__ import annotations

import time

import pytest

import app_webview as aw
from atom_core import cloud_upload


@pytest.fixture(autouse=True)
def sin_cliente_oauth(monkeypatch):
    from atom_core import cloud_config

    monkeypatch.setattr(cloud_config, "load_client", lambda base_dir=None: None)


class _AuthFalso:
    """Lo mínimo que `cloud_upload` necesita de una auth: logueada y con
    identidad, para que `GcsOAuthProvider`/`RunReporter` no revienten al
    construirse (aquí siempre van sustituidos, pero por si acaso)."""

    identity = None

    def is_logged_in(self) -> bool:
        return True


class _ReporterFalso:
    """Doble de `RunReporter`: registra las llamadas en vez de hablar con la
    Suite. `activo` se controla desde fuera para simular el alta rechazada."""

    def __init__(self, activo: bool = True):
        self.activo = activo
        self.llamadas_iniciar: list[dict] = []
        self.llamadas_progreso: list[dict] = []
        self.llamadas_fin: list[dict] = []

    def iniciar(self, *, inspeccion=None, etapa=None, items_total=None,
                bytes_total=None):
        self.llamadas_iniciar.append({
            "inspeccion": inspeccion, "etapa": etapa,
            "items_total": items_total, "bytes_total": bytes_total,
        })

    def progreso(self, stats: dict) -> None:
        self.llamadas_progreso.append(stats)

    def fin(self, *, ok: bool, error: str | None = None) -> None:
        self.llamadas_fin.append({"ok": ok, "error": error})


def _plan_falso(root, prefix, n_items=2):
    """`UploadPlan` real y mínimo: dos `UploadItem` con tamaño fijo, para que
    `plan.total_bytes`/`len(plan.items)` salgan predecibles."""
    items = [
        cloud_upload.UploadItem(local=root / f"f{i}.jpg",
                                remote=f"{prefix}/f{i}.jpg", size=100)
        for i in range(n_items)
    ]
    return cloud_upload.UploadPlan(root=root, items=items, prefix=prefix)


def _resultado_ok(bytes_sent=200):
    return cloud_upload.UploadResult(uploaded=2, bytes_sent=bytes_sent,
                                     elapsed=0.1, reconciliado=True)


class _SinkFalso:
    """Captura lo que `_push_cloud` manda por `atom:cloud`, sin `pywebview`
    real de por medio."""

    def __init__(self):
        self.eventos: list[dict] = []

    def dispatch(self, event: str, detail: dict) -> None:
        if event == "atom:cloud":
            self.eventos.append(detail)

    def dispatch_many(self, event: str, details: list[dict]) -> None:
        for d in details:
            self.dispatch(event, d)


@pytest.fixture
def api(monkeypatch, tmp_path):
    """`Api(broker=True)` con auth falsa siempre logueada, sink capturado y
    `GcsOAuthProvider`/`upload_file` fuera de juego (nunca se toca red).

    `detectar_estadillos` y `_subir_objeto_json` van fuera de juego también:
    estos tests son de la telemetría del reporter (`RunReporter`), no del
    contrato de lotes/estadillo (eso lo cubre `tests/test_lotes.py`), y sin
    esto la gate "sin estadillo no se sube" y el intento real de subir
    `manifest.json` con un provider falso los harían fallar por motivos
    ajenos a lo que verifican.
    """
    from atom_core import estadillo as estadillo_mod

    a = aw.Api(broker=True)
    monkeypatch.setattr(a, "_get_auth", lambda: _AuthFalso())
    sink = _SinkFalso()
    a.bind_sink(sink)
    monkeypatch.setattr(cloud_upload, "GcsOAuthProvider",
                        lambda bucket, auth, **kw: object())
    root = tmp_path / "carpeta"
    root.mkdir()
    estadillo_falso = root / "estadillo.csv"
    estadillo_falso.write_text("id;planta\n")
    monkeypatch.setattr(estadillo_mod, "detectar_estadillos",
                        lambda carpeta, **kw: {"rutas": [str(estadillo_falso)],
                                               "descartados": []})
    monkeypatch.setattr(a, "_subir_objeto_json", lambda remoto, contenido: None)
    return a, sink, root


def _esperar_fin(api_obj, timeout=5.0):
    """Espera a que el hilo de `cloud_upload` termine (`_uploading` vuelve a
    `False`), con polling corto en vez de un `sleep` fijo."""
    t0 = time.monotonic()
    while api_obj._uploading:
        if time.monotonic() - t0 > timeout:
            raise AssertionError("cloud_upload no terminó a tiempo")
        time.sleep(0.01)


def _eventos_de(sink, kind: str) -> list[dict]:
    return [e for e in sink.eventos if e.get("kind") == kind]


# ---------------------------------------------------------------------------
# 1. Subida OK: iniciar/progreso/fin se llaman con los datos correctos.
# ---------------------------------------------------------------------------
def test_subida_ok_reporta_iniciar_progreso_y_fin(api, monkeypatch):
    a, sink, root = api
    reporter = _ReporterFalso(activo=True)
    monkeypatch.setattr(a, "_reporter_actual", lambda: reporter)

    plan = _plan_falso(root, "PLANTA_X")
    monkeypatch.setattr(cloud_upload, "build_plan", lambda r, p: plan)

    def fake_upload_plan(plan_, provider, *, on_progress=None, on_stats=None,
                         should_stop=None, remotos=None, **kw):
        # Simula al menos un snapshot de progreso real, como haría la subida.
        on_stats({"uploaded": 1, "total": 2, "bytes_sent": 100})
        return _resultado_ok()

    monkeypatch.setattr(cloud_upload, "upload_plan", fake_upload_plan)

    res = a.cloud_upload(str(root), prefix="PLANTA_X")
    assert res["started"] is True
    _esperar_fin(a)

    assert len(reporter.llamadas_iniciar) == 1
    ini = reporter.llamadas_iniciar[0]
    assert ini["inspeccion"] == "PLANTA_X"
    assert ini["items_total"] == len(plan.items)
    assert ini["bytes_total"] == plan.total_bytes

    assert len(reporter.llamadas_progreso) >= 1

    assert len(reporter.llamadas_fin) == 1
    assert reporter.llamadas_fin[0]["ok"] is True


# ---------------------------------------------------------------------------
# 2. Alta rechazada (`reporter.activo` False tras `iniciar`): no se llama a
#    progreso ni a fin, y la subida local sigue y termina en éxito.
# ---------------------------------------------------------------------------
def test_alta_rechazada_no_llama_progreso_ni_fin_pero_la_subida_termina(api, monkeypatch):
    a, sink, root = api
    reporter = _ReporterFalso(activo=False)  # la Suite no aceptó el run
    monkeypatch.setattr(a, "_reporter_actual", lambda: reporter)

    plan = _plan_falso(root, "PLANTA_Y")
    monkeypatch.setattr(cloud_upload, "build_plan", lambda r, p: plan)

    def fake_upload_plan(plan_, provider, *, on_progress=None, on_stats=None,
                         should_stop=None, remotos=None, **kw):
        on_stats({"uploaded": 1, "total": 2, "bytes_sent": 100})
        return _resultado_ok()

    monkeypatch.setattr(cloud_upload, "upload_plan", fake_upload_plan)

    res = a.cloud_upload(str(root), prefix="PLANTA_Y")
    assert res["started"] is True
    _esperar_fin(a)

    # `iniciar` sí se llama (es lo que revela que la Suite lo rechazó), pero
    # ni `progreso` ni `fin`: `cloud_upload` suelta el reporter a `None` en
    # cuanto ve `activo is False`.
    assert len(reporter.llamadas_iniciar) == 1
    assert reporter.llamadas_progreso == []
    assert reporter.llamadas_fin == []

    done = _eventos_de(sink, "done")
    assert len(done) == 1
    assert done[0]["ok"] is True


# ---------------------------------------------------------------------------
# 3. Cancelación: `fin(ok=False)` con error no vacío.
# ---------------------------------------------------------------------------
def test_subida_cancelada_cierra_run_con_ok_false_y_error(api, monkeypatch):
    a, sink, root = api
    reporter = _ReporterFalso(activo=True)
    monkeypatch.setattr(a, "_reporter_actual", lambda: reporter)

    plan = _plan_falso(root, "PLANTA_Z")
    monkeypatch.setattr(cloud_upload, "build_plan", lambda r, p: plan)

    def fake_upload_plan(plan_, provider, *, on_progress=None, on_stats=None,
                         should_stop=None, remotos=None, **kw):
        # El worker marca `_cancel_upload` ANTES de que termine `upload_plan`,
        # como haría un operador pulsando "cancelar" a mitad de subida.
        a._cancel_upload = True
        return _resultado_ok()

    monkeypatch.setattr(cloud_upload, "upload_plan", fake_upload_plan)

    res = a.cloud_upload(str(root), prefix="PLANTA_Z")
    assert res["started"] is True
    _esperar_fin(a)

    assert len(reporter.llamadas_fin) == 1
    cierre = reporter.llamadas_fin[0]
    assert cierre["ok"] is False
    assert cierre["error"]  # no vacío


# ---------------------------------------------------------------------------
# 4. Sin `_reporter_actual` (sin login): la subida funciona igual, sin
#    reventar, y llega el `done` al sink.
# ---------------------------------------------------------------------------
def test_sin_reporter_la_subida_funciona_igual(api, monkeypatch):
    a, sink, root = api
    monkeypatch.setattr(a, "_reporter_actual", lambda: None)

    plan = _plan_falso(root, "PLANTA_W")
    monkeypatch.setattr(cloud_upload, "build_plan", lambda r, p: plan)

    def fake_upload_plan(plan_, provider, *, on_progress=None, on_stats=None,
                         should_stop=None, remotos=None, **kw):
        on_stats({"uploaded": 1, "total": 2, "bytes_sent": 100})
        return _resultado_ok()

    monkeypatch.setattr(cloud_upload, "upload_plan", fake_upload_plan)

    res = a.cloud_upload(str(root), prefix="PLANTA_W")
    assert res["started"] is True
    _esperar_fin(a)

    done = _eventos_de(sink, "done")
    assert len(done) == 1
    assert done[0]["ok"] is True


# ---------------------------------------------------------------------------
# 5. Unitario de `_on_stats_subida`: repinta siempre; llama a `progreso` del
#    reporter solo si lo hay, y con el mismo dict.
# ---------------------------------------------------------------------------
def test_on_stats_subida_con_reporter_none_solo_repinta(api):
    a, sink, root = api
    stats = {"uploaded": 1, "total": 5}

    a._on_stats_subida(None, stats)  # no debe lanzar

    eventos = _eventos_de(sink, "stats")
    assert len(eventos) == 1
    assert eventos[0]["uploaded"] == 1
    assert eventos[0]["total"] == 5


def test_on_stats_subida_con_reporter_repinta_y_llama_progreso(api):
    a, sink, root = api
    reporter = _ReporterFalso(activo=True)
    stats = {"uploaded": 3, "total": 5}

    a._on_stats_subida(reporter, stats)

    eventos = _eventos_de(sink, "stats")
    assert len(eventos) == 1
    assert eventos[0]["uploaded"] == 3

    assert reporter.llamadas_progreso == [stats]
