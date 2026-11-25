"""Modo `solo_timestamp` de `_leer_metadatos_rust` (calendario previo): una térmica DJI con el SOS
tras los 256 KiB de cabecera (`necesita_completo`) NO se relee entera si la cabecera ya trae la fecha
EXIF y el XMP cerrado; si falta alguno se relee entera como en el run normal, que nunca usa el modo.
`atom_rs` se sustituye por un doble que cuenta llamadas (el parseo real lo cubre `cargo test`)."""
import datetime
import types

from atom_core import indice

_CABECERA = b"\xff\xd8" + b"\0" * 100
_ENTERO = _CABECERA + b"\0" * 1000


def _cab(*, necesita_completo, timestamp_iso=None, timestamp_cabecera=None, xmp_presente=True,
         formato="jpeg"):
    return types.SimpleNamespace(
        formato=formato, necesita_completo=necesita_completo, xmp_presente=xmp_presente,
        timestamp_iso=timestamp_iso, timestamp_cabecera=timestamp_cabecera,
        modelo="M3T", make="DJI", make_ilegible=False, gps=None, ancho=None, alto=None,
        gimbal_yaw="-45.5", gimbal_pitch="-88.0", flight_yaw_en_xmp=False,
        xmp=["0"] * 9)


class _Rs:
    def __init__(self, corta, entera):
        self.corta, self.entera, self.llamadas = corta, entera, []

    def parsear_cabecera(self, buf, completo=False):
        self.llamadas.append((len(buf), completo))
        return self.entera if completo else self.corta


def _leer(monkeypatch, rs, solo_timestamp):
    leidos = []
    monkeypatch.setattr(indice, "_leer_fichero_entero", lambda ruta: leidos.append(ruta) or _ENTERO)
    contador = indice._ContadorRust()
    dato = indice._leer_metadatos_rust(rs, "/x/DJI_0001_T.JPG", "DJI_0001_T.JPG", _CABECERA, None,
                                       contador, solo_timestamp=solo_timestamp)
    return dato, leidos, contador


def test_termica_con_fecha_en_cabecera_no_se_relee_entera(monkeypatch):
    corta = _cab(necesita_completo=True, timestamp_cabecera="2026:08:20 10:11:12")
    rs = _Rs(corta, _cab(necesita_completo=False, timestamp_iso="2026:08:20 10:11:12"))
    dato, leidos, contador = _leer(monkeypatch, rs, solo_timestamp=True)
    assert leidos == [] and contador.corto == 0
    assert rs.llamadas == [(len(_CABECERA), False)]
    assert dato.timestamp == datetime.datetime(2026, 8, 20, 10, 11, 12)
    assert dato.yaw == -45.5 and dato.make == "DJI"


def test_sin_fecha_en_cabecera_se_relee_entera(monkeypatch):
    corta = _cab(necesita_completo=True, timestamp_cabecera=None)
    rs = _Rs(corta, _cab(necesita_completo=False, timestamp_iso="2026:08:20 10:11:12"))
    dato, leidos, contador = _leer(monkeypatch, rs, solo_timestamp=True)
    assert leidos == ["/x/DJI_0001_T.JPG"] and contador.corto == 1
    assert rs.llamadas[-1] == (len(_ENTERO), True)
    assert dato.timestamp == datetime.datetime(2026, 8, 20, 10, 11, 12)


def test_xmp_sin_cerrar_en_cabecera_se_relee_entera(monkeypatch):
    corta = _cab(necesita_completo=True, timestamp_cabecera="2026:08:20 10:11:12", xmp_presente=False)
    rs = _Rs(corta, _cab(necesita_completo=False, timestamp_iso="2026:08:20 10:11:12"))
    _dato, leidos, _contador = _leer(monkeypatch, rs, solo_timestamp=True)
    assert leidos == ["/x/DJI_0001_T.JPG"]


def test_run_normal_siempre_relee_entera(monkeypatch):
    corta = _cab(necesita_completo=True, timestamp_cabecera="2026:08:20 10:11:12")
    rs = _Rs(corta, _cab(necesita_completo=False, timestamp_iso="2026:08:20 10:11:12"))
    _dato, leidos, contador = _leer(monkeypatch, rs, solo_timestamp=False)
    assert leidos == ["/x/DJI_0001_T.JPG"] and contador.corto == 1


def test_leer_timestamps_activa_el_modo(monkeypatch, tmp_path):
    visto = {}

    def _falso(imagenes, *a, **k):
        visto.update(k)
        return types.SimpleNamespace(metadatos=[], no_dji=[], omitidas=[], no_disponibles=[])

    monkeypatch.setattr(indice, "_listar_imagenes", lambda origen: [])
    monkeypatch.setattr(indice, "_leer_lote", _falso)
    indice.leer_timestamps(str(tmp_path), types.SimpleNamespace(), exif=object())
    assert visto.get("solo_timestamp") is True
