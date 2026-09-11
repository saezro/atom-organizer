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

import json
import os
import shutil
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait as _esperar_futuros
from typing import Any, Mapping

import external_tools
import dji_worker_pool
import pipeline
from exif import extraer_bloque_xmp_crudo
from atom_core import indice as indice_mod
from atom_core import perfil_rgb

#: MB que se le presupone a un item de RGB para el reporte al controlador
#: adaptativo. Si no se puede leer el tamaño real del origen (p. ej. se
#: borró entre el índice y el apply), no se revienta el run por eso.
_MB_POR_DEFECTO_SI_FALLA_STAT = 0.0

#: Prefijo del canal `progress_callback` (texto) que `atom_core.organize`
#: intercepta y convierte en `emit("stats", {...})` — ver
#: `organize.STATS_APPLY_PREFIX`. Ni `aplicar_rgb` ni `aplicar_termicas`
#: tienen acceso al `emit` real de `organize.run_task` (solo reciben las tres
#: señales de siempre: log/percent/summary), así que la métrica viaja como
#: una línea de texto reconocible por el mismo canal que ya usa `_PHASE_PREFIX`
#: para las fases — no se inventa un cuarto canal.
STATS_APPLY_PREFIX = "---> STATS_APPLY: "

#: Cada cuántas filas completadas se re-emite la métrica de velocidad. Mismo
#: criterio que `progress_stats.IMAGE_EMIT_EVERY`: un `emit` por imagen
#: inundaría el puente Python->JS; re-emitir cada 10 (y siempre en la última)
#: es gratis y sigue siendo "en vivo" a efectos de UI.
_EMITIR_STATS_CADA = 10


class _EmisorProgreso:
    """Emisor de la barra de progreso que solo habla cuando el porcentaje
    ENTERO cambia.

    `progress_bar.emit` cruza el puente Qt (señal entre hilos) y en la UI
    repinta; emitirlo una vez por imagen son miles de señales para 100
    valores distintos. Las stats de texto ya se throttlean con
    `_EMITIR_STATS_CADA`; esto es lo mismo para la barra."""

    def __init__(self, progress_bar) -> None:
        self._progress_bar = progress_bar
        self._lock = threading.Lock()
        self._ultimo = -1

    def emit(self, porcentaje: int) -> None:
        with self._lock:
            if porcentaje == self._ultimo:
                return
            self._ultimo = porcentaje
        self._progress_bar.emit(porcentaje)

#: Nº de completadas que entran en la media móvil de velocidad. Una media
#: GLOBAL desde el arranque de la fase miente en cuanto el ritmo cambia
#: (arranque en frío, mezcla de imágenes grandes/pequeñas); una ventana corta
#: de las últimas N sigue al ritmo real sin ser tan nerviosa como "la última
#: comparada con la penúltima".
_VENTANA_VELOCIDAD = 20


class _MedidorVelocidad:
    """Imágenes/segundo sobre una VENTANA MÓVIL de las últimas completadas.

    Sin al menos 2 muestras que abarquen algo de tiempo (> 0s) no hay
    velocidad fiable, y por tanto tampoco ETA: se devuelve `None` antes que
    un ETA absurdo (p. ej. infinito o negativo) — decisión explícita del
    contrato de eventos (ver LEDGER-metricas-progreso.md)."""

    def __init__(self, ventana: int = _VENTANA_VELOCIDAD) -> None:
        self._marcas: deque[float] = deque(maxlen=ventana)

    def registrar(self, ahora: float | None = None) -> None:
        self._marcas.append(ahora if ahora is not None else time.monotonic())

    def img_por_segundo(self) -> float | None:
        if len(self._marcas) < 2:
            return None
        transcurrido = self._marcas[-1] - self._marcas[0]
        if transcurrido <= 0:
            return None
        return (len(self._marcas) - 1) / transcurrido

    def eta_segundos(self, pendientes: int) -> int | None:
        velocidad = self.img_por_segundo()
        if not velocidad or pendientes <= 0:
            return None
        return round(pendientes / velocidad)


class _ContadorRotacion:
    """Cuenta ACUMULADA del run de cuántas imágenes se giraron 270°, 90° o
    ninguna, para la línea "Rotación: N giradas 270° · M sin girar" de la UI
    (`ProgressModal.jsx`, `rotLine`). El motor viejo la sacaba con un regex
    sobre su propio texto de log (`progress_stats.py`); el motor
    plan-apply gira inline por fila (`_transpose_para_angulo`) y no deja ese
    rastro de texto, así que aquí es donde hay que llevar la cuenta.

    A propósito NO se reinicia entre fases: `aplicar_rgb` y `aplicar_termicas`
    reciben la MISMA instancia (`phases.py`) para que el total sobreviva al
    cambio de fase — la línea de rotación se pinta durante y después de todo
    el proceso, no solo de la fase RGB. Un `threading.Lock` propio porque se
    incrementa desde los mismos callbacks concurrentes que ya tocan
    `resultado`/`completadas` en ambas funciones (varios workers a la vez)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rot270 = 0
        self._rot90 = 0
        self._rot_none = 0

    def registrar(self, angulo: int) -> None:
        with self._lock:
            if angulo == 270:
                self._rot270 += 1
            elif angulo == 90:
                self._rot90 += 1
            else:
                self._rot_none += 1

    def valores(self) -> dict:
        with self._lock:
            return {
                "rot270": self._rot270,
                "rot90": self._rot90,
                "rot_none": self._rot_none,
            }


def _emitir_stats_apply(progress_callback, fase: str, done: int, total: int,
                        rgb: int, termica: int, medidor: "_MedidorVelocidad",
                        contador_rotacion: "_ContadorRotacion") -> None:
    """Empaqueta y emite la métrica de velocidad de una fase del apply.
    Formato ver `STATS_APPLY_PREFIX`."""
    velocidad = medidor.img_por_segundo()
    payload = {
        "fase": fase,
        "done": done,
        "total": total,
        "rgb": rgb,
        "termica": termica,
        "img_por_segundo": round(velocidad, 2) if velocidad is not None else None,
        "eta_segundos": medidor.eta_segundos(total - done),
    }
    payload.update(contador_rotacion.valores())
    progress_callback.emit(STATS_APPLY_PREFIX + json.dumps(payload))


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
                      pipeline_mod, etapa_encode: str = "encode_original") -> None:
    """Guarda `img` (con el crop/giro que le toque) en `destino` escribiendo
    primero a `<destino>.parcial` y renombrando con `os.replace`. Un fallo a
    mitad de un `save()` (disco lleno, JPEG corrupto al escribir, lo que
    sea) no puede dejar un fichero truncado en la carpeta de entrega: o el
    `.parcial` desaparece, o `destino` queda completo. Nunca un intermedio.

    `etapa_encode` distingue en `perfil_rgb` si este guardado es el
    original o el `_CROP` (`"encode_original"` / `"encode_crop"`); el
    `os.replace` final se mide aparte, en la etapa genérica `"escritura"`
    (instrumentación OPT-IN, no-op si `perfil_rgb.ACTIVO` es `False`).
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
        with perfil_rgb.medir(etapa_encode):
            pipeline_mod._procesar_y_guardar_imagen(img, cfg_escritura)
    except Exception:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise
    with perfil_rgb.medir("escritura"):
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

    Giro UNA sola vez — de dónde sale el ahorro: el transpose de una imagen
    de 48 MP cuesta ~0,55 s, y pasarle `rotate_degrees` a las DOS escrituras
    lo pagaba DOS veces sobre el mismo decode. Aquí se gira una vez y ambas
    salidas se escriben desde la imagen ya girada, sin transformación de
    giro propia (~20 % del ciclo de una imagen girada). El recorte centrado
    conmuta con el giro de 90°: `crop_centered_pct` es una fracción
    simétrica, así que recortar la girada da exactamente el mismo píxel que
    girar el recorte — solo se intercambian ancho y alto.

    Instrumentación OPT-IN (`atom_core.perfil_rgb`, activa solo con
    `ORGANIZER_PERFIL_RGB` definida): la lectura del fichero de origen y su
    decode van en las etapas `"lectura"`/`"decode"`, cada `_guardar_atomico`
    mide su propio encode (`"encode_crop"`/`"encode_original"`) y su
    escritura (`"escritura"`); el giro (`transpose`) no tiene columna propia
    en el CSV, así que su tiempo queda dentro de `t_total` sin repartir en
    ninguna etapa — es fiel al "no existe en este camino" del resto de
    columnas cuando no aplica.
    """
    nombre = os.path.basename(fila["ruta_origen"])
    try:
        bytes_origen = os.path.getsize(fila["ruta_origen"])
    except OSError:
        bytes_origen = 0

    with perfil_rgb.medir_imagen(nombre, bytes_origen):
        angulo = fila["angulo_giro"] or 0
        transpose = _transpose_para_angulo(angulo, pipeline_mod)
        calidad = pipeline_mod._ROTATION_JPEG_QUALITY if angulo else cfg.compress_level

        with perfil_rgb.medir("lectura"):
            img = pipeline_mod.Image.open(fila["ruta_origen"])
        with perfil_rgb.medir("decode"):
            img.load()

        escritas: list[str] = []
        girada = None
        try:
            # La girada pasa a ser la base de AMBAS salidas; a partir de aquí
            # ninguna de las dos escrituras vuelve a girar nada.
            if transpose is not None:
                girada = img.transpose(transpose)
            base = girada if girada is not None else img

            ruta_crop = fila["ruta_salida_crop"]
            if ruta_crop:
                _guardar_atomico(base, ruta_crop, None, fila["pct_recorte"], calidad,
                                 pipeline_mod, etapa_encode="encode_crop")
                escritas.append(ruta_crop)

            ruta_original = fila["ruta_salida_original"]
            _guardar_atomico(base, ruta_original, None, None, calidad, pipeline_mod,
                             etapa_encode="encode_original")
            escritas.append(ruta_original)
        finally:
            # Defensivo: el original ya cerró `base` (ahora nunca aplica
            # transformación en su llamada, ver docstring); un segundo `close()`
            # sobre una imagen PIL ya cerrada es un no-op seguro.
            for imagen in (girada, img):
                if imagen is None:
                    continue
                try:
                    imagen.close()
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

    def __init__(self, permisos_iniciales: int, reloj=time.monotonic) -> None:
        permisos_iniciales = max(1, permisos_iniciales)
        self._semaforo = threading.Semaphore(permisos_iniciales)
        self._lock = threading.Lock()
        self._objetivo = permisos_iniciales
        self._en_circulacion = permisos_iniciales

        # --- Telemetría de aprovechamiento --------------------------------
        # Se integra el número de tareas EN VUELO a lo largo del tiempo
        # (`_area`, en worker·segundo). La media de esa integral dividida
        # entre el máximo del pool es el "% del potencial" que se reporta al
        # acabar la fase: es lo único que distingue "tardó 20 min con el pool
        # lleno" (trabajo real) de "tardó 20 min con 1 en vuelo" (la
        # regresión de la 3.4.81). Coste: dos restas por imagen.
        self._reloj = reloj
        self._t0 = reloj()
        self._t_ultimo = self._t0
        self._en_vuelo = 0
        self._pico = 0
        self._area = 0.0

    def _acumular(self, delta: int) -> None:
        """Cierra el tramo de tiempo con el nº de tareas en vuelo actual y
        aplica el cambio. Se llama con `_lock` tomado."""
        ahora = self._reloj()
        self._area += self._en_vuelo * (ahora - self._t_ultimo)
        self._t_ultimo = ahora
        self._en_vuelo += delta
        if self._en_vuelo > self._pico:
            self._pico = self._en_vuelo

    def resumen(self, maximo: int) -> dict:
        """Foto del aprovechamiento hasta este instante."""
        with self._lock:
            self._acumular(0)
            segundos = max(self._t_ultimo - self._t0, 1e-9)
            media = self._area / segundos
        return {
            "segundos": self._t_ultimo - self._t0,
            "media": media,
            "pico": self._pico,
            "maximo": maximo,
            "potencial_pct": (media / maximo * 100) if maximo else 0.0,
        }

    def adquirir(self) -> None:
        self._semaforo.acquire()
        with self._lock:
            self._acumular(+1)

    def liberar(self) -> None:
        with self._lock:
            self._acumular(-1)
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


def _formatear_duracion(segundos: float) -> str:
    """`312.4` -> `5 min 12 s`. Para el resumen de fase: los minutos son la
    unidad en la que Rodrigo mide el organizado, no los segundos."""
    segundos = max(0.0, segundos)
    if segundos < 60:
        return f"{segundos:.1f} s"
    minutos, resto = divmod(int(round(segundos)), 60)
    return f"{minutos} min {resto:02d} s"


def _emitir_resumen_fase(progress_callback, fase: str, unidad: str, total: int,
                         resumen: dict) -> None:
    """Línea de cierre de una fase paralela: cuánto tardó y cuánto del
    paralelismo disponible se llegó a usar de verdad. Es el diagnóstico de
    campo que faltaba en la 3.4.81: con `1,0 de 14 = 7 % del potencial` la
    regresión se ve de un vistazo en el log del usuario, sin instrumentar
    nada ni pedirle el PC."""
    segundos = resumen["segundos"]
    velocidad = total / segundos if segundos > 0 else 0.0
    progress_callback.emit(
        f"\n[paralelismo] {fase}: {total} imagen(es) en "
        f"{_formatear_duracion(segundos)} — {velocidad:.1f} img/s — "
        f"media {resumen['media']:.1f} {unidad} en vuelo "
        f"(pico {resumen['pico']}, máximo {resumen['maximo']}) = "
        f"{resumen['potencial_pct']:.0f} % del potencial.\n"
    )


def _emitir_resumen_perfil_rgb(progress_callback) -> None:
    """Cierre de la instrumentación OPT-IN de `perfil_rgb`: no-op si
    `ORGANIZER_PERFIL_RGB` no está definida. Con la env var activa, relee el
    CSV ya escrito (una fila por imagen, de ambos caminos: secuencial y
    pool) y loguea la media de cada etapa."""
    resumen = perfil_rgb.resumen()
    if resumen is None:
        return
    medias = resumen["medias"]
    progress_callback.emit(
        f"\n[perfil_rgb] {resumen['n_imagenes']} imagen(es) — medias (s): "
        f"lectura={medias['t_lectura']:.4f} decode={medias['t_decode']:.4f} "
        f"encode_original={medias['t_encode_original']:.4f} "
        f"encode_crop={medias['t_encode_crop']:.4f} "
        f"thumbnail={medias['t_thumbnail']:.4f} "
        f"escritura={medias['t_escritura']:.4f} total={medias['t_total']:.4f}\n"
    )


def aplicar_rgb(manifiesto, cfg, pipeline_mod, progress_callback, progress_bar,
                progress_summarize, controlador=None,
                contador_rotacion: "_ContadorRotacion | None" = None) -> dict:
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
    progress_summarize.emit("---> SUBPROCESO: Imágenes RGB")

    # `RGB_Extra` cuenta como RGB aquí: en el motor viejo
    # `iterate_folders_for_rgb_cropping` (pipeline.py:4003) recorre TODO el
    # árbol de salida salvo `TERMICA`, así que las imágenes del tercer grupo
    # de sufijos también se comprimen/recortan igual que las RGB normales
    # (`Pipeline.iterate_folders`, pipeline.py:2829, comparte el mismo flag
    # `compress_checked`). Dejarlas fuera de este filtro las dejaría
    # 'pendiente' para siempre y `cierre.verificar` las reportaría como run
    # interrumpido sin haberlo estado.
    filas = [dict(fila) for fila in manifiesto.pendientes()
            if fila["tipo"] in indice_mod.TIPOS_RGB]
    resultado = {"hecho": 0, "fallido": 0}
    total = len(filas)
    if total == 0:
        return resultado

    # Instrumentación OPT-IN (`ORGANIZER_PERFIL_RGB`): la cabecera la escribe
    # SOLO el proceso padre (aquí), nunca los workers — si la escribiera cada
    # worker del pool se duplicaría una vez por proceso.
    perfil_rgb.escribir_cabecera()

    # Sin instancia compartida (llamada suelta, tests) se crea una propia:
    # solo importa que `aplicar_rgb` y `aplicar_termicas` del MISMO run
    # reciban la misma (ver `_ContadorRotacion`).
    if contador_rotacion is None:
        contador_rotacion = _ContadorRotacion()

    # Velocidad/ETA de esta fase (ver `_MedidorVelocidad`): un único medidor
    # compartido por el camino secuencial y el paralelo, porque `_cerrar_fila`
    # es el ÚNICO punto por el que pasa cada fila completada en AMBOS caminos.
    medidor_velocidad = _MedidorVelocidad()
    # Los contadores de `resultado` los tocan el hilo principal (camino
    # secuencial) o el hilo de gestión del pool (camino paralelo, vía
    # `add_done_callback`). Un lock los deja a salvo de cualquier cambio
    # futuro en quién llama, y su coste es nulo frente al de la imagen.
    lock_resultado = threading.Lock()

    def _cerrar_fila(fila: dict, verificacion=None, error: Exception = None) -> None:
        contador_rotacion.registrar(fila["angulo_giro"] or 0)
        if error is not None:
            manifiesto.marcar_fallida(fila["id"], str(error))
            with lock_resultado:
                resultado["fallido"] += 1
        else:
            manifiesto.marcar_hecha(fila["id"], verificacion)
            with lock_resultado:
                resultado["hecho"] += 1
        medidor_velocidad.registrar()
        with lock_resultado:
            completadas = resultado["hecho"] + resultado["fallido"]
        if completadas % _EMITIR_STATS_CADA == 0 or completadas == total:
            _emitir_stats_apply(progress_callback, "Imágenes RGB", completadas, total,
                               rgb=completadas, termica=0, medidor=medidor_velocidad,
                               contador_rotacion=contador_rotacion)
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
        _emitir_resumen_perfil_rgb(progress_callback)
        return resultado

    # --- Camino paralelo -----------------------------------------------
    aforo = _AforoDinamico(controlador.trabajadores)
    lock_progreso = threading.Lock()
    estado_progreso = {"completadas": 0}
    emisor = _EmisorProgreso(progress_bar)
    progress_callback.emit(
        f"\n[paralelismo] Imágenes RGB: {controlador.trabajadores} proceso(s) en "
        f"vuelo (máximo {controlador.maximo}), {total} imagen(es).\n"
    )

    def _al_terminar(fila: dict, futuro) -> None:
        # TODO el cuerpo va en try/finally: `concurrent.futures` se TRAGA las
        # excepciones de un callback (solo las loguea), así que si
        # `_cerrar_fila` lanza (sqlite ocupada tras agotar el busy_timeout, un
        # emit, el controlador) y el permiso no vuelve al semáforo, nada
        # aborta: el aforo va perdiendo capacidad en silencio hasta que
        # `aforo.adquirir()` se bloquea para siempre y el run queda colgado.
        # Es el mismo patrón que `_con_aforo` en las térmicas.
        try:
            try:
                verificacion = futuro.result()
            except Exception as exc:
                _cerrar_fila(fila, error=exc)
            else:
                _cerrar_fila(fila, verificacion=verificacion)
        finally:
            try:
                aforo.ajustar(controlador.trabajadores)
            finally:
                aforo.liberar()
            with lock_progreso:
                estado_progreso["completadas"] += 1
                emisor.emit(int(estado_progreso["completadas"] / total * 100))

    with ProcessPoolExecutor(max_workers=controlador.maximo) as executor:
        futuros = []
        for fila in filas:
            aforo.adquirir()
            try:
                manifiesto.marcar_en_curso(fila["id"])
                futuro = executor.submit(_trabajo_fila, fila, cfg)
            except Exception as exc:
                # Sin futuro no habrá callback: hay que cerrar la fila y
                # devolver el permiso aquí mismo. Un `BrokenProcessPool` (un
                # worker muerto por RAM) rompe TODOS los submit siguientes, y
                # dejar que la excepción suba abortaría el lote entero con las
                # filas ya marcadas 'en_curso' y el aforo a medias.
                _cerrar_fila(fila, error=exc)
                aforo.liberar()
                continue
            futuro.add_done_callback(lambda futuro, fila=fila: _al_terminar(fila, futuro))
            futuros.append(futuro)

        _esperar_futuros(futuros)

    _emitir_resumen_fase(progress_callback, "Imágenes RGB", "proceso(s)", total,
                         aforo.resumen(controlador.maximo))
    _emitir_resumen_perfil_rgb(progress_callback)
    return resultado


# --- Térmicas: dji_irp + metadatos -----------------------------------------
# Las térmicas no son CPU del intérprete: son espera a dos procesos externos
# (dji_irp/libdirp.so para el TIFF, exiftool para el EXIF). Por eso van en
# ThreadPoolExecutor, no en procesos.


# Calidad del re-encodado al girar el JPG térmico. 95, la misma que usaba
# `pipeline._girar_termica_local`: el giro obliga a descomprimir y volver a
# comprimir, y esta es la única copia que queda, así que no puede añadir
# artefactos visibles. NO es `pipeline._ROTATION_JPEG_QUALITY` (40), que es la
# de RGB — bajarla aquí degradaría la térmica frente al motor viejo.
_CALIDAD_GIRO_JPG_TERMICO = 95


def _copiar_jpg_destino(origen: str, destino: str, angulo: int = 0) -> None:
    """Publica el JPG térmico en su ruta final, girándolo si el ángulo del
    manifiesto lo pide, de forma atómica (`<raíz>.parcial<ext>` + `os.replace`).

    El giro es el que hacía el motor viejo (`pipeline.rotate_thermal_jpgs_in_place`
    -> `_girar_termica_local`) y usa el MISMO mapeo `_transpose_para_angulo` que
    el TIFF y que RGB (invariante nº1: todo el vuelo comparte ángulo), así que el
    `*_T.JPG` y el TIFF salen orientados igual.

    Re-guardar con PIL DESTRUYE el payload radiométrico propietario del R-JPEG:
    aquí es inocuo porque a esta altura el TIFF ya está extraído del ORIGINAL
    (`_convertir_una_termica` convierte antes de llamar aquí) y el giro se aplica
    sobre la copia de destino, nunca sobre el fichero de origen — el motor viejo
    sí giraba en sitio. El EXIF se arrastra explícitamente: lleva el GPS y la
    fecha, que es justo lo que se consulta luego sobre estas fotos.

    Sin giro (`angulo` 0) se copia byte a byte con `copy2`, sin reabrir ni
    recomprimir nada: el caso normal no paga ningún coste.
    """
    carpeta = os.path.dirname(destino)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    raiz, extension = os.path.splitext(destino)
    parcial = f"{raiz}.parcial{extension}"
    transpose = _transpose_para_angulo(angulo, pipeline)
    try:
        if transpose is None:
            shutil.copy2(origen, parcial)
        else:
            img = pipeline.Image.open(origen)
            try:
                if img.height > img.width:
                    # Las térmicas DJI son apaisadas de fábrica (640x512). Si esta
                    # ya viene vertical, girarla la dejaría a 180º: se publica tal
                    # cual. Misma guarda que `pipeline._girar_termica_local`.
                    img.close()
                    shutil.copy2(origen, parcial)
                else:
                    exif = img.info.get("exif")
                    # El bloque XMP (GimbalYawDegree, GPS DJI, etc.) se lee del
                    # ORIGEN antes de girar: `PIL.Image.save` no lo re-adjunta
                    # (solo conoce el EXIF de `img.info`), así que sin esto la
                    # copia girada perdía el XMP aunque conservase el EXIF.
                    bloque_xmp = extraer_bloque_xmp_crudo(origen)
                    girada = img.transpose(transpose)
                    try:
                        if exif:
                            girada.save(parcial, format="JPEG",
                                        quality=_CALIDAD_GIRO_JPG_TERMICO, exif=exif)
                        else:
                            girada.save(parcial, format="JPEG",
                                        quality=_CALIDAD_GIRO_JPG_TERMICO)
                    finally:
                        girada.close()
                    if bloque_xmp:
                        # Mismo esquema que `make_dji_jpeg` en tests/conftest.py:
                        # el XMP va pegado tras el JPEG, como texto crudo, no
                        # como segmento estructurado — así lo escriben y así lo
                        # leen `leer_bloque_xmp`/`get_gimbal_yaw_pitch`/
                        # `get_xmp_data`, que buscan el texto en el fichero
                        # entero sin mirar los segmentos.
                        with open(parcial, "ab") as fh:
                            fh.write(bloque_xmp)
            finally:
                img.close()
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
    `pipeline.SplitImages`), la publica ya girada en su ruta final y publica
    su JPG de origen —también girado— en el destino que decidió el índice.

    El giro NO se delega en el conversor: `rotate_90`/`rotate_minus_90` van
    siempre a `False` y `auto_rotate=False`, porque el criterio automático
    (`degree_de_giro` -> `read_auto_rotate_degree`) lee un CSV de criterio
    que en el motor plan-apply todavía no existe a esta altura (lo escribe
    el cierre, Tarea 6, DESPUÉS del apply). El ángulo sale de
    `fila["angulo_giro"]` y se aplica después, al publicar: al TIFF en
    `_publicar_tiff_girado` y al `*_T.JPG` en `_copiar_jpg_destino`. Los dos
    salen con la MISMA orientación, como en el motor viejo.

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

        # El giro del `*_T.JPG` cuelga del switch maestro de ROTACION
        # (`gen_thumbnails`, el que apaga `--sin-rotacion`), igual que en el motor
        # viejo (`phases.split_images`): girar destruye el APP3/4/5 del R-JPEG y con
        # él su radiometría, así que quien pide «sin rotación» no puede acabar con
        # R-JPEG destruidos (incidente CLARE, execution `wpv52`, 2026-08-21). El TIFF
        # sí se gira: lo único que se pierde es que el JPG case visualmente con él.
        angulo_jpg = (fila["angulo_giro"] or 0) if getattr(cfg, "gen_thumbnails", True) else 0
        _copiar_jpg_destino(fila["ruta_origen"], fila["ruta_salida_original"], angulo_jpg)
    finally:
        shutil.rmtree(staging_salida, ignore_errors=True)


def _en_lotes(items: list, tamano: int):
    """Trocea `items` en listas de como mucho `tamano` elementos, en orden."""
    tamano = max(1, tamano)
    for inicio in range(0, len(items), tamano):
        yield items[inicio:inicio + tamano]


def _reportar_colisiones_destino(manifiesto, progress_callback) -> None:
    """Avisa (SOLO avisa -- no cambia nada de lo que ya se escribe) cuando
    dos o más imágenes de origen distinto comparten `ruta_salida_original`.

    `os.replace` (más abajo, en `_escribir_salidas_de_fila` y en
    `_convertir_una_termica`) es atómico pero silencioso: la segunda imagen
    pisa a la primera sin error, y el fichero perdido no deja ningún rastro
    salvo esta comprobación contra el manifiesto. Se lanza UNA vez, al cerrar
    la fase de térmicas (la última antes del cierre del organizado), sobre el
    manifiesto completo -- no depende de cuántas filas de térmica haya, así
    que corre igual en un run puramente RGB.
    """
    colisiones = manifiesto.colisiones_ruta_salida_original()
    if not colisiones:
        return
    total_colisiones = len(colisiones)
    destinos = [fila["ruta_salida_original"] for fila in colisiones]
    listado = ", ".join(destinos[:10])
    if total_colisiones > 10:
        listado += f", … ({total_colisiones - 10} más)"
    progress_callback.emit(
        f"\n[colisión] {total_colisiones} destino(s) recibieron imágenes de "
        f"MÁS de un origen distinto; solo queda escrita la última que llegó, "
        f"el resto se perdió en silencio: {listado}\n"
    )


def aplicar_termicas(manifiesto, cfg, pipeline, progress_callback, progress_bar,
                     progress_summarize, controlador=None, tamano_lote_exif: int = 200,
                     contador_rotacion: "_ContadorRotacion | None" = None) -> dict:
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

    # Chequeo de colisiones de destino sobre el manifiesto COMPLETO (todas
    # las filas ya están insertadas por el índice antes de que arranque
    # ningún apply): se hace aquí, y no en `aplicar_rgb`, porque esta es la
    # última fase de escritura antes del cierre -- corre siempre, aunque no
    # haya ninguna térmica pendiente en esta pasada.
    _reportar_colisiones_destino(manifiesto, progress_callback)

    filas = [dict(fila) for fila in manifiesto.pendientes() if fila["tipo"] == "TERMICA"]
    resultado = {"hecho": 0, "fallido": 0}
    total = len(filas)
    if total == 0:
        return resultado

    if contador_rotacion is None:
        contador_rotacion = _ContadorRotacion()

    exiftool_exe = external_tools.resource_path("programas_externos", "exiftool.exe")
    dji_utility = external_tools.dji_utility_path()

    staging_raiz = tempfile.mkdtemp(prefix="apply_termicas_")
    try:
        convertidas: dict[int, dict] = {}
        # Velocidad/ETA de la conversión (el paso caro: dji_irp + copia). El
        # lote de exiftool que cierra la fase es aparte y no lleva medidor
        # propio: es una operación en bloque, no por-imagen, y su coste no
        # se refleja bien como "img/s".
        medidor_velocidad = _MedidorVelocidad()
        completadas = {"n": 0}
        # `_procesar_una` corre desde varios hilos a la vez (camino paralelo):
        # `resultado["fallido"]`, `completadas["n"]` y `convertidas` son estado
        # compartido y se tocan bajo lock. Sin él, dos hilos que terminan a la
        # vez pierden un incremento y el recuento final no cuadra con el
        # manifiesto (y `cierre.verificar` reporta un desajuste inexistente).
        lock_contadores = threading.Lock()

        def _procesar_una(fila: dict) -> None:
            manifiesto.marcar_en_curso(fila["id"])
            contador_rotacion.registrar(fila["angulo_giro"] or 0)
            try:
                _convertir_una_termica(fila, cfg, pipeline, exiftool_exe, dji_utility,
                                       progress_callback, progress_bar, staging_raiz)
            except Exception as exc:
                manifiesto.marcar_fallida(fila["id"], str(exc))
                with lock_contadores:
                    resultado["fallido"] += 1
            else:
                with lock_contadores:
                    convertidas[fila["id"]] = fila
            medidor_velocidad.registrar()
            with lock_contadores:
                completadas["n"] += 1
                hechas = completadas["n"]
            if hechas % _EMITIR_STATS_CADA == 0 or hechas == total:
                _emitir_stats_apply(progress_callback, "Conversión térmica",
                                   hechas, total, rgb=0,
                                   termica=hechas, medidor=medidor_velocidad,
                                   contador_rotacion=contador_rotacion)
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
            emisor = _EmisorProgreso(progress_bar)
            progress_callback.emit(
                f"\n[paralelismo] Conversión térmica: {controlador.trabajadores} hilo(s) "
                f"en vuelo (máximo {controlador.maximo}), {total} imagen(es).\n"
            )

            def _con_aforo(fila: dict) -> None:
                try:
                    _procesar_una(fila)
                finally:
                    try:
                        aforo.ajustar(controlador.trabajadores)
                    finally:
                        aforo.liberar()
                    with lock_progreso:
                        estado_progreso["completadas"] += 1
                        emisor.emit(int(estado_progreso["completadas"] / total * 50))

            with ThreadPoolExecutor(max_workers=controlador.maximo) as executor:
                futuros = []
                for fila in filas:
                    aforo.adquirir()
                    try:
                        futuros.append(executor.submit(_con_aforo, fila))
                    except Exception as exc:
                        # Sin futuro nadie llamará a `_con_aforo`: cerrar la
                        # fila y devolver el permiso aquí (ver `aplicar_rgb`).
                        manifiesto.marcar_fallida(fila["id"], str(exc))
                        with lock_contadores:
                            resultado["fallido"] += 1
                        aforo.liberar()
                        continue
                _esperar_futuros(futuros)

            # La conversión es el grueso de la fase; el lote de exiftool que
            # viene después es en bloque y no pasa por el aforo, así que este
            # resumen mide justo la parte paralelizada.
            _emitir_resumen_fase(progress_callback, "Conversión térmica", "hilo(s)",
                                 total, aforo.resumen(controlador.maximo))

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
        # Fin de la fase térmica: cierra los workers persistentes del SDK DJI si se
        # usaron (no-op en Windows/x86 o con ATOM_DJI_PERSISTENT=0).
        dji_worker_pool.shutdown()
        shutil.rmtree(staging_raiz, ignore_errors=True)

    return resultado
