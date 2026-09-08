"""Caché en disco del DLL del diálogo moderno de carpeta (Windows).

Compilar el C# con `csc.exe` costaba 1-2 s en cada apertura del selector.
`_dll_carpeta_moderna()` compila una sola vez y cachea el DLL en disco; en
Linux (donde no hay `csc.exe` ni sentido a este diálogo) devuelve `None` de
inmediato. `precalentar_dialogo_carpeta()` lanza esa compilación en un hilo
daemon al arrancar para que la primera apertura ya lo encuentre hecho.
"""
from __future__ import annotations

import threading
import time

import app_webview as aw


def test_dll_carpeta_moderna_devuelve_none_en_linux(monkeypatch):
    monkeypatch.setattr(aw.platform, "system", lambda: "Linux")
    monkeypatch.setattr(aw, "_dll_carpeta", None)
    assert aw._dll_carpeta_moderna() is None


def test_precalentar_dialogo_carpeta_no_lanza_en_linux(monkeypatch):
    monkeypatch.setattr(aw.platform, "system", lambda: "Linux")
    # No debe lanzar ni levantar hilo alguno: en Linux es un no-op.
    aw.precalentar_dialogo_carpeta()


def test_ruta_dll_carpeta_cambia_con_el_fuente(monkeypatch, tmp_path):
    monkeypatch.setattr("external_tools._user_config_path", lambda: str(tmp_path / "cfg" / "Config.ini"))

    monkeypatch.setattr(aw, "_MODERN_FOLDER_SRC", "fuente original")
    ruta_1 = aw._ruta_dll_carpeta()

    monkeypatch.setattr(aw, "_MODERN_FOLDER_SRC", "fuente modificada")
    ruta_2 = aw._ruta_dll_carpeta()

    assert ruta_1.name != ruta_2.name
    assert ruta_1.parent == ruta_2.parent == tmp_path / "cfg"


def test_dll_carpeta_moderna_en_windows_compila_y_cachea(monkeypatch, tmp_path):
    """Simula Windows: la primera llamada "compila" (crea el fichero), la
    segunda sale directamente de la caché en memoria sin volver a invocar
    `subprocess.run`."""
    import types

    monkeypatch.setattr(aw.platform, "system", lambda: "Windows")
    monkeypatch.setattr(aw, "_dll_carpeta", None)

    destino = tmp_path / "modern-folder-abc123.dll"
    monkeypatch.setattr(aw, "_ruta_dll_carpeta", lambda: destino)

    llamadas = []

    def compilar_falso(*args, **kwargs):
        llamadas.append(1)
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporal = destino.with_name(f"{destino.stem}.{aw.os.getpid()}.tmp")
        temporal.write_bytes(b"dll falso")
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(aw.subprocess, "run", compilar_falso)

    primero = aw._dll_carpeta_moderna()
    assert primero == str(destino)
    assert len(llamadas) == 1

    segundo = aw._dll_carpeta_moderna()
    assert segundo == str(destino)
    assert len(llamadas) == 1  # caché en memoria: no vuelve a compilar


def test_lit_ps_duplica_comillas_simples():
    assert aw._lit_ps("C:\\Users\\O'Brien") == "C:\\Users\\O''Brien"


def test_dll_carpeta_moderna_ignora_cacheado_de_tamano_cero(monkeypatch, tmp_path):
    """Un DLL cacheado de 0 bytes (compilación anterior interrumpida a medias,
    disco lleno...) no se debe dar por bueno: hay que recompilar."""
    import types

    monkeypatch.setattr(aw.platform, "system", lambda: "Windows")
    monkeypatch.setattr(aw, "_dll_carpeta", None)

    destino = tmp_path / "modern-folder-abc123.dll"
    destino.write_bytes(b"")  # cacheado pero vacío
    monkeypatch.setattr(aw, "_ruta_dll_carpeta", lambda: destino)

    llamadas = []

    def compilar_falso(*args, **kwargs):
        llamadas.append(1)
        temporal = destino.with_name(f"{destino.stem}.{aw.os.getpid()}.tmp")
        temporal.write_bytes(b"dll de verdad")
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(aw.subprocess, "run", compilar_falso)

    resultado = aw._dll_carpeta_moderna()
    assert resultado == str(destino)
    assert len(llamadas) == 1
    assert destino.stat().st_size > 0


def test_dll_carpeta_moderna_falla_no_deja_destino_ni_tmp(monkeypatch, tmp_path):
    """Si el subprocess simulado falla (rc != 0), el destino definitivo no debe
    existir y no debe quedar ningún `.tmp` suelto en la carpeta."""
    import types

    monkeypatch.setattr(aw.platform, "system", lambda: "Windows")
    monkeypatch.setattr(aw, "_dll_carpeta", None)

    destino = tmp_path / "modern-folder-abc123.dll"
    monkeypatch.setattr(aw, "_ruta_dll_carpeta", lambda: destino)

    def compilar_falla(*args, **kwargs):
        # Simula que el compilador llegó a escribir algo en el temporal antes
        # de abortar, para probar que igualmente se limpia.
        temporal = destino.with_name(f"{destino.stem}.{aw.os.getpid()}.tmp")
        temporal.parent.mkdir(parents=True, exist_ok=True)
        temporal.write_bytes(b"a medias")
        return types.SimpleNamespace(returncode=1, stderr="csc.exe: error interno")

    monkeypatch.setattr(aw.subprocess, "run", compilar_falla)

    resultado = aw._dll_carpeta_moderna()
    assert resultado is None
    assert not destino.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_invalidar_dll_carpeta_borra_y_resetea_global(monkeypatch, tmp_path):
    destino = tmp_path / "modern-folder-abc123.dll"
    destino.write_bytes(b"dll cacheado")
    monkeypatch.setattr(aw, "_ruta_dll_carpeta", lambda: destino)
    monkeypatch.setattr(aw, "_dll_carpeta", str(destino))

    aw._invalidar_dll_carpeta()

    assert aw._dll_carpeta is None
    assert not destino.exists()


def test_invalidar_dll_carpeta_no_revienta_si_no_existe(monkeypatch, tmp_path):
    destino = tmp_path / "modern-folder-no-existe.dll"
    monkeypatch.setattr(aw, "_ruta_dll_carpeta", lambda: destino)
    monkeypatch.setattr(aw, "_dll_carpeta", "algo")

    aw._invalidar_dll_carpeta()  # no debe lanzar

    assert aw._dll_carpeta is None


def test_win_pick_folder_reintenta_si_dll_cacheado_es_inservible(monkeypatch, tmp_path):
    """DLL cacheado presente, pero el diálogo aborta (rc != 0, no cancelación
    del usuario): se invalida la caché y se reintenta una vez por el camino
    antiguo (compilar en caliente)."""
    monkeypatch.setattr(aw.platform, "system", lambda: "Windows")
    destino = tmp_path / "modern-folder-abc123.dll"
    destino.write_bytes(b"dll cacheado")
    monkeypatch.setattr(aw, "_ruta_dll_carpeta", lambda: destino)
    monkeypatch.setattr(aw, "_dll_carpeta", str(destino))

    api = aw.Api()

    llamadas = []

    def dialog_espia(self, ps_body):
        llamadas.append(ps_body)
        if len(llamadas) == 1:
            self._rc_dialogo = 1  # abortó, no fue cancelación del usuario
            return None
        self._rc_dialogo = 0
        return "C:\\ruta\\elegida"

    monkeypatch.setattr(aw.Api, "_win_dialog", dialog_espia)

    resultado = api._win_pick_folder()

    assert resultado == "C:\\ruta\\elegida"
    assert len(llamadas) == 2
    # El primer intento usa el DLL cacheado (Add-Type -Path); el reintento usa
    # el camino antiguo, que compila en caliente.
    assert "Add-Type -Path" in llamadas[0]
    assert aw._MODERN_FOLDER_CS in llamadas[1]
    assert aw._dll_carpeta is None  # la caché quedó invalidada


def test_win_pick_folder_no_reintenta_si_usuario_cancela(monkeypatch, tmp_path):
    """rc == 0 con resultado None es cancelación del usuario: no hay que
    invalidar la caché ni reintentar."""
    monkeypatch.setattr(aw.platform, "system", lambda: "Windows")
    destino = tmp_path / "modern-folder-abc123.dll"
    destino.write_bytes(b"dll cacheado")
    monkeypatch.setattr(aw, "_ruta_dll_carpeta", lambda: destino)
    monkeypatch.setattr(aw, "_dll_carpeta", str(destino))

    api = aw.Api()

    llamadas = []

    def dialog_espia(self, ps_body):
        llamadas.append(ps_body)
        self._rc_dialogo = 0
        return None

    monkeypatch.setattr(aw.Api, "_win_dialog", dialog_espia)

    resultado = api._win_pick_folder()

    assert resultado is None
    assert len(llamadas) == 1  # sin reintento
    assert aw._dll_carpeta == str(destino)  # caché intacta


def test_precalentar_dialogo_carpeta_en_windows_lanza_hilo_daemon(monkeypatch):
    monkeypatch.setattr(aw.platform, "system", lambda: "Windows")

    lanzados = []
    real_thread = threading.Thread

    def thread_espia(*args, **kwargs):
        hilo = real_thread(*args, **kwargs)
        lanzados.append((hilo, kwargs.get("daemon")))
        return hilo

    monkeypatch.setattr(aw.threading, "Thread", thread_espia)
    monkeypatch.setattr(aw, "_dll_carpeta_moderna", lambda log=None: None)

    aw.precalentar_dialogo_carpeta()

    assert len(lanzados) == 1
    hilo, daemon = lanzados[0]
    assert daemon is True
    hilo.join(timeout=5)
    assert not hilo.is_alive()
