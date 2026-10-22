"""AT141A / AT141B (2026-10-06): la fila «PB GENERALES» del estadillo tiene ventana
propia pero el margen `seconds_range` (30 s) del PB 1 anterior la invadía, y como el
orden del estadillo desempata, las fotos GENERALES se asignaban a PB1_V1.

Fixtures: los estadillos reales (`tests/fixtures/estadillos/`)."""
import datetime as dt
import os
import types

import pandas as pd
import pytest

import pipeline
from atom_core import indice
from atom_core.indice import _asignar_vuelo, _ventanas_por_vuelo

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "estadillos")
COLS = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha", "Hora_de_inicio": "Hora_de_inicio",
        "Hora_final": "Hora_final", "Equipo_de_vuelo": "Equipo_de_vuelo"}


class _Pipe:
    ventana_horaria_vuelo = pipeline.GenStructFolder.ventana_horaria_vuelo


def _ventanas(planta, margen=30.0):
    df = pd.read_csv(os.path.join(FIX, f"2026_10_06_{planta}.CSV"), sep=";", dtype=str)
    cfg = types.SimpleNamespace(seconds_range=margen, mismatch_hours=0, mismatch_minutes=0)
    mensajes = []
    cb = types.SimpleNamespace(emit=lambda p=None, *a, **k: mensajes.append(p))
    return _ventanas_por_vuelo(df, COLS, _Pipe(), cfg, cb), mensajes


def _dato(hhmmss):
    h, m, s = (int(x) for x in hhmmss.split(":"))
    return types.SimpleNamespace(timestamp=dt.datetime(2026, 10, 6, h, m, s))


@pytest.mark.parametrize("planta,hora_generales,hora_pb,hora_hueco", [
    ("AT141A", "13:51:25", "13:50:00", "13:51:16"),
    ("AT141B", "13:43:30", "13:42:00", "13:43:10"),
])
def test_foto_en_ventana_generales_va_a_generales(planta, hora_generales, hora_pb, hora_hueco):
    ventanas, mensajes = _ventanas(planta)
    assert [v["pb"] for v in ventanas] == ["1", "GENERALES"] and mensajes == []

    v = _asignar_vuelo(_dato(hora_generales), ventanas)
    assert v is not None and v["pb"] == "GENERALES"
    # Pegada al final del PB (dentro del margen de PB1 Y dentro de GENERALES): GENERALES.
    v = _asignar_vuelo(_dato(hora_generales[:-2] + "21"), ventanas)
    assert v["pb"] == "GENERALES"
    # Una foto claramente del PB sigue en el PB.
    assert _asignar_vuelo(_dato(hora_pb), ventanas)["pb"] == "1"


@pytest.mark.parametrize("planta,hora_hueco", [("AT141A", "13:51:16"), ("AT141B", "13:43:10")])
def test_foto_en_el_hueco_no_se_asigna_en_silencio(planta, hora_hueco):
    ventanas, _ = _ventanas(planta)
    assert _asignar_vuelo(_dato(hora_hueco), ventanas) is None


def test_sin_margen_la_foto_del_hueco_queda_sin_asignar():
    ventanas, _ = _ventanas("AT141A", margen=0.0)
    assert _asignar_vuelo(_dato("13:51:16"), ventanas) is None


def test_motor_legado_tambien_prioriza_generales():
    """`organizar_imagenes_en_vuelos` usa la misma regla (`elegir_ventana`)."""
    ventanas, _ = _ventanas("AT141A")
    legado = [{"inicio": v["inicio"], "fin": v["fin"], "inicio_exacto": v["inicio_exacto"],
               "fin_exacto": v["fin_exacto"], "destinos": {"TERMICA": v["pb"]},
               "generales": v["pb"] == "GENERALES"} for v in ventanas]
    assert indice.elegir_ventana(_dato("13:51:25").timestamp, legado)["destinos"]["TERMICA"] == "GENERALES"
    assert indice.elegir_ventana(_dato("13:50:00").timestamp, legado)["destinos"]["TERMICA"] == "1"


def test_cierre_no_emite_csv_de_generales(tmp_path, make_dji_jpeg, logger):
    """Emisión (cierre.emitir_csvs/_emitir_meta_location): las fotos GENERALES NO generan CSV
    (decisión de producto, 3.4.142) y tampoco aparecen en el CSV de PB1_V1."""
    import datetime as dt
    import os
    from types import SimpleNamespace

    from atom_core import cierre
    from atom_core.manifiesto import FilaManifiesto, Manifiesto

    class _Cb:
        def emit(self, *a, **k):
            pass

    base = dt.datetime(2026, 10, 6, 13, 50, 0)
    # (tipo, pb, vuelo, carpeta, nombre, segundos desde 13:50:00)
    imagenes = [
        ("TERMICA", "1", "1", ("TERMICA", "PB1", "PB1_V1"), "20261006_135000_DJI_0001_T.JPG", 0),
        ("RGB", "1", "1", ("RGB", "PB1", "PB1_V1"), "20261006_135000_DJI_0001_W.JPG", 0),
        ("TERMICA", "GENERALES", "AT141A", ("FOTOS_GENERALES",), "20261006_135138_DJI_0036_T.JPG", 98),
        ("RGB", "GENERALES", "AT141A", ("FOTOS_GENERALES",), "20261006_135138_DJI_0036_W.JPG", 98),
    ]
    raiz = tmp_path / "d"
    os.makedirs(raiz / ".organizado")
    (raiz / "CSVs").mkdir()
    m = Manifiesto(raiz / ".organizado" / "m.db")
    m.crear_esquema()
    filas = []
    for tipo, pb, vuelo, carpeta, nombre, seg in imagenes:
        ruta = raiz.joinpath(*carpeta, nombre)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        t = base + dt.timedelta(seconds=seg)
        make_dji_jpeg(str(ruta), lat=37.1, lon=-5.6, gimbal_yaw=0.0, gimbal_pitch=-90.0,
                      relative_altitude=50.0, dt_val=t)
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=t.isoformat(), modelo=None,
            pb=pb, vuelo=vuelo, nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=str(ruta), ruta_salida_crop=None,
            ruta_salida_tiff=None, unassigned=False, bytes_origen=os.path.getsize(ruta)))
    m.insertar_o_reabrir(filas)
    for fila in m.todas():
        m.marcar_hecha(fila["id"], "")

    cfg = SimpleNamespace(output_folder=str(raiz), include_v=True, flight_height=50.0,
                          calculate_proyected_distance=False, gen_meta_location=True)
    cierre.emitir_csvs(m, cfg, _Cb())

    csvs = raiz / "CSVs"
    leer = lambda n: (csvs / n).read_text()  # noqa: E731
    assert not list(csvs.glob("FOTOS_GENERALES*"))
    assert "DJI_0036" not in leer("PB1_V1_meta.csv") + leer("PB1_V1_location.csv")


def test_foto_10s_fuera_del_fin_del_pb_queda_sin_asignar_aunque_el_margen_sea_30():
    t0 = dt.datetime(2026, 10, 6, 10, 0, 0)
    ventanas = [{"pb": "1", "vuelo": "1", "inicio": t0, "fin": t0 + dt.timedelta(seconds=60),
                 "inicio_exacto": t0, "fin_exacto": t0 + dt.timedelta(seconds=60)}]
    assert indice.elegir_ventana(t0 + dt.timedelta(seconds=70), ventanas) is None
    assert indice.elegir_ventana(t0 + dt.timedelta(seconds=30), ventanas)["pb"] == "1"


def test_ventanas_por_vuelo_ignoran_seconds_range():
    con30, _ = _ventanas("AT141A", margen=30.0)
    con0, _ = _ventanas("AT141A", margen=0.0)
    assert [(v["inicio"], v["fin"]) for v in con30] == [(v["inicio"], v["fin"]) for v in con0]
    assert all(v["inicio"] == v["inicio_exacto"] and v["fin"] == v["fin_exacto"] for v in con30)


def test_hueco_con_margen_30_configurado_queda_sin_asignar():
    ventanas, _ = _ventanas("AT141A", margen=30.0)
    assert _asignar_vuelo(_dato("13:51:16"), ventanas) is None


def test_foto_exactamente_en_el_segundo_de_inicio_entra_borde_inclusivo():
    ventanas, _ = _ventanas("AT141A", margen=30.0)
    pb = next(v for v in ventanas if v["pb"] == "1")
    assert indice.elegir_ventana(pb["inicio_exacto"], ventanas)["pb"] == "1"


def test_foto_exactamente_en_el_segundo_de_fin_entra_borde_inclusivo():
    ventanas, _ = _ventanas("AT141A", margen=30.0)
    pb = next(v for v in ventanas if v["pb"] == "1")
    assert indice.elegir_ventana(pb["fin_exacto"], ventanas)["pb"] == "1"


def test_segundo_compartido_entre_dos_ventanas_gana_generales_si_es_generales():
    t = dt.datetime(2026, 10, 6, 10, 0, 10)
    pb = {"pb": "1", "vuelo": "1", "inicio": t - dt.timedelta(seconds=60), "fin": t}
    gen = {"pb": "GENERALES", "vuelo": "1", "inicio": t, "fin": t + dt.timedelta(seconds=60)}
    assert indice.elegir_ventana(t, [pb, gen])["pb"] == "GENERALES"
    # Sin GENERALES, gana la primera en orden del estadillo.
    pb2 = {"pb": "2", "vuelo": "1", "inicio": t, "fin": t + dt.timedelta(seconds=60)}
    assert indice.elegir_ventana(t, [pb, pb2])["pb"] == "1"


def test_empate_en_el_borde_compartido_pb_contiguos_gana_el_primero_del_estadillo():
    t = dt.datetime(2026, 10, 6, 10, 1, 0)
    a = {"pb": "1", "vuelo": "1", "inicio": t - dt.timedelta(seconds=60), "fin": t}
    b = {"pb": "2", "vuelo": "1", "inicio": t, "fin": t + dt.timedelta(seconds=60)}
    assert indice.elegir_ventana(t, [a, b])["pb"] == "1"
    assert indice.elegir_ventana(t, [b, a])["pb"] == "2"
