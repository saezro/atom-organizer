import os
from utils import OrganizerLogger as ol
from pipeline import GenStructFolder


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_gsf(tmp_path):
    logger = ol("test_gen_struct", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    return GenStructFolder(logger)


def test_moverListaImagenes_no_sobreescribe_en_colision(tmp_path):
    origen1 = tmp_path / "origen1"
    origen2 = tmp_path / "origen2"
    destino = tmp_path / "destino"
    origen1.mkdir()
    origen2.mkdir()
    destino.mkdir()
    (origen1 / "img.jpg").write_bytes(b"CONTENIDO_A")
    (origen2 / "img.jpg").write_bytes(b"CONTENIDO_B")

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 2
    cb = FakeSignal()

    gsf.moverListaImagenes(str(origen1), str(destino), ["img.jpg"], cb, cb)
    gsf.moverListaImagenes(str(origen2), str(destino), ["img.jpg"], cb, cb)

    destinos = sorted(os.listdir(destino))
    assert len(destinos) == 2, f"Se esperaban 2 archivos en destino, hay {destinos} (colisión perdió uno)"
    contenidos = {open(destino / f, "rb").read() for f in destinos}
    assert contenidos == {b"CONTENIDO_A", b"CONTENIDO_B"}, "Se ha perdido/corrompido el contenido de una de las fotos"


def test_rename_images_no_sobreescribe_en_colision(tmp_path):
    carpeta = tmp_path / "vuelo"
    carpeta.mkdir()
    (carpeta / "DJI_0001.jpg").write_bytes(b"FOTO_1")
    (carpeta / "DJI_0002.jpg").write_bytes(b"FOTO_2")

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 2
    gsf.utils_obj.get_images_from_dir = lambda folder, *a, **kw: ["DJI_0001.jpg", "DJI_0002.jpg"]
    # Forzamos que ambas fotos calculen el MISMO nuevo nombre (mismo timestamp EXIF) para provocar la colisión.
    gsf.exif_management_obj.fechaHora_DJI = lambda path, progress_callback: "20240101_120000"
    cb = FakeSignal()

    gsf.rename_images(str(carpeta), cb, cb)

    resultado = sorted(os.listdir(carpeta))
    assert len(resultado) == 2, f"Se esperaban 2 archivos tras renombrar, hay {resultado} (colisión perdió uno)"


def test_gen_thumbnails_and_rotate_manual_no_reprocesa_miniaturas(tmp_path):
    root = tmp_path / "PLANTA"
    (root / "TERMICA").mkdir(parents=True)
    miniaturas_existentes = root / "MINIATURAS" / "PB1_V1_miniaturas"
    miniaturas_existentes.mkdir(parents=True)
    (miniaturas_existentes / "PB1_V1_0001.JPG").write_bytes(b"YA_GENERADA")

    gsf = _make_gsf(tmp_path)
    gsf.root_folder = str(root)
    gsf.miniaturas_root_folder = str(root / "MINIATURAS")
    gsf.total_images_number = 1

    llamadas = []
    gsf.compress_image_obj.rotate_and_save = lambda *args, **kwargs: llamadas.append(args[1]) or False

    cb = FakeSignal()
    gsf.gen_thumbnails_and_rotate_manual(str(root), rgb_processing=True, rotation_value_90=True, progress_callback=cb, progress_bar=cb)

    reprocesadas = [c for c in llamadas if "MINIATURAS" in c]
    assert reprocesadas == [], f"Se ha reprocesado contenido dentro de MINIATURAS: {reprocesadas}"
