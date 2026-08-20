"""`Api.cloud_upload` y el lote por CARPETA (`atom_core/lotes.py`).

Tres ramas, la nueva regla de negocio:

1. Sin estado previo → se genera un lote nuevo y se registra (incompleto).
2. Estado previo INCOMPLETO (no se llegó a escribir `manifest.json`: se cortó
   o se canceló) → se REANUDA el mismo lote sin preguntar nada.
3. Estado previo COMPLETO (`manifest.json` ya se subió) → NO se sube nada sin
   confirmación explícita (`confirmar_subida_extra`); con ella, se abre un
   lote NUEVO (otro estadillo sobre la misma carpeta).

Sigue las convenciones de `test_app_webview_run_progreso.py`: `Api(broker=True)`
+ auth falsa, `build_plan`/`upload_plan`/`GcsOAuthProvider`/`_subir_objeto_json`
fuera de juego, y polling corto de `_uploading` en vez de un `sleep` fijo.
"""
from __future__ import annotations

import time

import pytest

import app_webview as aw
from atom_core import cloud_upload, lotes


@pytest.fixture(autouse=True)
def sin_cliente_oauth(monkeypatch):
    from atom_core import cloud_config

    monkeypatch.setattr(cloud_config, "load_client", lambda base_dir=None: None)


@pytest.fixture(autouse=True)
def user_data_dir_aislado(monkeypatch, tmp_path):
    """El estado de lotes vive en `user_data_dir()`: aislado a `tmp_path`
    para no tocar el `~/.config/atom-organizer` real de quien corre esto."""
    from atom_core import google_auth

    destino = tmp_path / "config-atom-organizer"
    monkeypatch.setattr(google_auth, "user_data_dir", lambda: destino)


class _AuthFalso:
    identity = None

    def is_logged_in(self) -> bool:
        return True


class _SinkFalso:
    def __init__(self):
        self.eventos: list[dict] = []

    def dispatch(self, event: str, detail: dict) -> None:
        if event == "atom:cloud":
            self.eventos.append(detail)

    def dispatch_many(self, event: str, details: list[dict]) -> None:
        for d in details:
            self.dispatch(event, d)


def _plan_falso(root, prefix, n_items=2):
    items = [
        cloud_upload.UploadItem(local=root / f"f{i}.jpg",
                                remote=f"{prefix}/f{i}.jpg", size=100)
        for i in range(n_items)
    ]
    return cloud_upload.UploadPlan(root=root, items=items, prefix=prefix)


def _resultado_ok(bytes_sent=200):
    return cloud_upload.UploadResult(uploaded=2, bytes_sent=bytes_sent,
                                     elapsed=0.1, reconciliado=True)


@pytest.fixture
def api(monkeypatch, tmp_path):
    from atom_core import estadillo as estadillo_mod

    a = aw.Api(broker=True)
    monkeypatch.setattr(a, "_get_auth", lambda: _AuthFalso())
    monkeypatch.setattr(a, "_reporter_actual", lambda: None)
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
    t0 = time.monotonic()
    while api_obj._uploading:
        if time.monotonic() - t0 > timeout:
            raise AssertionError("cloud_upload no terminó a tiempo")
        time.sleep(0.01)


def _prefijo_usado(build_plan_calls):
    """Los `prefix_lote` con los que se llamó a `build_plan`."""
    return [c[1] for c in build_plan_calls]


def _mock_build_plan(monkeypatch, root, calls):
    def fake(r, p):
        calls.append((r, p))
        return _plan_falso(r, p)
    monkeypatch.setattr(cloud_upload, "build_plan", fake)


def _mock_upload_plan_ok(monkeypatch):
    def fake(plan_, provider, *, on_progress=None, on_stats=None,
            should_stop=None, remotos=None, **kw):
        return _resultado_ok()
    monkeypatch.setattr(cloud_upload, "upload_plan", fake)


# ---------------------------------------------------------------------------
# 1. Sin estado previo: lote nuevo, registrado incompleto y luego completo.
# ---------------------------------------------------------------------------
def test_carpeta_sin_lote_previo_genera_uno_nuevo(api, monkeypatch):
    a, sink, root = api
    calls = []
    _mock_build_plan(monkeypatch, root, calls)
    _mock_upload_plan_ok(monkeypatch)

    assert lotes.estado_lote_carpeta(root) is None

    res = a.cloud_upload(str(root), prefix="PLANTA_X")
    assert res["started"] is True
    _esperar_fin(a)

    assert len(calls) == 1
    prefix_lote = calls[0][1]
    assert prefix_lote.startswith(f"PLANTA_X/{lotes.CARPETA_SUBIDAS}/")

    estado = lotes.estado_lote_carpeta(root)
    assert estado is not None
    assert estado["completo"] is True  # manifest.json se subió sin fallos


# ---------------------------------------------------------------------------
# 2. Lote INCOMPLETO (reintento tras corte/cancelación): se reanuda el mismo,
#    sin preguntar nada y sin `requiere_confirmacion`.
# ---------------------------------------------------------------------------
def test_lote_incompleto_se_reanuda_sin_confirmacion(api, monkeypatch):
    a, sink, root = api
    lotes.registrar_lote(root, "2026-08-20T100000Z__piloto")  # incompleto

    calls = []
    _mock_build_plan(monkeypatch, root, calls)
    _mock_upload_plan_ok(monkeypatch)

    res = a.cloud_upload(str(root), prefix="PLANTA_X")
    assert res["started"] is True
    _esperar_fin(a)

    assert len(calls) == 1
    prefix_lote = calls[0][1]
    assert prefix_lote == (
        f"PLANTA_X/{lotes.CARPETA_SUBIDAS}/2026-08-20T100000Z__piloto")
    assert "requiere_confirmacion" not in res

    # Y ahora sí queda completo (se pudo escribir manifest.json).
    assert lotes.estado_lote_carpeta(root) == {
        "lote": "2026-08-20T100000Z__piloto", "completo": True}


# ---------------------------------------------------------------------------
# 3a. Lote COMPLETO sin confirmación: no se sube nada, se informa del lote
#     anterior, y `build_plan`/`upload_plan` no se llegan a tocar.
# ---------------------------------------------------------------------------
def test_lote_completo_sin_confirmacion_no_sube_nada(api, monkeypatch):
    a, sink, root = api
    lotes.registrar_lote(root, "L_VIEJO")
    lotes.marcar_lote_completo(root, "L_VIEJO")

    calls = []
    _mock_build_plan(monkeypatch, root, calls)
    _mock_upload_plan_ok(monkeypatch)

    res = a.cloud_upload(str(root), prefix="PLANTA_X")

    assert res["started"] is False
    assert res["requiere_confirmacion"] is True
    assert res["lote_anterior"] == "L_VIEJO"
    assert calls == []  # no se construyó ningún plan: no se subió nada
    assert a._uploading is False

    # El estado no cambia: sigue siendo el lote viejo, completo.
    assert lotes.estado_lote_carpeta(root) == {"lote": "L_VIEJO", "completo": True}


# ---------------------------------------------------------------------------
# 3b. Lote COMPLETO con confirmación: se abre un lote NUEVO (otro estadillo).
# ---------------------------------------------------------------------------
def test_lote_completo_con_confirmacion_abre_lote_nuevo(api, monkeypatch):
    a, sink, root = api
    lotes.registrar_lote(root, "L_VIEJO")
    lotes.marcar_lote_completo(root, "L_VIEJO")

    calls = []
    _mock_build_plan(monkeypatch, root, calls)
    _mock_upload_plan_ok(monkeypatch)

    res = a.cloud_upload(str(root), prefix="PLANTA_X",
                         confirmar_subida_extra=True)
    assert res["started"] is True
    _esperar_fin(a)

    assert len(calls) == 1
    prefix_lote = calls[0][1]
    assert "L_VIEJO" not in prefix_lote  # es otro lote, no el mismo

    estado = lotes.estado_lote_carpeta(root)
    assert estado["lote"] != "L_VIEJO"
    assert estado["completo"] is True
