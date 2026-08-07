import pytest

from utils import run_batch


def _worker_ok(item):
    return item * 2


def _worker_fails_on_item_k(item, k):
    if item == k:
        raise ValueError(f"fallo simulado en el item {item}")
    return item * 2


def test_run_batch_procesa_todos_los_items_sin_fallos():
    items = list(range(10))
    result = run_batch(
        items,
        worker_fn=_worker_ok,
        worker_args_fn=lambda item: (item,),
        max_workers=2,
    )
    assert sorted(result["results"]) == [i * 2 for i in items]
    assert result["errors"] == []


def test_run_batch_tolera_que_un_item_falle_y_procesa_el_resto(monkeypatch):
    items = list(range(10))
    k = 5  # el item con valor 5 fallará

    result = run_batch(
        items,
        worker_fn=_worker_fails_on_item_k,
        worker_args_fn=lambda item: (item, k),
        max_workers=2,
    )

    esperado = sorted(i * 2 for i in items if i != k)
    assert sorted(result["results"]) == esperado
    assert len(result["errors"]) == 1
    assert result["errors"][0][0] == k
    assert "fallo simulado" in result["errors"][0][1]


def test_run_batch_reporta_progreso_hasta_100_incluso_con_fallos():
    items = list(range(4))
    k = 2
    progresos = []

    run_batch(
        items,
        worker_fn=_worker_fails_on_item_k,
        worker_args_fn=lambda item: (item, k),
        on_progress=progresos.append,
        max_workers=2,
    )

    assert len(progresos) == len(items)  # se llama a on_progress una vez por item completado (éxito o fallo)
    assert progresos[-1] == 100
    assert all(0 <= p <= 100 for p in progresos)


def test_run_batch_con_lista_vacia_no_lanza_procesos_ni_falla():
    result = run_batch([], worker_fn=_worker_ok, worker_args_fn=lambda item: (item,))
    assert result["results"] == []
    assert result["errors"] == []
