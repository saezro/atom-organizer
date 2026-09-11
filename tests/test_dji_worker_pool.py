"""
Protocolo del pool de workers persistentes del SDK térmico DJI en Linux no-x86
(`dji_worker_pool.py` + modo `--server` de `dji_irp_linux.py`).

No necesita el SDK real ni box64:
- `_serve()` se ejercita en proceso, con `measure()` monkeypatcheado.
- `dji_worker_pool` se ejercita contra un proceso stub (mismo protocolo JSON
  por stdin/stdout, sin libdirp) para probar reutilización de workers y
  descarte tras un fallo, que es lo que garantiza el fallback a la vía antigua.
"""
import io
import json
import os
import sys
import textwrap

import pytest

import dji_irp_linux
import dji_worker_pool
import external_tools


# --- 1. Protocolo del modo servidor (dji_irp_linux._serve) ------------------
def test_serve_responde_ok_y_reutiliza_measure(monkeypatch):
    llamadas = []

    def fake_measure(img, raw_out, humidity, emissivity, lib_dir):
        llamadas.append((img, raw_out, humidity, emissivity, lib_dir))

    monkeypatch.setattr(dji_irp_linux, "measure", fake_measure)
    monkeypatch.setattr(dji_irp_linux.os, "_exit", lambda code: None)

    peticion = json.dumps({
        "img": "a.jpg", "raw_out": "a.raw", "humidity": 50.0,
        "emissivity": 0.9, "lib_dir": "/lib",
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(peticion + "\n" + json.dumps({"cmd": "quit"}) + "\n"))
    salida = io.StringIO()
    monkeypatch.setattr(sys, "stdout", salida)

    dji_irp_linux._serve()

    assert llamadas == [("a.jpg", "a.raw", 50.0, 0.9, "/lib")]
    lineas = [l for l in salida.getvalue().splitlines() if l.strip()]
    assert json.loads(lineas[0]) == {"ok": True}


def test_serve_responde_error_si_measure_lanza(monkeypatch):
    def fake_measure(*a, **k):
        raise RuntimeError("dirp_measure_ex rc=-1")

    monkeypatch.setattr(dji_irp_linux, "measure", fake_measure)
    monkeypatch.setattr(dji_irp_linux.os, "_exit", lambda code: None)

    peticion = json.dumps({
        "img": "a.jpg", "raw_out": "a.raw", "humidity": 50.0,
        "emissivity": 0.9, "lib_dir": "/lib",
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(peticion + "\n" + json.dumps({"cmd": "quit"}) + "\n"))
    salida = io.StringIO()
    monkeypatch.setattr(sys, "stdout", salida)

    dji_irp_linux._serve()

    lineas = [l for l in salida.getvalue().splitlines() if l.strip()]
    resp = json.loads(lineas[0])
    assert resp["ok"] is False
    assert "dirp_measure_ex" in resp["error"]


# --- 2. dji_worker_pool contra un proceso stub (mismo protocolo, sin SDK) ---
_STUB_SERVER = textwrap.dedent("""
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("cmd") == "quit":
            break
        if req.get("emissivity") == -1:
            sys.stdout.write(json.dumps({"ok": False, "error": "boom simulado"}) + "\\n")
            sys.stdout.flush()
            continue
        if req.get("emissivity") == -2:
            sys.exit(1)  # muere sin responder: simula worker caído
        with open(req["raw_out"], "wb") as f:
            f.write(b"\\x00\\x00\\x00\\x00")  # 4 bytes = un float32, raw_ok
        sys.stdout.write(json.dumps({"ok": True}) + "\\n")
        sys.stdout.flush()
""")


@pytest.fixture
def stub_pool(tmp_path, monkeypatch):
    """Apunta dji_worker_pool a un proceso stub en vez de dji_irp_linux.py real,
    y fuerza el lanzador a `[sys.executable]` sin box64 (equivalente al camino
    x86-64 nativo de `dji_linux_launcher`, aquí reutilizado solo como vehículo
    de proceso para no depender del SDK)."""
    stub = tmp_path / "stub_server.py"
    stub.write_text(_STUB_SERVER, encoding="utf-8")
    monkeypatch.setattr(dji_worker_pool, "_DJI_IRP_LINUX", str(stub))
    monkeypatch.setattr(external_tools, "dji_linux_launcher", lambda lib_dir: ([sys.executable], {}))
    monkeypatch.setattr(dji_worker_pool, "_pool", None)
    yield
    dji_worker_pool.shutdown()


def test_pool_reutiliza_el_mismo_worker_entre_llamadas(tmp_path, stub_pool):
    raw1 = tmp_path / "a.raw"
    raw2 = tmp_path / "b.raw"
    dji_worker_pool.measure("a.jpg", str(raw1), 50.0, 0.9, "/lib")
    dji_worker_pool.measure("b.jpg", str(raw2), 50.0, 0.9, "/lib")

    assert raw1.exists() and raw1.stat().st_size == 4
    assert raw2.exists() and raw2.stat().st_size == 4
    # Dos peticiones secuenciales -> un solo worker creado (se reutiliza, no se
    # relanza el proceso por imagen, que es justo lo que amortiza el arranque).
    assert dji_worker_pool._pool._n_created == 1


def test_pool_descarta_worker_tras_error_del_sdk(tmp_path, stub_pool):
    with pytest.raises(RuntimeError, match="boom simulado"):
        dji_worker_pool.measure("bad.jpg", str(tmp_path / "bad.raw"), 50.0, -1, "/lib")
    # El worker que devolvió el error queda descartado (no vuelve a la cola de
    # disponibles): la siguiente petición crea uno nuevo.
    assert dji_worker_pool._pool._idle.qsize() == 0
    raw_ok = tmp_path / "ok.raw"
    dji_worker_pool.measure("ok.jpg", str(raw_ok), 50.0, 0.9, "/lib")
    assert raw_ok.exists()


def test_pool_descarta_worker_muerto_y_permite_reintento(tmp_path, stub_pool):
    with pytest.raises(RuntimeError):
        dji_worker_pool.measure("dead.jpg", str(tmp_path / "dead.raw"), 50.0, -2, "/lib")
    raw_ok = tmp_path / "ok2.raw"
    dji_worker_pool.measure("ok2.jpg", str(raw_ok), 50.0, 0.9, "/lib")
    assert raw_ok.exists()


def test_persistent_enabled_false_en_x86_64(monkeypatch):
    monkeypatch.setattr(external_tools, "is_x86_64", lambda: True)
    monkeypatch.delenv("ATOM_DJI_PERSISTENT", raising=False)
    assert dji_worker_pool.persistent_enabled() is False


def test_persistent_enabled_true_en_no_x86_salvo_variable_de_entorno(monkeypatch):
    monkeypatch.setattr(external_tools, "is_x86_64", lambda: False)
    monkeypatch.delenv("ATOM_DJI_PERSISTENT", raising=False)
    assert dji_worker_pool.persistent_enabled() is True
    monkeypatch.setenv("ATOM_DJI_PERSISTENT", "0")
    assert dji_worker_pool.persistent_enabled() is False
