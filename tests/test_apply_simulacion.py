"""`--simular`: el apply lee y calcula igual, pero NINGUNA operacion de escritura,
copia, move o delete llega al almacenamiento; solo se escribe el informe JSON de
`.organizado/simulaciones/<ejecucion>-task<idx>.json`."""
import json
import os

import pytest

from atom_core import almacen as almacen_mod
from atom_core import phases, simulacion
from tests.test_giro_manifiesto_gcs import _BlobGen, _BucketGen, bucket  # noqa: F401 (fixture)
from tests.test_almacen_gcs import BucketFalso
from tests.test_organizado_plan_apply_e2e import (  # noqa: F401 (fixtures)
    _cfg, _inspeccion,
)
from tests.test_organizado_shards_fases import RAIZ_GCS, PREFIJO_GCS, _correr_fase


@pytest.fixture(autouse=True)
def _apagar_simulacion():
    simulacion.desactivar()
    yield
    simulacion.desactivar()


def test_guard_cuenta_y_no_ejecuta(tmp_path):
    simulacion.activar()
    f = tmp_path / "a.bin"
    f.write_bytes(b"x" * 10)
    assert simulacion.interceptar("publicar", "gs://b/x", 10) is True
    with simulacion.permitir_informe():
        assert simulacion.interceptar("publicar", "gs://b/informe") is False
    inf = simulacion.informe("e", 0, 1)
    assert inf["bytes_que_se_habrian_escrito"] == 10
    assert inf["operaciones_interceptadas"]["publicar"] == {"n": 1, "bytes": 10}


def test_apply_simulado_gcs_no_escribe_nada_salvo_el_informe(
        _inspeccion, logger, monkeypatch, bucket, tmp_path):  # noqa: F811
    (tmp_path / "tmp").mkdir(exist_ok=True)
    monkeypatch.setattr(phases.paralelismo_mod, "ControladorAdaptativo", lambda **kw: None)
    cfg = _cfg(_inspeccion, output_folder=RAIZ_GCS)
    _correr_fase(logger, monkeypatch, cfg, "indice")  # real: deja el manifiesto en el bucket

    antes = dict(bucket.objetos)
    ops = []
    for nombre in ("upload_from_filename", "rewrite", "delete"):
        original = getattr(_BlobGen, nombre)

        def espia(self, *a, _n=nombre, _o=original, **k):
            ops.append((_n, self.name))
            return _o(self, *a, **k)
        monkeypatch.setattr(_BlobGen, nombre, espia)
    copy_original = BucketFalso.copy_blob
    monkeypatch.setattr(BucketFalso, "copy_blob",
                        lambda self, *a, **k: (ops.append(("copy_blob", a[2])), copy_original(self, *a, **k))[1])

    simulacion.activar()
    _correr_fase(logger, monkeypatch, cfg, "apply", 0, 1, "exec-sim")

    clave_informe = PREFIJO_GCS + ".organizado/simulaciones/exec-sim-task0.json"
    # La unica operacion de escritura que llego al bucket es la del informe.
    assert ops == [("upload_from_filename", clave_informe)], ops
    assert set(bucket.objetos) - set(antes) == {clave_informe}
    assert {k: v for k, v in bucket.objetos.items() if k != clave_informe} == antes
    inf = json.loads(bucket.objetos[clave_informe])
    assert inf["task_idx"] == 0 and inf["tasks"] == 1 and inf["ejecucion"] == "exec-sim"
    assert inf["ficheros"]["rgb"] >= 1 and inf["ficheros"]["termica"] >= 1
    assert inf["bytes_leidos"] > 0 and inf["bytes_que_se_habrian_escrito"] > 0
    assert set(inf["segundos_por_fase"]) == {"listado", "lectura", "computo", "escritura_simulada"}
    assert inf["operaciones_interceptadas"]["publicar"]["n"] > 0
    assert inf["deltas_no_escritos"]["hechas"] > 0
    assert not any("/shards/" in k for k in bucket.objetos)


def test_apply_simulado_local_no_escribe_nada_salvo_el_informe(_inspeccion, logger, monkeypatch):  # noqa: F811
    cfg = _cfg(_inspeccion)
    _correr_fase(logger, monkeypatch, cfg, "indice")

    def arbol():
        return {os.path.relpath(os.path.join(r, n), cfg.output_folder):
                os.path.getsize(os.path.join(r, n))
                for r, _d, ns in os.walk(cfg.output_folder) for n in ns}

    antes = arbol()
    simulacion.activar()
    _correr_fase(logger, monkeypatch, cfg, "apply", 0, 1, "exec-sim")
    despues = arbol()
    informe = os.path.join(".organizado", "simulaciones", "exec-sim-task0.json")
    assert set(despues) - set(antes) == {informe}
    assert {k: v for k, v in despues.items() if k != informe} == antes
    assert json.load(open(os.path.join(cfg.output_folder, informe)))["bytes_leidos"] > 0


# ---------------------------------------------------------------- CLI

def _cli(monkeypatch, tmp_path, *extra):
    import organize_cli
    monkeypatch.delenv("CLOUD_RUN_EXECUTION", raising=False)
    return organize_cli.main(["--origen", str(tmp_path), "--destino", str(tmp_path / "d"),
                              "--fase", "apply", *extra])


def test_simular_sin_ejecucion_es_error_y_no_activa_nada(monkeypatch, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        _cli(monkeypatch, tmp_path, "--simular")
    assert e.value.code == 2
    assert "--ejecucion" in capsys.readouterr().err
    assert not (tmp_path / "d").exists()


def test_simular_rechaza_estadillo_y_otras_fases(monkeypatch, tmp_path, capsys):
    est = tmp_path / "e.csv"
    est.write_text("x")
    with pytest.raises(SystemExit):
        _cli(monkeypatch, tmp_path, "--simular", "--ejecucion", "x", "--estadillo", str(est))
    assert "--estadillo" in capsys.readouterr().err
    import organize_cli
    with pytest.raises(SystemExit):
        organize_cli.main(["--origen", str(tmp_path), "--destino", str(tmp_path / "d"),
                           "--fase", "cierre", "--simular", "--ejecucion", "x"])


def test_simular_no_inicia_run_reporter_y_activa_antes(monkeypatch, tmp_path):
    import organize_cli
    from atom_core import run_reporter
    llamadas = []
    for nombre in ("iniciar", "fase", "progreso", "fin", "estadillo", "log", "giros"):
        monkeypatch.setattr(run_reporter.RunReporter, nombre,
                            lambda self, *a, _n=nombre, **k: llamadas.append(_n))
    monkeypatch.setenv("ORGANIZER_INGEST_SECRET", "s")
    activa_en_run = []
    import atom_core.organize as org
    monkeypatch.setattr(org, "run_task",
                        lambda *a, **k: activa_en_run.append(simulacion.activa()))
    (tmp_path / "o").mkdir()
    rc = organize_cli.main(["--origen", str(tmp_path / "o"), "--destino", str(tmp_path / "d"),
                            "--fase", "apply", "--simular", "--ejecucion", "x"])
    assert rc == 0 and activa_en_run == [True]
    assert llamadas == []
    assert not (tmp_path / "d").exists()


def test_informe_no_pisa_el_de_otra_ejecucion(tmp_path):
    simulacion.activar()
    destino = str(tmp_path)
    simulacion.escribir_informe(destino, "e1", 0, 1)
    with pytest.raises(FileExistsError):
        simulacion.escribir_informe(destino, "e1", 0, 1)
