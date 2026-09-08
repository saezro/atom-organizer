"""rjpeg_a_tiff.py delega la localizacion del SDK DJI y de exiftool en external_tools
(punto unico oficial), en vez de reimplementar su propia deteccion. Estos tests
fijan que:

  (a) sin --sdk/--exiftool explicitos, main() usa external_tools.resolve_tool()
  (b) con --sdk explicito, ese valor manda y external_tools NO se consulta para el SDK
  (c) ES_WINDOWS deriva de external_tools._current_os(), no de os.name

No se toca disco real ni se requieren binarios: external_tools.resolve_tool se
mockea con monkeypatch, y las "rutas" resueltas son ficheros vacios creados en
tmp_path solo para pasar el os.path.exists() de main().
"""
import sys

import external_tools
import rjpeg_a_tiff


def test_sin_sdk_explicito_usa_external_tools_resolve_tool(tmp_path, monkeypatch):
    entrada = tmp_path / "vuelo"
    entrada.mkdir()

    sdk_falso = tmp_path / "dji_irp.exe"
    sdk_falso.write_bytes(b"")

    llamadas = []

    def resolve_tool_falso(nombre):
        llamadas.append(nombre)
        if nombre == "dji_irp":
            return str(sdk_falso)
        if nombre == "exiftool":
            return "exiftool"
        raise AssertionError("herramienta inesperada: {0}".format(nombre))

    monkeypatch.setattr(external_tools, "resolve_tool", resolve_tool_falso)
    monkeypatch.setattr(sys, "argv", ["rjpeg_a_tiff.py", str(entrada)])

    rc = rjpeg_a_tiff.main()

    assert rc == 0
    assert "dji_irp" in llamadas
    assert "exiftool" in llamadas


def test_con_sdk_explicito_gana_sobre_external_tools(tmp_path, monkeypatch):
    entrada = tmp_path / "vuelo"
    entrada.mkdir()

    sdk_manual = tmp_path / "mi_sdk" / "dji_irp.exe"
    sdk_manual.parent.mkdir()
    sdk_manual.write_bytes(b"")

    llamadas = []

    def resolve_tool_falso(nombre):
        llamadas.append(nombre)
        if nombre == "exiftool":
            return "exiftool"
        raise AssertionError(
            "con --sdk explicito, external_tools.resolve_tool('dji_irp') no debe consultarse")

    monkeypatch.setattr(external_tools, "resolve_tool", resolve_tool_falso)
    monkeypatch.setattr(
        sys, "argv",
        ["rjpeg_a_tiff.py", str(entrada), "--sdk", str(sdk_manual)])

    rc = rjpeg_a_tiff.main()

    assert rc == 0
    assert "dji_irp" not in llamadas
    assert llamadas == ["exiftool"]


def test_es_windows_deriva_de_external_tools_current_os():
    assert rjpeg_a_tiff.ES_WINDOWS == (external_tools._current_os() == "win")
