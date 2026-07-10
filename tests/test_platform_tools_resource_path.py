import os
from external_tools import resource_path, app_base_dir


def test_resource_path_joins_with_native_separator():
    path = resource_path("config", "Config.ini")
    assert path == os.path.join(app_base_dir(), "config", "Config.ini")
    assert "\\\\" not in path or os.sep == "\\"


def test_resource_path_no_hardcoded_backslash_on_posix():
    if os.sep == "/":
        path = resource_path("programas_externos", "exiftool.exe")
        assert "\\" not in path


def test_resource_path_single_part():
    path = resource_path("config")
    assert path == os.path.join(app_base_dir(), "config")
