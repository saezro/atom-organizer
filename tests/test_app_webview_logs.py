"""Historial de procesos (`Api.logs_listar` / `Api.logs_leer` / `Api.logs_carpeta`).

Cada log de corrida es el que ya escribe `atom_core.organize.run_task` en
`user_log_dir()`: cabecera `[run] ... ctx={...}`, luego `[plant]`/`[error]`/
`[done]`. Aquí se construyen a mano ficheros con ese formato para probar el
parseo sin depender de una corrida real.
"""
from __future__ import annotations

import external_tools
from app_webview import Api


def _api(monkeypatch, carpeta):
    monkeypatch.setattr(external_tools, "user_log_dir", lambda: str(carpeta), raising=False)
    return Api()


def _escribir_log(carpeta, nombre, lineas):
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / nombre).write_text("\n".join(lineas) + "\n", encoding="utf-8")


def test_logs_listar_parsea_cabecera_y_estado_ok(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    _escribir_log(
        carpeta,
        "atom-organizer-run_20260101_120000_pid111.log",
        [
            "[run] version=3.5.0 pid=111 task=gen_struct_folder "
            "ctx={'origen': '/a/ORIGEN', 'destino': '/a/PLANTA_X', 'estadillo': ''}",
            "[params] {}",
            "[advanced] (ninguno: se usan los defaults del backend)",
            "[plant] PLANTA_X",
            "[log] arrancando",
            "[done] {'status': 'ok', 'errors': 0, 'warnings': 0, 'elapsed': 12.3, 'last': None}",
        ],
    )
    api = _api(monkeypatch, carpeta)

    res = api.logs_listar()

    assert res["ok"] is True
    assert len(res["runs"]) == 1
    run = res["runs"][0]
    assert run["nombre"] == "atom-organizer-run_20260101_120000_pid111.log"
    assert run["fecha"].startswith("2026-01-01T12:00:00")
    assert run["planta"] == "PLANTA_X"
    assert run["task"] == "gen_struct_folder"
    assert run["origen"] == "/a/ORIGEN"
    assert run["destino"] == "/a/PLANTA_X"
    assert run["version"] == "3.5.0"
    assert run["errores"] == 0
    assert run["estado"] == "ok"
    assert run["duracion"] == 12.3


def test_logs_listar_sin_plant_usa_basename_del_destino(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    _escribir_log(
        carpeta,
        "atom-organizer-run_20260102_090000_pid222.log",
        [
            "[run] version=3.5.0 pid=222 task=split_images "
            "ctx={'origen': '/a/ORIGEN', 'destino': '/mnt/PLANTA_Y', 'estadillo': ''}",
            "[done] {'status': 'ok', 'errors': 0, 'warnings': 0, 'elapsed': 1.0, 'last': None}",
        ],
    )
    api = _api(monkeypatch, carpeta)

    run = api.logs_listar()["runs"][0]

    assert run["planta"] == "PLANTA_Y"


def test_logs_listar_estado_error_y_conteo(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    _escribir_log(
        carpeta,
        "atom-organizer-run_20260103_100000_pid333.log",
        [
            "[run] version=3.5.0 pid=333 task=split_images ctx={'origen': '/a', 'destino': '/b'}",
            "[plant] PLANTA_Z",
            "[error] fallo 1",
            "[error] fallo 2",
            "[done] {'status': 'errors', 'errors': 2, 'warnings': 0, 'elapsed': 3.0, 'last': None}",
        ],
    )
    api = _api(monkeypatch, carpeta)

    run = api.logs_listar()["runs"][0]

    assert run["estado"] == "error"
    assert run["errores"] == 2


def test_logs_listar_estado_incompleto_sin_done_ni_error(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    _escribir_log(
        carpeta,
        "atom-organizer-run_20260104_100000_pid444.log",
        [
            "[run] version=3.5.0 pid=444 task=split_images ctx={'origen': '/a', 'destino': '/b'}",
            "[plant] PLANTA_W",
            "[log] a medias, el proceso se cortó aquí",
        ],
    )
    api = _api(monkeypatch, carpeta)

    run = api.logs_listar()["runs"][0]

    assert run["estado"] == "incompleto"
    assert run["duracion"] is None


def test_logs_listar_ordena_por_fecha_descendente(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    for ts, pid in (("20260101_100000", "1"), ("20260103_100000", "3"), ("20260102_100000", "2")):
        _escribir_log(
            carpeta,
            f"atom-organizer-run_{ts}_pid{pid}.log",
            [f"[run] version=3.5.0 pid={pid} task=split_images ctx={{'origen': '/a', 'destino': '/b'}}"],
        )
    api = _api(monkeypatch, carpeta)

    runs = api.logs_listar()["runs"]

    assert [r["nombre"] for r in runs] == [
        "atom-organizer-run_20260103_100000_pid3.log",
        "atom-organizer-run_20260102_100000_pid2.log",
        "atom-organizer-run_20260101_100000_pid1.log",
    ]


def test_logs_listar_tolera_fichero_corrupto(monkeypatch, tmp_path):
    """Un `.log` con nombre válido pero contenido raro no debe tirar el listado."""
    carpeta = tmp_path / "Logs"
    carpeta.mkdir(parents=True)
    # Cabecera con ctx no parseable: no debe petar, solo dejar los campos vacíos.
    _escribir_log(
        carpeta,
        "atom-organizer-run_20260105_100000_pid555.log",
        ["[run] version=3.5.0 pid=555 task=split_images ctx={esto no es un dict valido"],
    )
    api = _api(monkeypatch, carpeta)

    res = api.logs_listar()

    assert res["ok"] is True
    assert len(res["runs"]) == 1


def test_logs_listar_carpeta_inexistente_no_peta(monkeypatch, tmp_path):
    api = _api(monkeypatch, tmp_path / "no-existe")

    res = api.logs_listar()

    assert res == {"ok": True, "runs": []}


def test_logs_leer_devuelve_el_contenido(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    nombre = "atom-organizer-run_20260101_120000_pid1.log"
    _escribir_log(carpeta, nombre, ["[run] version=3.5.0 pid=1 task=x ctx={}", "[log] hola"])
    api = _api(monkeypatch, carpeta)

    res = api.logs_leer(nombre)

    assert res["ok"] is True
    assert "hola" in res["texto"]
    assert res["truncado"] is False


def test_logs_leer_trunca_ficheros_grandes(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    carpeta.mkdir(parents=True)
    nombre = "atom-organizer-run_20260101_120000_pid1.log"
    # > 1 MB: cada línea ~20 bytes, 100000 líneas de sobra.
    (carpeta / nombre).write_text(
        "\n".join(f"[log] linea {i}" for i in range(100000)) + "\n[log] ULTIMA-LINEA\n",
        encoding="utf-8",
    )
    api = _api(monkeypatch, carpeta)

    res = api.logs_leer(nombre)

    assert res["ok"] is True
    assert res["truncado"] is True
    assert "ULTIMA-LINEA" in res["texto"]


def test_logs_leer_rechaza_nombre_con_puntos_dobles(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    carpeta.mkdir(parents=True)
    (tmp_path / "secreto.txt").write_text("no deberia poder leerse", encoding="utf-8")
    api = _api(monkeypatch, carpeta)

    res = api.logs_leer("../secreto.txt")

    assert res["ok"] is False


def test_logs_leer_rechaza_nombre_con_separador_de_ruta(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    carpeta.mkdir(parents=True)
    api = _api(monkeypatch, carpeta)

    res_slash = api.logs_leer("subcarpeta/atom-organizer-run_x.log")
    res_absoluta = api.logs_leer("/etc/passwd")

    assert res_slash["ok"] is False
    assert res_absoluta["ok"] is False


def test_logs_leer_nombre_inexistente(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    carpeta.mkdir(parents=True)
    api = _api(monkeypatch, carpeta)

    res = api.logs_leer("atom-organizer-run_no_existe_pid1.log")

    assert res["ok"] is False


def test_logs_carpeta_devuelve_la_ruta(monkeypatch, tmp_path):
    carpeta = tmp_path / "Logs"
    api = _api(monkeypatch, carpeta)

    res = api.logs_carpeta()

    assert res["ok"] is True
    assert res["ruta"] == str(carpeta)
