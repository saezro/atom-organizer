"""La instalación silenciosa tiene que dejar log, y en la carpeta que el usuario manda.

Contexto (2026-08-04): a un usuario le saltó el modal de actualización y ATOM no
volvió a abrirse. No hubo forma de saber si la instalación había ido bien y sólo
había fallado el relanzado, o si había fallado entera — `updater.install()` no
pasaba `/LOG` a Inno Setup, así que la instalación era una caja negra. Es el
MISMO agujero que tenía el conversor DJI: un proceso externo cuyo resultado no
se captura es un fallo que no se puede diagnosticar en la máquina de otro.

`_watch` no cubre esto: cuando la instalación va bien, la app está muerta (Inno
la mata con /FORCECLOSEAPPLICATIONS) antes de poder informar de nada.
"""
import subprocess

import pytest

from atom_core import updater


@pytest.fixture
def instalador(tmp_path):
    """Un fichero que existe: `install()` aborta antes de nada si no lo hay."""
    exe = tmp_path / "ATOM-Organizer-Setup-v9.9.9.exe"
    exe.write_bytes(b"no es un exe de verdad, solo hace falta que exista")
    return exe


@pytest.fixture
def popen_espia(monkeypatch):
    """Captura el argv con el que se lanzaría el instalador, sin lanzarlo."""
    capturado = {}

    class _Proc:
        def wait(self):
            return 0

    def _fake_popen(argv, **kwargs):
        capturado["argv"] = argv
        capturado["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    # install() sólo hace algo en Windows; el resto del test es agnóstico.
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    return capturado


def test_install_pasa_LOG_a_inno(instalador, popen_espia, tmp_path, monkeypatch):
    """Sin /LOG la instalación no deja rastro y el fallo no es diagnosticable."""
    monkeypatch.setattr(updater, "_install_log_path", lambda: tmp_path / "inst.log")

    res = updater.install(str(instalador))

    assert res["ok"] is True
    flags = popen_espia["argv"][1:]
    log_flags = [f for f in flags if f.startswith("/LOG=")]
    assert log_flags, f"no se pasó /LOG a Inno Setup: {flags}"
    assert log_flags[0] == f"/LOG={tmp_path / 'inst.log'}"
    # La ruta se devuelve para poder citarla en los mensajes de error.
    assert res["log"] == str(tmp_path / "inst.log")


def test_install_conserva_los_flags_que_hacen_funcionar_la_actualizacion(
    instalador, popen_espia, tmp_path, monkeypatch
):
    """Regresión: quitar cualquiera de estos rompe el auto-update entero.

    /FORCECLOSEAPPLICATIONS es el que evita el ExitCode 5 (el Restart Manager no
    consigue cerrar QtWebEngineProcess); sin /SILENT y /SUPPRESSMSGBOXES el
    instalador se queda esperando a un usuario que no ve ninguna ventana.
    """
    monkeypatch.setattr(updater, "_install_log_path", lambda: tmp_path / "inst.log")

    updater.install(str(instalador))

    flags = set(popen_espia["argv"][1:])
    for esperado in ("/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS",
                     "/FORCECLOSEAPPLICATIONS", "/NORESTART"):
        assert esperado in flags, f"falta el flag {esperado}"
    # /VERYSILENT oculta el progreso (el usuario reabre la app a mitad);
    # /RESTARTAPPLICATIONS lo sustituye el [Run] con WizardSilent.
    assert "/VERYSILENT" not in flags
    assert "/RESTARTAPPLICATIONS" not in flags


def test_log_va_a_la_carpeta_de_logs_del_usuario(monkeypatch, tmp_path):
    """Debe caer junto a los logs de corrida, NO en %TEMP%.

    Es lo que hace que el log llegue solo: esa carpeta es la que el usuario ya
    abre y nos manda cuando algo falla. En %TEMP% habría que pedírselo aparte.
    """
    destino = tmp_path / "AppData" / "ATOM-Organizer" / "Logs"
    monkeypatch.setattr(
        "external_tools.user_log_dir", lambda: str(destino), raising=False
    )

    ruta = updater._install_log_path()

    assert ruta.parent == destino
    assert destino.is_dir(), "la carpeta de logs debe crearse si no existía"
    assert ruta.name.startswith("atom-organizer-install_")
    assert ruta.suffix == ".log"


def test_log_cae_en_temp_si_no_hay_carpeta_de_logs(monkeypatch, tmp_path):
    """Un log en mal sitio sigue siendo mejor que ningún log."""
    import external_tools

    def _revienta():
        raise RuntimeError("no hay carpeta de logs")

    monkeypatch.setattr(external_tools, "user_log_dir", _revienta, raising=False)

    ruta = updater._install_log_path()

    assert ruta.name.startswith("atom-organizer-install_")
    assert "atom-organizer-update" in str(ruta)


def test_el_fallo_del_instalador_cita_el_log(monkeypatch, tmp_path):
    """El mensaje de error tiene que decir DÓNDE mirar, o no sirve de nada."""
    avisos = []

    class _ProcQueFalla:
        def wait(self):
            return 5  # el ExitCode 5 clásico: no pudo cerrar la app

    updater._watch(_ProcQueFalla(), lambda code, msg: avisos.append((code, msg)),
                   str(tmp_path / "inst.log"))

    assert len(avisos) == 1
    code, msg = avisos[0]
    assert code == 5
    assert "no se pudo cerrar la aplicación" in msg.lower()
    assert str(tmp_path / "inst.log") in msg


def test_watch_calla_cuando_la_instalacion_va_bien(tmp_path):
    """rc 0 = éxito: avisar aquí sería un falso positivo en la cara del usuario."""
    avisos = []

    class _ProcOk:
        def wait(self):
            return 0

    updater._watch(_ProcOk(), lambda code, msg: avisos.append((code, msg)),
                   str(tmp_path / "inst.log"))

    assert avisos == []


def _api_para_install(monkeypatch, proc):
    import app_webview
    api = app_webview.Api.__new__(app_webview.Api)
    api._update_path = "x.exe"
    api._update_sha256 = "abc"
    api._sink = None
    cerrado = []
    monkeypatch.setattr(updater, "install",
                        lambda *a, **k: {"ok": True, "log": "l", "_proc": proc})
    monkeypatch.setattr(app_webview.Api, "_cerrar_para_actualizar",
                        lambda self: cerrado.append(True))
    monkeypatch.setattr(app_webview.time, "sleep", lambda s: None)
    return api, cerrado


def test_install_update_no_cierra_si_setup_sale_con_error(monkeypatch):
    class _P:
        def poll(self):
            return 5
    api, cerrado = _api_para_install(monkeypatch, _P())
    res = api.install_update()
    assert res["ok"] is True and "_proc" not in res
    assert cerrado == []


def test_install_update_cierra_si_setup_sigue_vivo_o_sale_ok(monkeypatch):
    class _Vivo:
        def poll(self):
            return None
    api, cerrado = _api_para_install(monkeypatch, _Vivo())
    api.install_update()
    assert cerrado == [True]

    class _Ok:
        def poll(self):
            return 0
    api, cerrado = _api_para_install(monkeypatch, _Ok())
    api.install_update()
    assert cerrado == [True]
