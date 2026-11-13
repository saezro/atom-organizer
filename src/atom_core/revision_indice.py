"""Revisión del índice: coordina qué se hace con las fotos sin colocar al acabar el índice.

Tiene el flag, el modo, la aplicación de lo seguro, la auditoría y la pausa de escritorio
(`pausar_si_armada`: cola de peticiones, latido y timeout). Modos:
- `pausa`: el proceso de la app (escritorio, Pi/kiosco, control remoto) armó la revisión con
  `armar()` y habrá canal de respuesta.
- `inmediato`: nube y CLI local sin UI: las seguras (confianza alta) se aplican al momento y las
  demás quedan en SIN_ORDENAR con aviso visible.
- `apagado`: comportamiento anterior, sin cambios.
"""
from __future__ import annotations

import datetime
import getpass
import json
import os
import queue
import re
import socket
import threading
import time
from concurrent.futures import Future
from typing import Callable, Literal

from atom_core import cancelacion, sin_colocar
from atom_core.manifiesto import Manifiesto
from atom_core.recomendaciones_sin_colocar import recomendar, umbrales_desde_entorno
from atom_core.sin_colocar import ErrorSinColocar

ENV_ACTIVA = "ATOM_REVISION_INDICE"
_ORIGEN = re.compile(r"\(([^()]+)\)")

# Estado de proceso (patrón de `cancelacion.py`): solo hay un run a la vez.
_LOCK = threading.Lock()
_ARMADA = False
_ESPERA_S = 1800.0
_ON_EVENTO: Callable | None = None


def activa(env=os.environ, params: dict | None = None) -> bool:
    """Flag de release. `params["revision_indice"]["activa"]`, si existe, manda sobre el entorno."""
    bloque = (params or {}).get("revision_indice")
    if isinstance(bloque, dict) and "activa" in bloque:
        valor = bloque["activa"]
        if isinstance(valor, str):   # bool("false") sería True: la cadena se interpreta
            texto = valor.strip().lower()
            if texto in ("1", "true"):
                return True
            if texto in ("", "0", "false"):
                return False
            raise ValueError(
                f"params.revision_indice.activa ilegible: {valor!r} (se esperaba true/false).")
        if isinstance(valor, bool):
            return valor
        raise ValueError(
            f"params.revision_indice.activa ilegible: {valor!r} (se esperaba true/false).")
    return str(env.get(ENV_ACTIVA, "")).strip() == "1"


def armar(espera_s: float = 1800, on_evento=None) -> None:
    global _ARMADA, _ESPERA_S, _ON_EVENTO
    with _LOCK:
        _ARMADA, _ESPERA_S, _ON_EVENTO = True, float(espera_s), on_evento


def desarmar() -> None:
    global _ARMADA, _ON_EVENTO
    with _LOCK:
        _ARMADA, _ON_EVENTO = False, None


def armada() -> bool:
    return _ARMADA


def modo(env=os.environ) -> Literal["apagado", "pausa", "inmediato"]:
    if armada():
        return "pausa"
    return "inmediato" if activa(env) else "apagado"


def _origen_de(quien: str) -> str:
    m = _ORIGEN.search(quien)
    if not m:
        raise ValueError(
            f"firma automática sin origen entre paréntesis: {quien!r} "
            "(p. ej. 'auto:recomendacion-alta (sin respuesta en 30 min)@equipo').")
    return m.group(1)


def _resumen(rec: dict) -> dict:
    return {**{k: v for k, v in rec.items() if k != "claves"}, "n_fotos": len(rec["claves"])}


def _aviso(rec: dict, explicacion: str) -> str:
    return (f"AVISO: {len(rec['claves'])} fotos quedan en SIN_ORDENAR sin decisión "
            f"({rec['motivo']}; confianza {rec['confianza']}: {explicacion})")


def aplicar_seguras(man: Manifiesto, ruta_salida: str, ejecucion_id: int, quien: str,
                    emit, origen: str | None = None, parcial: list | None = None) -> dict:
    """Aplica solo las recomendaciones de confianza `alta`, con el MISMO manifiesto del run
    (nunca abre otro). Una línea por aplicada y un `AVISO:` por cada no aplicada. `origen`
    (texto de la línea) solo lo da quien firma como persona (`usuario@equipo`); una firma
    automática lo lleva entre paréntesis y se extrae de ella. `parcial`, si se da, es la lista
    en la que se van acumulando las `aplicadas` (la misma que se devuelve): si la función revienta
    a mitad, quien llama conserva lo que ya quedó escrito."""
    origen = origen if origen is not None else _origen_de(quien)
    recs = recomendar(man, ruta_salida, ejecucion_id, umbrales_desde_entorno())
    aplicadas: list[dict] = parcial if parcial is not None else []
    claves_aplicadas: list[str] = []
    destinos: dict[str, tuple] = {}     # ruta_origen -> (pb, vuelo) de cada foto aplicada
    incoherente = False
    sin_aplicar: list[dict] = []
    avisos: list[str] = []
    for rec in recs:
        if rec["confianza"] != "alta":
            sin_aplicar.append(_resumen(rec))
            avisos.append(_aviso(rec, rec["explicacion"]))
            emit(avisos[-1])
            continue
        try:
            rutas = {c: f["ruta_origen"] for c, f in man.filas_por_claves(rec["claves"]).items()}
        except Exception as error:  # noqa: BLE001 - sin rutas no se aplica: nada se ha tocado
            sin_aplicar.append(_resumen(rec))
            avisos.append(f"ERROR: {len(rec['claves'])} fotos: no se pudo leer el manifiesto "
                          f"antes de aplicar la recomendación ({rec['motivo']}): "
                          f"{type(error).__name__}: {error}")
            emit(avisos[-1])
            continue
        try:
            sin_colocar.asignar_fotos(man, ruta_salida, rec["grupo_id"], rec["claves"],
                                      rec["fila"], quien)
        except ErrorSinColocar as error:
            sin_aplicar.append(_resumen(rec))
            avisos.append(_aviso(rec, f"{rec['explicacion']}; no se pudo aplicar: {error}"))
            emit(avisos[-1])
            continue
        except Exception as error:  # noqa: BLE001 - nunca en silencio: ERROR visible y sigue
            # `asignar_fotos` no es atómico: puede haber asignado parte antes de fallar.
            try:
                filas = man.filas_por_claves(rec["claves"])
                hechas = [c for c in rec["claves"] if c in filas and not filas[c]["unassigned"]]
            except Exception as error2:  # noqa: BLE001 - estado real desconocido: se dice
                avisos.append(
                    f"ERROR: {len(rec['claves'])} fotos: no se pudo aplicar la recomendación "
                    f"({rec['motivo']}): {type(error).__name__}: {error}; y no se pudo "
                    f"comprobar cuántas quedaron asignadas: {type(error2).__name__}: {error2}")
                sin_aplicar.append(_resumen(rec))
                incoherente = True      # puede haber fotos asignadas que no sabemos cuáles
                emit(avisos[-1])
                continue
            resto = [c for c in rec["claves"] if c not in hechas]
            if hechas:
                aplicadas.append(_resumen({**rec, "claves": hechas}))
                claves_aplicadas.extend(hechas)
                destinos.update({rutas[c]: (rec["pb"], rec["vuelo"]) for c in hechas if c in rutas})
            if resto or not hechas:
                sin_aplicar.append(_resumen({**rec, "claves": resto or rec["claves"]}))
            avisos.append(f"ERROR: {len(hechas)} de {len(rec['claves'])} fotos quedaron "
                          f"asignadas y {len(resto)} siguen en SIN_ORDENAR: la aplicación de la "
                          f"recomendación ({rec['motivo']}) falló a mitad: "
                          f"{type(error).__name__}: {error}")
            emit(avisos[-1])
            continue
        aplicadas.append(_resumen(rec))
        claves_aplicadas.extend(rec["claves"])
        destinos.update({rutas[c]: (rec["pb"], rec["vuelo"]) for c in rec["claves"] if c in rutas})
        emit(f"Revisión del índice · aplicada recomendación: {len(rec['claves'])} fotos → "
             f"PB{rec['pb']}/V{rec['vuelo']} fila {rec['fila']} ({rec['explicacion']}) [{origen}]")
    # Pendientes de la ejecución que ninguna recomendación cubre: nunca en silencio.
    cubiertas = {c for rec in recs for c in rec["claves"]}
    manuales = man.asignaciones_manuales()
    for grupo in man.grupos_sin_colocar_raw():
        if grupo.get("ejecucion") != int(ejecucion_id):
            continue
        filas = man.filas_por_claves(grupo["claves"])
        huerfanas = [c for c in grupo["claves"]
                     if c in filas and filas[c]["unassigned"] and c not in manuales
                     and c not in cubiertas]
        if not huerfanas:
            continue
        sin_aplicar.append({"grupo_id": grupo["grupo_id"], "motivo": grupo["motivo"],
                            "confianza": None, "explicacion": "sin recomendación",
                            "n_fotos": len(huerfanas)})
        avisos.append(f"AVISO: {len(huerfanas)} fotos del grupo {grupo['grupo_id']} quedan en "
                      f"SIN_ORDENAR sin decisión ({grupo['motivo']}: sin recomendación)")
        emit(avisos[-1])
    return {"aplicadas": aplicadas, "sin_aplicar": sin_aplicar, "avisos": avisos,
            "claves_aplicadas": claves_aplicadas, "destinos": destinos,
            "incoherente": incoherente}


def dias_sin_estadillo_restantes(man: Manifiesto, dias: list) -> list:
    """`dias_sin_estadillo` del índice `[(YYYY-MM-DD, n_fotos)]` recalculado desde el manifiesto:
    solo los días que aún tienen fotos sin colocar, con cuántas (nunca más que las del índice)."""
    restantes = []
    for dia, n_fotos in dias:
        (n,) = man._conexion().execute(
            "SELECT COUNT(*) FROM imagenes WHERE unassigned = 1 AND substr(timestamp_exif, 1, 10) = ?",
            (dia,)).fetchone()
        if n:
            restantes.append((dia, min(int(n_fotos), int(n))))
    return restantes


def _clave_auditoria(ejecucion_id: int) -> str:
    return f"revision_indice:{int(ejecucion_id)}"


def guardar_auditoria(man: Manifiesto, ejecucion_id: int, datos: dict) -> None:
    man.guardar_ajuste(_clave_auditoria(ejecucion_id), json.dumps(datos, ensure_ascii=False))


def leer_auditoria(man: Manifiesto, ejecucion_id: int) -> dict | None:
    valor = man.leer_ajuste(_clave_auditoria(ejecucion_id))
    return None if valor is None else json.loads(valor)


# --- Pausa de escritorio -------------------------------------------------------------------
# Estado de la espera (bajo `_LOCK`). `_COLA` solo la vacía el hilo del run: único escritor.
_COLA: "queue.Queue[tuple[dict, Future]]" = queue.Queue()
_DESPIERTA = threading.Event()
_ESPERANDO = {"estado": None, "ultima": 0.0, "espera_s": 0.0}   # estado None = sin pausa
_PASO_S = 0.5      # cadencia con que la espera mira la cancelación
_ESTADOS_ABIERTOS = ("esperando", "aplicando")


def _hora() -> str:
    return datetime.datetime.now(sin_colocar._MADRID).isoformat(timespec="seconds")


def _firma_humana() -> str:
    return f"{getpass.getuser()}@{socket.gethostname()}"


def latido() -> None:
    """Reinicia la cuenta atrás de inactividad. Sin pausa en curso no hace nada."""
    with _LOCK:
        if _ESPERANDO["estado"] is not None:
            _ESPERANDO["ultima"] = time.monotonic()


def estado() -> dict:
    with _LOCK:
        actual = _ESPERANDO["estado"]
        if actual is None:
            return {"activa": False, "estado": None, "deadline_epoch": None,
                    "inactividad_s": None}
        inactivo = time.monotonic() - _ESPERANDO["ultima"]
        return {"activa": True, "estado": actual,
                "deadline_epoch": time.time() + (_ESPERANDO["espera_s"] - inactivo),
                "inactividad_s": inactivo}


def enviar(peticion: dict) -> Future:
    """Encola una petición de la UI; la procesa el hilo del run. Sin pausa en curso el Future
    falla (error visible), nunca queda colgado."""
    futuro: Future = Future()
    with _LOCK:
        if _ESPERANDO["estado"] not in _ESTADOS_ABIERTOS:
            futuro.set_exception(RuntimeError(
                "no hay una revisión del índice en espera: la petición no se aplicó."))
            return futuro
        _COLA.put((peticion, futuro))
    _DESPIERTA.set()
    return futuro


def _fallar_pendientes(motivo: str) -> None:
    while True:
        try:
            _peticion, futuro = _COLA.get_nowait()
        except queue.Empty:
            return
        futuro.set_exception(RuntimeError(motivo))


def _pendientes(man: Manifiesto, ejecucion_id: int) -> int:
    """Fotos de la ejecución aún sin colocar y sin decisión manual."""
    manuales = man.asignaciones_manuales()
    total = 0
    for grupo in man.grupos_sin_colocar_raw():
        if grupo.get("ejecucion") != int(ejecucion_id):
            continue
        filas = man.filas_por_claves(grupo["claves"])
        total += sum(1 for c in grupo["claves"]
                     if c in filas and filas[c]["unassigned"] and c not in manuales)
    return total


def _sin_decision(man: Manifiesto, ruta_salida: str, ejecucion_id: int, emit) -> tuple[list, list]:
    """Lo que sigue sin colocar: `(sin_aplicar, avisos)`, con un `AVISO:` por cada uno."""
    recs = recomendar(man, ruta_salida, ejecucion_id, umbrales_desde_entorno())
    sin_aplicar, avisos = [], []
    for rec in recs:
        sin_aplicar.append(_resumen(rec))
        avisos.append(_aviso(rec, rec["explicacion"]))
    cubiertas = {c for rec in recs for c in rec["claves"]}
    manuales = man.asignaciones_manuales()
    for grupo in man.grupos_sin_colocar_raw():
        if grupo.get("ejecucion") != int(ejecucion_id):
            continue
        filas = man.filas_por_claves(grupo["claves"])
        huerfanas = [c for c in grupo["claves"]
                     if c in filas and filas[c]["unassigned"] and c not in manuales
                     and c not in cubiertas]
        if huerfanas:
            sin_aplicar.append({"grupo_id": grupo["grupo_id"], "motivo": grupo["motivo"],
                                "confianza": None, "explicacion": "sin recomendación",
                                "n_fotos": len(huerfanas)})
            avisos.append(f"AVISO: {len(huerfanas)} fotos del grupo {grupo['grupo_id']} quedan "
                          f"en SIN_ORDENAR sin decisión ({grupo['motivo']}: sin recomendación)")
    for aviso in avisos:
        emit(aviso)
    return sin_aplicar, avisos


def _aplicar_final(man: Manifiesto, ruta_salida: str, ejecucion_id: int, espera_s: float,
                   emit, parcial: list | None = None) -> dict:
    """Vence la espera sin respuesta: solo las de alta, firmadas como automáticas."""
    quien = (f"auto:recomendacion-alta (sin respuesta en {round(espera_s / 60)} min)@"
             + socket.gethostname())
    return aplicar_seguras(man, ruta_salida, ejecucion_id, quien, emit, parcial=parcial)


def _asignar(man: Manifiesto, ruta_salida: str, peticion: dict) -> dict:
    quien = peticion.get("quien")
    if not quien:
        raise ValueError("asignar: falta `quien` (firma de quien decide).")
    claves = list(dict.fromkeys(peticion.get("claves") or []))
    if not claves:
        raise sin_colocar.ErrorSinColocar("asignar: sin fotos que asignar.")
    por_grupo: dict[str, list[str]] = {}
    dueno = {c: g["grupo_id"] for g in man.grupos_sin_colocar_raw() for c in g["claves"]}
    ajenas = [c for c in claves if c not in dueno]
    if ajenas:
        raise sin_colocar.ErrorSinColocar(
            f"{len(ajenas)} foto(s) no están en ningún grupo sin colocar (p. ej. {ajenas[0]}).")
    for c in claves:
        por_grupo.setdefault(dueno[c], []).append(c)
    hechas = []
    for grupo_id, cs in por_grupo.items():
        sin_colocar.asignar_fotos(man, ruta_salida, grupo_id, cs, peticion.get("fila"), quien)
        hechas.append({"grupo_id": grupo_id, "n_fotos": len(cs), "fila": peticion.get("fila"),
                       "quien": quien})
    return {"aplicadas": hechas}


def _aceptar(man: Manifiesto, ruta_salida: str, ejecucion_id: int, peticion: dict) -> dict:
    quien = peticion.get("quien")
    if not quien:
        raise ValueError("aceptar: falta `quien` (firma de quien decide).")
    ids = list(dict.fromkeys(peticion.get("ids") or []))
    if not ids:
        raise sin_colocar.ErrorSinColocar("aceptar: sin recomendaciones que aceptar.")
    recs = {r["id"]: r for r in recomendar(man, ruta_salida, ejecucion_id,
                                           umbrales_desde_entorno())}
    desconocidas = [i for i in ids if i not in recs]
    if desconocidas:
        raise sin_colocar.ErrorSinColocar(
            f"recomendación(es) inexistente(s) o ya resueltas: {', '.join(desconocidas)}.")
    hechas = []
    for i in ids:
        rec = recs[i]
        sin_colocar.asignar_fotos(man, ruta_salida, rec["grupo_id"], rec["claves"], rec["fila"],
                                  quien)
        hechas.append({**_resumen(rec), "quien": quien})
    return {"aplicadas": hechas}


def pausar_si_armada(man: Manifiesto, cfg, ejecucion_id: int, emit, on_evento=None) -> dict | None:
    """Pausa del run tras el índice si hay fotos sin colocar y la revisión está armada.

    `None` si no está armada o no hay pendientes (sin evento). Si no, el HILO DEL RUN espera
    (`Event.wait` en pasos de 0,5 s con `cancelacion.comprobar()`), procesa aquí las peticiones
    de `enviar` (único escritor, con `man`) y termina con `continuar`, con la cancelación
    (`RunCancelado`, auditoría `cancelada`) o al vencer la inactividad: entonces aplica solo
    las de alta, firmadas como automáticas. Devuelve `{resultado, aplicadas, sin_aplicar}`."""
    with _LOCK:
        if not _ARMADA:
            return None
        espera_s, evento = _ESPERA_S, on_evento or _ON_EVENTO
    ruta = cfg.output_folder
    inicio = _hora()
    if _pendientes(man, ejecucion_id) == 0:
        guardar_auditoria(man, ejecucion_id, {
            "inicio": inicio, "fin": _hora(), "resultado": "sin_pendientes", "modo": "pausa",
            "espera_s": espera_s, "aplicadas": [], "dudosas": []})
        return None

    aplicadas: list[dict] = []
    errores: list[str] = []     # peticiones `continuar` fallidas (pueden dejar asignaciones a medias)
    resultado, sin_aplicar = "desatendida", []
    with _LOCK:
        _ESPERANDO.update(estado="esperando", ultima=time.monotonic(), espera_s=espera_s)
    _DESPIERTA.clear()
    try:
        emit(f"Revisión del índice · hay fotos sin colocar: se espera tu respuesta hasta "
             f"{round(espera_s / 60)} min sin actividad; si no, se aplican solo las de alta "
             "confianza y el resto queda en SIN_ORDENAR.")
        if evento is not None:
            try:
                evento({"tipo": "revision", "estado": "esperando", "espera_s": espera_s,
                        "deadline_epoch": estado()["deadline_epoch"]})
            except Exception as error:  # noqa: BLE001 - el aviso a la UI no tumba el run
                emit(f"ERROR: no se pudo avisar a la interfaz de la revisión del índice: {error}")
        while True:
            cancelacion.comprobar()
            if _DESPIERTA.wait(_PASO_S):
                _DESPIERTA.clear()
            cancelacion.comprobar()
            final = None
            while final is None:
                try:
                    peticion, futuro = _COLA.get_nowait()
                except queue.Empty:
                    break
                final = _procesar(man, ruta, ejecucion_id, peticion, futuro, aplicadas, emit,
                                  errores)
            if final is not None:
                resultado, sin_aplicar = "respondida", final
                break
            with _LOCK:
                vencida = time.monotonic() - _ESPERANDO["ultima"] >= espera_s
                if vencida:
                    _ESPERANDO["estado"] = "continuando"
            if vencida:
                nuevas: list[dict] = []
                try:
                    res = _aplicar_final(man, ruta, ejecucion_id, espera_s, emit, parcial=nuevas)
                finally:
                    aplicadas.extend(nuevas)    # lo ya escrito consta aunque falle a mitad
                resultado, sin_aplicar = "desatendida", res["sin_aplicar"]
                break
    except Exception as error:
        # Un fallo real NO es una cancelación: auditoría `error` con el motivo, y se propaga.
        _cerrar_pausa(man, ejecucion_id, inicio, "error", espera_s, aplicadas, [], emit,
                      motivo="la revisión terminó con un error", errores=errores,
                      error=f"{type(error).__name__}: {error}")
        raise
    except BaseException:       # RunCancelado, KeyboardInterrupt, SystemExit: salida pedida
        _cerrar_pausa(man, ejecucion_id, inicio, "cancelada", espera_s, aplicadas, [], emit,
                      errores=errores)
        raise
    _cerrar_pausa(man, ejecucion_id, inicio, resultado, espera_s, aplicadas, sin_aplicar, emit,
                  errores=errores)
    return {"resultado": resultado, "aplicadas": aplicadas, "sin_aplicar": sin_aplicar}


def _cerrar_pausa(man, ejecucion_id, inicio, resultado, espera_s, aplicadas, dudosas, emit,
                  motivo="la revisión del índice ya terminó", errores=None, error=None) -> None:
    """Sale de la pausa: deja de aceptar peticiones, falla las que quedaban y audita."""
    with _LOCK:
        _ESPERANDO["estado"] = None
        _fallar_pendientes(f"{motivo}: la petición no se aplicó.")
    datos = {"inicio": inicio, "fin": _hora(), "resultado": resultado, "modo": "pausa",
             "espera_s": espera_s, "aplicadas": aplicadas, "dudosas": dudosas}
    if error is not None:
        datos["error"] = error
    if errores:
        datos["errores"] = list(errores)
    try:
        guardar_auditoria(man, ejecucion_id, datos)
    except Exception as error:  # noqa: BLE001 - lo aplicado ya está escrito: se dice, no se tapa
        emit(f"ERROR: no se pudo guardar la auditoría de la revisión del índice: {error}")


def _procesar(man, ruta, ejecucion_id, peticion, futuro, aplicadas, emit,
              errores=None) -> list | None:
    """Una petición, en el hilo del run. Devuelve `sin_aplicar` si era `continuar` (fin de la
    pausa) y `None` si la espera sigue. Un fallo de la petición va a su Future, no al run."""
    with _LOCK:
        _ESPERANDO["estado"] = "aplicando"
        _ESPERANDO["ultima"] = time.monotonic()
    try:
        op = peticion.get("op")
        if op == "asignar":
            res = _asignar(man, ruta, peticion)
        elif op == "aceptar":
            res = _aceptar(man, ruta, ejecucion_id, peticion)
        elif op == "continuar":
            return _continuar(man, ruta, ejecucion_id, peticion, futuro, aplicadas, emit,
                              errores)
        else:
            raise ValueError(f"operación desconocida: {op!r} (asignar, aceptar o continuar).")
        aplicadas.extend(res["aplicadas"])
        futuro.set_result(res)
        return None
    except Exception as error:  # noqa: BLE001 - visible para quien lo pidió, el run sigue
        with _LOCK:
            _ESPERANDO["estado"] = "esperando"      # un `continuar` fallido no cierra la pausa
        if errores is not None and peticion.get("op") == "continuar":
            errores.append(f"continuar ({peticion.get('modo')}): {type(error).__name__}: {error}")
        futuro.set_exception(error)
        return None
    finally:
        with _LOCK:
            if _ESPERANDO["estado"] == "aplicando":
                _ESPERANDO["estado"] = "esperando"


def _continuar(man, ruta, ejecucion_id, peticion, futuro, aplicadas, emit,
               errores=None) -> list | None:
    modo_c = peticion.get("modo")
    if modo_c not in ("tal_cual", "con_alta"):
        raise ValueError(f"continuar: modo desconocido {modo_c!r} (tal_cual o con_alta).")
    with _LOCK:
        _ESPERANDO["estado"] = "continuando"
    if modo_c == "con_alta":
        nuevas: list[dict] = []
        try:
            res = aplicar_seguras(man, ruta, ejecucion_id, _firma_humana(), emit,
                                  origen="aceptada al continuar", parcial=nuevas)
        finally:
            aplicadas.extend(nuevas)    # lo ya escrito consta en la auditoría aunque falle
        sin_aplicar = res["sin_aplicar"]
    else:
        sin_aplicar, _avisos = _sin_decision(man, ruta, ejecucion_id, emit)
    futuro.set_result({"resultado": "respondida", "aplicadas": list(aplicadas),
                       "sin_aplicar": sin_aplicar})
    return sin_aplicar
