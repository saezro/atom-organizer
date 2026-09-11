"""Controlador adaptativo de paralelismo del organizado.

PORQUÉ EXISTE
-------------
Hoy el número de trabajadores lo fija `utils.workers_para_lote` (`utils.py:1305`)
UNA sola vez al arrancar, a partir de núcleos y RAM. Eso ignora dos cosas: el
tipo de disco (en HDD, más hilos es thrashing, no más velocidad) y la carga
del resto de la máquina mientras el organizado corre. Rodrigo reportó el
2026-09-08 que un mismo organizado tardó 10 minutos más con el PC ocupado por
otros procesos: el dimensionado estático pidió los mismos trabajadores de
siempre y se peleó por CPU con lo demás.

Este controlador NO sustituye a `workers_para_lote`: lo usa como punto de
partida (y como techo por RAM, multiplicado por dos) y luego mide throughput,
CPU ociosa y RAM libre en ventanas de tiempo para subir o bajar el número de
trabajadores en caliente. No crea ni redimensiona ningún pool: solo dice el
número. Quien lo consume aforo un semáforo con ese número (un
`ProcessPoolExecutor` no se puede redimensionar).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

import utils

#: Traza del aforo en vivo. Sale por el log de la app
#: (%APPDATA%\ATOM-Organizer\Logs\app_*.log) con el prefijo `[paralelismo]`,
#: una línea por VENTANA cerrada (5 s), para poder ver a posteriori si el
#: controlador llegó a subir trabajadores o se quedó estancado. Es la única
#: forma de saber por qué una fase va al 47% de CPU sin repetir el run.
_log = logging.getLogger(__name__)

#: Sink opcional para que las líneas `[paralelismo]` lleguen también al log
#: crudo del modal de progreso de la webui (que consume `progress_callback`,
#: no el logger estándar). `organize.py` lo instala/desinstala durante el
#: run; fuera de un run vale None y `_trazar` se comporta como antes.
_sink_progreso: Optional[Callable[[str], None]] = None


def set_sink_progreso(fn: Optional[Callable[[str], None]]) -> None:
    """Instala (o quita, pasando None) el sink al que además del logger se
    envían las líneas `[paralelismo]` ya formateadas."""
    global _sink_progreso
    _sink_progreso = fn


def _trazar(fmt: str, *args) -> None:
    """Loguea como siempre y, si hay sink instalado, le pasa el mensaje ya
    formateado. Nunca puede tumbar el organizado: un sink roto (o el propio
    logging) se traga en silencio, igual que el resto de trazas de este
    fichero."""
    try:
        _log.info(fmt, *args)
    except Exception:
        pass
    if _sink_progreso is not None:
        try:
            _sink_progreso(fmt % args)
        except Exception:
            pass


#: Zona muerta para no oscilar por ruido de medida: un cambio de rendimiento
#: por debajo de este umbral no mueve el número de trabajadores.
_ZONA_MUERTA_PCT = 0.05

#: Por debajo de este umbral de CPU ociosa no merece la pena subir trabajadores
#: aunque el rendimiento esté plano: la máquina ya está ocupada. Medido en un
#: banco de 16 núcleos: con el umbral en 40.0 el controlador se quedaba
#: infra-aprovisionado con la CPU ociosa al 30-46% (nunca llegaba a subir);
#: en 15.0 sí aprovecha ese margen.
_CPU_OCIOSA_PARA_SUBIR = 15.0

#: Cuántas mediciones recientes se miran para la histéresis de la regla 5: si
#: en alguna pareja consecutiva de las últimas `_VENTANAS_ESPERA_TRAS_BAJADA`
#: mediciones el número de trabajadores bajó, no se sube aunque sobre CPU
#: ociosa. Sin esto, bajar el umbral de arriba haría que en discos lentos el
#: controlador subiera otra vez justo después de haber bajado, oscilando en
#: bucle en vez de asentarse en el techo real de la máquina.
_VENTANAS_ESPERA_TRAS_BAJADA = 3

#: Tope de trabajadores en la fase RGB cuando el disco de ORIGEN es HDD.
#: La regla 5 de `decidir_trabajadores` (CPU ociosa >40% -> subir) interpreta
#: el hueco de CPU esperando al disco como margen libre y sube de 7 a 14
#: workers, pero en un disco mecánico más lectores concurrentes = más cabezal
#: saltando entre pistas (seek thrashing), no más throughput. Con menos
#: workers el disco lee más secuencial y el conjunto va más rápido de
#: verdad, aunque la CPU se vea "ociosa".
#: Bench KL19 (2026-09-11, PC i7-7700K sobre HDD): tope 5 = 1026 s, 6 = 850 s,
#: 7 = 826 s, 8 = 767 s (±50 s entre rondas). Con 3 se quedaba muy corto; 7
#: es el punto donde deja de mejorar fuera del ruido. Además hay SSD USB que
#: el kernel reporta rotational=1 y caían en este tope sin serlo.
TOPE_WORKERS_HDD = 7


def maximo_cpu_bound() -> int:
    """Techo de trabajadores para una fase CPU-bound (RGB): los núcleos
    utilizables, sin más.

    El default del controlador es `arranque * 2`, que tiene sentido en fases
    I/O-bound (las térmicas esperan a `dji_irp`/`exiftool` y aprovechan el
    doble de hilos que núcleos), pero en RGB cada worker está decodificando y
    encodeando (0,94 s de CPU por imagen): sobre-suscribir 2x solo añade
    cambios de contexto y presión de memoria. Medido en ZARATAN (16 núcleos):
    con 29,6 workers de media la fase tardó 506 s frente a 467 s con 10,9.

    Se pasa `mb_por_worker=0` a propósito para saltarse el límite por RAM: ese
    es el punto de ARRANQUE seguro, no el techo. Si la memoria aprieta durante
    el run, la regla 1 de `decidir_trabajadores` ya baja en caliente.
    """
    return utils.workers_para_lote(mb_por_worker=0)


@dataclass(frozen=True)
class Medicion:
    """Una ventana de trabajo ya cerrada: cuántos trabajadores había, cuánto se
    completó y en qué estado quedaron CPU y RAM al cierre."""

    trabajadores: int
    completados: int
    segundos: float
    mb_procesados: float
    cpu_ociosa_pct: float
    ram_libre_mb: float


#: Caída de rendimiento a partir de la cual una bajada de -1 se queda corta:
#: si un salto geométrico se pasó de frenada, deshacerlo de uno en uno cuesta
#: tantas ventanas como saltos hubo. Por encima de este desplome se vuelve a
#: la mitad de golpe.
_DESPLOME_PARA_HALVING = 0.25


def _techo_por_ram_libre(trabajadores: int, mb_por_worker: float, ram_libre_mb: float) -> float:
    """Cuántos trabajadores caben SIN pasar de la RAM libre real de esta
    medición, dejando `mb_por_worker` de margen de seguridad (no se agota el
    último colchón, igual que la regla 1 de `decidir_trabajadores`).

    `ram_libre_mb` viene de la ÚLTIMA `Medicion` del historial, no de una
    lectura nueva de `psutil`: es la misma lectura que ya hizo el
    `lector_recursos` inyectado (o el real, vía `_lector_recursos_psutil`) al
    cerrar esta ventana, así que sigue siendo "la RAM disponible en este
    instante" sin duplicar la lectura ni romper la inyección para tests.

    Cuando no se pudo medir la RAM (`ram_libre_mb == inf`, ver
    `_RECURSOS_DESCONOCIDOS`), devuelve `inf`: sin dato de RAM no hay techo
    dinámico que aplicar y manda solo `maximo`, igual que antes de este
    cambio."""
    if mb_por_worker <= 0 or ram_libre_mb == float("inf"):
        return float("inf")
    extra = max(0.0, ram_libre_mb - mb_por_worker) // mb_por_worker
    return trabajadores + extra


def _subir(
    historial: List[Medicion],
    trabajadores: int,
    maximo: int,
    mb_por_worker: float = utils.MB_POR_WORKER,
    ram_libre_mb: float = float("inf"),
) -> int:
    """Cuánto subir cuando hay margen.

    El techo YA NO es solo `maximo` (fijo, calculado una vez al arrancar como
    `arranque*2`): antes de subir se recalcula cuántos trabajadores caben en
    la RAM libre REAL de esta medición (`_techo_por_ram_libre`) y se usa el
    más bajo de los dos. `maximo` sigue mandando si el llamante lo pasó
    explícito (nunca se sube por encima de él); lo que cambia es que un
    `maximo` heredado de `arranque*2` ya no basta por sí solo si la RAM se ha
    ido llenando con otra cosa mientras el organizado corría — en un PC de
    8 GB, subir a ciegas hasta `arranque*2` podía significar 8-12 workers x
    600 MB = 4,8-7,2 GB solo en esta fase.

    Durante la RAMPA INICIAL (mientras el controlador nunca haya tenido que
    bajar) duplica: llegar de 7 a 32 de uno en uno son 25 ventanas = 125 s,
    que en una fase de 355 s es el 35% del tiempo corriendo infra-aprovisionado
    — y basta una ventana de ruido para perder el paso. Duplicando son 3.

    En cuanto ha habido una bajada (alguna ventana del historial tuvo MÁS
    trabajadores que ahora) se pasa a +1: ya se conoce el techo real de la
    máquina y lo que toca es afinar, no volver a pasarse."""
    techo_ram = _techo_por_ram_libre(trabajadores, mb_por_worker, ram_libre_mb)
    techo = maximo if techo_ram == float("inf") else min(maximo, int(techo_ram))
    # Nunca bajar de los que ya hay al decidir una SUBIDA: si no cabe ni uno
    # más, `_subir` simplemente mantiene, no reduce (eso es cosa de `_bajar`).
    techo = max(trabajadores, techo)
    if any(medicion.trabajadores > trabajadores for medicion in historial):
        return min(techo, trabajadores + 1)
    return min(techo, max(trabajadores + 1, trabajadores * 2))


def _bajar(cambio_pct: float, trabajadores: int, minimo: int) -> int:
    """Cuánto bajar cuando el rendimiento empeora. -1 por defecto; ante un
    desplome grande (`_DESPLOME_PARA_HALVING`) se corta a la mitad, que es la
    contrapartida necesaria del salto geométrico de `_subir`: si duplicar
    provocó thrashing, hay que deshacerlo igual de rápido."""
    if cambio_pct <= -_DESPLOME_PARA_HALVING:
        return max(minimo, trabajadores // 2)
    return max(minimo, trabajadores - 1)


def decidir_trabajadores(
    historial: List[Medicion],
    minimo: int,
    maximo: int,
    mb_por_worker: float = utils.MB_POR_WORKER,
) -> int:
    """Decide cuántos trabajadores debe haber a partir del historial de
    mediciones. Reglas literales, en este orden (el orden importa: la RAM
    manda sobre todo lo demás):

    1. RAM libre por debajo de `2 * mb_por_worker` en la última medición →
       bajar 1 (límite duro, no preferencia).
    2. Con menos de 2 mediciones → mantener (no hay tendencia que comparar).
    3. Rendimiento mejora >5% respecto a la ventana anterior → subir (ver
       `_subir`: geométrico durante la rampa inicial, +1 después).
    4. Rendimiento empeora >5% Y el número de trabajadores SUBIÓ entre la
       ventana anterior y la última → bajar (ver `_bajar`: -1, o a la mitad
       si el desplome es grande). Esto detecta el thrashing de HDD. Si los
       trabajadores NO subieron, la caída no es atribuible al paralelismo
       (ruido de medida por el contenido de las fotos, o carga externa) y no
       se baja: bajar aquí sin más era la causa medida de que el controlador
       nunca se recuperase (`_subir` tras una bajada solo sube +1).
    5. Dentro del ±5% (zona muerta) y CPU ociosa >15% → subir (hay margen),
       salvo que en las últimas `_VENTANAS_ESPERA_TRAS_BAJADA` mediciones
       haya habido una bajada de trabajadores (histéresis: no reoscilar justo
       después de haber bajado).
    6. En cualquier otro caso → mantener.
    """
    ultima = historial[-1]

    # Regla 1: la RAM es un límite duro, se comprueba antes que nada.
    if ultima.ram_libre_mb < 2 * mb_por_worker:
        return max(minimo, ultima.trabajadores - 1)

    # Regla 2: sin dos ventanas no hay tendencia que valga.
    if len(historial) < 2:
        return ultima.trabajadores

    anterior = historial[-2]
    rendimiento_anterior = anterior.completados / anterior.segundos
    rendimiento_ultimo = ultima.completados / ultima.segundos

    if rendimiento_anterior == 0:
        cambio_pct = 0.0 if rendimiento_ultimo == 0 else float("inf")
    else:
        cambio_pct = (rendimiento_ultimo - rendimiento_anterior) / rendimiento_anterior

    # Regla 3: mejora clara → subir.
    if cambio_pct > _ZONA_MUERTA_PCT:
        return _subir(historial, ultima.trabajadores, maximo, mb_por_worker, ultima.ram_libre_mb)

    # Regla 4: empeora claro, pero solo es "thrashing de HDD" si la subida de
    # trabajadores precedió a la caída. Si el número de trabajadores no
    # cambió, la caída es ruido de medida (o carga externa), no un efecto del
    # paralelismo, y bajar aquí no tiene forma de recuperarse después.
    if cambio_pct < -_ZONA_MUERTA_PCT and ultima.trabajadores > anterior.trabajadores:
        return _bajar(cambio_pct, ultima.trabajadores, minimo)

    # Regla 5: zona muerta con margen de CPU → subir, salvo que se acabara de
    # bajar (histéresis: dar tiempo a asentarse antes de volver a subir).
    if ultima.cpu_ociosa_pct > _CPU_OCIOSA_PARA_SUBIR:
        recientes = historial[-_VENTANAS_ESPERA_TRAS_BAJADA:]
        hubo_bajada_reciente = any(
            recientes[i].trabajadores > recientes[i + 1].trabajadores
            for i in range(len(recientes) - 1)
        )
        if not hubo_bajada_reciente:
            return _subir(historial, ultima.trabajadores, maximo, mb_por_worker, ultima.ram_libre_mb)

    # Regla 6: nada de lo anterior → mantener.
    return ultima.trabajadores


#: Lectura neutra cuando no se puede medir la máquina: CPU ociosa 0 (la regla 5
#: no sube trabajadores "por si acaso") y RAM libre infinita (la regla 1 no los
#: baja por un dato que no tenemos). El controlador sigue reaccionando al
#: throughput real, que es lo que mide él mismo.
_RECURSOS_DESCONOCIDOS = (0.0, float("inf"))


def _lector_recursos_psutil():
    """Lector de recursos por defecto, vía `psutil`. Import perezoso: si el
    llamante inyecta su propio lector (siempre en tests), no hace falta cargar
    psutil para nada.

    Con red de seguridad, igual que `utils._memoria_disponible_mb`: `psutil`
    está en requirements, pero si en el .exe congelado no viajara, o si una
    lectura puntual fallara, el organizado tiene que seguir (guiado solo por
    throughput) en vez de tumbar la fase entera a mitad de run."""
    try:
        import psutil

        cpu_ociosa_pct = 100.0 - psutil.cpu_percent(interval=None)
        ram_libre_mb = psutil.virtual_memory().available / (1024 * 1024)
        return cpu_ociosa_pct, ram_libre_mb
    except Exception:
        return _RECURSOS_DESCONOCIDOS


def _proveedor_tipo_disco_por_defecto() -> Optional[str]:
    """Proveedor por defecto del tipo de disco de origen: import perezoso de
    `atom_core.diagnostico_maquina` para no crear un ciclo de imports entre
    ambos módulos (`organize.py` importa de los dos). Si el import fallara
    por lo que sea, se degrada a `None` (sin dato, sin tope)."""
    try:
        from atom_core.diagnostico_maquina import tipo_disco_origen_detectado

        return tipo_disco_origen_detectado()
    except Exception:
        return None


class ControladorAdaptativo:
    """Mide throughput/latencia/RAM en ventanas de tiempo y decide cuántos
    trabajadores debe haber en cada momento. No toca ningún pool: solo
    calcula el número; quien lo consume aforo un semáforo con él."""

    def __init__(
        self,
        minimo: int = 1,
        maximo: Optional[int] = None,
        ventana_segundos: float = 5.0,
        mb_por_worker: float = utils.MB_POR_WORKER,
        reloj: Callable[[], float] = time.monotonic,
        lector_recursos: Optional[Callable[[], "tuple[float, float]"]] = None,
        etiqueta: str = "",
        arranque: Optional[int] = None,
        tope_hdd: Optional[int] = None,
        proveedor_tipo_disco: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self.etiqueta = etiqueta
        self.minimo = minimo
        self.mb_por_worker = mb_por_worker
        self.ventana_segundos = ventana_segundos
        self.reloj = reloj
        self.lector_recursos = lector_recursos or _lector_recursos_psutil

        # Tope de trabajadores para HDD (solo la fase RGB lo pasa, ver
        # `phases.py`): si `tope_hdd` es `None` el comportamiento es
        # EXACTAMENTE el de antes de este cambio (las térmicas y todos los
        # tests existentes no se enteran).
        self.tope_hdd = tope_hdd
        self.proveedor_tipo_disco = proveedor_tipo_disco or _proveedor_tipo_disco_por_defecto
        # Una vez detectado HDD en este run, se queda capado aunque el
        # proveedor deje de responder "HDD" en una consulta puntual: el disco
        # no cambia de tipo a mitad de run.
        self._es_hdd = False
        self._aviso_tope_trazado = False

        # `workers_para_lote` dimensiona por RAM pensando en PROCESOS que
        # decodifican imágenes de 48 MP (la fase RGB). Una fase de HILOS que
        # solo esperan a un proceso externo no tiene esa presión de memoria y
        # arrancar ahí la deja infra-aprovisionada, así que se puede inyectar
        # un `arranque` propio (lo hace `phases.py` en las térmicas).
        if arranque is None:
            arranque = utils.workers_para_lote(mb_por_worker)
        # El techo por RAM que da workers_para_lote es el punto de partida;
        # el máximo del controlador es ese mismo valor multiplicado por 2,
        # nunca un número inventado.
        self.maximo = maximo if maximo is not None else arranque * 2

        self._trabajadores = max(self.minimo, min(self.maximo, arranque))
        self._historial: List[Medicion] = []

        self._lock = threading.Lock()
        self._inicio_ventana = self.reloj()
        self._completados_ventana = 0
        self._mb_ventana = 0.0

    def _aplicar_tope_disco(self, n: int) -> int:
        """Capa `n` a `tope_hdd` si el disco de origen es HDD; si no, lo
        devuelve intacto. Sin `tope_hdd` (fases que no lo pasan, como
        Térmicas) es un no-op exacto, byte a byte igual que antes de este
        cambio."""
        if self.tope_hdd is None:
            return n

        if not self._es_hdd:
            try:
                tipo = self.proveedor_tipo_disco()
            except Exception:
                # Un proveedor roto nunca puede tumbar la fase: sin dato, no
                # se capa (igual que si la sonda todavía no ha terminado).
                tipo = None
            if tipo == "HDD":
                self._es_hdd = True

        if not self._es_hdd:
            return n

        if not self._aviso_tope_trazado:
            self._aviso_tope_trazado = True
            _trazar(
                "[paralelismo]%s disco HDD: tope de trabajadores = %d",
                " " + self.etiqueta if self.etiqueta else "",
                self.tope_hdd,
            )

        return max(self.minimo, min(n, self.tope_hdd))

    @property
    def trabajadores(self) -> int:
        return self._aplicar_tope_disco(self._trabajadores)

    def registrar(self, mb: float) -> None:
        """Registra un item terminado dentro de la ventana en curso. Llamado
        desde varios hilos a la vez (uno por worker del apply): se protege
        con un lock."""
        with self._lock:
            self._completados_ventana += 1
            self._mb_ventana += mb

    def revisar(self) -> int:
        """Cierra la ventana en curso si ya ha pasado `ventana_segundos` y
        recalcula `trabajadores`. Si no ha pasado el tiempo, devuelve el
        valor actual sin tocar nada: la ventana se cierra por tiempo, no por
        número de items, o un lote de imágenes enormes nunca cerraría
        ninguna ventana."""
        ahora = self.reloj()
        with self._lock:
            transcurrido = ahora - self._inicio_ventana
            if transcurrido < self.ventana_segundos:
                return self._trabajadores

            try:
                cpu_ociosa_pct, ram_libre_mb = self.lector_recursos()
            except Exception:
                # Un fallo del lector no puede tumbar la fase: se decide con
                # lectura neutra y el throughput medido (ver `_lector_recursos_psutil`).
                cpu_ociosa_pct, ram_libre_mb = _RECURSOS_DESCONOCIDOS
            medicion = Medicion(
                trabajadores=self._trabajadores,
                completados=self._completados_ventana,
                segundos=transcurrido,
                mb_procesados=self._mb_ventana,
                cpu_ociosa_pct=cpu_ociosa_pct,
                ram_libre_mb=ram_libre_mb,
            )
            self._historial.append(medicion)

            previos = self._trabajadores
            decidido = decidir_trabajadores(
                self._historial, self.minimo, self.maximo, self.mb_por_worker
            )
            # Capar aquí también (no solo en la property `trabajadores`) para
            # que el HISTORIAL refleje los workers REALES que hubo: si no, la
            # próxima decisión razonaría sobre un `trabajadores` que nunca
            # llegó a existir de verdad.
            self._trabajadores = self._aplicar_tope_disco(decidido)
            _trazar_ventana(self.etiqueta, medicion, previos, self._trabajadores,
                            self.minimo, self.maximo)

            self._inicio_ventana = ahora
            self._completados_ventana = 0
            self._mb_ventana = 0.0

            return self._trabajadores


def _trazar_ventana(etiqueta: str, medicion: Medicion, previos: int, nuevos: int,
                    minimo: int, maximo: int) -> None:
    """Escribe al log la ventana recién cerrada. Nunca puede tumbar el
    organizado: un fallo del logging (fichero rotando, consola cerrada en el
    .exe congelado) se traga en silencio."""
    try:
        img_s = medicion.completados / medicion.segundos if medicion.segundos else 0.0
        mb_s = medicion.mb_procesados / medicion.segundos if medicion.segundos else 0.0
    except Exception:
        return
    _trazar(
        "[paralelismo]%s workers %d->%d (min=%d max=%d) | %d img en %.1fs "
        "= %.2f img/s, %.1f MB/s | cpu_ociosa=%.0f%% ram_libre=%.0fMB",
        " " + etiqueta if etiqueta else "",
        previos, nuevos, minimo, maximo,
        medicion.completados, medicion.segundos, img_s, mb_s,
        medicion.cpu_ociosa_pct, medicion.ram_libre_mb,
    )
