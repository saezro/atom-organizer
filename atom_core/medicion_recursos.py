"""Mide disco/CPU del organizado por fase, para saber dónde está el cuello.

PORQUÉ EXISTE
-------------
Organizar una planta tarda ~20 min y hasta ahora nadie sabía si el cuello era
el disco (lectura/escritura de las imágenes originales, RAW enormes) o la CPU
(recorte, conversión TIFF, exif). Sin ese dato cualquier optimización es un
tiro a ciegas. Este módulo muestrea `psutil` en un hilo de fondo mientras el
pipeline corre y, al cierre de cada fase, devuelve un veredicto legible.

DECISIÓN DE DISEÑO CLAVE: contadores GLOBALES, no del proceso propio
---------------------------------------------------------------------
El pipeline reparte el trabajo pesado en un `ProcessPoolExecutor`: los workers
hacen la I/O real y MUEREN al terminar la fase. Si midiéramos
`psutil.Process(os.getpid()).io_counters()` del proceso padre, esa I/O de los
hijos ya desaparecidos no contaría para nada — veríamos "0 MB leídos" en la
fase que más lee disco. Por eso se usa `psutil.disk_io_counters()`, que son los
contadores del DISCO DEL SISTEMA (agregan todo lo que hay corriendo en la
máquina). Es menos "puro" (en teoría se contamina si otro proceso ajeno hace
I/O a la vez), pero en la práctica del organizado un despliegue de escritorio
no comparte máquina con otra carga, y es la única forma de ver la I/O real que
importa aquí.

ROBUSTEZ: esto NUNCA puede tumbar ni frenar el organizado
-----------------------------------------------------------
Es puramente diagnóstico. Si `psutil` no está instalado, si
`disk_io_counters()` devuelve `None` (pasa en algunas VMs/contenedores sin
soporte) o si cualquier llamada lanza por lo que sea, el medidor se queda
INERTE: todos los métodos públicos devuelven `None` sin propagar la excepción,
y el hilo de muestreo captura cualquier `Exception` en su bucle sin morirse
(o simplemente no arranca).
"""
from __future__ import annotations

import threading
import time
from typing import Optional

try:
    import psutil
except Exception:  # pragma: no cover - entorno sin psutil
    psutil = None

# Intervalo de muestreo del hilo de fondo. 1 s es suficiente para fases de
# minutos y no genera overhead perceptible.
_INTERVALO_MUESTREO_S = 1.0

# Umbrales del veredicto (porcentaje de CPU sobre TODOS los núcleos).
_UMBRAL_DISCO = 40.0
_UMBRAL_CPU = 70.0

# Ventana que se conserva en `_muestras_recientes` para `resumen_parcial()`.
# Más que suficiente para cualquier `ventana_s` razonable (el uso previsto es
# de pocos segundos a un par de minutos); recortar aquí evita que la lista
# crezca sin límite en fases de 20+ minutos.
_VENTANA_RECIENTES_MAX_S = 300.0


def _snapshot_disco():
    """Devuelve `(mb_leidos_acum, mb_escritos_acum)` o `None` si no hay dato.

    Los contadores de `disk_io_counters()` son ACUMULADOS desde el arranque del
    sistema; la resta entre dos snapshots da el consumo del intervalo.
    """
    if psutil is None:
        return None
    try:
        io = psutil.disk_io_counters()
    except Exception:
        return None
    if io is None:
        # Pasa en algunas VMs/contenedores: el kernel no expone el contador.
        return None
    return (io.read_bytes / (1024 * 1024), io.write_bytes / (1024 * 1024))


def _veredicto(cpu_pct: Optional[float], tipo_disco: Optional[str] = None) -> Optional[str]:
    if cpu_pct is None:
        return None
    # Con un HDD el umbral de 40% es demasiado optimista: en discos mecánicos
    # el organizado espera a la cabeza lectora bastante antes de que la CPU se
    # note "libre" en el sentido de la SSD (visto en campo: fase RGB al 47% de
    # CPU y 36 MB/s en un disco de origen mecánico, claramente disco-bound
    # aunque el 47% caiga en la banda "mixto" de un SSD). Con HDD confirmado,
    # cualquier CPU por debajo del umbral "cpu" ya es indicio suficiente de
    # cuello de disco.
    if tipo_disco == "HDD" and cpu_pct < _UMBRAL_CPU:
        return "disco"
    if cpu_pct < _UMBRAL_DISCO:
        return "disco"
    if cpu_pct > _UMBRAL_CPU:
        return "cpu"
    return "mixto"


class MedidorRecursos:
    """Muestreador de CPU/disco en segundo plano, con cortes por fase.

    Uso:
        medidor = MedidorRecursos()
        medidor.iniciar()
        medidor.abrir_fase()
        ... trabajo de la fase ...
        resumen = medidor.cerrar_fase()   # dict o None
        medidor.abrir_fase()              # siguiente fase
        ...
        medidor.detener()
        total = medidor.resumen_total()
    """

    def __init__(self) -> None:
        self._activo = psutil is not None
        self._hilo: Optional[threading.Thread] = None
        self._parar = threading.Event()
        self._lock = threading.Lock()

        # Muestras de CPU acumuladas desde `iniciar()` (para el resumen total)
        # y desde el último `abrir_fase()` (para el resumen de fase). Guardamos
        # ambas listas porque una fase corta no debe perder sus propias
        # muestras al recortarlas del acumulado global.
        self._muestras_cpu_total: list = []
        self._muestras_cpu_fase: list = []

        # Buffer con marca de tiempo (`time.monotonic()`) para `resumen_parcial()`:
        # tuplas `(ts, cpu_pct, snapshot_disco_o_None)`, una por vuelta del hilo
        # de muestreo. Es independiente de las dos listas de arriba (que no
        # llevan timestamp) para no tocar su formato ni el de los resúmenes
        # existentes.
        self._muestras_recientes: list = []

        self._inicio_total_disco = None
        self._inicio_total_ts = None
        self._inicio_fase_disco = None
        self._inicio_fase_ts = None

        # Tipo de disco de ORIGEN ("HDD"/"SSD"/"desconocido"/None): afecta al
        # veredicto porque un HDD se satura con mucha menos CPU que un SSD.
        self._tipo_disco: Optional[str] = None

    # -- ciclo de vida del hilo -------------------------------------------------

    def iniciar(self) -> None:
        """Arranca el muestreo. Idempotente: llamar dos veces no duplica el hilo."""
        if not self._activo:
            return
        try:
            if self._hilo is not None and self._hilo.is_alive():
                return
            self._parar.clear()
            # Primera lectura de `cpu_percent()` sin intervalo devuelve 0.0/basura
            # (necesita una llamada previa como referencia); se descarta aquí para
            # que la primera muestra útil sea la del bucle.
            psutil.cpu_percent(percpu=False)
            snap = _snapshot_disco()
            ahora = time.monotonic()
            self._inicio_total_disco = snap
            self._inicio_total_ts = ahora
            self._inicio_fase_disco = snap
            self._inicio_fase_ts = ahora
            self._muestras_cpu_total = []
            self._muestras_cpu_fase = []
            self._muestras_recientes = []
            self._hilo = threading.Thread(
                target=self._bucle_muestreo, name="MedidorRecursos", daemon=True
            )
            self._hilo.start()
        except Exception:
            self._activo = False

    def detener(self) -> None:
        """Para el hilo. Segura de llamar sin haber iniciado nunca."""
        try:
            self._parar.set()
            if self._hilo is not None and self._hilo.is_alive():
                self._hilo.join(timeout=_INTERVALO_MUESTREO_S * 3)
            self._hilo = None
        except Exception:
            pass

    def _bucle_muestreo(self) -> None:
        while not self._parar.is_set():
            try:
                muestra = psutil.cpu_percent(percpu=False)
                ts = time.monotonic()
                disco = _snapshot_disco()
                with self._lock:
                    self._muestras_cpu_total.append(muestra)
                    self._muestras_cpu_fase.append(muestra)
                    self._muestras_recientes.append((ts, muestra, disco))
                    # Recorte: solo hace falta conservar la ventana máxima que
                    # `resumen_parcial()` puede pedir; el resto ya no aporta.
                    corte = ts - _VENTANA_RECIENTES_MAX_S
                    while self._muestras_recientes and self._muestras_recientes[0][0] < corte:
                        self._muestras_recientes.pop(0)
            except Exception:
                # Un fallo puntual de psutil no debe matar el hilo: se ignora
                # la muestra y se sigue intentando en la siguiente vuelta.
                pass
            self._parar.wait(_INTERVALO_MUESTREO_S)

    def configurar_disco(self, tipo_origen: Optional[str]) -> None:
        """Fija el tipo de disco de ORIGEN ("HDD"/"SSD"/"desconocido"/None).

        Se usa solo para afinar `_veredicto`: un HDD se satura con mucha menos
        CPU que un SSD, así que el mismo % de CPU significa cosas distintas
        según el disco. No dispara ninguna medición por sí sola.
        """
        self._tipo_disco = tipo_origen

    # -- fases -------------------------------------------------------------

    def abrir_fase(self) -> None:
        """Marca el inicio de una fase: snapshot de disco y reset de muestras CPU."""
        if not self._activo:
            return
        try:
            with self._lock:
                self._muestras_cpu_fase = []
            self._inicio_fase_disco = _snapshot_disco()
            self._inicio_fase_ts = time.monotonic()
        except Exception:
            pass

    def cerrar_fase(self) -> Optional[dict]:
        """Resumen de la fase abierta desde el último `abrir_fase()`."""
        if not self._activo:
            return None
        try:
            with self._lock:
                muestras = list(self._muestras_cpu_fase)
            return self._resumir(muestras, self._inicio_fase_disco, self._inicio_fase_ts)
        except Exception:
            return None

    def resumen_total(self) -> Optional[dict]:
        """Resumen agregado desde el último `iniciar()`."""
        if not self._activo:
            return None
        try:
            with self._lock:
                muestras = list(self._muestras_cpu_total)
            return self._resumir(muestras, self._inicio_total_disco, self._inicio_total_ts)
        except Exception:
            return None

    def resumen_parcial(self, ventana_s: float = 15.0) -> Optional[dict]:
        """Veredicto EN VIVO sobre los últimos `ventana_s` segundos.

        Pensado para emitirse periódicamente mientras una fase larga corre
        (a diferencia de `cerrar_fase()`, que solo existe al final). Usa el
        buffer `_muestras_recientes` en vez de recortar las listas de fase,
        para no interferir con el resumen "oficial" de la fase.
        """
        if not self._activo:
            return None
        try:
            ahora = time.monotonic()
            corte = ahora - ventana_s
            with self._lock:
                ventana = [m for m in self._muestras_recientes if m[0] >= corte]

            # Menos de 2 muestras = ruido: ni la media de CPU ni el delta de
            # disco (que necesita dos puntos) son fiables todavía.
            if len(ventana) < 2:
                return None

            cpu_pct = sum(m[1] for m in ventana) / len(ventana)
            segundos = ventana[-1][0] - ventana[0][0]

            discos_validos = [m[2] for m in ventana if m[2] is not None]
            if len(discos_validos) >= 2:
                mb_leidos = max(0.0, discos_validos[-1][0] - discos_validos[0][0])
                mb_escritos = max(0.0, discos_validos[-1][1] - discos_validos[0][1])
            else:
                mb_leidos = 0.0
                mb_escritos = 0.0
            mb_por_segundo = (mb_leidos + mb_escritos) / segundos if segundos > 0 else 0.0

            try:
                nucleos = psutil.cpu_count(logical=True) or 1
            except Exception:
                nucleos = 1

            return {
                "mb_leidos": round(mb_leidos, 1),
                "mb_escritos": round(mb_escritos, 1),
                "mb_por_segundo": round(mb_por_segundo, 1),
                "cpu_pct": round(cpu_pct, 1),
                "nucleos": nucleos,
                "veredicto": _veredicto(cpu_pct, self._tipo_disco),
                "tipo_disco": self._tipo_disco,
            }
        except Exception:
            return None

    def _resumir(self, muestras: list, inicio_disco, inicio_ts) -> Optional[dict]:
        if inicio_ts is None:
            return None
        segundos = time.monotonic() - inicio_ts
        # Fase demasiado corta para que las muestras (cada ~1 s) signifiquen algo:
        # no hay dato fiable, mejor no aventurar un veredicto.
        if segundos < _INTERVALO_MUESTREO_S * 2 or not muestras:
            cpu_pct = None
        else:
            cpu_pct = sum(muestras) / len(muestras)

        fin_disco = _snapshot_disco()
        if inicio_disco is not None and fin_disco is not None:
            mb_leidos = max(0.0, fin_disco[0] - inicio_disco[0])
            mb_escritos = max(0.0, fin_disco[1] - inicio_disco[1])
        else:
            mb_leidos = 0.0
            mb_escritos = 0.0
        mb_por_segundo = (mb_leidos + mb_escritos) / segundos if segundos > 0 else 0.0

        try:
            nucleos = psutil.cpu_count(logical=True) or 1
        except Exception:
            nucleos = 1

        return {
            "mb_leidos": round(mb_leidos, 1),
            "mb_escritos": round(mb_escritos, 1),
            "mb_por_segundo": round(mb_por_segundo, 1),
            "cpu_pct": round(cpu_pct, 1) if cpu_pct is not None else 0.0,
            "nucleos": nucleos,
            "veredicto": _veredicto(cpu_pct, self._tipo_disco),
            "tipo_disco": self._tipo_disco,
        }
