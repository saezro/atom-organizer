# atom_organizer_webview.spec — build Windows ONEFILE de la UI React (pywebview + Qt).
# -*- mode: python ; coding: utf-8 -*-
#
# Entry: app_webview.py. Produce dist/ATOM-Organizer.exe (portable, un click).
# SOLO Windows: PyInstaller no cross-compila.
#
# Notas de diseño:
#  - Desde v3.9 Windows pinta con el backend Qt de pywebview (PySide6 + QtWebEngine,
#    Chromium embebido), IGUAL que Linux (ver app_webview.py: `webview.start(gui="qt")`).
#    Se abandonó WebView2 (v3.8.x): con ese backend `window.pywebview` no se inyectaba y
#    el bridge JS↔Python quedaba muerto. QtWebEngine SÍ se empaqueta aquí (antes se excluía).
#  - El pipeline importa gui.py (`from gui import MainWindow` en atom_core/organize.py) y
#    gui.py importa PySide6.{QtWidgets,QtCore,QtGui} a nivel módulo → PySide6 core imprescindible.
#    Se excluye solo lo pesado que nadie usa (Qt3D, Charts, Multimedia, Quick3D, DataVisualization).
#  - La UI React va como data en 'webui/dist'; app_webview.py la resuelve vía _MEIPASS.
#  - Recursos del pipeline (config, programas_externos, assets) los resuelve external_tools.app_base_dir.
#  - programas_externos/{exiftool.exe, M2EA/dji_irp.exe, M4T/dji_irp.exe} son binarios Windows: imprescindibles.
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# pyexiv2 arrastra su binario nativo (libexiv2 / .pyd) — imprescindible en runtime
pyexiv2_datas, pyexiv2_binaries, pyexiv2_hidden = collect_all('pyexiv2')
# matplotlib mpl-data (colormaps usados por el pipeline)
mpl_datas = collect_data_files('matplotlib')

# Runtime de Visual C++ (2015-2022). QtWebEngineProcess.exe (subproceso Chromium de
# QtWebEngine) enlaza contra msvcp140/vcruntime140; WebView2 no lo necesitaba porque
# Edge ya trae el redist en el sistema, pero QtWebEngine SÍ. En una máquina sin el
# VC++ Redistributable instalado el subproceso muere con "MSVCP140.dll was not found"
# (confirmado en VM limpia). Se bundlean explícitamente para que el .exe sea portable
# de verdad, colocadas junto a QtWebEngineProcess.exe (PySide6/) y en la raíz _MEIPASS.
import os as _os
_sys32 = _os.path.join(_os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
_vc_dlls = ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
            'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll')
vcruntime_binaries = []
for _d in _vc_dlls:
    _p = _os.path.join(_sys32, _d)
    if _os.path.exists(_p):
        vcruntime_binaries.append((_p, '.'))
        vcruntime_binaries.append((_p, 'PySide6'))

a = Analysis(
    ['app_webview.py'],
    pathex=[],
    binaries=pyexiv2_binaries + vcruntime_binaries,
    datas=[
        ('webui/dist', 'webui/dist'),          # UI React buildeada (npm run build)
        ('config/Config.ini', 'config'),
        ('Logo_atom_uas_horizonta-02.png', '.'),
        ('assets', 'assets'),                  # atom-icon.svg, check.svg, dot.svg, fonts/ (los referencia gui.py)
        ('programas_externos', 'programas_externos'),
    ] + pyexiv2_datas + mpl_datas,
    hiddenimports=[
        'pyexiv2', 'ipaddress',
        'gui', 'atom_core.organize',           # import perezoso en el worker → forzarlo explícito
        'webview.platforms.qt',                # backend pywebview Qt (antes edgechromium/WebView2)
        'qtpy', 'bottle', 'proxy_tools',       # transitivas de pywebview
        'PySide6.QtWebEngineWidgets',          # Chromium embebido (bridge Qt)
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel',
        # diálogos nativos de carpeta/archivo (pick_folder/pick_file en Windows, vía PowerShell -STA):
        'pythoncom', 'pywintypes', 'win32gui', 'win32con',
        'win32com', 'win32com.shell',
        'win32comext.shell', 'win32comext.shell.shell', 'win32comext.shell.shellcon',
    ] + pyexiv2_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'IPython', 'ipykernel', 'jupyter_client', 'jupyter_core',
        'debugpy', 'jedi', 'parso',
        'clr', 'pythonnet',                    # pythonnet/WebView2: ya no se usa (Qt en su lugar)
        # pesados que el pipeline no usa (QtWebEngine SÍ se mantiene ahora)
        'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtMultimedia',
        'PySide6.QtQuick3D', 'PySide6.QtDataVisualization',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ONEFILE: binaries + datas dentro del propio EXE (sin COLLECT).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ATOM-Organizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,                 # sin consola (app de ventana)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/atom-icon.ico' if __import__('os').path.exists('assets/atom-icon.ico') else None,
)
