"""El modo de destino tiene que llegar entero desde el flag del CLI hasta el
`safe_move` que mueve cada imagen. Es una cadena de 5 saltos (CLI -> params ->
dataclass -> host/fase -> objeto de pipeline) y cualquiera de ellos que se
olvide deja el flag silenciosamente sin efecto: el usuario pide `obviar`, la
UI lo muestra, y el pipeline sigue duplicando. Por eso se testea la cadena, no
solo los extremos."""
import pytest

import utils
from atom_core import organize


def test_split_config_por_defecto_sobrescribe():
    cfg = organize._default_split_config({"origen": "/in", "destino": "/out", "estadillo": "/e.csv"})
    assert cfg.modo_destino == utils.MODO_SOBRESCRIBIR


def test_split_config_respeta_modo_obviar():
    cfg = organize._default_split_config(
        {"origen": "/in", "destino": "/out", "estadillo": "/e.csv", "modo_destino": utils.MODO_OBVIAR})
    assert cfg.modo_destino == utils.MODO_OBVIAR


def test_split_config_rechaza_modo_invalido():
    with pytest.raises(ValueError):
        organize._default_split_config(
            {"origen": "/in", "destino": "/out", "modo_destino": "borrar_todo"})


# NOTA de adaptacion respecto al brief: `organize_cli.main` valida que
# `--origen` (y el estadillo, si se pasa) existan en disco ANTES de construir
# `params` y llamar a `run_task` (organize_cli.py ~221-226); con las rutas
# ficticias `/in`/`/e.csv` del brief corta ahi con `return 2` y `run_task` ni
# se invoca, así que `capturado` queda vacío. Se usan `tmp_path` reales para
# que el CLI llegue a construir `params`; el aserto sigue siendo exactamente
# el que pedia el brief: `params["modo_destino"]`.
def test_cli_mete_el_modo_en_params(monkeypatch, tmp_path):
    import organize_cli
    capturado = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        capturado.update(params)
        return {"status": "ok"}

    # `organize_cli.main` importa run_task de forma perezosa DESDE atom_core.organize
    # (organize_cli.py:232), asi que el doble hay que ponerlo en el modulo de origen.
    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    origen = tmp_path / "in"
    origen.mkdir()
    destino = tmp_path / "out"
    estadillo = tmp_path / "e.csv"
    estadillo.write_text("")

    organize_cli.main([
        "--origen", str(origen), "--destino", str(destino), "--estadillo", str(estadillo),
        "--modo-destino", "obviar", "--quiet", "--json",
    ])

    assert capturado.get("modo_destino") == utils.MODO_OBVIAR


def test_cli_por_defecto_sobrescribe(monkeypatch, tmp_path):
    import organize_cli
    capturado = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        capturado.update(params)
        return {"status": "ok"}

    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    origen = tmp_path / "in"
    origen.mkdir()
    destino = tmp_path / "out"

    organize_cli.main(["--origen", str(origen), "--destino", str(destino), "--quiet", "--json"])

    assert capturado.get("modo_destino") == utils.MODO_SOBRESCRIBIR


def test_gen_struct_folder_nace_sobrescribiendo():
    import pipeline

    class _LogFalso:
        class logger:
            @staticmethod
            def info(*a, **k):
                pass

    obj = pipeline.GenStructFolder(_LogFalso())
    assert obj.modo_destino == utils.MODO_SOBRESCRIBIR
    assert obj.skipped_image_number == 0
