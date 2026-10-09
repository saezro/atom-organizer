"""Auditoria CRITICO 1 y 2 (parte implementable sin cambiar el flujo de etapas)."""
import os
from types import SimpleNamespace

import pytest

from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO
from atom_core.phases import PipelinePhasesMixin
from tests.test_gen_struct_folder import (FakeSignal, _con_timestamps, _destino_plano,
                                          _estadillo, _make_gsf)
from tests.test_guard_termicas_sin_girar import _Cb, _fake_phases, _fila_t, _manifiesto


def test_manifiesto_viejo_con_fila_hecha_no_da_falso_positivo(tmp_path):
    raiz = tmp_path / "destino"
    (raiz / "TERMICA").mkdir(parents=True)
    (raiz / "TERMICA" / "DJI_0001_T.JPG").write_bytes(b"x")
    m = _manifiesto(raiz / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db",
                    [_fila_t(raiz, "/origen/DJI_0001_T.JPG")])
    fila = m.todas()[0]
    m.marcar_hecha(fila["id"], "ok")
    m.cerrar()
    fake, _ = _fake_phases(tmp_path)
    fake._exigir_sin_vuelo_asignado_en_raiz(SimpleNamespace(output_folder=str(raiz)), _Cb())


def test_imagen_sin_timestamp_es_error_no_barrido_mudo(tmp_path):
    root = tmp_path / "destino"
    _destino_plano(root, ["a.jpg", "sin_fecha.jpg"])
    estadillo = _estadillo(tmp_path / "est.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 4
    import datetime as _dt

    def _fake(ruta):
        if os.path.basename(ruta) == "sin_fecha.jpg":
            return None
        return _dt.datetime(2026, 3, 17, 10, 2, 0)

    gsf.exif_management_obj.get_timestamp_from_image = _fake
    cb = FakeSignal()
    gsf.gen_folder_struct(estadillo, str(root), str(root), True, 0, 0, 0, cb, cb)
    assert gsf.error_gen_struct_folder >= 2  # una por T y otra por RGB
    assert any("Sin fecha/hora" in e for e in gsf.errors_type_gen_struct_folder)
