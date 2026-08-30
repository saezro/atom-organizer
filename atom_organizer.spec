# atom_organizer.spec — build Linux onedir para AppImage v1
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

# pyexiv2 arrastra libexiv2.so (binario nativo) — imprescindible en runtime
pyexiv2_datas, pyexiv2_binaries, pyexiv2_hidden = collect_all('pyexiv2')
# matplotlib mpl-data (colormaps usados por pipeline.cm.get_cmap)
mpl_datas = collect_data_files('matplotlib')

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=pyexiv2_binaries,
    datas=[
        ('config/Config.ini', 'config'),
        ('Logo_atom_uas_horizonta-02.png', '.'),
        ('programas_externos', 'programas_externos'),
    ] + pyexiv2_datas + mpl_datas,
    hiddenimports=['pyexiv2', 'ipaddress'] + pyexiv2_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'IPython', 'ipykernel', 'jupyter_client', 'jupyter_core',
        'debugpy', 'jedi', 'parso',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.Qt3DCore', 'PySide6.QtCharts', 'PySide6.QtQuick',
        'PySide6.QtQml', 'PySide6.QtMultimedia',
        # atom_core.almacen_gcs importa 'google.cloud.storage' de forma perezosa
        # (dentro de AlmacenGCS.__init__, no a nivel de módulo) para que el
        # backend gs:// exista sin arrastrar el SDK al escritorio: SOLO lo trae
        # la imagen del Cloud Run Job. El escritorio no lo necesita (usa
        # atom_core.cloud_upload, solo stdlib) — se excluye explícito para que
        # nunca se cuele en el .exe aunque el entorno de build lo tenga instalado.
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
    name='atom_organizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
