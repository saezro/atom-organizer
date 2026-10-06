"""El origen es solo lectura: ni se reescribe, ni se toca su mtime, ni aparecen
ficheros nuevos en él. Cubre el motor de `split_images` (aplicar_rgb) y el CSV
de diagnóstico `_Relacion_final_imagenes_log_*.csv`, que antes se escribía en
la carpeta de logs del vuelo (origen del usuario)."""
import hashlib
import os

import pandas as pd

import pipeline as pipeline_real
from atom_core import apply


def _foto(carpeta):
    out = {}
    for raiz, _d, ficheros in os.walk(carpeta):
        for f in ficheros:
            p = os.path.join(raiz, f)
            with open(p, "rb") as fh:
                out[p] = (hashlib.sha256(fh.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


def test_csv_relacion_final_no_se_escribe_en_el_origen(tmp_path, monkeypatch):
    logs_origen = tmp_path / "logs_vuelo"
    logs_origen.mkdir()
    (logs_origen / "vuelo.log").write_bytes(b"log")
    app_logs = tmp_path / "app_logs"
    monkeypatch.setattr(pipeline_real.external_tools, "user_log_dir", lambda: str(app_logs))
    antes = _foto(str(logs_origen))

    obj = pipeline_real.RGBProcessing.__new__(pipeline_real.RGBProcessing)
    ruta = obj._guardar_csv_relacion_final(
        pd.DataFrame({"a": [1]}), "vuelo", str(tmp_path / "RGB" / "PB1_V1"))

    assert _foto(str(logs_origen)) == antes
    assert os.path.dirname(ruta) == str(app_logs) and os.path.exists(ruta)


def test_aplicar_rgb_deja_el_origen_identico_y_el_destino_sale(tmp_path, make_dji_jpeg):
    origen = tmp_path / "origen"
    origen.mkdir()
    foto = origen / "DJI_0001.JPG"
    make_dji_jpeg(str(foto))
    os.utime(foto, (1_600_000_000, 1_600_000_000))
    antes = _foto(str(origen))

    salida = tmp_path / "salida" / "DJI_0001.JPG"
    fila = {"ruta_origen": str(foto), "angulo_giro": 90, "pct_recorte": None,
            "ruta_salida_original": str(salida), "ruta_salida_crop": None}
    from tests.test_apply_rgb import _cfg
    apply._escribir_salidas_de_fila(fila, _cfg(cropping_rgb=False), pipeline_real)

    assert _foto(str(origen)) == antes
    assert salida.exists()
