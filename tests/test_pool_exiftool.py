"""`apply._PoolExiftool` contra un exiftool FALSO (script Python que habla el
protocolo `-stay_open True -@ -` por stdin/stdout) con modos: ok, unchanged,
error, hang, die."""
import os
import sys
import threading

import pytest

from atom_core import apply

FAKE = r'''
import sys
modo = open(sys.argv[0] + ".modo").read().strip()
if modo == "die":
    sys.exit(1)
args = []
for linea in sys.stdin:
    linea = linea.rstrip("\n")
    if linea == "-execute":
        if "False" in args:
            break
        dst = args[-1]
        if "FALLA" in dst:
            print("Error: File not found - " + dst); print("    0 image files updated")
            print("    1 files weren't updated due to errors")
        elif "CUELGA" in dst:
            import time; time.sleep(60)
        elif "UNCHANGED" in dst:
            print("    1 image files unchanged")
        else:
            print("    1 image files updated")
        sys.stdout.write("{ready}\n"); sys.stdout.flush()
        args = []
    else:
        args.append(linea)
'''


@pytest.fixture
def fake(tmp_path):
    script = tmp_path / "fake_exiftool.py"
    script.write_text(FAKE)
    (tmp_path / "fake_exiftool.py.modo").write_text("ok")
    return [sys.executable, str(script)]


def _pool(fake, procesos=1, timeout=3.0):
    return apply._PoolExiftool("x", procesos, timeout=timeout, comando=fake)


def test_ok_y_unchanged_son_exito(fake):
    p = _pool(fake)
    p.copiar("a", "ok.tiff")
    p.copiar("a", "UNCHANGED.tiff")
    p.cerrar()


def test_error_es_fallo_y_no_deadlock_con_max_1(fake):
    """Tras un fallo (proceso descartado) con max=1 el siguiente copiar crea
    otro proceso en vez de colgarse."""
    p = _pool(fake, procesos=1)
    p.copiar("a", "ok.tiff")
    with pytest.raises(RuntimeError, match="weren't updated|Error"):
        p.copiar("a", "FALLA.tiff")
    hecho = []
    t = threading.Thread(target=lambda: (p.copiar("a", "ok2.tiff"), hecho.append(1)))
    t.start(); t.join(10)
    assert hecho == [1]
    p.cerrar()


def test_timeout_mata_y_falla_la_fila(fake):
    p = _pool(fake, procesos=1, timeout=0.5)
    p.copiar("a", "ok.tiff")
    with pytest.raises(RuntimeError, match="no contestó"):
        p.copiar("a", "CUELGA.tiff")
    p.copiar("a", "ok2.tiff")  # se recupera
    p.cerrar()


def test_no_supera_max_procesos(fake):
    p = _pool(fake, procesos=2)
    errores = []

    def trabajo(i):
        try:
            for j in range(5):
                p.copiar("a", f"ok{i}_{j}.tiff")
        except Exception as e:  # pragma: no cover
            errores.append(e)

    hilos = [threading.Thread(target=trabajo, args=(i,)) for i in range(8)]
    for h in hilos: h.start()
    for h in hilos: h.join(30)
    assert errores == []
    assert len(p._todos) <= 2
    p.cerrar()


def test_exiftool_que_no_arranca_es_fallo_masivo(tmp_path, fake):
    (tmp_path / "fake_exiftool.py.modo").write_text("die")
    p = _pool(fake)
    with pytest.raises(apply.ExiftoolNoDisponible):
        p.copiar("a", "ok.tiff")
    assert p.fatal
    with pytest.raises(apply.ExiftoolNoDisponible):
        p.copiar("a", "ok.tiff")
    p.cerrar()


def test_ejecutable_inexistente_es_fallo_masivo():
    p = apply._PoolExiftool("x", 1, comando=["/no/existe/exiftool"])
    with pytest.raises(apply.ExiftoolNoDisponible):
        p.copiar("a", "b")


def test_primer_comando_cuelga_es_arranque_fallido_fatal(fake):
    """Un proceso que nunca contestó y se cuelga cuenta como arranque fallido."""
    p = _pool(fake, procesos=1, timeout=0.3)
    p._esperas_arranque = (0.01, 0.01, 0.01)
    with pytest.raises(apply.ExiftoolNoDisponible, match="timeout"):
        p.copiar("a", "CUELGA.tiff")
    assert p.fatal
    p.cerrar()


def test_proceso_muerto_se_descarta_y_no_vuelve_a_libres(fake):
    p = _pool(fake, procesos=1)
    p.copiar("a", "ok.tiff")
    viejo = p._libres.get_nowait()
    viejo.kill(); viejo.wait()
    p._libres.put(viejo)
    with pytest.raises(RuntimeError):  # el muerto da EOF: la fila falla
        p.copiar("a", "ok2.tiff")
    assert p.fatal is None
    assert viejo not in p._todos and p._libres.empty()
    p.copiar("a", "ok3.tiff")  # proceso nuevo
    p.cerrar()


# --- Arranque robusto (reintentos, diagnóstico, calentamiento) ---

def _wrap_popen(monkeypatch, pool, fake_modos):
    """Sustituye Popen: el modo del n-ésimo lanzamiento sale de `fake_modos`
    (último se repite). Registra si el pool ya había contestado en cada lanzamiento."""
    real = apply.subprocess.Popen
    lanzados = []
    modo_path = fake_modos["modo_path"]

    def popen(argv, **kw):
        n = len(lanzados)
        modos = fake_modos["modos"]
        modo_path.write_text(modos[min(n, len(modos) - 1)])
        lanzados.append(pool._alguna_vez_ok)
        proc = real(argv, **kw)
        if n < len(modos):
            proc.stdin  # el script lee el modo al arrancar; damos tiempo a leerlo
            import time as _t; _t.sleep(0.3)
        return proc

    monkeypatch.setattr(apply.subprocess, "Popen", popen)
    return lanzados


def test_primer_arranque_muere_y_el_segundo_va(tmp_path, fake, monkeypatch):
    p = _pool(fake)
    p._esperas_arranque = (0.01, 0.01, 0.01)
    lanzados = _wrap_popen(monkeypatch, p, {"modo_path": tmp_path / "fake_exiftool.py.modo",
                                             "modos": ["die", "ok"]})
    p.copiar("a", "ok.tiff")
    assert p.fatal is None and len(lanzados) == 2
    p.cerrar()


def test_siempre_muere_da_no_disponible_con_exit_code(tmp_path, fake):
    (tmp_path / "fake_exiftool.py.modo").write_text("die")
    p = _pool(fake)
    p._esperas_arranque = (0.01, 0.01, 0.01)
    with pytest.raises(apply.ExiftoolNoDisponible, match="exit code 1"):
        p.copiar("a", "ok.tiff")
    assert "exit code 1" in p.fatal
    p.cerrar()


def test_calentamiento_lanza_un_proceso_antes_que_el_resto(fake, monkeypatch):
    p = _pool(fake, procesos=4)
    real = apply.subprocess.Popen
    lanzados = []

    def popen(argv, **kw):
        lanzados.append(p._alguna_vez_ok)
        return real(argv, **kw)

    monkeypatch.setattr(apply.subprocess, "Popen", popen)
    hilos = [threading.Thread(target=lambda i=i: p.copiar("a", f"ok{i}.tiff")) for i in range(8)]
    for h in hilos: h.start()
    for h in hilos: h.join(30)
    assert lanzados[0] is False
    assert all(lanzados[1:])  # el resto solo tras contestar el primero
    p.cerrar()


def test_proceso_nuevo_muere_al_arrancar_con_pool_sano_se_reintenta(tmp_path, fake, monkeypatch):
    p = _pool(fake, procesos=2)
    p._esperas_arranque = (0.01, 0.01, 0.01)
    p.copiar("a", "ok.tiff")  # pool sano: un proceso contestó
    assert p._alguna_vez_ok
    viejo = p._libres.get()  # fuerza que la siguiente copia necesite un proceso NUEVO
    p._descartar(viejo)
    real = apply.subprocess.Popen
    lanzados = []

    def popen(argv, **kw):
        (tmp_path / "fake_exiftool.py.modo").write_text("die" if not lanzados else "ok")
        lanzados.append(1)
        proc = real(argv, **kw)
        import time as _t; _t.sleep(0.3)
        return proc

    monkeypatch.setattr(apply.subprocess, "Popen", popen)
    p.copiar("a", "ok2.tiff")  # no debe lanzar
    assert len(lanzados) == 2 and p.fatal is None
    p.cerrar()


# --- Fallos fatales vs por fila ---

def test_binario_ausente_es_fatal_inmediato(monkeypatch):
    p = _pool(["x"])
    p._esperas_arranque = (5.0, 5.0, 5.0)  # si reintentara, el test tardaría

    def popen(argv, **kw):
        raise OSError("no such file")

    monkeypatch.setattr(apply.subprocess, "Popen", popen)
    import time as _t
    t0 = _t.monotonic()
    with pytest.raises(apply.ExiftoolNoDisponible, match="No se pudo arrancar"):
        p.copiar("a", "ok.tiff")
    assert _t.monotonic() - t0 < 2
    assert p.fatal


def test_proceso_nuevo_muere_siempre_con_pool_sano_solo_falla_la_fila(tmp_path, fake, monkeypatch):
    p = _pool(fake, procesos=2)
    p._esperas_arranque = (0.01, 0.01, 0.01)
    p.copiar("a", "ok.tiff")
    p._descartar(p._libres.get())  # la siguiente copia necesita proceso nuevo
    real = apply.subprocess.Popen
    modo = tmp_path / "fake_exiftool.py.modo"
    modo.write_text("die")

    def popen(argv, **kw):
        proc = real(argv, **kw)
        import time as _t; _t.sleep(0.3)
        return proc

    monkeypatch.setattr(apply.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError) as ei:
        p.copiar("a", "ok2.tiff")
    assert not isinstance(ei.value, apply.ExiftoolNoDisponible)
    assert p.fatal is None
    modo.write_text("ok")
    p.copiar("a", "ok3.tiff")  # las filas posteriores siguen
    p.cerrar()


def test_cuelgue_en_calentamiento_es_fatal_acotado(tmp_path, fake):
    # Fake que no responde jamas: duerme sin leer ni contestar.
    (tmp_path / "fake_exiftool.py").write_text("import time; time.sleep(60)\n")
    p = _pool(fake, procesos=4, timeout=0.3)
    p._esperas_arranque = (0.01, 0.01, 0.01)
    import time as _t
    t0 = _t.monotonic()
    resultados = []

    def trabajo():
        try:
            p.copiar("a", "ok.tiff")
        except Exception as e:
            resultados.append(e)

    hilos = [threading.Thread(target=trabajo) for _ in range(8)]
    for h in hilos: h.start()
    for h in hilos: h.join(30)
    assert _t.monotonic() - t0 < 10  # muy lejos de 8 x 4 x timeout real
    assert len(resultados) == 8
    assert all(isinstance(e, apply.ExiftoolNoDisponible) for e in resultados)
    assert "timeout" in p.fatal
    p.cerrar()


def test_mensaje_fatal_recorta_stderr_a_500(tmp_path, fake):
    script = tmp_path / "fake_exiftool.py"
    script.write_text("import sys\nsys.stderr.write('E' * 2000 + 'FINAL')\nsys.stderr.flush()\nsys.exit(3)\n")
    p = _pool(fake)
    p._esperas_arranque = (0.01, 0.01, 0.01)
    with pytest.raises(apply.ExiftoolNoDisponible, match="exit code 3"):
        p.copiar("a", "ok.tiff")
    assert "FINAL" in p.fatal
    cola = p.fatal.split("salida: ", 1)[1]
    assert len(cola) <= 500
    assert "E" * 400 in cola and "E" * 2000 not in p.fatal
    p.cerrar()
