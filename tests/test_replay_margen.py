import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from replay_margen_asignacion import aplicar_margen, comparar_asignacion, etiqueta_destino  # noqa: E402

T0 = dt.datetime(2026, 10, 6, 13, 50, 0)


def _v(pb, vuelo, ini, fin, margen):
    m = dt.timedelta(seconds=margen)
    return {"pb": pb, "vuelo": vuelo, "inicio": ini - m, "fin": fin + m,
            "inicio_exacto": ini, "fin_exacto": fin}


def _ventanas(margen):
    return [_v("1", "1", T0, T0 + dt.timedelta(seconds=60), margen)]


def _fila(nombre, seg, pb="1", vuelo="1", unassigned=0):
    return {"nombre_original": nombre, "tipo": "TERMICA", "pb": pb, "vuelo": vuelo,
            "unassigned": unassigned,
            "timestamp_exif": (T0 + dt.timedelta(seconds=seg)).isoformat()}


def _exactas(ventanas):
    return [{**v, "inicio": v["inicio_exacto"], "fin": v["fin_exacto"]} for v in ventanas]


def test_foto_en_el_margen_cambia_de_destino():
    filas = [_fila("dentro.jpg", 30), _fila("margen.jpg", 70)]   # 10 s tras el fin exacto
    r = comparar_asignacion(filas, _ventanas(30), _exactas(_ventanas(30)))
    assert r["total"] == 2
    assert [c["nombre"] for c in r["cambian"]] == ["margen.jpg"]
    assert r["cambian"][0]["destino_margen"] == "PB1_V1" and r["cambian"][0]["destino_exacto"] == "SIN_ORDENAR"
    assert r["cambian"][0]["seg_fuera"] == 10
    assert r["transiciones"] == {"PB1_V1 -> SIN_ORDENAR": 1}


def test_fotos_en_el_borde_exacto_se_cuentan_aparte():
    filas = [_fila("ini.jpg", 0), _fila("fin.jpg", 60), _fila("medio.jpg", 30)]
    r = comparar_asignacion(filas, _ventanas(30), _exactas(_ventanas(30)))
    assert sorted(r["en_borde"]) == ["fin.jpg", "ini.jpg"]


def test_replay_detecta_si_no_reproduce_el_manifiesto():
    filas = [_fila("x.jpg", 30, pb="9", vuelo="9")]               # el manifiesto dijo otro vuelo
    r = comparar_asignacion(filas, _ventanas(30), _exactas(_ventanas(30)))
    assert r["incoherentes_con_manifiesto"] == ["x.jpg"]


def test_etiqueta_generales():
    assert etiqueta_destino({"pb": "GENERALES", "vuelo": "AT141A"}) == "FOTOS_GENERALES"
    assert etiqueta_destino(None) == "SIN_ORDENAR"
    assert etiqueta_destino({"pb": "2", "vuelo": "3"}) == "PB2_V3"


def _ventanas_generales(margen):
    # solo GENERALES (pb="GENERALES", vuelo=<planta>): franja exacta T0+70..T0+130
    return [_v("GENERALES", "AT141A", T0 + dt.timedelta(seconds=70), T0 + dt.timedelta(seconds=130), margen)]


def _fila_gen(nombre, seg):
    return _fila(nombre, seg, pb="GENERALES", vuelo="AT141A")


def test_generales_en_el_margen_cambia_de_destino():
    filas = [_fila_gen("margen_gen.jpg", 65)]                     # 5 s antes de la franja exacta
    r = comparar_asignacion(filas, _ventanas_generales(30), _exactas(_ventanas_generales(30)))
    assert r["transiciones"] == {"FOTOS_GENERALES -> SIN_ORDENAR": 1}
    assert r["cambian"][0]["destino_margen"] == "FOTOS_GENERALES"
    assert r["cambian"][0]["seg_fuera"] == 5
    assert r["generales_cambian"] == 1
    assert r["incoherentes_con_manifiesto"] == []                 # _etiqueta_manifiesto reproduce GENERALES


def test_generales_dentro_de_franja_exacta_no_cambia():
    filas = [_fila_gen("dentro_gen.jpg", 100)]
    r = comparar_asignacion(filas, _ventanas_generales(30), _exactas(_ventanas_generales(30)))
    assert r["cambian"] == [] and r["generales_cambian"] == 0
    assert r["incoherentes_con_manifiesto"] == []


def test_generales_solapada_con_pb_gana_en_franja_exacta():
    # PB1 con margen llega a T0+90; GENERALES exacta desde T0+70: la foto en +80 es GENERALES en ambos casos
    vent = _ventanas(30) + _ventanas_generales(30)
    filas = [_fila_gen("solape.jpg", 80)]
    r = comparar_asignacion(filas, vent, _exactas(vent))
    assert r["cambian"] == [] and r["generales_cambian"] == 0
    assert r["incoherentes_con_manifiesto"] == []


def test_aplicar_margen_ensancha_desde_la_franja_exacta():
    # _ventanas_por_vuelo (3.4.141) ya devuelve inicio==inicio_exacto: el margen lo pone el replay
    base = [{"pb": "1", "vuelo": "1", "inicio": T0, "fin": T0 + dt.timedelta(seconds=60),
             "inicio_exacto": T0, "fin_exacto": T0 + dt.timedelta(seconds=60)}]
    con, exa = aplicar_margen(base, 30)
    assert con[0]["inicio"] == T0 - dt.timedelta(seconds=30)
    assert con[0]["fin"] == T0 + dt.timedelta(seconds=90)
    assert exa[0]["inicio"] == T0 and exa[0]["fin"] == T0 + dt.timedelta(seconds=60)
    r = comparar_asignacion([_fila("margen.jpg", 70)], con, exa)
    assert len(r["cambian"]) == 1
