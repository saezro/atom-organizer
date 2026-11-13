"""Recomendaciones de colocación, con confianza, para las fotos sin colocar.

Lógica pura y de SOLO LECTURA: nunca escribe en el manifiesto (ni `vuelos`, ni
`asignaciones_manuales`). Quien aplica (revisión del índice) decide qué hacer con cada
recomendación; aquí solo se puntúa. Criterio de «alta confianza» (spec §7):

Vecino (grupo `fuera-de-ventana`):
- el vecino más cercano está a <= `Umbrales.alta_s`;
- el otro vecino no existe o está a >= `otro_min_s` o a >= `factor_otro` veces esa distancia;
- la fila destino no es de generales;
- el giro no contradice FlightYaw;
- la foto no tiene otra recomendación que apunte a otra fila.

Par partido (la pareja de la misma toma quedó colocada y esta no):
- la pareja está en un vuelo asignado por el índice (no a mano);
- sin conflicto con otra recomendación.

Par partido ambiguo (varias filas PB/vuelo y ninguna contiene la hora de la pareja): no se
recomienda por par partido (no se adivina la fila); queda el vecino si lo hay.

Sin segundo vecino (`otro is None`) la confianza del vecino puede ser alta: el otro "no existe".

Nunca alta: motivos `sin-fecha-exif`, `dia-sin-estadillo`, `estadillo-sin-fecha-legible`, ni
dos vecinos dentro del umbral (baja). Las horas son hora EXIF del dron (`exif-local`).
"""
from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass

from atom_core import pares as pares_mod
from atom_core import sin_colocar
from atom_core.manifiesto import Manifiesto
from atom_core.sin_colocar import (
    MOTIVOS_CON_VECINOS, SEGUNDOS_MAX_TOMA, ErrorSinColocar, angulo_propuesto)

ENV_ALTA_S = "ATOM_REVISION_ALTA_S"
_ORDEN = {"baja": 0, "media": 1, "alta": 2}


@dataclass(frozen=True)
class Umbrales:
    alta_s: float = 120.0
    otro_min_s: float = 600.0
    factor_otro: float = 5.0


def umbrales_desde_entorno(env=os.environ) -> Umbrales:
    """`ATOM_REVISION_ALTA_S` sobre el umbral por defecto. Ilegible o <= 0 es un error."""
    bruto = env.get(ENV_ALTA_S)
    if bruto is None or str(bruto).strip() == "":
        return Umbrales()
    try:
        alta = float(bruto)
    except (TypeError, ValueError):
        raise ValueError(f"{ENV_ALTA_S} ilegible: {bruto!r} (segundos, > 0)") from None
    if not alta > 0:
        raise ValueError(f"{ENV_ALTA_S} inválido: {bruto!r} (segundos, > 0)")
    return Umbrales(alta_s=alta)


def _tiempo(segundos: float) -> str:
    return f"{round(segundos)} s" if segundos < 120 else f"{round(segundos / 60)} min"


def _tope(actual: str, maximo: str) -> str:
    return actual if _ORDEN[actual] <= _ORDEN[maximo] else maximo


def _es_generales(ficha: dict) -> bool:
    from atom_core import indice as ind  # noqa: PLC0415 - evita ciclo
    return bool(ficha.get("generales") or ind._es_pb_generales(ficha.get("pb")))


def _confianza_vecino(cerca: dict, otro: dict | None, u: Umbrales) -> str:
    d = cerca["distancia_s"]
    if d > u.alta_s:
        return "media"
    if otro is None:
        return "alta"
    d2 = otro["distancia_s"]
    if d2 <= u.alta_s:
        return "baja"       # ambos vecinos dentro del umbral: no hay forma de decidir
    if d2 >= u.otro_min_s or d2 >= u.factor_otro * d:
        return "alta"
    return "media"


def _fichas_de(man: Manifiesto, ejecucion: int) -> list[dict]:
    return [json.loads(r["datos"]) for r in man._conexion().execute(
        "SELECT datos FROM filas_estadillo WHERE ejecucion = ? ORDER BY fila", (int(ejecucion),))]


def _ts(fila) -> datetime.datetime | None:
    return (datetime.datetime.fromisoformat(fila["timestamp_exif"])
            if fila["timestamp_exif"] else None)


def _parejas_por_toma(man: Manifiesto) -> dict[tuple, list]:
    """`{(carpeta_origen, idx_toma): [filas ya colocadas]}`."""
    indice: dict[tuple, list] = {}
    for f in man.todas():
        if f["unassigned"]:
            continue
        idx = pares_mod.idx_toma(f["nombre_original"])
        if idx is not None:
            indice.setdefault((os.path.dirname(f["ruta_origen"]), idx), []).append(f)
    return indice


def _instante(valor) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(valor) if valor else None
    except (TypeError, ValueError):
        return None


def _ficha_de_pareja(fichas: list[dict], pareja) -> dict | None:
    """Fila del estadillo del vuelo de la pareja (PB/vuelo). Una sola candidata es esa; si hay
    varias, la que contiene la hora de la pareja. Sin hora que desempate (o ninguna la contiene)
    es ambiguo: `None`, no se adivina la fila. Fichas con inicio/fin vacíos o ilegibles se saltan."""
    candidatas = [f for f in fichas if f["pb"] == pareja["pb"] and f["vuelo"] == pareja["vuelo"]]
    if len(candidatas) <= 1:
        return candidatas[0] if candidatas else None
    ts = _ts(pareja)
    if ts is None:
        return None
    dentro = []
    for f in candidatas:
        ini, fin = _instante(f.get("inicio")), _instante(f.get("fin"))
        if ini is not None and fin is not None and ini <= ts <= fin:
            dentro.append(f)
    return dentro[0] if len(dentro) == 1 else None


def _recomendacion(grupo: dict, ficha: dict, claves: list[str], motivo: str, distancia: float,
                   confianza: str, explicacion: str) -> dict:
    return {"id": f"{grupo['grupo_id']}:{ficha['fila']}:{motivo}", "grupo_id": grupo["grupo_id"],
            "claves": list(claves), "fila": ficha["fila"], "pb": ficha["pb"],
            "vuelo": ficha["vuelo"], "motivo": motivo, "distancia_s": round(distancia, 3),
            "confianza": confianza, "explicacion": explicacion, "_grupo": grupo, "_ficha": ficha}


def recomendar(man: Manifiesto, ruta_salida: str, ejecucion_id: int,
               umbrales: Umbrales) -> list[dict]:
    """Recomendaciones para los grupos pendientes de `ejecucion_id`. Ver el docstring del
    módulo. Orden estable: `grupo_id`, luego `fila`. No escribe."""
    manuales = man.asignaciones_manuales()
    parejas = _parejas_por_toma(man)
    fichas_de: dict[int, list[dict]] = {}
    recs: list[dict] = []

    for grupo in man.grupos_sin_colocar_raw():
        if grupo.get("ejecucion") != int(ejecucion_id):
            continue
        filas = man.filas_por_claves(grupo["claves"])
        pendientes = [c for c in grupo["claves"]
                      if c in filas and filas[c]["unassigned"] and c not in manuales]
        if not pendientes:
            continue
        con_vecinos = grupo["motivo"] in MOTIVOS_CON_VECINOS
        fichas = fichas_de.setdefault(grupo["ejecucion"], _fichas_de(man, grupo["ejecucion"]))

        # Par partido: la pareja de la misma toma (<= 5 s, misma carpeta) ya está colocada.
        por_fila: dict[int, dict] = {}
        for clave in pendientes:
            f = filas[clave]
            ts, idx = _ts(f), pares_mod.idx_toma(f["nombre_original"])
            if ts is None or idx is None:
                continue
            for p in parejas.get((os.path.dirname(f["ruta_origen"]), idx), []):
                ts_p = _ts(p)
                if ts_p is None or abs((ts_p - ts).total_seconds()) > SEGUNDOS_MAX_TOMA:
                    continue
                ficha = _ficha_de_pareja(fichas, p)
                if ficha is None:
                    continue
                acum = por_fila.setdefault(ficha["fila"], {"ficha": ficha, "claves": [],
                                                           "d": [], "manual": False})
                if clave not in acum["claves"]:
                    acum["claves"].append(clave)
                acum["d"].append(abs((ts_p - ts).total_seconds()))
                acum["manual"] = acum["manual"] or p["clave"] in manuales
        for fila, acum in sorted(por_fila.items()):
            ficha = acum["ficha"]
            base = "media" if acum["manual"] else "alta"
            if acum["manual"]:
                texto = (f"par partido: su pareja de la misma toma está en PB{ficha['pb']}/"
                         f"V{ficha['vuelo']}, colocada a mano")
            else:
                texto = (f"par partido: su pareja de la misma toma está en PB{ficha['pb']}/"
                         f"V{ficha['vuelo']}")
            recs.append(_recomendacion(grupo, ficha, acum["claves"], "par-partido",
                                       min(acum["d"]), base, texto))

        # Vecino más cercano, para lo que no cubre un par partido a esa misma fila.
        anterior, siguiente = grupo.get("vecino_anterior"), grupo.get("vecino_siguiente")
        vecinos = [("el anterior", anterior), ("el siguiente", siguiente)]
        vecinos = [(n, v) for n, v in vecinos if v is not None]
        if con_vecinos and vecinos:
            vecinos.sort(key=lambda nv: nv[1]["distancia_s"])
            (_n, cerca), resto = vecinos[0], (vecinos[1] if len(vecinos) > 1 else None)
            ficha = man.fila_estadillo(grupo["ejecucion"], cerca["fila"])
            if ficha is None:
                raise ErrorSinColocar(
                    f"la fila {cerca['fila']} del grupo {grupo['grupo_id']} no existe en el "
                    f"estadillo de la ejecución {grupo['ejecucion']}.")
            ya = set(por_fila.get(cerca["fila"], {}).get("claves", []))
            claves = [c for c in pendientes if c not in ya]
            if claves:
                confianza = _confianza_vecino(cerca, resto[1] if resto else None, umbrales)
                texto = f"vecino PB{cerca['pb']}/V{cerca['vuelo']} a {_tiempo(cerca['distancia_s'])}"
                texto += (f"; {resto[0]} a {_tiempo(resto[1]['distancia_s'])}" if resto
                          else "; no hay otro vecino")
                recs.append(_recomendacion(grupo, ficha, claves, "vecino",
                                           cerca["distancia_s"], confianza, texto))

    # Topes que valen para todas: motivo, generales, giro.
    for r in recs:
        grupo, ficha = r["_grupo"], r["_ficha"]
        if grupo["motivo"] not in MOTIVOS_CON_VECINOS:
            r["confianza"] = _tope(r["confianza"], "media")
            r["explicacion"] += f"; motivo {grupo['motivo']}: nunca se aplica sola"
        if _es_generales(ficha):
            r["confianza"] = _tope(r["confianza"], "media")
            r["explicacion"] += "; fila de generales"
        else:
            from atom_core import indice as ind  # noqa: PLC0415 - evita ciclo
            try:
                angulo_propuesto(man, ruta_salida, ficha, grupo, r["claves"])
            except (ErrorSinColocar, ind.ErrorGiroContradictorio) as error:
                r["confianza"] = "baja"
                r["explicacion"] += f"; giro no verificado: {error}"

    # Conflicto: una misma foto con recomendaciones a filas distintas.
    filas_de_foto: dict[str, set[int]] = {}
    for r in recs:
        for c in r["claves"]:
            filas_de_foto.setdefault(c, set()).add(r["fila"])
    for r in recs:
        if any(len(filas_de_foto[c]) > 1 for c in r["claves"]):
            r["confianza"] = "baja"
            r["explicacion"] += "; conflicto: la misma foto tiene otra recomendación a otra fila"

    for r in recs:
        del r["_grupo"], r["_ficha"]
    recs.sort(key=lambda r: (r["grupo_id"], r["fila"], r["motivo"]))
    return recs
