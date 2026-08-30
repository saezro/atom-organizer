"""Inverso de `tests/test_escritorio_sin_sdk.py`.

Aquel fija que el ESCRITORIO vive sin `google-cloud-storage`; este fija que la
imagen del Cloud Run Job SÍ lo lleva. Sin esta comprobación el bug de 3790 F5
pasa desapercibido: `Dockerfile.job` no instalaba el SDK, así que la primera
ruta `gs://` reventaba con `ImportError` en `abrir_almacen()` y las 16 tasks
morían con exit 2 antes de tocar un solo fichero.

No se puede afirmar por `import` (la suite corre en el host, que a propósito no
tiene el SDK): se afirma sobre el Dockerfile, que es lo que construye la imagen.
Y se afirma también lo contrario en `requirements-server.txt`, compartido con la
Raspberry Pi y con el bundle PyInstaller — si alguien "arregla" el bug metiendo
el SDK ahí, este test lo caza.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# `google_cloud_storage`, `google-cloud-storage`, may/minúsculas: pip los
# normaliza al mismo paquete, así que el test tiene que verlos todos.
_SDK = re.compile(r"google[-_]cloud[-_]storage", re.IGNORECASE)


def _lineas_utiles(texto: str) -> list[str]:
    """Líneas sin comentarios ni vacías: los comentarios de estos ficheros
    MENCIONAN el SDK a propósito (explican por qué está o por qué no), y no
    deben contar como instalación."""
    fuera = []
    for linea in texto.splitlines():
        limpia = linea.split("#", 1)[0].strip()
        if limpia:
            fuera.append(limpia)
    return fuera


def test_dockerfile_job_instala_el_sdk_de_gcs():
    """La imagen del Job es el ÚNICO entorno que habla `gs://` (AlmacenGCS):
    tiene que traer el SDK instalado y con versión fijada."""
    dockerfile = REPO_ROOT / "Dockerfile.job"
    assert dockerfile.is_file(), "falta Dockerfile.job"

    instala = [l for l in _lineas_utiles(dockerfile.read_text(encoding="utf-8"))
               if l.startswith("RUN") and "pip install" in l and _SDK.search(l)]
    assert instala, (
        "Dockerfile.job no instala google-cloud-storage: la imagen del Job no "
        "podrá abrir rutas gs:// y organize_cli abortará con ImportError."
    )
    assert any("==" in l for l in instala), (
        f"google-cloud-storage sin versión fijada en Dockerfile.job: {instala}"
    )


def test_requirements_compartidos_no_traen_el_sdk():
    """`requirements-server.txt` lo comparten Job, Raspberry Pi y (vía
    requirements-linux) el bundle PyInstaller del escritorio. El SDK NO puede
    entrar ahí: engordaría el bundle y rompería el contrato que vigila
    `tests/test_escritorio_sin_sdk.py`."""
    for nombre in ("requirements-server.txt", "requirements.txt", "requirements-linux.txt"):
        fichero = REPO_ROOT / nombre
        if not fichero.is_file():
            continue
        culpables = [l for l in _lineas_utiles(fichero.read_text(encoding="utf-8"))
                     if _SDK.search(l)]
        assert not culpables, (
            f"{nombre} declara google-cloud-storage ({culpables}): ese fichero "
            "llega al escritorio/Pi. El SDK va SOLO en Dockerfile.job."
        )
