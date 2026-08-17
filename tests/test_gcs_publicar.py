import io
import json
from atom_core import gcs_publicar as gp


class RespuestaFalsa(io.BytesIO):
    def __init__(self, cuerpo: bytes, status: int = 200):
        super().__init__(cuerpo)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_prefijo_existe_true_cuando_la_api_devuelve_items():
    llamadas = []

    def abrir(req, timeout=None):
        llamadas.append(req.full_url)
        return RespuestaFalsa(json.dumps({"items": [{"name": "CALAMOCHA/x.tif"}]}).encode())

    assert gp.prefijo_existe("plantas_pv_nl", "CALAMOCHA/", abrir_url=abrir) is True
    assert "prefix=CALAMOCHA%2F" in llamadas[0]
    assert "maxResults=1" in llamadas[0]


def test_prefijo_existe_false_cuando_no_hay_items():
    def abrir(req, timeout=None):
        return RespuestaFalsa(json.dumps({}).encode())

    assert gp.prefijo_existe("plantas_pv_nl", "OCANA/", abrir_url=abrir) is False


def test_prefijo_existe_false_si_la_api_falla():
    def abrir(req, timeout=None):
        raise OSError("boom")

    assert gp.prefijo_existe("plantas_pv_nl", "CALAMOCHA/", abrir_url=abrir) is False


def test_subir_objeto_usa_upload_y_no_sobrescribe_por_accidente():
    vistos = {}

    def abrir(req, timeout=None):
        vistos["url"] = req.full_url
        vistos["metodo"] = req.get_method()
        vistos["cuerpo"] = req.data
        return RespuestaFalsa(json.dumps({"name": "x"}).encode())

    ok = gp.subir_objeto("plantas_pv_nl", "CALAMOCHA/ESTADILLOS/T/d.csv",
                         b"PB;Vuelo", abrir_url=abrir)
    assert ok is True
    assert "/upload/storage/v1/b/plantas_pv_nl/o" in vistos["url"]
    assert "uploadType=media" in vistos["url"]
    assert "name=CALAMOCHA%2FESTADILLOS%2FT%2Fd.csv" in vistos["url"]
    assert vistos["metodo"] == "POST"
    assert vistos["cuerpo"] == b"PB;Vuelo"


def test_subir_objeto_devuelve_false_si_falla_y_no_lanza():
    def abrir(req, timeout=None):
        raise OSError("403")

    assert gp.subir_objeto("plantas_pv_nl", "CALAMOCHA/x.csv", b"a", abrir_url=abrir) is False
