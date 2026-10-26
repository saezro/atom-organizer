"""Refresco de token deduplicado y circuit breaker de 403 persistente en
`cloud_upload`. Todo con mocks de urlopen/auth: nunca red real."""
from __future__ import annotations

import io
import threading
import urllib.error
from pathlib import Path

import pytest

from atom_core import cloud_upload as cu
from atom_core.google_auth import RateLimitAuthError


def _http(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://storage.googleapis.com/b/o?X-Goog-Signature=SECRETO",
                                  code, "Forbidden", {}, io.BytesIO(body))


class _Auth:
    es_password = False
    es_google = False

    def __init__(self, fallo=None):
        self.llamadas = 0
        self.fallo = fallo
        self._lock = threading.Lock()

    def access_token(self, **kw):
        with self._lock:
            self.llamadas += 1
        if self.fallo and kw.get("force_refresh"):
            raise self.fallo
        return "tok"


def test_refresco_deduplicado_entre_hilos():
    auth = _Auth()
    prov = cu.GcsOAuthProvider("b", auth)
    hilos = [threading.Thread(target=prov.recover_auth) for _ in range(16)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert auth.llamadas == 1


def test_refresco_tras_429_espera_y_refresca(monkeypatch):
    auth = _Auth(fallo=RateLimitAuthError("429", retry_after=5))
    prov = cu.GcsOAuthProvider("b", auth)
    with pytest.raises(RateLimitAuthError):
        prov.recover_auth()
    assert prov._sin_pedir_hasta - cu.time.monotonic() > 50  # >= 60 s
    # En cooldown el hilo espera y refresca de verdad (no devuelve True con
    # el token caducado).
    esperas = []
    monkeypatch.setattr(cu.time, "sleep", lambda s: esperas.append(s))
    auth.fallo = None
    assert prov.recover_auth() is True
    assert esperas and esperas[0] > 50
    assert auth.llamadas == 2


def test_reutiliza_solo_si_el_token_es_mas_nuevo():
    auth = _Auth()
    prov = cu.GcsOAuthProvider("b", auth)
    prov.headers()                 # este hilo usa el token de la generación 0
    otro = threading.Thread(target=prov.recover_auth)
    otro.start()
    otro.join()                    # otro hilo refresca: generación 1
    n = auth.llamadas
    assert prov.recover_auth() is True   # token más nuevo que el suyo: reutiliza
    assert auth.llamadas == n
    prov.headers()                 # ahora usa la generación 1 y da 403
    n = auth.llamadas
    assert prov.recover_auth() is True
    assert auth.llamadas == n + 1  # no hay más nuevo: refresca de verdad


def test_refresco_fallido_no_se_repite_en_serie():
    auth = _Auth(fallo=RuntimeError("boom"))
    prov = cu.GcsOAuthProvider("b", auth)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            prov.recover_auth()
    assert auth.llamadas == 1


class _Prov(cu.UrlProvider):
    def __init__(self):
        self.recuperaciones = 0

    def upload_url(self, remote, size, *, sobrescribir=False):
        return "https://storage.googleapis.com/b/" + remote + "?X-Goog-Signature=SECRETO"

    def headers(self):
        return {"Authorization": "Bearer SECRETO"}

    def recover_auth(self):
        self.recuperaciones += 1
        return True


def _plan(tmp: Path, n: int) -> cu.UploadPlan:
    items = []
    for i in range(n):
        f = tmp / f"f{i}.jpg"
        f.write_bytes(b"x")
        items.append(cu.UploadItem(local=f, remote=f"P/f{i}.jpg", size=1))
    return cu.UploadPlan(root=tmp, prefix="P/", items=items)


def test_403_persistente_aborta_con_motivo(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(cu.time, "sleep", lambda s: None)
    llamadas = []

    def urlopen(req, timeout=None):
        llamadas.append(req.full_url)
        raise _http(403, b"x does not have storage.objects.create access")

    monkeypatch.setattr(cu.urllib.request, "urlopen", urlopen)
    plan = _plan(tmp_path, 40)
    with caplog.at_level("ERROR"):
        res = cu.upload_plan(plan, _Prov(), concurrency=1,
                             manifest=cu.Manifest(tmp_path / "m.json"))
    assert res.abortado and "403" in res.abortado
    assert "storage.objects.create" in res.abortado
    assert res.uploaded == 0
    # K=3 ficheros x 2 intentos, no los 40
    assert len(llamadas) <= cu.MAX_403_CONSECUTIVOS * 2
    log = caplog.text
    assert "does not have storage.objects.create access" in log
    assert "SECRETO" not in log and "Bearer" not in log


def test_403_aislado_no_aborta(tmp_path, monkeypatch):
    monkeypatch.setattr(cu.time, "sleep", lambda s: None)
    estado = {"n": 0}

    class Resp:
        status = 200
        headers = {"Location": "https://s/up"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        estado["n"] += 1
        if estado["n"] == 1:
            raise _http(403, b"caducado")
        return Resp()

    monkeypatch.setattr(cu.urllib.request, "urlopen", urlopen)
    res = cu.upload_plan(_plan(tmp_path, 3), _Prov(), concurrency=1,
                         manifest=cu.Manifest(tmp_path / "m.json"))
    assert res.abortado is None and res.uploaded == 3


def test_breaker_con_mas_ficheros_que_hilos_no_revienta(tmp_path, monkeypatch):
    monkeypatch.setattr(cu.time, "sleep", lambda s: None)

    def urlopen(req, timeout=None):
        raise _http(403, b"sin permiso")

    monkeypatch.setattr(cu.urllib.request, "urlopen", urlopen)
    res = cu.upload_plan(_plan(tmp_path, 40), _Prov(), concurrency=2,
                         manifest=cu.Manifest(tmp_path / "m.json"))
    assert res.abortado and res.uploaded == 0


def test_breaker_se_resetea_con_yaexiste_o_fallo_no_403(tmp_path, monkeypatch):
    resultados = iter([cu.GcsRechazado("x"), cu.GcsRechazado("x"), cu.YaExiste("r"),
                       cu.GcsRechazado("x"), cu.GcsRechazado("x"), OSError("red"),
                       cu.GcsRechazado("x"), cu.GcsRechazado("x")])

    def falso(item, provider, **kw):
        raise next(resultados)

    monkeypatch.setattr(cu, "upload_file", falso)
    res = cu.upload_plan(_plan(tmp_path, 8), _Prov(), concurrency=1,
                         manifest=cu.Manifest(tmp_path / "m.json"))
    assert res.abortado is None


def test_refrescado_403_se_resetea_tras_chunk_correcto(tmp_path, monkeypatch):
    monkeypatch.setattr(cu.time, "sleep", lambda s: None)
    monkeypatch.setattr(cu, "CHUNK_SIZE", 1)
    f = tmp_path / "a.bin"
    f.write_bytes(b"abc")
    item = cu.UploadItem(local=f, remote="P/a.bin", size=3)
    monkeypatch.setattr(cu, "_open_session", lambda url, h=None, *a, **k: "https://s/up")
    monkeypatch.setattr(cu, "_committed_offset", lambda *a, **k: 0)
    monkeypatch.setattr(cu, "_file_md5_b64", lambda p: "md5")
    puts = {"n": 0}

    def put(uri, chunk, start, total, headers):
        puts["n"] += 1
        if puts["n"] in (2, 4):   # token caduca dos veces en el mismo fichero
            raise _http(403, b"caducado")

    monkeypatch.setattr(cu, "_put_chunk", put)
    prov = _Prov()
    assert cu.upload_file(item, prov) == "md5"
    assert prov.recuperaciones == 2
