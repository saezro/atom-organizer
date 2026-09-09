"""Traducción de los marcadores de texto a `emit("stats", ...)` y duración
por fase en `payload_done["fases"]` (`atom_core.organize.run_task`).

`construir_indice`/`aplicar_rgb`/`aplicar_termicas` no tienen acceso al
`emit` real de `run_task` — solo a las tres señales de siempre
(`progress_callback`/`progress_bar`/`progress_summarize`) — así que viajan
como una línea de texto con un prefijo reconocible (`STATS_INDICE_PREFIX` /
`STATS_APPLY_PREFIX`) que `run_task` intercepta y reenvía como
`emit("stats", payload)`. Estos tests sujetan esa traducción, y que
`payload_done["fases"]` acumula la duración de CADA fase en orden (ver
LEDGER-metricas-progreso.md).

No se ejecuta el pipeline real (evita `HeadlessHost`, que carga config y
loggers reales): se sustituye `atom_core.organize.HeadlessHost` por un doble
mínimo cuyo único "método de tarea" emite las fases y los marcadores que
cada test necesita. Doble a mano, nunca `unittest.mock` — estilo de la casa.
"""
import json

from atom_core import organize
from atom_core.apply import STATS_APPLY_PREFIX
from atom_core.indice import STATS_INDICE_PREFIX
from utils import RenameImagesConfig


def _emisor():
    eventos = []

    def emit(kind, payload=None):
        eventos.append((kind, payload))
    return eventos, emit


def _de_tipo(eventos, kind):
    return [p for k, p in eventos if k == kind]


class _HostFalso:
    """Doble de `HeadlessHost`: no construye NINGÚN objeto de negocio real
    (logger, config, pipeline de dron...), solo expone el método que
    `_TASKS["stats_test"]` apunte, con la firma `(cfg, pcb, pbar, psum)` que
    exige `run_task`."""

    def __init__(self, acciones):
        self._acciones = acciones

    def correr(self, cfg, pcb, pbar, psum):
        for accion in self._acciones:
            accion(pcb, pbar, psum)


def _run_con_acciones(monkeypatch, acciones):
    monkeypatch.setitem(organize._TASKS, "stats_test", ("correr", RenameImagesConfig))
    monkeypatch.setattr(organize, "HeadlessHost", lambda: _HostFalso(acciones))
    eventos, emit = _emisor()
    organize.run_task("stats_test", {}, emit)
    return eventos


def test_marcador_de_indice_se_traduce_a_stats_y_no_ensucia_el_summary(monkeypatch):
    payload_indice = {
        "fase": "Índice", "total": 10, "rgb": 6, "termica": 2, "rgb_extra": 1,
        "sin_asignar": 1, "sin_timestamp": 0, "vuelos": 2,
    }

    def _emitir(pcb, pbar, psum):
        psum.emit(STATS_INDICE_PREFIX + json.dumps(payload_indice))

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    stats = _de_tipo(eventos, "stats")
    assert payload_indice in stats, "el marcador del índice debe llegar tal cual como evento stats"
    # El marcador es fontanería interna: no debe colarse en el panel de
    # summary que ve el usuario (sería un JSON crudo en la UI).
    assert not any(STATS_INDICE_PREFIX in str(p) for p in _de_tipo(eventos, "summary"))


def test_marcador_de_apply_se_traduce_a_stats_y_no_ensucia_el_log(monkeypatch):
    payload_apply = {
        "fase": "Imágenes RGB", "done": 5, "total": 20, "rgb": 5, "termica": 0,
        "img_por_segundo": 2.5, "eta_segundos": 6,
    }

    def _emitir(pcb, pbar, psum):
        pcb.emit(STATS_APPLY_PREFIX + json.dumps(payload_apply))

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    stats = _de_tipo(eventos, "stats")
    assert payload_apply in stats
    assert not any(STATS_APPLY_PREFIX in str(p) for p in _de_tipo(eventos, "log"))


def test_entero_cero_por_el_canal_de_log_no_genera_evento_log(monkeypatch):
    """`_texto_de_log` (guard de `_on_log`) debe descartar un `.emit(0)` de
    progreso numérico legacy: no debe colarse como línea "0" en el log
    crudo del modal (regresión, ver docstring de `_texto_de_log`)."""

    def _emitir(pcb, pbar, psum):
        pcb.emit(0)

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    # El run siempre emite además la línea de la sonda de máquina por el
    # canal `log` (ver `test_sonda_inicial_...`): lo que NO debe aparecer es
    # el "0" colado por `.emit(0)`.
    assert "0" not in _de_tipo(eventos, "log")


def test_string_normal_por_el_canal_de_log_si_genera_evento_log(monkeypatch):
    """Contraparte del test anterior: un string normal sí debe pasar el
    guard de `_texto_de_log` y llegar intacto como evento `log`."""

    def _emitir(pcb, pbar, psum):
        pcb.emit("procesando imagen 3 de 10")

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    assert "procesando imagen 3 de 10" in _de_tipo(eventos, "log")


def test_puntos_de_spinner_siguen_contando_tras_el_guard_de_texto_de_log(monkeypatch):
    """Los `"."` que `_on_log` usa como spinner por-imagen deben seguir
    contándose para el "N de M analizadas" del modal (`stats.done`, cada
    `IMAGE_EMIT_EVERY` imágenes): `_texto_de_log` no debe tumbarlos al
    filtrar los no-string."""

    def _emitir(pcb, pbar, psum):
        pcb.emit("...")
        pcb.emit(".......")  # 3 + 7 = 10 == IMAGE_EMIT_EVERY: dispara el snapshot

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    stats = _de_tipo(eventos, "stats")
    assert any(p.get("done") == 10 for p in stats), (
        "3 puntos + 7 puntos = 10 imágenes contadas por el spinner"
    )
    # Los puntos son ruido visual: no deben aparecer como línea de log.
    assert not any(p in (".", "...", ".......") for p in _de_tipo(eventos, "log"))


def test_payload_done_trae_las_fases_en_orden_con_su_duracion(monkeypatch):
    """`payload_done["fases"]` debe listar CADA fase que arrancó, en el
    orden en que arrancaron, con su duración — es lo que el modal usa para
    destacar cuál fue la más lenta."""

    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Fase A")
        psum.emit("---> SUBPROCESO: Fase B")

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    dones = _de_tipo(eventos, "done")
    assert len(dones) == 1
    fases = dones[0]["fases"]
    nombres = [f["nombre"] for f in fases]
    assert nombres == ["Fase A", "Fase B"]
    for fase in fases:
        assert isinstance(fase["segundos"], float)
        assert fase["segundos"] >= 0.0


def test_task_sin_fases_deja_la_lista_vacia(monkeypatch):
    """Una task sin ningún prefijo `---> SUBPROCESO:` (no pasa por el
    checklist de fases) no debe reventar: `fases` queda vacía en vez de
    faltar la clave."""
    eventos = _run_con_acciones(monkeypatch, [lambda pcb, pbar, psum: None])

    dones = _de_tipo(eventos, "done")
    assert len(dones) == 1
    assert dones[0]["fases"] == []


def test_sonda_inicial_se_emite_una_vez_al_arrancar_el_run(monkeypatch):
    """La sonda de máquina (`atom_core.diagnostico_maquina.sonda_inicial`)
    debe dispararse una única vez al arrancar el run, ANTES de procesar
    ninguna imagen, y su `texto` debe viajar también por el canal `log`."""
    sonda_falsa = {
        "disco_origen": {"tipo": "HDD", "modelo": "WD Blue", "unidad": "E:"},
        "disco_destino": {"tipo": "SSD", "modelo": None, "unidad": "C:"},
        "mismo_disco": False, "nucleos": 8, "ram_total_gb": 16.0,
        "ram_libre_gb": 4.0, "cpu_ocupada_pct": 12.0, "maquina_ocupada": False,
        "texto": "Origen E: HDD (disco mecánico) · Destino C: SSD",
    }
    llamadas = []

    def _sonda_inicial_falsa(origen, destino):
        llamadas.append((origen, destino))
        return sonda_falsa

    monkeypatch.setattr(organize, "sonda_inicial", _sonda_inicial_falsa)

    eventos = _run_con_acciones(monkeypatch, [lambda pcb, pbar, psum: None])

    assert len(llamadas) == 1, "la sonda debe lanzarse UNA sola vez por run"
    maquinas = _de_tipo(eventos, "maquina")
    assert maquinas == [sonda_falsa]
    assert any(sonda_falsa["texto"] in str(p) for p in _de_tipo(eventos, "log"))


def test_stats_incluye_recursos_vivo_cuando_el_medidor_lo_devuelve(monkeypatch):
    """`_emit_stats` debe adjuntar `recursos_vivo` con lo que devuelva
    `medidor.resumen_parcial()` (aquí mockeado) en cada snapshot de `stats`."""
    resumen_falso = {
        "mb_leidos": 10.0, "mb_escritos": 5.0, "mb_por_segundo": 36.4,
        "cpu_pct": 22.0, "nucleos": 8, "veredicto": "disco", "tipo_disco": "HDD",
    }
    monkeypatch.setattr(organize.MedidorRecursos, "resumen_parcial",
                         lambda self, ventana_s=15.0: resumen_falso)

    def _emitir(pcb, pbar, psum):
        psum.emit("Procesando 10 imágenes en el directorio /out/RGB")

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    stats = _de_tipo(eventos, "stats")
    assert stats, "debe haberse emitido al menos un snapshot de stats"
    assert any(p.get("recursos_vivo") == resumen_falso for p in stats)


def test_balance_de_bytes_lee_el_manifiesto_real(tmp_path):
    """Con un manifiesto de verdad en `<salida>/.atom_manifiesto/manifiesto.db`
    y filas ya `hecho`, `_balance_de_bytes` debe devolver el mismo dict que
    `Manifiesto.balance_bytes()` (entrada/salida/imágenes), no un resumen
    aparte reinventado en `organize.py`."""
    from atom_core.manifiesto import Manifiesto, NOMBRE_CARPETA_MANIFIESTO, FilaManifiesto

    carpeta_salida = tmp_path / "salida"
    carpeta_salida.mkdir()
    ruta_db = carpeta_salida / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db"
    ruta_db.parent.mkdir(parents=True)
    manifiesto = Manifiesto(ruta_db)
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        FilaManifiesto(
            ruta_origen="C:\\origen\\a.jpg", tipo="RGB",
            timestamp_exif="2026-05-01T10:00:00", modelo="M3T", pb="PB1",
            vuelo="V01", nombre_nuevo="a.jpg", angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original="C:\\salida\\a.jpg",
            ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=False,
            bytes_origen=1500,
        ),
    ])
    id_fila = manifiesto.todas()[0]["id"]
    manifiesto.marcar_hecha(id_fila, "C:\\salida\\a.jpg:1000; C:\\salida\\b.jpg:500")
    manifiesto.cerrar()

    cfg = RenameImagesConfig(output_folder=str(carpeta_salida))

    assert organize._balance_de_bytes(cfg) == {
        "entrada": 1500, "salida": 1500, "imagenes": 1,
    }


def test_balance_de_bytes_none_sin_manifiesto_o_sin_output_folder(tmp_path):
    """Dos motivos legítimos de ausencia, no un fallo: (1) la task terminó
    pero nunca se creó `manifiesto.db` (motores viejos sin manifiesto), y
    (2) la cfg ni siquiera tiene `output_folder` (tasks que no escriben a
    carpeta de salida). En ambos casos el modal simplemente omite la línea."""
    carpeta_salida = tmp_path / "salida_vacia"
    carpeta_salida.mkdir()
    cfg = RenameImagesConfig(output_folder=str(carpeta_salida))
    assert organize._balance_de_bytes(cfg) is None

    class _CfgSinOutputFolder:
        pass

    assert organize._balance_de_bytes(_CfgSinOutputFolder()) is None


def test_balance_de_bytes_none_si_no_hay_filas_hechas(tmp_path):
    """El manifiesto existe pero ninguna fila llegó a `hecho` (todas
    pendientes/en curso/falladas): no hay nada que entregar, así que no hay
    balance que mostrar."""
    from atom_core.manifiesto import Manifiesto, NOMBRE_CARPETA_MANIFIESTO, FilaManifiesto

    carpeta_salida = tmp_path / "salida"
    carpeta_salida.mkdir()
    ruta_db = carpeta_salida / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db"
    ruta_db.parent.mkdir(parents=True)
    manifiesto = Manifiesto(ruta_db)
    manifiesto.crear_esquema()
    manifiesto.insertar_muchas([
        FilaManifiesto(
            ruta_origen="/origen/a.jpg", tipo="RGB",
            timestamp_exif="2026-05-01T10:00:00", modelo="M3T", pb="PB1",
            vuelo="V01", nombre_nuevo="a.jpg", angulo_giro=0, pct_recorte=None,
            comprime=False, ruta_salida_original="/salida/a.jpg",
            ruta_salida_crop=None, ruta_salida_tiff=None, unassigned=False,
            bytes_origen=1500,
        ),
    ])
    # Fila deja en estado "pendiente" a propósito: no se marca hecha.
    manifiesto.cerrar()

    cfg = RenameImagesConfig(output_folder=str(carpeta_salida))

    assert organize._balance_de_bytes(cfg) is None


class TestDerivePlant:
    """Nombre de planta del modal: prioridad inspeccion > estadillo > destino
    (ver comentario en `organize._derive_plant`)."""

    def test_con_inspeccion_gana_sobre_estadillo_y_destino(self):
        params = {
            "inspeccion": "KL19 - Inspección térmica agosto",
            "estadillo": "/vuelos/2026_08_19_estadillo_KL19.csv",
            "destino": "/salida/KL19",
        }
        assert organize._derive_plant(params) == "KL19 - Inspección térmica agosto"

    def test_sin_inspeccion_cae_al_estadillo_sin_extension(self):
        params = {"estadillo": "/vuelos/2026_08_19_estadillo_KL19.csv", "destino": "/salida/KL19"}
        assert organize._derive_plant(params) == "2026_08_19_estadillo_KL19"

    def test_sin_inspeccion_ni_estadillo_cae_al_destino(self):
        params = {"destino": "/salida/KL19"}
        assert organize._derive_plant(params) == "KL19"
