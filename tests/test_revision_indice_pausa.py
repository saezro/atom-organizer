"""Pausa de escritorio de la revisión del índice: cola, latido, timeout y hook del run `completo`.

La espera es del hilo del run (único escritor, con su `Manifiesto`); las peticiones de la UI
(`enviar`) entran por una cola que procesa ese mismo hilo. Sin armar, el run es el de siempre.
"""
import datetime as dt
import os
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from atom_core import cancelacion
from atom_core import revision_indice as ri
from atom_core.cancelacion import RunCancelado
from atom_core.recomendaciones_sin_colocar import recomendar, umbrales_desde_entorno
from atom_core.organize import _active_split_phases
from tests.test_giro_manifiesto_gcs import CLAVE as CLAVE_GCS
from tests.test_giro_manifiesto_gcs import RAIZ as RAIZ_GCS
from tests.test_giro_manifiesto_gcs import bucket  # noqa: F401 (fixture)
from tests.test_recomendaciones_sin_colocar import _indexar
from tests.test_sin_colocar import _t, tanda  # noqa: F401 (fixture)

# Alta (45 s de la fila 0, otro vecino a 49 min) y media (200 s de la fila 0).
FOTOS = [("DJI_0001_D.JPG", _t(10, 10, 45)), ("DJI_0002_D.JPG", _t(10, 40, 0))]


@pytest.fixture(autouse=True)
def _limpio():
    cancelacion.limpiar()
    ri.desarmar()
    yield
    cancelacion.limpiar()
    ri.desarmar()


def _ejecucion(man):
    return man.grupos_sin_colocar_raw()[0]["ejecucion"]


def _cfg(tanda):
    return SimpleNamespace(output_folder=tanda.destino)


def _preparar(tanda):
    _indexar(tanda, FOTOS)
    man = tanda.manifiesto()
    return man, _ejecucion(man)


def _hilo(fn):
    hilo = threading.Thread(target=fn, daemon=True)
    hilo.start()
    return hilo


def _esperar_estado(estado="esperando", plazo=5.0):
    fin = time.monotonic() + plazo
    while time.monotonic() < fin:
        if ri.estado()["estado"] == estado:
            return
        time.sleep(0.01)
    raise AssertionError(f"la pausa no llegó a {estado!r}: {ri.estado()}")


# --- sin pausa ---------------------------------------------------------------

def test_no_armada_devuelve_none_y_no_toca_nada(tanda):
    man, ejecucion = _preparar(tanda)
    eventos = []
    try:
        assert ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lambda _t: None,
                                   eventos.append) is None
        assert man.asignaciones_manuales() == {}
        assert ri.leer_auditoria(man, ejecucion) is None
    finally:
        man.cerrar()
    assert eventos == []


def test_sin_pendientes_no_pausa(tanda):
    _indexar(tanda, [("DJI_0001_D.JPG", _t(10, 5, 0))])    # dentro de ventana: nada pendiente
    man = tanda.manifiesto()
    eventos = []
    ri.armar(espera_s=30)
    try:
        t0 = time.monotonic()
        assert ri.pausar_si_armada(man, _cfg(tanda), 1, lambda _t: None, eventos.append) is None
        assert time.monotonic() - t0 < 2
        assert ri.estado()["activa"] is False
    finally:
        man.cerrar()
    assert eventos == []


def test_espera_por_defecto_1800():
    ri.armar()
    assert ri._ESPERA_S == 1800.0
    assert ri.estado()["activa"] is False        # armada no es en espera


# --- timeout -----------------------------------------------------------------

def test_timeout_aplica_solo_alta_y_audita(tanda):
    man, ejecucion = _preparar(tanda)
    eventos, lineas = [], []
    ri.armar(espera_s=0.2)
    try:
        res = ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lineas.append, eventos.append)
        assert res["resultado"] == "desatendida"
        assert len(res["aplicadas"]) == 1 and len(res["sin_aplicar"]) == 1
        manuales = man.asignaciones_manuales()
        assert len(manuales) == 1                        # solo la alta
        [(clave, a)] = manuales.items()
        assert a["quien"].startswith("auto:recomendacion-alta (sin respuesta en ")
        assert a["quien"].startswith("auto:recomendacion-alta (sin respuesta en 0 min)@")
        assert man.filas_por_claves([clave])[clave]["unassigned"] == 0
        audit = ri.leer_auditoria(man, ejecucion)
        assert audit["resultado"] == "desatendida" and audit["modo"] == "pausa"
        assert audit["espera_s"] == 0.2
        assert len(audit["aplicadas"]) == 1 and len(audit["dudosas"]) == 1
        assert audit["inicio"] and audit["fin"]
    finally:
        man.cerrar()
    assert len(eventos) == 1
    ev = eventos[0]
    assert ev["tipo"] == "revision" and ev["estado"] == "esperando" and ev["espera_s"] == 0.2
    assert ev["deadline_epoch"] > time.time() - 5
    assert any(l.startswith("AVISO:") and "confianza media" in l for l in lineas)
    assert ri.estado()["activa"] is False


def test_firma_de_timeout_redondea_minutos(tanda):
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=0.2)
    try:
        # round(espera_s / 60): 90 s -> 2 min (se simula sin esperar 90 s)
        res = ri._aplicar_final(man, tanda.destino, ejecucion, 90, lambda _t: None)
        assert res["aplicadas"]
        [(_c, a)] = man.asignaciones_manuales().items()
        assert a["quien"].startswith("auto:recomendacion-alta (sin respuesta en 2 min)@")
    finally:
        man.cerrar()


def test_latido_reinicia_la_cuenta_atras(tanda):
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=0.6)
    respuesta = {}

    def _usuario():
        _esperar_estado()
        for _ in range(4):                    # 4 x 0,25 s = 1 s > espera 0,6 s
            time.sleep(0.25)
            ri.latido()
        respuesta["f"] = ri.enviar({"op": "continuar", "modo": "tal_cual"})

    hilo = _hilo(_usuario)
    try:
        res = ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lambda _t: None, lambda _e: None)
    finally:
        hilo.join(5)
        man.cerrar()
    assert res["resultado"] == "respondida"
    assert respuesta["f"].result(5)["resultado"] == "respondida"


# --- cola ---------------------------------------------------------------------

def _con_cola(tanda, peticiones, espera=30):
    """Corre la pausa en este hilo y las peticiones en otro; devuelve (res, resultados, man)."""
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=espera)
    resultados = []

    def _usuario():
        _esperar_estado()
        for fabrica in peticiones:
            futuro = ri.enviar(fabrica(man, ejecucion))
            try:
                resultados.append(("ok", futuro.result(10)))
            except Exception as error:  # noqa: BLE001
                resultados.append(("error", error))

    hilo = _hilo(_usuario)
    res = ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lambda _t: None, lambda _e: None)
    hilo.join(10)
    return res, resultados, man


def _clave_de(man, nombre):
    [(clave,)] = man._conexion().execute(
        "SELECT clave FROM imagenes WHERE nombre_original = ?", (nombre,)).fetchall()
    return clave


def test_cola_asignar_y_continuar_con_el_manifiesto_del_run(tanda, monkeypatch):
    def _no_abrir(*_a, **_k):
        raise AssertionError("la cola abrió un segundo manifiesto")
    monkeypatch.setattr(ri.sin_colocar, "_abrir", _no_abrir)
    res, resultados, man = _con_cola(tanda, [
        lambda m, e: {"op": "asignar", "claves": [_clave_de(m, "DJI_0002_D.JPG")],
                      "fila": 1, "quien": "ana@pc"},
        lambda m, e: {"op": "continuar", "modo": "tal_cual"}])
    try:
        assert res["resultado"] == "respondida"
        assert [r[0] for r in resultados] == ["ok", "ok"]
        manuales = man.asignaciones_manuales()
        [(clave, a)] = manuales.items()
        assert a["quien"] == "ana@pc" and a["fila"] == 1
        # la alta NO se aplicó (continuar tal_cual): sigue pendiente, con aviso
        assert man.filas_por_claves([_clave_de(man, "DJI_0001_D.JPG")]) \
            [_clave_de(man, "DJI_0001_D.JPG")]["unassigned"] == 1
        assert len(res["aplicadas"]) == 1 and len(res["sin_aplicar"]) == 1
        assert ri.leer_auditoria(man, _ejecucion(man))["resultado"] == "respondida"
    finally:
        man.cerrar()


def test_cola_aceptar_y_continuar_con_alta(tanda):
    res, resultados, man = _con_cola(tanda, [
        lambda m, e: {"op": "aceptar", "quien": "ana@pc", "ids": [
            r["id"] for r in recomendar(m, tanda.destino, e, umbrales_desde_entorno())
            if r["confianza"] == "alta"]},
        lambda m, e: {"op": "continuar", "modo": "con_alta"}])
    try:
        assert [r[0] for r in resultados] == ["ok", "ok"]
        assert res["resultado"] == "respondida" and len(res["aplicadas"]) == 1
        [(_c, a)] = man.asignaciones_manuales().items()
        assert a["quien"] == "ana@pc"
    finally:
        man.cerrar()


def test_cola_continuar_con_alta_firma_humana(tanda):
    res, _resultados, man = _con_cola(tanda, [
        lambda m, e: {"op": "continuar", "modo": "con_alta"}])
    try:
        assert res["resultado"] == "respondida" and len(res["aplicadas"]) == 1
        [(_c, a)] = man.asignaciones_manuales().items()
        assert not a["quien"].startswith("auto:") and "@" in a["quien"]
    finally:
        man.cerrar()


def test_peticion_invalida_es_error_visible_y_la_pausa_sigue(tanda):
    res, resultados, man = _con_cola(tanda, [
        lambda m, e: {"op": "asignar", "claves": ["no-existe"], "fila": 0, "quien": "ana@pc"},
        lambda m, e: {"op": "inventada"},
        lambda m, e: {"op": "continuar", "modo": "tal_cual"}])
    try:
        assert resultados[0][0] == "error"
        assert resultados[1][0] == "error" and "inventada" in str(resultados[1][1])
        assert resultados[2][0] == "ok"
        assert res["resultado"] == "respondida" and man.asignaciones_manuales() == {}
    finally:
        man.cerrar()


def test_enviar_sin_pausa_falla_visible():
    with pytest.raises(RuntimeError, match="revisión"):
        ri.enviar({"op": "continuar", "modo": "tal_cual"}).result(1)


def test_cola_con_gcs_usa_el_manifiesto_del_run(tanda, bucket, monkeypatch):  # noqa: F811
    """Destino `gs://`: la cola escribe con el `Manifiesto` del run, nunca con un `_abrir`."""
    from atom_core import almacen as almacen_mod  # noqa: PLC0415
    _indexar(tanda, FOTOS)
    almacen_mod.publicar_en(os.path.join(tanda.destino, ".organizado", "manifiesto.db"),
                            RAIZ_GCS + "/.organizado/manifiesto.db", if_generation_match=0)

    def _no_abrir(*_a, **_k):
        raise AssertionError("se abrió un segundo manifiesto")
    monkeypatch.setattr(ri.sin_colocar, "_abrir", _no_abrir)
    man = tanda.manifiesto()
    ejecucion = _ejecucion(man)
    ri.armar(espera_s=30)
    resultados = []

    def _usuario():
        _esperar_estado()
        resultados.append(ri.enviar({"op": "continuar", "modo": "con_alta"}).result(10))

    hilo = _hilo(_usuario)
    try:
        res = ri.pausar_si_armada(man, SimpleNamespace(output_folder=RAIZ_GCS), ejecucion,
                                  lambda _t: None, lambda _e: None)
        hilo.join(10)
        assert res["resultado"] == "respondida" and len(res["aplicadas"]) == 1
        [fila] = [f for f in man.todas() if not f["unassigned"]]
        assert fila["ruta_salida_original"].startswith(RAIZ_GCS + "/RGB/PB1/")
    finally:
        man.cerrar()
    assert bucket.generaciones[CLAVE_GCS] == 1   # no se subió nada por su cuenta


# --- cancelación ---------------------------------------------------------------

def test_cancelar_durante_la_pausa_sale_sin_cierre(tanda):
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=30)

    def _usuario():
        _esperar_estado()
        pendiente = ri.enviar({"op": "asignar", "claves": [], "fila": 0, "quien": "x@y"})
        cancelacion.solicitar()
        try:
            pendiente.result(5)
        except Exception:  # noqa: BLE001 - solo importa que no se cuelgue
            pass

    hilo = _hilo(_usuario)
    try:
        t0 = time.monotonic()
        with pytest.raises(RunCancelado):
            ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lambda _t: None, lambda _e: None)
        assert time.monotonic() - t0 < 3
        hilo.join(5)
        assert ri.leer_auditoria(man, ejecucion)["resultado"] == "cancelada"
        assert man.asignaciones_manuales() == {}
        assert ri.estado()["activa"] is False
    finally:
        man.cerrar()
    # sin locks huérfanos: una petición posterior falla en vez de colgarse
    with pytest.raises(RuntimeError):
        ri.enviar({"op": "continuar", "modo": "tal_cual"}).result(1)


# --- errores: auditoría fiel -------------------------------------------------------

def test_error_inesperado_audita_error_con_motivo_y_propaga(tanda, monkeypatch):
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=0.2)

    def _roto(*_a, **_k):
        raise RuntimeError("disco lleno")
    monkeypatch.setattr(ri, "_aplicar_final", _roto)
    try:
        with pytest.raises(RuntimeError, match="disco lleno"):
            ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lambda _t: None, lambda _e: None)
        audit = ri.leer_auditoria(man, ejecucion)
        assert audit["resultado"] == "error"
        assert audit["error"] == "RuntimeError: disco lleno"
        assert ri.estado()["activa"] is False
    finally:
        man.cerrar()


@pytest.mark.parametrize("excepcion", [KeyboardInterrupt, SystemExit])
def test_interrupcion_de_proceso_sigue_siendo_cancelada(tanda, monkeypatch, excepcion):
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=0.2)

    def _corta(*_a, **_k):
        raise excepcion()
    monkeypatch.setattr(ri, "_aplicar_final", _corta)
    try:
        with pytest.raises(excepcion):
            ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lambda _t: None, lambda _e: None)
        audit = ri.leer_auditoria(man, ejecucion)
        assert audit["resultado"] == "cancelada" and "error" not in audit
    finally:
        man.cerrar()


def test_continuar_con_alta_fallo_a_mitad_la_auditoria_refleja_lo_escrito(tanda, monkeypatch):
    man, ejecucion = _preparar(tanda)
    ri.armar(espera_s=1.5)
    real = ri.sin_colocar.asignar_fotos
    estado = {"escrita": False, "romper": True}

    def _asignar(*a, **k):
        res = real(*a, **k)
        estado["escrita"] = True
        return res
    monkeypatch.setattr(ri.sin_colocar, "asignar_fotos", _asignar)
    original = man.asignaciones_manuales

    def _manuales():
        if estado["escrita"] and estado["romper"]:
            raise sqlite3.OperationalError("database is locked")
        return original()
    monkeypatch.setattr(man, "asignaciones_manuales", _manuales)
    respuesta = {}

    def _usuario():
        _esperar_estado()
        futuro = ri.enviar({"op": "continuar", "modo": "con_alta"})
        try:
            futuro.result(10)
        except Exception as error:  # noqa: BLE001
            respuesta["error"] = error
        estado["romper"] = False          # la pausa sigue; se deja vencer la espera

    hilo = _hilo(_usuario)
    try:
        res = ri.pausar_si_armada(man, _cfg(tanda), ejecucion, lambda _t: None, lambda _e: None)
        hilo.join(10)
        assert "database is locked" in str(respuesta["error"])
        manuales = man.asignaciones_manuales()
        assert len(manuales) == 1                     # la alta quedó escrita
        audit = ri.leer_auditoria(man, ejecucion)
        assert len(audit["aplicadas"]) == 1           # y la auditoría lo refleja
        assert audit["aplicadas"][0]["n_fotos"] == 1
        assert len(audit["errores"]) == 1
        assert "OperationalError: database is locked" in audit["errores"][0]
        assert len(res["aplicadas"]) == 1
    finally:
        man.cerrar()


# --- plan de fases ---------------------------------------------------------------

def test_plan_de_fases_incluye_revision_solo_si_armada():
    cfg = SimpleNamespace()
    assert _active_split_phases(cfg, "todo") == [
        "Índice", "Imágenes RGB", "Conversión térmica", "Cierre"]
    ri.armar()
    try:
        assert _active_split_phases(cfg, "todo") == [
            "Índice", "Revisión del índice", "Imágenes RGB", "Conversión térmica", "Cierre"]
    finally:
        ri.desarmar()
    assert "Revisión del índice" not in _active_split_phases(cfg, "todo")


# --- hook en el run `completo` ----------------------------------------------------

from tests.test_organizado_plan_apply_e2e import (  # noqa: E402
    _correr, _escribir_estadillo, _HostDePrueba, _cfg as _cfg_e2e, _inspeccion)  # noqa: F401


def test_no_armada_run_completo_igual_que_hoy(_inspeccion, logger, monkeypatch):  # noqa: F811
    host = _HostDePrueba(logger)
    cfg = _cfg_e2e(_inspeccion)
    pcb, _pbar, psum = _correr(host, cfg, monkeypatch)
    assert [m for m in psum.mensajes if str(m).startswith("---> SUBPROCESO:")] == [
        "---> SUBPROCESO: Índice", "---> SUBPROCESO: Imágenes RGB",
        "---> SUBPROCESO: Conversión térmica", "---> SUBPROCESO: Cierre"]
    assert not any("Revisión del índice" in str(m) for m in pcb.mensajes)
    man_db = os.path.join(cfg.output_folder, ".organizado", "manifiesto.db")
    con = sqlite3.connect(man_db)
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM ajustes WHERE clave LIKE 'revision_indice:%'").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM asignaciones_manuales").fetchone()[0] == 0
    finally:
        con.close()


def test_run_armado_sin_pendientes_emite_fase_y_no_pausa(tmp_path, make_dji_jpeg, logger,
                                                         monkeypatch):
    origen = tmp_path / "origen"
    origen.mkdir()
    ts = dt.datetime(2026, 1, 15, 10, 0, 0)
    make_dji_jpeg(str(origen / "DJI_0001_D.JPG"), dt_val=ts, gimbal_yaw=0.0, make="DJI")
    _escribir_estadillo(tmp_path / "estadillo.csv",
                        [("1", "1", "2026:01:15", "09:55:00", "10:05:00")])
    host = _HostDePrueba(logger)
    eventos = []
    ri.armar(espera_s=30, on_evento=eventos.append)
    _pcb, _pbar, psum = _correr(host, _cfg_e2e(tmp_path), monkeypatch)
    assert "---> SUBPROCESO: Revisión del índice" in psum.mensajes
    assert eventos == []


def test_avisos_sin_ordenar_recalculados_tras_asignar(_inspeccion, logger, monkeypatch):  # noqa: F811
    """El aviso «día sin estadillo → irán a SIN_ORDENAR» sale del manifiesto tras la pausa."""
    cfg = _cfg_e2e(_inspeccion)
    host = _HostDePrueba(logger)
    ri.armar(espera_s=30)
    respuestas = []

    def _usuario():
        _esperar_estado()
        con = sqlite3.connect(os.path.join(cfg.output_folder, ".organizado", "manifiesto.db"))
        try:
            [(clave,)] = con.execute(
                "SELECT clave FROM imagenes WHERE nombre_original = 'DJI_9999_D.JPG'").fetchall()
        finally:
            con.close()
        respuestas.append(ri.enviar({"op": "asignar", "claves": [clave], "fila": 0,
                                     "quien": "ana@pc"}).result(20))
        respuestas.append(ri.enviar({"op": "continuar", "modo": "tal_cual"}).result(20))

    hilo = _hilo(_usuario)
    pcb, _pbar, psum = _correr(host, cfg, monkeypatch)
    hilo.join(20)
    assert len(respuestas) == 2
    texto = "".join(str(m) for m in pcb.mensajes)
    aviso = "día 2020-01-01 sin estadillo (1 fotos) → irán a SIN_ORDENAR"
    # Una sola vez (la del índice, previa a la pausa): la del cierre, recalculada, ya no sale.
    assert texto.count(aviso) == 1


def test_avisos_sin_ordenar_se_mantienen_si_no_se_asigna(_inspeccion, logger, monkeypatch):  # noqa: F811
    cfg = _cfg_e2e(_inspeccion)
    host = _HostDePrueba(logger)
    ri.armar(espera_s=30)
    respuestas = []

    def _usuario():
        _esperar_estado()
        respuestas.append(ri.enviar({"op": "continuar", "modo": "tal_cual"}).result(20))

    hilo = _hilo(_usuario)
    pcb, _pbar, _psum = _correr(host, cfg, monkeypatch)
    hilo.join(20)
    assert len(respuestas) == 1
    texto = "".join(str(m) for m in pcb.mensajes)
    aviso = "día 2020-01-01 sin estadillo (1 fotos) → irán a SIN_ORDENAR"
    assert texto.count(aviso) == 2       # índice + cierre
