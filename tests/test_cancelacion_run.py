"""Botón «Cancelar» del run: cancelación cooperativa (`atom_core.cancelacion`).

Sujeta: (a) el índice con un lector bloqueado termina rápido al cancelar;
(b) un apply cancelado a mitad deja las filas completas en 'hecho', ningún
parcial en destino y el relanzamiento completa el resto sin duplicar;
(c) el origen no se toca (sha256); y el estado final `cancelled` de
`organize.run_task` (ni error ni éxito)."""
import datetime as dt
import hashlib
import os
import threading
import time

import pytest

from atom_core import cancelacion, indice, organize
from atom_core import apply
from atom_core.indice import construir_indice
from atom_core.manifiesto import Manifiesto
from utils import RenameImagesConfig

from .test_apply_rgb import (
    _SignalFalsa, _cfg as _cfg_rgb, _fila_manifiesto, pipeline_real)
from .test_indice_organizado import (
    _PipelineDePrueba, _Signal, _cfg, _crear_imagen, _escribir_estadillo,
    _ExifDePrueba, _manifiesto)
from .test_tandas_mismo_destino import V1, VENTANAS
from .test_organizado_plan_apply_e2e import (
    _HostDePrueba, _cfg as _cfg_e2e, _fake_convert_dji_image_to_tif,
    _fake_run_exif_batch_local, _inspeccion)  # noqa: F401 — fixture


@pytest.fixture(autouse=True)
def _limpia_cancelacion():
    cancelacion.limpiar()
    yield
    cancelacion.limpiar()


def _sha_arbol(carpeta):
    out = {}
    for raiz, _d, ficheros in os.walk(carpeta):
        for f in ficheros:
            p = os.path.join(raiz, f)
            with open(p, "rb") as fh:
                out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


def _ficheros(carpeta):
    return sorted(
        os.path.join(r, f) for r, _d, fs in os.walk(carpeta) for f in fs)


# (a) índice -----------------------------------------------------------------

def test_indice_con_lector_bloqueado_cancela_rapido(tmp_path, monkeypatch):
    _escribir_estadillo(tmp_path / "estadillo.csv", [("1", "1", *V1)])
    cfg = _cfg(tmp_path)
    rutas = [_crear_imagen(cfg.input_folder, f"DJI_{i:04d}_D.JPG") for i in range(20)]
    exif = _ExifDePrueba(timestamps={r: dt.datetime(2024, 6, 1, 10, 5, 0) for r in rutas})
    sha_antes = _sha_arbol(cfg.input_folder)

    liberar = threading.Event()
    original = indice._leer_metadatos

    def falsa(ruta, exif_, cb):
        if ruta == rutas[5]:
            liberar.wait(30)  # atascado "para siempre"
        return original(ruta, exif_, cb)

    monkeypatch.setattr(indice, "_leer_metadatos", falsa)
    m = _manifiesto(tmp_path)
    threading.Timer(0.4, cancelacion.solicitar).start()
    t0 = time.monotonic()
    try:
        with pytest.raises(cancelacion.RunCancelado):
            construir_indice(
                cfg, _PipelineDePrueba(VENTANAS), exif, m, _Signal(), _Signal(), _Signal(),
                max_hilos=4, timeout_s=60, timeout_reintento_s=60, watchdog_s=60,
                intervalo_progreso_s=0.05)
    finally:
        liberar.set()
    assert time.monotonic() - t0 < 5
    assert m.todas() == []            # nada a medias en el manifiesto
    assert _sha_arbol(cfg.input_folder) == sha_antes   # (c) origen intacto
    m.cerrar()


# (b)+(c) apply --------------------------------------------------------------

def test_apply_cancelado_a_mitad_conserva_hechas_sin_parciales_y_relanza(tmp_path, make_dji_jpeg, monkeypatch):
    origen = tmp_path / "origen"
    origen.mkdir()
    salida = tmp_path / "salida"
    filas = []
    for i in range(6):
        f = origen / f"DJI_{i:04d}.JPG"
        make_dji_jpeg(str(f))
        filas.append(_fila_manifiesto(f, salida / f"DJI_{i:04d}.JPG"))
    sha_antes = _sha_arbol(str(origen))

    m = Manifiesto(tmp_path / "m.db")
    m.crear_esquema()
    m.insertar_muchas(filas)

    original = apply._escribir_salidas_de_fila
    llamadas = {"n": 0}

    def con_cancel(fila, cfg, pipeline_mod):
        r = original(fila, cfg, pipeline_mod)
        llamadas["n"] += 1
        if llamadas["n"] == 3:
            cancelacion.solicitar()  # se pulsa Cancelar durante la 3ª fila
        return r

    monkeypatch.setattr(apply, "_escribir_salidas_de_fila", con_cancel)
    with pytest.raises(cancelacion.RunCancelado):
        apply.aplicar_rgb(m, _cfg_rgb(), pipeline_real,
                          _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    resumen = m.resumen()
    assert resumen["hecho"] == 3 and resumen["pendiente"] == 3
    en_destino = _ficheros(str(salida))
    assert len(en_destino) == 3
    assert not [f for f in en_destino if "parcial" in f.lower() or f.endswith(".tmp")]
    # Todo lo marcado 'hecho' existe en destino (y solo eso).
    hechas = {os.path.basename(r["ruta_salida_original"])
              for r in m.todas() if r["estado"] == "hecho"}
    assert hechas == {os.path.basename(f) for f in en_destino}
    mtimes = {f: os.stat(f).st_mtime_ns for f in en_destino}
    assert _sha_arbol(str(origen)) == sha_antes   # (c)

    # Relanzar: completa el resto sin rehacer ni duplicar lo hecho.
    cancelacion.limpiar()
    monkeypatch.setattr(apply, "_escribir_salidas_de_fila", original)
    res2 = apply.aplicar_rgb(m, _cfg_rgb(), pipeline_real,
                             _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
    assert res2 == {"hecho": 3, "fallido": 0}
    assert len(_ficheros(str(salida))) == 6
    for f, mt in mtimes.items():
        assert os.stat(f).st_mtime_ns == mt
    assert _sha_arbol(str(origen)) == sha_antes
    m.cerrar()


def test_organizar_cancelado_entre_fases_no_cierra_y_relanza_sin_reconvertir(
        _inspeccion, logger, monkeypatch):
    from atom_core import phases as phases_mod
    cfg = _cfg_e2e(_inspeccion)
    sha_antes = _sha_arbol(cfg.input_folder)

    host = _HostDePrueba(logger)
    convertidas = []

    def conv(input_folder, output_folder, image_name, *a, **k):
        convertidas.append(image_name)
        return _fake_convert_dji_image_to_tif(input_folder, output_folder, image_name, *a, **k)

    monkeypatch.setattr(host.split_images_obj, "convert_dji_image_to_tif", conv)
    monkeypatch.setattr(host.split_images_obj, "_run_exif_batch_local", _fake_run_exif_batch_local)

    real_termicas = phases_mod.aplicar_termicas

    def termicas_tras_cancelar(*a, **k):
        cancelacion.solicitar()  # el usuario cancela al acabar el RGB
        return real_termicas(*a, **k)

    monkeypatch.setattr(phases_mod, "aplicar_termicas", termicas_tras_cancelar)
    with pytest.raises(cancelacion.RunCancelado) as info:
        host.organizar_plan_apply(cfg, _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    assert info.value.hechas == 3 and info.value.total == 5   # 3 RGB hechas de 5
    assert convertidas == []
    ficheros = _ficheros(cfg.output_folder)
    assert not [f for f in ficheros if "parcial" in f.lower()]
    assert not os.path.exists(os.path.join(cfg.output_folder, "CSVs", "_criterio"))  # sin cierre
    assert _sha_arbol(cfg.input_folder) == sha_antes

    # Relanzar con host nuevo: solo convierte las 2 térmicas pendientes.
    cancelacion.limpiar()
    monkeypatch.setattr(phases_mod, "aplicar_termicas", real_termicas)
    host2 = _HostDePrueba(logger)
    convertidas2 = []

    def conv2(input_folder, output_folder, image_name, *a, **k):
        convertidas2.append(image_name)
        return _fake_convert_dji_image_to_tif(input_folder, output_folder, image_name, *a, **k)

    monkeypatch.setattr(host2.split_images_obj, "convert_dji_image_to_tif", conv2)
    monkeypatch.setattr(host2.split_images_obj, "_run_exif_batch_local", _fake_run_exif_batch_local)
    host2.organizar_plan_apply(cfg, _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
    assert sorted(convertidas2) == ["DJI_0001_T.JPG", "DJI_0002_T.JPG"]
    assert len(os.listdir(os.path.join(cfg.output_folder, "RGB", "PB1", "PB1_V1"))) == 2
    assert len(os.listdir(os.path.join(cfg.output_folder, "TERMICA", "PB1", "PB1_V1"))) == 4
    assert _sha_arbol(cfg.input_folder) == sha_antes


# Estado final ---------------------------------------------------------------

def test_run_task_cancelado_emite_done_cancelled_sin_error(monkeypatch):
    class _Host:
        def correr(self, cfg, pcb, pbar, psum):
            exc = cancelacion.RunCancelado()
            exc.hechas, exc.total = 7, 20
            raise exc

    monkeypatch.setitem(organize._TASKS, "cancel_test", ("correr", RenameImagesConfig))
    monkeypatch.setattr(organize, "HeadlessHost", lambda: _Host())
    eventos = []
    organize.run_task("cancel_test", {}, lambda k, p=None: eventos.append((k, p)))

    kinds = [k for k, _ in eventos]
    assert "error" not in kinds
    done = [p for k, p in eventos if k == "done"]
    assert len(done) == 1
    assert done[0]["status"] == "cancelled" and done[0]["hechas"] == 7 and done[0]["total_filas"] == 20
    textos = " ".join(str(p) for k, p in eventos if k in ("log", "summary"))
    assert "Cancelado por el usuario: 7/20" in textos


def test_run_cancelado_no_la_traga_except_exception():
    assert not issubclass(cancelacion.RunCancelado, Exception)
    with pytest.raises(cancelacion.RunCancelado):
        try:
            raise cancelacion.RunCancelado()
        except Exception:  # noqa: BLE001
            pytest.fail("except Exception no debe capturar RunCancelado")
