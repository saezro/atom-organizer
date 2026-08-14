"""Fusión de N estadillos: `--estadillo` (CLI) admite varias rutas, se
fusionan EN EL ORDEN dado, y solo cuando el mismo PB+Vuelo aparece con fecha
distinta la carpeta lleva sufijo de fecha. Con 1 sola ruta todo tiene que
comportarse exactamente igual que antes.

Cadena cubierta:
- `atom_core.estadillo.empaquetar_rutas` / `desempaquetar_rutas` (codificación
  de N rutas en el único string que atraviesa `GenStructFolderConfig.estad`).
- `atom_core.estadillo.combinar_estadillos` (fusión + validación de cabeceras).
- `atom_core.estadillo.detectar_colisiones_pb_vuelo`.
- `pipeline.GenStructFolder.gen_folder_struct` end-to-end (carpetas resultantes).
- `organize_cli` (`--estadillo` nargs='+' -> params["estadillo"]).
"""
import os

import pandas as pd
import pytest

import utils
from atom_core import estadillo


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_gsf(tmp_path):
    logger = utils.OrganizerLogger("test_estadillo_multi", log_dir=str(tmp_path / "Logs"),
                                    create_file_handler=False)
    from pipeline import GenStructFolder
    return GenStructFolder(logger)


def _csv(path, filas, columnas=("PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final")):
    lineas = [";".join(columnas)]
    lineas += [";".join(str(c) for c in fila) for fila in filas]
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(path)


# --- atom_core.estadillo: empaquetar/desempaquetar --------------------------

def test_empaquetar_1_sola_ruta_no_mete_separador():
    """El caso de siempre (1 estadillo): el string resultante es la ruta tal
    cual, sin el separador `\\x1f` de por medio."""
    assert estadillo.empaquetar_rutas(["/x/e.csv"]) == "/x/e.csv"


def test_desempaquetar_1_sola_ruta_sin_separador():
    """Un string normal (como el que produce SIEMPRE `--estadillo` con 1
    fichero) vuelve como lista de 1 elemento, tal cual."""
    assert estadillo.desempaquetar_rutas("/x/e.csv") == ["/x/e.csv"]


def test_empaquetar_desempaquetar_n_rutas_son_inversas():
    rutas = ["/a/e1.csv", "/b/e2.csv", "/c/e3.csv"]
    assert estadillo.desempaquetar_rutas(estadillo.empaquetar_rutas(rutas)) == rutas


def test_desempaquetar_vacio():
    assert estadillo.desempaquetar_rutas("") == []
    assert estadillo.desempaquetar_rutas(None) == []


# --- atom_core.estadillo.combinar_estadillos ---------------------------------

def test_combinar_1_estadillo_es_el_mismo_dataframe_con_columna_origen(tmp_path):
    ruta = _csv(tmp_path / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    combinado = estadillo.combinar_estadillos([ruta])
    assert len(combinado) == 1
    assert combinado.iloc[0]["PB"] == 1
    assert combinado[estadillo.COLUMNA_ORIGEN].tolist() == ["e.csv"]


def test_combinar_2_estadillos_respeta_el_orden_de_los_ficheros(tmp_path):
    """El orden importa (desempate de solapes): la fila del primer fichero
    tiene que quedar antes que la del segundo en el DataFrame fusionado."""
    r1 = _csv(tmp_path / "e1.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    r2 = _csv(tmp_path / "e2.csv", [("2", "1", "2026:03:18", "11:00:00", "11:05:00")])

    combinado = estadillo.combinar_estadillos([r1, r2])

    assert len(combinado) == 2
    assert combinado.iloc[0]["PB"] == 1
    assert combinado.iloc[1]["PB"] == 2
    assert combinado[estadillo.COLUMNA_ORIGEN].tolist() == ["e1.csv", "e2.csv"]


def test_combinar_cabeceras_incompatibles_lanza_error_claro(tmp_path):
    """Falta una columna ESENCIAL (Hora_final) en e2.csv: eso sí debe cortar
    la fusión, con el fichero y la columna en el mensaje. e1.csv trae todas
    las esenciales y no aparece señalado (el chequeo ya no es "cabeceras
    idénticas entre sí", es "cada fichero trae lo mínimo que exige el
    pipeline")."""
    r1 = _csv(tmp_path / "e1.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    # Sin "Hora_final": le falta una columna esencial.
    r2 = tmp_path / "e2.csv"
    r2.write_text("PB;Vuelo;Fecha;Hora_de_inicio\n1;1;2026:03:18;11:00:00\n", encoding="utf-8")

    with pytest.raises(estadillo.EstadilloHeaderError) as excinfo:
        estadillo.combinar_estadillos([r1, str(r2)])

    mensaje = str(excinfo.value)
    # El error tiene que decir QUÉ fichero y QUÉ columna falta, no solo "error".
    assert "e2.csv" in mensaje
    assert "Hora_final" in mensaje


# --- atom_core.estadillo.detectar_colisiones_pb_vuelo ------------------------

def test_sin_colision_pb_vuelo_no_repetido():
    df = pd.DataFrame({
        "PB": [1, 2], "Vuelo": [1, 1],
        "Fecha": ["2026:03:17", "2026:03:18"],
    })
    cols = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha"}
    assert estadillo.detectar_colisiones_pb_vuelo(df, cols) == {}


def test_sin_colision_pb_vuelo_repetido_pero_misma_fecha():
    """Mismo PB+Vuelo, misma fecha (p. ej. estadillo duplicado o reprocesado):
    NO es una colisión real, es la misma franja."""
    df = pd.DataFrame({
        "PB": [1, 1], "Vuelo": [1, 1],
        "Fecha": ["2026:03:17", "2026:03:17"],
    })
    cols = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha"}
    assert estadillo.detectar_colisiones_pb_vuelo(df, cols) == {}


def test_colision_pb_vuelo_con_fecha_distinta():
    df = pd.DataFrame({
        "PB": [1, 1], "Vuelo": [1, 1],
        "Fecha": ["2026:03:17", "2026:03:18"],
    })
    cols = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha"}
    colisiones = estadillo.detectar_colisiones_pb_vuelo(df, cols)
    assert ("1", "1") in colisiones
    assert colisiones[("1", "1")] == ["2026:03:17", "2026:03:18"]


def test_sufijo_fecha_formato_estandar():
    assert estadillo.sufijo_fecha("2026:03:17") == "20260317"


def test_sufijo_fecha_formato_raro_no_revienta():
    assert estadillo.sufijo_fecha("no-es-una-fecha") == "noesunafecha"


# --- pipeline.GenStructFolder.gen_folder_struct end-to-end -------------------

def test_gen_folder_struct_1_estadillo_sin_cambios(tmp_path):
    """(a) Retrocompatibilidad: 1 sola ruta, tal cual desempaquetar_rutas la
    deja pasar, se comporta exactamente como el pipeline de siempre."""
    root = tmp_path / "destino"
    (root / "RGB").mkdir(parents=True)
    (root / "TERMICA").mkdir(parents=True)

    est = _csv(tmp_path / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    gsf = _make_gsf(tmp_path)
    cb = FakeSignal()

    gsf.gen_folder_struct(est, str(root), str(root), False, 0, 0, 0, cb, cb)

    assert os.path.isdir(root / "RGB" / "PB1" / "PB1_V1")
    assert os.path.isdir(root / "TERMICA" / "PB1" / "PB1_V1")
    # Nombre de carpeta SIN sufijo de fecha: el caso de 1 estadillo no cambia.
    assert sorted(os.listdir(root / "RGB" / "PB1")) == ["PB1_V1"]


def test_gen_folder_struct_2_estadillos_sin_colision_no_lleva_sufijo(tmp_path):
    """(b) Dos estadillos con PB+Vuelo distintos entre sí: carpetas con el
    nombre de siempre, sin sufijo de fecha."""
    root = tmp_path / "destino"
    (root / "RGB").mkdir(parents=True)
    (root / "TERMICA").mkdir(parents=True)

    e1 = _csv(tmp_path / "e1.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    e2 = _csv(tmp_path / "e2.csv", [("2", "1", "2026:03:18", "11:00:00", "11:05:00")])
    path_estadillo = estadillo.empaquetar_rutas([e1, e2])

    gsf = _make_gsf(tmp_path)
    cb = FakeSignal()

    gsf.gen_folder_struct(path_estadillo, str(root), str(root), False, 0, 0, 0, cb, cb)

    assert os.path.isdir(root / "RGB" / "PB1" / "PB1_V1")
    assert os.path.isdir(root / "RGB" / "PB2" / "PB2_V1")
    assert sorted(os.listdir(root / "RGB" / "PB1")) == ["PB1_V1"]
    assert sorted(os.listdir(root / "RGB" / "PB2")) == ["PB2_V1"]
    # Copia de ambos estadillos en ESTADILLOS/, uno por fichero de origen.
    assert sorted(os.listdir(root / "ESTADILLOS")) == ["e1.csv", "e2.csv"]


def test_gen_folder_struct_2_estadillos_con_colision_sufijo_solo_ahi(tmp_path):
    """(c) Mismo PB+Vuelo en los dos estadillos, con fecha distinta: SOLO esa
    carpeta lleva el sufijo de fecha; un tercer vuelo sin colisión (mismo PB,
    Vuelo distinto) conserva el nombre de siempre."""
    root = tmp_path / "destino"
    (root / "RGB").mkdir(parents=True)
    (root / "TERMICA").mkdir(parents=True)

    e1 = _csv(tmp_path / "e1.csv", [
        ("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
        ("1", "2", "2026:03:17", "12:00:00", "12:05:00"),
    ])
    e2 = _csv(tmp_path / "e2.csv", [
        ("1", "1", "2026:03:18", "10:00:00", "10:05:00"),
    ])
    path_estadillo = estadillo.empaquetar_rutas([e1, e2])

    gsf = _make_gsf(tmp_path)
    cb = FakeSignal()

    gsf.gen_folder_struct(path_estadillo, str(root), str(root), False, 0, 0, 0, cb, cb)

    carpetas_pb1 = sorted(os.listdir(root / "RGB" / "PB1"))
    # PB1_V1 colisiona (10:00 del 17 y 10:00 del 18) -> sufijo en AMBAS filas
    # colisionadas, distinguibles por fecha. PB1_V2 no colisiona -> sin sufijo.
    assert carpetas_pb1 == ["PB1_V1_20260317", "PB1_V1_20260318", "PB1_V2"], carpetas_pb1


def test_gen_folder_struct_cabeceras_incompatibles_propaga_error(tmp_path):
    """(d) Cabeceras incompatibles entre los estadillos fusionados: el error
    sube tal cual, no se traga en silencio ni fusiona con NaN."""
    root = tmp_path / "destino"
    (root / "RGB").mkdir(parents=True)
    (root / "TERMICA").mkdir(parents=True)

    e1 = _csv(tmp_path / "e1.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    e2 = tmp_path / "e2.csv"
    e2.write_text("PB;Vuelo;Fecha;Hora_de_inicio\n1;1;2026:03:18;11:00:00\n", encoding="utf-8")
    path_estadillo = estadillo.empaquetar_rutas([e1, str(e2)])

    gsf = _make_gsf(tmp_path)
    cb = FakeSignal()

    with pytest.raises(estadillo.EstadilloHeaderError):
        gsf.gen_folder_struct(path_estadillo, str(root), str(root), False, 0, 0, 0, cb, cb)


# --- atom_core.estadillo.read_estadillo_info: sigue funcionando con N --------

def test_read_estadillo_info_1_ruta_string_sigue_igual(tmp_path):
    ruta = _csv(tmp_path / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    info = estadillo.read_estadillo_info(ruta)
    assert "error" not in info
    assert info["num_vuelos"] == 1
    assert info["vuelos"][0]["pb"] == "1"


def test_read_estadillo_info_n_rutas_agrega_info(tmp_path):
    e1 = _csv(tmp_path / "e1.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    e2 = _csv(tmp_path / "e2.csv", [("2", "1", "2026:03:18", "11:00:00", "11:05:00")])

    info = estadillo.read_estadillo_info([e1, e2])

    assert "error" not in info
    assert info["num_vuelos"] == 2
    assert [v["pb"] for v in info["vuelos"]] == ["1", "2"]
    assert info["fechas"] == ["2026:03:17", "2026:03:18"]


def test_read_estadillo_info_n_rutas_cabeceras_incompatibles_da_error_legible(tmp_path):
    e1 = _csv(tmp_path / "e1.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    e2 = tmp_path / "e2.csv"
    e2.write_text("PB;Vuelo;Fecha;Hora_de_inicio\n1;1;2026:03:18;11:00:00\n", encoding="utf-8")

    info = estadillo.read_estadillo_info([e1, str(e2)])

    assert "error" in info
    assert "e2.csv" in info["error"]


# --- organize_cli: --estadillo admite N rutas --------------------------------

def test_cli_estadillo_1_ruta_no_cambia_params(monkeypatch, tmp_path):
    """(a) Retrocompatibilidad del CLI: 1 sola ruta llega a params["estadillo"]
    tal cual (sin separador de por medio)."""
    import organize_cli
    from atom_core import organize

    capturado = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        capturado.update(params)
        return {"status": "ok"}

    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    origen = tmp_path / "in"
    origen.mkdir()
    destino = tmp_path / "out"
    est = tmp_path / "e.csv"
    est.write_text("")

    organize_cli.main([
        "--origen", str(origen), "--destino", str(destino),
        "--estadillo", str(est), "--quiet", "--json",
    ])

    assert capturado.get("estadillo") == str(est)


def test_cli_estadillo_n_rutas_se_empaquetan_en_params(monkeypatch, tmp_path):
    import organize_cli
    from atom_core import organize

    capturado = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        capturado.update(params)
        return {"status": "ok"}

    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    origen = tmp_path / "in"
    origen.mkdir()
    destino = tmp_path / "out"
    e1 = tmp_path / "e1.csv"
    e2 = tmp_path / "e2.csv"
    e1.write_text("")
    e2.write_text("")

    organize_cli.main([
        "--origen", str(origen), "--destino", str(destino),
        "--estadillo", str(e1), str(e2), "--quiet", "--json",
    ])

    assert capturado.get("estadillo") == estadillo.empaquetar_rutas([str(e1), str(e2)])
    assert estadillo.desempaquetar_rutas(capturado["estadillo"]) == [str(e1), str(e2)]


def test_cli_estadillo_ruta_inexistente_falla_para_las_n(monkeypatch, tmp_path, capsys):
    import organize_cli
    from atom_core import organize

    monkeypatch.setattr(organize, "run_task", lambda *a, **k: {"status": "ok"}, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", lambda *a, **k: {"status": "ok"}, raising=False)

    origen = tmp_path / "in"
    origen.mkdir()
    destino = tmp_path / "out"
    e1 = tmp_path / "e1.csv"
    e1.write_text("")

    codigo = organize_cli.main([
        "--origen", str(origen), "--destino", str(destino),
        "--estadillo", str(e1), str(tmp_path / "no_existe.csv"), "--quiet", "--json",
    ])

    assert codigo == 2
    assert "no_existe.csv" in capsys.readouterr().err
