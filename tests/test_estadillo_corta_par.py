"""H30T: el estadillo corta un par T/W (la W ~1 s fuera de la franja): la W sigue a su T y
se AVISA (nada silencioso); el cierre dice qué par quedó partido."""
import datetime as dt

from atom_core.cierre import recuentos_por_vuelo
from atom_core.indice import _MetadatosImagen, _heredar_vuelo_por_pareja


def _d(nombre, ts):
    return _MetadatosImagen(ruta=f"/sd/100MEDIA/{nombre}", nombre=nombre, timestamp=ts,
                            modelo=None, yaw=None, gps=None)


class _Cb:
    def __init__(self):
        self.lineas = []

    def emit(self, texto):
        self.lineas.append(str(texto))


V1 = {"pb": "11", "vuelo": "1",
      "inicio_exacto": dt.datetime(2026, 3, 1, 10, 0, 0),
      "fin_exacto": dt.datetime(2026, 3, 1, 10, 4, 59)}


def test_w_h30t_fuera_de_franja_hereda_y_avisa_en_modo_z_y_w():
    t = _d("DJI_20260301100459_0001_T_point0.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w = _d("DJI_20260301100500_0001_W_point0.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
    for modo in ("Z", "W"):
        cb = _Cb()
        res = _heredar_vuelo_por_pareja([(t, V1), (w, None)], cb, modo)
        assert res[1][1] is V1
        texto = "".join(cb.lineas)
        assert ("AVISO: Estadillo: PB11_V1 corta el par 1: DJI_20260301100500_0001_W_point0.JPG "
                "(10:05:00) fuera de la franja de su vuelo 10:00:00–10:04:59; va con su térmica. "
                "Revisa el estadillo.") in texto


def test_aviso_agrupado_con_recuento_y_tres_nombres():
    asig, cb = [], _Cb()
    for i in range(1, 6):
        t = _d(f"DJI_20260301100459_{i:04d}_T_point0.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
        w = _d(f"DJI_20260301100500_{i:04d}_W_point0.JPG", dt.datetime(2026, 3, 1, 10, 5, 0))
        asig += [(t, V1), (w, None)]
    _heredar_vuelo_por_pareja(asig, cb, "Z")
    texto = "".join(cb.lineas)
    assert "PB11_V1 corta 5 pares" in texto and "(+2 más)" in texto
    assert "_0003_W" in texto and "_0004_W" not in texto


def test_sin_herencia_no_hay_aviso_de_corte_y_ambiguas_siguen_avisando():
    t = _d("DJI_20260301100459_0001_T_point0.JPG", dt.datetime(2026, 3, 1, 10, 4, 59))
    w = _d("DJI_20260301100520_0001_W_point0.JPG", dt.datetime(2026, 3, 1, 10, 5, 20))
    cb = _Cb()
    assert _heredar_vuelo_por_pareja([(t, V1), (w, None)], cb, "Z")[1][1] is None
    texto = "".join(cb.lineas)
    assert "corta" not in texto and "NO heredan" in texto


def _fila(tipo, nombre, pb="11", vuelo="1", unassigned=False):
    return {"tipo": tipo, "pb": pb, "vuelo": vuelo, "estado": "hecho", "unassigned": unassigned,
            "ruta_salida_tiff": None, "ruta_salida_crop": None,
            "ruta_origen": f"/sd/100MEDIA/{nombre}"}


def test_cierre_meta_distinto_de_t_nombra_el_par_partido():
    filas = [_fila("TERMICA", "DJI_20260301100459_0001_T_point0.JPG"),
             _fila("TERMICA", "DJI_20260301100400_0002_T_point0.JPG"),
             _fila("RGB", "DJI_20260301100500_0001_W_point0.JPG", pb="", vuelo="", unassigned=True),
             _fila("RGB", "DJI_20260301100400_0002_W_point0.JPG")]
    r = recuentos_por_vuelo(filas, {("11", "1"): {"meta": 1, "location": 1}})[("11", "1")]
    assert r["estado"] == "ERROR" and "meta != T" in r["motivo"]
    assert "par partido: DJI_20260301100500_0001_W_point0.JPG en SIN_ORDENAR" in r["motivo"]
    filas[2] = _fila("RGB", "DJI_20260301100500_0001_W_point0.JPG", pb="11", vuelo="2")
    r = recuentos_por_vuelo(filas, {("11", "1"): {"meta": 1, "location": 1}})[("11", "1")]
    assert "0001_W_point0.JPG en PB11_V2" in r["motivo"]


def test_cortes_misma_ventana_en_fechas_distintas_no_se_agrupan():
    asig, cb = [], _Cb()
    for dia in (1, 2):
        v = dict(V1, inicio_exacto=dt.datetime(2026, 3, dia, 10, 0, 0),
                 fin_exacto=dt.datetime(2026, 3, dia, 10, 4, 59))
        t = _d(f"DJI_2026030{dia}100459_000{dia}_T_point0.JPG", dt.datetime(2026, 3, dia, 10, 4, 59))
        w = _d(f"DJI_2026030{dia}100500_000{dia}_W_point0.JPG", dt.datetime(2026, 3, dia, 10, 5, 0))
        asig += [(t, v), (w, None)]
    _heredar_vuelo_por_pareja(asig, cb, "W")
    assert sum("corta el par" in l for l in cb.lineas) == 2


def _fila_ts(tipo, nombre, ts, **kw):
    f = _fila(tipo, nombre, **kw)
    f["timestamp_exif"] = ts
    return f


def test_cierre_modo_z_ignora_w_partida_si_la_z_va_bien():
    filas = [_fila_ts("TERMICA", "DJI_20260301100459_0001_T_point0.JPG", "2026-03-01T10:04:59"),
             _fila_ts("RGB", "DJI_20260301100500_0001_W_point0.JPG", "2026-03-01T10:05:00",
                      pb="", vuelo="", unassigned=True),
             _fila_ts("RGB", "DJI_20260301100459_0001_Z_point0.JPG", "2026-03-01T10:04:59")]
    kw = {"camara_rgb": "Z", "hay_zoom": True}
    csv = {("11", "1"): {"meta": 0, "location": 0}}
    r = recuentos_por_vuelo(filas, csv, **kw)[("11", "1")]
    assert "meta != T" in r["motivo"] and "par partido" not in r["motivo"]
    # gemelo: la Z (cámara elegida) partida de verdad SÍ se nombra
    filas[2] = _fila_ts("RGB", "DJI_20260301100500_0001_Z_point0.JPG", "2026-03-01T10:05:00",
                        pb="", vuelo="", unassigned=True)
    r = recuentos_por_vuelo(filas, csv, **kw)[("11", "1")]
    assert "par partido: DJI_20260301100500_0001_Z_point0.JPG en SIN_ORDENAR" in r["motivo"]


def test_cierre_idx_repetido_de_otro_vuelo_no_es_par_partido():
    filas = [_fila_ts("TERMICA", "DJI_20260301100459_0001_T_point0.JPG", "2026-03-01T10:04:59"),
             _fila_ts("RGB", "DJI_20260301100459_0001_W_point0.JPG", "2026-03-01T10:04:59"),
             _fila_ts("RGB", "DJI_20260301150000_0001_W_point0.JPG", "2026-03-01T15:00:00",
                      pb="", vuelo="", unassigned=True)]
    csv = {("11", "1"): {"meta": 2, "location": 2}}
    r = recuentos_por_vuelo(filas, csv)[("11", "1")]
    assert "meta != T" in r["motivo"] and "par partido" not in r["motivo"]
    # gemelo: mismo idx a 1 s de la T sí es par partido
    filas[2] = _fila_ts("RGB", "DJI_20260301100500_0001_W_point0.JPG", "2026-03-01T10:05:00",
                        pb="", vuelo="", unassigned=True)
    r = recuentos_por_vuelo(filas, csv)[("11", "1")]
    assert "par partido: DJI_20260301100500_0001_W_point0.JPG en SIN_ORDENAR" in r["motivo"]
