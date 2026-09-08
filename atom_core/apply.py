"""Apply del organizado: escribe las imágenes desde el manifiesto.

Recorre el manifiesto (Tarea 1) y materializa lo que el índice (Tarea 3) ya
decidió sobre cada fila: nombre, ángulo de giro, recorte y rutas de salida.
Este módulo NO decide nada — si lo hiciera, apply e índice podrían discrepar
sobre qué le toca a una imagen.

Hoy una RGB pasa por ~3 ciclos decode+encode (compresión, recorte, rotación)
más una copia y un move. Aquí se abre UNA sola vez y se escribe directo a su
carpeta final: `_escribir_salidas_de_fila` reutiliza
`pipeline._procesar_y_guardar_imagen` (Tarea 4, correcciones §3) para el
original y su `_CROP`, sobre el MISMO objeto `Image` abierto.

Preparado para que la Tarea 5 (térmicas) añada `aplicar_termicas` a este
mismo fichero sin refactor: `_AforoDinamico` es el mismo mecanismo de
aforo adaptativo que usará el `ThreadPoolExecutor` de las térmicas.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait as _esperar_futuros
from typing import Any, Mapping

import external_tools
import pipeline

#: MB que se le presupone a un item de RGB para el reporte al controlador
#: adaptativo. Si no se puede leer el tamaño real del origen (p. ej. se
#: borró entre el índice y el apply), no se revienta el run por eso.
_MB_POR_DEFECTO_SI_FALLA_STAT = 0.0


def _tamano_mb(ruta: str) -> float:
    try:
        return os.path.getsize(ruta) / (1024 * 1024)
    except OSError:
        return _MB_POR_DEFECTO_SI_FALLA_STAT


def _transpose_para_angulo(angulo: int, pipeline_mod) -> "int | None":
    """Mapea el ángulo de giro (0/90/270, criterio horario) a la constante de
    PIL con la que hay que llamar a `transpose`. PIL gira en sentido
    ANTIhorario: un giro de 90 se corrige con `ROTATE_270` y al revés — es
    el MISMO mapeo que ya usa `pipeline.py` para RGB (`pipeline.py:2075-2091`)
    y para térmicas (`pipeline.py:3214-3216`); si divergiera aquí, la imagen
    saldría del revés.
    """
    if angulo == 90:
        return pipeline_mod.Image.ROTATE_270
    if angulo == 270:
        return pipeline_mod.Image.ROTATE_90
    return None


def _guardar_atomico(img, destino: str, transpose, pct_recorte, calidad: int,
                      pipeline_mod) -> None:
    """Guarda `img` (con el crop/giro que le toque) en `destino` escribiendo
    primero a `<destino>.parcial` y renombrando con `os.replace`. Un fallo a
    mitad de un `save()` (disco lleno, JPEG corrupto al escribir, lo que
    sea) no puede dejar un fichero truncado en la carpeta de entrega: o el
    `.parcial` desaparece, o `destino` queda completo. Nunca un intermedio.
    """
    carpeta = os.path.dirname(destino)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    # `.parcial` va ANTES de la extensión real (`DJI_0001.parcial.JPG`), no
    # después (`DJI_0001.JPG.parcial`): PIL decide el formato de guardado
    # por la extensión del nombre de fichero (`ImageProcessConfig` no tiene
    # parámetro `format`, y no se toca `pipeline.py` para añadirlo — ver
    # reutilización obligatoria) y `<algo>.parcial` sin extensión reconocida
    # revienta con `ValueError: unknown file extension: .parcial`.
    raiz, extension = os.path.splitext(destino)
    parcial = f"{raiz}.parcial{extension}"
    cfg_escritura = pipeline_mod.ImageProcessConfig(
        output_path=parcial,
        quality=calidad,
        crop_centered_pct=pct_recorte,
        rotate_degrees=transpose,
    )
    try:
        pipeline_mod._procesar_y_guardar_imagen(img, cfg_escritura)
    except Exception:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise
    os.replace(parcial, destino)


def _escribir_salidas_de_fila(fila: Mapping[str, Any], cfg, pipeline_mod) -> str:
    """Escribe el original y su `_CROP` (si la fila lleva) desde UN solo
    decode. Devuelve la cadena de verificación (ruta:tamaño de cada fichero
    escrito), que el manifiesto guarda en `verificacion` al marcar la fila
    hecha.

    Orden de escritura — NO es arbitrario: el `_CROP` se escribe SIEMPRE
    antes que el original. `pipeline._procesar_y_guardar_imagen` reasigna su
    variable local `img` solo si aplica `crop` o `transpose`, y cierra esa
    variable local al terminar (`finally: img.close()`). El `_CROP` siempre
    aplica `crop_centered_pct`, así que su cierre nunca toca el `img` de
    fuera. El original, cuando el ángulo es 0, no aplica NINGUNA
    transformación — su llamada recibiría literalmente el mismo objeto que
    pasamos, y sería ESE el que se cerraría. Escribirlo el último es lo que
    permite que ambas salidas compartan el mismo decode sin que la primera
    escritura cierre el fichero que necesita la segunda.
    """
    angulo = fila["angulo_giro"] or 0
    transpose = _transpose_para_angulo(angulo, pipeline_mod)
    calidad = pipeline_mod._ROTATION_JPEG_QUALITY if angulo else cfg.compress_level

    img = pipeline_mod.Image.open(fila["ruta_origen"])
    escritas: list[str] = []
    try:
        ruta_crop = fila["ruta_salida_crop"]
        if ruta_crop:
            _guardar_atomico(img, ruta_crop, transpose, fila["pct_recorte"], calidad,
                             pipeline_mod)
            escritas.append(ruta_crop)

        ruta_original = fila["ruta_salida_original"]
        _guardar_atomico(img, ruta_original, transpose, None, calidad, pipeline_mod)
        escritas.append(ruta_original)
    finally:
        # Defensivo: si el original ya cerró `img` (caso ángulo 0, ver
        # docstring), un segundo `close()` sobre una imagen PIL ya cerrada
        # es un no-op seguro.
        try:
            img.close()
        except Exception:
            pass

    return "; ".join(f"{ruta}:{os.path.getsize(ruta)}" for ruta in escritas)


def _trabajo_fila(fila: dict, cfg) -> str:
    """Función de MÓDULO, picklable: es la que se manda al
    `ProcessPoolExecutor`. No recibe el módulo `pipeline` como argumento —
    un módulo no es picklable (`TypeError: cannot pickle 'module' object`,
    comprobado) — así que usa el `pipeline` importado a nivel de fichero,
    que un proceso hijo `spawn` reimporta solo con reimportar este módulo,
    igual que hace `pipeline.process_one_image` con sus propios workers.
    """
    return _escribir_salidas_de_fila(fila, cfg, pipeline)


class _AforoDinamico:
    """Envoltorio sobre un `threading.Semaphore` cuyo número de permisos
    puede crecer o encogerse en caliente, siguiendo a
    `controlador.trabajadores`. Un semáforo normal solo sabe crecer (con
    `release()` de más); para encogerlo hay que "comerse" un permiso sin
    devolverlo la próxima vez que se libera uno — eso es lo que hace
    `ajustar()` cuando el nuevo objetivo es menor que el actual.
    """

    def __init__(self, permisos_iniciales: int) -> None:
        permisos_iniciales = max(1, permisos_iniciales)
        self._semaforo = threading.Semaphore(permisos_iniciales)
        self._lock = threading.Lock()
        self._objetivo = permisos_iniciales
        self._en_circulacion = permisos_iniciales

    def adquirir(self) -> None:
        self._semaforo.acquire()

    def liberar(self) -> None:
        with self._lock:
            if self._en_circulacion > self._objetivo:
                # Encogiendo: este permiso no vuelve al semáforo.
                self._en_circulacion -= 1
                return
        self._semaforo.release()

    def ajustar(self, nuevo_objetivo: int) -> None:
        nuevo_objetivo = max(1, nuevo_objetivo)
        with self._lock:
            crecimiento = nuevo_objetivo - self._objetivo
            self._objetivo = nuevo_objetivo
            if crecimiento > 0:
                self._en_circulacion += crecimiento
                for _ in range(crecimiento):
                    self._semaforo.release()


def aplicar_rgb(manifiesto, cfg, pipeline_mod, progress_callback, progress_bar,
                progress_summarize, controlador=None) -> dict:
    """Recorre el manifiesto y escribe las salidas RGB pendientes.

    Reanudable por construcción: solo toca filas en estado 'pendiente'
    (`manifiesto.pendientes()`), así que una fila 'hecha' de un run anterior
    no se reabre ni se reescribe — un segundo `aplicar_rgb` sobre el mismo
    manifiesto es gratis.

    Sin `controlador` corre en secuencial, en el mismo proceso: es el
    camino que usan los tests (deterministas, sin dependencias de
    multiproceso) y el que se activa automáticamente si el índice no ha
    entregado ninguno — nunca revienta por falta de uno.

    Con `controlador`, el trabajo (CPU-bound: decode/encode) va a un
    `ProcessPoolExecutor` con el número de tareas en vuelo limitado por un
    `_AforoDinamico` que sigue a `controlador.trabajadores`; el pool en sí
    NO se redimensiona nunca (un `ProcessPoolExecutor` no se puede
    redimensionar), solo el aforo con el que se le echa trabajo.

    Returns:
    --------
    dict con "hecho" y "fallido": cuántas filas de RGB acabaron en cada
    estado en esta pasada.
    """
    progress_summarize.emit("---> SUBPROCESO: Escritura de imágenes RGB")

    filas = [dict(fila) for fila in manifiesto.pendientes() if fila["tipo"] == "RGB"]
    resultado = {"hecho": 0, "fallido": 0}
    total = len(filas)
    if total == 0:
        return resultado

    def _cerrar_fila(fila: dict, verificacion=None, error: Exception = None) -> None:
        if error is not None:
            manifiesto.marcar_fallida(fila["id"], str(error))
            resultado["fallido"] += 1
        else:
            manifiesto.marcar_hecha(fila["id"], verificacion)
            resultado["hecho"] += 1
        if controlador is not None:
            controlador.registrar(mb=_tamano_mb(fila["ruta_origen"]))
            controlador.revisar()

    if controlador is None:
        for indice, fila in enumerate(filas, start=1):
            manifiesto.marcar_en_curso(fila["id"])
            try:
                verificacion = _escribir_salidas_de_fila(fila, cfg, pipeline_mod)
            except Exception as exc:
                _cerrar_fila(fila, error=exc)
            else:
                _cerrar_fila(fila, verificacion=verificacion)
            progress_bar.emit(int(indice / total * 100))
        return resultado

    # --- Camino paralelo -----------------------------------------------
    aforo = _AforoDinamico(controlador.trabajadores)
    lock_progreso = threading.Lock()
    estado_progreso = {"completadas": 0}

    def _al_terminar(fila: dict, futuro) -> None:
        try:
            verificacion = futuro.result()
        except Exception as exc:
            _cerrar_fila(fila, error=exc)
        else:
            _cerrar_fila(fila, verificacion=verificacion)
        aforo.ajustar(controlador.trabajadores)
        aforo.liberar()
        with lock_progreso:
            estado_progreso["completadas"] += 1
            progress_bar.emit(int(estado_progreso["completadas"] / total * 100))

    with ProcessPoolExecutor(max_workers=controlador.maximo) as executor:
        futuros = []
        for fila in filas:
            aforo.adquirir()
            manifiesto.marcar_en_curso(fila["id"])
            futuro = executor.submit(_trabajo_fila, fila, cfg)
            futuro.add_done_callback(lambda futuro, fila=fila: _al_terminar(fila, futuro))
            futuros.append(futuro)

        _esperar_futuros(futuros)

    return resultado


# --- Térmicas: dji_irp + metadatos -----------------------------------------
# Las térmicas no son CPU del intérprete: son espera a dos procesos externos
# (dji_irp/libdirp.so para el TIFF, exiftool para el EXIF). Por eso van en
# ThreadPoolExecutor, no en procesos.


def _copiar_jpg_destino(origen: str, destino: str) -> None:
    """Copia el JPG térmico de origen a su ruta final, de forma atómica
    (`<raíz>.parcial<ext>` + `os.replace`). El JPG NO se gira aquí: es un
    R-JPEG con el payload radiométrico propietario del SDK, y re-guardarlo
    con PIL lo destruiría (`pipeline.rotate_thermal_jpgs_in_place`, que hace
    ese giro en el motor viejo, corre DESPUÉS y sobre una copia ya sin
    payload). Esta función solo lo materializa en su carpeta final."""
    carpeta = os.path.dirname(destino)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    raiz, extension = os.path.splitext(destino)
    parcial = f"{raiz}.parcial{extension}"
    try:
        shutil.copy2(origen, parcial)
    except Exception:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise
    os.replace(parcial, destino)


def _publicar_tiff_girado(ruta_staging: str, destino: str, angulo: int) -> None:
    """Mueve el TIFF recién convertido (`ruta_staging`, sin girar: ver
    `_convertir_una_termica`) a su ruta final, girándolo si el ángulo del
    manifiesto lo pide. El giro usa el MISMO mapeo de `_transpose_para_angulo`
    que RGB (invariante nº1: TIFF y JPG del mismo vuelo comparten ángulo) y
    guarda con `quality=96, subsampling=0`, igual que `rotate_tiff_image`
    (`pipeline.py:2309`) — no se mejora la calidad del motor viejo por
    sorpresa. Sin giro, el TIFF se mueve tal cual, byte a byte, como
    `convert_dji_image_to_tif` lo dejó (nunca se reabre sin necesidad).

    Escritura atómica: `<raíz>.parcial<ext>` + `os.replace`, igual que el
    resto de salidas de este módulo."""
    carpeta = os.path.dirname(destino)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    raiz, extension = os.path.splitext(destino)
    parcial = f"{raiz}.parcial{extension}"
    transpose = _transpose_para_angulo(angulo, pipeline)
    try:
        if transpose is None:
            shutil.copy2(ruta_staging, parcial)
        else:
            img = pipeline.Image.open(ruta_staging)
            try:
                girada = img.transpose(transpose)
                try:
                    girada.save(parcial, quality=96, subsampling=0)
                finally:
                    girada.close()
            finally:
                img.close()
    except Exception:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise
    os.replace(parcial, destino)


def _convertir_una_termica(fila: dict, cfg, pipeline_obj, exiftool_exe: str, dji_utility: str,
                           progress_callback, progress_bar, carpeta_staging: str) -> None:
    """Convierte la térmica de `fila` a TIFF vía
    `pipeline_obj.convert_dji_image_to_tif` (una instancia de
    `pipeline.SplitImages`), la publica ya girada en su ruta final y copia
    su JPG de origen al destino que decidió el índice.

    El giro NO se delega en el conversor: `rotate_90`/`rotate_minus_90` van
    siempre a `False` y `auto_rotate=False`, porque el criterio automático
    (`degree_de_giro` -> `read_auto_rotate_degree`) lee un CSV de criterio
    que en el motor plan-apply todavía no existe a esta altura (lo escribe
    el cierre, Tarea 6, DESPUÉS del apply). El ángulo sale de
    `fila["angulo_giro"]` y se aplica después, en `_publicar_tiff_girado`.

    No toca el manifiesto: solo escribe en disco o lanza si algo falla, para
    que la llamadora decida cómo marcar la fila."""
    input_folder = os.path.dirname(fila["ruta_origen"])
    nombre_imagen = os.path.basename(fila["ruta_origen"])
    staging_salida = tempfile.mkdtemp(dir=carpeta_staging, prefix="t_")
    try:
        resultado = pipeline_obj.convert_dji_image_to_tif(
            input_folder, staging_salida, nombre_imagen, exiftool_exe, dji_utility,
            progress_callback, progress_bar,
            emissivity=cfg.convert_to_tif_emissivity,
            humidity=cfg.convert_to_tif_humidity,
            auto_temp=bool(cfg.convert_to_tif_temp_auto),
            up_threshold_temperature=cfg.convert_to_tif_up_temperature,
            low_threshold_temperature=cfg.convert_to_tif_low_temperature,
            rotate_90=False, rotate_minus_90=False, auto_rotate=False,
            just_atom_selection=bool(cfg.convert_to_tif_solo_seleccion_atom),
            generate_gray_scale_images=bool(cfg.convert_to_tif_create_gray_scale_images),
            generate_colormap_images=False,
            defer_exif=True,
        )
        if not resultado:
            raise RuntimeError(
                "El conversor DJI no generó el TIFF de {0} (ver log de la corrida)."
                .format(fila["ruta_origen"]))
        _origen_pair, tiff_staging = resultado

        ruta_tiff_destino = fila["ruta_salida_tiff"]
        if ruta_tiff_destino:
            _publicar_tiff_girado(tiff_staging, ruta_tiff_destino, fila["angulo_giro"] or 0)

        _copiar_jpg_destino(fila["ruta_origen"], fila["ruta_salida_original"])
    finally:
        shutil.rmtree(staging_salida, ignore_errors=True)


def _en_lotes(items: list, tamano: int):
    """Trocea `items` en listas de como mucho `tamano` elementos, en orden."""
    tamano = max(1, tamano)
    for inicio in range(0, len(items), tamano):
        yield items[inicio:inicio + tamano]


def aplicar_termicas(manifiesto, cfg, pipeline, progress_callback, progress_bar,
                     progress_summarize, controlador=None, tamano_lote_exif: int = 200) -> dict:
    """Recorre el manifiesto y escribe las salidas de térmica pendientes.

    Por cada fila: convierte el JPG a TIFF (`pipeline.convert_dji_image_to_tif`,
    una instancia de `pipeline.SplitImages`), lo gira con el MISMO ángulo que
    su RGB hermana del mismo vuelo (`fila["angulo_giro"]` — invariante nº1:
    un TIFF y su JPG nunca pueden divergir de orientación) y copia el JPG de
    origen a su carpeta final. Los metadatos EXIF se copian DESPUÉS, con
    exiftool EN LOTES (`pipeline._run_exif_batch_local`, como mucho
    `tamano_lote_exif` pares por invocación) — nunca un exiftool por imagen.

    `exiftool -stay_open` no da granularidad por imagen dentro de un lote:
    si un lote falla, TODAS sus filas quedan fallidas. Es la única forma
    honesta de cumplir "un TIFF sin sus metadatos no puede quedar hecho" sin
    inventarse una señal que la herramienta no da.

    Reanudable igual que `aplicar_rgb`: solo toca filas 'pendiente' de tipo
    'TERMICA'. Sin `controlador` la conversión corre secuencial en el
    proceso actual (el camino de los tests); con `controlador` va a un
    `ThreadPoolExecutor` (espera a procesos externos, no CPU del intérprete)
    con el aforo en vuelo siguiendo a `controlador.trabajadores`, mismo
    mecanismo (`_AforoDinamico`) que usa `aplicar_rgb`.

    Returns:
    --------
    dict con "hecho" y "fallido": cuántas filas de térmica acabaron en cada
    estado en esta pasada.
    """
    progress_summarize.emit("---> SUBPROCESO: Conversión térmica")

    filas = [dict(fila) for fila in manifiesto.pendientes() if fila["tipo"] == "TERMICA"]
    resultado = {"hecho": 0, "fallido": 0}
    total = len(filas)
    if total == 0:
        return resultado

    exiftool_exe = external_tools.resource_path("programas_externos", "exiftool.exe")
    dji_utility = external_tools.dji_utility_path()

    staging_raiz = tempfile.mkdtemp(prefix="apply_termicas_")
    try:
        convertidas: dict[int, dict] = {}

        def _procesar_una(fila: dict) -> None:
            manifiesto.marcar_en_curso(fila["id"])
            try:
                _convertir_una_termica(fila, cfg, pipeline, exiftool_exe, dji_utility,
                                       progress_callback, progress_bar, staging_raiz)
            except Exception as exc:
                manifiesto.marcar_fallida(fila["id"], str(exc))
                resultado["fallido"] += 1
            else:
                convertidas[fila["id"]] = fila
            if controlador is not None:
                controlador.registrar(mb=_tamano_mb(fila["ruta_origen"]))
                controlador.revisar()

        if controlador is None:
            for indice, fila in enumerate(filas, start=1):
                _procesar_una(fila)
                progress_bar.emit(int(indice / total * 50))
        else:
            # --- Camino paralelo: threads, no procesos (ver docstring) -----
            aforo = _AforoDinamico(controlador.trabajadores)
            lock_progreso = threading.Lock()
            estado_progreso = {"completadas": 0}

            def _con_aforo(fila: dict) -> None:
                try:
                    _procesar_una(fila)
                finally:
                    aforo.ajustar(controlador.trabajadores)
                    aforo.liberar()
                    with lock_progreso:
                        estado_progreso["completadas"] += 1
                        progress_bar.emit(int(estado_progreso["completadas"] / total * 50))

            with ThreadPoolExecutor(max_workers=controlador.maximo) as executor:
                futuros = []
                for fila in filas:
                    aforo.adquirir()
                    futuros.append(executor.submit(_con_aforo, fila))
                _esperar_futuros(futuros)

        # --- Metadatos: exiftool en lotes, sobre lo que sí se convirtió ----
        pendientes_exif = list(convertidas.values())
        lotes = list(_en_lotes(pendientes_exif, tamano_lote_exif))
        for numero_lote, lote in enumerate(lotes, start=1):
            pares = [(fila["ruta_origen"], fila["ruta_salida_tiff"]) for fila in lote]
            try:
                pipeline._run_exif_batch_local(pares, exiftool_exe, progress_callback)
            except Exception as exc:
                for fila in lote:
                    manifiesto.marcar_fallida(fila["id"], str(exc))
                    resultado["fallido"] += 1
            else:
                for fila in lote:
                    piezas = [
                        f"{fila['ruta_salida_original']}:{os.path.getsize(fila['ruta_salida_original'])}",
                    ]
                    if fila["ruta_salida_tiff"]:
                        piezas.append(
                            f"{fila['ruta_salida_tiff']}:{os.path.getsize(fila['ruta_salida_tiff'])}"
                        )
                    manifiesto.marcar_hecha(fila["id"], "; ".join(piezas))
                    resultado["hecho"] += 1
            progress_bar.emit(int(50 + numero_lote / len(lotes) * 50))
    finally:
        shutil.rmtree(staging_raiz, ignore_errors=True)

    return resultado
