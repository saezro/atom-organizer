from datetime import datetime

from atom_core import validacion_vuelos


def _vuelo(pb="1", num_vuelo="1", fecha="2026-09-20", inicio="09:00:00", final="09:20:00", **extra):
    d = {
        "fecha": fecha, "piloto": "PilotoA", "equipo_vuelo": "M300",
        "pb": pb, "num_vuelo": num_vuelo, "hora_inicio": inicio, "hora_fin": final,
    }
    d.update(extra)
    return d


def _dt(h, m, s=0, dia=20):
    return datetime(2026, 9, dia, h, m, s)


def test_todo_ok():
    vuelos = [_vuelo(num_vuelo="1", inicio="09:00:00", final="09:20:00")]
    fotos = [_dt(9, 0, 5), _dt(9, 10, 0), _dt(9, 19, 55)]

    res = validacion_vuelos.validar(vuelos, fotos)

    assert res["ok"] is True
    assert res["pendiente"] is False
    assert res["fotos_fuera"] == 0
    assert res["avisos"] == []
    assert len(res["vuelos"]) == 1
    v = res["vuelos"][0]
    assert v["fotos"] == 3
    assert v["estado"] == "ok"
    assert v["nombre"]
    assert v["id"]


def test_sin_fotos():
    vuelos = [_vuelo(num_vuelo="1", inicio="09:00:00", final="09:20:00")]
    fotos = [_dt(12, 0, 0)]  # ninguna cae en el vuelo

    res = validacion_vuelos.validar(vuelos, fotos)

    assert res["ok"] is False
    assert res["vuelos"][0]["estado"] == "sin_fotos"
    assert res["vuelos"][0]["fotos"] == 0
    assert res["fotos_fuera"] == 1
    assert any("sin_fotos" not in a and "no se ha encontrado ninguna foto" in a.lower() for a in res["avisos"])


def test_pocas_fotos():
    vuelo = _vuelo(num_vuelo="1", inicio="09:00:00", final="09:20:00")
    vuelo["esperado"] = 10
    fotos = [_dt(9, 5, 0), _dt(9, 6, 0)]  # 2 de 10 esperadas

    res = validacion_vuelos.validar([vuelo], fotos)

    assert res["ok"] is False
    v = res["vuelos"][0]
    assert v["estado"] == "pocas_fotos"
    assert v["fotos"] == 2
    assert v["esperado"] == 10
    assert any("2 fotos de 10 esperadas" in a for a in res["avisos"])


def test_pocas_fotos_no_dispara_si_supera_el_umbral():
    vuelo = _vuelo(num_vuelo="1", inicio="09:00:00", final="09:20:00")
    vuelo["esperado"] = 10
    fotos = [_dt(9, 1, i) for i in range(9)]  # 9 de 10 = 90%, justo en el umbral

    res = validacion_vuelos.validar([vuelo], fotos)

    assert res["vuelos"][0]["estado"] == "ok"


def test_solapado():
    vuelos = [
        _vuelo(num_vuelo="1", inicio="09:00:00", final="09:20:00"),
        _vuelo(num_vuelo="2", inicio="09:19:00", final="09:40:00"),
    ]
    # foto entre 09:19 y 09:20 cae en el margen de ambos vuelos (margen 120s)
    fotos = [_dt(9, 19, 30)]

    res = validacion_vuelos.validar(vuelos, fotos, margen_s=120)

    estados = {v["id"]: v["estado"] for v in res["vuelos"]}
    assert set(estados.values()) == {"solapado"}
    assert res["ok"] is False


def test_fotos_fuera():
    vuelos = [_vuelo(num_vuelo="1", inicio="09:00:00", final="09:20:00")]
    fotos = [_dt(9, 5, 0), _dt(14, 0, 0), _dt(15, 0, 0)]

    res = validacion_vuelos.validar(vuelos, fotos)

    assert res["fotos_fuera"] == 2
    assert any("2 fotos fuera" in a for a in res["avisos"])


def test_zona_horaria_iso_con_offset_vs_exif_local():
    # El estadillo puede mandar horas con offset +02:00 (verano en Madrid);
    # el EXIF es naive (hora local de la camara, sin zona). Ambos deben caer
    # en la misma ventana una vez normalizados a Madrid naive.
    vuelos = [_vuelo(num_vuelo="1", inicio="09:00:00", final="09:20:00")]
    fotos = ["2026-09-20T07:05:00+00:00"]  # 09:05 en Madrid (verano, +02:00)

    res = validacion_vuelos.validar(vuelos, fotos)

    assert res["vuelos"][0]["fotos"] == 1
    assert res["vuelos"][0]["estado"] == "ok"


def test_inicio_y_fin_directos_como_datetime():
    vuelos = [{"id": "v1", "nombre": "Vuelo 1", "inicio": _dt(9, 0), "fin": _dt(9, 20)}]
    fotos = [_dt(9, 10)]

    res = validacion_vuelos.validar(vuelos, fotos)

    assert res["vuelos"][0]["id"] == "v1"
    assert res["vuelos"][0]["fotos"] == 1


def test_vuelos_vacios_sin_fotos_da_ok():
    res = validacion_vuelos.validar([], [])

    assert res["ok"] is True
    assert res["vuelos"] == []
    assert res["fotos_fuera"] == 0
