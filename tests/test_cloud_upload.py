"""Tests de `atom_core.cloud_upload` — subida de un vuelo a Cloud Storage.

Lo que se comprueba aquí no es "que suba": es que **sobreviva a que la subida
salga mal a mitad**. Un vuelo son varios GB y decenas de minutos de
transferencia, así que los caminos que importan son los de error — corte de
red, sesión caducada, app cerrada a mitad — y esos son justo los que nunca se
ejercitan a mano.

La subida real vive en Rust (`atom_rs.subir_lote`): aquí no se habla con GCS ni
se prueba el protocolo resumable (eso es de los tests Rust). Se monta un
`atom_rs` falso en memoria (`FakeGCS`) con el mismo contrato que `subir_lote`
(items, `url_fn`, callback de eventos, `hecho` final) para comprobar lo que sí es
de Python: reconciliación con el bucket, manifiesto, contadores y eventos.
"""
from __future__ import annotations

import base64
import email.message
import hashlib
import io
import json
import os
import sys
import types
import urllib.error
import urllib.request

import pytest

from atom_core import cloud_upload as cu
from atom_core import rust_bridge


# --------------------------------------------------------------------------
# GCS falso
# --------------------------------------------------------------------------

def _headers(pairs: dict | None = None) -> email.message.Message:
    msg = email.message.Message()
    for k, v in (pairs or {}).items():
        msg[k] = v
    return msg


def _http_error(code: int, headers: dict | None = None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://fake", code, "err",
                                  _headers(headers), io.BytesIO(b""))


class FakeGCS:
    """`atom_rs` falso con el contrato de `subir_lote`, con un bucket en memoria.

    - `rechazar`: fragmentos de nombre remoto que acaban en `fallidos` (motivo "HTTP 404").
    - `ya_existen`: fragmentos de nombre remoto que GCS contesta 412 (`saltados_412`).
    - `puts`: ficheros realmente enviados; `llamadas`: veces que se invocó `subir_lote`.
    """

    def __init__(self, *, rechazar: tuple[str, ...] = (), ya_existen: tuple[str, ...] = ()):
        self.objects: dict[str, bytes] = {}
        self.rechazar = tuple(rechazar)
        self.ya_existen = tuple(ya_existen)
        self.puts = 0
        self.llamadas: list[dict] = []

    @property
    def opened(self) -> int:  # ficheros que llegaron a abrir sesión
        return self.puts

    def instalar(self, monkeypatch):
        m = types.ModuleType("atom_rs")
        m.version_abi = lambda: rust_bridge.ABI_ESPERADA
        m.version = lambda: "9.9.9"
        m.subir_lote = self.subir_lote
        monkeypatch.setitem(sys.modules, "atom_rs", m)
        return self

    def subir_lote(self, items, prefijo, modo, url_fn, token_fn, recover_fn, callback,
                   should_stop, **kw):
        self.llamadas.append(dict(items=list(items), prefijo=prefijo, modo=modo))
        subidos = saltados = enviados = 0
        fallidos: list = []
        md5: dict[str, str] = {}
        for local, remote, size, sobrescribir, _urgente in items:
            if should_stop():
                fallidos.append((remote, "cancelado"))
                continue
            url_fn(remote, size, sobrescribir)
            if any(x in remote for x in self.ya_existen):
                saltados += 1
                continue
            if any(x in remote for x in self.rechazar):
                fallidos.append((remote, "HTTP 404"))
                continue
            datos = open(local, "rb").read()
            self.objects[remote] = datos
            self.puts += 1
            subidos += 1
            enviados += len(datos)
            md5[remote] = base64.b64encode(hashlib.md5(datos).digest()).decode()
        total = sum(int(i[2]) for i in items)
        callback(dict(kind="progreso", bytes_done=enviados, bytes_total=total,
                      files_done=subidos + saltados + len(fallidos), files_total=len(items),
                      mbps=1.0, eta=None, hilos_activos=1, hilos_max=1,
                      fichero_actual="", fallidas=len(fallidos), reintentos=0,
                      medidas={"red_s": 0.1, "disco_s": 0.1, "sesion_s": 0.0,
                              "reintentos": {}}))
        return dict(kind="hecho", subidos=subidos, saltados_412=saltados,
                    bytes_enviados=enviados, elapsed=0.5, reintentos=0,
                    cancelado=False, abortado=None, fallidos=fallidos, md5=md5)


class StaticProvider(cu.UrlProvider):
    """Firma URLs sin backend. Cuenta cuántas veces se le pide una."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []
        # Qué items pidieron URL sin protección `ifGenerationMatch=0`.
        self.sobrescritos: list[str] = []

    def upload_url(self, remote: str, size: int, *,
                   sobrescribir: bool = False) -> str:
        self.calls.append((remote, size))
        if sobrescribir:
            self.sobrescritos.append(remote)
        return f"https://fake/upload/{remote.replace('/', '_')}?sig=x"


@pytest.fixture
def gcs(monkeypatch):
    return FakeGCS().instalar(monkeypatch)


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

    plan = cu.build_plan(root, prefix="vuelos/planta_b")
    remotos = sorted(i.remote for i in plan.items)

    assert remotos == [
        "vuelos/planta_b/DJI_202608/DJI_0001_T.JPG",
        "vuelos/planta_b/DJI_202608/DJI_0001_W.JPG",
        "vuelos/planta_b/location.csv",
    ]
    assert plan.total_bytes == 10 + 20 + len(b"lat,lon\n")


def test_build_plan_nunca_sube_el_manifiesto(tmp_path):
    root = tmp_path / "planta"
    (root / ".organizado").mkdir(parents=True)
    (root / ".organizado" / "manifiesto.db").write_bytes(b"x")
    (root / ".organizado" / "foto.jpg").write_bytes(b"x")
    (root / "a.jpg").write_bytes(b"x")
    plan = cu.build_plan(root, prefix="p", suffixes=())
    assert [i.remote for i in plan.items] == ["p/a.jpg"]


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
    # Y sobrevive al cierre de la app: se relee del disco (tras el flush final).
    man.flush()
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

def test_upload_one_sube_un_fichero(tmp_path, gcs):
    root = _vuelo(tmp_path, {"a.jpg": b"z" * 3000})
    item = cu.build_plan(root).items[0]

    cu.upload_one(item, StaticProvider())

    assert gcs.objects["a.jpg"] == b"z" * 3000
    assert gcs.puts == 1


def test_devuelve_el_md5_en_base64_como_lo_expone_gcs(tmp_path, gcs):
    import base64
    import hashlib

    root = _vuelo(tmp_path, {"a.jpg": b"contenido"})
    item = cu.build_plan(root).items[0]

    md5 = cu.upload_one(item, StaticProvider())
    esperado = base64.b64encode(hashlib.md5(b"contenido").digest()).decode()
    assert md5 == esperado


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
    FakeGCS(rechazar=("malo",)).instalar(monkeypatch)

    root = _vuelo(tmp_path, {"bueno.jpg": b"z" * 10, "malo.jpg": b"z" * 10})
    res = cu.upload_plan(cu.build_plan(root), StaticProvider(), concurrency=2)

    assert not res.ok
    assert res.uploaded == 1
    assert [r for r, _ in res.failed] == ["malo.jpg"]
    assert "404" in res.failed[0][1]


def test_lo_fallido_no_se_marca_como_subido(tmp_path, monkeypatch):
    """Si se marcase, la reanudación daría por bueno un objeto que no existe."""
    FakeGCS(rechazar=("a.jpg",)).instalar(monkeypatch)

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

    # Solo la línea de resumen inicial (el progreso fino va por on_stats). Nunca una por fichero.
    assert len(lineas) == 1
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

    url = cu.SignedUrlProvider("https://api/firma", "tok123").upload_url("a/b.jpg", 42)

    assert url == "https://firmada"
    assert capturado["auth"] == "Bearer tok123"
    assert capturado["body"] == {"object": "a/b.jpg", "size": 42}


def test_el_proveedor_falla_claro_si_el_endpoint_no_devuelve_url(monkeypatch):
    monkeypatch.setattr(cu.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp(200, body=b'{"error":"no"}'))
    with pytest.raises(RuntimeError, match="no devolvió URL"):
        cu.SignedUrlProvider("https://api/firma", "tok").upload_url("a.jpg", 1)


# --------------------------------------------------------------------------
# Reconciliación contra el bucket
# --------------------------------------------------------------------------
# El manifiesto local (`.atom-upload.json`) vive DENTRO de la carpeta del vuelo
# y se pierde con facilidad: se copia el vuelo a otro disco, se reinstala la
# app, alguien limpia ocultos. Antes eso costaba resubir los 8,7 GB enteros
# aunque el bucket ya los tuviera. Lo que se comprueba aquí es que la fuente de
# verdad pasa a ser el bucket, y el manifiesto solo una caché.

class ListingProvider(StaticProvider):
    """Como `StaticProvider`, pero además sabe qué hay en el destino."""

    def __init__(self, remotos: dict[str, cu.RemoteObject] | None = None,
                 *, explota: bool = False):
        super().__init__()
        self.remotos = remotos or {}
        self.explota = explota
        self.listados = 0

    def listar_remotos(self, prefix: str):
        self.listados += 1
        if self.explota:
            raise urllib.error.URLError("sin red para listar")
        return dict(self.remotos)


def _remoto(plan, indices: list[int], *, md5: str = "") -> dict[str, cu.RemoteObject]:
    """Simula que ciertos ficheros del plan ya están en el bucket."""
    return {
        plan.items[i].remote: cu.RemoteObject(plan.items[i].remote,
                                              plan.items[i].size, md5)
        for i in indices
    }


def test_sin_manifiesto_no_resube_lo_que_el_bucket_ya_tiene(tmp_path, gcs):
    """El caso que motivó todo esto: manifiesto perdido, bucket lleno.

    Antes se resubía el vuelo entero. Ahora no se manda ni un byte.
    """
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500, "b.jpg": b"b" * 700})
    plan = cu.build_plan(root, prefix="v")
    assert not (root / cu.Manifest.FILENAME).exists()  # nunca hubo manifiesto

    provider = ListingProvider(_remoto(plan, [0, 1]))
    res = cu.upload_plan(plan, provider)

    assert res.uploaded == 0
    assert res.skipped == 2
    assert res.skipped_remoto == 2
    assert res.reconciliado is True
    assert gcs.puts == 0        # no se abrió ni una transferencia
    assert provider.calls == []


def test_solo_se_suben_los_que_faltan(tmp_path, gcs):
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500, "b.jpg": b"b" * 700,
                             "c.jpg": b"c" * 300})
    plan = cu.build_plan(root, prefix="v")

    provider = ListingProvider(_remoto(plan, [0, 2]))
    res = cu.upload_plan(plan, provider)

    assert res.uploaded == 1
    assert res.skipped == 2
    assert [r for r, _ in provider.calls] == [plan.items[1].remote]


def test_se_resube_si_el_tamano_del_bucket_no_coincide(tmp_path, gcs):
    """Mismo nombre y distinto tamaño = no es el mismo fichero.

    Es el caso de dos vuelos distintos bajo la misma inspección: las imágenes
    de dron se llaman igual (`DJI_0001.JPG`) en todos.
    """
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500})
    plan = cu.build_plan(root, prefix="v")
    remotos = {plan.items[0].remote: cu.RemoteObject(plan.items[0].remote, 999)}

    res = cu.upload_plan(plan, ListingProvider(remotos))

    assert res.uploaded == 1
    assert res.skipped == 0


def test_se_resube_si_el_md5_del_bucket_contradice_al_manifiesto(tmp_path, gcs):
    """Coincidir en tamaño no basta si los hashes dicen lo contrario."""
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500})
    plan = cu.build_plan(root, prefix="v")

    manifest = cu.Manifest(root / cu.Manifest.FILENAME)
    manifest.mark(plan.items[0], "md5-del-que-subi-yo")
    remotos = {plan.items[0].remote: cu.RemoteObject(plan.items[0].remote, 500,
                                                     "md5-de-otra-cosa")}

    res = cu.upload_plan(plan, ListingProvider(remotos), manifest=manifest)

    assert res.uploaded == 1


def test_el_manifiesto_se_reconstruye_con_lo_que_hay_en_el_bucket(tmp_path, gcs):
    """Tras reconciliar, el registro local vuelve a reflejar la realidad."""
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500})
    plan = cu.build_plan(root, prefix="v")
    remotos = _remoto(plan, [0], md5="hash-del-bucket")

    cu.upload_plan(plan, ListingProvider(remotos))

    guardado = json.loads((root / cu.Manifest.FILENAME).read_text())
    assert guardado["done"][plan.items[0].remote]["md5"] == "hash-del-bucket"


def test_si_no_se_puede_listar_se_sube_con_el_manifiesto_local(tmp_path, gcs):
    """Sin listado (signed URL, o la consulta falla) se vuelve al comportamiento
    de antes: el manifiesto manda. Lo que NO puede pasar es que no se suba."""
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500})
    plan = cu.build_plan(root, prefix="v")

    res = cu.upload_plan(plan, ListingProvider(explota=True))

    assert res.uploaded == 1
    assert res.reconciliado is False


def test_un_provider_que_no_sabe_listar_no_rompe_la_subida(tmp_path, gcs):
    """`SignedUrlProvider` no tiene permiso de listado: devuelve None."""
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500})
    plan = cu.build_plan(root, prefix="v")

    res = cu.upload_plan(plan, StaticProvider())

    assert res.uploaded == 1
    assert res.reconciliado is False


def test_listar_objetos_remotos_recorre_todas_las_paginas(monkeypatch):
    """Un vuelo pasa de 1000 objetos: sin paginar se daría por subido de menos."""
    paginas = [
        {"items": [{"name": "v/a.jpg", "size": "10", "md5Hash": "h1"}],
         "nextPageToken": "t1"},
        {"items": [{"name": "v/b.jpg", "size": "20", "md5Hash": "h2"}]},
    ]
    vistas: list[str] = []

    def fake_urlopen(req, timeout=None):
        vistas.append(req.full_url)
        return _Resp(200, body=json.dumps(paginas[len(vistas) - 1]).encode())

    monkeypatch.setattr(cu.urllib.request, "urlopen", fake_urlopen)

    class Auth:
        def access_token(self, force_refresh=False):
            return "tok"

    out = cu.listar_objetos_remotos("bucket", "v", Auth())

    assert set(out) == {"v/a.jpg", "v/b.jpg"}
    assert out["v/a.jpg"] == cu.RemoteObject("v/a.jpg", 10, "h1")
    assert "pageToken=t1" in vistas[1]


# --------------------------------------------------------------------------
# Diagnóstico: reintentos y progreso
# --------------------------------------------------------------------------

def test_sumar_medidas_acumula_grupos_y_tolera_none():
    a = cu.medidas_vacias(); a["red_s"] = 2.0; a["reintentos"]["429"] = 1
    b = cu.medidas_vacias(); b["red_s"] = 3.0; b["disco_s"] = 1.0; b["reintentos"]["429"] = 2
    t = cu.sumar_medidas(a, b)
    assert t["red_s"] == 5.0 and t["disco_s"] == 1.0 and t["reintentos"]["429"] == 3
    assert cu.sumar_medidas(None, None) == cu.medidas_vacias()


def test_on_stats_informa_del_progreso_para_poder_pintarlo(tmp_path, gcs):
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 4000, "b.jpg": b"b" * 4000})
    plan = cu.build_plan(root, prefix="v")
    recogido: list[dict] = []

    cu.upload_plan(plan, StaticProvider(), on_stats=recogido.append)

    assert recogido, "la UI se queda sin datos que enseñar"
    ultimo = recogido[-1]
    assert ultimo["final"] is True
    assert ultimo["files_done"] == 2
    assert ultimo["files_total"] == 2
    assert ultimo["bytes_done"] == 8000
    assert ultimo["bytes_total"] == 8000
    assert ultimo["elapsed"] > 0


def test_el_progreso_no_ahoga_a_la_ui_con_un_evento_por_trozo(tmp_path, gcs):
    """Con 8,7 GB, un evento por chunk son miles de repintados."""
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 50_000})  # ~49 chunks de 1 KB
    plan = cu.build_plan(root, prefix="v")
    recogido: list[dict] = []

    cu.upload_plan(plan, StaticProvider(), on_stats=recogido.append)

    # Throttle de 1 s: en un test que dura milisegundos solo debe salir el
    # evento final forzado (y como mucho el del fichero completado).
    assert len(recogido) <= 3


def test_se_resube_si_el_fichero_local_cambio_despues_de_subirlo(tmp_path, gcs):
    """Reemplazar una imagen por otra del MISMO tamaño no puede pasar en balde.

    El bucket seguiría teniendo la versión vieja y el tamaño coincide, así que
    la comparación contra el destino da «ya está». Lo que lo detecta es el
    mtime que guardó el manifiesto al subirlo.
    """
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500})
    plan = cu.build_plan(root, prefix="v")

    manifest = cu.Manifest(root / cu.Manifest.FILENAME)
    manifest.mark(plan.items[0], "md5-viejo")

    # Mismo tamaño, contenido distinto, mtime posterior.
    (root / "a.jpg").write_bytes(b"z" * 500)
    os.utime(root / "a.jpg", (10_000_000, 10_000_000))

    remotos = {plan.items[0].remote: cu.RemoteObject(plan.items[0].remote, 500,
                                                     "md5-viejo")}
    res = cu.upload_plan(cu.build_plan(root, prefix="v"),
                         ListingProvider(remotos), manifest=manifest)

    assert res.uploaded == 1, "se quedó la versión vieja en el bucket"


def test_un_fichero_intacto_no_se_resube_por_tener_manifiesto(tmp_path, gcs):
    """El contrapunto del anterior: si no ha cambiado, no se toca."""
    root = _vuelo(tmp_path, {"a.jpg": b"a" * 500})
    plan = cu.build_plan(root, prefix="v")

    manifest = cu.Manifest(root / cu.Manifest.FILENAME)
    manifest.mark(plan.items[0], "md5-igual")
    remotos = {plan.items[0].remote: cu.RemoteObject(plan.items[0].remote, 500,
                                                     "md5-igual")}

    res = cu.upload_plan(plan, ListingProvider(remotos), manifest=manifest)

    assert res.uploaded == 0
    assert res.skipped == 1


def test_el_listado_avisa_si_se_queda_corto(tmp_path, monkeypatch):
    """Truncar el inventario hace resubir de más; callarlo lo haría inexplicable."""
    def fake_urlopen(req, timeout=None):
        return _Resp(200, body=json.dumps(
            {"items": [{"name": "v/a.jpg", "size": "1"}],
             "nextPageToken": "siempre-hay-mas"}).encode())

    monkeypatch.setattr(cu.urllib.request, "urlopen", fake_urlopen)

    class Auth:
        def access_token(self, force_refresh=False):
            return "tok"

    avisos: list[str] = []
    monkeypatch.setattr(cu._log, "warning",
                        lambda msg, *a: avisos.append(msg % a if a else msg))

    out = cu.listar_objetos_remotos("bucket", "v", Auth(), max_paginas=3)

    assert out  # devuelve lo que pudo ver
    assert avisos and "más de" in avisos[0]


# --------------------------------------------------------------------------
# 412: el objeto ya estaba en el bucket
# --------------------------------------------------------------------------

def test_upload_one_412_lanza_ya_existe(tmp_path, monkeypatch):
    """GCS corta con 412 (Rust lo devuelve en `saltados_412`): upload_one lanza YaExiste."""
    server = FakeGCS(ya_existen=("a.jpg",)).instalar(monkeypatch)

    root = _vuelo(tmp_path, {"a.jpg": b"z" * 100})
    item = cu.build_plan(root).items[0]

    with pytest.raises(cu.YaExiste):
        cu.upload_one(item, StaticProvider())
    assert len(server.llamadas) == 1


def test_upload_plan_cuenta_el_412_como_precondicion_no_como_fallo(tmp_path, monkeypatch):
    """Un fichero que GCS ya tenía no debe tumbar el plan ni contarse como error."""
    server = FakeGCS(ya_existen=("malo",)).instalar(monkeypatch)

    root = _vuelo(tmp_path, {"bueno.jpg": b"z" * 10, "malo.jpg": b"z" * 10})
    res = cu.upload_plan(cu.build_plan(root), StaticProvider(), concurrency=2)

    assert res.ok
    assert res.failed == []
    assert res.skipped_precondicion == 1
    assert res.uploaded == 1
    assert server.objects["bueno.jpg"] == b"z" * 10


def test_reconciliar_pide_url_con_sobrescribir_solo_para_los_que_resube(tmp_path, gcs):
    """`reconciliar` marca `sobrescribir=True` cuando el objeto YA existe (con
    otro tamaño); un fichero nuevo debe seguir pidiendo la URL protegida."""
    root = _vuelo(tmp_path, {"cambiado.jpg": b"a" * 500, "nuevo.jpg": b"b" * 300})
    plan = cu.build_plan(root, prefix="v")
    # "cambiado.jpg" ya está en el bucket con otro tamaño -> se fuerza resubida.
    remotos = {plan.items[0].remote: cu.RemoteObject(plan.items[0].remote, 999)}
    provider = ListingProvider(remotos)

    res = cu.upload_plan(plan, provider)

    assert res.uploaded == 2
    assert plan.items[0].remote in provider.sobrescritos
    assert plan.items[1].remote not in provider.sobrescritos
