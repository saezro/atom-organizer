"""Subida GCS en Rust (Task 6, fase ``subida``): contrato de ``subir_lote`` con un
``atom_rs`` FALSO (sin red, sin wheel). TDD: los tests marcados ``DEPENDE 6k``/``6l``
quedan en rojo hasta que ``cloud_upload.upload_plan``/``subida_resultado`` tengan la
rama Rust. Los de ``rust_bridge`` (6i) y el caso 1 (flag OFF) ya deben pasar."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from atom_core import cloud_upload as cu
from atom_core import rust_bridge
from atom_core import subida_resultado as sr
from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO
from atom_core.rust_bridge import RustNoDisponible

PREFIJO = "KL05/INSPECCIONES/TERMICA_MODULOS/2026/"
CLAVES_STATS = {
    "files_done", "files_total", "bytes_done", "bytes_total", "elapsed", "eta", "mbps",
    "files_per_s", "retries", "hilos_activos", "fichero_actual", "fallidas", "hilos_max",
    "medidas", "final",
}


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    monkeypatch.delenv("ATOM_RUST", raising=False)
    monkeypatch.delenv("ATOM_RUST_FASES", raising=False)


# ---------------------------------------------------------------- doble de atom_rs

class Falso:
    """``atom_rs`` falso: registra los argumentos y emite eventos programados.

    ``guion`` recibe los kwargs de la llamada y devuelve la lista de eventos; el
    último debe ser ``hecho`` (es lo que devuelve ``subir_lote``)."""

    def __init__(self, guion=None):
        self.llamadas: list[dict] = []
        self.guion = guion

    def subir_lote(self, items, prefijo, modo, url_fn, token_fn, recover_fn, callback,
                   should_stop, hilos_min=16, hilos_max=64, chunk_bytes=16_777_216,
                   simple_max_bytes=16_777_216, reintentos_hash=3):
        self.llamadas.append(dict(
            items=items, prefijo=prefijo, modo=modo, url_fn=url_fn, token_fn=token_fn,
            recover_fn=recover_fn, callback=callback, should_stop=should_stop,
            hilos_min=hilos_min, hilos_max=hilos_max, chunk_bytes=chunk_bytes,
            simple_max_bytes=simple_max_bytes, reintentos_hash=reintentos_hash))
        eventos = self.guion(self.llamadas[-1]) if self.guion else [hecho()]
        for ev in eventos:
            callback(ev)
        return eventos[-1]


def hecho(**kw) -> dict:
    base = dict(kind="hecho", subidos=0, saltados_412=0, bytes_enviados=0, elapsed=1.0,
                reintentos=0, cancelado=False, abortado=None, fallidos=[], md5={})
    base.update(kw)
    return base


def progreso(**kw) -> dict:
    base = dict(kind="progreso", bytes_done=10, bytes_total=30, files_done=1, files_total=3,
                mbps=8.0, eta=None, hilos_activos=16, hilos_max=16, fichero_actual="a.csv",
                fallidas=0, reintentos=0, pb_actual="General",
                medidas={"reintentos": {}})
    base.update(kw)
    return base


def instalar(monkeypatch, falso: Falso | None, fases="subida") -> Falso | None:
    if fases:
        monkeypatch.setenv("ATOM_RUST_FASES", fases)
    if falso is None:
        monkeypatch.setitem(sys.modules, "atom_rs", None)
        return None
    m = types.ModuleType("atom_rs")
    m.version_abi = lambda: rust_bridge.ABI_ESPERADA
    m.version = lambda: "9.9.9"
    m.subir_lote = falso.subir_lote
    monkeypatch.setitem(sys.modules, "atom_rs", m)
    return falso


# ---------------------------------------------------------------- proveedores / planes

class Auth:
    es_password = False
    es_google = True


class OAuthProv(cu.GcsOAuthProvider):
    solo_crear = False

    def __init__(self):
        super().__init__("bucket-x", Auth())
        self.cabeceras = 0
        self.recuperaciones = 0

    def headers(self):
        self.cabeceras += 1
        return {"Authorization": "Bearer TOK"}

    def recover_auth(self):
        self.recuperaciones += 1
        return True


class FirmadaProv(cu.SignedUrlProvider):
    def __init__(self):
        super().__init__("http://suite.invalid/firmar", "tok")

    def upload_url(self, remote, size, *, sobrescribir=False):
        return f"https://firmada.invalid/{remote}"


def _plan(tmp_path: Path, ficheros: dict[str, bytes], prefix="KL05/X") -> cu.UploadPlan:
    items = []
    for rel, datos in ficheros.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(datos)
        items.append(cu.UploadItem(local=p, remote=f"{prefix}/{rel}", size=len(datos)))
    return cu.UploadPlan(root=tmp_path, items=items, prefix=prefix)


ARBOL = {
    "CSVs/V1_meta.csv": b"meta",
    "TERMICA/PB1/V1/A_T.tiff": b"t" * 40,
    "TERMICA/PB1/V1/A_T.JPG": b"j" * 20,
}


def _no_red(monkeypatch):
    """Cualquier petición urllib o ``upload_file`` en la ruta Rust = fallo."""
    def _boom(*a, **k):
        pytest.fail("la ruta Rust no debe usar la red/upload_file de Python")
    monkeypatch.setattr(cu.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(cu, "upload_file", _boom)


# ---------------------------------------------------------------- 1 flag OFF

def test_flag_off_no_importa_atom_rs_y_usa_camino_python(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "atom_rs", None)  # si se importara, RustNoDisponible
    plan = _plan(tmp_path, ARBOL)
    llamados = []

    def _uf(item, provider, **k):
        llamados.append(item.remote)
        return "AA=="

    monkeypatch.setattr(cu, "upload_file", _uf)
    r = cu.upload_plan(plan, OAuthProv(), concurrency=2, remotos={},
                       manifest=cu.Manifest(tmp_path / "m.json"))
    assert sorted(llamados) == sorted(i.remote for i in plan.items)
    assert r.uploaded == 3 and not r.failed


# ---------------------------------------------------------------- 2 flag ON sin Rust válido
# DEPENDE 6k (rama Rust en upload_plan): hoy upload_plan ignora el flag.

def _contar_red(monkeypatch):
    peticiones = []
    monkeypatch.setattr(cu.urllib.request, "urlopen",
                        lambda *a, **k: peticiones.append(a) or pytest.fail("red tocada"))
    monkeypatch.setattr(cu, "upload_file", lambda *a, **k: peticiones.append(a) or pytest.fail("python"))
    return peticiones


def test_flag_on_sin_modulo_lanza_antes_de_subir(tmp_path, monkeypatch):
    instalar(monkeypatch, None)
    pet = _contar_red(monkeypatch)
    plan = _plan(tmp_path, ARBOL)
    with pytest.raises(RustNoDisponible, match="atom_rs"):
        cu.upload_plan(plan, OAuthProv(), remotos={}, manifest=cu.Manifest(tmp_path / "m.json"))
    assert pet == []


def test_flag_on_abi_mala_lanza_antes_de_subir(tmp_path, monkeypatch):
    f = instalar(monkeypatch, Falso())
    sys.modules["atom_rs"].version_abi = lambda: rust_bridge.ABI_ESPERADA - 1
    pet = _contar_red(monkeypatch)
    with pytest.raises(RustNoDisponible, match="ABI"):
        cu.upload_plan(_plan(tmp_path, ARBOL), OAuthProv(), remotos={},
                       manifest=cu.Manifest(tmp_path / "m.json"))
    assert pet == [] and f.llamadas == []


def test_flag_on_sin_simbolo_lanza_antes_de_subir(tmp_path, monkeypatch):
    instalar(monkeypatch, Falso())
    del sys.modules["atom_rs"].subir_lote
    pet = _contar_red(monkeypatch)
    with pytest.raises(RustNoDisponible, match="subir_lote"):
        cu.upload_plan(_plan(tmp_path, ARBOL), OAuthProv(), remotos={},
                       manifest=cu.Manifest(tmp_path / "m.json"))
    assert pet == []


# ---------------------------------------------------------------- 3 items entregados (DEPENDE 6k)

def test_items_solo_pendientes_y_modo_oauth(tmp_path, monkeypatch):
    f = instalar(monkeypatch, Falso())
    _no_red(monkeypatch)
    plan = _plan(tmp_path, ARBOL)
    ya = plan.items[2]  # A_T.JPG ya está en el bucket (mismo tamaño, sin md5 => hecho)
    remotos = {ya.remote: cu.RemoteObject(ya.remote, ya.size, "")}
    prov = OAuthProv()
    urg = {plan.items[1].remote}
    cu.upload_plan(plan, prov, remotos=remotos, manifest=cu.Manifest(tmp_path / "m.json"),
                   urgentes=urg)
    (ll,) = f.llamadas
    assert ll["modo"] == "oauth"
    assert ll["prefijo"] == plan.prefix or ll["prefijo"] == plan.prefix + "/"
    assert sorted(ll["items"]) == sorted([
        (str(plan.items[0].local), plan.items[0].remote, plan.items[0].size, False, False),
        (str(plan.items[1].local), plan.items[1].remote, plan.items[1].size, False, True),
    ])
    assert ll["token_fn"]() == "Bearer TOK"
    assert ll["recover_fn"]() is True and prov.recuperaciones == 1
    assert "bucket-x" in ll["url_fn"](plan.items[0].remote, 4, False)
    assert ll["should_stop"]() is False


def test_modo_firmada_sin_token_fn(tmp_path, monkeypatch):
    f = instalar(monkeypatch, Falso())
    _no_red(monkeypatch)
    plan = _plan(tmp_path, ARBOL)
    cu.upload_plan(plan, FirmadaProv(), remotos={}, manifest=cu.Manifest(tmp_path / "m.json"))
    (ll,) = f.llamadas
    assert ll["modo"] == "firmada" and ll["token_fn"] is None
    assert len(ll["items"]) == 3
    assert ll["url_fn"](plan.items[0].remote, 4, False).startswith("https://firmada.invalid/")


def test_proveedor_resultado_es_modo_oauth(tmp_path, monkeypatch):
    f = instalar(monkeypatch, Falso())
    _no_red(monkeypatch)
    prov = sr.ProveedorResultado(Auth(), 7)
    cu.upload_plan(_plan(tmp_path, ARBOL), prov, remotos={}, manifest=cu.Manifest(tmp_path / "m.json"))
    assert f.llamadas[0]["modo"] == "oauth"


# ---------------------------------------------------------------- 4 progreso -> on_stats (DEPENDE 6k)

def test_progreso_llega_a_on_stats_con_claves_de_stats(tmp_path, monkeypatch):
    instalar(monkeypatch, Falso(lambda ll: [progreso(), hecho(subidos=3)]))
    _no_red(monkeypatch)
    fotos = []
    cu.upload_plan(_plan(tmp_path, ARBOL), OAuthProv(), remotos={},
                   manifest=cu.Manifest(tmp_path / "m.json"), on_stats=fotos.append)
    assert fotos
    for foto in fotos:
        assert CLAVES_STATS <= set(foto), CLAVES_STATS - set(foto)
    f0 = fotos[0]
    assert (f0["bytes_done"], f0["bytes_total"], f0["files_done"], f0["files_total"]) == (10, 30, 1, 3)
    assert f0["hilos_activos"] == 16 and f0["hilos_max"] == 16 and f0["eta"] is None
    assert fotos[-1]["final"] is True


# ---------------------------------------------------------------- 5 pb_listo (DEPENDE 6k)

def test_pb_listo_linea_y_on_evento(tmp_path, monkeypatch):
    ev_pb = dict(kind="pb_listo", pb="PB3", ficheros=12, bytes=1234)
    instalar(monkeypatch, Falso(lambda ll: [ev_pb, hecho()]))
    _no_red(monkeypatch)
    lineas, eventos = [], []
    cu.upload_plan(_plan(tmp_path, ARBOL), OAuthProv(), remotos={},
                   manifest=cu.Manifest(tmp_path / "m.json"),
                   on_progress=lineas.append, on_evento=eventos.append)
    assert any("PB 3 listo para analizar" in l for l in lineas), lineas
    assert ev_pb in eventos


def test_pb_general_listo_no_dice_pb_general(tmp_path, monkeypatch):
    ev = dict(kind="pb_listo", pb="General", ficheros=2, bytes=5)
    instalar(monkeypatch, Falso(lambda ll: [ev, hecho()]))
    _no_red(monkeypatch)
    eventos = []
    cu.upload_plan(_plan(tmp_path, ARBOL), OAuthProv(), remotos={},
                   manifest=cu.Manifest(tmp_path / "m.json"), on_evento=eventos.append)
    assert ev in eventos


# ---------------------------------------------------------------- 6 hecho.md5 / fallidos (DEPENDE 6k)

def test_md5_verificado_marca_manifiesto_y_uploaded(tmp_path, monkeypatch):
    plan = _plan(tmp_path, ARBOL)
    a, b, c = plan.items
    md5 = {a.remote: "AAAA", b.remote: "BBBB"}  # c NO verificado
    instalar(monkeypatch, Falso(lambda ll: [hecho(
        subidos=2, bytes_enviados=44, md5=md5,
        fallidos=[(c.remote, "hash: md5 distinto tras 3 intentos")])]))
    _no_red(monkeypatch)
    man = cu.Manifest(tmp_path / "m.json")
    r = cu.upload_plan(plan, OAuthProv(), remotos={}, manifest=man)
    assert r.uploaded == 2 and r.bytes_sent == 44
    assert man.is_done(a) and man.md5_de(a) == "AAAA"
    assert man.is_done(b) and man.md5_de(b) == "BBBB"
    assert not man.is_done(c)  # jamás marcado sin hash verificado
    assert r.failed == [(c.remote, "hash: md5 distinto tras 3 intentos")]


def test_error_hash_visible_en_failed_y_no_en_manifiesto(tmp_path, monkeypatch):
    plan = _plan(tmp_path, ARBOL)
    a = plan.items[0]
    err = dict(kind="error", remote=a.remote, tipo="hash", motivo="crc32c no coincide")
    instalar(monkeypatch, Falso(lambda ll: [err, hecho(fallidos=[(a.remote, "hash: crc32c no coincide")])]))
    _no_red(monkeypatch)
    man = cu.Manifest(tmp_path / "m.json")
    eventos = []
    r = cu.upload_plan(plan, OAuthProv(), remotos={}, manifest=man, on_evento=eventos.append)
    assert any("crc32c no coincide" in m for _, m in r.failed)
    assert not man.is_done(a)
    assert err in eventos


def test_412_cuenta_como_skipped_precondicion(tmp_path, monkeypatch):
    instalar(monkeypatch, Falso(lambda ll: [hecho(saltados_412=2)]))
    _no_red(monkeypatch)
    r = cu.upload_plan(_plan(tmp_path, ARBOL), OAuthProv(), remotos={},
                       manifest=cu.Manifest(tmp_path / "m.json"))
    assert r.skipped_precondicion == 2


# ---------------------------------------------------------------- 7 cancelación (DEPENDE 6k)

def test_should_stop_llega_a_rust_y_cancelado_va_a_failed(tmp_path, monkeypatch):
    plan = _plan(tmp_path, ARBOL)
    pendientes = [i.remote for i in plan.items]

    def guion(ll):
        assert ll["should_stop"]() is True  # el flag del llamante llega a Rust
        return [hecho(cancelado=True, fallidos=[(r, "cancelado") for r in pendientes])]

    instalar(monkeypatch, Falso(guion))
    _no_red(monkeypatch)
    man = cu.Manifest(tmp_path / "m.json")
    r = cu.upload_plan(plan, OAuthProv(), remotos={}, manifest=man, should_stop=lambda: True)
    assert sorted(r.failed) == sorted((x, "cancelado") for x in pendientes)
    assert r.uploaded == 0 and not any(man.is_done(i) for i in plan.items)


# ---------------------------------------------------------------- 8 abortado (DEPENDE 6k)

def test_abortado_va_a_result_abortado(tmp_path, monkeypatch):
    msg = "403 persistente en 3 ficheros seguidos"
    instalar(monkeypatch, Falso(lambda ll: [hecho(abortado=msg)]))
    _no_red(monkeypatch)
    lineas = []
    r = cu.upload_plan(_plan(tmp_path, ARBOL), OAuthProv(), remotos={},
                       manifest=cu.Manifest(tmp_path / "m.json"), on_progress=lineas.append)
    assert r.abortado and msg in r.abortado


# ---------------------------------------------------------------- 9 subir_resultado (DEPENDE 6l)

SALIDA = {
    "CSVs/V1_meta.csv": b"meta",
    "ESTADILLOS/e.xlsx": b"est",
    "TERMICA/PB1/V1/A_T.tiff": b"t" * 100,
    "TERMICA/PB1/V1/A_T.JPG": b"j" * 60,
    "TERMICA/PB2/V1/B_T.tiff": b"u" * 90,
    "TERMICA/PB2/V1/B_T.JPG": b"k" * 50,
}


def _arbol(raiz: Path):
    m = raiz / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_bytes(b"x")
    for rel, datos in SALIDA.items():
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(datos)


class ProvResultado(OAuthProv):
    def __init__(self, remotos):
        super().__init__()
        self._remotos = remotos

    def prefijo_destino(self):
        return PREFIJO

    def listar_remotos(self, prefix):
        return dict(self._remotos)


def _remoto(rel: str, datos: bytes):
    import base64, hashlib
    nombre = PREFIJO + rel
    return nombre, cu.RemoteObject(nombre, len(datos), base64.b64encode(hashlib.md5(datos).digest()).decode())


def test_subir_resultado_una_sola_llamada_con_urgente_correcto(tmp_path, monkeypatch):
    _arbol(tmp_path)
    monkeypatch.setenv("ATOM_RUST_FASES", "subida")
    llamadas = []

    def subir(plan, prov, *, urgentes=None, on_evento=None, **k):
        llamadas.append(dict(items=[i.remote for i in plan.items], urgentes=set(urgentes or ()),
                             on_evento=on_evento))
        return cu.UploadResult()

    prov = ProvResultado({})
    sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, None, proveedor=prov, subir=subir)
    assert len(llamadas) == 1  # urgentes + resto en UNA llamada
    ll = llamadas[0]
    assert len(ll["items"]) == len(SALIDA)
    esperadas = {PREFIJO + r for r in ("CSVs/V1_meta.csv", "ESTADILLOS/e.xlsx",
                                       "TERMICA/PB1/V1/A_T.tiff", "TERMICA/PB2/V1/B_T.tiff")}
    assert ll["urgentes"] == esperadas
    assert callable(ll["on_evento"])


def test_subir_resultado_pb_sin_pendientes_emite_pb_listo_desde_python(tmp_path, monkeypatch):
    _arbol(tmp_path)
    monkeypatch.setenv("ATOM_RUST_FASES", "subida")
    # PB2 ya está entero en el bucket: no hay nada que subir de él.
    remotos = dict(_remoto(r, SALIDA[r]) for r in SALIDA if "/PB2/" in r)
    eventos, llamadas = [], []

    def subir(plan, prov, *, urgentes=None, on_evento=None, **k):
        llamadas.append([i.remote for i in plan.items])
        return cu.UploadResult()

    sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, None, proveedor=ProvResultado(remotos),
                       subir=subir, on_evento=eventos.append)
    assert all("/PB2/" not in r for r in llamadas[0])
    pbs = [e["pb"] for e in eventos if e.get("kind") == "pb_listo"]
    assert pbs == ["PB2"]  # PB1 lo anunciará Rust al verificar su último fichero


def test_subir_resultado_flag_off_camino_actual_dos_grupos(tmp_path, monkeypatch):
    _arbol(tmp_path)
    monkeypatch.setitem(sys.modules, "atom_rs", None)
    llamadas = []

    def subir(plan, prov, **k):
        assert "urgentes" not in k and "on_evento" not in k
        llamadas.append(len(plan.items))
        return cu.UploadResult()

    sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, None, proveedor=ProvResultado({}), subir=subir)
    assert llamadas == [4, 2]  # urgentes y luego resto, como hoy


# ---------------------------------------------------------------- T4.1: objeto suelto (app_webview)

def test_subir_objeto_fichero_delega_en_subir_lote_con_1_item(tmp_path, monkeypatch):
    import app_webview as aw

    f = tmp_path / "est.csv"
    f.write_bytes(b"datos")
    falso = instalar(monkeypatch, Falso(lambda kw: [
        hecho(subidos=1, bytes_enviados=5, md5={"KL05/ESTADILLOS/est.csv": "MD5B64"})]))
    monkeypatch.setattr(cu, "upload_file", lambda *a, **k: pytest.fail("python"))
    monkeypatch.setattr(cu.GcsOAuthProvider, "headers",
                        lambda self: {"Authorization": "Bearer TOK"})
    api = aw.Api.__new__(aw.Api)
    monkeypatch.setattr(api, "_get_auth", lambda: Auth(), raising=False)
    api._subir_objeto_fichero("KL05/ESTADILLOS/est.csv", str(f), prefix="KL05")
    (ll,) = falso.llamadas
    assert ll["items"] == [(str(f), "KL05/ESTADILLOS/est.csv", 5, False, False)]
    assert ll["modo"] == "oauth" and ll["prefijo"] == "KL05/ESTADILLOS/"
    assert ll["token_fn"]() == "Bearer TOK" and callable(ll["recover_fn"])


def test_upload_one_traduce_errores(tmp_path, monkeypatch):
    f = tmp_path / "a.csv"
    f.write_bytes(b"x")
    item = cu.UploadItem(local=f, remote="P/a.csv", size=1)
    instalar(monkeypatch, Falso(lambda kw: [hecho(saltados_412=1)]))
    with pytest.raises(cu.YaExiste):
        cu.upload_one(item, OAuthProv())
    instalar(monkeypatch, Falso(lambda kw: [hecho(fallidos=[["P/a.csv", "boom"]])]))
    with pytest.raises(RuntimeError, match="boom"):
        cu.upload_one(item, OAuthProv())
    instalar(monkeypatch, Falso(lambda kw: [hecho(abortado="403 persistente")]))
    with pytest.raises(cu.GcsRechazado):
        cu.upload_one(item, OAuthProv())


def test_upload_one_fase_off_va_por_upload_file(tmp_path, monkeypatch):
    f = tmp_path / "a.csv"
    f.write_bytes(b"x")
    item = cu.UploadItem(local=f, remote="P/a.csv", size=1)
    falso = instalar(monkeypatch, Falso(), fases=None)
    llamadas = []
    monkeypatch.setattr(cu, "upload_file", lambda i, p, **k: llamadas.append(i) or "MD5")
    assert cu.upload_one(item, OAuthProv()) == "MD5"
    assert llamadas == [item] and falso.llamadas == []
