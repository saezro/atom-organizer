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
    subiendo: es el caso de un SSD infrautilizado."""
    historial = [_medicion(trabajadores=4, completados=100),
                 _medicion(trabajadores=5, completados=130)]
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


def test_baja_si_el_rendimiento_empeora():
    """Thrashing de HDD: al subir trabajadores el throughput CAE. Si el
    controlador no lo detecta y baja, el run se hace más lento cuanto más
    paralelismo le echamos, que es justo lo contrario de lo que se busca."""
    historial = [_medicion(trabajadores=6, completados=130),
                 _medicion(trabajadores=7, completados=90)]
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
    assert decidir_trabajadores(historial, minimo=1, maximo=16) == 6


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
