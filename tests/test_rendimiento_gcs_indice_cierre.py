"""Índice y cierre con origen/destino `gs://`: sin round-trips por imagen.

Bucket falso que se comporta como el real en lo que importa: `list_blobs`
devuelve el tamaño (sin `reload()`), y cuenta `reload`/`exists`/rangos/listados.
Se sujeta que (a) el índice rellena `meta_leida` leyendo UNA cabecera por rango
por imagen y sin `reload` por blob, (b) el cierre no relee el EXIF de la salida
y verifica con UN listado, (c) el resultado (posición y CSV meta/location) es
idéntico al de la ruta local y (d) un EXIF sin `Image Model` no peta.
"""
import datetime as dt
import types
from pathlib import Path
from unittest import mock

import pytest

import exif
from atom_core import almacen as almacen_mod, cierre, indice
from atom_core.almacen_gcs import AlmacenGCS
from atom_core.manifiesto import FilaManifiesto, Manifiesto

BUCKET = "bucket-rend"
RAIZ = f"gs://{BUCKET}/PLANTA/INSP"


class _Cb:
    def emit(self, *a, **k):
        pass


class _Blob:
    def __init__(self, bucket, nombre, size=None):
        self.bucket, self.name, self.size = bucket, nombre, size

    def exists(self):
        self.bucket.exists += 1
        return self.name in self.bucket.objetos

    def reload(self):
        self.bucket.reloads += 1
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.size = len(self.bucket.objetos[self.name])

    def download_as_bytes(self, start=0, end=None):
        self.bucket.rangos += 1
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        datos = self.bucket.objetos[self.name]
        return datos[start:(end + 1) if end is not None else None]

    def download_to_filename(self, ruta):
        self.bucket.descargas += 1
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        Path(ruta).write_bytes(self.bucket.objetos[self.name])

    def upload_from_filename(self, ruta, **kw):
        self.bucket.objetos[self.name] = Path(ruta).read_bytes()


class _Bucket:
    def __init__(self):
        self.objetos = {}
        self.exists = self.reloads = self.rangos = self.descargas = self.listados = 0

    def blob(self, nombre, **kw):
        return _Blob(self, nombre)

    def list_blobs(self, prefix=""):
        self.listados += 1
        for nombre in sorted(self.objetos):
            if nombre.startswith(prefix):
                yield _Blob(self, nombre, size=len(self.objetos[nombre]))


class _Cliente:
    def __init__(self, bucket):
        self._b = bucket

    def bucket(self, nombre):
        return self._b


@pytest.fixture
def bucket():
    b = _Bucket()
    almacen_mod._limpiar_cache_almacenes()
    almacen_mod._ALMACENES[f"gs://{BUCKET}"] = AlmacenGCS(BUCKET, cliente=_Cliente(b))
    yield b
    almacen_mod._limpiar_cache_almacenes()


def _subir(bucket, tmp_path, make_dji_jpeg, nombres, prefijo="PLANTA/INSP/ORIGEN"):
    locales = {}
    for i, nombre in enumerate(nombres):
        loc = tmp_path / "loc" / nombre
        loc.parent.mkdir(exist_ok=True)
        make_dji_jpeg(str(loc), lat=37.1 + i / 100, dt_val=dt.datetime(2026, 1, 15, 10, i, 0),
                      make="DJI", model="M4T")
        bucket.objetos[f"{prefijo}/{nombre}"] = loc.read_bytes()
        locales[nombre] = str(loc)
    return locales


def test_indice_gcs_rellena_meta_leida_sin_reload_ni_tamano_por_blob(bucket, tmp_path, make_dji_jpeg, logger):
    nombres = ["DJI_0001_W.JPG", "DJI_0002_W.JPG", "DJI_0003_T.JPG"]
    _subir(bucket, tmp_path, make_dji_jpeg, nombres)
    gi = exif.GeneralInformationFromImage(logger)

    tamanos = {}
    rutas = indice._listar_imagenes_gcs(f"{RAIZ}/ORIGEN", tamanos)
    assert len(rutas) == 3
    assert bucket.listados == 1  # UN solo list_blobs del origen
    datos = [indice._leer_metadatos(r, gi, _Cb()) for r in rutas]

    assert all(d.meta_leida and d.posicion is not None and d.make == "DJI" for d in datos)
    assert bucket.rangos == 3          # 1 lectura por rango por imagen
    assert bucket.descargas == 0       # ni descarga completa
    assert bucket.reloads == 0 and bucket.exists == 0

    # el tamaño viene del listado: `_construir_fila` no llama a tamano_de
    cfg = mock.MagicMock(input_folder=f"{RAIZ}/ORIGEN", output_folder=f"{RAIZ}/OUT")
    pipe = mock.MagicMock()
    pipe.nombre_destino.return_value = "x.JPG"
    with mock.patch.object(indice.almacen, "tamano_de", side_effect=AssertionError("reload por blob")):
        with mock.patch.object(indice, "_clasificar_tipo", return_value="RGB"), \
                mock.patch.object(indice, "_pct_recorte", return_value=None):
            fila = indice._construir_fila(datos[0], None, {}, cfg, pipe, None, tamanos)
    assert fila.bytes_origen == len(bucket.objetos["PLANTA/INSP/ORIGEN/DJI_0001_W.JPG"])
    assert bucket.reloads == 0


def test_posicion_identica_local_y_gcs(bucket, tmp_path, make_dji_jpeg, logger):
    locales = _subir(bucket, tmp_path, make_dji_jpeg, ["DJI_0001_T.JPG"])
    gi = exif.GeneralInformationFromImage(logger)
    d_loc = indice._leer_metadatos(locales["DJI_0001_T.JPG"], gi, _Cb())
    d_gcs = indice._leer_metadatos(f"{RAIZ}/ORIGEN/DJI_0001_T.JPG", gi, _Cb())
    assert d_gcs.meta_leida is True
    assert indice.campos_posicion(d_gcs) == indice.campos_posicion(d_loc)
    assert (d_gcs.timestamp, d_gcs.modelo, d_gcs.make, d_gcs.yaw, d_gcs.gps, d_gcs.pitch,
            d_gcs.flight_yaw) == (d_loc.timestamp, d_loc.modelo, d_loc.make, d_loc.yaw,
                                  d_loc.gps, d_loc.pitch, d_loc.flight_yaw)


def _cfg_cierre(salida):
    return types.SimpleNamespace(output_folder=salida, include_v=True, flight_height=50.0,
                                 calculate_proyected_distance=False, gen_meta_location=True,
                                 convert_to_tif=False, cropping_rgb=False, input_folder="")


def _manifiesto(tmp_path, nombre_db, salida, locales, datos, gi, bucket=None, subir=False):
    filas = []
    for i, (tipo, nombre) in enumerate((("RGB", "20260115_100000_DJI_0001_W.JPG"),
                                        ("TERMICA", "20260115_100100_DJI_0002_T.JPG"))):
        loc = locales[i]
        if subir:
            bucket.objetos[f"PLANTA/INSP/{tipo}/PB1/PB1_V1/{nombre}"] = Path(loc).read_bytes()
        else:
            dest = Path(salida) / tipo / "PB1" / "PB1_V1"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / nombre).write_bytes(Path(loc).read_bytes())
        filas.append(FilaManifiesto(
            ruta_origen=f"/sd/{nombre}", tipo=tipo, timestamp_exif=None, modelo=None,
            pb="1", vuelo="1", nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original=f"{salida}/{tipo}/PB1/PB1_V1/{nombre}",
            ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=False,
            bytes_origen=1, **indice.campos_posicion(datos[i])))
    m = Manifiesto(tmp_path / nombre_db)
    m.crear_esquema()
    m.insertar_o_reabrir(filas)
    for fila in m.todas():
        m.marcar_hecha(fila["id"], "")
    return m


def test_cierre_no_relee_exif_y_verifica_con_un_listado(bucket, tmp_path, make_dji_jpeg, logger, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    locales = _subir(bucket, tmp_path, make_dji_jpeg, ["a.JPG", "b.JPG"])
    gi = exif.GeneralInformationFromImage(logger)
    datos = [indice._leer_metadatos(f"{RAIZ}/ORIGEN/{n}", gi, _Cb()) for n in ("a.JPG", "b.JPG")]
    assert all(d.meta_leida for d in datos)
    m = _manifiesto(tmp_path, "m.db", RAIZ, [locales["a.JPG"], locales["b.JPG"]], datos, gi,
                    bucket=bucket, subir=True)

    bucket.reloads = bucket.exists = bucket.rangos = bucket.descargas = bucket.listados = 0
    with mock.patch.object(exif.MetaLocation, "leer_exif_imagen",
                           side_effect=AssertionError("relee exif de la salida")):
        cierre.emitir_csvs(m, _cfg_cierre(RAIZ), _Cb())
    assert bucket.descargas == 0 and bucket.rangos == 0

    bucket.reloads = bucket.exists = bucket.listados = 0
    problemas = cierre.verificar(m, _cfg_cierre(RAIZ))
    assert [p for p in problemas if "no existe" in p or "vacío" in p] == []
    assert bucket.reloads == 0 and bucket.exists == 0
    # y sigue detectando ausentes / vacíos con el listado
    del bucket.objetos["PLANTA/INSP/TERMICA/PB1/PB1_V1/20260115_100100_DJI_0002_T.JPG"]
    bucket.objetos["PLANTA/INSP/RGB/PB1/PB1_V1/20260115_100000_DJI_0001_W.JPG"] = b""
    problemas = cierre.verificar(m, _cfg_cierre(RAIZ))
    assert any("no existe" in p and "DJI_0002_T.JPG" in p for p in problemas), problemas
    assert any("vacío" in p and "DJI_0001_W.JPG" in p for p in problemas), problemas
    assert bucket.reloads == 0 and bucket.exists == 0
    m.cerrar()


def test_csv_meta_location_identico_local_y_gcs(bucket, tmp_path, make_dji_jpeg, logger, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    locales = _subir(bucket, tmp_path, make_dji_jpeg, ["a.JPG", "b.JPG"])
    gi = exif.GeneralInformationFromImage(logger)
    d_loc = [indice._leer_metadatos(locales[n], gi, _Cb()) for n in ("a.JPG", "b.JPG")]
    d_gcs = [indice._leer_metadatos(f"{RAIZ}/ORIGEN/{n}", gi, _Cb()) for n in ("a.JPG", "b.JPG")]
    salida_local = str(tmp_path / "OUT")
    m_loc = _manifiesto(tmp_path, "l.db", salida_local, [locales["a.JPG"], locales["b.JPG"]], d_loc, gi)
    m_gcs = _manifiesto(tmp_path, "g.db", RAIZ, [locales["a.JPG"], locales["b.JPG"]], d_gcs, gi,
                        bucket=bucket, subir=True)
    cierre.emitir_csvs(m_loc, _cfg_cierre(salida_local), _Cb())
    cierre.emitir_csvs(m_gcs, _cfg_cierre(RAIZ), _Cb())

    csv_loc = {p.name.split("_", 1)[-1]: p.read_bytes() for p in Path(salida_local, "CSVs").glob("*.csv")}
    csv_gcs = {k.rsplit("/", 1)[-1].split("_", 1)[-1]: v for k, v in bucket.objetos.items()
               if k.startswith("PLANTA/INSP/CSVs/") and k.endswith(".csv")}
    assert csv_loc and set(csv_loc) == set(csv_gcs)
    for nombre in csv_loc:
        assert csv_loc[nombre] == csv_gcs[nombre], nombre
    m_loc.cerrar()
    m_gcs.cerrar()


def test_get_model_sin_image_model_no_peta(tmp_path, make_dji_jpeg, logger):
    ruta = make_dji_jpeg(str(tmp_path / "sin_modelo.JPG"))  # sin model en el EXIF
    gi = exif.GeneralInformationFromImage(logger)
    assert gi.get_model(ruta, _Cb()) is None
    assert gi.error_exif_data == 1
