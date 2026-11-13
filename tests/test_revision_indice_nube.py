"""Nube y CLI local sin UI: el índice aplica al momento las recomendaciones seguras
(confianza alta) dentro de `construir_indice` y publica el bloque `revision_indice` en
`propuesta.json`. Con el flag apagado la salida es la de siempre."""
import json
import os

import pytest

import organize_cli
from atom_core import indice_mapreduce as mr
from atom_core import revision_indice as ri
from atom_core import sin_colocar
from atom_core.indice import construir_indice
from atom_core.manifiesto import Manifiesto
from atom_core.propuesta import construir_propuesta
from tests.test_indice_mapreduce import _origen  # noqa: F401 (fixture)
from tests.test_indice_organizado import (
    _cfg, _crear_imagen, _escribir_estadillo, _ExifDePrueba, _PipelineDePrueba, _Signal)
from tests.test_recomendaciones_sin_colocar import _ventanas
from tests.test_sin_colocar import ESTADILLO, _t

EJEC = "exec-nube"
# Alta (45 s de la fila 0, otro vecino a 49 min) y media (200 s de la fila 0).
FOTOS = [("DJI_0001_D.JPG", _t(10, 10, 45)), ("DJI_0002_D.JPG", _t(10, 40, 0))]


def _indexar(tmp_path, fotos, env=None, monkeypatch=None, ejecucion=EJEC, nombre="d",
             destino=None):
    """Un índice N=1 con `ejecucion_resumen`. Devuelve `(destino, mensajes)`."""
    if monkeypatch is not None:
        monkeypatch.delenv(ri.ENV_ACTIVA, raising=False)
        if env is not None:
            monkeypatch.setenv(ri.ENV_ACTIVA, env)
    destino = destino or str(tmp_path / nombre)
    os.makedirs(os.path.join(destino, ".organizado"), exist_ok=True)
    _escribir_estadillo(tmp_path / "estadillo.csv", ESTADILLO)
    carpeta = str(tmp_path / "origen")
    cfg = _cfg(tmp_path, input_folder=carpeta, output_folder=destino)
    rutas = {n: _crear_imagen(carpeta, n) for n, _ts in fotos}
    exif = _ExifDePrueba(timestamps={rutas[n]: ts for n, ts in fotos if ts is not None}, yaws={})
    man = Manifiesto(os.path.join(destino, ".organizado", "manifiesto.db"))
    man.crear_esquema()
    senal = _Signal()
    try:
        construir_indice(
            cfg, _PipelineDePrueba(_ventanas(ESTADILLO)), exif, man, senal, _Signal(), _Signal(),
            ejecucion_id=man.abrir_ejecucion(carpeta, "test"), ejecucion_resumen=ejecucion)
    finally:
        man.cerrar()
    return destino, senal.mensajes


def _json(ruta):
    with open(ruta, encoding="utf-8") as fh:
        return json.load(fh)


def _salida(destino, ejecucion=EJEC):
    return _json(mr.ruta_resumen(destino, ejecucion)), _json(mr.ruta_propuesta(destino, ejecucion))


def _manifiesto(destino):
    return Manifiesto(os.path.join(destino, ".organizado", "manifiesto.db"))


@pytest.mark.parametrize("valor", [None, "", "0", "false", "False", "no"])
def test_flag_apagado_resumen_y_propuesta_identicos(tmp_path, monkeypatch, valor):
    base, _m = _indexar(tmp_path, FOTOS, None, monkeypatch, nombre="a")
    otro, _m = _indexar(tmp_path, FOTOS, valor, monkeypatch, nombre="b")
    resumen_a, propuesta_a = _salida(base)
    resumen_b, propuesta_b = _salida(otro)
    assert "revision_indice" not in propuesta_a and "revision_indice" not in propuesta_b
    assert resumen_a["n_sin_ordenar"] == resumen_b["n_sin_ordenar"] == 2
    # Valores fijos (no solo A == B): el flag apagado no cambia el contrato de siempre.
    claves_resumen = {"ejecucion_indice", "fuera_de_orden", "hash_listado", "n_imagenes",
                      "n_pares", "n_pb", "n_por_tipo", "n_sin_ordenar", "n_vuelos", "pb",
                      "version"}
    claves_propuesta = {"ejecucion_indice", "fuente_categoria", "fuera_de_vuelo", "generado",
                        "hash_listado", "planta", "totales", "version", "vuelos"}
    for resumen, propuesta in ((resumen_a, propuesta_a), (resumen_b, propuesta_b)):
        assert set(resumen) == claves_resumen and set(propuesta) == claves_propuesta
        assert resumen["n_imagenes"] == 2 and resumen["n_sin_ordenar"] == 2
        assert propuesta["totales"] == {"n_imagenes": 2, "bytes": 0, "n_pb": 0,
                                        "n_vuelos": 0, "n_sin_ordenar": 2}

    def _norm(d, destino):
        return json.loads(json.dumps(d).replace(destino.replace("\\", "/"), "<D>"))
    for a, b in ((resumen_a, resumen_b), (propuesta_a, propuesta_b)):
        a, b = dict(a), dict(b)
        a.pop("generado", None), b.pop("generado", None)
        assert set(a) == set(b)
        assert all(_norm(a[k], base) == _norm(b[k], otro) for k in a), [
            k for k in a if _norm(a[k], base) != _norm(b[k], otro)]
    man = _manifiesto(otro)
    try:
        assert man.asignaciones_manuales() == {}
        assert ri.leer_auditoria(man, 1) is None
    finally:
        man.cerrar()


def test_nube_aplica_alta_al_momento(tmp_path, monkeypatch):
    destino, mensajes = _indexar(tmp_path, FOTOS, "1", monkeypatch)
    resumen, propuesta = _salida(destino)
    assert resumen["n_sin_ordenar"] == 1            # bajó en n_fotos (1) desde 2
    bloque = propuesta["revision_indice"]
    assert bloque["version"] == 1 and bloque["modo"] == "inmediato"
    assert bloque["resultado"] == "inmediata"
    [aplicada] = bloque["aplicadas"]
    assert aplicada["confianza"] == "alta" and aplicada["n_fotos"] == 1
    assert (aplicada["fila"], aplicada["pb"], aplicada["vuelo"]) == (0, "1", "1")
    assert set(aplicada) == {"id", "grupo_id", "n_fotos", "fila", "pb", "vuelo", "sufijo", "motivo",
                             "motivo_fuera", "distancia_s", "confianza", "explicacion"}
    assert aplicada["motivo"] in ("vecino", "par-partido")
    assert aplicada["motivo_fuera"] == "fuera-de-ventana"
    [dudosa] = bloque["sin_aplicar"]
    assert dudosa["confianza"] == "media" and set(dudosa) == set(aplicada)
    assert len(bloque["avisos"]) == 1 and bloque["avisos"][0].startswith("AVISO:")
    assert "confianza media" in bloque["avisos"][0]
    # La propuesta es válida contra el manifiesto ya recolocado (categoría coherente).
    man = _manifiesto(destino)
    try:
        man.checkpoint()
        construir_propuesta(man.ruta_db, {"version": mr.VERSION_RESUMEN,
                                          "ejecucion_indice": EJEC, **{
            k: v for k, v in resumen.items() if k not in ("version", "ejecucion_indice")}},
            "PLANTA", "2026-11-13T00:00:00Z")
        manuales = man.asignaciones_manuales()
        assert len(manuales) == 1
        [(clave, a)] = manuales.items()
        assert a["quien"].startswith(
            "auto:recomendacion-alta (aplicada al momento, sin canal de respuesta)@")
        fila = man.filas_por_claves([clave])[clave]
        assert fila["unassigned"] == 0 and fila["categoria"] != "sin_ordenar"
    finally:
        man.cerrar()
    texto = "".join(str(m) for m in mensajes)
    assert "aplicada recomendación" in texto and "AVISO:" in texto


def test_auditoria_inmediata_conserva_origen_de_cada_aplicada(tmp_path, monkeypatch):
    destino, _m = _indexar(tmp_path, FOTOS, "1", monkeypatch)
    man = _manifiesto(destino)
    try:
        ejecucion = man.grupos_sin_colocar_raw()[0]["ejecucion"]
        datos = ri.leer_auditoria(man, ejecucion)
    finally:
        man.cerrar()
    assert datos["aplicadas"]
    for fila in datos["aplicadas"]:
        assert fila["origen"] == "aplicada al momento, sin canal de respuesta"


def test_nube_sin_pendientes_bloque_sin_pendientes(tmp_path, monkeypatch):
    destino, mensajes = _indexar(tmp_path, [("DJI_0001_D.JPG", _t(10, 5, 0))], "1", monkeypatch)
    _resumen, propuesta = _salida(destino)
    bloque = propuesta["revision_indice"]
    assert bloque["resultado"] == "sin_pendientes" and bloque["modo"] == "inmediato"
    assert bloque["aplicadas"] == [] and bloque["sin_aplicar"] == [] and bloque["avisos"] == []
    assert not any("AVISO:" in str(m) for m in mensajes if "sin colocar" in str(m))


def test_auditoria_resultado_inmediata(tmp_path, monkeypatch):
    destino, _m = _indexar(tmp_path, FOTOS, "1", monkeypatch)
    man = _manifiesto(destino)
    try:
        ejecucion = man.grupos_sin_colocar_raw()[0]["ejecucion"]
        datos = ri.leer_auditoria(man, ejecucion)
    finally:
        man.cerrar()
    assert datos["resultado"] == "inmediata" and datos["modo"] == "inmediato"
    # Esquema fijado de la auditoría (spec: decisión E, sin cambio de esquema SQLite).
    assert set(datos) == {"inicio", "fin", "resultado", "modo", "espera_s", "aplicadas", "dudosas"}
    assert datos["espera_s"] == 0
    assert len(datos["aplicadas"]) == 1 and len(datos["dudosas"]) == 1
    assert datos["inicio"] <= datos["fin"]


def test_error_al_aplicar_es_visible_y_no_tumba_el_indice(tmp_path, monkeypatch):
    def _roto(*_a, **_k):
        raise RuntimeError("fallo simulado")
    monkeypatch.setattr(sin_colocar, "asignar_fotos", _roto)
    destino, mensajes = _indexar(tmp_path, FOTOS, "1", monkeypatch)
    resumen, propuesta = _salida(destino)
    assert resumen["n_sin_ordenar"] == 2
    bloque = propuesta["revision_indice"]
    assert bloque["aplicadas"] == [] and len(bloque["sin_aplicar"]) == 2
    assert any(a.startswith("ERROR:") and "fallo simulado" in a for a in bloque["avisos"])
    assert any("ERROR:" in str(m) and "fallo simulado" in str(m) for m in mensajes)


def test_fallo_de_revision_no_tumba_el_indice(tmp_path, monkeypatch):
    def _roto(*_a, **_k):
        raise RuntimeError("recomendar roto")
    monkeypatch.setattr(ri, "recomendar", _roto)
    destino, mensajes = _indexar(tmp_path, FOTOS, "1", monkeypatch)
    resumen, propuesta = _salida(destino)
    assert resumen["n_sin_ordenar"] == 2
    bloque = propuesta["revision_indice"]
    assert bloque["aplicadas"] == [] and bloque["resultado"] == "inmediata"
    assert any(a.startswith("ERROR:") and "recomendar roto" in a for a in bloque["avisos"])
    assert any("ERROR:" in str(m) and "recomendar roto" in str(m) for m in mensajes)


def test_shard_del_map_no_aplica(_origen, logger, monkeypatch):  # noqa: F811
    """Review Focus 3: solo el escritor único (N=1 o el reduce) aplica; el map nunca."""
    from tests.test_indice_mapreduce import _cfg_meta, _correr
    llamadas = []
    monkeypatch.setenv(ri.ENV_ACTIVA, "1")
    monkeypatch.setattr(ri, "aplicar_seguras", lambda *a, **k: llamadas.append(a) or {
        "aplicadas": [], "sin_aplicar": [], "avisos": [], "claves_aplicadas": [], "destinos": {},
        "incoherente": False})
    cfg = _cfg_meta(_origen)
    _correr(logger, monkeypatch, cfg, "indice", 0, 2)         # primer shard: map puro
    assert llamadas == []
    destino = cfg.output_folder
    assert not os.path.exists(mr.ruta_propuesta(destino, "exec-idx"))
    _correr(logger, monkeypatch, cfg, "indice", 1, 2)         # el último hace el reduce
    assert len(llamadas) == 1
    assert "revision_indice" in _json(mr.ruta_propuesta(destino, "exec-idx"))


def test_cli_local_sin_ui_va_como_nube(tmp_path, monkeypatch):
    """`--fase completo` local sin UI: nadie arma la pausa; el índice aplica al momento."""
    monkeypatch.setenv(ri.ENV_ACTIVA, "1")
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "DJI_0001_D.JPG").write_bytes(b"x")
    estadillo = tmp_path / "estadillo.csv"
    estadillo.write_text("x")
    destino = str(tmp_path / "salida")
    visto = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        visto["modo"], visto["armada"] = ri.modo(), ri.armada()
        visto["fase"] = params.get("fase")
        _indexar(tmp_path, FOTOS, env="1", destino=params["destino"])
        return {"status": "ok"}

    llamadas_pausa = []
    monkeypatch.setattr(ri, "pausar_si_armada",
                        lambda *a, **k: llamadas_pausa.append((a, k)), raising=False)
    from atom_core import organize
    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)
    codigo = organize_cli.main(["--origen", str(origen), "--destino", destino,
                                "--estadillo", str(estadillo), "--fase", "completo",
                                "--quiet", "--json"])
    assert codigo != 2
    assert llamadas_pausa == []     # la CLI sin UI nunca espera: no llama a la pausa
    assert visto == {"modo": "inmediato", "armada": False, "fase": "completo"}
    propuesta = _json(mr.ruta_propuesta(destino, EJEC))
    assert propuesta["revision_indice"]["resultado"] == "inmediata"
    assert len(propuesta["revision_indice"]["aplicadas"]) == 1


def test_fallo_de_auditoria_no_deshace_lo_aplicado(tmp_path, monkeypatch):
    def _roto(*_a, **_k):
        raise RuntimeError("auditoria rota")
    monkeypatch.setattr(ri, "guardar_auditoria", _roto)
    destino, mensajes = _indexar(tmp_path, FOTOS, "1", monkeypatch)
    resumen, propuesta = _salida(destino)
    assert resumen["n_sin_ordenar"] == 1            # la foto alta sigue contada como asignada
    assert propuesta["totales"]["n_sin_ordenar"] == 1
    bloque = propuesta["revision_indice"]
    assert len(bloque["aplicadas"]) == 1
    assert any(a.startswith("ERROR:") and "auditoria rota" in a for a in bloque["avisos"])
    assert any("ERROR:" in str(m) and "auditoria rota" in str(m) for m in mensajes)
    man = _manifiesto(destino)
    try:
        assert len(man.asignaciones_manuales()) == 1
    finally:
        man.cerrar()


def test_recolocadas_salen_de_aplicar_seguras_sin_releer_manifiesto(tmp_path, monkeypatch):
    """Tras aplicar, el índice no relee el manifiesto: aunque `filas_por_claves` falle, el
    resumen sale coherente y la propuesta se publica."""
    real = ri.aplicar_seguras

    def _aplicar_y_romper_relectura(*a, **k):
        res = real(*a, **k)
        assert res["destinos"], "debe informar el destino de cada foto aplicada"
        def _roto(*_a, **_k):
            raise RuntimeError("relectura rota")
        monkeypatch.setattr(Manifiesto, "filas_por_claves", _roto)
        return res
    monkeypatch.setattr(ri, "aplicar_seguras", _aplicar_y_romper_relectura)
    destino, mensajes = _indexar(tmp_path, FOTOS, "1", monkeypatch)
    resumen, propuesta = _salida(destino)           # la propuesta se publicó (válida)
    assert resumen["n_sin_ordenar"] == 1
    assert propuesta["revision_indice"]["aplicadas"]
    assert not any("incoherente" in m for m in mensajes)


def test_incoherente_aborta_el_indice_con_error_explicito(tmp_path, monkeypatch):
    """asignar_fotos falla a mitad y la relectura también: no se puede publicar un resumen
    veraz, así que el índice aborta (RuntimeError) y el ERROR se emitió antes."""
    emitidos = []
    real = ri.aplicar_seguras

    def _captura(man, ruta, ejec, quien, emit):
        return real(man, ruta, ejec, quien, lambda t: (emitidos.append(t), emit(t)))
    monkeypatch.setattr(ri, "aplicar_seguras", _captura)

    def _asignar_roto(*_a, **_k):
        original = Manifiesto.filas_por_claves

        def _relectura_rota(*_x, **_y):       # falla solo la relectura tras el fallo
            monkeypatch.setattr(Manifiesto, "filas_por_claves", original)
            raise RuntimeError("relectura rota")
        monkeypatch.setattr(Manifiesto, "filas_por_claves", _relectura_rota)
        raise RuntimeError("asignar roto a mitad")
    monkeypatch.setattr(sin_colocar, "asignar_fotos", _asignar_roto)
    with pytest.raises(RuntimeError, match="resumen incoherente"):
        _indexar(tmp_path, FOTOS, "1", monkeypatch)
    assert any("ERROR:" in t and "no se pudo comprobar" in t for t in emitidos)
