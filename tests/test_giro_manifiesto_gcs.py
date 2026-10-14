"""Raíz gs:// (Cloud Run): el giro va a un manifiesto LOCAL descargado del bucket
(si existe) y se SUBE al terminar; fallo de subida/descarga = error visible."""
import os
import sqlite3
import types

import pytest

import pipeline
from atom_core import almacen as almacen_mod
from atom_core.almacen_gcs import AlmacenGCS
from atom_core.manifiesto import Manifiesto
from tests.test_almacen_gcs import BucketFalso, ClienteFalso

RAIZ = "gs://bucket-falso/PLANTA/INSPECCION"
CLAVE = "PLANTA/INSPECCION/.organizado/manifiesto.db"


class _BlobGen:
    """BlobFalso con `generation` y precondiciones (`if_generation_match`)."""

    def __init__(self, bucket, nombre):
        self.bucket, self.name = bucket, nombre

    def exists(self):
        return self.name in self.bucket.objetos

    def reload(self):
        if self.name not in self.bucket.objetos:
            raise FileNotFoundError(self.name)
        self.size = len(self.bucket.objetos[self.name])
        self.generation = self.bucket.generaciones[self.name]

    def _comprobar(self, if_generation_match):
        if if_generation_match is None:
            return
        actual = self.bucket.generaciones.get(self.name, 0)
        if actual != if_generation_match:
            self.bucket.rechazos += 1
            err = RuntimeError("412 PreconditionFailed")
            err.code = 412
            raise err

    def upload_from_filename(self, ruta, if_generation_match=None):
        self._comprobar(if_generation_match)
        self.bucket.objetos[self.name] = open(ruta, "rb").read()
        self.bucket.generaciones[self.name] = self.bucket.generaciones.get(self.name, 0) + 1
        if self.bucket.antes_de_subir_siguiente:
            self.bucket.antes_de_subir_siguiente = None

    def download_to_filename(self, ruta, if_generation_match=None):
        self._comprobar(if_generation_match)
        open(ruta, "wb").write(self.bucket.objetos[self.name])

    def delete(self):
        del self.bucket.objetos[self.name]


class _BucketGen(BucketFalso):
    def __init__(self):
        super().__init__()
        self.generaciones = {}
        self.rechazos = 0
        self.antes_de_subir_siguiente = None

    def blob(self, nombre):
        return _BlobGen(self, nombre)


@pytest.fixture
def bucket(monkeypatch, tmp_path):
    bucket = _BucketGen()
    almacen_mod._limpiar_cache_almacenes()
    almacen_mod._ALMACENES["gs://bucket-falso"] = AlmacenGCS("bucket-falso", cliente=ClienteFalso(bucket))
    monkeypatch.setattr(pipeline.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    yield bucket
    almacen_mod._limpiar_cache_almacenes()


def _cb():
    mensajes = []
    return types.SimpleNamespace(emit=lambda p=None, *a, **k: mensajes.append(p)), mensajes


def _gen(logger):
    obj = pipeline.GenStructFolder(logger)
    obj.root_folder = RAIZ
    obj.csvs_root_folder = RAIZ + "/CSVs"
    return obj


def _split(tmp_path):
    from utils import OrganizerLogger as ol
    return pipeline.SplitImages(ol("t_giro_gcs", log_dir=str(tmp_path / "L"), create_file_handler=False))


def _vuelos_del_bucket(bucket, destino):
    local = destino / "m.db"
    local.write_bytes(bucket.objetos[CLAVE])
    m = Manifiesto(local)
    try:
        return m.vuelos()
    finally:
        m.cerrar()


def test_giro_gcs_se_registra_y_se_sube(bucket, logger, tmp_path):
    cb, mensajes = _cb()
    _gen(logger).registrar_giro_vuelo(RAIZ + "/TERMICA/PB1/PB1_V1", 90, 10, 10, 0, 0, "ok", None, cb)
    assert not any("ERROR" in str(m) for m in mensajes), mensajes
    assert CLAVE in bucket.objetos
    vuelos = _vuelos_del_bucket(bucket, tmp_path)
    assert [v["angulo"] for v in vuelos.values()] == [90]


def test_giro_gcs_parte_del_manifiesto_del_bucket_y_lo_conserva(bucket, logger, tmp_path):
    cb, _ = _cb()
    g = _gen(logger)
    g.registrar_giro_vuelo(RAIZ + "/TERMICA/PB1/PB1_V1", 90, 10, 10, 0, 0, "ok", None, cb)
    # Otro proceso/tanda: la copia local desaparece; el bucket manda.
    import shutil
    shutil.rmtree(tmp_path / "tmp")
    g.registrar_giro_vuelo(RAIZ + "/TERMICA/PB2/PB2_V1", 270, 10, 0, 10, 0, "ok", None, cb)
    assert sorted(v["angulo"] for v in _vuelos_del_bucket(bucket, tmp_path).values()) == [90, 270]


def test_giro_gcs_lectura_usa_la_copia_del_bucket(bucket, logger, tmp_path):
    cb, _ = _cb()
    g = _gen(logger)
    g.registrar_giro_vuelo(RAIZ + "/TERMICA/PB1/PB1_V1", 270, 10, 0, 10, 0, "ok", None, cb)
    sp = _split(tmp_path)
    fila = sp._giro_desde_manifiesto(RAIZ, "PB1_V1")
    assert fila is not None and fila["angulo"] == 270
    assert sp._giro_desde_manifiesto(RAIZ, "PB9_V9") is None


def test_giro_gcs_sin_manifiesto_en_bucket_es_ausencia_de_dato(bucket, logger, tmp_path):
    assert _split(tmp_path)._giro_desde_manifiesto(RAIZ, "PB1_V1") is None


def test_giro_gcs_fallo_de_subida_es_error_visible(bucket, logger, monkeypatch):
    def _falla(origen, destino, **kw):
        raise RuntimeError("403 sin permiso")
    monkeypatch.setattr(pipeline.almacen, "publicar_en", _falla)
    cb, mensajes = _cb()
    g = _gen(logger)
    g.registrar_giro_vuelo(RAIZ + "/TERMICA/PB1/PB1_V1", 90, 10, 10, 0, 0, "ok", None, cb)
    assert any("ERROR" in str(m) and "NO se ha guardado" in str(m) and "403" in str(m)
               for m in mensajes), mensajes
    assert CLAVE not in bucket.objetos


def test_giro_gcs_lectura_con_descarga_rota_es_error_sqlite(bucket, logger, monkeypatch, tmp_path):
    bucket.objetos[CLAVE] = b"x"
    monkeypatch.setattr(pipeline.shutil, "copyfile",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("red caída")))
    with pytest.raises(sqlite3.Error):
        _split(tmp_path)._giro_desde_manifiesto(RAIZ, "PB1_V1")


# ---------------------------------------------------------------- concurrencia

@pytest.fixture
def bucket_gen(monkeypatch, tmp_path):
    bucket = _BucketGen()
    almacen_mod._limpiar_cache_almacenes()
    almacen_mod._ALMACENES["gs://bucket-falso"] = AlmacenGCS("bucket-falso", cliente=ClienteFalso(bucket))
    monkeypatch.setattr(pipeline.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    yield bucket
    almacen_mod._limpiar_cache_almacenes()


def test_dos_procesos_concurrentes_conservan_ambas_filas(bucket_gen, logger, tmp_path, monkeypatch):
    """Proceso B sube entre la descarga y la subida de A: A recibe 412, re-descarga,
    reaplica su fila y sube; el manifiesto final lleva las dos filas."""
    cb, mensajes = _cb()
    g = _gen(logger)
    sube_original = pipeline._subir_manifiesto_gcs
    estado = {"intruso": True}

    def _subida_con_intruso(raiz, local, generacion):
        if estado["intruso"]:
            estado["intruso"] = False
            # «Otro proceso» (otra copia local, otro tmp) registra PB2 justo antes.
            otro = tmp_path / "otro.db"
            m = Manifiesto(otro)
            m.crear_esquema()
            if generacion:
                from atom_core.almacen import descargar_con_generacion
                m.cerrar()
                descargar_con_generacion(RAIZ + "/.organizado/manifiesto.db", otro)
                m = Manifiesto(otro)
            m.guardar_vuelos([{"pb": "2", "vuelo": "1", "sufijo": "", "angulo": 270, "n_total": 1,
                               "n_90": 0, "n_270": 1, "n_0": 0, "pct_90": 0.0, "pct_270": 100.0,
                               "pct_0": 0.0, "pct_ganadora": 100.0, "estado": "ok", "motivo": None,
                               "version_algoritmo": 0}])
            m.cerrar()
            almacen_mod.publicar_en(str(otro), RAIZ + "/.organizado/manifiesto.db",
                                    if_generation_match=generacion)
        sube_original(raiz, local, generacion)

    monkeypatch.setattr(pipeline, "_subir_manifiesto_gcs", _subida_con_intruso)
    g.registrar_giro_vuelo(RAIZ + "/TERMICA/PB1/PB1_V1", 90, 10, 10, 0, 0, "ok", None, cb)
    assert not any("ERROR" in str(m) for m in mensajes), mensajes
    assert bucket_gen.rechazos == 1
    assert sorted(v["angulo"] for v in _vuelos_del_bucket(bucket_gen, tmp_path).values()) == [90, 270]


def test_412_agotado_es_error_visible(bucket_gen, logger, monkeypatch):
    def _siempre_412(raiz, local, generacion):
        err = RuntimeError("412 PreconditionFailed")
        err.code = 412
        raise err
    monkeypatch.setattr(pipeline, "_subir_manifiesto_gcs", _siempre_412)
    cb, mensajes = _cb()
    _gen(logger).registrar_giro_vuelo(RAIZ + "/TERMICA/PB1/PB1_V1", 90, 10, 10, 0, 0, "ok", None, cb)
    assert any("ERROR" in str(m) and "NO se ha guardado" in str(m) and "412" in str(m)
               for m in mensajes), mensajes


def test_publicar_en_local_rechaza_precondicion(tmp_path):
    (tmp_path / "a").write_text("x")
    with pytest.raises(ValueError):
        almacen_mod.publicar_en(tmp_path / "a", str(tmp_path / "b"), if_generation_match=0)


# ---------------------------------------------------------------- phases.py

def test_phases_con_salida_gcs_usa_copia_local_y_sube(bucket_gen, tmp_path, monkeypatch):
    """organizar_plan_apply con output gs://: no crea carpeta local «gs:» y sube el manifiesto."""
    from atom_core import phases as phases_mod

    class _Sig:
        def __init__(self):
            self.m = []

        def emit(self, *a, **k):
            self.m.append(a[0] if a else None)

    def _indice_que_corta(*a, **k):
        raise RuntimeError("corte simulado")

    monkeypatch.setattr(phases_mod.indice_mod, "construir_indice", _indice_que_corta)
    monkeypatch.setattr(phases_mod, "_copiar_estadillos_a_salida", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)
    host = types.SimpleNamespace(
        organizer_logger_obj=types.SimpleNamespace(logger=__import__("logging").getLogger("t")),
        split_images_obj=types.SimpleNamespace(exif_management_obj=None), gen_struct_folder_obj=None, rgb_cropping_obj=None, config_obj=None)
    cfg = types.SimpleNamespace(output_folder=RAIZ, input_folder=str(tmp_path / "o"))
    with pytest.raises(RuntimeError):
        phases_mod.PipelinePhasesMixin.organizar_plan_apply(host, cfg, _Sig(), _Sig(), _Sig())
    assert not (tmp_path / "gs:").exists() and not (tmp_path / "gs:").is_symlink()
    assert CLAVE in bucket_gen.objetos


# ---------------------------------------------------------------- 412 / rutas únicas

def _err412():
    err = RuntimeError("412 PreconditionFailed")
    err.code = 412
    return err


def _host_phases(tmp_path):
    return types.SimpleNamespace(
        organizer_logger_obj=types.SimpleNamespace(logger=__import__("logging").getLogger("t")),
        split_images_obj=types.SimpleNamespace(exif_management_obj=None), gen_struct_folder_obj=None,
        rgb_cropping_obj=None, config_obj=None)


def test_phases_412_en_subida_final_deja_run_fallido_y_conserva_copia(bucket_gen, tmp_path, monkeypatch):
    """Subida final con 412 y sin excepción previa: el run lanza (fallido) y la copia
    local queda como manifiesto-*-NO-SUBIDO.db, con su ruta en el mensaje."""
    from atom_core import phases as phases_mod
    import glob

    class _Sig:
        def emit(self, *a, **k):
            pass

    def _412(*a, **k):
        raise _err412()

    monkeypatch.setattr(phases_mod.indice_mod, "construir_indice", lambda *a, **k: {"nuevas": 0})
    monkeypatch.setattr(phases_mod, "_copiar_estadillos_a_salida", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_subir_manifiesto_gcs", _412)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "o").mkdir()
    from unittest import mock
    cfg = mock.MagicMock()
    cfg.output_folder, cfg.input_folder = RAIZ, str(tmp_path / "o")
    with pytest.raises(Exception) as exc:
        phases_mod.PipelinePhasesMixin.organizar_plan_apply(_host_phases(tmp_path), cfg, _Sig(), _Sig(), _Sig())
    copias = glob.glob(str(tmp_path / "tmp" / "atom_manifiesto_gcs_no_subido" / "manifiesto-*-NO-SUBIDO.db"))
    assert len(copias) == 1 and os.path.getsize(copias[0]) > 0
    assert copias[0] in str(exc.value) or "412" in str(exc.value) or "NO-SUBIDO" in str(exc.value)


def test_phases_412_con_excepcion_previa_no_la_tapa_y_conserva_copia(bucket_gen, tmp_path, monkeypatch):
    from atom_core import phases as phases_mod
    import glob

    class _Sig:
        def emit(self, *a, **k):
            pass

    def _corte(*a, **k):
        raise ValueError("corte original")

    def _412(*a, **k):
        raise _err412()

    monkeypatch.setattr(phases_mod.indice_mod, "construir_indice", _corte)
    monkeypatch.setattr(phases_mod, "_copiar_estadillos_a_salida", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "_subir_manifiesto_gcs", _412)
    monkeypatch.chdir(tmp_path)
    cfg = types.SimpleNamespace(output_folder=RAIZ, input_folder=str(tmp_path / "o"))
    with pytest.raises(ValueError, match="corte original"):
        phases_mod.PipelinePhasesMixin.organizar_plan_apply(_host_phases(tmp_path), cfg, _Sig(), _Sig(), _Sig())
    assert len(glob.glob(str(tmp_path / "tmp" / "atom_manifiesto_gcs_no_subido" / "*-NO-SUBIDO.db"))) == 1


def test_412_en_descarga_reintenta_y_acaba_bien(bucket_gen, logger, tmp_path, monkeypatch):
    original = pipeline.almacen.descargar_con_generacion
    estado = {"n": 0}

    def _descarga_412_una_vez(origen, destino):
        estado["n"] += 1
        if estado["n"] == 1:
            raise _err412()
        return original(origen, destino)

    monkeypatch.setattr(pipeline.almacen, "descargar_con_generacion", _descarga_412_una_vez)
    cb, mensajes = _cb()
    _gen(logger).registrar_giro_vuelo(RAIZ + "/TERMICA/PB1/PB1_V1", 90, 10, 10, 0, 0, "ok", None, cb)
    assert estado["n"] == 2
    assert not any("ERROR" in str(m) for m in mensajes), mensajes
    assert [v["angulo"] for v in _vuelos_del_bucket(bucket_gen, tmp_path).values()] == [90]


def test_dos_sincronizaciones_usan_rutas_locales_distintas(bucket_gen):
    a, _ = pipeline._sincronizar_manifiesto_gcs(RAIZ)
    b, _ = pipeline._sincronizar_manifiesto_gcs(RAIZ)
    assert a != b and os.path.dirname(a) != os.path.dirname(b)
    pipeline._limpiar_copia_local_manifiesto_gcs(a)
    assert not os.path.exists(os.path.dirname(a)) and os.path.isdir(os.path.dirname(b))
    pipeline._limpiar_copia_local_manifiesto_gcs(b)
