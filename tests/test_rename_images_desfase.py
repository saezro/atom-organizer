import os
import datetime
from pipeline import GenStructFolder


class DummySignal:
    def emit(self, *a, **k):
        pass


def test_rename_images_aplica_desfase(tmp_path, synthetic_jpeg, organizer_logger_stub):
    carpeta = tmp_path / "PB1_V01"
    carpeta.mkdir()
    img_path = carpeta / "DJI_0001.jpg"
    synthetic_jpeg(str(img_path), dt_val=datetime.datetime(2024, 3, 10, 10, 0, 0))

    obj = GenStructFolder(organizer_logger_stub)
    obj.current_image_number = 0
    obj.total_images_number = 1

    obj.rename_images(str(carpeta), DummySignal(), DummySignal(), desfase_horas=2, desfase_minutos=0)

    renamed = os.listdir(carpeta)
    assert any(name.startswith("20240310_120000_") for name in renamed), renamed
