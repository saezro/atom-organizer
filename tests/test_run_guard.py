from utils import can_start


def test_can_start_false_si_busy():
    assert can_start(busy=True, active_threads=0) is False


def test_can_start_false_si_hay_hilos_activos():
    assert can_start(busy=False, active_threads=1) is False


def test_can_start_true_si_libre():
    assert can_start(busy=False, active_threads=0) is True


def test_can_start_false_si_busy_y_ademas_hilos_activos():
    assert can_start(busy=True, active_threads=2) is False
