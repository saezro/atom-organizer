"""El índice guarda la posición de cada imagen con los MISMOS valores que hoy
lee `MetaLocation` al generar meta/location en el cierre. Si divergieran, el
CSV desde manifiesto dejaría de ser idéntico al actual."""
import datetime as dt

from atom_core import indice
import exif


class _Cb:
    def emit(self, *a, **k):
        pass


def test_posicion_coincide_con_metalocation(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "DJI_0001_T.JPG"), lat=37.123456, lon=-5.654321,
                         dt_val=dt.datetime(2026, 1, 15, 10, 0, 0),
                         relative_altitude=48.3, gimbal_yaw=-87.4, gimbal_pitch=-45.2)
    gi = exif.GeneralInformationFromImage(logger)
    ml = exif.MetaLocation(logger)

    dato = indice._leer_metadatos(ruta, gi, _Cb())

    coords = ml.leerLatitudLongitudAltitud_exif_DJI(ruta, _Cb())
    gimbal = gi.get_gimbal_yaw_pitch(ruta)
    xmp = gi.get_xmp_data(ruta)
    p = dato.posicion
    assert dato.meta_leida is True
    assert (p.lat, p.lon) == (coords[1], coords[2])
    assert (p.gimbal_yaw, p.gimbal_pitch) == (gimbal[0], gimbal[1])
    assert (p.altitud_abs, p.altura_relativa, p.gimbal_roll, p.flight_yaw) == (xmp[0], xmp[1], xmp[2], xmp[4])
    assert (p.ancho_px, p.alto_px) == (64, 48)
    assert dato.yaw == float(gimbal[0])


def test_sin_gps_queda_leida_pero_sin_coordenadas(tmp_path, logger):
    from PIL import Image
    ruta = str(tmp_path / "SIN_GPS.JPG")
    Image.new("RGB", (32, 16)).save(ruta, format="JPEG")
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    assert dato.meta_leida is True
    assert dato.posicion.lat is None and dato.posicion.lon is None


def test_campos_posicion_mapea_a_fila(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "A.JPG"))
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    campos = indice.campos_posicion(dato)
    assert campos["meta_leida"] is True
    assert set(campos) == {"meta_leida", "lat", "lon", "altitud_abs", "altura_relativa",
                           "gimbal_yaw", "gimbal_pitch", "gimbal_roll", "flight_yaw",
                           "ancho_px", "alto_px"}


def test_campos_posicion_sin_lectura_es_meta_leida_false():
    dato = indice._MetadatosImagen(ruta="gs://b/A.JPG", nombre="A.JPG", timestamp=None,
                                   modelo=None, yaw=None, gps=None)
    assert indice.campos_posicion(dato) == {"meta_leida": False}


def test_make_y_modelo_desde_la_misma_lectura(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "DJI_0002_T.JPG"), make="DJI", model="M4T")
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    assert (dato.make, dato.modelo) == ("DJI", "M4T")


def test_sin_make_queda_none(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "DJI_0003_T.JPG"))
    dato = indice._leer_metadatos(ruta, exif.GeneralInformationFromImage(logger), _Cb())
    assert dato.make is None


class _CbLineas:
    def __init__(self):
        self.lineas = []

    def emit(self, v):
        self.lineas.append(v)


def _dato(nombre, modelo):
    return indice._MetadatosImagen(ruta=f"/sd/{nombre}", nombre=nombre, timestamp=None,
                                   modelo=modelo, yaw=None, gps=None)


def test_aviso_equipo_una_linea_por_vuelo_que_discrepa():
    """Caso real MELINESTI: estadillo 'DJI M300', EXIF M4T."""
    v1 = {"pb": "TS09", "vuelo": "1", "equipo": "DJI M300"}
    v2 = {"pb": "TS09", "vuelo": "2", "equipo": "DJI Matrice 4T"}
    v3 = {"pb": "TS10", "vuelo": "1", "equipo": "Dron1"}
    cb = _CbLineas()
    n = indice._avisar_equipo([(_dato("A_T.JPG", "M4T"), v1), (_dato("B_T.JPG", "M4T"), v1),
                               (_dato("C_T.JPG", "M4T"), v2), (_dato("D_T.JPG", "M4T"), v3),
                               (_dato("E_T.JPG", "M4T"), None)], cb)
    assert n == 1
    avisos = [l for l in cb.lineas if "AVISO" in l]
    assert len(avisos) == 1
    assert "TS09" in avisos[0] and "'DJI M300'" in avisos[0] and "M4T" in avisos[0]


def test_ventanas_llevan_equipo_del_estadillo():
    import pandas as pd

    class _Pipeline:
        def ventana_horaria_vuelo(self, *a):
            return 0, 1

    cfg = type("Cfg", (), {"seconds_range": 0, "mismatch_hours": 0, "mismatch_minutes": 0})()
    cols = {"PB": "PB", "Vuelo": "Vuelo", "Fecha": "Fecha", "Hora_de_inicio": "HI",
            "Hora_final": "HF", "Equipo_de_vuelo": "Equipo_de_vuelo"}
    df = pd.DataFrame({"PB": ["1", "2"], "Vuelo": ["1", "1"], "Fecha": ["2026:09:10"] * 2,
                       "HI": ["10:00:00"] * 2, "HF": ["10:20:00"] * 2,
                       "Equipo_de_vuelo": ["DJI M300", float("nan")]})
    ventanas = indice._ventanas_por_vuelo(df, cols, _Pipeline(), cfg, _Cb())
    assert [v["equipo"] for v in ventanas] == ["DJI M300", None]
    sin_col = indice._ventanas_por_vuelo(df.drop(columns=["Equipo_de_vuelo"]), cols,
                                         _Pipeline(), cfg, _Cb())
    assert [v["equipo"] for v in sin_col] == [None, None]
