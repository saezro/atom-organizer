"""Rutas `gs://…` a través del CLI (`organize_cli.main`) y de la lectura de
estadillos (`atom_core.estadillo`).

Cubre:
1. `organize_cli.main` con `--origen`/`--destino` en `gs://…`: las URIs
   llegan INTACTAS (sin `Path(...).resolve()`, sin `mkdir`) a `run_task`.
2. Regresión: rutas locales siguen comportándose exactamente igual (origen
   inexistente -> `return 2` con el mismo mensaje).
3-4. `atom_core.estadillo._read_dataframe`: paridad `gs://` vs local para
   `.csv` (sep=';') y `.xlsx` (extensión decidida sobre la ruta ORIGINAL).
5. `atom_core.estadillo.read_estadillo_info`: `gs://` que existe (dict sin
   `error`) y que no existe (`{"error": "No existe el estadillo: ..."}`).

Mismo estilo de doble en memoria del backend GCS que
`tests/test_almacen_gcs.py`/`tests/test_almacen_rutas.py`/
`tests/test_post_dji_tif_almacen.py`: NO se toca el `startswith` literal de
`BucketFalso.list_blobs` (replica a propósito la semántica de prefijo de GCS
real), y `BlobFalso.download_to_filename` lanza `_NotFound` (nunca
`FileNotFoundError`: el SDK real de GCS jamás lanza esa excepción de Python).
"""
from pathlib import Path

import pandas as pd
import pytest

import atom_core.almacen as almacen_mod
from atom_core.almacen_gcs import AlmacenGCS
from atom_core import estadillo


@pytest.fixture(autouse=True)
def _cache_limpia():
    """`_ALMACENES` es una caché a nivel de módulo (a propósito, ver docstring
    en `atom_core/almacen.py`); hay que vaciarla entre tests."""
    almacen_mod._limpiar_cache_almacenes()
    yield
    almacen_mod._limpiar_cache_almacenes()


# --- Doble en memoria del backend GCS ---------------------------------------

_NotFound = type("NotFound", (Exception,), {})


class BlobFalso:
    def __init__(self, bucket, nombre):
        self.bucket = bucket
        self.name = nombre

    def exists(self) -> bool:
        return self.name in self.bucket.objetos

    def upload_from_filename(self, ruta_local: str) -> None:
        self.bucket.objetos[self.name] = Path(ruta_local).read_bytes()

    def download_to_filename(self, ruta_local: str) -> None:
        if self.name not in self.bucket.objetos:
            raise _NotFound(self.name)
        Path(ruta_local).write_bytes(self.bucket.objetos[self.name])

    def delete(self) -> None:
        del self.bucket.objetos[self.name]

    def reload(self) -> None:
        if self.name not in self.bucket.objetos:
            raise _NotFound(self.name)
        self.size = len(self.bucket.objetos[self.name])


class BucketFalso:
    def __init__(self):
        self.objetos: dict[str, bytes] = {}

    def blob(self, nombre: str) -> BlobFalso:
        return BlobFalso(self, nombre)

    def list_blobs(self, prefix: str = ""):
        for nombre in list(self.objetos):
            if nombre.startswith(prefix):
                yield BlobFalso(self, nombre)

    def copy_blob(self, blob_origen: BlobFalso, bucket_destino: "BucketFalso", nombre_destino: str):
        bucket_destino.objetos[nombre_destino] = self.objetos[blob_origen.name]
        return BlobFalso(bucket_destino, nombre_destino)


class ClienteFalso:
    def __init__(self, bucket: BucketFalso):
        self._bucket = bucket

    def bucket(self, nombre: str) -> BucketFalso:
        return self._bucket


def _sembrar_almacen_gcs(bucket_nombre: str) -> BucketFalso:
    """Mete un `AlmacenGCS` de prueba (sin SDK real) en la caché de
    `abrir_almacen`, bajo la clave que usaría `gs://<bucket_nombre>/...`."""
    bucket = BucketFalso()
    cliente = ClienteFalso(bucket)
    almacen = AlmacenGCS(bucket_nombre, prefijo_raiz="", cliente=cliente)
    almacen_mod._ALMACENES[f"gs://{bucket_nombre}"] = almacen
    return bucket


# --- 1. CLI con --origen/--destino en gs://: URIs intactas hasta run_task ---

def test_cli_rutas_gcs_llegan_intactas_a_run_task(monkeypatch):
    import organize_cli
    from atom_core import organize

    bucket = _sembrar_almacen_gcs("b")
    # `es_carpeta("gs://b/x")` -> `existe_ruta` -> hay algo colgando del
    # prefijo "x/": basta un objeto cualquiera.
    bucket.objetos["x/DJI_0001.JPG"] = b"contenido"

    capturado = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        capturado.update(params)
        return {"status": "ok"}

    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    codigo = organize_cli.main([
        "--origen", "gs://b/x", "--destino", "gs://b/y", "--quiet", "--json",
    ])

    assert codigo != 2
    assert capturado.get("origen") == "gs://b/x"
    assert capturado.get("destino") == "gs://b/y"
    # Nada de `mkdir` local para el destino gs://: sin objetos nuevos bajo
    # "y/" en el bucket falso (`_fake_run_task` no escribe nada) y sin que
    # haya reventado por intentar `Path.mkdir` sobre una URI.
    assert not any(nombre.startswith("y/") for nombre in bucket.objetos)


# --- 2. Regresión: rutas locales, comportamiento idéntico al de siempre -----

def test_cli_rutas_locales_origen_inexistente_devuelve_2(tmp_path, capsys):
    import organize_cli

    origen = tmp_path / "no_existe"
    destino = tmp_path / "salida"

    codigo = organize_cli.main([
        "--origen", str(origen), "--destino", str(destino), "--quiet", "--json",
    ])

    salida = capsys.readouterr()
    assert codigo == 2
    assert f"error: la carpeta de origen no existe: {origen.expanduser().resolve()}" in salida.err


# --- 3. _read_dataframe: paridad gs:// vs local para .csv (sep=';') --------

def test_read_dataframe_csv_paridad_gcs_local(tmp_path):
    contenido = "PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n1;1;2026:03:18;10:00:00;11:00:00\n"

    local = tmp_path / "estadillo.csv"
    local.write_text(contenido, encoding="utf-8")

    bucket = _sembrar_almacen_gcs("b")
    bucket.objetos["estadillo.csv"] = contenido.encode("utf-8")

    df_local = estadillo._read_dataframe(str(local))
    df_gcs = estadillo._read_dataframe("gs://b/estadillo.csv")

    assert df_local.columns.tolist() == df_gcs.columns.tolist()
    assert df_local.equals(df_gcs)


# --- 4. _read_dataframe: paridad gs:// vs local para .xlsx ------------------

def test_read_dataframe_xlsx_paridad_gcs_local(tmp_path):
    df_origen = pd.DataFrame({
        "PB": [1], "Vuelo": [1], "Fecha": ["2026:03:18"],
        "Hora_de_inicio": ["10:00:00"], "Hora_final": ["11:00:00"],
    })

    local = tmp_path / "estadillo.xlsx"
    df_origen.to_excel(local, index=False)

    bucket = _sembrar_almacen_gcs("b")
    bucket.objetos["estadillo.xlsx"] = local.read_bytes()

    # Si la extensión se decidiera sobre el temporal descargado (que puede no
    # conservar el sufijo) en vez de sobre la URI original, esto intentaría
    # leer un .xlsx binario como CSV y reventaría en vez de devolver un df.
    df_local = estadillo._read_dataframe(str(local))
    df_gcs = estadillo._read_dataframe("gs://b/estadillo.xlsx")

    assert df_local.columns.tolist() == df_gcs.columns.tolist()
    assert df_local.equals(df_gcs)


# --- 5. read_estadillo_info: gs:// que existe / que no existe --------------

def test_read_estadillo_info_gcs_existente_sin_error():
    contenido = "PB;Vuelo;Fecha;Hora_de_inicio;Hora_final\n1;1;2026:03:18;10:00:00;11:00:00\n"
    bucket = _sembrar_almacen_gcs("b")
    bucket.objetos["estadillo.csv"] = contenido.encode("utf-8")

    info = estadillo.read_estadillo_info("gs://b/estadillo.csv")

    assert "error" not in info


def test_read_estadillo_info_gcs_inexistente_da_error():
    _sembrar_almacen_gcs("b")  # bucket vacío: "estadillo.csv" no existe

    info = estadillo.read_estadillo_info("gs://b/estadillo.csv")

    assert info == {"error": "No existe el estadillo: gs://b/estadillo.csv"}


# --- 6. Regresión: `--origen gs://…` + ORGANIZER_INGEST_SECRET (Cloud Run) --
#
# Es la ruta REAL de producción: el Job de Cloud Run siempre trae
# ORGANIZER_INGEST_SECRET en el entorno. Con el bug `origen.name` (AttributeError:
# `str` no tiene `.name`) esta ruta reventaba con `origen` como URI `gs://…`, y
# como la llamada a `reporter.iniciar(...)` NO está envuelta en ningún
# `try/except`, la excepción se propaga fuera de `main()` sin más: si el fix
# (`nombre_de(str(origen))`) se revierte, este test falla con un
# `AttributeError` sin necesidad de aserción extra.

class _FakeRunReporter:
    """Doble mínimo de `atom_core.run_reporter.RunReporter`: registra la
    llamada a `iniciar` y se declara `activo` para no desviar el flujo del CLI
    hacia la rama de "la Suite no aceptó el alta"."""

    instancias: list["_FakeRunReporter"] = []

    def __init__(self, auth=None, *, secreto=None, tipo=None):
        self.activo = True
        self.iniciar_kwargs = None
        type(self).instancias.append(self)

    def iniciar(self, **kwargs):
        self.iniciar_kwargs = kwargs

    def fin(self, **kwargs):
        pass


def _preparar_cli_con_secreto(monkeypatch):
    """Común a los dos tests de abajo: mockea `run_task` (no interesa el
    pipeline en sí, solo qué le llega a `reporter.iniciar`) y sustituye
    `RunReporter` por el doble de arriba. Devuelve la lista de instancias
    creadas (normalmente 1)."""
    import organize_cli
    from atom_core import organize
    import atom_core.run_reporter as run_reporter_mod

    def _fake_run_task(task, params, emit, avanzado=None):
        return {"status": "ok"}

    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    _FakeRunReporter.instancias = []
    monkeypatch.setattr(run_reporter_mod, "RunReporter", _FakeRunReporter)

    monkeypatch.setenv("ORGANIZER_INGEST_SECRET", "secreto-test")
    monkeypatch.delenv("CLOUD_RUN_EXECUTION", raising=False)

    return _FakeRunReporter.instancias


def test_cli_gcs_con_ingest_secret_no_revienta_y_reporta_basename_de_la_uri(monkeypatch):
    import organize_cli

    instancias = _preparar_cli_con_secreto(monkeypatch)

    bucket = _sembrar_almacen_gcs("b")
    bucket.objetos["x/DJI_0001.JPG"] = b"contenido"

    codigo = organize_cli.main([
        "--origen", "gs://b/x", "--destino", "gs://b/y", "--quiet", "--json",
    ])

    assert codigo != 2
    assert len(instancias) == 1
    assert instancias[0].iniciar_kwargs["inspeccion"] == "x"


def test_cli_local_con_ingest_secret_reporta_basename_de_siempre(tmp_path, monkeypatch):
    import organize_cli

    instancias = _preparar_cli_con_secreto(monkeypatch)

    origen = tmp_path / "VUELO_1"
    origen.mkdir()
    destino = tmp_path / "salida"

    codigo = organize_cli.main([
        "--origen", str(origen), "--destino", str(destino), "--quiet", "--json",
    ])

    assert codigo != 2
    assert len(instancias) == 1
    assert instancias[0].iniciar_kwargs["inspeccion"] == "VUELO_1"


# --- 7. `_contar_imagenes` sobre gs://: cuenta bien, y fail-open si listar revienta ---

def test_contar_imagenes_gcs_cuenta_solo_extensiones_de_imagen():
    import organize_cli

    bucket = _sembrar_almacen_gcs("b")
    bucket.objetos["x/DJI_0001.JPG"] = b"a"
    bucket.objetos["x/DJI_0002.tif"] = b"b"
    bucket.objetos["x/estadillo.csv"] = b"c"
    bucket.objetos["x/notas.txt"] = b"d"
    # Un vuelo real cuelga de subcarpetas por PB: el conteo DEBE ser recursivo,
    # igual que el `os.scandir` del camino local.
    bucket.objetos["x/sub/DJI_0003.JPG"] = b"e"

    assert organize_cli._contar_imagenes("gs://b/x") == 3


def test_contar_imagenes_gcs_fail_open_si_listar_revienta(monkeypatch):
    import organize_cli

    def _abrir_roto(ruta):
        raise RuntimeError("boom: SDK caído")

    monkeypatch.setattr(organize_cli, "abrir_almacen", _abrir_roto)

    assert organize_cli._contar_imagenes("gs://b/x") == 0
