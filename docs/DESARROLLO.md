# Desarrollo de ATOM Organizer

Aplicación de escritorio (Python 3.11 y PySide6, con interfaz web embebida) para organizar y procesar imágenes de vuelos de dron (RGB y térmicas).

## Herramientas externas

La aplicación se apoya en binarios externos, resueltos por PATH o mediante `external_tools.py`:

- **exiftool**: lectura y escritura de metadatos EXIF.
- **ffmpeg**: manipulación de vídeo e imágenes.
- **dji_irp** (`libdirp.so` / `dji_irp.exe`): SDK térmico de DJI para la conversión radiométrica.

## Compilar

El empaquetado usa **PyInstaller 5.13.2** en modo *onedir*. Los specs están en la raíz (`atom_organizer_webview.spec` para Windows y `atom_organizer_webview_linux.spec` para Linux).

### Requisito obligatorio: `ipaddress`

PyInstaller con Python 3.11 no incluye `ipaddress` en `base_library.zip`, y la aplicación falla al arrancar con `ModuleNotFoundError: No module named 'ipaddress'`. `hiddenimports` no lo soluciona.

Tras cada build de PyInstaller, ejecuta `python inject_ipaddress.py` (idempotente, válido en Linux y Windows). Los workflows de release ya lo hacen.

### Versión

La fuente única de la versión es `version.py`. El tag de git debe coincidir con ese valor, con `v` delante (versión `3.4.105`, tag `v3.4.105`). El workflow de release falla a propósito si no coinciden.

### Publicación

El workflow [`release.yml`](../.github/workflows/release.yml) compila y publica en GitHub Releases:

- Windows: `ATOM-Organizer-Setup-vX.Y.Z.exe` (instalador Inno Setup, definido en `packaging/windows/`).
- Linux: `ATOM_Organizer-vX.Y.Z-x86_64.AppImage`.

Para Linux, las dependencias están en `requirements-linux.txt` (equivale a `requirements.txt` sin `pywin32` ni `pywin32-ctypes`).

## Interfaz web

El código de la interfaz está en `webui/` (React y Vite). El build de producción se genera en `webui/dist/` y lo empaqueta PyInstaller.

## Limitaciones conocidas en Linux

- **`dji_irp` / `libdirp.so`**: el SDK radiométrico de DJI solo funciona en Windows, por lo que la conversión DJI a TIFF radiométrico no está disponible en Linux.
- **`.TMC` (ThermoViewer)**: la extracción depende de ThermoViewer, solo para Windows.

El resto de funciones (organización RGB, EXIF, ffmpeg) funcionan igual en ambas plataformas.

## Seguridad del repositorio

El repositorio es público. No se commitean credenciales, `google_client.json`, URLs internas, nombres de clientes o plantas, ni capturas con datos reales.
