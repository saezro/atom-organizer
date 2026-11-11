"""`--hash-listado-esperado` del CLI: las fases apply/rgb/termica abortan (exit 2,
sin escribir nada en destino) si el origen lista distinto que el índice."""
import pytest

import organize_cli
from atom_core import organize
from atom_core.indice_mapreduce import hash_listado


@pytest.fixture
def _lote(tmp_path, monkeypatch):
    origen = tmp_path / "origen"
    origen.mkdir()
    imagenes = []
    for i in range(3):
        f = origen / f"DJI_2026011510000{i}_000{i}_W.JPG"
        f.write_bytes(b"x")
        imagenes.append(str(f))
    est = origen / "estadillo.csv"
    est.write_text("c")
    llamadas = []

    def _fake_run_task(task, params, emit, avanzado=None):
        llamadas.append(params)
        return {"status": "ok"}

    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)
    return origen, est, tmp_path / "destino", sorted(imagenes), llamadas


def _args(origen, est, destino, fase, *extra):
    return ["--origen", str(origen), "--destino", str(destino), "--estadillo", str(est),
            "--fase", fase, "--ejecucion", "e1", "--quiet", "--json", *extra]


@pytest.mark.parametrize("fase", ["apply", "rgb", "termica"])
def test_hash_correcto_la_fase_pasa(_lote, fase):
    origen, est, destino, imagenes, llamadas = _lote
    codigo = organize_cli.main(_args(origen, est, destino, fase,
                                     "--hash-listado-esperado", hash_listado(imagenes)))
    assert codigo != 2
    assert len(llamadas) == 1


@pytest.mark.parametrize("fase", ["apply", "rgb", "termica"])
def test_hash_distinto_exit_2_y_destino_vacio(_lote, fase, capsys):
    origen, est, destino, imagenes, llamadas = _lote
    esperado = hash_listado(imagenes[:-1])
    with pytest.raises(SystemExit) as exc:
        organize_cli.main(_args(origen, est, destino, fase, "--hash-listado-esperado", esperado))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert f"listado-cambiado: esperado={esperado} actual={hash_listado(imagenes)}" in err
    assert not destino.exists()
    assert llamadas == []


def test_sin_arg_igual_que_antes(_lote):
    origen, est, destino, _imagenes, llamadas = _lote
    codigo = organize_cli.main(_args(origen, est, destino, "apply"))
    assert codigo != 2
    assert len(llamadas) == 1
    assert destino.is_dir()


def test_fases_que_no_son_de_apply_ignoran_el_hash(_lote):
    origen, est, destino, _imagenes, llamadas = _lote
    codigo = organize_cli.main(_args(origen, est, destino, "indice",
                                     "--hash-listado-esperado", "0" * 64))
    assert codigo != 2
    assert len(llamadas) == 1
