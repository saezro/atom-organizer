"""Revisión del índice: coordina qué se hace con las fotos sin colocar al acabar el índice.

Este módulo solo tiene el flag, el modo, la aplicación de lo seguro y la auditoría (sin pausa
todavía). Modos:
- `pausa`: el proceso de la app (escritorio, Pi/kiosco, control remoto) armó la revisión con
  `armar()` y habrá canal de respuesta.
- `inmediato`: nube y CLI local sin UI: las seguras (confianza alta) se aplican al momento y las
  demás quedan en SIN_ORDENAR con aviso visible.
- `apagado`: comportamiento anterior, sin cambios.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Callable, Literal

from atom_core import sin_colocar
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
                    emit) -> dict:
    """Aplica solo las recomendaciones de confianza `alta`, con el MISMO manifiesto del run
    (nunca abre otro). Una línea por aplicada y un `AVISO:` por cada no aplicada."""
    origen = _origen_de(quien)
    recs = recomendar(man, ruta_salida, ejecucion_id, umbrales_desde_entorno())
    aplicadas: list[dict] = []
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


def _clave_auditoria(ejecucion_id: int) -> str:
    return f"revision_indice:{int(ejecucion_id)}"


def guardar_auditoria(man: Manifiesto, ejecucion_id: int, datos: dict) -> None:
    man.guardar_ajuste(_clave_auditoria(ejecucion_id), json.dumps(datos, ensure_ascii=False))


def leer_auditoria(man: Manifiesto, ejecucion_id: int) -> dict | None:
    valor = man.leer_ajuste(_clave_auditoria(ejecucion_id))
    return None if valor is None else json.loads(valor)
