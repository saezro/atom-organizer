"""Tests unitarios de `scripts/simular_estadillo_digital.py`: solo el
generador de vuelos sintéticos (`_vuelos_de_prueba`), sin red ni docker."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import simular_estadillo_digital as sim


def test_hora_inicio_por_defecto_es_0800():
    vuelos = sim._vuelos_de_prueba("KL05", "2026-09-20", "Rebeca", "M300", n_vuelos=2)
    assert vuelos[0]["Hora_de_inicio"] == "08:00:00"
    assert vuelos[0]["Hora_final"] == "08:25:00"
    assert vuelos[1]["Hora_de_inicio"] == "08:30:00"


def test_hora_inicio_desplaza_vuelos_consecutivos():
    vuelos = sim._vuelos_de_prueba("KL19", "2026-08-19", "Rebeca", "M300",
                                    n_vuelos=2, hora_inicio="13:27")
    assert vuelos[0]["Hora_de_inicio"] == "13:27:00"
    assert vuelos[0]["Hora_final"] == "13:52:00"
    assert vuelos[1]["Hora_de_inicio"] == "13:57:00"


def test_fuera_de_tarjeta_queda_3h_antes_de_hora_inicio():
    vuelos = sim._vuelos_de_prueba("KL19", "2026-08-19", "Rebeca", "M300",
                                    n_vuelos=1, fuera_de_tarjeta=1, hora_inicio="13:27")
    fuera = vuelos[-1]
    assert fuera["PB"] == "9"
    assert fuera["Hora_de_inicio"] == "10:27:00"
    assert fuera["Hora_final"] == "10:52:00"


def test_fuera_de_tarjeta_no_cruza_medianoche_hacia_atras():
    vuelos = sim._vuelos_de_prueba("KL05", "2026-09-20", "Rebeca", "M300",
                                    n_vuelos=1, fuera_de_tarjeta=1, hora_inicio="01:00")
    fuera = vuelos[-1]
    assert fuera["Hora_de_inicio"] == "00:00:00"
