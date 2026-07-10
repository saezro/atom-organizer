import pytest
import piexif
from PIL import Image

from exif import GeneralInformationFromImage
from exif import MetaLocation


class _NoopCallback:
    def emit(self, *args, **kwargs):
        pass


@pytest.fixture
def synthetic_jpeg_no_datetime(tmp_path):
    """JPEG con EXIF válido (tiene otras tags) pero SIN el tag 36867 (DateTimeOriginal)."""
    path = str(tmp_path / "no_datetime.jpg")
    img = Image.new("RGB", (64, 48), color=(120, 130, 140))

    gps_ifd = {
        piexif.GPSIFD.GPSLatitudeRef: "N",
        piexif.GPSIFD.GPSLatitude: ((40, 1), (25, 1), (100, 100)),
        piexif.GPSIFD.GPSLongitudeRef: "W",
        piexif.GPSIFD.GPSLongitude: ((3, 1), (42, 1), (137, 100)),
    }
    # 0th/Exif sin DateTimeOriginal (36867), a propósito.
    zeroth_ifd = {piexif.ImageIFD.Make: "DJI"}
    exif_dict = {"0th": zeroth_ifd, "Exif": {}, "GPS": gps_ifd}
    exif_bytes = piexif.dump(exif_dict)

    img.save(path, format="JPEG", exif=exif_bytes)
    img.close()
    return path


@pytest.fixture
def synthetic_jpeg_gps_no_ref(tmp_path):
    """
    JPEG con IFD GPS (34853) presente, con GPSLatitude/GPSLongitude/GPSAltitude
    (tags 2, 4, 6) pero SIN GPSLatitudeRef/GPSLongitudeRef (tags 1, 3).
    """
    path = str(tmp_path / "gps_no_ref.jpg")
    img = Image.new("RGB", (64, 48), color=(120, 130, 140))

    date_str = "2024:06:01 10:30:00"
    gps_ifd = {
        piexif.GPSIFD.GPSLatitude: ((40, 1), (25, 1), (100, 100)),
        piexif.GPSIFD.GPSLongitude: ((3, 1), (42, 1), (137, 100)),
        piexif.GPSIFD.GPSAltitude: (50, 1),
    }
    exif_ifd = {piexif.ExifIFD.DateTimeOriginal: date_str}
    exif_dict = {"0th": {}, "Exif": exif_ifd, "GPS": gps_ifd}
    exif_bytes = piexif.dump(exif_dict)

    img.save(path, format="JPEG", exif=exif_bytes)
    img.close()
    return path


def test_fechaHora_dji_missing_datetime_tag_no_crash(organizer_logger_stub, synthetic_jpeg_no_datetime):
    """Imagen con EXIF pero SIN tag 36867 (DateTimeOriginal): no debe lanzar KeyError, debe devolver None y registrar el error."""
    exif_mgmt = GeneralInformationFromImage(organizer_logger_stub)
    resultado = exif_mgmt.fechaHora_DJI(str(synthetic_jpeg_no_datetime), _NoopCallback())
    assert resultado is None
    assert exif_mgmt.error_exif_data == 1


def test_get_timestamp_missing_datetime_tag_no_crash(organizer_logger_stub, synthetic_jpeg_no_datetime):
    exif_mgmt = GeneralInformationFromImage(organizer_logger_stub)
    resultado = exif_mgmt.get_timestamp_from_image(str(synthetic_jpeg_no_datetime))
    assert resultado is None


def test_leer_lat_lon_altitud_missing_gps_ref_no_crash(organizer_logger_stub, synthetic_jpeg_gps_no_ref):
    """Imagen con IFD GPS presente pero sin GPSLatitudeRef/GPSLongitudeRef (tags 1 y 3): no debe lanzar KeyError ni UnboundLocalError."""
    meta = MetaLocation(organizer_logger_stub)
    resultado = meta.leerLatitudLongitudAltitud_exif_DJI(str(synthetic_jpeg_gps_no_ref), _NoopCallback())
    assert resultado is None
    assert meta.error_meta_location == 1
