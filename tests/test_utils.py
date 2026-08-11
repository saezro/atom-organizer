import errno
import os
import shutil
import sys
import subprocess
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "general_functions"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import utils
from utils import (
    ExternalToolError,
    safe_pct,
    unique_dest,
    safe_move,
    safe_copy2,
    run_external,
)


def test_safe_pct_total_cero():
    assert safe_pct(0, 0) == 0

def test_safe_pct_normal():
    assert safe_pct(1, 4) == 25

def test_safe_pct_recortado_a_100():
    assert safe_pct(5, 4) == 100

def test_unique_dest_sin_colision(tmp_path):
    dest = tmp_path / "foto.jpg"
    assert unique_dest(str(dest)) == str(dest)

def test_unique_dest_con_colision(tmp_path):
    dest = tmp_path / "foto.jpg"
    dest.write_text("existente")
    resultado = unique_dest(str(dest))
    assert resultado == str(tmp_path / "foto_1.jpg")

def test_safe_move_mueve_archivo(tmp_path):
    src = tmp_path / "origen.jpg"
    src.write_text("contenido")
    dest = tmp_path / "destino.jpg"
    resultado = safe_move(str(src), str(dest))
    assert resultado == str(dest)
    assert not src.exists()
    assert dest.read_text() == "contenido"

def test_safe_move_no_sobreescribe_destino_existente(tmp_path):
    src = tmp_path / "origen.jpg"
    src.write_text("nuevo")
    dest = tmp_path / "destino.jpg"
    dest.write_text("original")
    resultado = safe_move(str(src), str(dest))
    assert resultado == str(tmp_path / "destino_1.jpg")
    assert dest.read_text() == "original"
    assert (tmp_path / "destino_1.jpg").read_text() == "nuevo"
    assert not src.exists()

def test_run_external_returncode_no_cero_lanza_error():
    with pytest.raises(ExternalToolError):
        run_external([sys.executable, "-c", "import sys;sys.exit(3)"])

def test_run_external_ok_devuelve_completed_process():
    resultado = run_external([sys.executable, "-c", "print(1)"])
    assert isinstance(resultado, subprocess.CompletedProcess)
    assert resultado.returncode == 0

def test_run_external_timeout_lanza_error():
    with pytest.raises(ExternalToolError):
        run_external(
            [sys.executable, "-c", "import time;time.sleep(5)"],
            timeout=0.2,
        )


def test_safe_move_modo_sobrescribir_pisa_el_destino(tmp_path):
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"nuevo")
    destino = caso / "destino.jpg"
    destino.write_bytes(b"viejo")

    final = utils.safe_move(str(origen), str(destino), modo=utils.MODO_SOBRESCRIBIR)

    assert final == str(destino)
    assert destino.read_bytes() == b"nuevo"
    assert not origen.exists()
    # Lo que de verdad se comprueba: NO ha nacido ningun `destino_1.jpg`.
    assert sorted(p.name for p in caso.iterdir()) == ["destino.jpg"]


def test_safe_move_modo_obviar_no_toca_nada_si_el_destino_existe(tmp_path):
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"nuevo")
    destino = caso / "destino.jpg"
    destino.write_bytes(b"viejo")

    final = utils.safe_move(str(origen), str(destino), modo=utils.MODO_OBVIAR)

    assert final is None
    assert destino.read_bytes() == b"viejo"
    assert origen.exists(), "en modo obviar el origen NO se consume"
    assert sorted(p.name for p in caso.iterdir()) == ["destino.jpg", "origen.jpg"]


def test_safe_move_modo_obviar_mueve_si_el_destino_no_existe(tmp_path):
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"nuevo")
    destino = caso / "destino.jpg"

    final = utils.safe_move(str(origen), str(destino), modo=utils.MODO_OBVIAR)

    assert final == str(destino)
    assert destino.read_bytes() == b"nuevo"
    assert not origen.exists()


def test_safe_move_modo_sobrescribir_falla_si_el_destino_es_un_directorio(tmp_path):
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"nuevo")
    destino = caso / "destino.jpg"
    destino.mkdir()

    with pytest.raises(OSError, match="directorio"):
        utils.safe_move(str(origen), str(destino), modo=utils.MODO_SOBRESCRIBIR)

    assert origen.exists(), "el origen no se consume si el destino es inservible"
    assert destino.is_dir()
    assert list(destino.iterdir()) == [], "el fichero NO puede acabar dentro del directorio"


def test_safe_move_modo_sobrescribir_mismo_fs(tmp_path):
    # Camino corto: origen y destino en el mismo filesystem, `os.replace` de una.
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"nuevo")
    destino = caso / "destino.jpg"
    destino.write_bytes(b"viejo")

    utils.safe_move(str(origen), str(destino), modo=utils.MODO_SOBRESCRIBIR)

    assert sorted(p.name for p in caso.iterdir()) == ["destino.jpg"]
    assert destino.read_bytes() == b"nuevo"


def _replace_que_falla_cross_device(veces, real=None):
    """`os.replace` falso que simula EXDEV las primeras `veces` llamadas.

    En el entorno de test origen y destino caen siempre en el mismo filesystem,
    asi que la rama cross-device (la unica que crea el temporal) no se ejerce
    nunca sola. Forzarla es la unica forma de testear de verdad la limpieza.
    """
    estado = {"n": 0}

    def fake(src, dst, *a, **kw):
        estado["n"] += 1
        if estado["n"] <= veces:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst, *a, **kw)

    return fake


def test_safe_move_sobrescribir_cross_device_publica_y_limpia(tmp_path, monkeypatch):
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"nuevo")
    destino = caso / "destino.jpg"
    destino.write_bytes(b"viejo")

    # Solo la primera llamada (src -> destino) finge ser cross-device; la
    # segunda (temporal -> destino) es la real, que es lo que publica.
    monkeypatch.setattr(os, "replace", _replace_que_falla_cross_device(1, real=os.replace))

    resultado = utils.safe_move(str(origen), str(destino), modo=utils.MODO_SOBRESCRIBIR)

    assert resultado == str(destino)
    assert destino.read_bytes() == b"nuevo"
    assert not origen.exists()
    assert sorted(p.name for p in caso.iterdir()) == ["destino.jpg"]


def test_safe_move_sobrescribir_cross_device_fallo_no_deja_temporales(tmp_path, monkeypatch):
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"nuevo")
    destino = caso / "destino.jpg"
    destino.write_bytes(b"viejo")

    # Ninguna llamada funciona: el copy2 al temporal si ocurre, pero la
    # publicacion falla. El temporal NO puede quedarse ahi tirado.
    monkeypatch.setattr(os, "replace", _replace_que_falla_cross_device(99))

    with pytest.raises(OSError):
        utils.safe_move(str(origen), str(destino), modo=utils.MODO_SOBRESCRIBIR)

    assert [p.name for p in caso.iterdir() if ".atom-parcial" in p.name] == []
    assert origen.exists(), "el origen no se toca si la publicacion fallo"
    assert destino.read_bytes() == b"viejo", "el destino conserva lo viejo"


def test_safe_move_temporal_lleva_sufijo_unico(tmp_path, monkeypatch):
    # Dos shards que caen a la vez en la rama cross-device con el mismo destino
    # no pueden compartir el nombre del temporal.
    caso = tmp_path / "caso"
    caso.mkdir()
    vistos = []

    copy2_real = shutil.copy2  # antes del parche: `utils.shutil` ES el modulo global

    def copy2_espia(src, dst, *a, **kw):
        vistos.append(os.path.basename(dst))
        return copy2_real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", _replace_que_falla_cross_device(1, real=os.replace))
    monkeypatch.setattr(utils.shutil, "copy2", copy2_espia)

    for i in range(2):
        origen = caso / f"origen{i}.jpg"
        origen.write_bytes(b"nuevo")
        destino = caso / "destino.jpg"
        utils.safe_move(str(origen), str(destino), modo=utils.MODO_SOBRESCRIBIR)
        monkeypatch.setattr(os, "replace", _replace_que_falla_cross_device(1, real=os.replace))

    assert len(vistos) == 2
    assert vistos[0] != vistos[1], f"nombre de temporal repetido: {vistos}"


def test_safe_move_modo_desconocido_falla_pronto(tmp_path):
    caso = tmp_path / "caso"
    caso.mkdir()
    origen = caso / "origen.jpg"
    origen.write_bytes(b"x")
    with pytest.raises(ValueError):
        utils.safe_move(str(origen), str(caso / "destino.jpg"), modo="loquesea")
    assert origen.exists(), "un modo invalido no debe consumir el origen"
