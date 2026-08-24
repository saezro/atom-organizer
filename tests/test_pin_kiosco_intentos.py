"""Bloqueo escalado tras PINs fallidos."""

from atom_core.pin_kiosco import ControlIntentos


class RelojFalso:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        self.t += segundos


def test_al_principio_no_esta_bloqueado():
    assert ControlIntentos(reloj=RelojFalso()).bloqueado() is False


def test_cuatro_fallos_no_bloquean():
    c = ControlIntentos(reloj=RelojFalso())
    for _ in range(4):
        c.fallo()
    assert c.bloqueado() is False


def test_cinco_fallos_bloquean_treinta_segundos():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    assert c.bloqueado() is True
    assert c.espera_segundos() == 30


def test_la_espera_expira_sola():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    reloj.avanzar(31)
    assert c.bloqueado() is False
    assert c.espera_segundos() == 0


def test_la_espera_escala_en_cada_tanda():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    reloj.avanzar(31)
    for _ in range(5):
        c.fallo()
    assert c.espera_segundos() == 60
    reloj.avanzar(61)
    for _ in range(5):
        c.fallo()
    assert c.espera_segundos() == 120


def test_la_espera_tiene_techo():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(20):
        for _ in range(5):
            c.fallo()
        reloj.avanzar(10000)
    assert c.espera_segundos() <= 600


def test_un_acierto_lo_resetea_todo():
    reloj = RelojFalso()
    c = ControlIntentos(reloj=reloj)
    for _ in range(5):
        c.fallo()
    c.acierto()
    assert c.bloqueado() is False
    assert c.espera_segundos() == 0
    for _ in range(4):
        c.fallo()
    assert c.bloqueado() is False
