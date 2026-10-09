"""Guard contra térmicas sin girar apartadas a SIN_ORDENAR (incidente WILLKA
2026-10-07, 2.015 T): fase T incompleta, cierre y barrido tienen que dar ERROR,
nunca aviso ni `return` mudo."""
import logging
import os
from types import SimpleNamespace

import pytest

from atom_core import apply, cierre
from atom_core import esperado as esperado_mod
from atom_core.manifiesto import FilaManifiesto, Manifiesto, NOMBRE_CARPETA_MANIFIESTO
from atom_core.phases import PipelinePhasesMixin


class _Cb:
    def __init__(self):
        self.mensajes = []

    def emit(self, valor=None, *a, **k):
        self.mensajes.append(valor)


def _fila_t(destino, origen, vuelo="V01", unassigned=False):
    nombre = os.path.basename(origen)
    return FilaManifiesto(
        ruta_origen=origen, tipo="TERMICA", timestamp_exif="2026-05-01T10:00:00",
        modelo="M3T", pb="PB1", vuelo=vuelo, nombre_nuevo=nombre, angulo_giro=90,
        pct_recorte=None, comprime=False,
        ruta_salida_original=str(destino / "PB1_V01" / "TERMICA" / nombre),
        ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=unassigned)


def _manifiesto(ruta, filas):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    m = Manifiesto(ruta)
    m.crear_esquema()
    m.insertar_muchas(filas)
    return m


def test_fase_t_incompleta_es_error_bloqueante(tmp_path, monkeypatch):
    """Una térmica con vuelo que sale de la fase sin procesar (return mudo en
    `_procesar_una`) debe lanzar RuntimeError y emitir ERROR."""
    class _Pool:
        fatal = None

        def __init__(self, *a):
            pass

        def copiar(self, src, dst):
            pass

        def cerrar(self):
            pass

    def _no_procesa(*a, **k):
        raise apply.ExiftoolNoDisponible("simulado")  # `_procesar_una` hace return mudo

    monkeypatch.setattr(apply, "_PoolExiftool", _Pool)
    monkeypatch.setattr(apply, "_convertir_una_termica", _no_procesa)
    m = _manifiesto(tmp_path / "m.db", [_fila_t(tmp_path, "/origen/DJI_0001_T.JPG")])
    cb = _Cb()
    with pytest.raises(RuntimeError, match="Fase térmica incompleta"):
        apply._aplicar_termicas_impl(m, SimpleNamespace(), SimpleNamespace(), cb, _Cb(), _Cb())
    assert any(str(x).startswith("ERROR: ") for x in cb.mensajes)
    m.cerrar()


def test_cierre_termica_pendiente_con_vuelo_es_error(tmp_path):
    m = _manifiesto(tmp_path / "m.db", [_fila_t(tmp_path, "/origen/DJI_0001_T.JPG")])
    cfg = SimpleNamespace(output_folder=str(tmp_path), convert_to_tif=False, cropping_rgb=False)
    errores, _avisos = cierre.separar_problemas(cierre.verificar(m, cfg))
    assert any("térmica" in e and "fase T" in e for e in errores)
    m.cerrar()


def test_cierre_termica_pendiente_sin_vuelo_no_es_error(tmp_path):
    m = _manifiesto(tmp_path / "m.db",
                    [_fila_t(tmp_path, "/origen/DJI_0001_T.JPG", vuelo=None, unassigned=True)])
    cfg = SimpleNamespace(output_folder=str(tmp_path), convert_to_tif=False, cropping_rgb=False)
    errores, avisos = cierre.separar_problemas(cierre.verificar(m, cfg))
    assert errores == [] and avisos


def _fake_phases(tmp_path):
    class _Utils:
        def get_images_from_dir(self, carpeta):
            return sorted(os.listdir(carpeta)) if os.path.isdir(carpeta) else []

    class _Struct:
        llamado = False

        def checking_results_gen_struct_folder(self, *a):
            _Struct.llamado = True
            return 0, 0

    fake = SimpleNamespace(
        utils_obj=_Utils(), gen_struct_folder_obj=_Struct(),
        organizer_logger_obj=SimpleNamespace(logger=logging.getLogger("t")))
    fake._exigir_sin_vuelo_asignado_en_raiz = (
        lambda cfg, cb: PipelinePhasesMixin._exigir_sin_vuelo_asignado_en_raiz(fake, cfg, cb))
    return fake, _Struct


def test_barrido_no_aparta_ficheros_con_vuelo_asignado(tmp_path):
    raiz = tmp_path / "destino"
    (raiz / "TERMICA").mkdir(parents=True)
    (raiz / "TERMICA" / "DJI_0001_T.JPG").write_bytes(b"x")
    (raiz / "TERMICA" / "DJI_0002_T.JPG").write_bytes(b"x")
    _manifiesto(raiz / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db",
                [_fila_t(raiz, "/origen/DJI_0001_T.JPG")]).cerrar()
    fake, struct = _fake_phases(tmp_path)
    cfg = SimpleNamespace(output_folder=str(raiz))
    cb = _Cb()
    with pytest.raises(ValueError, match="DJI_0001_T.JPG"):
        PipelinePhasesMixin._barrido_sin_ordenar(fake, cfg, cb)
    assert not struct.llamado
    assert not (raiz / "SIN_ORDENAR").exists()
    assert any(str(x).startswith("ERROR: ") for x in cb.mensajes)


def test_barrido_sin_asignados_sigue_funcionando(tmp_path):
    raiz = tmp_path / "destino"
    (raiz / "TERMICA").mkdir(parents=True)
    (raiz / "TERMICA" / "DJI_0002_T.JPG").write_bytes(b"x")
    _manifiesto(raiz / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db",
                [_fila_t(raiz, "/origen/DJI_0001_T.JPG")]).cerrar()
    fake, _ = _fake_phases(tmp_path)
    fake._exigir_sin_vuelo_asignado_en_raiz(SimpleNamespace(output_folder=str(raiz)), _Cb())


def test_barrido_foto_en_sin_ordenar_es_error_con_ejemplos(tmp_path):
    """Decisión 2026-10-09: cualquier foto apartada a SIN_ORDENAR hace fallar la tanda."""
    raiz = tmp_path / "destino"
    raiz.mkdir()
    # Fuente del guard (sin manifiesto ni esperado ahora es error): esperado vacío.
    esperado_mod.escribir_esperado(str(raiz), 0, [])
    fake, struct = _fake_phases(tmp_path)
    fake.gen_struct_folder_obj.checking_results_gen_struct_folder = lambda *a: (0, 3)
    fake.gen_struct_folder_obj.ejemplos_sin_ordenar = [str(raiz / "RGB" / "X_W.JPG")]
    # Quedan imágenes en RGB para no disparar el guard "todo fuera".
    fake.utils_obj = SimpleNamespace(
        get_images_from_dir=lambda d: [], contar_imagenes_or_tmc=lambda d, **k: 5)
    cb = _Cb()
    with pytest.raises(ValueError, match=r"3 foto\(s\).*X_W.JPG"):
        PipelinePhasesMixin._barrido_sin_ordenar(fake, SimpleNamespace(output_folder=str(raiz)), cb)
    assert any(str(x).startswith("ERROR: ") for x in cb.mensajes)
