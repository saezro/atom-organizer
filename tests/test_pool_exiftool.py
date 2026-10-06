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


def test_primer_comando_cuelga_no_es_fatal(fake):
    p = _pool(fake, procesos=1, timeout=0.5)
    with pytest.raises(RuntimeError, match="no contestó") as ei:
        p.copiar("a", "CUELGA.tiff")
    assert not isinstance(ei.value, apply.ExiftoolNoDisponible)
    assert p.fatal is None
    p.copiar("a", "ok.tiff")
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
