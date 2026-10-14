"""meta/location del cierre: nunca mezclan días y se ordenan por hora de captura.

Destino acumulado: el mismo PB_V puede traer varios días (un CSV por fecha,
`<vuelo>_<YYYYMMDD>_meta|location.csv`) y el contador DJI puede volver a 0001
dentro de un vuelo (el orden lo da la hora de captura; el idx solo desempata)."""
import datetime as dt
import os
from types import SimpleNamespace

from atom_core import cierre
from atom_core.manifiesto import FilaManifiesto, Manifiesto


class _Cb:
    def __init__(self):
        self.textos = []

    def emit(self, *a, **k):
        self.textos.append(str(a[0]) if a else "")


# (tipo, nombre, datetime de captura)
def _montar(raiz, make_dji_jpeg, imagenes):
    manifiesto = Manifiesto(raiz / ".organizado" / "m.db")
    os.makedirs(raiz / ".organizado", exist_ok=True)
    (raiz / "CSVs").mkdir(exist_ok=True)
    manifiesto.crear_esquema()
    filas = []
    for i, (tipo, nombre, cuando) in enumerate(imagenes):
        carpeta = raiz / tipo / "PB1" / "PB1_V1"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = str(carpeta / nombre)
        make_dji_jpeg(ruta, lat=37.1 + i * 1e-5, lon=-5.6, gimbal_yaw=0.0, gimbal_pitch=-90.0,
                      relative_altitude=50.0, dt_val=cuando)
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=cuando.isoformat(), modelo=None,
            pb="1", vuelo="1", nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=ruta, ruta_salida_crop=None,
            ruta_salida_tiff=None, unassigned=False, bytes_origen=os.path.getsize(ruta)))
    manifiesto.insertar_o_reabrir(filas)
    for fila in manifiesto.todas():
        manifiesto.marcar_hecha(fila["id"], "")
    return manifiesto


def _cfg(raiz):
    return SimpleNamespace(output_folder=str(raiz), include_v=True, flight_height=50.0,
                           calculate_proyected_distance=False, gen_meta_location=True)


def _fotos(ruta):
    with open(ruta, encoding="utf-8") as f:
        return [linea.split(",")[0] for linea in f.read().splitlines() if linea]


def _csvs(raiz):
    return sorted(f for _d, _s, fs in os.walk(raiz / "CSVs") for f in fs)


def _par(n_idx, cuando, prefijo=""):
    return [("TERMICA", f"{prefijo}DJI_{n_idx:04d}_T.JPG", cuando),
            ("RGB", f"{prefijo}DJI_{n_idx:04d}_W.JPG", cuando)]


def test_dos_dias_mismo_vuelo_dos_csv_sin_mezcla(tmp_path, make_dji_jpeg):
    d1, d2 = dt.datetime(2026, 1, 15, 10, 0, 0), dt.datetime(2026, 1, 16, 9, 0, 0)
    imgs = (_par(1, d1) + _par(2, d1 + dt.timedelta(seconds=5))
            + _par(1, d2, "B") + _par(2, d2 + dt.timedelta(seconds=5), "B"))
    m = _montar(tmp_path, make_dji_jpeg, imgs)
    cb = _Cb()
    cierre._emitir_meta_location(m, _cfg(tmp_path), cb)
    assert _csvs(tmp_path) == [
        "PB1_V1_20260115_location.csv", "PB1_V1_20260115_meta.csv",
        "PB1_V1_20260116_location.csv", "PB1_V1_20260116_meta.csv"]
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_20260115_meta.csv") == ["DJI_0001_T.JPG", "DJI_0002_T.JPG"]
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_20260116_meta.csv") == ["BDJI_0001_T.JPG", "BDJI_0002_T.JPG"]
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_20260116_location.csv") == ["BDJI_0001_W.JPG", "BDJI_0002_W.JPG"]
    assert any("WARN: PB1_1 aparece en 2 fechas" in t for t in cb.textos)
    m.cerrar()


def test_contador_dji_reiniciado_ordena_por_hora_de_captura(tmp_path, make_dji_jpeg):
    base = dt.datetime(2026, 1, 15, 10, 0, 0)
    orden = [998, 999, 1, 2]  # el contador vuelve a 0001; los nombres NO llevan hora
    imgs = [p for i, n in enumerate(orden) for p in _par(n, base + dt.timedelta(seconds=10 * i))]
    m = _montar(tmp_path, make_dji_jpeg, imgs)
    cierre._emitir_meta_location(m, _cfg(tmp_path), _Cb())
    assert _csvs(tmp_path) == ["PB1_V1_location.csv", "PB1_V1_meta.csv"]
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_meta.csv") == [f"DJI_{n:04d}_T.JPG" for n in orden]
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_location.csv") == [f"DJI_{n:04d}_W.JPG" for n in orden]
    m.cerrar()


def test_rgb_solo_independiente_ordena_por_hora_no_por_idx(tmp_path, make_dji_jpeg):
    base = dt.datetime(2026, 1, 15, 10, 0, 0)
    orden = [999, 1, 2]
    imgs = [("RGB", f"DJI_{n:04d}_W.JPG", base + dt.timedelta(seconds=10 * i)) for i, n in enumerate(orden)]
    m = _montar(tmp_path, make_dji_jpeg, imgs)
    cierre._emitir_meta_location(m, _cfg(tmp_path), _Cb())
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_location.csv") == [f"DJI_{n:04d}_W.JPG" for n in orden]
    m.cerrar()


def test_un_solo_dia_nombres_de_siempre_sin_warn_de_fechas(tmp_path, make_dji_jpeg):
    d = dt.datetime(2026, 1, 15, 10, 0, 0)
    m = _montar(tmp_path, make_dji_jpeg, _par(1, d) + _par(2, d + dt.timedelta(seconds=5)))
    cb = _Cb()
    cierre._emitir_meta_location(m, _cfg(tmp_path), cb)
    assert _csvs(tmp_path) == ["PB1_V1_location.csv", "PB1_V1_meta.csv"]
    assert not any("fechas distintas" in t for t in cb.textos)
    m.cerrar()


def test_foto_sin_hora_error_visible_y_excluida(tmp_path, make_dji_jpeg):
    base = dt.datetime(2026, 1, 15, 10, 0, 0)
    imgs = _par(1, base) + _par(2, base + dt.timedelta(seconds=5))
    m = _montar(tmp_path, make_dji_jpeg, imgs)
    con = m._conexion()
    with con:  # la par 0002 pierde la hora de captura y su nombre no la trae
        con.execute("UPDATE imagenes SET timestamp_exif = NULL WHERE nombre_nuevo LIKE 'DJI_0002%'")
    cb = _Cb()
    cierre._emitir_meta_location(m, _cfg(tmp_path), cb)
    assert any("ERROR" in t and "sin hora de captura" in t for t in cb.textos)
    assert _fotos(tmp_path / "CSVs" / "PB1_V1_meta.csv") == ["DJI_0001_T.JPG"]
    m.cerrar()
