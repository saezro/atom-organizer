"""Tests de `pyi_rth_cuda._preload_gpu_dynamic_libs` — selección de subdir
por plataforma (bug: en el onedir Windows las DLL de cudart/nvrtc/nvjpeg
cuelgan de `nvidia/<paquete>/bin/`, no de `lib/` como en Linux; `lib/` en
Windows solo tiene `__init__.py`)."""
import ctypes

import pytest

import pyi_rth_cuda


def _arbol_windows(base):
    """`nvidia/<paquete>/bin/*.dll` con `lib/` como decoy (solo __init__.py),
    igual que el onedir real de PyInstaller en Windows."""
    for pkg, dll in (
        ("cuda_runtime", "cudart64_12.dll"),
        ("cuda_nvrtc", "nvrtc64_120_0.dll"),
        ("nvjpeg", "nvjpeg64_12.dll"),
    ):
        bin_dir = base / "nvidia" / pkg / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / dll).write_bytes(b"")
        lib_dir = base / "nvidia" / pkg / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / "__init__.py").write_bytes(b"")


def _arbol_linux(base):
    for pkg, so in (
        ("cuda_runtime", "libcudart.so.12"),
        ("cuda_nvrtc", "libnvrtc.so.12"),
        ("nvjpeg", "libnvjpeg.so.12"),
    ):
        lib_dir = base / "nvidia" / pkg / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / so).write_bytes(b"")


def test_preload_windows_usa_bin_no_lib(tmp_path, monkeypatch):
    _arbol_windows(tmp_path)
    monkeypatch.setattr(pyi_rth_cuda.sys, "platform", "win32")
    cargadas = []
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda ruta: cargadas.append(ruta) or object(), raising=False
    )
    pyi_rth_cuda._PRELOADED_HANDLES.clear()

    pyi_rth_cuda._preload_gpu_dynamic_libs(str(tmp_path))

    assert len(cargadas) == 3
    assert all(f"{pyi_rth_cuda.os.sep}bin{pyi_rth_cuda.os.sep}" in r for r in cargadas)
    assert not any(f"{pyi_rth_cuda.os.sep}lib{pyi_rth_cuda.os.sep}" in r for r in cargadas)


def test_preload_linux_usa_lib(tmp_path, monkeypatch):
    _arbol_linux(tmp_path)
    monkeypatch.setattr(pyi_rth_cuda.sys, "platform", "linux")
    cargadas = []
    monkeypatch.setattr(
        ctypes, "CDLL", lambda ruta, mode=0: cargadas.append(ruta) or object()
    )
    pyi_rth_cuda._PRELOADED_HANDLES.clear()

    pyi_rth_cuda._preload_gpu_dynamic_libs(str(tmp_path))

    assert len(cargadas) == 3
    assert all(f"{pyi_rth_cuda.os.sep}lib{pyi_rth_cuda.os.sep}" in r for r in cargadas)


def test_preload_windows_no_mira_lib(tmp_path, monkeypatch):
    """Si solo hubiera `lib/` (layout Linux) bajo Windows, el glob no debe
    encontrar nada: confirma que ya no se busca en `lib/` en esa plataforma
    (el bug original: buscaba ahí y fallaba en silencio)."""
    for pkg, dll in (
        ("cuda_runtime", "cudart64_12.dll"),
        ("cuda_nvrtc", "nvrtc64_120_0.dll"),
        ("nvjpeg", "nvjpeg64_12.dll"),
    ):
        lib_dir = tmp_path / "nvidia" / pkg / "lib"
        lib_dir.mkdir(parents=True)
        (lib_dir / dll).write_bytes(b"")
    monkeypatch.setattr(pyi_rth_cuda.sys, "platform", "win32")
    cargadas = []
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda ruta: cargadas.append(ruta) or object(), raising=False
    )
    pyi_rth_cuda._PRELOADED_HANDLES.clear()

    pyi_rth_cuda._preload_gpu_dynamic_libs(str(tmp_path))

    assert cargadas == []
