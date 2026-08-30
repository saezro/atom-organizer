# atom_organizer_webview.spec — build Windows ONEDIR de la UI React (pywebview + Qt).
# -*- mode: python ; coding: utf-8 -*-
#
# Entry: app_webview.py. Produce dist/ATOM-Organizer/ (carpeta), que el instalador
# Inno Setup (packaging/windows/ATOM-Organizer.iss) empaqueta en
# ATOM-Organizer-Setup-vX.Y.Z.exe. Ya no se distribuye .exe portable: el onefile
# se auto-extraía en %TEMP% en cada arranque (patrón que marcan los antivirus) y
# el instalador es además lo que consume el updater in-app.
# SOLO Windows: PyInstaller no cross-compila.
#
# Notas de diseño:
#  - Desde v3.9 Windows pinta con el backend Qt de pywebview (PySide6 + QtWebEngine,
#    Chromium embebido), IGUAL que Linux (ver app_webview.py: `webview.start(gui="qt")`).
#    Se abandonó WebView2 (v3.8.x): con ese backend `window.pywebview` no se inyectaba y
#    el bridge JS↔Python quedaba muerto. QtWebEngine SÍ se empaqueta aquí (antes se excluía).
#  - PySide6 core es imprescindible por el backend de pywebview (línea de arriba), NO por el
#    pipeline: desde que las fases viven en atom_core/phases.py, `atom_core.organize` ya no
#    importa gui.py ni PySide6 (eso es lo que permite una imagen de servidor sin Qt).
#    Se excluye solo lo pesado que nadie usa (Qt3D, Charts, Multimedia, Quick3D, DataVisualization).
#  - La UI React va como data en 'webui/dist'; app_webview.py la resuelve vía _MEIPASS.
#  - Recursos del pipeline (config, programas_externos, assets) los resuelve external_tools.app_base_dir.
#  - programas_externos/{exiftool.exe, DJI/dji_irp.exe} son binarios Windows: imprescindibles.
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# pyexiv2 arrastra su binario nativo (libexiv2 / .pyd) — imprescindible en runtime
pyexiv2_datas, pyexiv2_binaries, pyexiv2_hidden = collect_all('pyexiv2')

# CAUSA RAÍZ del 'ModuleNotFoundError: No module named exiv2api' al leer el estadillo
# (v3.9, solo Windows): pyexiv2/lib/__init__.py carga en RUNTIME el binario nativo que
# vive junto a él (ctypes.CDLL(<lib>/exiv2.dll) + import de exiv2api). collect_all() no
# garantiza que el .pyd/DLL queden en la ruta EXACTA que el runtime busca, así que se
# fuerzan como `datas` (no como binaries: datas preserva el path relativo literal y evita
# que PyInstaller reubique o aplane el .pyd). Sin esto, cualquier lectura de EXIF revienta.
# El layout cambió con pyexiv2 2.16: hasta 2.8 el .pyd colgaba de lib/py3.X-win/ y se
# importaba tras un sys.path.append dinámico; desde 2.9 está plano en lib/. Se soportan
# ambos para no atarse a la versión pineada en requirements.txt.
import os as _os_px, sys as _sys_px, glob as _glob_px
import pyexiv2 as _px
_px_lib = _os_px.path.join(_os_px.path.dirname(_px.__file__), 'lib')
_px_pyver = 'py{}.{}-win'.format(_sys_px.version_info.major, _sys_px.version_info.minor)
pyexiv2_native = []
for _dll in _glob_px.glob(_os_px.path.join(_px_lib, 'exiv2.dll')):
    pyexiv2_native.append((_dll, 'pyexiv2/lib'))
_px_api = (_glob_px.glob(_os_px.path.join(_px_lib, 'exiv2api*.pyd')) or
           _glob_px.glob(_os_px.path.join(_px_lib, _px_pyver, 'exiv2api*.pyd')))
if not _px_api:
    raise SystemExit('[spec] pyexiv2 nativo NO encontrado bajo: ' + _px_lib)
for _src in _px_api:
    _rel = _os_px.path.relpath(_os_px.path.dirname(_src), _px_lib)
    _dst = 'pyexiv2/lib' if _rel == '.' else 'pyexiv2/lib/' + _rel.replace('\\', '/')
    pyexiv2_native.append((_src, _dst))
# matplotlib mpl-data (colormaps usados por el pipeline)
mpl_datas = collect_data_files('matplotlib')

# CAUSA RAÍZ del bridge muerto en Windows (pw=N con WebView2 Y con Qt): PyInstaller
# empaquetaba webview/ SIN sus assets JS internos (webview/js/*.js). Esos scripts son
# los que inyectan `window.pywebview` / `window.pywebview.api` en la página; sin ellos
# el objeto bridge NUNCA se crea (con cualquier backend), aunque la UI React (que va
# aparte en webui/dist) sí renderice. Se fuerza su inclusión explícita.
webview_datas = collect_data_files('webview')

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
    ] + pyexiv2_datas + mpl_datas + webview_datas + pyexiv2_native,
    hiddenimports=[
        'pyexiv2', 'ipaddress',
        'version', 'atom_core.updater',   # updater: import perezoso desde app_webview
        'gui', 'atom_core.organize',           # import perezoso en el worker → forzarlo explícito
        'psutil',                              # utils.workers_para_lote: import perezoso; sin él
                                               # el pool no puede capar por RAM libre
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

# Metadatos VERSIONINFO del .exe (empresa, producto, versión). Además de salir
# en Propiedades del fichero, un binario CON metadatos coherentes puntúa mejor
# en las heurísticas de Defender/SmartScreen: un .exe anónimo y sin firmar es
# justo el perfil que marcan. Se genera al vuelo desde version.py (fuente única).
import sys as _sys_v
_sys_v.path.insert(0, _os.path.abspath('.'))
from version import __version__ as _APP_VER
_v_parts = tuple((list(int(x) for x in _APP_VER.split('.')[:3]) + [0, 0, 0])[:4])

_version_res = '''VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040a04b0', [
      StringStruct('CompanyName', 'Aerotools UAV'),
      StringStruct('FileDescription', 'ATOM Organizer'),
      StringStruct('FileVersion', '{s}'),
      StringStruct('InternalName', 'ATOM-Organizer'),
      StringStruct('LegalCopyright', 'Aerotools UAV'),
      StringStruct('OriginalFilename', 'ATOM-Organizer.exe'),
      StringStruct('ProductName', 'ATOM Organizer'),
      StringStruct('ProductVersion', '{s}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [0x40a, 1200])])
  ]
)'''.format(v=_v_parts, s=_APP_VER)
with open('file_version_info.txt', 'w', encoding='utf-8') as _fh:
    _fh.write(_version_res)

# ONEDIR (antes onefile). Dos motivos:
#  1) El onefile se auto-extrae en %TEMP% y ejecuta desde ahí en cada arranque:
#     ese patrón es exactamente lo que las heurísticas de antivirus marcan como
#     dropper. Onedir + instalador reduce mucho los falsos positivos.
#  2) El instalador Inno Setup necesita una carpeta que copiar a Archivos de
#     programa; y el arranque es bastante más rápido (no descomprime 200 MB).
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
    console=False,                 # sin consola (app de ventana)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version='file_version_info.txt',
    icon='assets/atom-icon.ico' if _os.path.exists('assets/atom-icon.ico') else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='ATOM-Organizer',         # dist/ATOM-Organizer/ATOM-Organizer.exe
)
