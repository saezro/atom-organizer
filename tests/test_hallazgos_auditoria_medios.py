"""Hallazgos medios de auditoría (3.4.124): A) vuelo solo RGB, B) clave única de pareja,
C) cola escalonada en el índice, D) tamaño siempre comprobado en la validación de apply."""
import datetime as dt
import io
import os
from types import SimpleNamespace

import pytest
from PIL import Image as PILImage

import exif
from atom_core import apply, cierre, indice, pares
from atom_core.manifiesto import FilaManifiesto, Manifiesto


class _Cb:
    def __init__(self):
        self.msgs = []

    def emit(self, msg=None, *a, **k):
        self.msgs.append(msg)


def _cfg(raiz):
    return SimpleNamespace(output_folder=str(raiz), include_v=True, flight_height=50.0,
                           calculate_proyected_distance=False, gen_meta_location=True)


def _montar(raiz, make_dji_jpeg, logger, imagenes):
    """imagenes: [(tipo, nombre)] de un único vuelo PB1/PB1_V1, todas 'hechas'."""
    manifiesto = Manifiesto(raiz / ".organizado" / "m.db")
    os.makedirs(raiz / ".organizado", exist_ok=True)
    manifiesto.crear_esquema()
    filas = []
    for tipo, nombre in imagenes:
        carpeta = raiz / tipo / "PB1" / "PB1_V1"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = str(carpeta / nombre)
        make_dji_jpeg(ruta, lat=37.1, lon=-5.6, gimbal_yaw=0.0, gimbal_pitch=-90.0,
                      relative_altitude=50.0, dt_val=dt.datetime(2026, 1, 15, 10, 0, 0))
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None,
            pb="1", vuelo="1", nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=ruta, ruta_salida_crop=None,
            ruta_salida_tiff=None, unassigned=False, bytes_origen=os.path.getsize(ruta)))
    manifiesto.insertar_o_reabrir(filas)
    for fila in manifiesto.todas():
        manifiesto.marcar_hecha(fila["id"], "")
    (raiz / "CSVs").mkdir(exist_ok=True)
    return manifiesto


def _lineas(p):
    return [l for l in open(p, encoding="utf-8").read().splitlines() if l.strip()]


def test_a_vuelo_solo_rgb_escribe_location_sin_error(tmp_path, make_dji_jpeg, logger):
    raiz = tmp_path / "d"
    m = _montar(raiz, make_dji_jpeg, logger,
                [("RGB", f"20260115_10000{i}_DJI_000{i}_W.JPG") for i in range(1, 4)])
    cb = _Cb()
    (raiz / "TERMICA").mkdir()  # el guard de nivel run exige ambas raíces (como antes)
    cierre._emitir_meta_location(m, _cfg(raiz), cb, {})
    loc = raiz / "RGB" / "PB1" / "PB1_V1" / "PB1_V1_location.csv"
    assert loc.exists() and len(_lineas(loc)) == 3
    assert not [x for x in cb.msgs if isinstance(x, str) and "ERROR" in x], cb.msgs


def test_a_vuelo_con_w_huerfana_avisa_y_escribe_las_emparejadas(tmp_path, make_dji_jpeg, logger):
    raiz = tmp_path / "d"
    imgs = [("RGB", "20260115_100001_DJI_0001_W.JPG"), ("TERMICA", "20260115_100001_DJI_0001_T.JPG"),
            ("RGB", "20260115_100002_DJI_0002_W.JPG"), ("TERMICA", "20260115_100002_DJI_0002_T.JPG"),
            ("RGB", "20260115_100003_DJI_0003_W.JPG")]
    m = _montar(raiz, make_dji_jpeg, logger, imgs)
    cb = _Cb()
    cierre._emitir_meta_location(m, _cfg(raiz), cb, {})
    assert len(_lineas(raiz / "RGB" / "PB1" / "PB1_V1" / "PB1_V1_location.csv")) == 2
    assert len(_lineas(raiz / "TERMICA" / "PB1" / "PB1_V1" / "PB1_V1_meta.csv")) == 2
    textos = [x for x in cb.msgs if isinstance(x, str)]
    assert any("AVISO" in x and "DJI_0003_W" in x for x in textos)
    assert not [x for x in textos if "ERROR" in x]


def test_a_exif_emparejado_solo_rgb_cae_a_independiente(tmp_path, monkeypatch):
    obj = exif.MetaLocation(__import__("utils").OrganizerLogger("t", log_dir=str(tmp_path), create_file_handler=False))
    obj.stop = False
    carpeta = tmp_path / "RGB"
    carpeta.mkdir()
    (carpeta / "20260115_100001_DJI_0001_W.JPG").write_bytes(b"x")
    llamadas = []
    monkeypatch.setattr(obj, "gen_meta_location", lambda *a, **k: llamadas.append(a[1]))
    obj.gen_meta_location_emparejado(None, str(carpeta), _Cb(), _Cb(), str(tmp_path), 0.0, False)
    assert llamadas == ["location.csv"] and obj.error_meta_location == 0


def test_b_misma_clave_en_paridad_y_cierre():
    t = ["DJI_20260930121952_0618_T_point1.JPG", "DJI_20260930121953_0619_T.JPG"]
    w = ["DJI_20260930121953_0618_W_point1.JPG", "DJI_20260930121952_0619_W.JPG"]  # timestamps +-1 s
    assert pares.paridad_tw(t + w) == ([], [])
    obj = exif.MetaLocation(__import__("utils").OrganizerLogger("t2", create_file_handler=False))
    par, sin_pareja, sin_patron = obj.emparejar_por_idx(t, w)
    assert sin_pareja == [] and sin_patron == []
    assert par == [(t[0], w[0]), (t[1], w[1])]
    # con huérfana: ambos coinciden
    assert pares.paridad_tw(t + w[:1]) == (["0619"], [])
    _, sp, _ = obj.emparejar_por_idx(t, w[:1])
    assert sp == [t[1]]


def _jpeg(extra=b""):
    b = io.BytesIO()
    PILImage.new("RGB", (64, 64), (200, 10, 10)).save(b, "JPEG")
    return b.getvalue() + extra


def _espia(monkeypatch):
    leidos = []
    real_open = open

    class _Esp:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._f.close()

        def fileno(self):
            return self._f.fileno()

        def seek(self, n, *a):
            return self._f.seek(n, *a)

        def read(self, n=-1):
            leidos.append(n)
            return self._f.read(n)

    monkeypatch.setattr("builtins.open", lambda r, m="r", *a, **k:
                        _Esp(real_open(r, m, *a, **k)) if str(r).endswith("espia.jpg") else real_open(r, m, *a, **k))
    return leidos


def test_c_eoi_al_final_lee_solo_cola_corta(tmp_path, monkeypatch):
    p = tmp_path / "espia.jpg"
    monkeypatch.setattr(indice, "_BYTES_CABECERA", 64)
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)  # fichero > cabecera, termina en FFD9
    p.write_bytes(_jpeg())
    assert os.path.getsize(p) > 64
    leidos = _espia(monkeypatch)
    _cab, a_medias, sin_eoi = indice._leer_cabecera_y_cola(str(p))
    assert (a_medias, sin_eoi) == (False, False)
    assert 64 * 1024 not in leidos and indice._BYTES_COLA_CORTA in leidos


def test_c_payload_tras_eoi_cae_a_64k(tmp_path, monkeypatch):
    p = tmp_path / "espia.jpg"
    monkeypatch.setattr(indice, "_BYTES_CABECERA", 64)
    monkeypatch.setattr(indice, "_FILTRO_ARCHIVO_A_MEDIAS", True)
    p.write_bytes(_jpeg(b"\x07" * 100000))
    leidos = _espia(monkeypatch)
    _cab, a_medias, sin_eoi = indice._leer_cabecera_y_cola(str(p))
    assert 64 * 1024 in leidos
    assert a_medias is False and sin_eoi is True  # EOI fuera de los últimos 64 KiB


def test_d_sin_eoi_cero_con_tamano_cambiado_no_se_salta_validacion(tmp_path):
    p = tmp_path / "i.jpg"
    p.write_bytes(_jpeg() + b"\x00" * 20000)  # cola a ceros: truncado/relleno
    fila = {"id": 1, "ruta_origen": str(p), "jpeg_sin_eoi": 0, "bytes_origen": 12345}

    class _M:
        fallidas = {}

        def marcar_fallida(self, i, motivo):
            self.fallidas[i] = motivo

    m = _M()
    # tamaño indexado coincide: no se relee (se acepta la marca del índice)
    ok = apply._validar_jpeg_origen(m, [dict(fila, bytes_origen=os.path.getsize(p))], _Cb())
    assert len(ok) == 1 and not m.fallidas
    # tamaño cambiado: se revalida como antes (el JPEG sigue siendo decodificable -> pasa)
    p.write_bytes(_jpeg()[:-200])
    ok = apply._validar_jpeg_origen(m, [dict(fila, bytes_origen=999)], _Cb())
    assert not ok and 1 in m.fallidas
