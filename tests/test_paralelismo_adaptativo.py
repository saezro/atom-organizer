"""Tests del controlador adaptativo de paralelismo.

El organizado corre en portátiles distintos, con discos distintos (SSD o
HDD, mismo disco o dos) y con el usuario haciendo otras cosas a la vez.
Rodrigo reportó un run 10 minutos más lento solo porque el PC estaba
ocupado. Un número de trabajadores fijado al arrancar no puede acertar en
todos esos casos: este controlador se corrige durante el run.

Todo se prueba con reloj y lector de recursos inyectados: un test que
dependiera del hardware real o de dormir sería lento y no determinista.
"""
import sys

import pytest

from atom_core import paralelismo
from atom_core.paralelismo import ControladorAdaptativo, Medicion, decidir_trabajadores


def _medicion(trabajadores=4, completados=100, segundos=10.0, mb_procesados=500.0,
              cpu_ociosa_pct=10.0, ram_libre_mb=8000.0):
    return Medicion(trabajadores, completados, segundos, mb_procesados,
                    cpu_ociosa_pct, ram_libre_mb)


def test_sube_si_el_rendimiento_mejora():
    """Mientras añadir trabajadores dé más imágenes por segundo, hay que seguir
    subiendo: es el caso de un SSD infrautilizado.

    En la rampa inicial (nunca se ha bajado) el salto es GEOMÉTRICO: subir de
    uno en uno cada 5 s tarda minutos en llegar al techo y una fase entera se
    consume infra-aprovisionada."""
    historial = [_medicion(trabajadores=4, completados=100),
                 _medicion(trabajadores=5, completados=130)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 10


def test_tras_una_bajada_la_subida_vuelve_a_ser_de_uno_en_uno():
    """Si el historial tiene una ventana con MÁS trabajadores que ahora, es que
    ya se tocó el techo real de la máquina y se bajó. A partir de ahí duplicar
    volvería a provocar el mismo thrashing: toca afinar de uno en uno."""
    historial = [_medicion(trabajadores=12, completados=90),
                 _medicion(trabajadores=6, completados=100),
                 _medicion(trabajadores=6, completados=130)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 7


def test_baja_si_el_rendimiento_empeora():
    """Thrashing de HDD: al subir trabajadores el throughput CAE. Si el
    controlador no lo detecta y baja, el run se hace más lento cuanto más
    paralelismo le echamos, que es justo lo contrario de lo que se busca."""
    historial = [_medicion(trabajadores=6, completados=130),
                 _medicion(trabajadores=7, completados=115)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_un_desplome_grande_corta_a_la_mitad():
    """Contrapartida del salto geométrico: si duplicar provocó thrashing, hay
    que deshacerlo igual de rápido. Bajando de uno en uno se tarda tantas
    ventanas como saltos hubo, y todas ellas corriendo mal."""
    historial = [_medicion(trabajadores=6, completados=130),
                 _medicion(trabajadores=12, completados=60)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_la_ram_manda_sobre_el_rendimiento():
    """Aunque el rendimiento esté mejorando, si queda poca RAM hay que bajar.
    Un pico de RAM con muchos decodes simultáneos tumba el proceso entero y
    se pierde el run: es un límite duro, no una preferencia."""
    historial = [_medicion(trabajadores=6, completados=100, ram_libre_mb=9000.0),
                 _medicion(trabajadores=7, completados=200, ram_libre_mb=200.0)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_mantiene_si_esta_en_la_zona_muerta_y_no_hay_cpu_libre():
    """Sin zona muerta, el ruido de medida haría oscilar el número de
    trabajadores arriba y abajo en cada ventana, y cada cambio de tamaño de
    pool cuesta."""
    historial = [_medicion(trabajadores=5, completados=100, cpu_ociosa_pct=5.0),
                 _medicion(trabajadores=5, completados=102, cpu_ociosa_pct=5.0)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 5


def test_sube_en_zona_muerta_si_sobra_cpu():
    """Rendimiento plano pero máquina medio ociosa: el cuello no es la CPU,
    merece la pena probar un trabajador más."""
    historial = [_medicion(trabajadores=5, completados=100, cpu_ociosa_pct=60.0),
                 _medicion(trabajadores=5, completados=101, cpu_ociosa_pct=60.0)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 10


def test_respeta_minimo_y_maximo():
    """Nunca cero trabajadores (el run se pararía) ni por encima del techo de
    RAM calculado al arrancar."""
    empeora = [_medicion(trabajadores=1, completados=100),
               _medicion(trabajadores=1, completados=10)]
    assert decidir_trabajadores(empeora, minimo=1, maximo=16) == 1

    mejora = [_medicion(trabajadores=16, completados=100),
              _medicion(trabajadores=16, completados=300)]
    assert decidir_trabajadores(mejora, minimo=1, maximo=16) == 16


def test_una_sola_medicion_mantiene():
    """Sin dos ventanas no hay comparación posible: inventarse una tendencia
    con un solo dato es peor que esperar."""
    assert decidir_trabajadores([_medicion()], minimo=1, maximo=16) == 4


class _RelojFalso:
    """Reloj monótono controlado por el test. Evita dormir de verdad."""

    def __init__(self):
        self.ahora = 0.0

    def __call__(self):
        return self.ahora

    def avanzar(self, segundos):
        self.ahora += segundos


def test_el_controlador_cierra_ventanas_por_tiempo():
    """La ventana se cierra por tiempo, no por número de items: si no, un lote
    de imágenes enormes no cerraría ninguna ventana y el controlador nunca
    reaccionaría."""
    reloj = _RelojFalso()
    recursos = {"cpu": 60.0, "ram": 9000.0}
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (recursos["cpu"], recursos["ram"]),
    )
    inicial = controlador.trabajadores

    for _ in range(10):
        controlador.registrar(mb=5.0)
    assert controlador.revisar() == inicial  # aún no ha pasado la ventana

    reloj.avanzar(6.0)
    for _ in range(10):
        controlador.registrar(mb=5.0)
    controlador.revisar()
    reloj.avanzar(6.0)
    for _ in range(30):
        controlador.registrar(mb=5.0)

    assert controlador.revisar() > inicial


def test_el_controlador_baja_cuando_se_acaba_la_ram():
    """Comprobación de extremo a extremo de la regla dura de RAM a través del
    controlador, no solo de la función de decisión."""
    reloj = _RelojFalso()
    recursos = {"cpu": 60.0, "ram": 9000.0}
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (recursos["cpu"], recursos["ram"]),
    )
    reloj.avanzar(6.0)
    controlador.registrar(mb=5.0)
    controlador.revisar()

    recursos["ram"] = 100.0
    reloj.avanzar(6.0)
    controlador.registrar(mb=5.0)
    antes = controlador.trabajadores
    assert controlador.revisar() < antes


def test_lector_psutil_devuelve_neutro_si_psutil_falla(monkeypatch):
    """Red de seguridad documentada en `_lector_recursos_psutil`: si `psutil`
    no viaja en el .exe congelado, o una lectura puntual revienta, el
    organizado tiene que seguir guiado solo por throughput en vez de tumbar
    la fase entera a mitad de run. `_lector_recursos_psutil` debe devolver
    la lectura neutra `_RECURSOS_DESCONOCIDOS`, nunca dejar subir la
    excepción."""

    class _PsutilQueRevienta:
        def cpu_percent(self, interval=None):
            raise RuntimeError("psutil roto (simulado)")

    monkeypatch.setitem(sys.modules, "psutil", _PsutilQueRevienta())

    assert paralelismo._lector_recursos_psutil() == paralelismo._RECURSOS_DESCONOCIDOS


def test_revisar_no_propaga_si_el_lector_de_recursos_lanza():
    """`revisar()` ya envuelve la llamada a `lector_recursos` en su propio
    try/except (para el lector inyectado, no solo para el de psutil): si
    revienta a mitad de ventana, la ventana se cierra igual con la lectura
    neutra y `revisar()` devuelve un entero. Sin esta red, un lector de
    recursos que fallara tumbaría el apply entero en plena fase."""
    reloj = _RelojFalso()

    def _lector_que_revienta():
        raise RuntimeError("lector de recursos roto (simulado)")

    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=_lector_que_revienta,
    )
    reloj.avanzar(6.0)
    controlador.registrar(mb=5.0)

    resultado = controlador.revisar()

    assert isinstance(resultado, int)
    assert resultado >= controlador.minimo


def test_el_arranque_se_puede_inyectar_para_fases_io():
    """Una fase de HILOS que esperan a un proceso externo (térmicas) no tiene
    la presión de RAM que `workers_para_lote` presupone: arrancar ahí la deja
    infra-aprovisionada. `phases.py` le inyecta `utils.arranque_io()`."""
    controlador = ControladorAdaptativo(maximo=32, arranque=16,
                                        lector_recursos=lambda: (50.0, 8000.0))
    assert controlador.trabajadores == 16


def test_el_arranque_inyectado_respeta_el_maximo():
    """El arranque nunca puede saltarse el techo de la fase."""
    controlador = ControladorAdaptativo(maximo=4, arranque=16,
                                        lector_recursos=lambda: (50.0, 8000.0))
    assert controlador.trabajadores == 4


def test_arranque_io_no_pasa_del_techo_de_io():
    """`arranque_io` es un punto de partida, nunca por encima de
    `max_io_workers` (que es el techo de la fase)."""
    import utils

    assert 1 <= utils.arranque_io() <= utils.max_io_workers()


def test_no_sube_por_encima_del_techo_de_ram_libre_aunque_el_maximo_sea_mayor():
    """El aforo antiguo (`maximo = arranque*2`) es estático y no vigila la
    RAM mientras el run avanza: en un PC de 8 GB podía subir hasta 8-12
    workers x 600 MB = 4,8-7,2 GB solo en esta fase. Con poca RAM libre en la
    última medición, `_subir` (llamado por la regla 3, rendimiento mejorando)
    debe TOPAR por debajo de `maximo`, no solo por debajo de él."""
    # Con 5 trabajadores y 600 MB/worker, dejando un colchón de 600 MB de
    # margen, 1200 MB libres solo dan margen para 1 worker más (el mínimo
    # para no chocar con la regla 1, que baja por debajo de esa RAM): sin
    # techo dinámico el salto geométrico habría subido a 10 (maximo=16 no
    # lo habría frenado); con él se queda en 6.
    historial = [
        _medicion(trabajadores=5, completados=100, ram_libre_mb=1200.0),
        _medicion(trabajadores=5, completados=130, ram_libre_mb=1200.0),
    ]
    assert decidir_trabajadores(historial, minimo=1, maximo=16, mb_por_worker=600.0) == 6


def test_sube_normal_si_hay_ram_de_sobra():
    """Con RAM abundante, el techo dinámico no debe cambiar el comportamiento
    de siempre: sigue subiendo geométrico en la rampa inicial, igual que
    antes de vigilar la RAM en `_subir`."""
    historial = [
        _medicion(trabajadores=5, completados=100, ram_libre_mb=16000.0),
        _medicion(trabajadores=5, completados=130, ram_libre_mb=16000.0),
    ]
    assert decidir_trabajadores(historial, minimo=1, maximo=16, mb_por_worker=600.0) == 10


def test_techo_por_ram_libre_no_pasa_del_maximo_estatico(monkeypatch):
    """Extremo a extremo con el controlador real: aunque la RAM disponible
    dé para muchísimos workers, `maximo` (fijo, el `arranque*2` de siempre)
    sigue siendo un techo que nunca se salta."""
    reloj = _RelojFalso()
    controlador = ControladorAdaptativo(
        minimo=1, maximo=6, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (60.0, 999999.0), arranque=5,
    )
    for _ in range(3):
        reloj.avanzar(6.0)
        for _ in range(50):
            controlador.registrar(mb=5.0)
        controlador.revisar()

    assert controlador.trabajadores <= 6


# --- tope_hdd: capar RGB en disco mecánico ---------------------------------


def test_tope_hdd_capa_aunque_el_rendimiento_pida_subir():
    """Con `tope_hdd=3` y disco HDD, aunque las ventanas simulen CPU ociosa
    alta (regla 5, la que interpretaba erróneamente el 47% de espera a disco
    como margen libre) `trabajadores` nunca debe superar el tope."""
    reloj = _RelojFalso()
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (60.0, 9000.0), arranque=7,
        tope_hdd=3, proveedor_tipo_disco=lambda: "HDD",
    )
    assert controlador.trabajadores == 3

    for _ in range(4):
        reloj.avanzar(6.0)
        for _ in range(50):
            controlador.registrar(mb=5.0)
        controlador.revisar()

    assert controlador.trabajadores <= 3


def test_tope_hdd_no_afecta_si_el_disco_es_ssd():
    """Con disco SSD (o desconocido) el comportamiento debe ser IDÉNTICO al
    de siempre: sigue subiendo por encima del tope de HDD."""
    reloj = _RelojFalso()
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (60.0, 9000.0), arranque=4,
        tope_hdd=3, proveedor_tipo_disco=lambda: "SSD",
    )
    for _ in range(3):
        reloj.avanzar(6.0)
        for _ in range(50):
            controlador.registrar(mb=5.0)
        controlador.revisar()

    assert controlador.trabajadores > 3


def test_tope_hdd_no_afecta_si_el_proveedor_devuelve_none():
    """Mientras la sonda todavía no ha terminado (`None`), no hay dato con el
    que capar: se comporta igual que sin `tope_hdd`."""
    reloj = _RelojFalso()
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (60.0, 9000.0), arranque=4,
        tope_hdd=3, proveedor_tipo_disco=lambda: None,
    )
    for _ in range(3):
        reloj.avanzar(6.0)
        for _ in range(50):
            controlador.registrar(mb=5.0)
        controlador.revisar()

    assert controlador.trabajadores > 3


def test_sin_tope_hdd_comportamiento_idéntico_al_actual():
    """`tope_hdd=None` (el default) no debe alterar nada, ni siquiera con un
    proveedor que dijera HDD: es el camino de las térmicas y de cualquier
    controlador creado antes de este cambio."""
    reloj = _RelojFalso()
    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (60.0, 9000.0), arranque=4,
        proveedor_tipo_disco=lambda: "HDD",
    )
    for _ in range(3):
        reloj.avanzar(6.0)
        for _ in range(50):
            controlador.registrar(mb=5.0)
        controlador.revisar()

    assert controlador.trabajadores > 3


def test_tope_hdd_proveedor_que_lanza_no_rompe_ni_capa():
    """Un proveedor roto nunca puede tumbar la fase (regla general de este
    módulo): se degrada a "sin dato" y no capa, igual que el resto de redes
    de seguridad de este controlador."""
    reloj = _RelojFalso()

    def _proveedor_que_revienta():
        raise RuntimeError("proveedor de tipo de disco roto (simulado)")

    controlador = ControladorAdaptativo(
        minimo=1, maximo=16, ventana_segundos=5.0, mb_por_worker=500.0,
        reloj=reloj, lector_recursos=lambda: (60.0, 9000.0), arranque=4,
        tope_hdd=3, proveedor_tipo_disco=_proveedor_que_revienta,
    )
    for _ in range(3):
        reloj.avanzar(6.0)
        for _ in range(50):
            controlador.registrar(mb=5.0)
        controlador.revisar()

    assert controlador.trabajadores > 3
