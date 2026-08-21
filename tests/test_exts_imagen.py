import utils
from rjpeg_a_tiff import EXTS_IMAGEN


def test_get_images_from_dir_acepta_todas_las_extensiones(tmp_path, organizer_logger_stub):
    nombres = [
        "a.jpg", "b.JPG", "c.jpeg", "d.jpe", "e.jfif",
        "f.png", "g.tif", "h.tiff", "i.dng", "j.DNG",
    ]
    for n in nombres:
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "notas.txt").write_bytes(b"x")

    encontrados = utils.Utils(organizer_logger_stub).get_images_from_dir(str(tmp_path))

    assert len(encontrados) == len(nombres), f"faltan extensiones: {encontrados}"
    assert not any(f.endswith(".txt") for f in encontrados)


def test_exts_imagen_es_la_fuente_unica():
    assert ".dng" in EXTS_IMAGEN and ".jfif" in EXTS_IMAGEN


def test_get_images_from_dir_solo_fuente_excluye_salidas_del_pipeline(tmp_path, organizer_logger_stub):
    nombres = ["a.JPG", "b.png", "c.tiff", "d.dng"]
    for n in nombres:
        (tmp_path / n).write_bytes(b"x")

    utils_obj = utils.Utils(organizer_logger_stub)

    solo_fuente = utils_obj.get_images_from_dir(str(tmp_path), solo_fuente=True)
    assert sorted(solo_fuente) == sorted(["a.JPG", "b.png"])

    todas = utils_obj.get_images_from_dir(str(tmp_path))
    assert sorted(todas) == sorted(nombres)
