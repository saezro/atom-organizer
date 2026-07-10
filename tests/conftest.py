# Setup de entorno (NO es un Step de test, es instrucción de instalación):
#
# Desde /home/rodrigo_saez/atom-organizer-src/src-v2.1.5 :
#   python3 -m venv .venv-test
#   source .venv-test/bin/activate
#   pip install pytest piexif Pillow numpy pandas natsort exifread pyexiv2
#   # exiftool y ffmpeg deben existir en el PATH del sistema
#   # (apt install libimage-exiftool-perl ffmpeg)
#
#   Ejecutar los tests con:
#   python -m pytest tests/ -v
"""
Conftest global. IMPORTANTE: el stub de `winreg` se registra al importar este
módulo, ANTES de que pytest recolecte ningún test, porque `utils.py`
hace `import winreg` a nivel de módulo y winreg no existe en Linux.
"""
import sys
import types
import datetime as dt

import pytest


# --- Stub de winreg (solo para permitir el import en Linux; nunca se invoca) ---
if "winreg" not in sys.modules:
    _winreg_stub = types.ModuleType("winreg")
    _winreg_stub.HKEY_LOCAL_MACHINE = 0
    _winreg_stub.OpenKey = lambda *a, **k: (_ for _ in ()).throw(
        OSError("winreg no disponible en Linux (stub de test)")
    )
    _winreg_stub.QueryInfoKey = lambda *a, **k: (0,)
    _winreg_stub.EnumKey = lambda *a, **k: ""
    _winreg_stub.QueryValueEx = lambda *a, **k: ("", "")
    sys.modules["winreg"] = _winreg_stub


import utils as organizer_logger_mod  # noqa: E402


@pytest.fixture
def logger(tmp_path):
    """Logger real, pero sin escribir a fichero (create_file_handler=False)."""
    log = organizer_logger_mod.OrganizerLogger(
        name=f"test_logger_{id(tmp_path)}",
        log_dir=str(tmp_path / "Logs"),
        create_file_handler=False,
    )
    return log


def _build_xmp_block(
    gimbal_yaw: float,
    gimbal_pitch: float,
    absolute_altitude: float,
    relative_altitude: float,
    gimbal_roll: float,
    flight_roll: float,
    flight_yaw: float,
    flight_pitch: float,
    cam_reverse: int,
    gimbal_reverse: int,
    rtk_flag: int,
) -> bytes:
    """
    Construye un bloque de texto XMP DJI mínimo. Los parsers reales
    (get_gimbal_yaw_pitch / get_xmp_data en exif_data/exif_management.py) buscan
    las subcadenas '<x:xmpmeta' ... '</x:xmpmeta' y dentro 'drone-dji:<Tag>' seguido
    de dígitos, así que no hace falta un XMP 100% válido: basta con el texto plano.
    """
    xmp = (
        "<x:xmpmeta xmlns:x='adobe:ns:meta/'>"
        "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
        "<rdf:Description rdf:about=''"
        " xmlns:drone-dji='http://www.dji.com/drone-dji/1.0/'"
        f" drone-dji:GimbalYawDegree='{gimbal_yaw}'"
        f" drone-dji:GimbalPitchDegree='{gimbal_pitch}'"
        f" drone-dji:AbsoluteAltitude='{absolute_altitude}'"
        f" drone-dji:RelativeAltitude='{relative_altitude}'"
        f" drone-dji:GimbalRollDegree='{gimbal_roll}'"
        f" drone-dji:FlightRollDegree='{flight_roll}'"
        f" drone-dji:FlightYawDegree='{flight_yaw}'"
        f" drone-dji:FlightPitchDegree='{flight_pitch}'"
        f" drone-dji:CamReverse='{cam_reverse}'"
        f" drone-dji:GimbalReverse='{gimbal_reverse}'"
        f" drone-dji:RtkFlag='{rtk_flag}'"
        "/></rdf:RDF></x:xmpmeta>"
    )
    return xmp.encode("latin-1")


def _to_deg_minute_sec(value: float):
    """GPS decimal -> ((deg,1),(min,1),(sec*100,100)) formato piexif rational."""
    abs_value = abs(value)
    deg = int(abs_value)
    minutes_float = (abs_value - deg) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60, 4)
    return ((deg, 1), (minutes, 1), (int(seconds * 100), 100))


@pytest.fixture
def make_dji_jpeg():
    """
    Factory fixture: devuelve una función `_make(path, lat, lon, dt_val,
    relative_altitude, gimbal_yaw, gimbal_pitch) -> str` que escribe un JPEG con
    EXIF GPS + DateTimeOriginal (vía piexif) y le añade un bloque XMP DJI en
    texto plano tras el marcador de fin de JPEG.
    """
    import piexif
    from PIL import Image

    def _make(
        path: str,
        lat: float = 40.4168,
        lon: float = -3.7038,
        dt_val: dt.datetime = None,
        relative_altitude: float = 50.0,
        gimbal_yaw: float = 12.5,
        gimbal_pitch: float = -90.0,
    ) -> str:
        if dt_val is None:
            dt_val = dt.datetime(2024, 6, 1, 10, 30, 0)

        img = Image.new("RGB", (64, 48), color=(120, 130, 140))

        lat_ref = "N" if lat >= 0 else "S"
        lon_ref = "E" if lon >= 0 else "W"

        gps_ifd = {
            piexif.GPSIFD.GPSLatitudeRef: lat_ref,
            piexif.GPSIFD.GPSLatitude: _to_deg_minute_sec(lat),
            piexif.GPSIFD.GPSLongitudeRef: lon_ref,
            piexif.GPSIFD.GPSLongitude: _to_deg_minute_sec(lon),
        }
        date_str = dt_val.strftime("%Y:%m:%d %H:%M:%S")
        exif_ifd = {
            piexif.ExifIFD.DateTimeOriginal: date_str,
        }
        zeroth_ifd = {
            piexif.ImageIFD.DateTime: date_str,
        }
        exif_dict = {"0th": zeroth_ifd, "Exif": exif_ifd, "GPS": gps_ifd}
        exif_bytes = piexif.dump(exif_dict)

        img.save(path, format="JPEG", exif=exif_bytes)
        img.close()

        xmp_block = _build_xmp_block(
            gimbal_yaw=gimbal_yaw,
            gimbal_pitch=gimbal_pitch,
            absolute_altitude=relative_altitude + 500.0,
            relative_altitude=relative_altitude,
            gimbal_roll=0.0,
            flight_roll=0.0,
            flight_yaw=gimbal_yaw,
            flight_pitch=0.0,
            cam_reverse=0,
            gimbal_reverse=0,
            rtk_flag=0,
        )
        with open(path, "ab") as fh:
            fh.write(xmp_block)

        return path

    return _make


@pytest.fixture
def tmp_inspection(tmp_path, make_dji_jpeg):
    """
    Estructura mínima de carpeta de vuelo:
      <tmp_path>/PB1_V01/RGB/DJI_0001_D.JPG
      <tmp_path>/PB1_V01/RGB/DJI_0002_V.JPG
      <tmp_path>/PB1_V01/TERMICA/DJI_0001_T.JPG
      <tmp_path>/PB1_V01/TERMICA/DJI_0002_T.JPG
    Devuelve el Path raíz de PB1_V01.
    """
    root = tmp_path / "PB1_V01"
    rgb_dir = root / "RGB"
    thermal_dir = root / "TERMICA"
    rgb_dir.mkdir(parents=True)
    thermal_dir.mkdir(parents=True)

    make_dji_jpeg(str(rgb_dir / "DJI_0001_D.JPG"), gimbal_yaw=0.0)
    make_dji_jpeg(str(rgb_dir / "DJI_0002_V.JPG"), gimbal_yaw=0.0)
    make_dji_jpeg(str(thermal_dir / "DJI_0001_T.JPG"), gimbal_yaw=0.0)
    make_dji_jpeg(str(thermal_dir / "DJI_0002_T.JPG"), gimbal_yaw=0.0)

    return root


@pytest.fixture
def synthetic_jpeg(make_dji_jpeg):
    """Alias canónico de `make_dji_jpeg`, consumido por Tasks 6,7,8,10,11,13,21,24."""
    return make_dji_jpeg


@pytest.fixture
def organizer_logger_stub(logger):
    """Alias canónico de `logger`, consumido por Tasks 6,7,8,10,11,13,21,24."""
    return logger
