# tests/test_fallback_only_pb.py
import logging
import os
import types
import pipeline

_PROG = types.SimpleNamespace(emit=lambda *a, **k: None)


def test_fallback_sin_carpeta_respeta_only_pb(tmp_path, monkeypatch):
    """Si TERMICA/RGB no existe pero se pasa only_pb, NO se rota la raiz entera."""
    raiz = tmp_path / "entrada"
    (raiz / "PB16_V1").mkdir(parents=True)
    (raiz / "PB17_V1").mkdir(parents=True)

    llamadas = []

    obj = pipeline.GenStructFolder.__new__(pipeline.GenStructFolder)
    obj.organizer_logger = types.SimpleNamespace(logger=logging.getLogger("test"))
    monkeypatch.setattr(
        obj, "gen_thumbnails_and_rotate",
        lambda carpeta, *a, **k: llamadas.append(carpeta), raising=False,
    )
    monkeypatch.setattr(
        obj, "utils_obj",
        type("U", (), {"prepare_output_folder": lambda self, *a, **k: None})(),
        raising=False,
    )

    obj.check_input_folder_and_iterate(
        str(raiz), ["RGB"], 5, 280, 260, 100, 80,
        True, False, _PROG, _PROG, only_pb=["PB16_V1"],
    )

    assert llamadas == [os.path.join(str(raiz), "PB16_V1")], (
        f"debia rotar solo el shard PB16_V1, rotó: {llamadas}"
    )
