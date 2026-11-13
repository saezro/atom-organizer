"""Calendario de la revisión del índice: el JSON del spec §5 que pinta el modal.

Solo lectura del manifiesto del destino. Las horas son hora EXIF del dron, sin zona
(`ZONA_HORARIA = "exif-local"`): nunca se convierten. Las cubetas por hora salen de
`imagenes` (`substr(timestamp_exif, 1, 13)`) agrupadas por (pb, vuelo, tipo, giro) de lo ya
colocado, para que el front pinte un histograma por hora y no una marca por foto.
"""
from __future__ import annotations

import datetime

from atom_core import revision_indice, sin_colocar
from atom_core.manifiesto import Manifiesto
from atom_core.recomendaciones_sin_colocar import recomendar, umbrales_desde_entorno
from atom_core.sin_colocar import ZONA_HORARIA, ErrorSinColocar

_TIPOS_RGB = ("RGB", "RGB_Extra")
_TIPO_T = "TERMICA"


def _hora_de(fecha_iso: str | None) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(fecha_iso) if fecha_iso else None
    except (TypeError, ValueError):
        return None


def _fichas(man: Manifiesto, ejecucion_id: int) -> list[dict]:
    import json  # noqa: PLC0415
    return [json.loads(r["datos"]) for r in man._conexion().execute(
        "SELECT datos FROM filas_estadillo WHERE ejecucion = ? ORDER BY fila", (int(ejecucion_id),))]


def _ficha_de_hora(candidatas: list[dict], hora: str) -> dict | None:
    """Ficha a la que pertenece una cubeta `YYYY-MM-DDTHH`: la única cuya ventana
    `[inicio, fin)` toca esa hora (`[hora, hora+1h)`). Cero o varias: `None` (ambiguo)."""
    inicio_h = _hora_de(hora + ":00:00")
    if inicio_h is None:
        return None
    fin_h = inicio_h + datetime.timedelta(hours=1)
    tocan = []
    for f in candidatas:
        ini, fin = _hora_de(f.get("inicio")), _hora_de(f.get("fin"))
        if ini is not None and fin is not None and ini < fin_h and fin > inicio_h:
            tocan.append(f)
    return tocan[0] if len(tocan) == 1 else None


def _vuelos_con_horas(man: Manifiesto, fichas: list[dict], ejecucion_id: int) -> tuple[list[dict], list[dict]]:
    """`(vuelos, horas_sin_fila)`. `horas_sin_fila` son cubetas de un PB/vuelo con varias filas
    en el estadillo cuya hora no decide la fila: se devuelven aparte, nunca se adivina."""
    angulos = man.vuelos()
    por_vuelo: dict[tuple, list[dict]] = {}
    for f in fichas:
        por_vuelo.setdefault((str(f["pb"]), str(f["vuelo"])), []).append(f)
    vuelos = {f["fila"]: {
        "fila": f["fila"], "pb": f["pb"], "vuelo": f["vuelo"], "sufijo": f.get("sufijo"),
        "fecha": f.get("fecha"), "inicio": f.get("inicio"), "fin": f.get("fin"),
        "generales": bool(f.get("generales")),
        "angulo": (angulos.get((f["pb"], f["vuelo"], f.get("sufijo") or None)) or {}).get("angulo"),
        "horas": {}, "n_total": 0, "n_giro_desconocido": 0} for f in fichas}
    sin_fila: dict[tuple, dict] = {}

    def cubeta(destino: dict, hora: str) -> dict:
        return destino.setdefault(hora, {"hora": hora, "n_rgb": 0, "n_t": 0, "n_otro": 0,
                                         "n_90": 0, "n_270": 0, "n_0": 0,
                                         "n_giro_desconocido": 0})

    filas = man._conexion().execute(
        "SELECT pb, vuelo, substr(timestamp_exif, 1, 13) AS hora, tipo, giro_voto, "
        "COUNT(*) AS n FROM imagenes WHERE unassigned = 0 AND pb IS NOT NULL "
        "AND vuelo IS NOT NULL AND timestamp_exif IS NOT NULL AND ejecucion_id = ? "
        "GROUP BY pb, vuelo, hora, tipo, giro_voto", (int(ejecucion_id),)).fetchall()
    for r in filas:
        candidatas = por_vuelo.get((str(r["pb"]), str(r["vuelo"])))
        if not candidatas or not r["hora"]:
            continue    # foto de otra tanda/ejecución sin fila en este estadillo
        ficha = _ficha_de_hora(candidatas, r["hora"])
        if ficha is None:
            destino, vuelo = sin_fila, None
            cub = cubeta(destino.setdefault((r["pb"], r["vuelo"]), {}), r["hora"])
        else:
            vuelo = vuelos[ficha["fila"]]
            cub = cubeta(vuelo["horas"], r["hora"])
        n = int(r["n"])
        campo = ("n_rgb" if r["tipo"] in _TIPOS_RGB else "n_t" if r["tipo"] == _TIPO_T
                 else "n_otro")
        cub[campo] += n
        if r["giro_voto"] in (90, 270, 0):
            cub[f"n_{r['giro_voto']}"] += n
        else:       # sin voto o voto fuera de 0/90/270: se cuenta aparte, nunca se descarta
            cub["n_giro_desconocido"] += n
            if vuelo is not None:
                vuelo["n_giro_desconocido"] += n
        if vuelo is not None:
            vuelo["n_total"] += n
    salida = []
    for v in vuelos.values():
        v["horas"] = [v["horas"][h] for h in sorted(v["horas"])]
        salida.append(v)
    ambiguas = [{"pb": pb, "vuelo": vuelo, "horas": [c[h] for h in sorted(c)],
                 "aviso": "la hora no cae en la ventana de una única fila del estadillo de ese "
                          "PB/vuelo: no se asigna a ninguna fila"}
                for (pb, vuelo), c in sorted(sin_fila.items())]
    return salida, ambiguas


def construir(man: Manifiesto, ruta_salida: str, ejecucion_id: int) -> dict:
    manuales = man.asignaciones_manuales()
    recs = recomendar(man, ruta_salida, ejecucion_id, umbrales_desde_entorno())
    rec_de: dict[str, str] = {}
    for r in recs:
        for c in r["claves"]:
            rec_de.setdefault(c, r["id"])
    grupos, libres = [], []
    for grupo in man.grupos_sin_colocar_raw():
        if grupo.get("ejecucion") != int(ejecucion_id):
            continue
        decision = sin_colocar._decision(grupo, manuales)
        salida = {k: v for k, v in grupo.items() if k not in ("claves", "fotos_giro")}
        salida["decision"] = decision
        salida["estado"] = ("pendiente" if decision is None else
                            "sin_ordenar" if decision["fila_estadillo"] is None else "asignado")
        grupos.append(salida)
        filas = man.filas_por_claves(grupo["claves"])
        for clave in grupo["claves"]:
            f = filas.get(clave)
            if f is None or not (f["unassigned"] or clave in manuales):
                continue
            m = manuales.get(clave)
            libres.append({
                "clave": clave, "nombre": f["nombre_original"], "tipo": f["tipo"],
                "ts": f["timestamp_exif"], "grupo_id": grupo["grupo_id"],
                "motivo": grupo["motivo"], "recomendacion": rec_de.get(clave),
                "decision": None if m is None else {"fila": m["fila"], "quien": m["quien"],
                                                    "cuando": m["cuando"]}})
    vuelos, horas_sin_fila = _vuelos_con_horas(man, _fichas(man, ejecucion_id), ejecucion_id)
    return {
        "ok": True, "zona_horaria": ZONA_HORARIA, "ejecucion": int(ejecucion_id),
        "espera": revision_indice.estado(),
        "vuelos": vuelos, "horas_sin_fila": horas_sin_fila, "libres": libres, "grupos": grupos,
        "recomendaciones": [
            {**{k: v for k, v in r.items() if k != "claves"}, "n_fotos": len(r["claves"])}
            for r in recs]}


def calendario(ruta_salida: str, ejecucion_id: int | None = None) -> dict:
    """JSON del calendario del destino `ruta_salida` (local o `gs://`). Sin `ejecucion_id`,
    la última del manifiesto. Sin manifiesto o sin ejecución: `ErrorSinColocar`."""
    with sin_colocar._abrir(ruta_salida, escribe=False) as man:
        ejecucion = ejecucion_id if ejecucion_id is not None else man.ultima_ejecucion_id()
        if ejecucion is None:
            raise ErrorSinColocar(f"el manifiesto de {ruta_salida} no tiene ninguna ejecución.")
        resultado = construir(man, ruta_salida, int(ejecucion))
    resultado["destino"] = ruta_salida
    return resultado
