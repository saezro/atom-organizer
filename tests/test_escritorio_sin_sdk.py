"""El escritorio (`gui.py`/`app_webview.py`) tiene que vivir SIN `google-cloud-
storage` instalado: solo la imagen del Cloud Run Job trae ese SDK (ver
docstring de `atom_core/almacen_gcs.py` y de `atom_core/cloud_upload.py`). Este
fichero fija dos cosas:

1. Que el paquete entero (`pipeline`, `utils`, `atom_core.phases`,
   `atom_core.organize`, `atom_core.almacen`, `atom_core.almacen_gcs`...) sigue
   siendo IMPORTABLE, y que la ruta LOCAL de `atom_core.almacen` funciona de
   punta a punta, con `google-cloud-storage` AUSENTE de verdad -no solo "no
   instalado en este venv por casualidad": se bloquea con un meta path finder
   que hace fallar cualquier `import google...`, para que el test siga
   protegiendo esto aunque algún día ese venv sí lo tenga.
2. Que con una ruta LOCAL no se construye NINGÚN cliente/objeto `AlmacenGCS`:
   la rama `gs://` no se toca para nada cuando el pipeline corre en el
   escritorio de un cliente sobre su propio disco.

Ambas comprobaciones corren en un subproceso limpio (`sys.executable`, mismo
venv que pytest): así el bloqueo de `google...` y el parcheo de `AlmacenGCS`
no dependen de qué haya quedado ya cacheado en `sys.modules` por el resto de
la suite, que sí puede haber importado `atom_core.almacen_gcs` antes.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _correr_script(codigo: str) -> subprocess.CompletedProcess:
    """Ejecuta `codigo` en un intérprete `python -c` nuevo, con el repo como
    cwd (para que `import pipeline`/`atom_core...` resuelva igual que en la
    suite), y devuelve el proceso terminado (sin lanzar por código de salida:
    el test decide cómo afirmar sobre stdout/stderr/returncode)."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(codigo)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


# Preludio común: bloquea de verdad cualquier `import google` / `import
# google.cloud` / `import google.cloud.storage`, ANTES de importar nada del
# proyecto. Se instala como `sys.meta_path` finder (no `sys.modules[...] =
# None`, que solo bloquea el import EXACTO de ese nombre y no sus submódulos)
# para que ni siquiera un `from google.cloud import storage` indirecto cuele.
_BLOQUEO_GOOGLE = """
import sys

class _BloqueaGoogleCloudStorage:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "google" or fullname.startswith("google."):
            raise ImportError(
                f"'{fullname}' bloqueado a propósito: el escritorio no lleva "
                "google-cloud-storage instalado."
            )
        return None

sys.meta_path.insert(0, _BloqueaGoogleCloudStorage())
"""


def test_paquete_importable_y_ruta_local_funciona_sin_google_cloud_storage(tmp_path):
    """Con `google...` bloqueado a nivel de import, el paquete entero sigue
    siendo importable y un ciclo completo publicar/listar/leer/existir sobre
    una ruta LOCAL de `atom_core.almacen` funciona de punta a punta."""
    raiz = tmp_path / "escritorio_local"
    raiz.mkdir()
    origen = tmp_path / "origen.txt"
    origen.write_text("contenido local")

    resultado = _correr_script(_BLOQUEO_GOOGLE + f"""
import pipeline  # noqa: F401  (arrastra atom_core.almacen, utils, sharding...)
import utils  # noqa: F401
import atom_core.phases  # noqa: F401
import atom_core.organize  # noqa: F401
import atom_core.almacen as almacen
import atom_core.almacen_gcs  # noqa: F401  (el MÓDULO se importa sin problema: el
                               # SDK solo se toca dentro de AlmacenGCS.__init__)

raiz = {str(raiz)!r}
origen = {str(origen)!r}

almacen.publicar_en(origen, almacen.unir(raiz, "destino.txt"))
assert almacen.existe_ruta(almacen.unir(raiz, "destino.txt"))
assert almacen.listar_ficheros(raiz) == ["destino.txt"]
assert almacen.tamano_de(almacen.unir(raiz, "destino.txt")) == len(b"contenido local")
with almacen.abrir_para_lectura(almacen.unir(raiz, "destino.txt")) as ruta:
    assert ruta.read_text() == "contenido local"

print("OK")
""")

    assert resultado.returncode == 0, (
        f"stdout={resultado.stdout!r}\\nstderr={resultado.stderr!r}"
    )
    assert "OK" in resultado.stdout
    # Control negativo: el bloqueo es real (si esto no lanzara, el resto del
    # test no probaría nada -podría estar pasando porque el venv sí trae el
    # SDK y el bloqueo no hizo nada).
    resultado_control = _correr_script(_BLOQUEO_GOOGLE + """
import google.cloud.storage  # noqa: F401
""")
    assert resultado_control.returncode != 0
    assert "bloqueado a propósito" in resultado_control.stderr


def test_ruta_local_no_construye_ningun_cliente_gcs(tmp_path):
    """Con una ruta LOCAL, `atom_core.almacen_gcs.AlmacenGCS` no se instancia
    NUNCA: se parchea su `__init__` para reventar si se llama, y se ejercitan
    las funciones URI-aware más usadas por el pipeline sobre disco."""
    raiz = tmp_path / "escritorio_local"
    raiz.mkdir()
    (raiz / "sub").mkdir()
    origen = tmp_path / "origen.txt"
    origen.write_text("x")

    resultado = _correr_script(_BLOQUEO_GOOGLE + f"""
import atom_core.almacen as almacen
import atom_core.almacen_gcs as almacen_gcs

def _no_deberia_llamarse(self, *a, **k):
    raise AssertionError("AlmacenGCS se construyo para una ruta LOCAL")

almacen_gcs.AlmacenGCS.__init__ = _no_deberia_llamarse

raiz = {str(raiz)!r}
origen = {str(origen)!r}

almacen.publicar_en(origen, almacen.unir(raiz, "a.txt"))
almacen.listar_ficheros(raiz)
almacen.listar_subcarpetas(raiz)
almacen.existe_ruta(almacen.unir(raiz, "a.txt"))
almacen.tamano_de(almacen.unir(raiz, "a.txt"))
with almacen.abrir_para_lectura(almacen.unir(raiz, "a.txt")) as _ruta:
    pass

# Y el propio `Almacen` local vía `abrir_almacen`, sin URI:
alm, prefijo = almacen.abrir_almacen(raiz)
assert isinstance(alm, almacen.AlmacenLocal)
assert prefijo == ""

print("OK")
""")

    assert resultado.returncode == 0, (
        f"stdout={resultado.stdout!r}\\nstderr={resultado.stderr!r}"
    )
    assert "OK" in resultado.stdout
    assert "AlmacenGCS se construyo" not in resultado.stdout + resultado.stderr
