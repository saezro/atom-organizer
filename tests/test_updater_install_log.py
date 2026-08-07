"""La instalación silenciosa tiene que dejar log, y en la carpeta que el usuario manda.

Contexto (2026-08-04): a Daniel le saltó el modal de actualización y ATOM no
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
    consigue cerrar QtWebEngineProcess); sin /VERYSILENT y /SUPPRESSMSGBOXES el
    instalador se queda esperando a un usuario que no ve ninguna ventana.
    """
    monkeypatch.setattr(updater, "_install_log_path", lambda: tmp_path / "inst.log")

    updater.install(str(instalador))

    flags = set(popen_espia["argv"][1:])
    for esperado in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS",
                     "/FORCECLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS", "/NORESTART"):
        assert esperado in flags, f"falta el flag {esperado}"


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
