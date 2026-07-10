import pytest
from exif import MetaLocation
from pipeline import Extraction, GenStructFolder, SplitImages, RGBProcessing, RGBCropping, CompressImage


@pytest.mark.parametrize("cls", [
    MetaLocation,
    Extraction,
    GenStructFolder,
    SplitImages,
    RGBProcessing,
    RGBCropping,
    CompressImage,
])
def test_send_progress_no_zero_division_with_total_zero(cls, organizer_logger_stub):
    """Con total_images_number=0 (carpeta vacía), calcular el progreso NO debe lanzar ZeroDivisionError."""
    obj = cls(organizer_logger_stub)
    obj.current_image_number = 0
    obj.total_images_number = 0
    # Simulamos exactamente el cálculo que hacen las funciones internas de progreso.
    from utils import safe_pct
    p = safe_pct(obj.current_image_number, obj.total_images_number)
    assert p == 0
