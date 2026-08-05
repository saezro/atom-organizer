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
# matplotlib mpl-data (colormaps usados por el pipeline térmico)
mpl_datas = collect_data_files('matplotlib')

a = Analysis(
    ['app_webview.py'],
    pathex=[],
    binaries=pyexiv2_binaries,
    datas=[
        ('webui/dist', 'webui/dist'),          # UI React buildeada (npm run build)
        ('config/Config.ini', 'config'),
        ('Logo_atom_uas_horizonta-02.png', '.'),
        ('assets', 'assets'),
        ('programas_externos', 'programas_externos'),  # DJI/ libdirp.so + deps (Linux)
    ] + pyexiv2_datas + mpl_datas,
    hiddenimports=[
        'pyexiv2', 'ipaddress',
        'version', 'atom_core.updater',        # updater: import perezoso desde app_webview
        'gui', 'atom_core.organize',           # import perezoso en el worker → forzarlo
        'webview.platforms.qt',                # backend pywebview en Linux
        'qtpy', 'bottle', 'proxy_tools',       # transitivas de pywebview
        'PySide6.QtWebEngineWidgets',          # Chromium embebido (imprescindible en Linux)
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebChannel',
    ] + pyexiv2_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'IPython', 'ipykernel', 'jupyter_client', 'jupyter_core',
        'debugpy', 'jedi', 'parso',
        'clr', 'pythonnet',                    # pythonnet/WebView2: solo Windows
        # pesados que el pipeline no usa (QtWebEngine SÍ se mantiene)
        'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtMultimedia',
        'PySide6.QtQuick3D', 'PySide6.QtDataVisualization',
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
