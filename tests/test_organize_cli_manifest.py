import json
import organize_cli


MANIFEST = {
    "version": 1,
    "planta": "Aerotools--CALAMOCHA--2026--T_Modulos",
    "subido_en": "2026-08-17T143022Z",
    "subido_por": "ofi@aerotools.es",
    "ficheros": [{"orden": 1, "objeto": "01__aa.csv", "nombre_original": "dia1.csv",
                  "md5_b64": "oQ==", "bytes": 8}],
    "validacion": {"vuelos_detectados": 5, "filas_con_problemas": 0},
}


def _montar(tmp_path):
    base = tmp_path / "Aerotools--CALAMOCHA--2026--T_Modulos" / "ESTADILLOS" / "actual"
    base.mkdir(parents=True)
    (base / "manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (base / "01__aa.csv").write_bytes(b"PB;Vuelo")
    return base / "manifest.json"


def test_publicar_estadillos_no_hace_nada_si_no_es_el_shard_0(tmp_path):
    ruta = _montar(tmp_path)
    r = organize_cli.publicar_estadillos(str(ruta), shard_index=3)
    assert r["publicados"] == 0
    assert r["motivo"] == "no-lider"


def test_publicar_estadillos_no_escribe_si_la_planta_no_existe_en_el_bucket(tmp_path):
    ruta = _montar(tmp_path)
    escrituras = []

    class PubFalso:
        @staticmethod
        def token_metadata(**k): return "tok"
        @staticmethod
        def prefijo_existe(bucket, prefijo, **k): return False
        @staticmethod
        def subir_objeto(bucket, objeto, datos, **k):
            escrituras.append(objeto)
            return True

    r = organize_cli.publicar_estadillos(str(ruta), shard_index=0, publicar=PubFalso)
    assert escrituras == [], "el bucket de plantas es SOLO para plantas: sin carpeta previa no se escribe"
    assert r["publicados"] == 0
    assert r["motivo"] == "planta-no-existe"


def test_publicar_estadillos_sube_con_el_nombre_original(tmp_path):
    ruta = _montar(tmp_path)
    escrituras = []

    class PubFalso:
        @staticmethod
        def token_metadata(**k): return "tok"
        @staticmethod
        def prefijo_existe(bucket, prefijo, **k): return True
        @staticmethod
        def subir_objeto(bucket, objeto, datos, **k):
            escrituras.append((bucket, objeto, datos))
            return True

    r = organize_cli.publicar_estadillos(str(ruta), shard_index=0, publicar=PubFalso)
    assert escrituras == [(
        "plantas_pv_nl",
        "CALAMOCHA/ESTADILLOS/2026-08-17T143022Z/dia1.csv",
        b"PB;Vuelo",
    )]
    assert r["publicados"] == 1


def test_publicar_estadillos_tolera_un_manifest_ilegible(tmp_path):
    (tmp_path / "manifest.json").write_text("{roto", encoding="utf-8")
    r = organize_cli.publicar_estadillos(str(tmp_path / "manifest.json"), shard_index=0)
    assert r["publicados"] == 0
    assert r["motivo"] == "manifest-ilegible"
