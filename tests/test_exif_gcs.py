"""EXIF desde `gs://` (Cloud Run): timestamp, modelo y XMP se leen por RANGO de
cabecera del bucket (falso), sin que PIL/open vean la URI. Y el indice con
origen `gs://` asigna PB/vuelo igual que en local (antes: todo a SIN_ORDENAR
por FileNotFoundError al abrir `gs://...` con PIL)."""
import datetime as dt
import os
import sqlite3

import pytest

import exif as exif_mod
import utils
from atom_core import phases
from tests.test_giro_manifiesto_gcs import _BlobGen, _BucketGen, bucket  # noqa: F401 (fixture)
from tests.test_organizado_plan_apply_e2e import (  # noqa: F401 (fixtures)
    _cfg, _inspeccion,
)
from tests.test_organizado_shards_fases import _correr_fase

RAIZ = "gs://bucket-falso/PLANTA/INSP"
TS = dt.datetime(2026, 1, 15, 10, 0, 0)


class _BlobRango(_BlobGen):
    """Blob falso con lectura por rango; apunta los bytes pedidos al bucket."""

    def download_as_bytes(self, start=0, end=None):
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        datos = self.bucket.objetos[self.name]
        trozo = datos[start:(None if end is None else end + 1)]
        self.bucket.rangos.append((self.name, len(trozo)))
        return trozo


@pytest.fixture
def bucket_rango(bucket, tmp_path):  # noqa: F811
    (tmp_path / "tmp").mkdir(exist_ok=True)
    bucket.rangos = []
    bucket.blob = lambda nombre: _BlobRango(bucket, nombre)
    return bucket


def _exif(logger):
    return exif_mod.GeneralInformationFromImage(logger)


def test_timestamp_y_modelo_desde_gcs_por_rango(bucket_rango, make_dji_jpeg, logger, tmp_path):
    local = tmp_path / "a.JPG"
    make_dji_jpeg(str(local), dt_val=TS, gimbal_yaw=0.0, make="DJI")
    bucket_rango.objetos["PLANTA/INSP/a.JPG"] = local.read_bytes()
    g = _exif(logger)
    esperado_modelo = g.get_model(str(local), type("C", (), {"emit": lambda *a: None})())
    assert g.get_timestamp_from_image(RAIZ + "/a.JPG") == TS
    assert g.get_model(RAIZ + "/a.JPG", type("C", (), {"emit": lambda *a: None})()) == esperado_modelo
    # Solo cabecera: nunca mas de 256 KB por lectura, y sin descarga entera.
    assert bucket_rango.rangos and all(n <= exif_mod._XMP_HEADER_BYTES for _k, n in bucket_rango.rangos)
    assert not os.path.exists("gs:")


def test_exif_gcs_sin_soporte_de_rango_lee_objeto_entero(bucket, make_dji_jpeg, logger, tmp_path):
    (tmp_path / "tmp").mkdir(exist_ok=True)
    local = tmp_path / "a.JPG"
    make_dji_jpeg(str(local), dt_val=TS, gimbal_yaw=0.0, make="DJI")
    bucket.objetos["PLANTA/INSP/a.JPG"] = local.read_bytes()  # blob sin download_as_bytes
    g = _exif(logger)
    assert g.get_timestamp_from_image(RAIZ + "/a.JPG") == TS
    assert g.get_make(RAIZ + "/a.JPG") == "DJI"
    assert "<x:xmpmeta" in exif_mod.leer_bloque_xmp(RAIZ + "/a.JPG")


def test_exif_gcs_objeto_inexistente_es_none(bucket_rango, logger):
    assert _exif(logger).get_timestamp_from_image(RAIZ + "/no.JPG") is None


def _estado_indice(ruta_db):
    con = sqlite3.connect(ruta_db)
    try:
        return sorted(con.execute(
            "SELECT ruta_origen, ruta_salida_original FROM imagenes").fetchall())
    finally:
        con.close()


def _normalizar(filas, raiz_origen, raiz_destino):
    return [(o.replace(raiz_origen, "").lstrip("/"),
             (s or "").replace(raiz_destino, "").lstrip("/")) for o, s in filas]


def test_indice_con_origen_gcs_no_deja_imagenes_en_sin_ordenar(
        _inspeccion, bucket_rango, logger, monkeypatch, tmp_path):
    monkeypatch.setattr(phases.paralelismo_mod, "ControladorAdaptativo", lambda **kw: None)
    origen = _inspeccion / "origen"
    # Referencia: indice local.
    cfg_l = _cfg(_inspeccion, output_folder=str(_inspeccion / "dest_local"))
    _correr_fase(logger, monkeypatch, cfg_l, "indice")
    ref = _normalizar(_estado_indice(os.path.join(cfg_l.output_folder, ".organizado", "manifiesto.db")),
                      str(origen), cfg_l.output_folder)
    # Mismo origen subido al bucket falso.
    for f in origen.iterdir():
        bucket_rango.objetos["PLANTA/INSP/origen/" + f.name] = f.read_bytes()
        bucket_rango.generaciones["PLANTA/INSP/origen/" + f.name] = 1
    cfg_g = _cfg(_inspeccion, input_folder=RAIZ + "/origen", output_folder=str(_inspeccion / "dest_gcs"))
    object.__setattr__(cfg_g, "planta", "INSP")  # origen sintético de 3 niveles: planta explícita
    _correr_fase(logger, monkeypatch, cfg_g, "indice")
    got = _normalizar(_estado_indice(os.path.join(cfg_g.output_folder, ".organizado", "manifiesto.db")),
                      RAIZ + "/origen", cfg_g.output_folder)
    sin_ordenar = [s for _o, s in got if "SIN_ORDENAR" in s]
    assert len(sin_ordenar) == 1 and "9999" in sin_ordenar[0]  # solo la de 2020, como en local
    assert got == ref
    assert any("PB1" in s for _o, s in got)


def test_cuota_cgroup_cpu_redondea_no_trunca(monkeypatch):
    for cpu_max, esperado in (("399000 100000", 4), ("400000 100000", 4),
                              ("199000 100000", 2), ("200000 100000", 2),
                              ("50000 100000", 1)):
        monkeypatch.setattr(utils, "_leer_texto", lambda r, v=cpu_max: v if r.endswith("cpu.max") else None)
        assert utils._cuota_cgroup_cpu() == esperado
    monkeypatch.setattr(os, "sched_getaffinity", lambda _p: set(range(8)))
    monkeypatch.setattr(utils, "_leer_texto", lambda r: "399000 100000" if r.endswith("cpu.max") else None)
    assert utils.cpus_efectivas() == 4
