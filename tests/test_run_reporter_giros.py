"""`RunReporter.giros()`: qué vuelos se giraron y en qué sentido -> la Suite.

Sigue el mismo patrón que `vaciar_logs()`/`fin()`: fail-open (un fallo de red
no debe abortar el run) y no-op silencioso sin `run_id`. Se mockea
`RunReporter._peticion` directamente -- es el único punto de la clase que
toca la red -- en vez de urllib, igual que se evita reimplementar la
fontanería HTTP en el resto de tests de este módulo.
"""
from unittest.mock import patch

from atom_core.run_reporter import RunReporter, RUTA_RUNS, TAM_LOTE_GIROS


def _reporter_con_id(run_id: int = 42) -> RunReporter:
    r = RunReporter(secreto="x")
    r._id = run_id
    return r


def test_giros_manda_post_al_endpoint_correcto_con_el_payload_correcto():
    r = _reporter_con_id(42)
    lista = [
        {"vuelo": "PB3_V2", "grados": 270, "imagenes": 442, "modo": "auto", "detalle": None},
        {"vuelo": "PB1_V2", "grados": 0, "imagenes": 980, "modo": "auto", "detalle": None},
    ]
    with patch.object(RunReporter, "_peticion", return_value={}) as mock_peticion:
        r.giros(lista)

    mock_peticion.assert_called_once_with(
        "POST", f"{RUTA_RUNS}/42/giros", {"giros": lista}
    )


def test_giros_sin_run_id_no_manda_nada():
    r = RunReporter(secreto="x")  # sin iniciar: self._id es None
    with patch.object(RunReporter, "_peticion") as mock_peticion:
        r.giros([{"vuelo": "PB1_V2", "grados": 90, "imagenes": 10,
                   "modo": "auto", "detalle": None}])
    mock_peticion.assert_not_called()


def test_giros_lista_vacia_no_manda_nada():
    r = _reporter_con_id(42)
    with patch.object(RunReporter, "_peticion") as mock_peticion:
        r.giros([])
    mock_peticion.assert_not_called()


def test_giros_trocea_en_lotes():
    r = _reporter_con_id(7)
    lista = [
        {"vuelo": f"V{i}", "grados": 0, "imagenes": 1, "modo": "auto", "detalle": None}
        for i in range(TAM_LOTE_GIROS + 5)
    ]
    with patch.object(RunReporter, "_peticion", return_value={}) as mock_peticion:
        r.giros(lista)

    assert mock_peticion.call_count == 2
    primer_lote = mock_peticion.call_args_list[0].args[2]["giros"]
    segundo_lote = mock_peticion.call_args_list[1].args[2]["giros"]
    assert len(primer_lote) == TAM_LOTE_GIROS
    assert len(segundo_lote) == 5


def test_giros_no_aborta_si_peticion_lanza_excepcion():
    r = _reporter_con_id(42)
    with patch.object(RunReporter, "_peticion", side_effect=RuntimeError("sin red")):
        r.giros([{"vuelo": "PB1_V2", "grados": 90, "imagenes": 10,
                   "modo": "auto", "detalle": None}])
    # No debe lanzar: fail-open. Si llegamos aquí, el test pasa.
