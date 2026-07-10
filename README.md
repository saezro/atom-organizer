# ATOM Organizer

Aplicación de escritorio **standalone** (Python 3.11 + PySide6) para organizar y procesar
imágenes de vuelos de dron (RGB + térmico) de Aerotools. Es una herramienta independiente:
**no** forma parte de Atom-suite ni toca su infraestructura.

> Versión de código base: **v2.1.5** (fuente). Los binarios distribuidos se etiquetan como **v3**.

## Qué hace

Interfaz de pestañas sobre `gui.py` / `pipeline.py`. Las principales:

- **Procesado RGB AEROTOOLS** (organizar) — reorganiza/renombra las imágenes de un vuelo según
  la nomenclatura Aerotools. **No destructivo**: copia con `shutil.copy2`, nunca toca el origen.
- **Extracción TMC** — extrae imágenes térmicas de contenedores `.TMC` (ThermoViewer).
  ⚠️ **Mueve/borra en el directorio de origen.**
- **Convertir DJI a TIFF** — convierte capturas térmicas DJI (R-JPEG) a TIFF radiométrico.
  ⚠️ **Mueve/borra en el directorio de origen.**

> **Aviso de datos:** las pestañas de Extracción TMC y Convertir DJI a TIFF **modifican el
> origen**. Para pruebas o material irreemplazable, trabajar siempre sobre una **copia** del vuelo.
> Solo *Procesado RGB AEROTOOLS* es garantizadamente no destructivo.

## Herramientas externas

La app se apoya en binarios externos (resueltos vía PATH / `external_tools.py`):

- **exiftool** (13.59) — lectura/escritura de metadatos EXIF.
- **ffmpeg** (7.0.2) — manipulación de vídeo/imágenes.
- **dji_irp** (`libdirp.so` / `dji_irp.exe`) — SDK térmico DJI para conversión radiométrica.

## Compilar

El build usa **PyInstaller 5.13.2** en modo *onedir* con `atom_organizer.spec`.

### Gotcha obligatorio: `ipaddress` (ambas plataformas)

PyInstaller con Python 3.11 **no** incluye `ipaddress` en `base_library.zip`. El bootstrap
(`urllib.parse`) lo importa en cabecera y la app peta con `ModuleNotFoundError: No module named
'ipaddress'` **antes de arrancar**. `hiddenimports` NO lo cura (va al PYZ, no al `base_library.zip`
que se carga en el bootstrap).

**Fix:** tras CADA build de PyInstaller, ejecutar `python inject_ipaddress.py` (idempotente, vale
Linux y Windows). El workflow de Windows ya lo hace; el build de AppImage también.

### Windows (.exe) — vía GitHub Actions

No requiere máquina Windows. El workflow [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml)
compila en `windows-latest`:

1. Push a `main` (o `workflow_dispatch` manual) → dispara el job.
2. Instala deps + PyInstaller 5.13.2, corre `pyinstaller --clean --noconfirm atom_organizer.spec`.
3. Inyecta `ipaddress` (`python inject_ipaddress.py`).
4. Empaqueta `dist/atom_organizer/*` en `ATOM_Organizer-v3-win-x64.zip`.
5. Sube el zip como **artifact** (retención 14 días) — descargar desde la pestaña Actions del run.

El zip incluye `atom_organizer.exe`, `base_library.zip` (con `ipaddress.pyc`), PySide6,
`dji_irp.exe` y `exiftool.exe`.

### Linux (AppImage)

Proceso manual documentado en `build-appimage/.progress.md`. Resumen:

1. venv de build con Python 3.11 y `requirements-linux.txt` (= `requirements.txt` **sin** `pywin32`
   / `pywin32-ctypes`).
2. `pyinstaller --clean --noconfirm atom_organizer.spec` → `dist/atom_organizer/` (onedir).
3. `python inject_ipaddress.py` (re-inyectar tras cada rebuild).
4. Montar el `AppDir` (binario + `ffmpeg`/`exiftool` en `usr/bin` + `AppRun` + `.desktop` + icono).
5. `appimagetool --appimage-extract-and-run` → `ATOM_Organizer-v3-x86_64.AppImage` (~137 MB).

### Degradaciones térmicas conocidas en Linux

El pipeline térmico DJI depende de binarios **solo-Windows**. En el AppImage de Linux:

- **`dji_irp` / `libdirp.so`** — el SDK radiométrico DJI solo funciona en Windows. La conversión
  DJI→TIFF radiométrico **no está disponible** en Linux.
- **`.TMC` (ThermoViewer)** — la extracción de contenedores `.TMC` depende de ThermoViewer, que es
  **solo Windows**. No disponible en Linux.

El resto de funciones (organización RGB, EXIF, ffmpeg) funcionan igual en ambas plataformas.
**Para el flujo térmico completo, usar la build de Windows.**

## Binarios

Ambos se distribuyen por Drive (folder privado de Rodrigo):

- **Windows:** `ATOM_Organizer-v3-win-x64.zip` (~92 MB)
- **Linux:** `ATOM_Organizer-v3-x86_64.AppImage` (~137 MB)
