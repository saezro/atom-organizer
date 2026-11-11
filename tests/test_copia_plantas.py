"""Fase `copiar`: SALIDA--… -> <raiz>/<PLANTA>/INSPECCIONES/<TIPO>/<AÑO>/…

Todo contra un almacén en memoria: ni bucket real ni red. El doble cuenta las
llamadas a `borrar` (tiene que ser siempre 0: la fase nunca borra).
"""
import json

import pytest

import organize_cli
from atom_core import copia_plantas as cp

RAIZ = "gs://plantas_pv_nl"
SAL = "gs://datos/SALIDA--RES_GROUP--TOCOPILLA--2026--T_Modulos"
SAL_PRUEBA = "gs://datos/SALIDA-PRUEBA-RES_GROUP--TOCOPILLA--2026--T_Modulos"
DEST = "gs://plantas_pv_nl/TOCOPILLA/INSPECCIONES/T_Modulos/2026"


class Mem:
    """Doble en memoria de la interfaz de backend de copia_plantas."""

    def __init__(self, objetos=None):
        # uri -> (bytes, md5 | None)
        self.o = {u: (b, m) for u, (b, m) in (objetos or {}).items()}
        self.borrados = 0
        self.copias = []
        self.escritos = {}

    def listar(self, prefijo):
        p = prefijo.rstrip("/") + "/"
        return [(u, len(b), m) for u, (b, m) in sorted(self.o.items()) if u.startswith(p)]

    def meta(self, uri):
        if uri not in self.o:
            return None
        b, m = self.o[uri]
        return len(b), m

    def copiar(self, origen, destino):
        if destino in self.o:
            raise FileExistsError(destino)
        self.o[destino] = self.o[origen]
        self.copias.append((origen, destino))

    def escribir_texto(self, uri, texto):
        self.escritos[uri] = texto

    def borrar(self, uri):  # nunca debe llamarse
        self.borrados += 1


def _origen(**extra):
    d = {f"{SAL}/x/y.tif": (b"aaaa", "m1"),
         f"{SAL}/INDICE_x.xlsx": (b"bb", "m2"),
         f"{SAL}/.organizado/copias/a.json": (b"{}", "m3"),
         f"{SAL}/z/.organizado/q": (b"q", "m4")}
    d.update(extra)
    return d


def test_mapeo_salida_y_salida_prueba():
    for s in (SAL, SAL_PRUEBA):
        be = Mem({f"{s}/x/y.tif": (b"aaaa", "m1")})
        plan = cp.planificar_copia(s, RAIZ, backend=be)
        assert [(i.origen, i.destino, i.size, i.md5) for i in plan] == [
            (f"{s}/x/y.tif", f"{DEST}/x/y.tif", 4, "m1")]


def test_plan_excluye_organizado_e_incluye_indice():
    plan = cp.planificar_copia(SAL, RAIZ, backend=Mem(_origen()))
    assert sorted(i.destino for i in plan) == [f"{DEST}/INDICE_x.xlsx", f"{DEST}/x/y.tif"]


@pytest.mark.parametrize("nombre", ["SALIDA--A--B--2026", "SALIDA--A--B--C--D--E", "OTRA--A--B--2026--T",
                                    "SALIDA--A----2026--T"])
def test_nombre_salida_invalido_error(nombre):
    with pytest.raises(ValueError):
        cp.planificar_copia(f"gs://datos/{nombre}", RAIZ, backend=Mem())


def test_destino_vacio_copia_todo():
    be = Mem(_origen())
    plan = cp.planificar_copia(SAL, RAIZ, backend=be)
    inf = cp.ejecutar_copia(plan, solo_plan=False, backend=be)
    assert (inf.copiados, inf.identicos, inf.choques, inf.bytes) == (2, 0, [], 6)
    assert be.o[f"{DEST}/x/y.tif"] == (b"aaaa", "m1")
    assert be.borrados == 0


def test_destino_identico():
    be = Mem(_origen(**{f"{DEST}/x/y.tif": (b"aaaa", "m1")}))
    inf = cp.ejecutar_copia(cp.planificar_copia(SAL, RAIZ, backend=be), solo_plan=False, backend=be)
    assert (inf.copiados, inf.identicos, inf.choques) == (1, 1, [])


def test_destino_distinto_es_choque_y_no_se_toca():
    be = Mem(_origen(**{f"{DEST}/x/y.tif": (b"OTRO", "mX")}))
    inf = cp.ejecutar_copia(cp.planificar_copia(SAL, RAIZ, backend=be), solo_plan=False, backend=be)
    assert [c["ruta"] for c in inf.choques] == [f"{DEST}/x/y.tif"]
    assert inf.choques[0]["motivo"] == "contenido-distinto"
    assert be.o[f"{DEST}/x/y.tif"] == (b"OTRO", "mX")
    assert inf.copiados == 1 and be.borrados == 0


def test_md5_ausente_es_choque_visible_no_copia():
    be = Mem({f"{SAL}/x/y.tif": (b"aaaa", None), f"{DEST}/x/y.tif": (b"aaaa", "m1")})
    inf = cp.ejecutar_copia(cp.planificar_copia(SAL, RAIZ, backend=be), solo_plan=False, backend=be)
    assert [c["motivo"] for c in inf.choques] == ["md5-no-disponible"]
    assert inf.copiados == 0


def test_solo_plan_no_escribe():
    be = Mem(_origen(**{f"{DEST}/x/y.tif": (b"OTRO", "mX")}))
    antes = dict(be.o)
    inf = cp.ejecutar_copia(cp.planificar_copia(SAL, RAIZ, backend=be), solo_plan=True, backend=be)
    assert be.o == antes and be.copias == []
    assert inf.copiados == 1 and len(inf.choques) == 1 and inf.solo_plan is True


def test_carrera_destino_aparece_entre_medias():
    be = Mem(_origen())
    plan = cp.planificar_copia(SAL, RAIZ, backend=be)
    be.o[f"{DEST}/x/y.tif"] = (b"OTRO", "mX")  # aparece tras comprobar
    orig = be.meta
    llamadas = {"n": 0}

    def meta(uri):
        if uri == f"{DEST}/x/y.tif" and llamadas["n"] == 0:
            llamadas["n"] += 1
            return None  # la comprobación previa aún no lo veía
        return orig(uri)
    be.meta = meta
    inf = cp.ejecutar_copia(plan, solo_plan=False, backend=be)
    assert [c["ruta"] for c in inf.choques] == [f"{DEST}/x/y.tif"]
    assert be.o[f"{DEST}/x/y.tif"] == (b"OTRO", "mX")


def test_error_de_copia_se_registra():
    be = Mem(_origen())

    def copiar(o, d):
        raise OSError("boom")
    be.copiar = copiar
    inf = cp.ejecutar_copia(cp.planificar_copia(SAL, RAIZ, backend=be), solo_plan=False, backend=be)
    assert inf.copiados == 0 and len(inf.errores) == 2 and "boom" in inf.errores[0]["motivo"]


def test_fase_escribe_informe_y_exit0_con_choques(monkeypatch):
    be = Mem(_origen(**{f"{DEST}/x/y.tif": (b"OTRO", "mX")}))
    monkeypatch.setattr(cp, "_backend_gcs", lambda: be)
    rc = organize_cli.main(["--destino", SAL, "--fase", "copiar",
                            "--destino-copia", RAIZ, "--ejecucion", "ej1"])
    assert rc == 0
    uri = f"{SAL}/.organizado/copias/ej1.json"
    d = json.loads(be.escritos[uri])
    assert d["copiados"] == 1 and len(d["choques"]) == 1 and d["solo_plan"] is False


def test_fase_error_real_exit_distinto_de_cero(monkeypatch):
    be = Mem(_origen())

    def copiar(o, d):
        raise OSError("boom")
    be.copiar = copiar
    monkeypatch.setattr(cp, "_backend_gcs", lambda: be)
    rc = organize_cli.main(["--destino", SAL, "--fase", "copiar",
                            "--destino-copia", RAIZ, "--ejecucion", "ej1"])
    assert rc != 0
    assert f"{SAL}/.organizado/copias/ej1.json" in be.escritos


def test_fase_sin_ejecucion_es_error(monkeypatch):
    monkeypatch.delenv("CLOUD_RUN_EXECUTION", raising=False)
    monkeypatch.setattr(cp, "_backend_gcs", lambda: Mem(_origen()))
    with pytest.raises(SystemExit) as e:
        organize_cli.main(["--destino", SAL, "--fase", "copiar",
                           "--destino-copia", RAIZ])
    assert e.value.code == 2


def test_copiar_sin_destino_copia_exit2():
    with pytest.raises(SystemExit) as e:
        organize_cli.main(["--destino", SAL, "--fase", "copiar"])
    assert e.value.code == 2


@pytest.mark.parametrize("extra", [["--destino-copia", RAIZ], ["--solo-plan"]])
def test_flags_de_copia_en_otra_fase_exit2(extra):
    with pytest.raises(SystemExit) as e:
        organize_cli.main(["--origen", "gs://datos/x", "--destino", SAL, "--fase", "indice", *extra])
    assert e.value.code == 2


def test_copiar_con_origen_exit2(monkeypatch):
    monkeypatch.setattr(cp, "_backend_gcs", lambda: Mem(_origen()))
    with pytest.raises(SystemExit) as e:
        organize_cli.main(["--origen", "gs://datos/x", "--destino", SAL, "--fase", "copiar",
                           "--destino-copia", RAIZ, "--ejecucion", "ej1"])
    assert e.value.code == 2


def test_origen_sigue_obligatorio_fuera_de_copiar():
    with pytest.raises(SystemExit) as e:
        organize_cli.main(["--destino", SAL, "--fase", "indice"])
    assert e.value.code == 2


# ---- fixes de review ------------------------------------------------------

def test_plan_vacio_es_error():
    be = Mem({f"{SAL}/.organizado/a.json": (b"{}", "m")})
    with pytest.raises(ValueError, match="nada que copiar"):
        cp.planificar_copia(SAL, RAIZ, backend=be)


def test_excluidos_contados_y_con_muestra(monkeypatch):
    be = Mem(_origen(**{f"{SAL}/carpeta/": (b"", None)}))
    inf = cp.ejecutar_fase(SAL, RAIZ, solo_plan=True, ejecucion="e", backend=be)
    assert inf.excluidos == 3
    assert sorted(inf.muestra_excluidos) == sorted([
        f"{SAL}/.organizado/copias/a.json", f"{SAL}/z/.organizado/q", f"{SAL}/carpeta/"])
    assert json.loads(be.escritos[f"{SAL}/.organizado/copias/e.json"])["excluidos"] == 3


def test_muestra_excluidos_limitada():
    objs = {f"{SAL}/.organizado/{i}": (b"x", "m") for i in range(30)}
    objs[f"{SAL}/a.tif"] = (b"x", "m")
    inf = cp.ejecutar_fase(SAL, RAIZ, solo_plan=True, ejecucion="e", backend=Mem(objs))
    assert inf.excluidos == 30 and len(inf.muestra_excluidos) == 20


def test_solo_plan_informe_en_salida_no_en_raiz_de_plantas():
    be = Mem(_origen())
    cp.ejecutar_fase(SAL, RAIZ, solo_plan=True, ejecucion="e", backend=be)
    assert list(be.escritos) == [f"{SAL}/.organizado/copias/e.json"]
    assert not any(u.startswith(RAIZ) for u in be.escritos)
    assert not any(u.startswith(RAIZ) for u in be.o)


@pytest.mark.parametrize("nombre", ["SALIDA--A--..--2026--T", "SALIDA--A--P--..--T",
                                    "SALIDA--A--P--2026--a/b", "SALIDA--A--P--20..26--T"])
def test_componentes_peligrosos_error(nombre):
    with pytest.raises(ValueError):
        cp.planificar_copia(f"gs://datos/{nombre}", RAIZ, backend=Mem({f"gs://datos/{nombre}/x": (b"1", "m")}))


@pytest.mark.parametrize("raiz", [SAL, f"{SAL}/sub"])
def test_destino_copia_dentro_de_la_salida_error(raiz):
    with pytest.raises(ValueError, match="dentro de la propia SALIDA"):
        cp.planificar_copia(SAL, raiz, backend=Mem(_origen()))


def test_cli_validaciones_exit2(monkeypatch):
    monkeypatch.setattr(cp, "_backend_gcs", lambda: Mem(_origen()))
    for args in (["--destino", SAL, "--destino-copia", SAL],
                 ["--destino", "gs://datos/SALIDA--A--..--2026--T", "--destino-copia", RAIZ]):
        assert organize_cli.main([*args, "--fase", "copiar", "--ejecucion", "e"]) == 2


# ---- _BackendGCS con cliente falso ----------------------------------------

class _Err(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    @property
    def size(self):
        return len(self.bucket.datos[self.name][0])

    @property
    def md5_hash(self):
        return self.bucket.datos[self.name][1]

    def rewrite(self, source, token=None, if_generation_match=None):
        b = self.bucket
        b.llamadas.append((source.name, self.name, token, if_generation_match))
        if if_generation_match == 0 and self.name in b.datos:
            raise _Err(412)
        vuelta = len([x for x in b.llamadas if x[1] == self.name])
        if vuelta < b.vueltas:
            return f"tok{vuelta}", vuelta, 99
        b.datos[self.name] = source.bucket.datos[source.name]
        return None, 99, 99


class _Bucket:
    def __init__(self, client, name, vueltas=3):
        self.client, self.name, self.vueltas = client, name, vueltas
        self.datos, self.llamadas, self.subidos = {}, [], {}

    def blob(self, k):
        return _Blob(self, k)

    def get_blob(self, k):
        return _Blob(self, k) if k in self.datos else None

    def list_blobs(self, prefix=""):
        return [_Blob(self, k) for k in sorted(self.datos) if k.startswith(prefix)]


class _Cliente:
    def __init__(self):
        self.buckets = {}

    def bucket(self, n):
        return self.buckets.setdefault(n, _Bucket(self, n))

    def list_blobs(self, bucket, prefix=""):
        return bucket.list_blobs(prefix=prefix)


@pytest.fixture
def gcs(monkeypatch):
    from atom_core import almacen as alm
    from atom_core.almacen_gcs import AlmacenGCS
    cli = _Cliente()
    monkeypatch.setattr(alm, "_ALMACENES", {f"gs://{b}": AlmacenGCS(b, cliente=cli)
                                           for b in ("datos", "plantas_pv_nl")})
    return cli, cp._BackendGCS()


def test_backend_gcs_rewrite_varias_vueltas_con_token(gcs):
    cli, be = gcs
    cli.bucket("datos").datos["S/x.tif"] = (b"abcd", "m1")
    be.copiar("gs://datos/S/x.tif", "gs://plantas_pv_nl/P/x.tif")
    dest = cli.bucket("plantas_pv_nl")
    assert dest.datos["P/x.tif"] == (b"abcd", "m1")
    assert [(t, g) for _, _, t, g in dest.llamadas] == [(None, 0), ("tok1", 0), ("tok2", 0)]


def test_backend_gcs_412_es_file_exists(gcs):
    cli, be = gcs
    cli.bucket("datos").datos["S/x.tif"] = (b"abcd", "m1")
    cli.bucket("plantas_pv_nl").datos["P/x.tif"] = (b"OTRO", "mX")
    with pytest.raises(FileExistsError):
        be.copiar("gs://datos/S/x.tif", "gs://plantas_pv_nl/P/x.tif")
    assert cli.bucket("plantas_pv_nl").datos["P/x.tif"] == (b"OTRO", "mX")


def test_backend_gcs_otro_error_se_propaga(gcs):
    cli, be = gcs
    cli.bucket("datos").datos["S/x.tif"] = (b"abcd", "m1")
    cli.bucket("plantas_pv_nl").rewrite_err = True

    def malo(self, source, token=None, if_generation_match=None):
        raise _Err(500)
    _Blob_rewrite, _Blob.rewrite = _Blob.rewrite, malo
    try:
        with pytest.raises(_Err):
            be.copiar("gs://datos/S/x.tif", "gs://plantas_pv_nl/P/x.tif")
    finally:
        _Blob.rewrite = _Blob_rewrite


def test_backend_gcs_meta_y_listar(gcs):
    cli, be = gcs
    cli.bucket("datos").datos.update({"S/a": (b"12", "ma"), "S/b/c": (b"123", "mc"), "S2/z": (b"1", "mz")})
    assert be.meta("gs://datos/S/a") == (2, "ma")
    assert be.meta("gs://datos/S/nada") is None
    assert be.listar("gs://datos/S") == [("gs://datos/S/a", 2, "ma"), ("gs://datos/S/b/c", 3, "mc")]
