import os
import sys
import pytest
from external_tools import (
    resolve_tool, is_feature_available, FEATURE_MATRIX, FeatureUnavailableError, app_base_dir,
)


def test_app_base_dir_exists():
    assert os.path.isdir(app_base_dir())


def test_resolve_tool_exiftool_linux_returns_path_name(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    tool = resolve_tool("exiftool")
    assert tool == "exiftool"


def test_resolve_tool_thermoviewer_linux_raises(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(FeatureUnavailableError):
        resolve_tool("ThermoViewer")


def test_resolve_tool_unknown_raises_value_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ValueError):
        resolve_tool("no_existe")


def test_is_feature_available_tmc_extraction_linux_false(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert is_feature_available("tmc_extraction") is False


def test_is_feature_available_tmc_extraction_windows_true(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert is_feature_available("tmc_extraction") is True


def test_is_feature_available_exif_true_both_os(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert is_feature_available("exif") is True
    monkeypatch.setattr(sys, "platform", "win32")
    assert is_feature_available("exif") is True
