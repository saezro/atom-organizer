"""FOTOS_GENERALES nunca genera meta.csv/location.csv (decision de producto); un vuelo normal si."""
import datetime as dt
import os
from types import SimpleNamespace

from atom_core import cierre
from atom_core.almacen import AVISO_GENERALES_SIN_CSV, NOMBRE_CARPETA_GENERALES, es_carpeta_generales
from atom_core.manifiesto import FilaManifiesto, Manifiesto
import exif
from utils import OrganizerLogger


class _Cb:
    def __init__(self):
        self.msgs = []

    def emit(self, msg=None, *a, **k):
        self.msgs.append(msg)


def _errores(cb):
    return [m for m in cb.msgs if isinstance(m, str) and "ERROR" in m]


def _ml(tmp_path):
    return exif.MetaLocation(OrganizerLogger("t_generales", log_dir=str(tmp_path / "Logs"), create_file_handler=False))


def _fotos(carpeta, make_dji_jpeg, base=0):
    carpeta.mkdir(parents=True)
    for i in (1, 2):
        i += base
        for suf in ("T", "W"):
            make_dji_jpeg(str(carpeta / f"20260115_10000{i}_DJI_000{i}_{suf}.JPG"), lat=37.1, lon=-5.6,
                          gimbal_yaw=0.0, gimbal_pitch=-90.0, relative_altitude=50.0,
                          dt_val=dt.datetime(2026, 1, 15, 10, 0, 0))


def _csvs(raiz):
    return [p for p in raiz.rglob("*.csv")]


def test_predicado():
    assert es_carpeta_generales("/x/y/FOTOS_GENERALES")
    assert es_carpeta_generales("gs://b/P/2026/FOTOS_GENERALES/")
    assert not es_carpeta_generales("/x/y/RGB") and not es_carpeta_generales(None)


def test_exif_generales_no_genera_csv(tmp_path, make_dji_jpeg):
    carpeta = tmp_path / "d" / NOMBRE_CARPETA_GENERALES
    _fotos(carpeta, make_dji_jpeg)
    (tmp_path / "d" / "CSVs").mkdir()
    cb, ml = _Cb(), _ml(tmp_path)
    ml.gen_meta_location_emparejado(str(carpeta), str(carpeta), cb, cb, str(tmp_path / "d" / "CSVs"), 50.0, False)
    ml.iterate_folders(str(carpeta), "meta.csv", cb, cb, str(tmp_path / "d" / "CSVs"), 50.0, False)
    assert not _csvs(tmp_path / "d")
    assert not _errores(cb) and any(isinstance(m, str) and AVISO_GENERALES_SIN_CSV in m for m in cb.msgs)


def test_exif_vuelo_normal_si_genera_csv(tmp_path, make_dji_jpeg):
    carpeta = tmp_path / "d" / "RGB" / "PB1" / "PB1_V1"
    _fotos(carpeta, make_dji_jpeg)
    (tmp_path / "d" / "CSVs").mkdir()
    cb = _Cb()
    _ml(tmp_path).gen_meta_location(str(carpeta), "location.csv", cb, cb, str(tmp_path / "d" / "CSVs"), 50.0, False)
    assert _csvs(tmp_path / "d")


def _manifiesto(raiz, destinos):
    m = Manifiesto(raiz / ".organizado" / "m.db")
    os.makedirs(raiz / ".organizado", exist_ok=True)
    m.crear_esquema()
    filas = []
    for pb, vuelo, carpeta in destinos:
        for i in ((5, 6) if pb == "GENERALES" else (1, 2)):
            for tipo, suf in (("TERMICA", "T"), ("RGB", "W")):
                nombre = f"20260115_10000{i}_DJI_000{i}_{suf}.JPG"
                ruta = str(carpeta[tipo] / nombre)
                filas.append(FilaManifiesto(
                    ruta_origen=f"/sd/{pb}/{tipo}/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None, pb=pb,
                    vuelo=vuelo, nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None, comprime=False,
                    ruta_salida_original=ruta, ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=False,
                    bytes_origen=os.path.getsize(ruta)))
    m.insertar_o_reabrir(filas)
    for fila in m.todas():
        m.marcar_hecha(fila["id"], "")
    return m


def test_cierre_generales_sin_csv_y_vuelo_normal_con_csv(tmp_path, make_dji_jpeg):
    raiz = tmp_path / "d"
    gen = raiz / NOMBRE_CARPETA_GENERALES
    _fotos(gen, make_dji_jpeg, base=4)
    for tipo, suf in (("TERMICA", "T"), ("RGB", "W")):
        c = raiz / tipo / "PB1" / "PB1_V1"
        c.mkdir(parents=True)
        for i in (1, 2):
            make_dji_jpeg(str(c / f"20260115_10000{i}_DJI_000{i}_{suf}.JPG"), lat=37.1, lon=-5.6,
                          gimbal_yaw=0.0, gimbal_pitch=-90.0, relative_altitude=50.0,
                          dt_val=dt.datetime(2026, 1, 15, 10, 0, 0))
    (raiz / "CSVs").mkdir()
    m = _manifiesto(raiz, [("GENERALES", "LA_NAVA", {"TERMICA": gen, "RGB": gen}),
                           ("1", "1", {t: raiz / t / "PB1" / "PB1_V1" for t in ("TERMICA", "RGB")})])
    cfg = SimpleNamespace(output_folder=str(raiz), include_v=True, flight_height=50.0,
                          calculate_proyected_distance=False, gen_meta_location=True)
    cb = _Cb()
    cierre._emitir_meta_location(m, cfg, cb, {})
    assert not list(gen.glob("*.csv"))
    assert all("GENERALES" not in p.name and "NAVA" not in p.name for p in _csvs(raiz))
    assert (raiz / "TERMICA" / "PB1" / "PB1_V1" / "PB1_V1_meta.csv").exists()
    assert (raiz / "RGB" / "PB1" / "PB1_V1" / "PB1_V1_location.csv").exists()
    assert not _errores(cb) and any(isinstance(x, str) and AVISO_GENERALES_SIN_CSV in x for x in cb.msgs)
