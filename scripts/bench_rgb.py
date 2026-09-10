"""Benchmark del organizado REAL: ¿dónde se va el tiempo en la fase RGB?

Lanza `atom_core.organize.run_organize` (el MISMO motor de producción,
`organizar_plan_apply`) sobre una carpeta de entrada real, con la
instrumentación OPT-IN de `atom_core.perfil_rgb` activada, y cruza dos
fuentes:

1. La línea `[tiempos]` que emite `atom_core.phases` al cerrar el
   organizado (duración de cada etapa: Índice, RGB, Térmicas, Cierre) y las
   líneas `[paralelismo]` de `atom_core.paralelismo` (cuántos workers usó
   de verdad cada ventana).
2. El CSV de `perfil_rgb` (una fila por imagen, con el desglose por
   sub-etapa: lectura, decode, encode_original, encode_crop, escritura).

Con eso, un CPU-segundos total del proceso (+ hijos, vía `resource`) permite
ver cuánto de ese tiempo queda "sin explicar" por las sub-etapas medidas —
la pista de si el cuello de botella es alguna de ellas o algo fuera del
camino instrumentado (overhead del `ProcessPoolExecutor`, GC, I/O no
medida...).

SOLO LECTURA sobre el motor de organizado en el sentido de que no cambia
ningún comportamiento: activa la instrumentación ya existente por env var
(`ORGANIZER_PERFIL_RGB`) y lee sus salidas. La carpeta `--destino` SÍ se
escribe (y se limpia entre repeticiones): es el organizado real, no un
simulacro.

Uso:
    python scripts/bench_rgb.py --origen ORIGEN --destino DESTINO \\
        [--estadillo ESTADILLO] [--perfil-csv RUTA] [--repeticiones N] \\
        [--json SALIDA.json]
"""
from __future__ import annotations

import argparse
import csv
import json as json_mod
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `resource` no existe en Windows: la medición de CPU-segundos degrada a
# `None` sin romper el resto del informe (requisito 5).
try:
    import resource
except ImportError:  # pragma: no cover - depende del SO, no de la lógica
    resource = None  # type: ignore[assignment]


#: Columnas de sub-etapa RGB que pide la tarea (deja fuera `thumbnail`, que
#: también escribe `perfil_rgb` pero no es una de las 5 pedidas aquí).
_COLUMNAS_SUBETAPAS = (
    "t_lectura", "t_decode", "t_encode_original", "t_encode_crop", "t_escritura",
)

#: Nombre legible (sin el prefijo `t_`) para las tablas del informe.
_NOMBRE_SUBETAPA = {
    "t_lectura": "lectura",
    "t_decode": "decode",
    "t_encode_original": "encode_original",
    "t_encode_crop": "encode_crop",
    "t_escritura": "escritura",
}

# Token de duración: un número (con . o , decimal) seguido de una unidad
# (h / min / m / s). Sirve tanto para el formato real de
# `phases._formatear_duracion` ("5 min 12 s", "52.0 s") como para el
# formato compacto ("6m49s", "1h2m3s"): ambos son "número+unidad" repetido,
# solo cambia si hay espacio entre número y unidad y si "min" va completo o
# abreviado a "m".
#: Sin `\b` tras la unidad a propósito: en el formato compacto ("6m49s") la
#: "m" va pegada al dígito siguiente ("6m" + "49s"), y un dígito cuenta como
#: carácter de palabra -> `\b` no encontraría frontera ahí y el token se
#: perdería (findall seguiría desde la "m" y solo capturaría "49s").
_RE_TOKEN_DURACION = re.compile(r"(\d+(?:[.,]\d+)?)\s*(min|h|m|s)")

_FACTOR_UNIDAD = {"h": 3600.0, "min": 60.0, "m": 60.0, "s": 1.0}


def _parsear_duracion(texto: str) -> float:
    """Inverso de `atom_core.apply._formatear_duracion`: `"5 min 12 s"` o
    `"6m49s"` o `"1h2m3s"` -> segundos (float). Suma cada token
    número+unidad que encuentre; si no encuentra ninguno, devuelve 0.0."""
    total = 0.0
    for numero, unidad in _RE_TOKEN_DURACION.findall(texto):
        total += float(numero.replace(",", ".")) * _FACTOR_UNIDAD[unidad]
    return total


def _parsear_linea_tiempos(linea: str) -> "dict[str, float]":
    """Parsea una línea `[tiempos] Organizado completo: ... (Índice 52.0 s ·
    RGB 6 min 49 s · Térmicas 1.0 s · Cierre 0.5 s).` y devuelve
    `{"Índice": 52.0, "RGB": 409.0, "Térmicas": 1.0, "Cierre": 0.5}`.

    El desglose por etapa va entre paréntesis, separado por `" · "`; cada
    segmento es `"<nombre de etapa> <duración>"` (el nombre puede llevar
    espacios y tildes, la duración siempre empieza por un dígito)."""
    m = re.search(r"\((.*)\)", linea)
    if not m:
        return {}
    etapas: "dict[str, float]" = {}
    for segmento in m.group(1).split(" · "):
        segmento = segmento.strip()
        m_seg = re.match(r"^(.+?)\s+(\d.*)$", segmento)
        if not m_seg:
            continue
        nombre, duracion = m_seg.group(1).strip(), m_seg.group(2).strip()
        etapas[nombre] = _parsear_duracion(duracion)
    return etapas


def _percentil(valores: "list[float]", pct: float) -> float:
    """Percentil `pct` (0-100) de `valores` por interpolación lineal entre
    los dos rangos más cercanos (mismo criterio que `numpy.percentile` por
    defecto). `0.0` sobre una lista vacía."""
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    posicion = (len(ordenados) - 1) * (pct / 100.0)
    inferior = int(posicion)
    superior = min(inferior + 1, len(ordenados) - 1)
    fraccion = posicion - inferior
    return ordenados[inferior] + (ordenados[superior] - ordenados[inferior]) * fraccion


def _agregar_estadisticas_csv(ruta_csv: str) -> "dict | None":
    """Lee el CSV de `perfil_rgb` y calcula, por cada sub-etapa RGB
    (`_COLUMNAS_SUBETAPAS`), la suma/media/p95 de sus segundos y el nº de
    imágenes. `None` si el fichero no existe o no tiene filas."""
    if not os.path.exists(ruta_csv):
        return None
    with open(ruta_csv, "r", encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    if not filas:
        return None
    n = len(filas)
    subetapas = {}
    for columna in _COLUMNAS_SUBETAPAS:
        valores = [float(fila[columna]) for fila in filas]
        subetapas[columna] = {
            "suma": sum(valores),
            "media": sum(valores) / n,
            "p95": _percentil(valores, 95),
        }
    return {"n_imagenes": n, "subetapas": subetapas}


def _cpu_segundos() -> "float | None":
    """CPU-segundos (user+sys) del proceso actual MÁS sus hijos ya
    terminados (los workers del `ProcessPoolExecutor`, una vez recogidos).
    `None` en plataformas sin el módulo `resource` (Windows)."""
    if resource is None:
        return None
    propio = resource.getrusage(resource.RUSAGE_SELF)
    hijos = resource.getrusage(resource.RUSAGE_CHILDREN)
    return propio.ru_utime + propio.ru_stime + hijos.ru_utime + hijos.ru_stime


def _limpiar_destino(destino: str) -> None:
    """Deja `destino` vacía (la crea si no existe). El motor de organizado
    exige la carpeta de salida vacía; entre repeticiones hay que limpiarla
    a mano."""
    if os.path.isdir(destino):
        shutil.rmtree(destino)
    os.makedirs(destino, exist_ok=True)


def _parse_args(argv: "list[str] | None" = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark del organizado real: desglose de tiempos de la fase RGB.")
    parser.add_argument("--origen", required=True, help="Carpeta de origen (imágenes a organizar).")
    parser.add_argument("--destino", required=True, help="Carpeta de destino (se limpia entre repeticiones).")
    parser.add_argument("--estadillo", default="", help="Ruta al estadillo (CSV/Excel), opcional.")
    parser.add_argument("--perfil-csv", dest="perfil_csv", default=None,
                        help="Ruta del CSV de perfil RGB. Por defecto, un fichero temporal.")
    parser.add_argument("--repeticiones", type=int, default=1,
                        help="Nº de veces que se repite el run completo (se reporta el mínimo por etapa).")
    parser.add_argument("--json", dest="json_salida", default=None,
                        help="Ruta donde volcar el informe en JSON, además de imprimirlo.")
    parser.add_argument("--workers", type=int, default=None,
                        help="Fija el número de trabajadores de la fase RGB en exactamente N "
                             "durante el run (el controlador adaptativo no sube ni baja). "
                             "Por defecto, controlador adaptativo normal.")
    return parser.parse_args(argv)


def _fijar_controlador_rgb(workers: int) -> None:
    """Monkeypatch de SOLO instrumentación (vive aquí, en el script de
    bench; `atom_core/paralelismo.py` y `atom_core/phases.py` no se tocan):
    sustituye `atom_core.paralelismo.ControladorAdaptativo` por una fábrica
    que ignora `minimo`/`maximo`/`arranque`/`tope_hdd` entrantes y los clava
    a `workers` (y `tope_hdd=None`, para que el tope por HDD tampoco pueda
    moverlo). `phases.py` resuelve `paralelismo_mod.ControladorAdaptativo`
    en cada llamada (no queda una referencia vieja tras el import), así que
    parchear el atributo del módulo es suficiente para que la fase RGB lo
    vea sin tocar su código.

    Con `minimo == maximo == arranque == workers`, `decidir_trabajadores`
    (ver `paralelismo.py`) devuelve `workers` en TODAS sus ramas: la regla 1
    hace `max(minimo, trabajadores - 1)` = `workers`, `_subir`/`_bajar`
    clampan a `maximo`/`minimo` = `workers`. El controlador queda inmóvil
    sin necesidad de tocar `revisar()`.

    Idempotente: si ya está parcheada (varias repeticiones en el mismo
    proceso), no vuelve a envolver la fábrica anterior."""
    import atom_core.paralelismo as paralelismo_mod

    actual = paralelismo_mod.ControladorAdaptativo
    if getattr(actual, "_bench_rgb_workers_fijados", None) == workers:
        return

    ControladorBase = getattr(actual, "_bench_rgb_original", actual)

    def _fabrica_fija(*args, **kwargs):
        kwargs["minimo"] = workers
        kwargs["maximo"] = workers
        kwargs["arranque"] = workers
        kwargs["tope_hdd"] = None
        return ControladorBase(*args, **kwargs)

    _fabrica_fija._bench_rgb_original = ControladorBase
    _fabrica_fija._bench_rgb_workers_fijados = workers
    paralelismo_mod.ControladorAdaptativo = _fabrica_fija


def _ejecutar_run(params: dict, workers: "int | None" = None) -> "dict[str, list[str]]":
    """Lanza UN run de organizado completo vía `run_organize` (motor real,
    `organizar_plan_apply`) y captura las líneas `[tiempos]` y
    `[paralelismo]` que emite. El import de `atom_core` va AQUÍ DENTRO,
    nunca a nivel de módulo: tiene que ocurrir después de fijar
    `ORGANIZER_PERFIL_RGB`, porque `atom_core.perfil_rgb` cachea la env var
    al importarse (`ACTIVO` se calcula UNA vez, ver docstring del módulo).

    Si `workers` no es `None`, fija el número de trabajadores de la fase RGB
    en exactamente ese valor durante el run (ver `_fijar_controlador_rgb`);
    con `workers=None` el comportamiento es idéntico al de antes de este
    parámetro."""
    from atom_core.organize import run_organize

    if workers is not None:
        _fijar_controlador_rgb(workers)

    lineas_tiempos: "list[str]" = []
    lineas_paralelismo: "list[str]" = []
    errores: "list[str]" = []

    def emit(kind: str, payload=None) -> None:
        if kind == "log" and payload:
            for linea in str(payload).splitlines():
                linea = linea.strip()
                if linea.startswith("[tiempos]"):
                    lineas_tiempos.append(linea)
                elif linea.startswith("[paralelismo]"):
                    lineas_paralelismo.append(linea)
        elif kind == "error":
            errores.append(str(payload))

    run_organize(params, emit)
    if errores:
        raise RuntimeError("El organizado terminó en error: " + " | ".join(errores))
    return {"tiempos": lineas_tiempos, "paralelismo": lineas_paralelismo}


def _correr_repeticiones(params: dict, destino: str, ruta_perfil_csv: str,
                          repeticiones: int, workers: "int | None" = None) -> "list[dict]":
    """Ejecuta el organizado `repeticiones` veces, limpiando `destino` antes
    de cada una, y devuelve una lista con el resultado crudo de cada
    repetición (etapas, líneas de paralelismo, CPU-segundos, duración de
    pared y estadísticas del CSV de esa corrida). `workers` ver `_ejecutar_run`."""
    resultados = []
    for _ in range(repeticiones):
        _limpiar_destino(destino)
        cpu_antes = _cpu_segundos()
        t0 = time.monotonic()
        captura = _ejecutar_run(params, workers=workers)
        duracion_pared = time.monotonic() - t0
        cpu_despues = _cpu_segundos()
        cpu_delta = None if cpu_antes is None else (cpu_despues - cpu_antes)

        etapas: "dict[str, float]" = {}
        for linea in captura["tiempos"]:
            etapas.update(_parsear_linea_tiempos(linea))

        resultados.append({
            "etapas": etapas,
            "paralelismo": captura["paralelismo"],
            "cpu_segundos": cpu_delta,
            "duracion_pared": duracion_pared,
            "csv_stats": _agregar_estadisticas_csv(ruta_perfil_csv),
        })
    return resultados


def _minimo_por_etapa(resultados: "list[dict]") -> "dict[str, float]":
    """Mínimo, por nombre de etapa, entre todas las repeticiones."""
    minimos: "dict[str, float]" = {}
    for r in resultados:
        for etapa, segundos in r["etapas"].items():
            if etapa not in minimos or segundos < minimos[etapa]:
                minimos[etapa] = segundos
    return minimos


def _mejor_repeticion(resultados: "list[dict]") -> dict:
    """La repetición con menor tiempo de RGB (o la primera, si ninguna trae
    esa etapa) — es la que se usa para el desglose CPU-vs-sub-etapas, para
    no mezclar el CPU de una corrida con las sub-etapas de otra."""
    con_rgb = [r for r in resultados if "RGB" in r["etapas"]]
    if con_rgb:
        return min(con_rgb, key=lambda r: r["etapas"]["RGB"])
    return resultados[0]


def _construir_informe(resultados: "list[dict]", workers: "int | None" = None) -> dict:
    minimos_etapas = _minimo_por_etapa(resultados)
    mejor = _mejor_repeticion(resultados)
    csv_stats = mejor["csv_stats"]

    suma_subetapas = 0.0
    subetapas_informe = {}
    if csv_stats is not None:
        for columna in _COLUMNAS_SUBETAPAS:
            datos = csv_stats["subetapas"][columna]
            subetapas_informe[_NOMBRE_SUBETAPA[columna]] = datos
            suma_subetapas += datos["suma"]

    cpu_total = mejor["cpu_segundos"]
    sin_explicar = None if cpu_total is None else cpu_total - suma_subetapas

    return {
        "repeticiones": len(resultados),
        "nucleos": os.cpu_count(),
        "etapas_minimo": minimos_etapas,
        "n_imagenes": csv_stats["n_imagenes"] if csv_stats else 0,
        "subetapas_rgb": subetapas_informe,
        "cpu_segundos_mejor_repeticion": cpu_total,
        "suma_subetapas_rgb": suma_subetapas,
        "sin_explicar": sin_explicar,
        "paralelismo": mejor["paralelismo"],
        "workers_fijados": workers,
    }


def _imprimir_informe(informe: dict) -> None:
    print(f"\n=== Benchmark RGB — {informe['repeticiones']} repetición(es), "
          f"{informe['nucleos']} núcleos ===\n")
    if informe.get("workers_fijados") is not None:
        print(f"Workers RGB fijados: {informe['workers_fijados']} (controlador adaptativo inmóvil)\n")

    print("Etapas del run (mínimo entre repeticiones):")
    if informe["etapas_minimo"]:
        for etapa, segundos in informe["etapas_minimo"].items():
            print(f"  {etapa:<12} {segundos:>10.1f} s")
    else:
        print("  (no se encontró ninguna línea [tiempos] en la salida del run)")

    print(f"\nSub-etapas RGB ({informe['n_imagenes']} imágenes, mejor repetición):")
    if informe["subetapas_rgb"]:
        print(f"  {'sub-etapa':<18}{'suma(s)':>10}{'media(s)':>10}{'p95(s)':>10}")
        for nombre, datos in informe["subetapas_rgb"].items():
            print(f"  {nombre:<18}{datos['suma']:>10.2f}{datos['media']:>10.3f}{datos['p95']:>10.3f}")
    else:
        print("  (sin datos: el CSV de perfil no tiene filas)")

    print("\nCPU vs sub-etapas medidas (mejor repetición):")
    if informe["cpu_segundos_mejor_repeticion"] is None:
        print("  CPU-segundos: no disponible (módulo `resource` ausente, p. ej. Windows)")
    else:
        print(f"  CPU-segundos totales (proceso + hijos): {informe['cpu_segundos_mejor_repeticion']:.2f} s")
        print(f"  Suma de sub-etapas RGB medidas:          {informe['suma_subetapas_rgb']:.2f} s")
        print(f"  Sin explicar:                            {informe['sin_explicar']:.2f} s")

    if informe["paralelismo"]:
        print(f"\nLíneas [paralelismo] ({len(informe['paralelismo'])}):")
        for linea in informe["paralelismo"]:
            print(f"  {linea}")


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(argv)

    ruta_perfil_csv = args.perfil_csv
    fichero_temporal = None
    if not ruta_perfil_csv:
        fichero_temporal = tempfile.NamedTemporaryFile(
            prefix="bench_rgb_perfil_", suffix=".csv", delete=False)
        ruta_perfil_csv = fichero_temporal.name
        fichero_temporal.close()

    # Debe fijarse ANTES de que nada importe `atom_core` (ver docstring de
    # `atom_core.perfil_rgb`): la instrumentación cachea la env var al
    # importarse, así que esto tiene que ir antes del `import` de dentro de
    # `_ejecutar_run`.
    os.environ["ORGANIZER_PERFIL_RGB"] = ruta_perfil_csv

    params = {"origen": args.origen, "destino": args.destino, "estadillo": args.estadillo}

    from atom_core import sesion_remota

    try:
        with sesion_remota.marcar("Benchmark RGB en curso"):
            resultados = _correr_repeticiones(params, args.destino, ruta_perfil_csv, args.repeticiones,
                                              workers=args.workers)
    finally:
        if fichero_temporal is not None:
            try:
                os.remove(ruta_perfil_csv)
            except OSError:
                pass

    informe = _construir_informe(resultados, workers=args.workers)
    _imprimir_informe(informe)

    if args.json_salida:
        with open(args.json_salida, "w", encoding="utf-8") as f:
            json_mod.dump(informe, f, ensure_ascii=False, indent=2)
        print(f"\nInforme volcado en {args.json_salida}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
