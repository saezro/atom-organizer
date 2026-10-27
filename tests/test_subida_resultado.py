"""`atom_core.subida_resultado`: selección por modo, partición, subida y disparo
automático. Todo con ficheros temporales y el GCS falso de `test_cloud_upload`:
nunca red real."""
from __future__ import annotations

from pathlib import Path

import pytest

from atom_core import cloud_upload as cu
from atom_core import subida_resultado as sr
from atom_core.manifiesto import Manifiesto, NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO

PREFIJO = "KL05/INSPECCIONES/TERMICA_MODULOS/2026/"


def _arbol(raiz: Path, ficheros: dict[str, bytes]) -> Path:
    for rel, datos in ficheros.items():
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(datos)
    return raiz


SALIDA_TIPICA = {
    "TERMICA/PB1/V1/A_T.tiff": b"t1",
    "TERMICA/PB1/V1/A_T.JPG": b"t2",
    "RGB/PB1/V1/A_W_CROP.JPG": b"c1",
    "RGB/PB1/V1/A_W.JPG": b"w1",
    "CSVs/V1_meta.csv": b"m",
    "CSVs/V1_location.csv": b"l",
    "CSVs/_criterio/V1_criterio.csv": b"k",
    "CSVs/otro.csv": b"o",
    "ESTADILLOS/estadillo.xlsx": b"e",
    "INDICE_KL05.xlsx": b"i",
    "LOGS/run.log": b"log",
    "RGB_Extra/PB1/V1/X.JPG": b"x",
    "Thumbs.db": b"basura",
}


@pytest.mark.parametrize("rel,esperado", [
    ("TERMICA/PB1/V1/A_T.tiff", sr.URGENTE),
    ("TERMICA/PB1/V1/A_T.TIFF", sr.URGENTE),
    ("RGB/PB1/V1/A_W_CROP.JPG", sr.URGENTE),
    ("CSVs/V1_meta.csv", sr.URGENTE),
    ("CSVs/V1_location.csv", sr.URGENTE),
    ("ESTADILLOS/estadillo.xlsx", sr.URGENTE),
    ("TERMICA/PB1/V1/A_T.JPG", sr.NORMAL),
    ("RGB/PB1/V1/A_W.JPG", sr.NORMAL),
    ("INDICE_KL05.xlsx", sr.NORMAL),
    ("INDICE_KL05_20261007_101500.xlsx", sr.NORMAL),
    ("CSVs/_criterio/V1_criterio.csv", sr.NORMAL),
    ("CSVs/otro.csv", None),
    ("CSVs/sub/V1_meta.csv", None),
    ("ESTADILLOS/sub/x.xlsx", None),
    ("RGB_Extra/PB1/V1/X.JPG", None),
    ("RGB/PB1/V1/A_Z.JPG", None),
    ("TERMICA/PB1/V1/A_T.png", None),
    ("RGB/A_W.JPG", sr.NORMAL),
    ("suelto.txt", None),
    ("LOGS/run.log", None),
    (".organizado/manifiesto.db", None),
])
def test_clasificar(rel, esperado):
    assert sr.clasificar(rel) == esperado


@pytest.mark.parametrize("rel,esperado", [
    ("CSVs/V1_meta.csv", True),
    ("CSVs/_criterio/V1_criterio.csv", True),
    ("ESTADILLOS/estadillo.xlsx", True),
    ("INDICE_KL05.xlsx", True),
    ("TERMICA/PB1/V1/A_T.tiff", False),
    ("RGB/PB1/V1/A_W_CROP.JPG", False),
    ("SIN_ORDENAR/RGB/B_W.JPG", False),
    ("MI_INDICE_KL05.xlsx", False),
])
def test_es_sobrescribible(rel, esperado):
    assert sr.es_sobrescribible(rel) is esperado


def _manifiesto_con_sin_ordenar(raiz: Path) -> None:
    """Dos ejecuciones. Sin asignar y hechas (se suben): A (1ª, sigue igual) y B+crop (2ª).
    No se suben: C (asignada), D (fallida), E (reasignada, unassigned=0) y F (pendiente)."""
    (raiz / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True, exist_ok=True)
    m = Manifiesto(raiz / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO)
    m.crear_esquema()
    e1 = m.abrir_ejecucion("/origen1", "3.4.115")
    e2 = m.abrir_ejecucion("/origen2", "3.4.115")
    con = m._conexion()
    filas = [
        ("a", e1, "SIN_ORDENAR/RGB/A_W.JPG", None, 1, "hecho"),
        ("b", e2, "SIN_ORDENAR/RGB/B_W.JPG", "SIN_ORDENAR/RGB/B_W_CROP.JPG", 1, "hecho"),
        ("c", e2, "RGB/PB1/V1/C_W.JPG", None, 0, "hecho"),
        ("d", e2, "SIN_ORDENAR/RGB/D_W.JPG", None, 1, "fallido"),
        ("e", e1, "SIN_ORDENAR/RGB/E_W.JPG", None, 0, "hecho"),
        ("f", e1, "SIN_ORDENAR/RGB/F_W.JPG", None, 1, "pendiente"),
    ]
    with con:
        for clave, ej, orig, crop, unas, estado in filas:
            con.execute(
                "INSERT INTO imagenes (ruta_origen, clave, tipo, ruta_salida_original, "
                "ruta_salida_crop, unassigned, estado, ejecucion_id) VALUES (?,?,?,?,?,?,?,?)",
                (f"/o/{clave}", clave, "RGB", str(raiz / orig),
                 str(raiz / crop) if crop else None, unas, estado, ej))
    m.cerrar()


def test_sin_ordenar_de_cualquier_ejecucion_si_siguen_sin_asignar(tmp_path):
    salida = tmp_path / "salida"
    _arbol(salida, {f"SIN_ORDENAR/RGB/{n}": b"x" for n in (
        "A_W.JPG", "B_W.JPG", "B_W_CROP.JPG", "D_W.JPG", "E_W.JPG", "F_W.JPG")})
    _manifiesto_con_sin_ordenar(salida)
    rels, avisos = sr.sin_ordenar_ultima_ejecucion(salida)
    # A (1ª ejecución, sigue sin asignar) SÍ; E reasignada, F pendiente y D fallida NO.
    assert rels == {"SIN_ORDENAR/RGB/A_W.JPG", "SIN_ORDENAR/RGB/B_W.JPG",
                    "SIN_ORDENAR/RGB/B_W_CROP.JPG"}
    assert avisos == []


def test_sin_ordenar_con_filas_fuera_del_destino_avisa(tmp_path):
    origen = tmp_path / "antes"
    _arbol(origen, {"SIN_ORDENAR/RGB/A_W.JPG": b"a"})
    _manifiesto_con_sin_ordenar(origen)
    movido = tmp_path / "despues"
    origen.rename(movido)  # el manifiesto sigue apuntando a `antes/`
    rels, avisos = sr.sin_ordenar_ultima_ejecucion(movido)
    assert rels == set()
    assert len(avisos) == 1 and "destino" in avisos[0].lower()


def test_sin_ordenar_no_crea_shm_ni_wal_junto_al_manifiesto(tmp_path):
    salida = tmp_path / "salida"
    _arbol(salida, {"SIN_ORDENAR/RGB/A_W.JPG": b"x"})
    _manifiesto_con_sin_ordenar(salida)
    db = salida / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO
    sr.sin_ordenar_ultima_ejecucion(salida)
    assert not Path(f"{db}-shm").exists()
    assert not Path(f"{db}-wal").exists()


def test_sin_manifiesto_avisa_y_no_selecciona_nada(tmp_path):
    rels, avisos = sr.sin_ordenar_ultima_ejecucion(tmp_path)
    assert rels == set()
    assert len(avisos) == 1 and "manifiesto" in avisos[0].lower()


def test_seleccionar_reparte_urgentes_y_resto_y_cuenta_sin_clasificar(tmp_path):
    # El conftest crea `tmp_path/Logs-subidas/subidas.log`: la salida va en una subcarpeta.
    salida = tmp_path / "salida"
    _arbol(salida, {**SALIDA_TIPICA, "SIN_ORDENAR/RGB/A_W.JPG": b"a",
                    "SIN_ORDENAR/RGB/B_W.JPG": b"b", "SIN_ORDENAR/RGB/B_W_CROP.JPG": b"bc",
                    "SIN_ORDENAR/RGB/E_W.JPG": b"e"})
    _manifiesto_con_sin_ordenar(salida)
    sel = sr.seleccionar(salida, PREFIJO)
    rem = lambda items: sorted(i.remote.removeprefix(PREFIJO) for i in items)  # noqa: E731
    assert rem(sel.urgentes) == [
        "CSVs/V1_location.csv", "CSVs/V1_meta.csv", "ESTADILLOS/estadillo.xlsx",
        "RGB/PB1/V1/A_W_CROP.JPG", "TERMICA/PB1/V1/A_T.tiff"]
    assert rem(sel.resto) == [
        "CSVs/_criterio/V1_criterio.csv", "INDICE_KL05.xlsx", "RGB/PB1/V1/A_W.JPG",
        "SIN_ORDENAR/RGB/A_W.JPG", "SIN_ORDENAR/RGB/B_W.JPG", "SIN_ORDENAR/RGB/B_W_CROP.JPG", "TERMICA/PB1/V1/A_T.JPG"]
    # CSVs/otro.csv y RGB_Extra/...: ni urgentes ni resto, pero contados.
    assert sel.sin_clasificar == 2
    assert any("sin clasificar" in a.lower() for a in sel.avisos)
    # Nunca .organizado, LOGS ni basura del sistema.
    todos = {i.remote for i in sel.urgentes + sel.resto}
    assert not any(".organizado" in r or "/LOGS/" in r or "Thumbs" in r for r in todos)
    # SIN_ORDENAR/RGB/E_W.JPG (reasignada en el manifiesto) NO se sube.
    assert PREFIJO + "SIN_ORDENAR/RGB/E_W.JPG" not in todos


def test_seleccionar_prefijo_sin_barra_final_y_tamanos(tmp_path):
    salida = tmp_path / "salida"
    _arbol(salida, {"TERMICA/PB1/V1/A_T.tiff": b"12345"})
    sel = sr.seleccionar(salida, PREFIJO.rstrip("/"))
    assert [(i.remote, i.size) for i in sel.urgentes] == [(PREFIJO + "TERMICA/PB1/V1/A_T.tiff", 5)]


def test_seleccionar_sin_manifiesto_avisa_pero_selecciona_lo_demas(tmp_path):
    salida = tmp_path / "salida"
    _arbol(salida, SALIDA_TIPICA)
    sel = sr.seleccionar(salida, PREFIJO)
    assert len(sel.urgentes) == 5
    assert any("manifiesto" in a.lower() for a in sel.avisos)


# ---- Parte 2: partición, proveedor y subida ------------------------------------
from tests.test_cloud_upload import FakeGCS, StaticProvider  # noqa: E402,F401


@pytest.fixture
def gcs(monkeypatch):
    server = FakeGCS()
    monkeypatch.setattr(cu.urllib.request, "urlopen", server.urlopen)
    monkeypatch.setattr(cu, "CHUNK_SIZE", 1024)
    monkeypatch.setattr(cu.time, "sleep", lambda _s: None)
    return server


_SIN = object()


class ProveedorFalso(StaticProvider):
    """Firma URLs en memoria, no sobrescribe-protege (como el token con admin), lista lo que le digan."""
    solo_crear = False

    def __init__(self, remotos=_SIN):
        super().__init__()
        self.remotos = {} if remotos is _SIN else remotos

    def prefijo_destino(self):
        return PREFIJO

    refleja_subidas = True   # el «bucket» incluye lo que se ha subido (listado fresco)
    fallar_listado_tras_subir = False

    def listar_remotos(self, prefix):
        if self.remotos is None:
            return None
        if self.calls and self.fallar_listado_tras_subir:
            raise OSError("red caida")
        res = dict(self.remotos)
        if self.refleja_subidas:
            for remote, size in self.calls:
                res[remote] = cu.RemoteObject(remote, size, "")
        return res


def _remoto(rel: str, datos: bytes) -> tuple[str, cu.RemoteObject]:
    import base64
    import hashlib
    nombre = PREFIJO + rel
    md5 = base64.b64encode(hashlib.md5(datos).digest()).decode()
    return nombre, cu.RemoteObject(nombre, len(datos), md5)


def _manifest(tmp_path):
    (tmp_path / ".organizado").mkdir(exist_ok=True)
    return cu.Manifest(tmp_path / ".organizado" / "subida_resultado.json")


def test_particionar_casos(tmp_path):
    _arbol(tmp_path, {
        "TERMICA/PB1/V1/N_T.tiff": b"nuevo",
        "TERMICA/PB1/V1/I_T.tiff": b"igual",
        "TERMICA/PB1/V1/C_T.tiff": b"cambiado-local",
        "CSVs/V1_meta.csv": b"csv-nuevo-contenido",
        "CSVs/V1_location.csv": b"loc",
        "INDICE_KL05.xlsx": b"indice-v2",
    })
    sel = sr.seleccionar(tmp_path, PREFIJO)
    items = sel.urgentes + sel.resto
    remotos = dict([
        _remoto("TERMICA/PB1/V1/I_T.tiff", b"igual"),
        _remoto("TERMICA/PB1/V1/C_T.tiff", b"otro-contenido-en-bucket"),
        _remoto("CSVs/V1_meta.csv", b"csv-viejo"),
        _remoto("CSVs/V1_location.csv", b"loc"),
        _remoto("INDICE_KL05.xlsx", b"indice-v1"),
    ])
    p = sr.particionar(items, remotos, _manifest(tmp_path), PREFIJO)
    rel = lambda xs: sorted(i.remote.removeprefix(PREFIJO) for i in xs)  # noqa: E731
    assert rel(p.pendientes) == ["CSVs/V1_meta.csv", "INDICE_KL05.xlsx", "TERMICA/PB1/V1/N_T.tiff"]
    assert rel(p.hechos) == ["CSVs/V1_location.csv", "TERMICA/PB1/V1/I_T.tiff"]
    assert [c[0].removeprefix(PREFIJO) for c in p.conflictos] == ["TERMICA/PB1/V1/C_T.tiff"]
    assert "otro contenido" in p.conflictos[0][1]
    # Solo las sobrescribibles que difieren llevan `sobrescribir=True` (sin ifGenerationMatch=0).
    marcados = {i.remote.removeprefix(PREFIJO): i.sobrescribir for i in p.pendientes}
    assert marcados == {"CSVs/V1_meta.csv": True, "INDICE_KL05.xlsx": True, "TERMICA/PB1/V1/N_T.tiff": False}


def test_particionar_usa_manifiesto_para_no_releer(tmp_path, monkeypatch):
    _arbol(tmp_path, {"TERMICA/PB1/V1/I_T.tiff": b"igual"})
    sel = sr.seleccionar(tmp_path, PREFIJO)
    nombre, ro = _remoto("TERMICA/PB1/V1/I_T.tiff", b"igual")
    m = _manifest(tmp_path)
    m.mark(sel.urgentes[0], ro.md5)
    monkeypatch.setattr(cu, "_file_md5_b64", lambda _p: pytest.fail("no debe releer el fichero"))
    p = sr.particionar(sel.urgentes, {nombre: ro}, m, PREFIJO)
    assert len(p.hechos) == 1 and not p.pendientes and not p.conflictos


def test_particionar_tamano_distinto_en_imagen_es_conflicto_sin_hashear(tmp_path, monkeypatch):
    _arbol(tmp_path, {"RGB/PB1/V1/A_W_CROP.JPG": b"12345"})
    sel = sr.seleccionar(tmp_path, PREFIJO)
    nombre, ro = _remoto("RGB/PB1/V1/A_W_CROP.JPG", b"123")
    monkeypatch.setattr(cu, "_file_md5_b64", lambda _p: pytest.fail("tamaño distinto: no hace falta hash"))
    p = sr.particionar(sel.urgentes, {nombre: ro}, _manifest(tmp_path), PREFIJO)
    assert len(p.conflictos) == 1 and not p.pendientes


SALIDA_PEQUENA = {
    "TERMICA/PB1/V1/A_T.tiff": b"t" * 100,
    "RGB/PB1/V1/A_W_CROP.JPG": b"c" * 80,
    "CSVs/V1_meta.csv": b"meta",
    "CSVs/V1_location.csv": b"loc",
    "ESTADILLOS/e.xlsx": b"est",
    "TERMICA/PB1/V1/A_T.JPG": b"j" * 60,
    "RGB/PB1/V1/A_W.JPG": b"w" * 90,
    "INDICE_KL05.xlsx": b"indice",
}


def test_subir_urgencia_sube_solo_urgentes_y_no_pone_ifgeneration_en_sobrescribibles(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = ProveedorFalso()
    estados = []
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov,
                           concurrency=1, on_estado=estados.append)
    subidos = sorted(c[0].removeprefix(PREFIJO) for c in prov.calls)
    assert subidos == ["CSVs/V1_location.csv", "CSVs/V1_meta.csv", "ESTADILLOS/e.xlsx",
                       "RGB/PB1/V1/A_W_CROP.JPG", "TERMICA/PB1/V1/A_T.tiff"]
    assert r.ok and not r.cancelado
    assert (r.urgentes.hechos, r.urgentes.total) == (5, 5)
    assert (r.resto.hechos, r.resto.total) == (0, 3)
    assert estados and estados[-1]["urgentes"] == {"hechos": 5, "total": 5}
    assert estados[-1]["resto"] == {"hechos": 0, "total": 3}


def test_subir_normal_urgentes_antes_que_resto_y_segunda_pasada_no_resube(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = ProveedorFalso()
    r1 = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=prov, concurrency=1)
    orden = [c[0].removeprefix(PREFIJO) for c in prov.calls]
    urg = {"TERMICA/PB1/V1/A_T.tiff", "RGB/PB1/V1/A_W_CROP.JPG", "CSVs/V1_meta.csv", "CSVs/V1_location.csv", "ESTADILLOS/e.xlsx"}
    assert set(orden[:5]) == urg and set(orden[5:]) == {"TERMICA/PB1/V1/A_T.JPG", "RGB/PB1/V1/A_W.JPG", "INDICE_KL05.xlsx"}
    assert r1.ok and (r1.resto.hechos, r1.resto.total) == (3, 3)
    # Segunda pasada: el bucket ya lo tiene todo -> no se abre ninguna sesión nueva.
    prov2 = ProveedorFalso(remotos=dict(_remoto(rel, d) for rel, d in SALIDA_PEQUENA.items()))
    r2 = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=prov2, concurrency=1)
    assert prov2.calls == [] and r2.ok
    assert (r2.urgentes.hechos, r2.resto.hechos) == (5, 3)


def test_subir_tras_urgencia_normal_solo_completa_el_resto(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    urgentes = {k: v for k, v in SALIDA_PEQUENA.items()
                if k in ("TERMICA/PB1/V1/A_T.tiff", "RGB/PB1/V1/A_W_CROP.JPG", "CSVs/V1_meta.csv", "CSVs/V1_location.csv", "ESTADILLOS/e.xlsx")}
    prov = ProveedorFalso(remotos=dict(_remoto(rel, d) for rel, d in urgentes.items()))
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=prov, concurrency=1)
    assert sorted(c[0].removeprefix(PREFIJO) for c in prov.calls) == [
        "INDICE_KL05.xlsx", "RGB/PB1/V1/A_W.JPG", "TERMICA/PB1/V1/A_T.JPG"]
    assert r.ok


def test_subir_csv_cambiado_se_sobrescribe_y_la_imagen_distinta_es_conflicto(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    remotos = dict(_remoto(rel, d) for rel, d in SALIDA_PEQUENA.items())
    n, ro = _remoto("CSVs/V1_meta.csv", b"meta-antiguo")
    remotos[n] = ro
    n, ro = _remoto("TERMICA/PB1/V1/A_T.tiff", b"x" * 100)  # mismo tamaño, otro contenido
    remotos[n] = ro
    prov = ProveedorFalso(remotos=remotos)
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov, concurrency=1)
    assert [c[0].removeprefix(PREFIJO) for c in prov.calls] == ["CSVs/V1_meta.csv"]
    assert [s.removeprefix(PREFIJO) for s in prov.sobrescritos] == ["CSVs/V1_meta.csv"]
    assert [c[0].removeprefix(PREFIJO) for c in r.conflictos] == ["TERMICA/PB1/V1/A_T.tiff"]
    assert not r.ok  # un conflicto deja la subida «con incidencias»


def test_urgentes_con_fallidas_no_intentan_el_resto(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    llamadas = []

    def subir(plan, provider, **kw):
        llamadas.append([i.remote.removeprefix(PREFIJO) for i in plan.items])
        return cu.UploadResult(uploaded=0, failed=[(plan.items[0].remote, "boom")])

    r = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=ProveedorFalso(), subir=subir)
    assert len(llamadas) == 1
    assert r.fallidas == [(PREFIJO + llamadas[0][0], "boom")] or len(r.fallidas) == 1
    assert not r.ok


def test_cancelar_no_sube_nada_y_marca_cancelado(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = ProveedorFalso()
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=prov, should_stop=lambda: True,
                           subir=lambda *a, **k: pytest.fail("no debe subir"))
    assert r.cancelado and not r.ok and prov.calls == []


def test_simular_no_sube_y_cuenta_pendientes(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=ProveedorFalso(), simular=True,
                           subir=lambda *a, **k: pytest.fail("no debe subir"))
    assert r.simulado and (r.urgentes.pendientes, r.resto.pendientes) == (5, 3)


def test_listar_remotos_none_es_error_visible(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = ProveedorFalso(remotos=None)
    with pytest.raises(sr.SubidaResultadoError, match="contenido del bucket"):
        sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov)


def test_modo_desconocido_y_carpeta_inexistente(tmp_path):
    with pytest.raises(ValueError):
        sr.subir_resultado(tmp_path, 7, "rapido", auth=None, proveedor=ProveedorFalso())
    with pytest.raises(sr.SubidaResultadoError, match="no existe"):
        sr.subir_resultado(tmp_path / "nada", 7, sr.MODO_URGENCIA, auth=None, proveedor=ProveedorFalso())


def test_proveedor_exige_modo_password():
    class AuthGoogle:
        es_password = False
    with pytest.raises(sr.SubidaResultadoError, match="usuario y contraseña"):
        sr.ProveedorResultado(AuthGoogle(), 7)


def test_proveedor_acepta_google_y_rechaza_pi_con_motivo():
    class AuthG:
        es_password = False
        es_google = True
    assert sr.ProveedorResultado(AuthG(), 7).solo_crear is False

    class AuthPi:
        es_password = False
        es_google = False
        es_broker = True
    with pytest.raises(sr.SubidaResultadoError, match="Pi/broker"):
        sr.ProveedorResultado(AuthPi(), 7)


def test_inspeccionar_destino_lista_y_marca_sobrescribibles(tmp_path):
    _arbol(tmp_path, {"CSVs/a_meta.csv": b"x", "ESTADILLOS/e.xlsx": b"y", "INDICE_1.xlsx": b"z",
                      "TERMICA/a_T.tiff": b"t"})
    remotos = dict([_remoto("CSVs/a_meta.csv", b"viejo"), _remoto("TERMICA/a_T.tiff", b"t"),
                    _remoto("OTRO/x.bin", b"1")])
    r = sr.inspeccionar_destino(tmp_path, 7, None, proveedor=ProveedorFalso(remotos))
    assert r["ok"] and r["existe"] and r["n_objetos"] == 3 and r["bytes"] == 5 + 1 + 1
    assert r["sobrescribe"] == ["CSVs/a_meta.csv"]


def test_inspeccionar_destino_vacio_y_errores_visibles(tmp_path):
    _arbol(tmp_path, {"CSVs/a_meta.csv": b"x"})
    r = sr.inspeccionar_destino(tmp_path, 7, None, proveedor=ProveedorFalso({}))
    assert r["ok"] and r["existe"] is False and r["n_objetos"] == 0 and r["sobrescribe"] == []
    r = sr.inspeccionar_destino(tmp_path, 7, None, proveedor=ProveedorFalso(None))
    assert r["ok"] is False and r["error"] and r["existe"] is None

    class Roto(ProveedorFalso):
        def listar_remotos(self, prefix):
            raise OSError("red caida")
    r = sr.inspeccionar_destino(tmp_path, 7, None, proveedor=Roto())
    assert r["ok"] is False and "red caida" in r["error"]


def test_credencial_resultado_tipos():
    class G:
        es_password = False
        es_google = True
    assert sr.credencial_resultado(G())["tipo"] == "google"
    assert sr.credencial_resultado(None)["ok"] is False


def test_proveedor_puede_sobrescribir_y_pide_ambito_resultado():
    class AuthPwd:
        es_password = True
        def prefijo_resultado(self, inspeccion_id):
            return PREFIJO
    p = sr.ProveedorResultado(AuthPwd(), 7)
    assert p.solo_crear is False
    assert p.bucket == sr.BUCKET_RESULTADO
    assert p.ambito == sr.AMBITO_RESULTADO and p.inspeccion_id == 7
    assert p.prefijo_destino() == PREFIJO


# ---- decidir_automatica -----------------------------------------------------------
def _params(**kw):
    return {"destino": "/salida", "inspeccion_id": 7, **kw}


def test_decidir_automatica_ok_y_warning_si_hay_inspeccion():
    for status in ("ok", "warning"):
        assert sr.decidir_automatica("split_images", _params(), {"status": status}) == (True, "")


def test_decidir_automatica_no_aplica_en_silencio_si_no_es_el_caso():
    assert sr.decidir_automatica("otra_task", _params(), {"status": "ok"}) == (False, "")
    assert sr.decidir_automatica("split_images", _params(), {"status": "cancelled"}) == (False, "")
    assert sr.decidir_automatica("split_images", _params(), {"status": "error"}) == (False, "")
    assert sr.decidir_automatica("split_images", _params(), None) == (False, "")
    assert sr.decidir_automatica("split_images", _params(subir_resultado=False), {"status": "ok"}) == (False, "")


def test_decidir_automatica_sin_inspeccion_o_sin_salida_avisa():
    ok, motivo = sr.decidir_automatica("split_images", _params(inspeccion_id=None), {"status": "ok"})
    assert ok is False and "inspección" in motivo
    ok, motivo = sr.decidir_automatica("split_images", {"inspeccion_id": 7}, {"status": "ok"})
    assert ok is False and "carpeta de salida" in motivo


# --- verificación fresca tras subir (subida incompleta nunca es ok) -----------------

def test_cancelado_sin_should_stop_es_incompleta(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)

    def subir(plan, provider, **kw):
        return cu.UploadResult(uploaded=0, failed=[(i.remote, "cancelado") for i in plan.items])

    prov = ProveedorFalso()
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov, subir=subir)
    assert not r.ok and r.incompleta and len(r.cancelados) == 5 and not r.cancelado
    assert "5 objetos sin subir" in r.motivo_incompleto()


def test_faltante_en_bucket_es_incompleta(tmp_path):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = ProveedorFalso()
    prov.refleja_subidas = False   # el bucket no refleja lo «subido»: falta todo
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov, concurrency=1)
    assert not r.ok and len(r.faltantes) == 5
    d = r.a_dict()
    assert d["faltantes"] == 5 and len(d["faltantes_ejemplos"]) == 5 and not d["ok"]


def test_todo_presente_es_ok(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=ProveedorFalso(), concurrency=1)
    assert r.ok and r.faltantes == [] and r.verificado


def test_fallo_al_listar_tras_subir_no_es_ok(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = ProveedorFalso()
    prov.fallar_listado_tras_subir = True
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov, concurrency=1)
    assert not r.ok and not r.verificado and r.incompleta


def test_listado_truncado_en_verificacion_es_no_verificable(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)

    class Truncado(ProveedorFalso):
        def listar_remotos(self, prefix):
            if self.calls:
                raise cu.ListadoTruncado("demasiados objetos")
            return super().listar_remotos(prefix)

    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=Truncado(), concurrency=1)
    assert r.truncado and not r.ok and r.faltantes == [] and "truncado" in r.motivo_incompleto()


def test_listar_objetos_remotos_estricto_lanza_si_trunca(monkeypatch):
    import io
    import json as _json

    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(cu.urllib.request, "urlopen", lambda *a, **k: R(
        _json.dumps({"items": [{"name": "a", "size": "1"}], "nextPageToken": "t"}).encode()))
    monkeypatch.setattr(cu, "token_gcs", lambda *a, **k: "tok")
    import pytest as _pt
    with _pt.raises(cu.ListadoTruncado):
        cu.listar_objetos_remotos("b", "p", None, max_paginas=2, estricto=True)
    assert len(cu.listar_objetos_remotos("b", "p", None, max_paginas=2)) == 1  # crudos: solo avisa


def test_motivo_distingue_fallidos_de_sin_intentar(tmp_path):
    r = sr.ResumenResultado(modo=sr.MODO_NORMAL, prefijo="P/",
                            fallidas=[("P/a", "boom")],
                            faltantes=[("P/a", "x"), ("P/b", "x"), ("P/c", "x")])
    m = r.motivo_incompleto()
    assert "3 objetos sin subir" in m and "1 fallidos, 2 sin intentar" in m


def test_estados_llevan_prefix_desde_el_primero_y_fase_comprobando(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    nombre, ro = _remoto("CSVs/V1_meta.csv", b"meta")  # mismo tamaño+md5: obliga a comprobar
    prov = ProveedorFalso({nombre: ro})
    estados = []
    sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov,
                       concurrency=1, on_estado=estados.append)
    assert estados[0]["fase"] == "listando"
    assert all(e["prefix"] == PREFIJO for e in estados)
    comp = [e["comprobando"] for e in estados if e["comprobando"]]
    assert comp and comp[-1] == {"hechos": 1, "total": 1}
    assert "files_per_s" in estados[-1]


def test_upload_plan_stats_trae_files_per_s(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    prov = ProveedorFalso()
    visto = []
    sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=prov,
                       concurrency=1, on_estado=visto.append)
    con_vel = [e for e in visto if e["files_per_s"] is not None]
    assert con_vel and con_vel[-1]["files_per_s"] > 0


# --- medición para la gráfica y el diagnóstico --------------------------------------

def test_estado_lleva_t_bytes_enviados_y_medidas_y_metricas_el_desglose_por_vuelo(tmp_path, gcs):
    _arbol(tmp_path, {
        "TERMICA/PB1_V1/A_T.tiff": b"t" * 100,
        "TERMICA/PB1_V2/B_T.tiff": b"u" * 50,
        "RGB/PB1_V1/A_W_CROP.JPG": b"c" * 80,
        "CSVs/PB1_V1_meta.csv": b"meta",
        "ESTADILLOS/e.xlsx": b"est",
    })
    eventos: list[dict] = []
    r = sr.subir_resultado(tmp_path, 7, sr.MODO_URGENCIA, auth=None, proveedor=ProveedorFalso(),
                           concurrency=1, on_estado=eventos.append)
    assert all("t" in e and "medidas" in e and "hilos_max" in e for e in eventos)
    ts = [e["t"] for e in eventos]
    assert ts == sorted(ts)                          # eje X monótono
    assert eventos[-1]["bytes_enviados"] == r.metricas["bytes_enviados"] > 0
    assert eventos[-1]["ficheros_subidos"] == r.metricas["urgentes"]["subidos"] > 0
    assert eventos[-1]["medidas"]["red_s"] > 0
    m = r.metricas
    assert m["medidas"]["red_s"] > 0 and m["hilos_max"] == 1
    up = m["urgentes"]
    # Solo lo subido, por vuelo (ESTADILLOS no pertenece a ningún vuelo).
    assert up["subidos_por_vuelo"] == {"PB1_V1": {"ficheros": 3, "bytes": 184},
                                       "PB1_V2": {"ficheros": 1, "bytes": 50}}
    assert up["subidos_por_tipo"]["tiff_termica"]["ficheros"] == 2
    assert sum(v["ficheros"] for v in up["subidos_por_tipo"].values()) == up["subidos"] == 5


def test_medidas_se_acumulan_entre_urgentes_y_resto(tmp_path, gcs):
    _arbol(tmp_path, SALIDA_PEQUENA)
    llamadas = []

    def subir(plan, provider, **kw):
        m = cu.medidas_vacias(); m["red_s"] = 1.5; m["reintentos"]["429"] = 2
        llamadas.append(len(plan.items))
        return cu.UploadResult(uploaded=len(plan.items), bytes_sent=len(plan.items) * 10, medidas=m)

    r = sr.subir_resultado(tmp_path, 7, sr.MODO_NORMAL, auth=None, proveedor=ProveedorFalso(), subir=subir)
    assert len(llamadas) == 2
    assert r.metricas["medidas"]["red_s"] == 3.0
    assert r.metricas["medidas"]["reintentos"]["429"] == 4
    assert r.metricas["bytes_enviados"] == sum(llamadas) * 10


# ---- 3.4.150: FOTOS_GENERALES urgentes, duplicados omitidos, lista de sin clasificar ----

def test_fotos_generales_son_urgentes_con_destino_bajo_el_prefijo(tmp_path):
    salida = tmp_path / "salida"
    nombres = ["G_W.JPG", "G_W_CROP.JPG", "G_T.JPG", "G_T.tiff", "g.jpeg", "h.TIF"]
    _arbol(salida, {f"FOTOS_GENERALES/{n}": b"x" for n in nombres})
    for n in nombres:
        assert sr.clasificar(f"FOTOS_GENERALES/{n}") == sr.URGENTE
    sel = sr.seleccionar(salida, PREFIJO)
    assert sorted(i.remote for i in sel.urgentes) == sorted(f"{PREFIJO}FOTOS_GENERALES/{n}" for n in nombres)
    assert sel.sin_clasificar == 0 and sel.resto == []


def test_fotos_generales_solo_planas_de_imagen_y_no_ocultas(tmp_path):
    salida = tmp_path / "salida"
    _arbol(salida, {"FOTOS_GENERALES/notas.txt": b"x", "FOTOS_GENERALES/.oculta.jpg": b"x",
                    "FOTOS_GENERALES/sub/a.jpg": b"x", "FOTOS_GENERALES/ok.jpg": b"x"})
    sel = sr.seleccionar(salida, PREFIJO)
    assert [i.remote for i in sel.urgentes] == [f"{PREFIJO}FOTOS_GENERALES/ok.jpg"]
    motivos = {x["ruta"]: x["motivo"] for x in sel.sin_clasificar_lista}
    assert set(motivos) == {"FOTOS_GENERALES/notas.txt", "FOTOS_GENERALES/.oculta.jpg", "FOTOS_GENERALES/sub/a.jpg"}
    assert "extensión" in motivos["FOTOS_GENERALES/notas.txt"]
    assert "oculto" in motivos["FOTOS_GENERALES/.oculta.jpg"]
    assert "plana" in motivos["FOTOS_GENERALES/sub/a.jpg"]


def test_duplicado_sin_gemelo_en_csvs_es_sin_clasificar(tmp_path):
    salida = tmp_path / "salida"
    _arbol(salida, {"RGB/PB1/V1/B_meta.csv": b"1", "CSVs/A_meta.csv": b"m", "RGB/PB1/V1/A_meta.csv": b"1"})
    sel = sr.seleccionar(salida, PREFIJO)
    assert sel.omitidos_duplicado == 1
    assert sel.sin_clasificar_lista == [{"ruta": "RGB/PB1/V1/B_meta.csv", "motivo": "CSV sin copia en CSVs/"}]


def test_location_meta_fuera_de_csvs_se_omiten_como_duplicado(tmp_path, caplog):
    salida = tmp_path / "salida"
    _arbol(salida, {"RGB/PB1/PB1_V1/V1_location.csv": b"1", "TERMICA/PB1/PB1_V1/V1_meta.csv": b"2",
                    "CSVs/V1_meta.csv": b"m", "CSVs/V1_location.csv": b"l"})
    gem = {"CSVs/V1_location.csv", "CSVs/V1_meta.csv"}
    assert sr.clasificar("RGB/PB1/PB1_V1/V1_location.csv", gem) == sr.OMITIDO_DUPLICADO
    assert sr.clasificar("TERMICA/PB1/PB1_V1/V1_meta.csv", gem) == sr.OMITIDO_DUPLICADO
    assert sr.clasificar("TERMICA/PB1/PB1_V1/V1_meta.csv") is None
    with caplog.at_level("INFO", logger=sr.logger.name):
        sel = sr.seleccionar(salida, PREFIJO)
    assert sel.omitidos_duplicado == 2 and sel.sin_clasificar == 0 and sel.sin_clasificar_lista == []
    assert sorted(i.remote.removeprefix(PREFIJO) for i in sel.urgentes) == ["CSVs/V1_location.csv", "CSVs/V1_meta.csv"]
    lineas = [r for r in caplog.records if "omitidos" in r.getMessage()]
    assert len(lineas) == 1 and "2" in lineas[0].getMessage()


def test_sin_clasificar_devuelve_lista_ruta_motivo_y_llega_al_resumen(tmp_path):
    salida = tmp_path / "salida"
    _arbol(salida, {**SALIDA_TIPICA})
    sel = sr.seleccionar(salida, PREFIJO)
    assert {x["ruta"] for x in sel.sin_clasificar_lista} == {"CSVs/otro.csv", "RGB_Extra/PB1/V1/X.JPG"}
    assert all(x["motivo"] for x in sel.sin_clasificar_lista)
    r = sr.ResumenResultado(modo="urgencia", prefijo="P/", sin_clasificar=2,
                            sin_clasificar_lista=sel.sin_clasificar_lista)
    d = r.a_dict()
    assert d["sin_clasificar"] == 2 and d["sin_clasificar_lista"] == sel.sin_clasificar_lista


def test_decidir_resto_ok_fallos_cancelado_y_desmarcado():
    ok = sr.ResumenResultado(modo="urgencia", prefijo="P/")
    assert sr.decidir_resto(ok, True) == (True, "")
    assert sr.decidir_resto(ok, False)[0] is False
    mal = sr.ResumenResultado(modo="urgencia", prefijo="P/", fallidas=[("a", "x"), ("b", "y")])
    assert sr.decidir_resto(mal, True) == (False, "Resto no lanzado: urgentes con 2 fallos")
    canc = sr.ResumenResultado(modo="urgencia", prefijo="P/", cancelado=True)
    lanzar, aviso = sr.decidir_resto(canc, True)
    assert lanzar is False and "cancelada" in aviso
