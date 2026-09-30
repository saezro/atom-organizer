"""Indicador «¿hay estadillo en esta carpeta?» del selector del kiosco:
`Api._estadillo_en_carpeta` (usado por `estadillo_espera_estado`, expuesto
como `estadillo_en_carpeta` en GET /api/estadillo/espera) reusa
`atom_core.estadillo.detectar_estadillos` sin bloquear -corre en un hilo
aparte y cachea por carpeta."""
import time

import app_webview


def _api():
    return app_webview.Api()


def _esperar(fn, timeout=2.0):
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        r = fn()
        if not r.get("buscando"):
            return r
        time.sleep(0.01)
    raise AssertionError("el escaneo no termino a tiempo")


def test_sin_carpeta_devuelve_vacio_sin_buscar(tmp_path):
    api = _api()
    assert api._estadillo_en_carpeta(None) == {"encontrado": False, "nombre": None, "buscando": False}
    assert api._estadillo_en_carpeta(str(tmp_path / "no_existe")) == {
        "encontrado": False, "nombre": None, "buscando": False}


def test_primera_llamada_dice_buscando_y_luego_encontrado(tmp_path):
    api = _api()
    carpeta = tmp_path / "fotos"
    carpeta.mkdir()
    (carpeta / "20260920_estadillo_PilotoA.csv").write_text(
        "PB;Vuelo;Fecha;Trabajo;Piloto;Hora_de_inicio;Hora_final\n"
        "1;1;2026:09:20;PLANTA_A;PilotoA;09:00:00;09:20:00\n",
        encoding="utf-8",
    )

    primera = api._estadillo_en_carpeta(str(carpeta))
    assert primera == {"encontrado": False, "nombre": None, "buscando": True}

    final = _esperar(lambda: api._estadillo_en_carpeta(str(carpeta)))
    assert final == {
        "encontrado": True, "nombre": "20260920_estadillo_PilotoA.csv", "buscando": False,
    }


def test_carpeta_sin_estadillo_da_no_encontrado(tmp_path):
    api = _api()
    carpeta = tmp_path / "fotos"
    carpeta.mkdir()
    (carpeta / "foto.jpg").write_bytes(b"")

    api._estadillo_en_carpeta(str(carpeta))
    final = _esperar(lambda: api._estadillo_en_carpeta(str(carpeta)))
    assert final == {"encontrado": False, "nombre": None, "buscando": False}


def test_resultado_se_cachea_no_relanza_hilo(tmp_path, monkeypatch):
    api = _api()
    carpeta = tmp_path / "fotos"
    carpeta.mkdir()

    llamadas = {"n": 0}
    from atom_core import estadillo as estadillo_mod
    original = estadillo_mod.detectar_estadillos

    def _contado(c, *a, **k):
        llamadas["n"] += 1
        return original(c, *a, **k)

    monkeypatch.setattr(estadillo_mod, "detectar_estadillos", _contado)

    api._estadillo_en_carpeta(str(carpeta))
    _esperar(lambda: api._estadillo_en_carpeta(str(carpeta)))
    # Repetidas consultas tras terminar: caché, no relanza el escaneo.
    api._estadillo_en_carpeta(str(carpeta))
    api._estadillo_en_carpeta(str(carpeta))
    assert llamadas["n"] == 1


def test_estadillo_espera_estado_expone_estadillo_en_carpeta(tmp_path, monkeypatch):
    import exif as exif_mod
    api = _api()
    carpeta = tmp_path / "fotos"
    carpeta.mkdir()
    monkeypatch.setattr(exif_mod, "rango_horas_exif", lambda c: (0, None, None))

    api.estadillo_espera_iniciar(str(carpeta), {})

    estado = api.estadillo_espera_estado()
    assert "estadillo_en_carpeta" in estado
    assert set(estado["estadillo_en_carpeta"]) == {"encontrado", "nombre", "buscando"}
