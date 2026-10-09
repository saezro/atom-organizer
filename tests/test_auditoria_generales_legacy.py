"""Auditoria CRITICO 3: el motor legacy (etapas Cloud Run) trataba el PB
GENERALES como un vuelo (PBGENERALES/PBGENERALES_V1) y las fotos acababan en
SIN_ORDENAR. Deben ir planas a FOTOS_GENERALES."""
import os

from tests.test_gen_struct_folder import (FakeSignal, _con_timestamps, _destino_plano,
                                          _estadillo, _make_gsf)


def test_pb_generales_va_a_fotos_generales_sin_carpeta_de_vuelo(tmp_path):
    root = tmp_path / "destino"
    _destino_plano(root, ["gen.jpg", "vuelo.jpg"])
    estadillo = _estadillo(tmp_path / "est.csv",
                           [("GENERALES", "1", "2026:03:17", "09:00:00", "09:30:00"),
                            ("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 4
    _con_timestamps(gsf, {"gen.jpg": "09:10:00", "vuelo.jpg": "10:02:00"})
    cb = FakeSignal()

    gsf.gen_folder_struct(estadillo, str(root), str(root), True, 0, 0, 0, cb, cb)

    assert not (root / "TERMICA" / "PBGENERALES").exists()
    assert not (root / "RGB" / "PBGENERALES").exists()
    assert os.listdir(root / "FOTOS_GENERALES") == ["gen.jpg"]  # T y RGB comparten nombre aqui
    for sub in ("RGB", "TERMICA"):
        assert os.listdir(root / sub / "PB1" / "PB1_V1") == ["vuelo.jpg"]
        assert [f for f in os.listdir(root / sub) if f.endswith(".jpg")] == []
