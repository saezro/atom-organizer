"""Tests de `atom_core.sesion_remota`: lock de sesión remota (benchmark por SSH)."""
import os
import socket

from atom_core import sesion_remota


def _config_ini_en(tmp_path):
    """Ruta de `Config.ini` bajo tmp_path, como haría `_user_config_path()`."""
    return str(tmp_path / "cfg" / "Config.ini")


def test_sin_fichero_activa_es_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    assert sesion_remota.activa() is None


def test_dentro_de_marcar_activa_devuelve_datos(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    with sesion_remota.marcar("benchmark pc oficina"):
        datos = sesion_remota.activa()
        assert datos is not None
        assert datos["motivo"] == "benchmark pc oficina"
        assert datos["host"] == socket.gethostname()
        assert datos["pid"] == os.getpid()


def test_al_salir_del_context_manager_se_limpia(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    with sesion_remota.marcar("x"):
        ruta = sesion_remota.ruta_lock()
        assert os.path.exists(ruta)
    assert sesion_remota.activa() is None
    assert not os.path.exists(ruta)


def test_lock_con_mtime_antiguo_esta_caducado(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    with sesion_remota.marcar("x"):
        ruta = sesion_remota.ruta_lock()
        antiguo = sesion_remota.dt.datetime.now().timestamp() - 500
        os.utime(ruta, (antiguo, antiguo))
        assert sesion_remota.activa() is None


def test_json_corrupto_no_lanza(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    ruta = sesion_remota.ruta_lock()
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("no-soy-json")
    assert sesion_remota.activa() is None


def test_excepcion_en_el_bloque_se_propaga_y_limpia(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    ruta = sesion_remota.ruta_lock()

    class ErrorDePrueba(Exception):
        pass

    try:
        with sesion_remota.marcar("x"):
            raise ErrorDePrueba("boom")
    except ErrorDePrueba:
        pass
    else:
        raise AssertionError("la excepción debía propagarse")

    assert not os.path.exists(ruta)
    assert sesion_remota.activa() is None


def test_latido_refresca_el_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    monkeypatch.setattr(sesion_remota, "LATIDO_S", 0.05)

    with sesion_remota.marcar("x"):
        ruta = sesion_remota.ruta_lock()
        mtime_inicial = os.path.getmtime(ruta)
        # dejamos pasar varios latidos
        import time

        time.sleep(0.3)
        assert os.path.getmtime(ruta) > mtime_inicial


def test_marcar_anidado_no_corrompe_ni_deja_hilos_huerfanos(tmp_path, monkeypatch):
    monkeypatch.setattr(sesion_remota, "_user_config_path", lambda: _config_ini_en(tmp_path))
    hilos_antes = set(t.ident for t in sesion_remota.threading.enumerate())

    with sesion_remota.marcar("externo"):
        with sesion_remota.marcar("interno"):
            assert sesion_remota.activa() is not None

    assert sesion_remota.activa() is None
    hilos_despues = set(t.ident for t in sesion_remota.threading.enumerate())
    assert hilos_despues == hilos_antes
