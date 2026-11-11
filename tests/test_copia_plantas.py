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
    assert inf.choques[0]["motivo"] == "destino-distinto"
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
