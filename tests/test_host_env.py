"""Salir del bundle sin arrastrar su entorno.

El fallo que motiva esto (v3.4.21, Linux): al pulsar «Iniciar sesión» el
navegador nunca abría —`/bin/sh: symbol lookup error: rl_trim_arg_from_keyseq`,
el `xdg-open` enlazando la libreadline del bundle— y el botón se quedaba en
«Ejecutando…» para siempre, esperando un callback de OAuth imposible.
"""
import subprocess
import sys

import pytest

from atom_core import host_env


# ---------------------------------------------------------------------------
# entorno_del_sistema
# ---------------------------------------------------------------------------

def test_restaura_el_valor_que_habia_antes_del_bundle():
    """`<VAR>_ORIG` es lo que PyInstaller apartó: es el valor bueno para un hijo."""
    limpio = host_env.entorno_del_sistema({
        "LD_LIBRARY_PATH": "/tmp/.mount_ATOM/usr/lib",
        "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
    })
    assert limpio["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in limpio


def test_sin_original_la_variable_se_quita_en_vez_de_dejar_la_del_bundle():
    """No había `LD_LIBRARY_PATH` antes de arrancar: el hijo tampoco debe verla.

    Dejar la del bundle es justo el bug: el hijo enlaza librerías que no son las
    suyas y muere por un símbolo que falta.
    """
    limpio = host_env.entorno_del_sistema({
        "LD_LIBRARY_PATH": "/tmp/.mount_ATOM/usr/lib",
        "PYTHONHOME": "/tmp/.mount_ATOM/usr",
        "PATH": "/usr/bin",
    })
    assert "LD_LIBRARY_PATH" not in limpio
    assert "PYTHONHOME" not in limpio
    assert limpio["PATH"] == "/usr/bin"  # lo que no contamina el bundle, se respeta


def test_un_original_vacio_cuenta_como_no_haber_tenido_nada():
    limpio = host_env.entorno_del_sistema({
        "LD_PRELOAD": "/tmp/.mount_ATOM/usr/lib/libxkbcommon.so.0",
        "LD_PRELOAD_ORIG": "",
    })
    assert "LD_PRELOAD" not in limpio


def test_no_toca_el_entorno_del_proceso():
    """Se devuelve una copia: envenenar `os.environ` rompería a la propia app,
    que sí necesita las rutas del bundle."""
    base = {"LD_LIBRARY_PATH": "/tmp/.mount_ATOM/usr/lib"}
    host_env.entorno_del_sistema(base)
    assert base == {"LD_LIBRARY_PATH": "/tmp/.mount_ATOM/usr/lib"}


# ---------------------------------------------------------------------------
# abrir_en_navegador
# ---------------------------------------------------------------------------

def test_fuera_del_bundle_se_usa_webbrowser_sin_inventar_nada(monkeypatch):
    """En Windows —lo que corre en producción— esto no debe cambiar."""
    monkeypatch.setattr(host_env, "dentro_de_bundle", lambda: False)
    visto = []
    monkeypatch.setattr(host_env.webbrowser, "open", lambda u: visto.append(u) or True)
    monkeypatch.setattr(subprocess, "Popen", _prohibido)

    assert host_env.abrir_en_navegador("https://ejemplo/auth") is True
    assert visto == ["https://ejemplo/auth"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ruta solo-Linux")
def test_en_el_bundle_el_navegador_se_lanza_con_el_entorno_del_sistema(monkeypatch):
    monkeypatch.setattr(host_env, "dentro_de_bundle", lambda: True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_ATOM/usr/lib")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.delenv("BROWSER", raising=False)
    lanzados = []

    def falso_popen(orden, *, env, **kwargs):
        lanzados.append((orden, env, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", falso_popen)

    assert host_env.abrir_en_navegador("https://ejemplo/auth") is True
    orden, env, kwargs = lanzados[0]
    assert orden == ["xdg-open", "https://ejemplo/auth"]
    assert "LD_LIBRARY_PATH" not in env
    # Sin esto, cerrar la app se llevaría por delante el navegador a medio login.
    assert kwargs["start_new_session"] is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ruta solo-Linux")
def test_se_respeta_el_navegador_que_el_usuario_puso_en_BROWSER(monkeypatch):
    monkeypatch.setattr(host_env, "dentro_de_bundle", lambda: True)
    monkeypatch.setenv("BROWSER", "firefox %s")
    lanzados = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda orden, **kw: lanzados.append(orden) or object())

    host_env.abrir_en_navegador("https://ejemplo/auth")
    # El `%s` es un marcador de posición, no un argumento que pasarle a firefox.
    assert lanzados[0] == ["firefox", "https://ejemplo/auth"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ruta solo-Linux")
def test_si_falta_xdg_open_se_prueba_el_siguiente(monkeypatch):
    monkeypatch.setattr(host_env, "dentro_de_bundle", lambda: True)
    monkeypatch.delenv("BROWSER", raising=False)
    intentos = []

    def falso_popen(orden, **kwargs):
        intentos.append(orden[0])
        if orden[0] == "xdg-open":
            raise FileNotFoundError(orden[0])
        return object()

    monkeypatch.setattr(subprocess, "Popen", falso_popen)

    assert host_env.abrir_en_navegador("https://ejemplo/auth") is True
    assert intentos == ["xdg-open", "gio"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="ruta solo-Linux")
def test_si_no_hay_ningun_abridor_queda_el_intento_de_webbrowser(monkeypatch):
    """Puede fallar por lo mismo, pero no perdemos una opción por no probarla."""
    monkeypatch.setattr(host_env, "dentro_de_bundle", lambda: True)
    monkeypatch.delenv("BROWSER", raising=False)
    monkeypatch.setattr(subprocess, "Popen", _revienta)
    monkeypatch.setattr(host_env.webbrowser, "open", lambda u: True)

    assert host_env.abrir_en_navegador("https://ejemplo/auth") is True


# ---------------------------------------------------------------------------
# El cableado: quien abre el navegador en el login es esta función
# ---------------------------------------------------------------------------

def test_el_login_abre_el_navegador_por_la_via_limpia():
    """Que `login()` traiga `webbrowser.open` por defecto es exactamente el bug:
    el subproceso heredaría el entorno del bundle."""
    import inspect

    from atom_core.google_auth import GoogleAuth

    por_defecto = inspect.signature(GoogleAuth.login).parameters["open_browser"].default
    assert por_defecto is host_env.abrir_en_navegador


def _prohibido(*_a, **_k):
    raise AssertionError("no debe lanzarse ningún proceso por esta vía")


def _revienta(*_a, **_k):
    raise FileNotFoundError("no hay abridor")
