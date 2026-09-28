"""Compara los vuelos de un estadillo con las fotos reales de la carpeta.

Aviso previo al piloto ANTES de subir (nunca bloquea): fotos que faltan,
vuelos sin ninguna foto, fotos que caen dentro de dos vuelos a la vez
(solapado) o fuera de todos (`fotos_fuera`).

Módulo puro: no toca disco ni red. El escaneo EXIF vive en `exif.py`
(`listar_horas_exif`); leer el estadillo vive en `atom_core/estadillo.py`
(`filas_para_suite`, que es de donde sale la forma de `vuelos` que se le
pasa aquí: `fecha`/`hora_inicio`/`hora_fin`/`pb`/`num_vuelo`/`piloto`/
`equipo_vuelo`). El enganche (esperar el escaneo EXIF, guardar el
resultado) vive en `app_webview.py`.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

_TZ_MADRID = ZoneInfo("Europe/Madrid")

# Por debajo de este porcentaje del nº de fotos esperado, "menos fotos de
# las esperadas" deja de ser ruido (un par de fotos borradas/descartadas a
# mano por el piloto) y pasa a ser un aviso real. Documentado aquí porque no
# hay ningún criterio del negocio que lo fije: 90% es un margen razonable.
UMBRAL_POCAS_FOTOS = 0.9

_ESTADOS = ("ok", "sin_fotos", "pocas_fotos", "solapado")


def _a_madrid_naive(valor) -> datetime.datetime | None:
    """Normaliza una hora (del estadillo o del EXIF) a un `datetime` naive
    en hora de Madrid.

    Acepta `datetime.datetime` (naive -se asume ya en hora local de la
    operación, como el EXIF- o con tzinfo -se convierte a Madrid y se le
    quita la zona-) y `str` ISO 8601 (con o sin offset). `None` si no se
    puede interpretar."""
    if valor is None:
        return None
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None
        try:
            valor = datetime.datetime.fromisoformat(texto)
        except ValueError:
            return None
    if not isinstance(valor, datetime.datetime):
        return None
    if valor.tzinfo is not None:
        valor = valor.astimezone(_TZ_MADRID).replace(tzinfo=None)
    return valor


def _combinar_fecha_hora(fecha, hora) -> datetime.datetime | None:
    """`fecha` ('YYYY-MM-DD') + `hora` ('HH:MM[:SS]') -> `datetime` naive.
    `None` si cualquiera de las dos falta o no se puede parsear."""
    if not fecha or not hora:
        return None
    for fmt_hora in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.datetime.strptime(f"{fecha} {hora}", f"%Y-%m-%d {fmt_hora}")
        except ValueError:
            continue
    return None


def _id_vuelo(vuelo: dict) -> str:
    if vuelo.get("id"):
        return str(vuelo["id"])
    pb = vuelo.get("pb") or ""
    num = vuelo.get("num_vuelo") or vuelo.get("vuelo") or ""
    return f"{pb}-{num}" if (pb or num) else str(id(vuelo))


def _nombre_vuelo(vuelo: dict) -> str:
    if vuelo.get("nombre"):
        return str(vuelo["nombre"])
    pb = vuelo.get("pb") or ""
    num = vuelo.get("num_vuelo") or vuelo.get("vuelo") or ""
    if pb or num:
        return f"PB{pb} · Vuelo {num}".strip()
    return "Vuelo"


def _esperado_vuelo(vuelo: dict) -> int | None:
    for clave in ("esperado", "fotos_esperadas", "n_fotos_esperadas"):
        valor = vuelo.get(clave)
        if valor is None:
            continue
        try:
            return int(valor)
        except (TypeError, ValueError):
            continue
    return None


def _ventana_vuelo(vuelo: dict, margen_s: int) -> tuple[datetime.datetime | None, datetime.datetime | None]:
    """[inicio, fin] del vuelo, ya con el margen aplicado a cada lado.

    Si falta la hora de fin se usa la de inicio (vuelo "puntual": solo se
    exige que las fotos caigan cerca de esa hora). Si el fin sale antes que
    el inicio se asume que el vuelo cruza medianoche (mismo criterio que el
    pipeline, ver `estadillo.py` L496-501) y se le suma un día."""
    inicio = vuelo.get("inicio")
    fin = vuelo.get("fin")
    if inicio is None:
        inicio = _combinar_fecha_hora(vuelo.get("fecha"), vuelo.get("hora_inicio"))
    else:
        inicio = _a_madrid_naive(inicio)
    if fin is None:
        fin = _combinar_fecha_hora(vuelo.get("fecha"), vuelo.get("hora_fin")) if vuelo.get("hora_fin") else None
    else:
        fin = _a_madrid_naive(fin)

    if inicio is None:
        return None, None
    if fin is None:
        fin = inicio
    if fin < inicio:
        fin = fin + datetime.timedelta(days=1)

    margen = datetime.timedelta(seconds=max(0, int(margen_s)))
    return inicio - margen, fin + margen


def validar(vuelos: list[dict], tiempos_fotos: list, margen_s: int = 120) -> dict:
    """Compara `vuelos` (forma de `estadillo.filas_para_suite`, o al menos
    con `pb`/`num_vuelo`/`fecha`/`hora_inicio`/`hora_fin`, o directamente
    `inicio`/`fin` ya como `datetime`/ISO) contra `tiempos_fotos` (lista de
    horas EXIF, `datetime` naive o ISO, sin zona: se asumen ya en hora de
    Madrid, igual que `exif.rango_horas_exif`).

    Nunca rechaza nada: solo informa. Devuelve
    `{ok, pendiente, vuelos: [{id, nombre, inicio, fin, fotos, esperado,
    estado}], fotos_fuera, avisos}` con `estado` en
    `ok|sin_fotos|pocas_fotos|solapado` por vuelo. `pendiente` siempre
    `False` aquí -lo pone `True` quien llama (`app_webview.py`) cuando el
    escaneo EXIF de la carpeta todavía no ha terminado, sin ni siquiera
    llamar a `validar`."""
    ventanas: dict[str, tuple[datetime.datetime | None, datetime.datetime | None]] = {}
    esperados: dict[str, int | None] = {}
    nombres: dict[str, str] = {}
    orden: list[str] = []

    for vuelo in vuelos or []:
        vid = _id_vuelo(vuelo)
        if vid in ventanas:
            # Ids duplicados (mismo pb+num_vuelo repetido en el estadillo):
            # se conserva el primero, no se pierde el vuelo del resultado.
            vid = f"{vid}#{len(orden)}"
        orden.append(vid)
        ventanas[vid] = _ventana_vuelo(vuelo, margen_s)
        esperados[vid] = _esperado_vuelo(vuelo)
        nombres[vid] = _nombre_vuelo(vuelo)

    conteos = {vid: 0 for vid in orden}
    solapados: set[str] = set()
    fotos_fuera = 0

    for t in tiempos_fotos or []:
        momento = _a_madrid_naive(t)
        if momento is None:
            continue
        coincide = [
            vid for vid in orden
            if ventanas[vid][0] is not None and ventanas[vid][0] <= momento <= ventanas[vid][1]
        ]
        if not coincide:
            fotos_fuera += 1
        elif len(coincide) == 1:
            conteos[coincide[0]] += 1
        else:
            solapados.update(coincide)

    resultado_vuelos = []
    avisos: list[str] = []
    for vid in orden:
        inicio, fin = ventanas[vid]
        fotos = conteos[vid]
        esperado = esperados[vid]
        nombre = nombres[vid]

        if inicio is None:
            estado = "sin_fotos"
            avisos.append(f"{nombre}: no se pudo interpretar la hora del vuelo en el estadillo.")
        elif vid in solapados:
            estado = "solapado"
            avisos.append(f"{nombre}: tiene fotos que también caen dentro de otro vuelo (horarios solapados).")
        elif fotos == 0:
            estado = "sin_fotos"
            avisos.append(f"{nombre}: no se ha encontrado ninguna foto en su horario.")
        elif esperado is not None and fotos < esperado * UMBRAL_POCAS_FOTOS:
            estado = "pocas_fotos"
            avisos.append(f"{nombre}: {fotos} fotos de {esperado} esperadas.")
        else:
            estado = "ok"

        resultado_vuelos.append({
            "id": vid,
            "nombre": nombre,
            "inicio": inicio.isoformat() if inicio else None,
            "fin": fin.isoformat() if fin else None,
            "fotos": fotos,
            "esperado": esperado,
            "estado": estado,
        })

    if fotos_fuera > 0:
        avisos.append(f"{fotos_fuera} foto{'s' if fotos_fuera != 1 else ''} fuera del horario de cualquier vuelo.")

    return {
        "ok": not avisos,
        "pendiente": False,
        "vuelos": resultado_vuelos,
        "fotos_fuera": fotos_fuera,
        "avisos": avisos,
    }
