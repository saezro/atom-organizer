"""M1 reparto LPT por coste, M2 `n_por_tipo`, M5 `--shards` y `--listado`."""
import gzip
import json
import random

import pytest

import organize_cli
from atom_core import apply_shards, organize
from atom_core import indice_mapreduce as mr


def _filas(n_rgb, n_term, semilla=1):
    filas = [{"id": i, "tipo": "RGB", "ruta_origen": f"gs://b/o/r{i:05d}.JPG"} for i in range(n_rgb)]
    filas += [{"id": n_rgb + i, "tipo": "TERMICA", "ruta_origen": f"gs://b/o/t{i:05d}_T.JPG"}
              for i in range(n_term)]
    random.Random(semilla).shuffle(filas)
    return filas


def _carga(reparto):
    return [sum(apply_shards.coste_fila(f) for f in lista) for lista in reparto]


def test_reparto_particiona_y_es_determinista():
    filas = _filas(300, 250)
    a = apply_shards.reparto_por_coste(filas, 7)
    b = apply_shards.reparto_por_coste(list(reversed(filas)), 7)
    assert [[f["id"] for f in t] for t in a] == [[f["id"] for f in t] for t in b]
    ids = sorted(f["id"] for t in a for f in t)
    assert ids == sorted(f["id"] for f in filas)


@pytest.mark.parametrize("k", [2, 5, 8, 16])
def test_desequilibrio_menor_5_por_ciento(k):
    carga = _carga(apply_shards.reparto_por_coste(_filas(120, 100), k))
    assert (max(carga) - min(carga)) / max(carga) <= 0.05


def test_orden_intercala_tipos():
    tarea = apply_shards.reparto_por_coste(_filas(100, 100), 2)[0]
    tipos = [f["tipo"] for f in tarea]
    assert tipos[:20].count("TERMICA") > 0 and tipos[:20].count("RGB") > 0
    assert max(len(s) for s in "".join("T" if t == "TERMICA" else "R" for t in tipos)
               .replace("RT", "R T").replace("TR", "T R").split()) <= 3


def test_tipo_desconocido_es_error():
    with pytest.raises(ValueError):
        apply_shards.reparto_por_coste([{"id": 1, "tipo": "X", "ruta_origen": "a"}], 2)


class _ManifiestoFalso:
    def __init__(self, filas):
        self.filas = filas
        self.estado = {}

    def todas(self):
        return self.filas

    def pendientes(self, origen=None):
        return [f for f in self.filas if self.estado.get(f["id"], "pendiente") == "pendiente"]

    def _actualizar(self, id_fila, **kw):
        self.estado[id_fila] = kw.get("estado")


def test_manifiesto_shard_apply_usa_lpt_y_todas_las_tasks_coinciden(tmp_path):
    filas = _filas(60, 40)
    vistos = []
    for i in range(4):
        reg = apply_shards.RegistroDeltas(str(tmp_path), "e", i, "apply")
        vista = apply_shards.ManifiestoShard(_ManifiestoFalso(filas), reg, i, 4)
        vistos.append([f["id"] for f in vista.pendientes()])
    esperado = apply_shards.reparto_por_coste(filas, 4)
    assert vistos == [[f["id"] for f in t] for t in esperado]
    # estable entre reintentos: lo hecho no cambia el reparto de lo demas
    m = _ManifiestoFalso(filas)
    m.estado[vistos[0][0]] = "hecho"
    reg = apply_shards.RegistroDeltas(str(tmp_path), "e", 1, "apply")
    assert [f["id"] for f in apply_shards.ManifiestoShard(m, reg, 1, 4).pendientes()] == vistos[1]


def test_fases_antiguas_conservan_hash(tmp_path):
    from atom_core import sharding
    filas = _filas(30, 30)
    reg = apply_shards.RegistroDeltas(str(tmp_path), "e", 2, "rgb")
    vista = apply_shards.ManifiestoShard(_ManifiestoFalso(filas), reg, 2, 5)
    assert {f["id"] for f in vista.pendientes()} == {
        f["id"] for f in filas if sharding.toca_imagen(f["ruta_origen"], 2, 5)}


# ---- M2 ----------------------------------------------------------------------

def test_resumen_n_por_tipo():
    imgs = ["/o/a", "/o/b", "/o/c"]
    r = mr.construir_resumen_indice(imgs, [(x, "1", "1") for x in imgs], [],
                                    tipos=["RGB", "TERMICA", "RGB_Extra"])
    assert r["n_por_tipo"] == {"rgb": 2, "termica": 1}
    assert "n_por_tipo" not in mr.construir_resumen_indice(imgs, [], [])


def test_resumen_json_lleva_n_por_tipo(tmp_path):
    imgs = ["/o/a"]
    ruta = mr.escribir_resumen_indice(
        str(tmp_path), "e1", mr.construir_resumen_indice(imgs, [], [], tipos=["TERMICA"]))
    assert json.load(open(ruta))["n_por_tipo"] == {"rgb": 0, "termica": 1}


# ---- M5 listado ---------------------------------------------------------------

def _escribir_listado(ruta, entradas):
    contenido = mr.serializar_listado(entradas)
    with gzip.open(ruta, "wb") as fh:
        fh.write(contenido)
    return mr.hash_listado_externo(entradas)


ENTRADAS = [{"ruta": "/o/b.JPG", "tamano": 5}, {"ruta": "/o/a_T.JPG", "tamano": 7},
            {"ruta": "/o/é.JPG", "tamano": 0}]


def test_formato_canonico_y_hash_fijo():
    esperado = ('{"ruta":"/o/a_T.JPG","tamano":7}\n{"ruta":"/o/b.JPG","tamano":5}\n'
                '{"ruta":"/o/é.JPG","tamano":0}\n').encode("utf-8")
    assert mr.serializar_listado(ENTRADAS) == esperado
    import hashlib
    assert mr.hash_listado_externo(ENTRADAS) == hashlib.sha256(esperado).hexdigest()


def test_leer_listado_ok(tmp_path):
    f = tmp_path / "l.jsonl.gz"
    h = _escribir_listado(f, ENTRADAS)
    rutas, tam = mr.leer_listado(str(f), h)
    assert rutas == ["/o/a_T.JPG", "/o/b.JPG", "/o/é.JPG"] and tam["/o/b.JPG"] == 5


def test_leer_listado_hash_distinto_o_ilegible_es_error(tmp_path):
    f = tmp_path / "l.jsonl.gz"
    _escribir_listado(f, ENTRADAS)
    with pytest.raises(mr.ErrorListado):
        mr.leer_listado(str(f), "0" * 64)
    with pytest.raises(mr.ErrorListado):
        mr.leer_listado(str(tmp_path / "no-existe.gz"), "0" * 64)
    with pytest.raises(mr.ErrorListado):
        mr.leer_listado(str(f), "")


def test_leer_listado_no_canonico_es_error(tmp_path):
    f = tmp_path / "l.jsonl.gz"
    contenido = b'{"ruta":"/o/b","tamano":1}\n{"ruta":"/o/a","tamano":1}\n'
    with gzip.open(f, "wb") as fh:
        fh.write(contenido)
    import hashlib
    with pytest.raises(mr.ErrorListado):
        mr.leer_listado(str(f), hashlib.sha256(contenido).hexdigest())


def test_indice_usa_listado_y_no_lista_el_origen(tmp_path, monkeypatch):
    from atom_core import indice
    f = tmp_path / "l.jsonl.gz"
    h = _escribir_listado(f, ENTRADAS)

    def _no(*a, **k):
        raise AssertionError("no debe listar el origen")
    monkeypatch.setattr(indice, "_listar_imagenes", _no)
    tam = {}
    imgs = indice._imagenes_del_origen(None, None, [], tam, (str(f), h))
    assert imgs == ["/o/a_T.JPG", "/o/b.JPG", "/o/é.JPG"] and tam["/o/a_T.JPG"] == 7
    with pytest.raises(mr.ErrorListado):
        indice._imagenes_del_origen(None, None, [], {}, (str(f), "1" * 64))


# ---- M5 CLI -------------------------------------------------------------------

@pytest.fixture
def _cli(tmp_path, monkeypatch):
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "estadillo.csv").write_text("c")
    llamadas = []

    def _fake(task, params, emit, avanzado=None):
        llamadas.append(params)
        return {"status": "ok"}
    monkeypatch.setattr(organize, "run_task", _fake, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake, raising=False)
    for v in ("CLOUD_RUN_EXECUTION", "CLOUD_RUN_TASK_INDEX", "CLOUD_RUN_TASK_COUNT"):
        monkeypatch.delenv(v, raising=False)

    def args(fase, *extra):
        return ["--origen", str(origen), "--destino", str(tmp_path / "d"),
                "--estadillo", str(origen / "estadillo.csv"), "--fase", fase, "--quiet",
                "--json", *extra]
    return args, llamadas


def test_cli_shards_mapea_task_a_shard_original(_cli, monkeypatch):
    args, llamadas = _cli
    monkeypatch.setenv("ORGANIZER_TELEMETRIA_OPCIONAL", "1")
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "nueva")
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "1")
    assert organize_cli.main(args("apply", "--shards", "3,7", "--ejecucion", "orig",
                                  "--shard-count", "9")) == 0
    p = llamadas[-1]
    assert (p["shard_index"], p["shard_count"], p["ejecucion"]) == (7, 9, "orig")
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    organize_cli.main(args("apply", "--shards", "3,7", "--ejecucion", "orig", "--shard-count", "9"))
    assert llamadas[-1]["shard_index"] == 3


@pytest.mark.parametrize("extra", [
    ["--shards", "3,7"],                                   # sin ejecucion
    ["--shards", "3,7", "--ejecucion", "o"],               # sin K original
    ["--shards", "3,12", "--ejecucion", "o", "--shard-count", "9"],
    ["--shards", "3,3", "--ejecucion", "o", "--shard-count", "9"],
])
def test_cli_shards_invalido_sale(_cli, extra):
    args, llamadas = _cli
    with pytest.raises(SystemExit) as exc:
        organize_cli.main(args("apply", *extra))
    assert exc.value.code != 0 and llamadas == []


def test_cli_shards_task_sin_shard(_cli, monkeypatch):
    args, llamadas = _cli
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "2")
    with pytest.raises(SystemExit) as exc:
        organize_cli.main(args("apply", "--shards", "3,7", "--ejecucion", "o", "--shard-count", "9"))
    assert exc.value.code != 0 and llamadas == []


def test_cli_listado_pasa_uri_y_hash(_cli):
    args, llamadas = _cli
    assert organize_cli.main(args("indice", "--ejecucion", "e", "--listado", "gs://b/l.jsonl.gz",
                                  "--hash-listado-esperado", "AB" * 32)) == 0
    assert llamadas[-1]["listado"] == ["gs://b/l.jsonl.gz", "ab" * 32]


@pytest.mark.parametrize("extra,fase", [
    (["--listado", "gs://b/l"], "indice"),                 # sin hash
    (["--listado", "gs://b/l", "--hash-listado-esperado", "a"], "apply"),
])
def test_cli_listado_invalido_sale(_cli, extra, fase):
    args, llamadas = _cli
    with pytest.raises(SystemExit) as exc:
        organize_cli.main(args(fase, "--ejecucion", "e", *extra))
    assert exc.value.code != 0 and llamadas == []
