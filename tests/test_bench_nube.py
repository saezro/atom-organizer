import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import bench_nube as b  # noqa: E402


def _nombres(n):
    out = []
    for i in range(1, n + 1):
        out += [f"DJI_20260824100{i % 10}00_{i:04d}_T_point1.JPG",
                f"DJI_20260824100{i % 10}01_{i:04d}_V_point1.JPG"]
    return out


def test_seleccion_determinista_y_orden():
    nombres = _nombres(200)
    p1 = b.seleccionar_pares(nombres, 150)
    p2 = b.seleccionar_pares(list(reversed(nombres)), 150)
    assert p1 == p2 and len(p1) == 150
    assert [t for t, _ in p1] == sorted(t for t, _ in p1)
    assert all("_T_" in t and "_V_" in v for t, v in p1)


def test_pocos_pares_error():
    with pytest.raises(b.BenchError):
        b.seleccionar_pares(_nombres(10), 150)


def test_huerfanos_y_duplicados_descartados():
    nombres = _nombres(5) + ["DJI_20260824100000_0099_T_point1.JPG",
                             "DJI_20260824100000_0001_T_point2.JPG"]
    assert len(b.seleccionar_pares(nombres, 4)) == 4
    with pytest.raises(b.BenchError):
        b.seleccionar_pares(nombres, 5)  # idx 1 duplicado y 99 sin W


def test_validar_n_pares():
    b.validar_n_pares(150, 150)
    with pytest.raises(b.BenchError):
        b.validar_n_pares(149, 150)
    with pytest.raises(b.BenchError):
        b.validar_n_pares(10, 0)


def test_s_por_gb_y_metricas():
    assert b.segundos_por_gb(10, 2_000_000_000) == 5
    assert b.segundos_por_gb(10, 0) is None
    assert b.imagenes_por_segundo(100, 20) == 5
    m = b.metricas_etapas({"RGB": 10.0, "Térmicas": 20.0}, 1_000_000_000, 150, 300)
    assert m["RGB"]["img_por_s"] == 30 and m["Térmicas"]["img_por_s"] == 7.5
    assert m["RGB"]["s_por_gb"] == 10


def test_salida_prohibida():
    with pytest.raises(b.BenchError):
        b.validar_salida("gs://plantas_pv_nl/x")
    b.validar_salida("gs://datos_para_organizar/_bench/rust/2026-11-16/")


def test_gcs_mock_listar_y_descargar(tmp_path):
    blob = mock.Mock(size=7)
    blob.name = "c/a.JPG"
    cli = mock.Mock()
    cli.list_blobs.return_value = [blob]
    assert b.listar(cli, "gs://bk/c") == {"a.JPG": 7}

    def dl(ruta):
        Path(ruta).write_bytes(b"1234567")
    cli.bucket.return_value.blob.return_value.download_to_filename.side_effect = dl
    assert b.descargar(cli, "gs://bk/c", ["a.JPG", "b.JPG"], tmp_path, hilos=99) == 14
    cli.bucket.return_value.blob.assert_any_call("c/a.JPG")
