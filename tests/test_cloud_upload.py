"""Tests de `atom_core.cloud_upload` — subida de un vuelo a Cloud Storage.

Lo que se comprueba aquí no es "que suba": es que **sobreviva a que la subida
salga mal a mitad**. Un vuelo son varios GB y decenas de minutos de
transferencia, así que los caminos que importan son los de error — corte de
red, sesión caducada, app cerrada a mitad — y esos son justo los que nunca se
ejercitan a mano.

No se habla con GCS: se monta un servidor falso en memoria (`FakeGCS`) que
implementa el protocolo resumable de verdad (POST de apertura → `Location`,
PUT por rangos, 308 con `Range` para reanudar). Así los tests verifican el
protocolo tal y como el módulo lo emite, no una imitación conveniente.
"""
from __future__ import annotations

import email.message
import io
import json
import os
import urllib.error
import urllib.request

import pytest

from atom_core import cloud_upload as cu


# --------------------------------------------------------------------------
# GCS falso
# --------------------------------------------------------------------------

def _headers(pairs: dict | None = None) -> email.message.Message:
    msg = email.message.Message()
    for k, v in (pairs or {}).items():
        msg[k] = v
    return msg


class _Resp(io.BytesIO):
    """Lo mínimo que `urlopen` devuelve y el módulo consume."""

    def __init__(self, status: int, headers: dict | None = None, body: bytes = b""):
        super().__init__(body)
        self.status = status
        self.headers = _headers(headers)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://fake", code, "err",
                                  _headers(headers), io.BytesIO(b""))


class FakeGCS:
    """Servidor resumable en memoria, con averías programables.

    - `fail_at_byte`: corta la conexión (URLError) cuando la sesión lleva ese
      número de bytes confirmados. Simula el corte de red a mitad de fichero.
    - `expire_after`: devuelve 403 en el PUT número N. Simula la credencial
      caducada durante una transferencia larga.
    """

    def __init__(self, *, fail_at_byte: int | None = None,
                 expire_after: int | None = None):
        self.objects: dict[str, bytes] = {}
        self.sessions: dict[str, str] = {}      # session_uri -> nombre objeto
        self.buffers: dict[str, bytearray] = {}  # session_uri -> bytes recibidos
        self.fail_at_byte = fail_at_byte
        self.expire_after = expire_after
        self.puts = 0
        self.opened = 0
        self.content_ranges: list[str] = []
        self._failed_once = False

    # -- transporte ------------------------------------------------------
    def urlopen(self, req, timeout=None):
        method = req.get_method()
        url = req.full_url

        if method == "POST" and req.headers.get("X-goog-resumable") == "start":
            return self._open(url)
        if method == "PUT":
            return self._put(req, url)
        raise AssertionError(f"petición inesperada: {method} {url}")

    # -- apertura de sesión ----------------------------------------------
    def _open(self, signed_url: str):
        self.opened += 1
        name = signed_url.split("?", 1)[0].rsplit("/", 1)[-1]
        session = f"https://fake/session/{self.opened}"
        self.sessions[session] = name
        self.buffers[session] = bytearray()
        return _Resp(200, {"Location": session})

    # -- PUT de datos o de consulta --------------------------------------
    def _put(self, req, session: str):
        if session not in self.sessions:
            raise _http_error(404)

        crange = req.headers.get("Content-range", "")
        self.content_ranges.append(crange)
        buf = self.buffers[session]
        name = self.sessions[session]

        # Consulta de offset: `bytes */TOTAL`, sin cuerpo.
        if crange.startswith("bytes */"):
            total = int(crange.rsplit("/", 1)[1])
            if total == 0:
                self.objects[name] = b""
                return _Resp(200)
            if len(buf) >= total:
                return _Resp(200)
            if not buf:
                raise _http_error(308)
            raise _http_error(308, {"Range": f"bytes=0-{len(buf) - 1}"})

        self.puts += 1
        if self.expire_after is not None and self.puts == self.expire_after:
            raise _http_error(403)

        body = req.data or b""
        rango, total_s = crange.split("/", 1)
        total = int(total_s)
        start = int(rango.split(" ", 1)[1].split("-", 1)[0])

        if self.fail_at_byte is not None and not self._failed_once \
                and start + len(body) > self.fail_at_byte:
            # Se corta ANTES de confirmar: el trozo no cuenta como recibido.
            self._failed_once = True
            raise urllib.error.URLError("conexión cortada")

        if start != len(buf):
            raise _http_error(400)  # rango descolocado: el cliente se equivocó
        buf.extend(body)

        if len(buf) >= total:
            self.objects[name] = bytes(buf)
            return _Resp(200)
        return _http_error_308_response()


def _http_error_308_response():
    # GCS responde 308 (no es un error de verdad, pero urllib lo trata como tal).
    raise _http_error(308)


class StaticProvider(cu.UrlProvider):
    """Firma URLs sin backend. Cuenta cuántas veces se le pide una."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    def signed_url(self, remote: str, size: int) -> str:
        self.calls.append((remote, size))
        return f"https://fake/upload/{remote.replace('/', '_')}?sig=x"


@pytest.fixture
def gcs(monkeypatch):
    server = FakeGCS()
    monkeypatch.setattr(cu.urllib.request, "urlopen", server.urlopen)
    monkeypatch.setattr(cu, "CHUNK_SIZE", 1024)  # trozos pequeños: varios PUT por fichero
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)  # sin esperas reales
    return server


def _vuelo(tmp_path, ficheros: dict[str, bytes]):
    for rel, data in ficheros.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return tmp_path


# --------------------------------------------------------------------------
# build_plan
# --------------------------------------------------------------------------

def test_build_plan_conserva_subcarpetas_y_descarta_basura(tmp_path):
    """El server necesita saber de qué carpeta `DJI_*` venía cada imagen."""
    root = _vuelo(tmp_path, {
        "DJI_202608/DJI_0001_T.JPG": b"a" * 10,
        "DJI_202608/DJI_0001_W.JPG": b"b" * 20,
        "location.csv": b"lat,lon\n",
        "Thumbs.db": b"basura",
        "notas.docx": b"no es de vuelo",
    })

    plan = cu.build_plan(root, prefix="vuelos/antolin")
    remotos = sorted(i.remote for i in plan.items)

    assert remotos == [
        "vuelos/antolin/DJI_202608/DJI_0001_T.JPG",
        "vuelos/antolin/DJI_202608/DJI_0001_W.JPG",
        "vuelos/antolin/location.csv",
    ]
    assert plan.total_bytes == 10 + 20 + len(b"lat,lon\n")


def test_build_plan_usa_separador_posix_aunque_el_cliente_sea_windows(tmp_path):
    root = _vuelo(tmp_path, {"sub/dir/DJI_0001_T.JPG": b"x"})
    plan = cu.build_plan(root, prefix="p")
    assert plan.items[0].remote == "p/sub/dir/DJI_0001_T.JPG"
    assert "\\" not in plan.items[0].remote


def test_build_plan_normaliza_el_prefijo(tmp_path):
    root = _vuelo(tmp_path, {"a.jpg": b"x"})
    assert cu.build_plan(root, prefix="/vuelos/x/").items[0].remote == "vuelos/x/a.jpg"
    assert cu.build_plan(root, prefix="").items[0].remote == "a.jpg"


def test_build_plan_exige_directorio(tmp_path):
    fichero = tmp_path / "no-soy-carpeta.jpg"
    fichero.write_bytes(b"x")
    with pytest.raises(NotADirectoryError):
        cu.build_plan(fichero)


def test_el_manifiesto_no_se_sube_a_si_mismo(tmp_path):
    """`.atom-upload.json` vive en la carpeta del vuelo; subirlo sería absurdo."""
    root = _vuelo(tmp_path, {"a.jpg": b"x", cu.Manifest.FILENAME: b"{}"})
    assert [i.remote for i in cu.build_plan(root).items] == ["a.jpg"]


def test_eta_avisa_antes_de_empezar(tmp_path):
    root = _vuelo(tmp_path, {"a.jpg": b"x" * 1_000_000})
    plan = cu.build_plan(root)
    assert plan.eta_seconds(8) == pytest.approx(1.0, rel=0.01)  # 8 Mbps = 1 MB/s
    assert plan.eta_seconds(0) == float("inf")


# --------------------------------------------------------------------------
# Manifiesto
# --------------------------------------------------------------------------

def test_manifiesto_da_por_subido_lo_marcado(tmp_path):
    root = _vuelo(tmp_path, {"a.jpg": b"x" * 100})
    item = cu.build_plan(root).items[0]
    man = cu.Manifest(root / cu.Manifest.FILENAME)

    assert not man.is_done(item)
    man.mark(item, "md5==")
    assert man.is_done(item)
    # Y sobrevive al cierre de la app: se relee del disco.
    assert cu.Manifest(root / cu.Manifest.FILENAME).is_done(item)


def test_manifiesto_invalida_el_fichero_si_cambia(tmp_path):
    """Si el usuario reemplaza una imagen entre dos intentos, se resube."""
    root = _vuelo(tmp_path, {"a.jpg": b"x" * 100})
    item = cu.build_plan(root).items[0]
    man = cu.Manifest(root / cu.Manifest.FILENAME)
    man.mark(item, "md5==")

    (root / "a.jpg").write_bytes(b"y" * 200)
    os.utime(root / "a.jpg", (0, 0))
    assert not man.is_done(item)


def test_manifiesto_corrupto_no_impide_subir(tmp_path):
    """Peor caso aceptable: se resube todo. Inaceptable: no poder subir."""
    ruta = tmp_path / cu.Manifest.FILENAME
    ruta.write_text("{esto no es json", encoding="utf-8")
    man = cu.Manifest(ruta)
    assert man._done == {}


# --------------------------------------------------------------------------
# Subida de un fichero
# --------------------------------------------------------------------------

def test_sube_un_fichero_en_varios_trozos(tmp_path, gcs):
    root = _vuelo(tmp_path, {"a.jpg": b"z" * 3000})  # 3 trozos de 1024
    item = cu.build_plan(root).items[0]

    cu.upload_file(item, StaticProvider())

    assert gcs.objects["a.jpg"] == b"z" * 3000
    assert gcs.puts == 3


def test_los_trozos_intermedios_son_multiplo_de_256_kib(tmp_path, monkeypatch):
    """Requisito duro de GCS: un chunk intermedio mal dimensionado da 400."""
    assert cu.CHUNK_SIZE % (256 * 1024) == 0


def test_reanuda_desde_el_offset_confirmado_tras_un_corte(tmp_path, monkeypatch):
    """Un corte a mitad no puede costar volver a empezar."""
    server = FakeGCS(fail_at_byte=2048)
    monkeypatch.setattr(cu.urllib.request, "urlopen", server.urlopen)
    monkeypatch.setattr(cu, "CHUNK_SIZE", 1024)
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)

    root = _vuelo(tmp_path, {"a.jpg": b"z" * 4096})
    item = cu.build_plan(root).items[0]

    cu.upload_file(item, StaticProvider())

    assert server.objects["a.jpg"] == b"z" * 4096
    # La sesión se reutiliza (no se reabre) y se retoma en 2048, no en 0.
    assert server.opened == 1
    assert "bytes */4096" in server.content_ranges
    assert "bytes 2048-3071/4096" in server.content_ranges


def test_una_credencial_caducada_pide_url_nueva_en_vez_de_fallar(tmp_path, monkeypatch):
    """Con vuelos de horas, la signed URL caduca a mitad. Regresión: el manejo
    del 403 era código muerto porque `_is_retryable` lo daba por definitivo."""
    server = FakeGCS(expire_after=2)
    monkeypatch.setattr(cu.urllib.request, "urlopen", server.urlopen)
    monkeypatch.setattr(cu, "CHUNK_SIZE", 1024)
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)

    root = _vuelo(tmp_path, {"a.jpg": b"z" * 3000})
    item = cu.build_plan(root).items[0]
    provider = StaticProvider()

    cu.upload_file(item, provider)

    assert server.objects["a.jpg"] == b"z" * 3000
    assert len(provider.calls) == 2   # se pidió una URL nueva
    assert server.opened == 2         # y se abrió otra sesión


def test_403_es_reintentable_y_500_tambien(tmp_path):
    assert cu._is_retryable(_http_error(403))
    assert cu._is_retryable(_http_error(503))
    assert cu._is_retryable(_http_error(429))
    assert not cu._is_retryable(_http_error(404))
    assert cu._needs_new_session(_http_error(401))
    assert not cu._needs_new_session(_http_error(503))


def test_un_error_definitivo_no_se_reintenta_cinco_veces(tmp_path, monkeypatch):
    intentos = {"n": 0}

    def _siempre_404(req, timeout=None):
        intentos["n"] += 1
        raise _http_error(404)

    monkeypatch.setattr(cu.urllib.request, "urlopen", _siempre_404)
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)

    root = _vuelo(tmp_path, {"a.jpg": b"z"})
    item = cu.build_plan(root).items[0]

    with pytest.raises(urllib.error.HTTPError):
        cu.upload_file(item, StaticProvider())
    assert intentos["n"] == 1


def test_fichero_vacio_se_cierra_con_bytes_estrella(tmp_path, gcs):
    """`bytes 0--1/0` es lo que salía de la fórmula general y GCS lo rechaza."""
    root = _vuelo(tmp_path, {"vacio.csv": b""})
    item = cu.build_plan(root).items[0]

    cu.upload_file(item, StaticProvider())

    assert gcs.objects["vacio.csv"] == b""
    assert "bytes */0" in gcs.content_ranges
    assert not any("--1" in r for r in gcs.content_ranges)


def test_devuelve_el_md5_en_base64_como_lo_expone_gcs(tmp_path, gcs):
    import base64
    import hashlib

    root = _vuelo(tmp_path, {"a.jpg": b"contenido"})
    item = cu.build_plan(root).items[0]

    md5 = cu.upload_file(item, StaticProvider())
    esperado = base64.b64encode(hashlib.md5(b"contenido").digest()).decode()
    assert md5 == esperado


def test_should_stop_corta_la_subida(tmp_path, gcs):
    root = _vuelo(tmp_path, {"a.jpg": b"z" * 8000})
    item = cu.build_plan(root).items[0]

    with pytest.raises(InterruptedError):
        cu.upload_file(item, StaticProvider(), should_stop=lambda: True)


# --------------------------------------------------------------------------
# Subida del plan completo
# --------------------------------------------------------------------------

def test_sube_el_plan_entero_en_paralelo(tmp_path, gcs):
    root = _vuelo(tmp_path, {f"img_{i}.jpg": b"z" * 500 for i in range(10)})
    plan = cu.build_plan(root)

    res = cu.upload_plan(plan, StaticProvider(), concurrency=4)

    assert res.ok
    assert res.uploaded == 10
    assert res.skipped == 0
    assert len(gcs.objects) == 10
    assert res.bytes_sent == 5000


def test_la_segunda_pasada_no_resube_nada(tmp_path, gcs):
    """Cerrar la app a mitad y reabrirla debe continuar, no empezar de cero."""
    root = _vuelo(tmp_path, {f"img_{i}.jpg": b"z" * 500 for i in range(5)})
    plan = cu.build_plan(root)

    cu.upload_plan(plan, StaticProvider())
    puts_primera = gcs.puts

    res = cu.upload_plan(cu.build_plan(root), StaticProvider())

    assert res.uploaded == 0
    assert res.skipped == 5
    assert gcs.puts == puts_primera  # ni un byte más


def test_un_fichero_que_falla_no_tumba_el_resto(tmp_path, monkeypatch):
    server = FakeGCS()
    original = server.urlopen

    def _falla_el_malo(req, timeout=None):
        if "malo" in req.full_url:
            raise _http_error(404)
        return original(req, timeout=timeout)

    monkeypatch.setattr(cu.urllib.request, "urlopen", _falla_el_malo)
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)

    root = _vuelo(tmp_path, {"bueno.jpg": b"z" * 10, "malo.jpg": b"z" * 10})
    res = cu.upload_plan(cu.build_plan(root), StaticProvider(), concurrency=2)

    assert not res.ok
    assert res.uploaded == 1
    assert [r for r, _ in res.failed] == ["malo.jpg"]
    assert "HTTPError" in res.failed[0][1]


def test_lo_fallido_no_se_marca_como_subido(tmp_path, monkeypatch):
    """Si se marcase, la reanudación daría por bueno un objeto que no existe."""
    monkeypatch.setattr(cu.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(_http_error(404)))
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)

    root = _vuelo(tmp_path, {"a.jpg": b"z"})
    cu.upload_plan(cu.build_plan(root), StaticProvider())

    manifiesto = root / cu.Manifest.FILENAME
    if manifiesto.exists():
        assert json.loads(manifiesto.read_text())["done"] == {}


def test_progreso_no_repinta_por_chunk(tmp_path, gcs):
    """Con miles de ficheros, un evento por trozo deja la UI repintando."""
    root = _vuelo(tmp_path, {f"img_{i}.jpg": b"z" * 2000 for i in range(30)})
    lineas: list[str] = []

    cu.upload_plan(cu.build_plan(root), StaticProvider(),
                   concurrency=2, on_progress=lineas.append)

    # 1 línea de resumen inicial + 1 cada 25 ficheros. Nunca una por chunk.
    assert len(lineas) == 2
    assert "30 ficheros" in lineas[0]


def test_plan_sin_pendientes_no_abre_ninguna_conexion(tmp_path, gcs):
    root = _vuelo(tmp_path, {"a.jpg": b"z"})
    plan = cu.build_plan(root)
    man = cu.Manifest(root / cu.Manifest.FILENAME)
    man.mark(plan.items[0], "md5==")

    res = cu.upload_plan(plan, StaticProvider(), manifest=man)

    assert res.skipped == 1 and res.uploaded == 0
    assert gcs.opened == 0


def test_resultado_calcula_la_velocidad_real(tmp_path):
    res = cu.UploadResult(bytes_sent=12_500_000, elapsed=1.0)
    assert res.mbps == pytest.approx(100.0)
    assert cu.UploadResult().mbps == 0.0
    assert cu.UploadResult().ok
    assert not cu.UploadResult(failed=[("a", "err")]).ok


# --------------------------------------------------------------------------
# Proveedor de URLs firmadas
# --------------------------------------------------------------------------

def test_el_proveedor_manda_el_token_y_no_guarda_credenciales_de_gcs(monkeypatch):
    """El cliente nunca lleva una SA key: solo un token contra el backend."""
    capturado = {}

    def _fake(req, timeout=None):
        capturado["auth"] = req.headers.get("Authorization")
        capturado["body"] = json.loads(req.data.decode())
        return _Resp(200, body=json.dumps({"url": "https://firmada"}).encode())

    monkeypatch.setattr(cu.urllib.request, "urlopen", _fake)

    url = cu.SignedUrlProvider("https://api/firma", "tok123").signed_url("a/b.jpg", 42)

    assert url == "https://firmada"
    assert capturado["auth"] == "Bearer tok123"
    assert capturado["body"] == {"object": "a/b.jpg", "size": 42}


def test_el_proveedor_falla_claro_si_el_endpoint_no_devuelve_url(monkeypatch):
    monkeypatch.setattr(cu.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(200, body=b'{"error":"no"}'))
    with pytest.raises(RuntimeError, match="no devolvió URL"):
        cu.SignedUrlProvider("https://api/firma", "tok").signed_url("a.jpg", 1)
