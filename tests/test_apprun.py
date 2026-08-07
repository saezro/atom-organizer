"""El AppRun del AppImage, ejecutado de verdad.

Es un script de shell y no lo cubre ningún test de Python, pero decide si la app
de Linux arranca y si el login funciona. Aquí se corre con un `exec` postizo que
solo imprime el entorno con el que habría arrancado la app.

Los dos fallos que cubre, vistos en v3.4.21 sobre CachyOS:

* sin `SSL_CERT_FILE`, todo HTTPS moría con «unable to get local issuer
  certificate» — el OpenSSL del bundle busca las CA donde las pone Ubuntu;
* la `libxkbcommon` del bundle acababa en SIGSEGV contra los datos de
  xkeyboard-config del sistema, con la app ya en marcha.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

APPRUN = Path(__file__).resolve().parent.parent / "packaging" / "AppRun"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="el AppRun solo corre en Linux"
)


@pytest.fixture
def correr(tmp_path):
    """Ejecuta el AppRun en un AppDir de mentira y devuelve su entorno final."""
    lib = tmp_path / "usr" / "lib" / "atom_organizer"
    lib.mkdir(parents=True)
    # El "ejecutable" de la app: en vez de arrancar Qt, vuelca el entorno.
    binario = lib / "ATOM-Organizer"
    binario.write_text("#!/bin/sh\nenv\n")
    binario.chmod(0o755)
    destino = tmp_path / "AppRun"
    destino.write_text(APPRUN.read_text())
    destino.chmod(0o755)

    def _correr(**entorno):
        base = {k: v for k, v in os.environ.items()
                if k in ("PATH", "HOME", "LANG")}
        base.update(entorno)
        salida = subprocess.run([str(destino)], env=base, capture_output=True,
                                text=True, timeout=30)
        assert salida.returncode == 0, salida.stderr
        return dict(
            linea.split("=", 1) for linea in salida.stdout.splitlines() if "=" in linea
        )

    return _correr


def test_le_dice_a_openssl_donde_estan_las_ca_del_sistema(correr):
    env = correr()
    ca = env.get("SSL_CERT_FILE")
    assert ca, "sin esto, el login de Google falla por certificado en Arch/Fedora"
    assert Path(ca).is_file()
    assert env.get("REQUESTS_CA_BUNDLE") == ca


def test_no_pisa_los_certificados_que_haya_elegido_el_usuario(correr):
    env = correr(SSL_CERT_FILE="/opt/mi-empresa/ca.pem")
    assert env["SSL_CERT_FILE"] == "/opt/mi-empresa/ca.pem"


def test_precarga_la_libxkbcommon_del_sistema(correr):
    """La librería tiene que ir a la par con los datos de xkeyboard-config, y los
    datos son del sistema."""
    env = correr()
    preload = env.get("LD_PRELOAD", "")
    assert "libxkbcommon.so.0" in preload, preload
    assert not preload.startswith("/tmp/.mount"), "esa es la del bundle, la que casca"


def test_conserva_lo_que_ya_hubiera_en_LD_PRELOAD(correr):
    env = correr(LD_PRELOAD="/opt/otra.so")
    assert env["LD_PRELOAD"].endswith("/opt/otra.so")


def test_sigue_desactivando_el_sandbox_de_qtwebengine(correr):
    """Dentro de un AppImage no hay sandbox setuid: sin esto no arranca la UI."""
    env = correr()
    assert env["QTWEBENGINE_DISABLE_SANDBOX"] == "1"
    assert "--no-sandbox" in env["QTWEBENGINE_CHROMIUM_FLAGS"]


def test_no_deja_sueltas_las_variables_de_trabajo(correr):
    """`_ca` y `_libdir` son del script; que salgan al entorno de la app es basura."""
    env = correr()
    assert "_ca" not in env
    assert "_libdir" not in env
