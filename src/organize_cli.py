"""Ejecuta el pipeline de organización sin GUI, desde la línea de comandos.

Existe por dos motivos que apuntan al mismo sitio:

1. **Medir.** Hasta ahora la única forma de correr el pipeline era abrir la
   ventana y darle a «Organizar completo», así que no había manera limpia de
   cronometrar un vuelo ni de comparar dos máquinas. Aquí el tiempo total y el
   de cada fase salen por stdout.
2. **El servidor.** La dirección del producto es «la app sube, el servidor
   organiza». Un contenedor no tiene ventana: necesita un proceso que reciba
   rutas, trabaje y termine con un código de salida. Esto es ese proceso, y es
   la pieza que faltaba para poder probar el pipeline en Cloud Run.

No duplica lógica: `atom_core.organize.run_task` ya era headless y es lo que usa
el bridge de la UI. Esto solo le da una entrada por argumentos y una salida por
consola, así que la app de escritorio y el servidor ejecutan exactamente el
mismo código.

Uso típico:

    python organize_cli.py --origen ./VUELO --destino ./SALIDA \\
        --estadillo ./ESTADILLOS/planta.csv

El código de salida es 0 si el pipeline terminó y 1 si emitió un error: es lo
que mira un Cloud Run Job para decidir si el trabajo fue bien.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import sys
import time
from pathlib import Path

# `atom_core.sharding` solo importa `os`: se puede traer arriba sin pagar los
# segundos de imports pesados que sí cuesta `atom_core.organize` (numpy, PIL,
# SDK térmico), que por eso se importa dentro de `main`.
from atom_core import sharding
from atom_core.almacen import abrir_almacen, es_uri_gcs, es_carpeta, existe_ruta, nombre_de

# Segundos por imagen y etapa, medidos en v3.4.32 sobre PLANTA_B (2.516 imágenes,
# 9,14 GB) con N=8 tareas. UN SOLO dataset: son un orden de magnitud, no una
# promesa, y por eso la Suite los pinta siempre con un «aprox.».
_SEG_POR_IMAGEN = {"split": 0.045, "struct": 0.026, "post": 0.102}
_SEG_POR_IMAGEN["todo"] = sum(_SEG_POR_IMAGEN.values())  # 0.173


def progreso_desde_stats(snapshot, *, final: bool = False):
    """Snapshot de `StatsTracker` -> cuerpo para `RunReporter.progreso()`.

    Existe porque los dos extremos hablan idiomas distintos y nadie los unía:
    el canal `stats` del pipeline emite `{"done", "total", "rgb", ...}` y
    `progreso()` espera las claves de `cloud_upload._stats`
    (`{"files_done", "files_total", ...}`). Sin esta traducción el PATCH salía
    vacío y `organizer_runs.items_hechos` se quedaba NULL en TODOS los runs.

    OJO con la semántica: `done`/`total` de `StatsTracker` son **de la fase
    actual** (se reinician en `start_phase`), no del run. Por eso lo que la
    Suite pinta durante el run es «progreso de la fase que está corriendo»,
    coherente con el `fase_actual/fases_total` que se muestra al lado; la foto
    del run entero la deja `main` al cerrar. Se mapea solo lo que tiene
    equivalente: `rgb`/`termica`/`rot*` no tienen columna y `eta` la calcula el
    propio CLI en el canal `phase` — colarla aquí la pisaría con un valor peor.

    Devuelve `None` cuando no hay nada que mandar (así el caller no gasta un
    latido en un PATCH vacío).
    """
    if not isinstance(snapshot, dict):
        return None
    if "done" not in snapshot:
        return None
    hechos = int(snapshot.get("done") or 0)
    # Igual que en `StatsTracker.snapshot`: nunca anunciar un total por debajo
    # de lo ya hecho. El total se deriva del texto del pipeline y hay
    # redacciones que no se reconocen; "120/0" en la barra es peor que "120/120".
    total = max(int(snapshot.get("total") or 0), hechos)
    cuerpo = {"files_done": hechos, "files_total": total}
    # `final` puede venir por kwarg o marcado en el propio snapshot (lo pone
    # `organize._emit_stats` en el cierre). Es lo único que hace a
    # `RunReporter.progreso` saltarse su `intervalo_latido`: sin la marca, el
    # último latido de un run cae dentro del throttle y se descarta, dejando
    # persistido el penúltimo múltiplo de IMAGE_EMIT_EVERY en vez del total
    # real (2194 de 2516 en el e2e de PLANTA_B v3.4.36), y un shard de menos de
    # IMAGE_EMIT_EVERY imágenes terminaría `ok` con 0.
    if final or snapshot.get("final"):
        cuerpo["final"] = True
    return cuerpo


def _contar_imagenes(origen: "Path | str") -> int:
    """Cuenta imágenes bajo `origen` recursivamente con `os.scandir`.

    Nada de `glob`/`Path.rglob`: un vuelo son decenas de miles de ficheros y
    esto tiene que tardar milisegundos, no segundos. Fail-open: si algo falla
    (permisos, ruta rara) devuelve 0 — sin conteo no hay ETA, pero el run
    sigue igual.
    """
    from rjpeg_a_tiff import EXTS_IMAGEN  # import local: rjpeg_a_tiff carga numpy/PIL, pesado para el arranque del CLI

    exts = EXTS_IMAGEN
    total = 0
    # En `gs://` no hay `scandir`: un unico `listar` recursivo del prefijo (una
    # sola llamada paginada al SDK) sustituye al recorrido del arbol. Sin esto
    # el `except` de abajo se lo tragaria y TODO run sobre GCS se quedaria sin
    # ETA en silencio.
    if es_uri_gcs(str(origen)):
        try:
            # `Almacen.listar` (no `listar_ficheros`, que es SOLO hijos
            # directos) para igualar el recorrido recursivo del camino local:
            # un vuelo cuelga de subcarpetas por PB, contar solo la raiz daria
            # casi cero. Una sola llamada paginada al SDK, no un arbol.
            almacen, prefijo = abrir_almacen(str(origen))
            return sum(1 for r in almacen.listar(prefijo)
                       if os.path.splitext(r)[1].lower() in exts)
        except Exception:  # noqa: BLE001 - mismo fail-open que el camino local
            return 0
    try:
        pendientes = [origen]
        while pendientes:
            actual = pendientes.pop()
            with os.scandir(actual) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pendientes.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            if os.path.splitext(entry.name)[1].lower() in exts:
                                total += 1
                    except OSError:
                        continue
    except Exception:  # noqa: BLE001 - fail-open: sin conteo no hay ETA
        return 0
    return total


def _calcular_eta(*, etapa: str, n_imagenes: int, shard_count: int,
                   transcurrido: float, fases_cerradas: int,
                   fases_total: int | None) -> int | None:
    """ETA en segundos, recalculada en CADA cambio de fase (nunca fijada al
    inicio). Asume fases de peso similar dentro de una etapa: es la
    aproximación que hay sin perfilado por fase, y es la razón del «aprox.»
    con el que la Suite pinta este dato.

    - Mientras no se haya cerrado ninguna fase, usa el presupuesto inicial
      derivado de `_SEG_POR_IMAGEN` (medido con N=8, de ahí el ×8 para
      des-escalar según el shard real).
    - En cuanto hay >=1 fase cerrada, es más fiable calibrar con el tiempo
      real transcurrido en ESTA máquina y ESTE vuelo que seguir fiándose de
      los coeficientes de otro dataset.
    """
    try:
        if fases_cerradas > 0 and fases_total:
            k = fases_cerradas
            total = fases_total
            if k >= total:
                return 0
            return max(0, int(round(transcurrido * (total - k) / k)))

        seg_por_imagen = _SEG_POR_IMAGEN.get(etapa)
        if seg_por_imagen is None or n_imagenes <= 0:
            return None
        presupuesto = seg_por_imagen * n_imagenes * 8 / max(shard_count, 1)
        return max(0, int(round(presupuesto - transcurrido)))
    except Exception:  # noqa: BLE001 - fail-open: sin ETA no pasa nada
        return None


def _formatear(segundos: float) -> str:
    """`1h 04m 12s`. Un vuelo entero se mide en decenas de minutos, y leer
    «3852.4 s» obliga a dividir a mano cada vez que se compara con otra corrida."""
    segundos = int(round(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    if horas:
        return f"{horas}h {minutos:02d}m {seg:02d}s"
    if minutos:
        return f"{minutos}m {seg:02d}s"
    return f"{seg}s"


class _EmisorNulo:
    """Emisor de progreso mudo para listar igual que el índice (con `_Limite`)."""

    def iniciar(self, *args, **kwargs) -> None:
        pass

    def progreso(self, *args, **kwargs) -> None:
        pass

    def aviso(self, *args, **kwargs) -> None:
        pass


def _verificar_hash_listado(origen: str, esperado: str) -> None:
    """Lista `origen` EXACTAMENTE como el índice (`indice._listar_imagenes`, con
    `_Limite`: sin filtro de «a medio copiar») y compara su hash con `esperado`.
    Si difiere, `SystemExit(2)` con `listado-cambiado: esperado=<h1> actual=<h2>`
    por stderr; nada se ha escrito todavía."""
    from atom_core import indice as indice_mod
    from atom_core.indice_mapreduce import hash_listado

    imagenes = indice_mod._listar_imagenes(origen, [], indice_mod._Limite(_EmisorNulo()), {})
    actual = hash_listado(imagenes)
    esperado = esperado.strip().lower()
    if actual != esperado:
        print(f"listado-cambiado: esperado={esperado} actual={actual}", file=sys.stderr)
        raise SystemExit(2)


def _main_copiar(parser, args) -> int:
    """`--fase copiar`: sale 0 aunque haya choques (van en el informe); !=0 solo
    ante error real (argumentos, o algún objeto que no se pudo copiar)."""
    from atom_core import copia_plantas
    if not args.destino_copia:
        parser.error("--fase copiar exige --destino-copia")
    if not es_uri_gcs(args.destino) or not es_uri_gcs(args.destino_copia):
        parser.error("--fase copiar exige --destino y --destino-copia en gs://")
    ejecucion = sharding.ejecucion_desde_entorno(explicita=args.ejecucion)
    if not ejecucion:
        parser.error("--fase copiar exige --ejecucion (o $CLOUD_RUN_EXECUTION) para nombrar el informe")
    try:
        inf = copia_plantas.ejecutar_fase(args.destino, args.destino_copia,
                                          solo_plan=args.solo_plan, ejecucion=ejecucion)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"copiar: copiados={inf.copiados} identicos={inf.identicos} "
          f"choques={len(inf.choques)} errores={len(inf.errores)} bytes={inf.bytes}"
          f"{' (solo plan)' if inf.solo_plan else ''}")
    if inf.errores:
        print(f"error: {len(inf.errores)} objeto(s) sin copiar; ver "
              f"{copia_plantas.ruta_informe(args.destino, ejecucion)}", file=sys.stderr)
        return 1
    return 0


def _main_sin_colocar(parser, args) -> int:
    """`--sin-colocar listar|asignar`: JSON por stdout (solo el JSON); errores por stderr con
    código 2. Local o `gs://` (mismo manifiesto que el índice/apply)."""
    from atom_core import sin_colocar
    destino = args.destino if es_uri_gcs(args.destino) else str(Path(args.destino).expanduser().resolve())
    try:
        if args.sin_colocar == "listar":
            if args.grupo or args.fila or args.quien:
                parser.error("--grupo/--fila/--quien no aplican a --sin-colocar listar")
            resultado = {"destino": destino, "grupos": sin_colocar.grupos_sin_colocar(
                destino, solo_pendientes=not args.todos, incluir_fotos=args.incluir_fotos)}
        else:
            if not args.grupo or args.fila is None or not args.quien:
                parser.error("--sin-colocar asignar exige --grupo, --fila y --quien")
            texto = args.fila.strip().lower()
            if texto in ("ninguna", "none", "null", "-"):
                fila = None
            else:
                try:
                    fila = int(texto)
                except ValueError:
                    parser.error(f"--fila debe ser un entero o `ninguna`: {args.fila!r}")
            resultado = sin_colocar.asignar_grupo(destino, args.grupo, fila, quien=args.quien)
    except (sin_colocar.ErrorSinColocar, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pipeline de ATOM Organizer sin interfaz gráfica.")
    parser.add_argument("--origen", default=None,
                        help="Carpeta del vuelo (la que se sube al bucket). Obligatorio "
                             "salvo con --fase copiar (donde NO se admite).")
    parser.add_argument("--destino", required=True,
                        help="Carpeta de salida. Se crea si no existe.")
    parser.add_argument("--estadillo", nargs="+", default=[], metavar="CSV",
                        help="CSV del estadillo. Sin él no se organiza en la "
                             "estructura de carpetas de la planta. Admite "
                             "varios (`--estadillo a.csv b.csv`): se fusionan "
                             "en el orden dado, que es el que decide quién "
                             "gana un solape de horario.")
    parser.add_argument("--task", default="split_images",
                        help="Task del pipeline (por defecto el completo).")
    parser.add_argument("--sin-tif", action="store_true",
                        help="Salta la conversión térmica a TIF. Útil para "
                             "medir solo la parte RGB.")
    parser.add_argument("--sin-miniaturas-termica", dest="sin_miniaturas_termica",
                        action="store_true",
                        help="Salta la fase de miniaturas/rotacion de la TERMICA. "
                             "Esa fase gira el JPG termico IN-PLACE antes de la "
                             "conversion a TIF y eso DESTRUYE el payload "
                             "radiometrico del R-JPEG (el giro bueno es el que va "
                             "despues del TIF). Obligatorio cuando la entrada son "
                             "R-JPEG originales de los que hay que sacar TIFF.")
    parser.add_argument("--sin-rotacion", dest="sin_rotacion",
                        action="store_true",
                        help="Salta ENTERO el subproceso ROTACION (el switch "
                             "maestro `gen_thumbnails`). Se anade en 3.4.49 "
                             "porque `--sin-miniaturas-termica` NO basta: deja "
                             "`carpetas_rot=[\"RGB\"]`, y si la entrada no tiene "
                             "carpeta RGB el fallback de "
                             "`pipeline.check_input_folder_and_iterate` rota la "
                             "RAIZ entera -- o sea la TERMICA -- e ignorando "
                             "`only_pb`, asi que las N tareas del Job se pisan "
                             "el mismo vuelo. El 2026-08-21 eso destruyo la "
                             "radiometria de 4.852 R-JPEG de CLARE. Desde 3.4.53 "
                             "frena ademas el giro IN-PLACE del JPG termico que "
                             "corre al final del TIF (`rotate_thermal_jpgs_in_place`): "
                             "tambien destruye el APP3/4/5 del R-JPEG y hasta "
                             "entonces se ejecutaba pese al flag. Contrapartida: "
                             "el JPG deja de casar visualmente con su TIFF.")
    parser.add_argument("--giro-tiff", dest="giro_tiff",
                        choices=("auto", "90", "-90", "ninguno"), default="auto",
                        help="Giro del TIFF termico. `auto` (por defecto) lee el "
                             "criterio de la tabla `vuelos` del manifiesto "
                             "(`.organizado/manifiesto.db`), que lo escribe el "
                             "subproceso ROTACION (el CSV `*_Videofiles.csv` de "
                             "versiones anteriores solo se lee, ya no se escribe); "
                             "con `--sin-rotacion` no hay criterio nuevo y, si "
                             "tampoco hay uno previo, `auto` da ERROR visible "
                             "y el TIFF sale sin girar (1280x1024). "
                             "`-90` fuerza 270 (antihorario, lo normal en vuelos "
                             "de inspeccion con la camara perpendicular: "
                             "GimbalYawDegree ~ -90), `90` fuerza horario y "
                             "`ninguno` fuerza sin giro. Los flags manuales mandan "
                             "sobre el criterio automatico "
                             "(`pipeline.degree_de_giro`).")
    parser.add_argument("--json", dest="como_json", action="store_true",
                        help="Resumen final en JSON en la última línea, para "
                             "que lo lea un script en vez de una persona.")
    parser.add_argument("--quiet", action="store_true",
                        help="Solo fases y resumen; sin el detalle por imagen.")
    # --- reparto entre N tareas -------------------------------------------
    # Una corrida completa de PLANTA_B son 33m 06s en una sola tarea de 8 vCPU, y
    # el 90 % es trabajo carpeta a carpeta sin dependencias. Con estas tres
    # opciones el MISMO Job se lanza tres veces (split -> struct -> post) y las
    # etapas repartibles se abren en N tareas paralelas. Ver atom_core/sharding.
    #
    # Los defaults dejan el comportamiento intacto: `--etapa todo` sin shard es
    # exactamente lo que hacía el CLI antes, que es lo que usa la app de
    # escritorio y lo que espera cualquier script existente.
    parser.add_argument("--etapa", choices=sharding.ETAPAS, default="todo",
                        help="Parte del pipeline a ejecutar. `todo` (por "
                             "defecto) = las fases de siempre, de principio a "
                             "fin. `split` = separación RGB/térmica (repartible). "
                             "`struct` = estructura de carpetas (una sola tarea). "
                             "`post` = recorte, meta, rotación y TIF (repartible).")
    parser.add_argument("--shard-index", dest="shard_index", default=None,
                        help="Índice de esta tarea, de 0 a shard-count-1. Por "
                             "defecto, $CLOUD_RUN_TASK_INDEX.")
    parser.add_argument("--shard-count", dest="shard_count", default=None,
                        help="Número total de tareas entre las que se reparte la "
                             "etapa. Por defecto, $CLOUD_RUN_TASK_COUNT (1 fuera "
                             "de Cloud Run).")
    parser.add_argument("--fase", choices=sharding.FASES, default="completo",
                        help="Parte del organizado (motor índice -> manifiesto -> apply "
                             "-> cierre) a ejecutar. `completo` (por defecto) = las tres "
                             "seguidas, como siempre. Para Cloud Run, en tres ejecuciones "
                             "encadenadas: `indice` (N tareas: cada una lee su tramo y la "
                             "última en terminar hace el reduce; `indice-reduce` = solo "
                             "ese reduce, a mano, 1 tarea) -> `apply` (N tareas, "
                             "reparto por imagen con --shard-count/CLOUD_RUN_TASK_COUNT; "
                             "`rgb` y `termica` son sus mitades por separado) -> `cierre` "
                             "(1 tarea, funde los deltas de las tareas de apply).")
    parser.add_argument("--destino-copia", dest="destino_copia", default=None, metavar="GS_URI",
                        help="Solo --fase copiar (OBLIGATORIO): raiz gs:// de las plantas "
                             "(la decide el backend, sin valor por defecto). Copia no-clobber "
                             "de --destino (carpeta SALIDA--…) a "
                             "<raiz>/<PLANTA>/INSPECCIONES/<TIPO>/<ANIO>/…; nunca borra. "
                             "Informe en <destino>/.organizado/copias/<ejecucion>.json.")
    parser.add_argument("--solo-plan", dest="solo_plan", action="store_true",
                        help="Solo --fase copiar: calcula y escribe el informe sin copiar nada.")
    parser.add_argument("--ejecucion", dest="ejecucion", default=None,
                        help="Identificador de la ejecución (fases apply/rgb/termica): "
                             "los deltas de cada tarea van a "
                             "<destino>/.organizado/shards/<ejecucion>/<shard>.jsonl. "
                             "Por defecto, $CLOUD_RUN_EXECUTION (que manda si existe).")
    parser.add_argument("--ejecucion-indice", dest="ejecucion_indice", default=None,
                        help="Solo con --fase indice-reduce: identificador de la ejecución "
                             "de `indice` (map) cuyos parciales se reducen "
                             "(<destino>/.organizado/indice/<ejecucion>/). Por defecto, "
                             "el valor de --ejecucion / $CLOUD_RUN_EXECUTION.")
    parser.add_argument("--hash-listado-esperado", dest="hash_listado_esperado", default=None,
                        metavar="HEX",
                        help="Solo fases apply/rgb/termica: hash (sha256 hex) del listado de "
                             "imagenes con el que se genero el indice. Si el origen lista "
                             "distinto, aborta con exit 2 (`listado-cambiado: esperado=… "
                             "actual=…`) SIN escribir nada en destino. Sin el, no se comprueba.")
    parser.add_argument("--shards", dest="shards", default=None, metavar="I,J,...",
                        help="Reintento parcial de apply/rgb/termica: indices ORIGINALES de "
                             "los shards a rehacer (p. ej. `3,7`). La task "
                             "CLOUD_RUN_TASK_INDEX=i (o --shard-index) procesa el shard "
                             "original shards[i] y reanuda desde su jsonl. Exige "
                             "--ejecucion (la ejecucion ORIGINAL, que manda sobre "
                             "$CLOUD_RUN_EXECUTION) y --shard-count = K ORIGINAL (el "
                             "$CLOUD_RUN_TASK_COUNT de este reintento se ignora).")
    parser.add_argument("--listado", dest="listado", default=None, metavar="GS_URI",
                        help="Solo fases indice/completo: jsonl.gz con el listado de imagenes "
                             "que SUSTITUYE al listado del origen (formato y hash en "
                             "`atom_core.indice_mapreduce.serializar_listado`). Exige "
                             "--hash-listado-esperado (aqui = sha256 del contenido "
                             "descomprimido del listado). Si no coincide o no se lee: error, "
                             "nunca se lista el origen.")
    parser.add_argument("--modo-destino", dest="modo_destino",
                        choices=["sobrescribir", "obviar"], default="sobrescribir",
                        help="Que hacer con lo que ya haya en el destino. "
                             "`sobrescribir` (por defecto) = la pasada actual "
                             "manda y pisa lo que encuentre en la misma ruta. "
                             "`obviar` = deja intacto lo que ya exista en esa "
                             "ruta y no mueve esa imagen. En ningun caso se "
                             "generan copias con sufijo.")
    parser.add_argument("--camara-rgb", dest="camara_rgb", choices=["Z", "W"], default=None,
                        help="Camara RGB que se recorta (_CROP) cuando la tanda trae RGB _W y "
                             "_Z (DJI H30T). Z = solo la zoom (la _W se copia sin recortar); "
                             "W = solo la wide (la _Z se copia sin recortar). Sin esta opcion "
                             "manda `[cropping] camara_rgb` de Config.ini y, si no esta, Z. "
                             "Sin _Z en la tanda no tiene efecto.")
    parser.add_argument("--simular", dest="simular", action="store_true",
                        help="Solo --fase apply: SIMULACION para medir tiempo y coste reales "
                             "sin tocar la planta. Lee, calcula y decide igual que el apply "
                             "real, pero NO escribe, copia, mueve ni borra nada en el "
                             "bucket/destino (ni deltas ni telemetria). Unica escritura: "
                             "<destino>/.organizado/simulaciones/<ejecucion>-task<idx>.json.")
    parser.add_argument(
        "--estadillo-manifest", dest="estadillo_manifest", default=None,
        help=("Ruta al manifest.json de la subida canonica (bucket de ENTRADA, "
              "montado en /gcs). Habilita que la ingesta a la Suite ligue el "
              "estadillo con la subida que lo origino. Opcional: sin el, el "
              "pipeline se comporta exactamente como antes."),
    )
    parser.add_argument(
        "--sin-colocar", dest="sin_colocar", choices=["listar", "asignar"], default=None,
        help=("Fotos fuera de rango del estadillo (SIN_ORDENAR). `listar`: imprime por stdout "
              "un JSON con los grupos pendientes del --destino (con la fila anterior y "
              "siguiente del estadillo). `asignar`: coloca un grupo (--grupo, --fila, --quien); "
              "el apply posterior escribe las fotos. No usa --origen."))
    parser.add_argument("--grupo", dest="grupo", default=None, metavar="GRUPO_ID",
                        help="Con --sin-colocar asignar: id del grupo (`grupo_id` del listado).")
    parser.add_argument("--fila", dest="fila", default=None, metavar="N|ninguna",
                        help="Con --sin-colocar asignar: fila del estadillo (`vecino_*.fila`) o "
                             "`ninguna` para dejar el grupo en SIN_ORDENAR a propósito.")
    parser.add_argument("--quien", dest="quien", default=None,
                        help="Con --sin-colocar asignar: quién asigna (queda en el manifiesto).")
    parser.add_argument("--todos", dest="todos", action="store_true",
                        help="Con --sin-colocar listar: incluye también los grupos ya decididos.")
    parser.add_argument("--incluir-fotos", dest="incluir_fotos", action="store_true",
                        help="Con --sin-colocar listar: añade `claves` de las fotos de cada grupo.")
    args = parser.parse_args(argv)

    if args.sin_colocar:
        return _main_sin_colocar(parser, args)
    if args.grupo or args.fila or args.quien or args.todos or args.incluir_fotos:
        parser.error("--grupo/--fila/--quien/--todos/--incluir-fotos solo aplican a --sin-colocar")

    if args.fase == "copiar":
        if args.origen is not None:
            parser.error("--fase copiar no usa --origen (parte de --destino): quitalo")
        return _main_copiar(parser, args)
    if args.origen is None:
        parser.error("--origen es obligatorio (salvo con --fase copiar)")
    if args.destino_copia is not None or args.solo_plan:
        parser.error("--destino-copia/--solo-plan solo aplican a --fase copiar")

    # El shard explícito manda sobre el entorno: así se puede reproducir en local
    # exactamente la partición que le tocó a una tarea concreta del Job.
    if args.shard_index is None and args.shard_count is None:
        shard_index, shard_count = sharding.shard_desde_entorno()
    else:
        shard_index, shard_count = sharding.normalizar_shard(
            args.shard_index if args.shard_index is not None else 0,
            args.shard_count if args.shard_count is not None else 1)

    if args.simular and args.fase != "apply":
        parser.error("--simular solo aplica a --fase apply")
    if args.simular and args.estadillo:
        parser.error("--simular no admite --estadillo (no se ingiere nada en la Suite)")
    if args.simular and not sharding.ejecucion_desde_entorno(explicita=args.ejecucion):
        parser.error("--simular exige --ejecucion o $CLOUD_RUN_EXECUTION: el informe se "
                     "nombra <ejecucion>-task<idx>.json y no puede pisar el de otra ejecucion")
    if args.simular:
        # ANTES de cualquier E/S o llamada del reporter: desde aqui nada escribe en planta.
        from atom_core import simulacion
        simulacion.activar()
        print("SIMULACIÓN: no se escribe nada en planta", flush=True)

    if args.shards is not None:
        if args.fase not in ("apply", "rgb", "termica"):
            parser.error("--shards solo aplica a las fases apply/rgb/termica")
        if not (args.ejecucion or "").strip():
            parser.error("--shards exige --ejecucion (la ejecucion original)")
        if args.shard_count is None:
            parser.error("--shards exige --shard-count = K original")
        try:
            originales = [int(x) for x in args.shards.split(",")]
            k_original = int(args.shard_count)
        except ValueError:
            parser.error("--shards debe ser una lista de enteros (`3,7`) y --shard-count un entero")
        if (not originales or len(set(originales)) != len(originales)
                or any(o < 0 or o >= k_original for o in originales)):
            parser.error(f"--shards {args.shards!r}: indices repetidos o fuera de 0..{k_original - 1}")
        try:
            i_task = int(args.shard_index if args.shard_index is not None
                         else os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
        except ValueError:
            parser.error("indice de task ilegible (--shard-index / CLOUD_RUN_TASK_INDEX)")
        if not 0 <= i_task < len(originales):
            parser.error(f"la task {i_task} no tiene shard: --shards lista {len(originales)}")
        shard_index, shard_count = originales[i_task], k_original

    # `gs://…` viaja como STRING TAL CUAL: `Path(...).resolve()` destroza el
    # esquema (`<cwd>/gs:/bucket/x`). Las rutas locales conservan byte a byte
    # el comportamiento de siempre (mismo `expanduser().resolve()`, mismo
    # `is_dir`/`is_file`, mismo `mkdir`).
    origen_es_gcs = es_uri_gcs(args.origen)
    destino_es_gcs = es_uri_gcs(args.destino)
    origen = args.origen if origen_es_gcs else Path(args.origen).expanduser().resolve()
    destino = args.destino if destino_es_gcs else Path(args.destino).expanduser().resolve()
    origen_existe = es_carpeta(origen) if origen_es_gcs else origen.is_dir()
    if not origen_existe:
        print(f"error: la carpeta de origen no existe: {origen}", file=sys.stderr)
        return 2
    faltantes_estadillo = [
        p for p in args.estadillo
        if not (existe_ruta(p) if es_uri_gcs(p) else Path(p).expanduser().is_file())
    ]
    if faltantes_estadillo:
        print(f"error: el estadillo no existe: {', '.join(faltantes_estadillo)}",
              file=sys.stderr)
        return 2

    # Estadillo OBLIGATORIO: sin él, el pipeline revienta a mitad de la fase
    # de estructura de carpetas (`combinar_estadillos`). Se corta AQUÍ, antes
    # de crear el destino o importar el pipeline, con la UNIÓN de los
    # estadillos autodetectados en `origen` y los pasados a mano por
    # `--estadillo` —NUNCA solo autodetección: `detectar_estadillos` falla en
    # algunos lotes (PLANTA_F) y si se aporta la ruta a mano el lote tiene que
    # poder organizarse igual.
    from atom_core.estadillo import detectar_estadillos, detectar_estadillos_en_padre
    rutas_autodetectadas = [] if origen_es_gcs else detectar_estadillos(str(origen))["rutas"]
    # El apply ya no usa el estadillo (el indice lo decidio): --simular no lo exige.
    if not rutas_autodetectadas and not args.estadillo and not args.simular:
        # NUNCA se coge el estadillo del padre en automático (decisión de
        # el responsable): si hay candidatos sueltos ahí, se avisa con
        # su ruta completa pero solo se usan pasándolos por --estadillo.
        candidatos_padre = [] if origen_es_gcs else detectar_estadillos_en_padre(str(origen))["rutas"]
        if candidatos_padre:
            print("error: No se ha encontrado ningún estadillo DENTRO de la carpeta de "
                  "origen. Hay candidato(s) en la carpeta PADRE, pero no se usan en "
                  "automático: " + ", ".join(candidatos_padre) + ". Si quieres usar uno, "
                  "pásalo explícito con --estadillo.",
                  file=sys.stderr)
        else:
            print("error: No se ha encontrado ningún estadillo: sin estadillo el "
                  "lote no se puede organizar. Indica la ruta del estadillo con "
                  "--estadillo o colócalo junto a la carpeta de fotos.",
                  file=sys.stderr)
        return 2

    # Antes de crear el destino: si el listado cambió desde el índice, no se
    # escribe NADA (ni la carpeta de destino).
    if args.listado is not None:
        if args.fase not in ("completo", "indice"):
            parser.error("--listado solo aplica a las fases completo/indice")
        if not (args.hash_listado_esperado or "").strip():
            parser.error("--listado exige --hash-listado-esperado")
    elif args.hash_listado_esperado is not None:
        if not args.hash_listado_esperado.strip():
            parser.error("--hash-listado-esperado vacío")
        if args.fase not in ("apply", "rgb", "termica"):
            parser.error("--hash-listado-esperado solo aplica a las fases apply/rgb/termica")
        _verificar_hash_listado(str(origen), args.hash_listado_esperado)

    # En GCS no hay directorios: nada que crear de antemano.
    if not destino_es_gcs and not args.simular:
        destino.mkdir(parents=True, exist_ok=True)

    # Import aquí y no arriba: `atom_core.organize` arrastra el pipeline entero
    # (numpy, PIL, el SDK térmico). Si el usuario solo pidió `--help`, no tiene
    # sentido pagar varios segundos de imports para imprimirlo. Igual por lo
    # mismo con `atom_core.estadillo` (importa pandas).
    from atom_core.organize import run_task
    from atom_core.estadillo import empaquetar_rutas

    # `estadillo` sigue viajando como un único string (es lo que espera
    # `atom_core.organize`/`GenStructFolderConfig.estad`, que no se toca en
    # este cambio): con 1 sola ruta `empaquetar_rutas` la devuelve tal cual,
    # así que el caso de siempre no cambia ni un byte.
    params: dict = {"origen": str(origen), "destino": str(destino),
                    "estadillo": empaquetar_rutas(args.estadillo),
                    "etapa": args.etapa,
                    "fase": args.fase,
                    # Con --shards la ejecucion ORIGINAL manda sobre la del entorno.
                    "ejecucion": (args.ejecucion.strip() if args.shards is not None
                                  else sharding.ejecucion_desde_entorno(explicita=args.ejecucion)),
                    "listado": ([args.listado, args.hash_listado_esperado.strip().lower()]
                                if args.listado is not None else None),
                    "ejecucion_indice": args.ejecucion_indice or "",
                    "modo_destino": args.modo_destino,
                    "shard_index": shard_index, "shard_count": shard_count}
    if args.camara_rgb:
        params["camara_rgb"] = args.camara_rgb
    avanzado: dict = {}
    if args.sin_tif:
        avanzado["convert_to_tif"] = False
    if args.sin_miniaturas_termica:
        avanzado["gen_thumbnails_termica"] = False
    if args.sin_rotacion:
        avanzado["gen_thumbnails"] = False
    if args.giro_tiff != "auto":
        # Import perezoso: `utils` arrastra pandas/PIL y este modulo evita a
        # proposito los imports pesados fuera de la ruta de ejecucion real
        # (mismo motivo por el que `atom_core.organize` se importa dentro de
        # `main`). Aqui ya estamos en esa ruta.
        import utils as _utils
        # El sentinel es obligatorio: sin el, `resolve_tif_rotation_intent`
        # descarta los tres bools por no poder distinguir "el usuario eligio
        # sin giro" de "front viejo que los manda a false por defecto".
        avanzado[_utils.TIF_ROTATION_INTENT_KEY] = args.giro_tiff
        avanzado["convert_to_tiff_rotate_auto"] = False
        avanzado["convert_to_tiff_rotate_90"] = args.giro_tiff == "90"
        avanzado["convert_to_tiff_rotate_minus_90"] = args.giro_tiff == "-90"
        # La task generica `convert_to_tif` usa otros tres campos con el mismo
        # significado (phases.py:1190-1202); se fijan iguales para que la flag
        # valga en las dos rutas.
        avanzado["rotate_auto"] = False
        avanzado["rotate_90"] = args.giro_tiff == "90"
        avanzado["rotate_minus_90"] = args.giro_tiff == "-90"
    avanzado = avanzado or None

    # Solo se reporta a la Suite si hay secreto: sin él (un dev corriendo el
    # CLI en local) `reporter` queda `None` y todo lo de abajo es no-op.
    #
    # Fail-open SÍ, mudo NO. La telemetría no puede tumbar un pipeline de
    # minutos u horas, así que cualquier fallo suyo se traga; pero se traga
    # DICIÉNDOLO. El modo de fallo que motivó esto fue una imagen publicada sin
    # `run_reporter.py`: el Job corría verde y la Suite no veía el run, sin un
    # solo indicio de que faltaba nada. `_telemetria_motivo` es None mientras
    # todo va bien y guarda la razón en cuanto deja de ir.
    reporter = None
    telemetria_motivo: str | None = None
    n_imagenes = _contar_imagenes(origen)
    secreto = os.environ.get("ORGANIZER_INGEST_SECRET")
    en_cloud_run = bool(os.environ.get("CLOUD_RUN_EXECUTION"))
    if args.simular:
        # Simulacion: sin RunReporter (ni alta, fase, progreso, fin ni estadillo) para
        # que no aparezca nada en la Suite.
        telemetria_motivo = "simulacion: telemetria a la Suite desactivada"
    elif secreto:
        try:
            from atom_core.run_reporter import RunReporter
        except Exception as exc:  # noqa: BLE001 - imagen sin el modulo: avisar, no morir
            RunReporter = None
            telemetria_motivo = (
                f"no se pudo importar atom_core.run_reporter "
                f"({type(exc).__name__}: {exc}); imagen sin telemetria?")

        if RunReporter is not None:
            # Sin `base`: RunReporter ya resuelve el destino desde ATOM_SUITE_API_BASE.
            # Pasarlo aquí duplicaba esa lógica y con el nombre equivocado
            # (`ATOM_SUITE_API`, que no existe): apuntar el Job a dev seteando esa
            # variable se ignoraba en silencio y los latidos se iban a PROD.
            reporter = RunReporter(auth=None, secreto=secreto, tipo="pipeline")
            extra_inicio = {
                "execution_name": os.environ.get("CLOUD_RUN_EXECUTION"),
                "shard_count": shard_count,
                "shard_index": shard_index,
            }
            extra_inicio = {k: v for k, v in extra_inicio.items() if v is not None}
            reporter.iniciar(inspeccion=nombre_de(str(origen)), etapa=args.etapa,
                              items_total=n_imagenes, **extra_inicio)
            # `iniciar` es fail-open por dentro: si la Suite no contestó, el run
            # no existe y todo lo demás sería no-op. Preguntarlo aquí es la
            # diferencia entre "no hay run" y "no sabemos que no hay run".
            if not reporter.activo:
                reporter = None
                telemetria_motivo = ("la Suite no acepto el alta del run "
                                     "(red, 401 o 5xx); no habra progreso en /organizer")
    elif en_cloud_run:
        # En local no tener secreto es lo normal y no merece ruido. Dentro de
        # Cloud Run sí: significa que la Suite lanzó el Job sin inyectar
        # ORGANIZER_INGEST_SECRET por containerOverrides.
        telemetria_motivo = "sin ORGANIZER_INGEST_SECRET en el entorno del Job"

    if telemetria_motivo:
        print(f"[telemetria] DESACTIVADA: {telemetria_motivo}",
              file=sys.stderr, flush=True)
        # Fail-closed dentro de Cloud Run. Un Job sin telemetria corre horas,
        # gasta CPU y escribe en el bucket sin dejar rastro en /organizer: nadie
        # ve el progreso ni el fallo, y la Suite se queda con un run fantasma.
        # Fuera de Cloud Run (dev con el CLI a mano) sigue siendo fail-open, y
        # ORGANIZER_TELEMETRIA_OPCIONAL=1 es la valvula para lanzar el Job a
        # proposito sin Suite detras.
        if en_cloud_run and os.environ.get("ORGANIZER_TELEMETRIA_OPCIONAL") != "1":
            print("[telemetria] abortando: el Job no arranca sin telemetria "
                  "(usa ORGANIZER_TELEMETRIA_OPCIONAL=1 para saltarselo)",
                  file=sys.stderr, flush=True)
            return 2

    fases: list[dict] = []
    errores: list[str] = []
    # Ultimo snapshot de `StatsTracker`. Los contadores rgb/termica/rot* son
    # acumulados de run (nunca se resetean entre fases), asi que el ultimo que
    # llega es el total del run y es el que se adjunta al cierre.
    ultimo_stats: dict | None = None
    arranque = time.monotonic()
    fase_desde = arranque

    def emit(kind: str, payload=None) -> None:
        nonlocal fase_desde, ultimo_stats
        if kind == "phase" and isinstance(payload, dict):
            ahora = time.monotonic()
            if fases:
                fases[-1]["segundos"] = round(ahora - fase_desde, 1)
            fase_desde = ahora
            nombre = payload.get("name", "?")
            fases.append({"nombre": nombre, "segundos": None})
            print(f"[fase {payload.get('index')}/{payload.get('total')}] {nombre}",
                  flush=True)
            if reporter is not None:
                index = payload.get("index")
                total = payload.get("total")
                fases_cerradas = sum(1 for f in fases if f["segundos"] is not None)
                eta = _calcular_eta(
                    etapa=args.etapa, n_imagenes=n_imagenes,
                    shard_count=shard_count,
                    transcurrido=ahora - arranque,
                    fases_cerradas=fases_cerradas, fases_total=total)
                reporter.fase(index=index, total=total, nombre=nombre,
                              eta_segundos=eta)
        elif kind == "error":
            errores.append(str(payload))
            print(f"[error] {payload}", file=sys.stderr, flush=True)
        elif kind == "done" and isinstance(payload, dict):
            # El pipeline NO aborta ante fallos por-imagen: los cuenta y sigue, y
            # `run_task` los agrega aquí. Sin esta rama el CLI solo miraba el canal
            # `error` (excepciones que se propagan) y salía 0 aunque hubiese fallado
            # el 100% de las imágenes: en v3.4.24 las 3.743 térmicas de PLANTA_B se
            # quedaron sin un solo TIFF y Cloud Run marcó la ejecución como ÉXITO.
            # Un vuelo con imágenes perdidas es un fallo del vuelo, no un aviso.
            n = int(payload.get("errors") or 0)
            if n:
                errores.append(
                    f"el pipeline terminó con {n} error(es) por imagen/carpeta "
                    f"(status={payload.get('status')}); revisa «Imágenes con error» "
                    f"en el resumen de arriba")
            print(f"[done] {payload}", flush=True)
        elif kind in ("stats", "summary", "plan"):
            if kind == "stats" and isinstance(payload, dict):
                ultimo_stats = payload
            if kind == "stats" and reporter is not None:
                # El único punto del CLI con avance por-imagen. Sin esto
                # `RunReporter.progreso` no lo llamaba NADIE y `items_hechos`
                # salía NULL en el 100% de los runs: /organizer mostraba la fase
                # pero jamás cuántas imágenes llevaba dentro de ella.
                # `progreso` ya trae throttle propio (1 latido cada
                # `intervalo_latido`) y manda el PATCH en un hilo aparte, así
                # que llamarlo en cada snapshot no frena al pipeline.
                # El snapshot de cierre viene marcado con `final` (ver
                # `organize._emit_stats`) y `progreso_desde_stats` lo propaga
                # solo: sin esa marca el throttle se come el último latido y el
                # run queda persistido a medias.
                cuerpo = progreso_desde_stats(payload)
                if cuerpo is not None:
                    reporter.progreso(cuerpo)
            if not args.quiet:
                print(f"[{kind}] {payload}", flush=True)
        elif kind == "log" and not args.quiet:
            print(payload, flush=True)
        elif kind == "giros" and isinstance(payload, dict):
            lista = payload.get("giros") or []
            for g in lista:
                vuelo = g.get("vuelo", "?")
                grados = g.get("grados")
                imagenes = g.get("imagenes")
                modo = g.get("modo")
                if grados:
                    print(f"[giros] {vuelo}: {grados}º ({imagenes} imagenes, {modo})",
                          flush=True)
                else:
                    print(f"[giros] {vuelo}: sin giro ({imagenes} imagenes, {modo})",
                          flush=True)
            if reporter is not None:
                reporter.giros(lista)

        # Espejo a la Suite: los logs del Job solo vivian en Cloud Run, ilegibles
        # desde un servidor de desarrollo. Va DESPUES del print y en su propio try: si el envio
        # falla, la salida estandar (que es lo que Cloud Run captura) no se toca.
        try:
            if reporter is not None and reporter.activo:
                nivel = "error" if kind == "error" else "info"
                reporter.log(f"[{kind}] {payload}", nivel=nivel)
        except Exception:  # noqa: BLE001 - fail-open
            pass

    print(f"origen   : {origen}")
    print(f"destino  : {destino}")
    print(f"estadillo: {', '.join(args.estadillo) if args.estadillo else '(ninguno)'}")
    print(f"task     : {args.task}")
    # La etapa y el shard van en la cabecera porque sin ellos un log de una tarea
    # de Cloud Run es indistinguible del de otra: las N escriben lo mismo salvo
    # QUÉ carpetas les tocaron, y al diagnosticar un vuelo incompleto lo primero
    # que hace falta saber es si una tarea se quedó sin trabajo.
    print(f"etapa    : {args.etapa}")
    print(f"fase     : {args.fase}")
    print(f"destino  : modo {args.modo_destino}", flush=True)
    print(f"shard    : {shard_index + 1}/{shard_count}\n", flush=True)

    try:
        run_task(args.task, params, emit, avanzado)
    except Exception as exc:  # noqa: BLE001 - el CLI reporta, no propaga traza cruda
        errores.append(f"{type(exc).__name__}: {exc}")
        print(f"[error] {type(exc).__name__}: {exc}", file=sys.stderr)

    total = time.monotonic() - arranque
    if fases and fases[-1]["segundos"] is None:
        fases[-1]["segundos"] = round(time.monotonic() - fase_desde, 1)

    print("\n" + "=" * 60)
    for f in fases:
        print(f"  {f['nombre']:<32} {_formatear(f['segundos'] or 0):>10}")
    print(f"  {'TOTAL':<32} {_formatear(total):>10}")
    if errores:
        print(f"\n  {len(errores)} error(es); el primero: {errores[0]}")

    if args.como_json:
        print(json.dumps({
            "ok": not errores, "segundos": round(total, 1), "fases": fases,
            "errores": errores, "origen": str(origen), "destino": str(destino),
            "etapa": args.etapa, "shard_index": shard_index,
            "shard_count": shard_count,
            "host": platform.node(), "cpus": os.cpu_count(),
            # Lo consume quien recoja la salida del Job: `false` aquí es la
            # prueba en frío de que este run no llegó a /organizer, y el motivo
            # evita tener que ir a bucear en los logs para saber por qué.
            "telemetria": reporter is not None,
            "telemetria_motivo": telemetria_motivo,
        }, ensure_ascii=False))

    if reporter is not None:
        try:
            reporter.fin(ok=not errores, error=errores[0] if errores else None,
                         stats=ultimo_stats)
        except Exception:  # noqa: BLE001 - la telemetría no puede tocar el exit code
            pass

    # Notifica el estadillo a la Suite (misiones+vuelos), en las mismas
    # condiciones que el resto de telemetria. `ruta_manifest` ya SI esta
    # disponible en este scope cuando la subida fue canonica (--estadillo-
    # manifest), asi que la ingesta queda ligada a la subida que la origino;
    # antes se mandaba None porque el CLI solo conocia rutas de CSV.
    # Sigue sin haber `planta_id`/`inspeccion_id` numericos aqui: el backend
    # los acepta como None.
    if reporter is not None and args.estadillo:
        try:
            from atom_core.estadillo import combinar_estadillos, filas_para_suite
            df_estadillo = combinar_estadillos(args.estadillo)
            reporter.estadillo(
                filas_para_suite(df_estadillo),
                ruta_manifest=args.estadillo_manifest,
            )
        except Exception:  # noqa: BLE001 - fail-open
            pass

    return 1 if errores else 0


if __name__ == "__main__":
    # Igual que en `app_webview.py`: el pipeline usa ProcessPoolExecutor y en
    # Windows el start method es `spawn`, así que el hijo re-ejecuta este mismo
    # fichero. Sin esto, el pool se rompe entero en cuanto hay recorte RGB.
    multiprocessing.freeze_support()
    sys.exit(main())
