"""Fotos fuera de rango del estadillo: grupos, listado y colocación manual.

Las imágenes que no caen en ninguna ventana del estadillo van a `SIN_ORDENAR/<tipo>`
(ver `indice.construir_indice`). Aquí se agrupan en bloques contiguos por hora EXIF
para que el usuario vea EN QUÉ PARTE del estadillo se quedaron sin colocar (fila
anterior y siguiente) y decida dónde van:

- `grupos_sin_colocar(ruta_salida)`: lista los grupos que dejó el índice.
- `asignar_grupo(ruta_salida, grupo_id, fila_estadillo, quien=...)`: asigna TODAS las
  fotos del grupo a una fila del estadillo (o las deja en SIN_ORDENAR a propósito con
  `None`). El apply posterior las coloca con la misma lógica de nombres, recorte
  (`_CROP`, ajuste `camara_rgb`) y giro que las automáticas.

HORAS: el motor trabaja en la hora EXIF del dron tal cual (datetime sin zona). Las
ventanas del estadillo ya llevan aplicado el desfase `mismatch_hours/minutes`, así que
se comparan directamente con el EXIF. NO hay conversión a Europe/Madrid en el motor: las
horas de los grupos y de los vecinos van en esa hora EXIF (`zona_horaria: "exif-local"`).
Solo `cuando` (momento de la asignación manual) va en Europe/Madrid.

Persistencia (tablas del manifiesto, viajan con él a `gs://`):
`filas_estadillo`, `grupos_sin_colocar`, `asignaciones_manuales` (por foto, clave de
imagen). El índice reaplica las asignaciones manuales en cada re-índice y tanda
posterior, así que no se pisan.
"""
from __future__ import annotations

import contextlib
import datetime
import hashlib
import os
from dataclasses import fields, replace
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from atom_core import almacen
from atom_core import pares as pares_mod
from atom_core.almacen import es_uri_gcs
from atom_core.manifiesto import (
    NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO, FilaManifiesto, Manifiesto,
    clave_imagen, nombre_de_ruta)

HUECO_DEFECTO_S = 120.0
ENV_HUECO = "ATOM_SIN_COLOCAR_HUECO_S"
# Misma tolerancia que la pareja T/W del índice (`indice._SEGUNDOS_MAX_PAREJA_TW`).
SEGUNDOS_MAX_TOMA = 5.0
ZONA_HORARIA = "exif-local"
AJUSTE_CRITERIO_GIRO = "criterio_giro"
CAMPOS_CRITERIO_GIRO = ("choose_mode_auto", "gen_thumbnails_rotate_90",
                        "gen_thumbnails_add_to_angle", "gen_thumbnails_subs_to_angle",
                        "gen_thumbnails_max_error", "include_v")
# Solo "fuera-de-ventana" tiene sentido con vecinos: es un hueco dentro de un día CON estadillo.
MOTIVOS_CON_VECINOS = ("fuera-de-ventana",)
_MADRID = ZoneInfo("Europe/Madrid")


class ErrorSinColocar(Exception):
    """Error explícito de grupos/asignaciones manuales (grupo o fila inexistente, estado
    incompatible…). Nunca se corrige en silencio."""


def hueco_segundos(valor=None) -> float:
    """Hueco máximo (s) entre fotos de un mismo grupo: argumento > `$ATOM_SIN_COLOCAR_HUECO_S`
    > 120. Un valor ilegible o <= 0 es un error."""
    bruto = valor if valor is not None else os.environ.get(ENV_HUECO)
    if bruto is None or str(bruto).strip() == "":
        return HUECO_DEFECTO_S
    try:
        hueco = float(bruto)
    except (TypeError, ValueError):
        raise ValueError(f"hueco de grupo ilegible: {bruto!r} (segundos, > 0)") from None
    if not hueco > 0:
        raise ValueError(f"hueco de grupo inválido: {bruto!r} (segundos, > 0)")
    return hueco


def _iso(ts) -> str | None:
    return ts.isoformat() if ts is not None else None


def _dt(texto: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(texto)


def ficha_ventana(fila: int, ventana: dict, carpeta_vuelo: str | None, generales: bool) -> dict:
    """Fila del estadillo tal y como se guarda (JSON): destino válido de una asignación."""
    return {"fila": int(fila), "pb": ventana["pb"], "vuelo": ventana["vuelo"],
            "sufijo": ventana.get("sufijo"), "fecha": ventana.get("fecha"),
            "inicio": _iso(ventana["inicio"]), "fin": _iso(ventana["fin"]),
            "carpeta_vuelo": carpeta_vuelo, "generales": bool(generales),
            "equipo": ventana.get("equipo")}


def firma_ficha(ficha: dict) -> dict:
    """Identidad de una fila del estadillo independiente de su número (que cambia si cambia
    el estadillo): es lo que reconoce el re-índice."""
    return {"pb": ficha["pb"], "vuelo": ficha["vuelo"], "sufijo": ficha.get("sufijo") or "",
            "inicio": ficha["inicio"], "fin": ficha["fin"], "fecha": ficha.get("fecha")}


def _vecino(ficha: dict, distancia: float) -> dict:
    return {"fila": ficha["fila"], "pb": ficha["pb"], "vuelo": ficha["vuelo"],
            "fecha": ficha.get("fecha"), "hora_inicio": ficha["inicio"],
            "hora_fin": ficha["fin"], "distancia_s": round(distancia, 3)}


def _vecinos(fichas: list[dict], inicio: datetime.datetime, fin: datetime.datetime):
    anterior = siguiente = None
    for f in fichas:
        f_ini, f_fin = _dt(f["inicio"]), _dt(f["fin"])
        if f_fin < inicio and (anterior is None or (f_fin, -f["fila"]) > (anterior[0], -anterior[1]["fila"])):
            anterior = (f_fin, f)
        if f_ini > fin and (siguiente is None or (f_ini, f["fila"]) < (siguiente[0], siguiente[1]["fila"])):
            siguiente = (f_ini, f)
    return (_vecino(anterior[1], (inicio - anterior[0]).total_seconds()) if anterior else None,
            _vecino(siguiente[1], (siguiente[0] - fin).total_seconds()) if siguiente else None)


def _id_grupo(claves: list[str]) -> str:
    return "g-" + hashlib.sha1("\n".join(sorted(claves)).encode("utf-8")).hexdigest()[:12]


def _grupo(fotos: list[dict], motivo: str, fichas: list[dict], hueco_s: float,
           ejecucion: int) -> dict:
    claves = [f["clave"] for f in fotos]
    por_tipo: dict[str, int] = {}
    for f in fotos:
        por_tipo[f["tipo"]] = por_tipo.get(f["tipo"], 0) + 1
    tiempos = [f["ts"] for f in fotos if f["ts"] is not None]
    inicio, fin = (min(tiempos), max(tiempos)) if tiempos else (None, None)
    anterior = siguiente = None
    if tiempos and motivo in MOTIVOS_CON_VECINOS:
        anterior, siguiente = _vecinos(fichas, inicio, fin)
    return {
        "grupo_id": _id_grupo(claves), "ejecucion": int(ejecucion), "motivo": motivo,
        "zona_horaria": ZONA_HORARIA, "hora_inicio": _iso(inicio), "hora_fin": _iso(fin),
        "n_fotos": len(fotos), "fotos_por_tipo": dict(sorted(por_tipo.items())),
        "hueco_s": hueco_s, "vecino_anterior": anterior, "vecino_siguiente": siguiente,
        "claves": claves,
        # Entradas del consenso de giro por foto (yaw, pitch, flight_yaw), para poder
        # decidir el ángulo al colocar el grupo sin releer los ficheros.
        "fotos_giro": {f["clave"]: f["giro"] for f in fotos if f.get("giro")},
    }


def agrupar(fotos: list[dict], ambito_de, fichas: list[dict], hueco_s: float,
            ejecucion: int) -> list[dict]:
    """Agrupa las fotos sin colocar. `fotos`: dicts `{clave, ruta, nombre, tipo, ts, motivo,
    giro}`. `ambito_de(ruta)` -> `(clave_ambito, [fila,...])`: las filas del estadillo contra
    las que compitió esa foto (scoping por carpeta del índice). Contiguos por hora EXIF:
    corte cuando el hueco SUPERA `hueco_s`, cambia el motivo o el ámbito, o una ventana del
    estadillo cae entre dos fotos. T/W/Z de una toma (mismo idx DJI, <= 5 s) no se separan."""
    por_fila = {f["fila"]: f for f in fichas}
    grupos: list[dict] = []

    # Sin hora EXIF no hay contigüidad: un grupo por carpeta de origen.
    sin_hora: dict[str, list[dict]] = {}
    for f in fotos:
        if f["ts"] is None:
            sin_hora.setdefault(os.path.dirname(f["ruta"]), []).append(f)
    for _carpeta, lista in sorted(sin_hora.items()):
        grupos.append(_grupo(lista, "sin-fecha-exif", [], hueco_s, ejecucion))

    tomas: list[dict] = []
    abiertas: dict[tuple, dict] = {}
    for f in sorted((f for f in fotos if f["ts"] is not None), key=lambda f: (f["ts"], f["nombre"])):
        idx = pares_mod.idx_toma(f["nombre"])
        k = (os.path.dirname(f["ruta"]), idx) if idx is not None else None
        toma = abiertas.get(k) if k else None
        if toma is not None and (f["ts"] - toma["ini"]).total_seconds() <= SEGUNDOS_MAX_TOMA:
            toma["fotos"].append(f)
            toma["fin"] = max(toma["fin"], f["ts"])
            continue
        ambito, filas = ambito_de(f["ruta"])
        toma = {"fotos": [f], "ini": f["ts"], "fin": f["ts"], "motivo": f["motivo"],
                "ambito": ambito, "filas": filas}
        tomas.append(toma)
        if k:
            abiertas[k] = toma

    actual: dict | None = None

    def cerrar() -> None:
        if actual is not None:
            fichas_ambito = [por_fila[i] for i in actual["filas"] if i in por_fila]
            grupos.append(_grupo(actual["fotos"], actual["motivo"], fichas_ambito, hueco_s,
                                 ejecucion))

    for toma in tomas:
        if actual is not None:
            hueco = (toma["ini"] - actual["fin"]).total_seconds()
            corta = (toma["motivo"] != actual["motivo"] or toma["ambito"] != actual["ambito"]
                     or hueco > hueco_s)
            if not corta and toma["motivo"] in MOTIVOS_CON_VECINOS:
                corta = any(_dt(por_fila[i]["inicio"]) < toma["ini"]
                            and _dt(por_fila[i]["fin"]) > actual["fin"]
                            for i in toma["filas"] if i in por_fila)
            if corta:
                cerrar()
                actual = None
        if actual is None:
            actual = {"fotos": [], "fin": toma["fin"], "motivo": toma["motivo"],
                      "ambito": toma["ambito"], "filas": toma["filas"]}
        actual["fotos"].extend(toma["fotos"])
        actual["fin"] = max(actual["fin"], toma["fin"])
    cerrar()
    return grupos


def registrar_tanda(manifiesto: Manifiesto, ejecucion_id, fichas: list[dict], fotos: list[dict],
                    ambito_de, hueco_s: float | None = None) -> dict:
    """Llamada del índice: agrupa las fotos sin colocar de la tanda y las persiste en el
    manifiesto (junto a las filas del estadillo). Devuelve `{"grupos", "fotos"}`."""
    hueco = hueco_segundos(hueco_s)
    ejecucion = int(ejecucion_id or 0)
    grupos = agrupar(fotos, ambito_de, fichas, hueco, ejecucion)
    manifiesto.guardar_sin_colocar(ejecucion, fichas, grupos)
    return {"grupos": len(grupos), "fotos": len(fotos)}


# --- Acceso al manifiesto del destino -------------------------------------------

@contextlib.contextmanager
def _abrir(ruta_salida: str, escribe: bool):
    """Manifiesto del destino (local o `gs://`, con subida condicional por generación si
    `escribe` y sin error). Sin manifiesto: `ErrorSinColocar`."""
    if es_uri_gcs(ruta_salida):
        import pipeline  # noqa: PLC0415 - pesado; solo con destino gs://
        with pipeline._LOCK_MANIFIESTO_GCS:
            local, generacion = pipeline._sincronizar_manifiesto_gcs(ruta_salida)
        if generacion == 0:
            pipeline._limpiar_copia_local_manifiesto_gcs(local)
            raise ErrorSinColocar(f"no hay manifiesto en {ruta_salida}: el índice no ha corrido.")
        manifiesto = Manifiesto(local)
        try:
            manifiesto.crear_esquema()
            yield manifiesto
            manifiesto.cerrar()
            if escribe:
                pipeline._subir_manifiesto_gcs(ruta_salida, local, generacion)
        finally:
            manifiesto.cerrar()
            pipeline._limpiar_copia_local_manifiesto_gcs(local)
        return
    ruta_db = os.path.join(ruta_salida, NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO)
    if not os.path.isfile(ruta_db):
        raise ErrorSinColocar(f"no hay manifiesto en {ruta_db}: el índice no ha corrido.")
    manifiesto = Manifiesto(ruta_db)
    try:
        manifiesto.crear_esquema()
        yield manifiesto
    finally:
        manifiesto.cerrar()


def _decision(grupo: dict, asignaciones: dict[str, dict]) -> dict | None:
    """Decisión del usuario sobre el grupo si TODAS sus fotos tienen la misma, o None."""
    previas = [asignaciones.get(c) for c in grupo["claves"]]
    if not previas or any(p is None for p in previas):
        return None
    primera = previas[0]
    if any(p["fila"] != primera["fila"] for p in previas):
        return None
    return {"fila_estadillo": primera["fila"], "quien": primera["quien"],
            "cuando": primera["cuando"]}


def grupos_sin_colocar(ruta_salida: str, *, solo_pendientes: bool = True,
                       incluir_fotos: bool = False) -> list[dict]:
    """Grupos de fotos sin colocar del destino, en el orden del índice. `solo_pendientes`
    excluye los ya decididos (asignados a una fila o dejados en SIN_ORDENAR a propósito).
    Cada grupo lleva `estado` (`pendiente` | `asignado` | `sin_ordenar`) y `decision`.
    `incluir_fotos` añade `claves` (las de las fotos). Solo lectura."""
    with _abrir(ruta_salida, escribe=False) as man:
        asignaciones = man.asignaciones_manuales()
        resultado = []
        for grupo in man.grupos_sin_colocar_raw():
            decision = _decision(grupo, asignaciones)
            if solo_pendientes and decision is not None:
                continue
            salida = {k: v for k, v in grupo.items() if k not in ("claves", "fotos_giro")}
            salida["decision"] = decision
            salida["estado"] = ("pendiente" if decision is None else
                                "sin_ordenar" if decision["fila_estadillo"] is None else "asignado")
            if incluir_fotos:
                salida["claves"] = list(grupo["claves"])
            resultado.append(salida)
        return resultado


# --- Asignación ---------------------------------------------------------------------

def _fila_manifiesto(fila) -> FilaManifiesto:
    datos = {c.name: fila[c.name] for c in fields(FilaManifiesto)}
    for booleano in ("comprime", "unassigned", "meta_leida"):
        datos[booleano] = bool(datos[booleano])
    if datos["jpeg_sin_eoi"] is not None:
        datos["jpeg_sin_eoi"] = bool(datos["jpeg_sin_eoi"])
    return FilaManifiesto(**datos)


class _Avisos:
    def __init__(self) -> None:
        self.mensajes: list[str] = []

    def emit(self, texto: str) -> None:
        self.mensajes.append(str(texto).strip())


def _angulo_de_vuelo(man: Manifiesto, ruta_salida: str, ficha: dict, grupo: dict,
                     claves: list[str], avisos: _Avisos):
    """`(angulo, version, {clave: voto})` para colocar `claves` en la fila `ficha`. Un solo
    ángulo por vuelo: el consenso vigente de `vuelos` manda; si no hay, el de las filas ya
    colocadas; si tampoco, se calcula (mismo código que el índice) con las fotos del grupo y
    se guarda en `vuelos`. Un ángulo que contradice FlightYaw es error (`ErrorGiroContradictorio`)."""
    from atom_core import indice as ind  # noqa: PLC0415 - evita ciclo
    if ind._es_pb_generales(ficha["pb"]) or ficha["generales"]:
        return 0, None, {}
    bruto = man.leer_ajuste(AJUSTE_CRITERIO_GIRO)
    if bruto is None:
        raise ErrorSinColocar(
            "el manifiesto no guarda el criterio de giro (índice de una versión anterior): "
            "reindexa el destino antes de colocar fotos a mano.")
    import json  # noqa: PLC0415
    cfg = SimpleNamespace(**json.loads(bruto))
    ventana = {"pb": ficha["pb"], "vuelo": ficha["vuelo"], "sufijo": ficha.get("sufijo")}
    datos = {}
    for clave in claves:
        giro = grupo["fotos_giro"].get(clave)
        if giro:
            datos[clave] = SimpleNamespace(yaw=giro[0], pitch=giro[1], flight_yaw=giro[2])
    asignaciones = [(d, ventana) for d in datos.values()]
    reparto: dict = {}
    votos: dict = {}
    vuelos = man.vuelos()
    ind._consenso_de_angulo_por_vuelo(
        asignaciones, None, cfg, ruta_salida, avisos, reparto_out=reparto, votos_out=votos,
        vuelos_previos=vuelos)
    clave_v = (ficha["pb"], ficha["vuelo"], ficha.get("sufijo"))
    previo = vuelos.get(clave_v)
    existente = (man.angulos_versionados_por_vuelo().get((ficha["pb"], ficha["vuelo"]))
                 if not ficha.get("sufijo") else None)
    guardar = None
    if previo is not None and previo.get("version_algoritmo") == ind.VERSION_ALGORITMO_GIRO:
        angulo, version = previo["angulo"], ind.VERSION_ALGORITMO_GIRO
    elif existente is not None:
        angulo, version = existente
    else:
        angulo, version = None, ind.VERSION_ALGORITMO_GIRO
        calculado = reparto.get(clave_v)
        if calculado is not None and "n_total" in calculado:
            angulo, guardar = calculado["angulo"], calculado
        else:
            angulo = 0   # ninguna foto con yaw: igual que el índice (sin yaw no hay voto)
    try:
        ind._validar_giro_contra_flight_yaw(asignaciones, {clave_v: angulo}, cfg)
    except ind.ErrorGiroContradictorio as error:
        raise ErrorSinColocar(str(error)) from error
    if guardar is not None:
        man.guardar_vuelos([guardar])
    return angulo, version, {c: votos.get(id(d)) for c, d in datos.items()}


def asignar_grupo(ruta_salida: str, grupo_id: str, fila_estadillo: int | None, *,
                  quien: str) -> dict:
    """Asigna TODAS las fotos del grupo a la fila `fila_estadillo` del estadillo (índice de
    fila que da `grupos_sin_colocar`: `vecino_*.fila`; vale cualquier fila del estadillo de
    la tanda del grupo), o las deja en SIN_ORDENAR a propósito con `None`. Lo registra en el
    manifiesto (por foto: quién, cuándo en Europe/Madrid y fila) y reescribe las filas del
    manifiesto con las rutas de salida de esa fila (mismos nombres, `_CROP`, `.tiff` y giro
    que una asignación automática). El APPLY posterior las coloca.

    - Grupo o fila inexistentes, `quien` vacío, apply en curso, o fotos ya colocadas Y
      APLICADAS en otra fila: `ErrorSinColocar` (nada se escribe).
    - Si el apply ya corrió y las fotos están en SIN_ORDENAR: se RECOLOCAN (la fila vuelve a
      `pendiente` con sus rutas viejas apuntadas para borrarse tras escribir las nuevas, el
      mecanismo de `insertar_o_reabrir`); hay que volver a correr el apply. Se devuelve en
      `recolocadas`.
    - Reasignar fotos aún sin aplicar (`pendiente`) a otra fila, o a `None`, sí se permite.

    Devuelve `{"grupo_id", "fila_estadillo", "n_fotos", "pendientes_apply", "recolocadas",
    "sin_cambio", "avisos"}`."""
    quien = str(quien or "").strip()
    if not quien:
        raise ErrorSinColocar("falta 'quien': la asignación manual debe quedar firmada.")
    if fila_estadillo is not None and (isinstance(fila_estadillo, bool)
                                       or not isinstance(fila_estadillo, int)):
        raise ErrorSinColocar(f"fila_estadillo debe ser un entero o None: {fila_estadillo!r}")
    with _abrir(ruta_salida, escribe=True) as man:
        return _asignar(man, ruta_salida, grupo_id, fila_estadillo, quien)


def _asignar(man: Manifiesto, ruta_salida: str, grupo_id: str, fila_estadillo, quien: str) -> dict:
    from atom_core import indice as ind  # noqa: PLC0415 - evita ciclo
    grupo = next((g for g in man.grupos_sin_colocar_raw() if g["grupo_id"] == grupo_id), None)
    if grupo is None:
        raise ErrorSinColocar(f"grupo {grupo_id!r} inexistente en {ruta_salida}.")
    ficha = None
    if fila_estadillo is not None:
        ficha = man.fila_estadillo(grupo["ejecucion"], fila_estadillo)
        if ficha is None:
            raise ErrorSinColocar(
                f"la fila {fila_estadillo} no existe en el estadillo del grupo {grupo_id} "
                f"(ejecución {grupo['ejecucion']}).")
    claves = list(grupo["claves"])
    filas = man.filas_por_claves(claves)
    ausentes = [c for c in claves if c not in filas]
    if ausentes:
        raise ErrorSinColocar(
            f"{len(ausentes)} foto(s) del grupo {grupo_id} ya no están en el manifiesto "
            f"(p. ej. {ausentes[0]}).")
    manuales = man.asignaciones_manuales()

    cambiar: list[str] = []      # claves cuya fila del manifiesto hay que reescribir
    recolocadas = sin_cambio = 0
    for clave in claves:
        f = filas[clave]
        if f["estado"] == "en_curso":
            raise ErrorSinColocar(
                f"hay un apply en curso sobre {f['ruta_origen']}: espera a que termine.")
        if f["unassigned"]:
            if ficha is None:
                sin_cambio += 1
            else:
                cambiar.append(clave)
                recolocadas += 1 if f["estado"] == "hecho" else 0
            continue
        # Ya asignada: solo se mueve si fue una asignación manual aún sin aplicar.
        if ficha is not None and f["pb"] == ficha["pb"] and f["vuelo"] == ficha["vuelo"]:
            sin_cambio += 1
            continue
        if f["estado"] == "hecho":
            raise ErrorSinColocar(
                f"{f['ruta_origen']} ya está colocada y aplicada en PB{f['pb']}/{f['vuelo']}: "
                "no se mueve (borra/reorganiza esa salida a mano si es un error).")
        if clave not in manuales:
            raise ErrorSinColocar(
                f"{f['ruta_origen']} la asignó el índice a PB{f['pb']}/{f['vuelo']}; "
                "no es una foto sin colocar.")
        cambiar.append(clave)

    avisos = _Avisos()
    nuevas: dict[int | None, list[FilaManifiesto]] = {}
    if cambiar:
        if ficha is not None:
            angulo, version, votos = _angulo_de_vuelo(man, ruta_salida, ficha, grupo, cambiar, avisos)
        for clave in cambiar:
            base = _fila_manifiesto(filas[clave])
            nombre_final = base.nombre_nuevo or nombre_de_ruta(base.ruta_origen)
            if ficha is None:
                carpeta = almacen.unir(ruta_salida, ind.NOMBRE_CARPETA_SIN_ORDENAR, base.tipo)
                cambios = dict(pb=None, vuelo=None, angulo_giro=0, angulo_version=None,
                               giro_voto=None, equipo_estadillo=None, unassigned=True)
            else:
                if ficha["generales"] or ind._es_pb_generales(ficha["pb"]):
                    carpeta = almacen.unir(ruta_salida, ind.NOMBRE_CARPETA_GENERALES)
                else:
                    carpeta = almacen.unir(ruta_salida, base.tipo, f"PB{ficha['pb']}",
                                           ficha["carpeta_vuelo"])
                cambios = dict(pb=ficha["pb"], vuelo=ficha["vuelo"], angulo_giro=angulo,
                               angulo_version=version, giro_voto=votos.get(clave),
                               equipo_estadillo=ficha.get("equipo"), unassigned=False)
            original, crop, tiff = ind.rutas_de_salida(
                carpeta, nombre_final, bool(base.ruta_salida_crop), bool(base.ruta_salida_tiff))
            nuevas.setdefault(filas[clave]["ejecucion_id"], []).append(replace(
                base, ruta_salida_original=original, ruta_salida_crop=crop,
                ruta_salida_tiff=tiff, **cambios))

    cuando = datetime.datetime.now(_MADRID).isoformat(timespec="seconds")
    man.registrar_asignaciones(
        claves, grupo_id, fila_estadillo, firma_ficha(ficha) if ficha else None, quien, cuando)
    for ejecucion_id, lista in nuevas.items():
        lista = ind._desambiguar_colisiones_generales(lista, man.rutas_salida_por_clave())
        resultado = man.insertar_o_reabrir(lista, ejecucion_id=ejecucion_id)
        if resultado.nuevas:
            raise ErrorSinColocar(
                f"el manifiesto no reconoció {resultado.nuevas} foto(s) del grupo {grupo_id}.")
    return {"grupo_id": grupo_id, "fila_estadillo": fila_estadillo, "n_fotos": len(claves),
            "pendientes_apply": len(cambiar), "recolocadas": recolocadas,
            "sin_cambio": sin_cambio, "avisos": avisos.mensajes}


# --- Reaplicación en el índice ---------------------------------------------------------

def ventana_de_firma(firma: dict, ventanas: list[dict]) -> dict | None:
    """Ventana del estadillo actual que cumple la firma guardada en una asignación manual."""
    for v in ventanas:
        if (v["pb"] == firma["pb"] and v["vuelo"] == firma["vuelo"]
                and (v.get("sufijo") or "") == (firma.get("sufijo") or "")
                and _iso(v["inicio"]) == firma["inicio"] and _iso(v["fin"]) == firma["fin"]):
            return v
    return None


def aplicar_asignaciones_manuales(asignaciones: list, manifiesto: Manifiesto, ventanas: list[dict],
                                  tamanos_origen, emitir) -> list:
    """Re-índice/tanda posterior: a cada foto sin ventana con asignación manual a una fila se
    le devuelve la ventana elegida (las demás no cambian). La asignación se reconoce por
    la firma de la fila (pb, vuelo, horas), no por su número. Si esa fila ya no está en
    el estadillo, ERROR visible y la foto sigue sin colocar. `emitir(texto)` avisa."""
    manuales = {c: a for c, a in manifiesto.asignaciones_manuales().items()
                if a["fila"] is not None and a["ventana"]}
    if not manuales:
        return asignaciones
    from atom_core import indice as ind  # noqa: PLC0415 - evita ciclo
    resultado = []
    huerfanas: dict[str, int] = {}
    aplicadas = 0
    for dato, ventana in asignaciones:
        if ventana is None and dato.timestamp is not None:
            clave = clave_imagen(dato.ruta, dato.timestamp.isoformat(),
                                 ind._bytes_origen_de(dato.ruta, tamanos_origen))
            manual = manuales.get(clave)
            if manual is not None:
                elegida = ventana_de_firma(manual["ventana"], ventanas)
                if elegida is not None:
                    ventana = elegida
                    aplicadas += 1
                else:
                    f = manual["ventana"]
                    etiqueta = f"PB{f['pb']} vuelo {f['vuelo']} {f['inicio']}–{f['fin']}"
                    huerfanas[etiqueta] = huerfanas.get(etiqueta, 0) + 1
        resultado.append((dato, ventana))
    if aplicadas:
        emitir(f"\nÍndice · {aplicadas} foto(s) colocadas a mano en tandas anteriores se "
               "mantienen en la fila elegida.\n")
    for etiqueta, n in sorted(huerfanas.items()):
        emitir(f"\nERROR: {n} foto(s) asignadas a mano a la fila {etiqueta} del estadillo, "
               "que ya no existe en el estadillo actual; siguen sin colocar (SIN_ORDENAR).\n")
    return resultado
