"""
Smoke test: si esto falla, hay un import roto en el árbol de módulos principal.
Se ejecuta el primero para detectar problemas de entorno (winreg, pyexiv2, exiftool)
antes de perder tiempo en tests más finos.
"""
import shutil


def test_import_general_functions_utils():
    import utils
    assert hasattr(utils, "Utils")


def test_import_exif_management():
    import exif
    assert hasattr(exif, "GeneralInformationFromImage")


def test_import_organizer_logger():
    import utils as organizer_logger
    assert hasattr(organizer_logger, "OrganizerLogger")


def test_import_gen_struct_folder():
    import pipeline as gen_struct_folder
    assert hasattr(gen_struct_folder, "GenStructFolder")


def test_import_split_images():
    import pipeline as split_images
    assert hasattr(split_images, "SplitImages")


def test_import_compress_image():
    import pipeline as compress_image
    assert hasattr(compress_image, "CompressImage")


def test_exiftool_available_in_path():
    assert shutil.which("exiftool") is not None, (
        "exiftool no está en el PATH. Instala con "
        "'apt install libimage-exiftool-perl' antes de correr tests que lo invoquen."
    )


def test_ffmpeg_available_in_path():
    assert shutil.which("ffmpeg") is not None, (
        "ffmpeg no está en el PATH. Instala con 'apt install ffmpeg' antes de "
        "correr tests que lo invoquen."
    )
