"""El estadillo recibido por LAN ("Estadillo Digital") se queda en
`estadillos_recibidos_dir()` hasta que se empieza a organizar. En ese
momento (`Api._mover_estadillo_espera_si_toca`, llamado desde
`_run_task_worker` justo antes de `atom_core.organize.run_task` cuando
`task == "split_images"`) se mueve a la carpeta que se va a analizar, con
su mismo nombre -la convención que usa `detectar_estadillos`-, sin
sobrescribir, y sin bloquear el run si algo falla.
"""
import datetime

import app_webview
from atom_core import estadillo as estadillo_mod

_CABECERA_ESTADILLO = (
    "PB;Vuelo;Fecha;Trabajo;Piloto;Hora_de_inicio;Hora_final\n"
    "1;1;2026:09:20;PLANTA_A;PilotoA;09:00:00;09:20:00\n"
)


def _api():
    return app_webview.Api()


# ---- atom_core.estadillo.mover_estadillo_recibido_a_carpeta ---------------

def test_mover_mueve_el_fichero_con_su_mismo_nombre(tmp_path):
    origen_dir = tmp_path / "estadillos_recibidos"
    origen_dir.mkdir()
    fichero = origen_dir / "20260920_estadillo_PilotoA.csv"
    fichero.write_text("PB;Vuelo\n1;1\n", encoding="utf-8")
    destino_dir = tmp_path / "fotos"
    destino_dir.mkdir()

    nueva = estadillo_mod.mover_estadillo_recibido_a_carpeta(str(fichero), str(destino_dir))

    assert nueva == str(destino_dir / "20260920_estadillo_PilotoA.csv")
    assert not fichero.exists()
    assert (destino_dir / "20260920_estadillo_PilotoA.csv").read_text(encoding="utf-8").startswith("PB;Vuelo")


def test_mover_no_sobrescribe_anade_sufijo(tmp_path):
    origen_dir = tmp_path / "estadillos_recibidos"
    origen_dir.mkdir()
    fichero = origen_dir / "20260920_estadillo_PilotoA.csv"
    fichero.write_text("nuevo", encoding="utf-8")
    destino_dir = tmp_path / "fotos"
    destino_dir.mkdir()
    ya_existe = destino_dir / "20260920_estadillo_PilotoA.csv"
    ya_existe.write_text("viejo", encoding="utf-8")

    nueva = estadillo_mod.mover_estadillo_recibido_a_carpeta(str(fichero), str(destino_dir))

    assert nueva == str(destino_dir / "20260920_estadillo_PilotoA_1.csv")
    assert ya_existe.read_text(encoding="utf-8") == "viejo"
    assert (destino_dir / "20260920_estadillo_PilotoA_1.csv").read_text(encoding="utf-8") == "nuevo"


def test_mover_ruta_inexistente_no_lanza_devuelve_none(tmp_path):
    destino_dir = tmp_path / "fotos"
    destino_dir.mkdir()
    assert estadillo_mod.mover_estadillo_recibido_a_carpeta(
        str(tmp_path / "no_existe.csv"), str(destino_dir)) is None


def test_mover_carpeta_destino_inexistente_no_lanza_devuelve_none(tmp_path):
    fichero = tmp_path / "algo.csv"
    fichero.write_text("x", encoding="utf-8")
    assert estadillo_mod.mover_estadillo_recibido_a_carpeta(
        str(fichero), str(tmp_path / "no_existe")) is None


# ---- Api._mover_estadillo_espera_si_toca -----------------------------------

def _preparar_espera_recibida(api, carpeta, ruta_csv):
    api._estadillo_espera = {
        "carpeta": carpeta,
        "recibido": True,
        "rutas": [ruta_csv],
        "inspeccion": {}, "fotos": {}, "errores": [],
        "caduca_en": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10),
    }


def test_mueve_el_estadillo_de_la_espera_al_origen_del_run(tmp_path):
    api = _api()
    origen = tmp_path / "fotos"
    origen.mkdir()
    recibidos = tmp_path / "estadillos_recibidos"
    recibidos.mkdir()
    csv = recibidos / "20260920_estadillo_PilotoA.csv"
    csv.write_text(_CABECERA_ESTADILLO, encoding="utf-8")

    _preparar_espera_recibida(api, str(origen), str(csv))
    params = {"origen": str(origen), "estadillo": str(csv)}

    nuevos = api._mover_estadillo_espera_si_toca(params)

    nueva_ruta = str(origen / "20260920_estadillo_PilotoA.csv")
    assert not csv.exists()
    assert (origen / "20260920_estadillo_PilotoA.csv").exists()
    assert nuevos["estadillo"] == nueva_ruta
    assert api._estadillo_espera["rutas"] == [nueva_ruta]
    # El estadillo detectado ahora en `origen` coincide con el nuevo path.
    assert estadillo_mod.detectar_estadillos(str(origen))["rutas"] == [nueva_ruta]


def test_nunca_mueve_antes_de_pasar_por_el_hook(tmp_path):
    """Sin llamar al hook, el fichero se queda donde `estadillos_recibidos_dir`
    lo dejo: mover "antes" (al iniciar la espera o al recibirlo) esta
    prohibido, solo se mueve en `_mover_estadillo_espera_si_toca`."""
    api = _api()
    origen = tmp_path / "fotos"
    origen.mkdir()
    recibidos = tmp_path / "estadillos_recibidos"
    recibidos.mkdir()
    csv = recibidos / "20260920_estadillo_PilotoA.csv"
    csv.write_text("PB;Vuelo\n", encoding="utf-8")

    _preparar_espera_recibida(api, str(origen), str(csv))

    # Solo comprobar estado/leer, nunca mover: el fichero sigue en su sitio.
    api.estadillo_espera_estado()
    api._estadillo_en_carpeta(str(origen))
    assert csv.exists()
    assert not (origen / "20260920_estadillo_PilotoA.csv").exists()


def test_no_mueve_si_la_carpeta_de_la_espera_no_es_la_de_este_run(tmp_path):
    api = _api()
    origen_run = tmp_path / "otra_carpeta"
    origen_run.mkdir()
    carpeta_espera = tmp_path / "fotos"
    carpeta_espera.mkdir()
    recibidos = tmp_path / "estadillos_recibidos"
    recibidos.mkdir()
    csv = recibidos / "20260920_estadillo_PilotoA.csv"
    csv.write_text("PB;Vuelo\n", encoding="utf-8")

    _preparar_espera_recibida(api, str(carpeta_espera), str(csv))
    params = {"origen": str(origen_run), "estadillo": str(csv)}

    nuevos = api._mover_estadillo_espera_si_toca(params)

    assert csv.exists()  # no se toco
    assert nuevos == params


def test_no_mueve_si_no_hay_espera_activa(tmp_path):
    api = _api()
    origen = tmp_path / "fotos"
    origen.mkdir()
    params = {"origen": str(origen), "estadillo": ""}
    assert api._mover_estadillo_espera_si_toca(params) == params


def test_no_mueve_si_no_esta_recibido_todavia(tmp_path):
    api = _api()
    origen = tmp_path / "fotos"
    origen.mkdir()
    api._estadillo_espera = {
        "carpeta": str(origen), "recibido": False, "rutas": [],
        "inspeccion": {}, "fotos": {}, "errores": [],
    }
    params = {"origen": str(origen), "estadillo": ""}
    assert api._mover_estadillo_espera_si_toca(params) == params


def test_fallo_al_mover_no_bloquea_el_run(tmp_path, monkeypatch, caplog):
    api = _api()
    origen = tmp_path / "fotos"
    origen.mkdir()
    csv = tmp_path / "no_existe.csv"  # no existe -> el move falla, devuelve None

    _preparar_espera_recibida(api, str(origen), str(csv))
    params = {"origen": str(origen), "estadillo": str(csv)}

    import logging
    with caplog.at_level(logging.WARNING):
        nuevos = api._mover_estadillo_espera_si_toca(params)

    assert nuevos == params  # el run sigue con los params tal cual
    assert any("estadillo" in r.message.lower() for r in caplog.records)


def test_no_toca_tasks_distintos_de_split_images(tmp_path, monkeypatch):
    """`_run_task_worker` solo llama al hook para `split_images`."""
    api = _api()
    llamadas = []
    monkeypatch.setattr(api, "_mover_estadillo_espera_si_toca",
                         lambda params: (llamadas.append(params), params)[1])
    monkeypatch.setattr("atom_core.organize.run_task", lambda *a, **k: None)

    api._run_task_worker("detect_suffixes", {"origen": str(tmp_path)}, None)
    assert llamadas == []

    api._run_task_worker("split_images", {"origen": str(tmp_path)}, None)
    assert len(llamadas) == 1
