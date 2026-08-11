"""El modo de destino tiene que llegar entero desde el flag del CLI hasta el
`safe_move` que mueve cada imagen. Es una cadena de 5 saltos (CLI -> params ->
dataclass -> host/fase -> objeto de pipeline) y cualquiera de ellos que se
olvide deja el flag silenciosamente sin efecto: el usuario pide `obviar`, la
UI lo muestra, y el pipeline sigue duplicando. Por eso se testea la cadena, no
solo los extremos."""
import os

import pytest

import utils
from atom_core import organize


def test_split_config_por_defecto_sobrescribe():
    cfg = organize._default_split_config({"origen": "/in", "destino": "/out", "estadillo": "/e.csv"})
    assert cfg.modo_destino == utils.MODO_SOBRESCRIBIR


def test_split_config_respeta_modo_obviar():
    cfg = organize._default_split_config(
        {"origen": "/in", "destino": "/out", "estadillo": "/e.csv", "modo_destino": utils.MODO_OBVIAR})
    assert cfg.modo_destino == utils.MODO_OBVIAR


def test_split_config_rechaza_modo_invalido():
    with pytest.raises(ValueError):
        organize._default_split_config(
            {"origen": "/in", "destino": "/out", "modo_destino": "borrar_todo"})


# NOTA de adaptacion respecto al brief: `organize_cli.main` valida que
# `--origen` (y el estadillo, si se pasa) existan en disco ANTES de construir
# `params` y llamar a `run_task` (organize_cli.py ~221-226); con las rutas
# ficticias `/in`/`/e.csv` del brief corta ahi con `return 2` y `run_task` ni
# se invoca, así que `capturado` queda vacío. Se usan `tmp_path` reales para
# que el CLI llegue a construir `params`; el aserto sigue siendo exactamente
# el que pedia el brief: `params["modo_destino"]`.
def test_cli_mete_el_modo_en_params(monkeypatch, tmp_path):
    import organize_cli
    capturado = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        capturado.update(params)
        return {"status": "ok"}

    # `organize_cli.main` importa run_task de forma perezosa DESDE atom_core.organize
    # (organize_cli.py:232), asi que el doble hay que ponerlo en el modulo de origen.
    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    origen = tmp_path / "in"
    origen.mkdir()
    destino = tmp_path / "out"
    estadillo = tmp_path / "e.csv"
    estadillo.write_text("")

    organize_cli.main([
        "--origen", str(origen), "--destino", str(destino), "--estadillo", str(estadillo),
        "--modo-destino", "obviar", "--quiet", "--json",
    ])

    assert capturado.get("modo_destino") == utils.MODO_OBVIAR


def test_cli_por_defecto_sobrescribe(monkeypatch, tmp_path):
    import organize_cli
    capturado = {}

    def _fake_run_task(task, params, emit, avanzado=None):
        capturado.update(params)
        return {"status": "ok"}

    monkeypatch.setattr(organize, "run_task", _fake_run_task, raising=False)
    monkeypatch.setattr(organize_cli, "run_task", _fake_run_task, raising=False)

    origen = tmp_path / "in"
    origen.mkdir()
    destino = tmp_path / "out"

    organize_cli.main(["--origen", str(origen), "--destino", str(destino), "--quiet", "--json"])

    assert capturado.get("modo_destino") == utils.MODO_SOBRESCRIBIR


def test_gen_struct_folder_nace_sobrescribiendo():
    import pipeline

    class _LogFalso:
        class logger:
            @staticmethod
            def info(*a, **k):
                pass

    obj = pipeline.GenStructFolder(_LogFalso())
    assert obj.modo_destino == utils.MODO_SOBRESCRIBIR
    assert obj.skipped_image_number == 0


# --- el modo llega a TODAS las etapas, no solo a struct ----------------------
#
# Cada `--etapa` es un proceso nuevo de Cloud Run con su propio GenStructFolder
# recien construido (default `sobrescribir`). La etapa `post` no ejecuta el
# bloque de struct, pero SI corre el barrido de sobrantes, que mueve imagenes
# usando `self.modo_destino`. Si la asignacion viviera dentro del bloque de
# struct, un `--etapa post --modo-destino obviar` (relanzar tras un fallo
# parcial: justo el caso de uso de `obviar`) sobrescribiria en silencio.

@pytest.mark.parametrize("etapa", ["todo", "split", "struct", "post"])
def test_el_modo_destino_llega_al_gen_struct_en_cualquier_etapa(etapa, monkeypatch):
    from tests.test_etapas_pipeline import _HostDePrueba, _SignalFalsa, _cfg

    host = _HostDePrueba(etapa=etapa)
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(_cfg(modo_destino=utils.MODO_OBVIAR),
                      _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    assert host.gen_struct_folder_obj.modo_destino == utils.MODO_OBVIAR, (
        f"etapa={etapa}: el flag --modo-destino se ha ignorado en silencio")


def test_sin_modo_en_la_config_la_etapa_post_sobrescribe(monkeypatch):
    """El default explicito: sin flag, `post` se comporta como siempre."""
    from tests.test_etapas_pipeline import _HostDePrueba, _SignalFalsa, _cfg

    host = _HostDePrueba(etapa="post")
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(_cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    assert host.gen_struct_folder_obj.modo_destino == utils.MODO_SOBRESCRIBIR


def test_get_summarize_avisa_de_omitidas_sin_marcarlo_como_error():
    import pipeline

    class _LogFalso:
        class logger:
            @staticmethod
            def info(*a, **k):
                pass

    obj = pipeline.GenStructFolder(_LogFalso())
    obj.total_images_number = 100
    obj.current_image_number = 100
    obj.skipped_image_number = 12

    resumen = obj.get_summarize()

    assert "ERROR" not in resumen, "omitir no es fallar: no puede teñir el run de rojo"
    assert resumen.get("AVISO") == "HA HABIDO AVISOS"
    assert "12" in str(resumen.get("Imagenes omitidas", ""))


def test_get_summarize_sin_omitidas_no_inventa_aviso():
    import pipeline

    class _LogFalso:
        class logger:
            @staticmethod
            def info(*a, **k):
                pass

    obj = pipeline.GenStructFolder(_LogFalso())
    obj.total_images_number = 100
    obj.current_image_number = 100
    obj.skipped_image_number = 0

    resumen = obj.get_summarize()

    assert "AVISO" not in resumen
    assert resumen.get("Sin Errores") == "Sin errores durante el proceso"


def test_el_total_de_struct_solo_cuenta_la_entrada_de_la_fase(tmp_path):
    """Un destino con organizacion previa (PB1/) no puede inflar el total
    esperado: esas imagenes ya estan en su sitio y struct no las va a mover."""
    from utils import Utils

    class _LogFalso:
        class logger:
            @staticmethod
            def info(*a, **k):
                pass

    (tmp_path / "TERMICA").mkdir()
    (tmp_path / "RGB").mkdir()
    (tmp_path / "PB1" / "PB1_V1").mkdir(parents=True)
    for i in range(3):
        (tmp_path / "TERMICA" / f"t{i}.JPG").write_bytes(b"x")
    for i in range(2):
        (tmp_path / "RGB" / f"r{i}.JPG").write_bytes(b"x")
    for i in range(50):
        (tmp_path / "PB1" / "PB1_V1" / f"viejo{i}.JPG").write_bytes(b"x")

    utils_obj = Utils(_LogFalso())
    total = utils_obj.contar_imagenes_or_tmc(str(tmp_path / "TERMICA")) \
        + utils_obj.contar_imagenes_or_tmc(str(tmp_path / "RGB"))

    assert total == 5, "solo TERMICA + RGB; las 50 ya organizadas no cuentan"


def test_el_total_de_struct_incluye_rgb_extra_si_hay_sufijo_extra(monkeypatch):
    """Con sufijo RGB extra, struct MUEVE tambien RGB_extra (pipeline.py:1158) y
    cada movimiento suma a current_image_number. Si el total no la cuenta, el run
    sale en rojo por descuadre — el mismo falso error, por la puerta de atras."""
    from tests.test_etapas_pipeline import _HostDePrueba, _SignalFalsa, _UtilsFalso, _cfg

    _UtilsFalso.filtros_recibidos = []
    host = _HostDePrueba(etapa="struct")
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(_cfg(end_rgb_extra_files="_E", end_rgb_files="_V"),
                      _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    contadas = {os.path.basename(c) for c, _f in _UtilsFalso.filtros_recibidos}
    assert {"TERMICA", "RGB", "RGB_extra"} <= contadas, contadas


def test_sin_sufijo_extra_el_total_no_cuenta_rgb_extra(monkeypatch):
    """Sin sufijo extra nadie mueve RGB_extra: contarla inflaria el total."""
    from tests.test_etapas_pipeline import _HostDePrueba, _SignalFalsa, _UtilsFalso, _cfg

    _UtilsFalso.filtros_recibidos = []
    host = _HostDePrueba(etapa="struct")
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(_cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    contadas = {os.path.basename(c) for c, _f in _UtilsFalso.filtros_recibidos}
    assert "RGB_extra" not in contadas, contadas


def test_el_total_de_struct_no_baja_al_arbol_del_destino(monkeypatch):
    """La entrada de struct es el NIVEL SUPERIOR de TERMICA/RGB. Con `obviar` el
    destino conserva los `TERMICA/PBx/PBx_Vy/` de la pasada anterior; contarlos
    mataba las ocho tareas con "No hay correspondencia entre numero inicial 715 y
    final de imagenes 283" (op 9 de la inspeccion 330)."""
    from tests.test_etapas_pipeline import _HostDePrueba, _SignalFalsa, _UtilsFalso, _cfg

    _UtilsFalso.recursivos_recibidos = []
    host = _HostDePrueba(etapa="struct")
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(_cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    recursivos = {os.path.basename(c): r for c, r in _UtilsFalso.recursivos_recibidos}
    assert recursivos.get("TERMICA") is False, recursivos
    assert recursivos.get("RGB") is False, recursivos


def test_el_barrido_a_sin_ordenar_no_deja_la_imagen_en_dos_sitios(tmp_path):
    """Fallo 2 de la op 9: con `obviar` y la copia ya en SIN_ORDENAR, `safe_move`
    devolvia None y el origen se quedaba varado en la raiz. 62 imagenes acabaron
    en las dos ubicaciones, y en silencio: el contador las suma igual."""
    import pipeline

    class _LogFalso:
        class logger:
            @staticmethod
            def info(*a, **k):
                pass

            @staticmethod
            def warning(*a, **k):
                pass

    for sub in ("TERMICA", "RGB"):
        (tmp_path / sub).mkdir()
        (tmp_path / "SIN_ORDENAR" / sub).mkdir(parents=True)
        # La huerfana ya se aparto en la pasada anterior y vuelve a estar en la raiz.
        (tmp_path / sub / "huerfana.JPG").write_bytes(b"copia de la raiz")
        (tmp_path / "SIN_ORDENAR" / sub / "huerfana.JPG").write_bytes(b"ya aparcada")

    obj = pipeline.GenStructFolder(_LogFalso())
    obj.modo_destino = utils.MODO_OBVIAR

    class _Emisor:
        @staticmethod
        def emit(*a, **k):
            pass

    termicas, rgbs = obj.checking_results_gen_struct_folder(str(tmp_path), _Emisor())

    assert (termicas, rgbs) == (1, 1)
    for sub in ("TERMICA", "RGB"):
        assert not (tmp_path / sub / "huerfana.JPG").exists(), \
            f"{sub}: el origen se quedo varado en la raiz -> imagen duplicada"
        assert (tmp_path / "SIN_ORDENAR" / sub / "huerfana.JPG").read_bytes() == b"ya aparcada", \
            f"{sub}: la copia ya aparcada NO se pisa"
