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
        return bool(bloque["activa"])
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
    sin_aplicar: list[dict] = []
    avisos: list[str] = []
    for rec in recs:
        if rec["confianza"] != "alta":
            sin_aplicar.append(_resumen(rec))
            avisos.append(_aviso(rec, rec["explicacion"]))
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
        aplicadas.append(_resumen(rec))
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
    return {"aplicadas": aplicadas, "sin_aplicar": sin_aplicar, "avisos": avisos}


def _clave_auditoria(ejecucion_id: int) -> str:
    return f"revision_indice:{int(ejecucion_id)}"


def guardar_auditoria(man: Manifiesto, ejecucion_id: int, datos: dict) -> None:
    man.guardar_ajuste(_clave_auditoria(ejecucion_id), json.dumps(datos, ensure_ascii=False))


def leer_auditoria(man: Manifiesto, ejecucion_id: int) -> dict | None:
    valor = man.leer_ajuste(_clave_auditoria(ejecucion_id))
    return None if valor is None else json.loads(valor)
