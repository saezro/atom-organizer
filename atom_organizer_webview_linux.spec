# atom_organizer_webview_linux.spec — build LINUX onedir de la UI React (pywebview + Qt).
# -*- mode: python ; coding: utf-8 -*-
#
# Entry: app_webview.py. En Linux pywebview pinta con backend Qt (PySide6 + QtWebEngine,
# Chromium embebido) — ver app_webview.py:191 (`gui = "qt"`). A diferencia del build de
# Windows (WebView2, atom_organizer_webview.spec), aquí QtWebEngine SÍ se empaqueta.
#
# Onedir (no onefile) para Linux: más fácil de depurar QtWebEngine dentro del AppDir y
# de montar luego un AppImage sobre dist/atom_organizer/.
#
# Tras el build: ejecutar `python inject_ipaddress.py` (ipaddress no entra en base_library.zip).
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# pyexiv2 arrastra su binario nativo (libexiv2) — imprescindible en runtime
pyexiv2_datas, pyexiv2_binaries, pyexiv2_hidden = collect_all('pyexiv2')
# numpy/pandas 2.x/3.x reparten módulos Python y extensiones nativas fuera de
# los caminos que recogían los hooks antiguos. Se fuerzan completos igual que
# en Windows: el pipeline los importa de forma perezosa al empezar una corrida.
numpy_datas, numpy_binaries, numpy_hidden = collect_all('numpy')
pandas_datas, pandas_binaries, pandas_hidden = collect_all('pandas')
# matplotlib mpl-data (colormaps usados por el pipeline térmico)
mpl_datas = collect_data_files('matplotlib')
# openpyxl (índice Excel acumulativo): mismo patrón que pyexiv2, por si acaso
# el hook genérico se deja algo de sus submódulos.
openpyxl_datas, openpyxl_binaries, openpyxl_hidden = collect_all('openpyxl')

# --- GPU opt-in (ORGANIZER_RGB_GPU=1, ver atom_core/rgb_gpu.py): cupy +
# nvImageCodec + las libs nvidia-*-cu12 que necesitan (requirements-gpu.txt).
# TOLERANTE a propósito: si requirements-gpu.txt no está instalado en el
# entorno de build (build normal, sin GPU), find_spec da None, no se empaqueta
# nada de esto y el AppImage sigue igual que hoy — rgb_gpu.activo() ve el
# ImportError en runtime y cae a CPU. Solo se fuerza collect_all sobre lo que
# SÍ está instalado al construir.
import importlib.util as _ilu_gpu

gpu_binaries, gpu_datas, gpu_hidden = [], [], []
for _gpu_pkg in (
    'cupy', 'cupy_backends', 'cupyx', 'fastrlock', 'cuda.pathfinder',
    'nvidia.cuda_runtime', 'nvidia.cuda_nvrtc', 'nvidia.nvjpeg', 'nvidia.nvimgcodec',
):
    if _ilu_gpu.find_spec(_gpu_pkg) is None:
        continue
    try:
        _gd, _gb, _gh = collect_all(_gpu_pkg)
    except Exception:
        continue
    gpu_datas += _gd
    gpu_binaries += _gb
    gpu_hidden += _gh

a = Analysis(
    ['app_webview.py'],
    pathex=[],
    binaries=pyexiv2_binaries + numpy_binaries + pandas_binaries + openpyxl_binaries + gpu_binaries,
    datas=[
        ('webui/dist', 'webui/dist'),          # UI React buildeada (npm run build)
        ('config/Config.ini', 'config'),
        ('Logo_atom_uas_horizonta-02.png', '.'),
        ('assets', 'assets'),
        ('programas_externos', 'programas_externos'),  # DJI/ libdirp.so + deps (Linux)
    ] + pyexiv2_datas + numpy_datas + pandas_datas + mpl_datas + openpyxl_datas + gpu_datas,
    hiddenimports=[
        'pyexiv2', 'ipaddress',
        'version', 'atom_core.updater',        # updater: import perezoso desde app_webview
        'gui', 'atom_core.organize',           # import perezoso en el worker → forzarlo
        'psutil',                              # utils.workers_para_lote: import perezoso; sin él
                                               # el pool no puede capar por RAM libre
        'webview.platforms.qt',                # backend pywebview en Linux
        'qtpy', 'bottle', 'proxy_tools',       # transitivas de pywebview
        'PySide6.QtWebEngineWidgets',          # Chromium embebido (imprescindible en Linux)
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel',
        # camino GPU opt-in ORGANIZER_RGB_GPU=1 (rgb_gpu.py)
        'atom_core.rgb_gpu',
        # graphlib (stdlib): lo importa cupy._core._carray/_scalar, extensiones
        # Cython compiladas (.pyx→.so) — invisible al análisis estático de
        # PyInstaller (no mira dentro de binarios compilados). Ver comentario
        # gemelo en atom_organizer_webview.spec.
        'graphlib',
    ] + pyexiv2_hidden + numpy_hidden + pandas_hidden + openpyxl_hidden + gpu_hidden,
    hookspath=[],
    hooksconfig={},
    # pyi_rth_cuda.py (raíz del repo): en Linux solo fija CUPY_CACHE_DIR si no
    # está ya definida (no toca LD_LIBRARY_PATH). Tolerante: no falla si no
    # hay GPU ni paquetes GPU empaquetados.
    runtime_hooks=['pyi_rth_cuda.py'],
    excludes=[
        'IPython', 'ipykernel', 'jupyter_client', 'jupyter_core',
        'debugpy', 'jedi', 'parso',
        'clr', 'pythonnet',                    # pythonnet/WebView2: solo Windows
        # pesados que el pipeline no usa (QtWebEngine SÍ se mantiene)
        'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtMultimedia',
        'PySide6.QtQuick3D', 'PySide6.QtDataVisualization',
        # atom_core.almacen_gcs importa 'google.cloud.storage' de forma perezosa
        # (dentro de AlmacenGCS.__init__, no a nivel de módulo): SOLO lo trae la
        # imagen del Cloud Run Job. El escritorio no lo necesita (sube vía
        # atom_core.cloud_upload, solo stdlib) — excluido explícito para que no
        # se cuele en el .exe aunque el entorno de build lo tenga instalado.
        'google.cloud.storage', 'google.cloud',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ATOM-Organizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # app de ventana
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='atom_organizer',
)
