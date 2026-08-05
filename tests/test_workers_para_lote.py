"""
El pool no puede abrir un proceso por núcleo a ciegas.

`run_batch` corre en el ordenador del usuario, no en un servidor. Con imágenes de 48 MP
dentro de cada worker, un proceso por núcleo puede agotar la RAM de un portátil y llevarse
por delante el procesado — o dejar la máquina inusable mientras dura. `workers_para_lote`
elige el menor de (núcleos libres, memoria disponible / coste por worker).

Lo que NO hace, y por eso no hay test de calidad aquí: cambiar el número de procesos no
cambia ni un píxel de la salida. Eso lo fija `test_rotacion_paralela.py`, que compara el
resultado del pool con el del bucle secuencial byte a byte.
"""
import os

import utils


def test_deja_nucleos_libres_para_la_interfaz(monkeypatch):
    """Ocupar la máquina entera deja la app congelada aunque el lote acabe antes."""
    monkeypatch.setattr(utils, "_memoria_disponible_mb", lambda: 64 * 1024)

    workers = utils.workers_para_lote()

    nucleos = os.process_cpu_count() if hasattr(os, "process_cpu_count") else os.cpu_count()
    assert workers <= max(1, nucleos - utils.NUCLEOS_RESERVADOS), (
        f"Se abrirían {workers} procesos con {nucleos} núcleos: no queda margen para la interfaz."
    )


def test_la_memoria_manda_cuando_es_el_recurso_escaso(monkeypatch):
    """Con 2 GB libres y 600 MB por worker caben 3, aunque haya 32 núcleos."""
    monkeypatch.setattr(utils, "_memoria_disponible_mb", lambda: 2048)

    assert utils.workers_para_lote(mb_por_worker=600) == 3, (
        "El cálculo no está mirando la RAM disponible: con 2 GB libres, abrir un proceso por "
        "núcleo es justo lo que agota la memoria de la máquina del usuario."
    )


def test_nunca_baja_de_un_proceso(monkeypatch):
    """
    Con la máquina al límite se procesa de uno en uno — que es exactamente lo que hacía la
    versión secuencial. Devolver 0 dejaría el pool sin poder arrancar.
    """
    monkeypatch.setattr(utils, "_memoria_disponible_mb", lambda: 10)

    assert utils.workers_para_lote(mb_por_worker=600) == 1


def test_sin_psutil_sigue_funcionando_solo_con_cpu(monkeypatch):
    """
    `psutil` está en requirements, pero si no viajara en el ejecutable congelado el
    procesado tiene que seguir, limitado por CPU. Un fallo aquí tumbaría el lote entero
    por no poder medir la memoria.
    """
    monkeypatch.setattr(utils, "_memoria_disponible_mb", lambda: None)

    assert utils.workers_para_lote() >= 1


def test_no_abre_mas_procesos_que_items(monkeypatch):
    """Un vuelo de 2 imágenes no necesita 15 procesos: solo cuesta arrancarlos."""
    usados = {}

    class _FakeExecutor:
        def __init__(self, max_workers=None):
            usados["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, *args):
            return object()  # Solo hace falta que sea hashable: as_completed no devuelve nada.

    monkeypatch.setattr(utils, "ProcessPoolExecutor", _FakeExecutor)
    monkeypatch.setattr(utils, "as_completed", lambda futures: [])

    utils.run_batch(["a", "b"], lambda x: x, lambda x: (x,))

    assert usados["max_workers"] == 2, (
        f"Se abrieron {usados['max_workers']} procesos para 2 items."
    )


def test_max_workers_explicito_manda(monkeypatch):
    """Quien pasa un número lo hace por algo; el cálculo automático no debe pisarlo."""
    usados = {}

    class _FakeExecutor:
        def __init__(self, max_workers=None):
            usados["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, *args):
            return object()  # Solo hace falta que sea hashable: as_completed no devuelve nada.

    monkeypatch.setattr(utils, "ProcessPoolExecutor", _FakeExecutor)
    monkeypatch.setattr(utils, "as_completed", lambda futures: [])
    monkeypatch.setattr(utils, "workers_para_lote", lambda *a, **k: 99)

    utils.run_batch(["a", "b", "c", "d"], lambda x: x, lambda x: (x,), max_workers=2)

    assert usados["max_workers"] == 2
