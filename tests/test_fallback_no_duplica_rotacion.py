# tests/test_fallback_no_duplica_rotacion.py
"""El fallback de "ni RGB ni TERMICA existen" debe correr UNA sola vez.

`folders_to_check` puede llegar como ["TERMICA", "RGB"] (phases.py:805 y :859).
Cuando el `else` del fallback vivía DENTRO del bucle, se ejecutaba una vez por
entrada: las mismas imágenes se giraban dos veces (90 + 90 = 180).
"""
import os
import pipeline


def _obj_con_espia(monkeypatch, llamadas):
    obj = pipeline.GenStructFolder.__new__(pipeline.GenStructFolder)
    monkeypatch.setattr(
        obj, "gen_thumbnails_and_rotate",
        lambda carpeta, *a, **k: llamadas.append(carpeta), raising=False,
    )
    monkeypatch.setattr(
        obj, "gen_thumbnails_and_rotate_manual",
        lambda carpeta, *a, **k: llamadas.append(carpeta), raising=False,
    )
    monkeypatch.setattr(
        obj, "utils_obj",
        type("U", (), {"prepare_output_folder": lambda self, *a, **k: None})(),
        raising=False,
    )
    return obj


def test_fallback_con_dos_carpetas_rota_una_sola_vez(tmp_path, monkeypatch):
    raiz = tmp_path / "entrada"
    (raiz / "PB16_V1").mkdir(parents=True)

    llamadas = []
    obj = _obj_con_espia(monkeypatch, llamadas)
    obj.check_input_folder_and_iterate(
        str(raiz), ["TERMICA", "RGB"], 5, 280, 260, 100, 80,
        True, False, None, None,
    )

    assert llamadas == [str(raiz)], (
        f"el fallback debia rotar la raiz UNA vez, rotó {len(llamadas)}: {llamadas}"
    )


def test_fallback_con_dos_carpetas_y_only_pb_rota_una_sola_vez(tmp_path, monkeypatch):
    raiz = tmp_path / "entrada"
    (raiz / "PB16_V1").mkdir(parents=True)

    llamadas = []
    obj = _obj_con_espia(monkeypatch, llamadas)
    obj.check_input_folder_and_iterate(
        str(raiz), ["TERMICA", "RGB"], 5, 280, 260, 100, 80,
        True, False, None, None, only_pb=["PB16_V1"],
    )

    assert llamadas == [os.path.join(str(raiz), "PB16_V1")], (
        f"el fallback debia rotar el shard UNA vez, rotó {len(llamadas)}: {llamadas}"
    )


def test_carpeta_presente_no_dispara_fallback(tmp_path, monkeypatch):
    """Si TERMICA existe, se procesa ella y NO se rota tambien la raiz."""
    raiz = tmp_path / "entrada"
    (raiz / "TERMICA" / "PB16_V1").mkdir(parents=True)

    llamadas = []
    obj = _obj_con_espia(monkeypatch, llamadas)
    obj.check_input_folder_and_iterate(
        str(raiz), ["TERMICA", "RGB"], 5, 280, 260, 100, 80,
        True, False, None, None,
    )

    assert llamadas == [os.path.join(str(raiz), "TERMICA")], (
        f"debia rotar solo TERMICA, rotó: {llamadas}"
    )
