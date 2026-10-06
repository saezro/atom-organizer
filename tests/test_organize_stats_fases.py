import pytest
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
from atom_core.apply import STATS_APPLY_PREFIX, STATS_ERRORES_PREFIX
from atom_core.indice import STATS_INDICE_PREFIX
from atom_core.manifiesto import NOMBRE_CARPETA_MANIFIESTO, Manifiesto
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
            "inspeccion": "PLANTA_C - Inspección térmica agosto",
            "estadillo": "/vuelos/2026_08_19_estadillo_PLANTA_C.csv",
            "destino": "/salida/PLANTA_C",
        }
        assert organize._derive_plant(params) == "PLANTA_C - Inspección térmica agosto"

    def test_sin_inspeccion_cae_al_estadillo_sin_extension(self):
        params = {"estadillo": "/vuelos/2026_08_19_estadillo_PLANTA_C.csv", "destino": "/salida/PLANTA_C"}
        assert organize._derive_plant(params) == "2026_08_19_estadillo_PLANTA_C"

    def test_sin_inspeccion_ni_estadillo_cae_al_destino(self):
        params = {"destino": "/salida/PLANTA_C"}
        assert organize._derive_plant(params) == "PLANTA_C"


class TestTeeLog:
    """`_TeeLog` hace fan-out del log de corrida al fichero de
    `user_log_dir()` y a `<destino>/LOGS/`: un handle roto (disco de red
    caído, destino desmontado) no puede tumbar la corrida ni silenciar el
    otro handle (ver docstring de la clase)."""

    class _HandleFalso:
        def __init__(self, escrituras, nombre="h"):
            self._escrituras = escrituras
            self._nombre = nombre

        def write(self, s):
            self._escrituras.append((self._nombre, "write", s))

        def flush(self):
            self._escrituras.append((self._nombre, "flush", None))

        def close(self):
            self._escrituras.append((self._nombre, "close", None))

    class _HandleQuePeta:
        """Lanza en la operación indicada (`falla_en`) y nunca más responde:
        una vez descartado por `_TeeLog`, no debe volver a recibir nada."""

        def __init__(self, falla_en):
            self._falla_en = falla_en
            self.llamadas = []

        def write(self, s):
            self.llamadas.append(("write", s))
            if self._falla_en == "write":
                raise OSError("disco de red caído")

        def flush(self):
            self.llamadas.append(("flush", None))
            if self._falla_en == "flush":
                raise OSError("disco de red caído")

        def close(self):
            self.llamadas.append(("close", None))
            if self._falla_en == "close":
                raise OSError("disco de red caído")

    def test_write_flush_close_llegan_a_todos_los_handles(self):
        escrituras = []
        h1 = self._HandleFalso(escrituras, "h1")
        h2 = self._HandleFalso(escrituras, "h2")
        tee = organize._TeeLog([h1, h2])

        tee.write("línea 1\n")
        tee.flush()
        tee.close()

        assert ("h1", "write", "línea 1\n") in escrituras
        assert ("h2", "write", "línea 1\n") in escrituras
        assert ("h1", "flush", None) in escrituras
        assert ("h2", "flush", None) in escrituras
        assert ("h1", "close", None) in escrituras
        assert ("h2", "close", None) in escrituras

    def test_handle_roto_en_write_no_propaga_y_el_otro_sigue_recibiendo(self):
        roto = self._HandleQuePeta(falla_en="write")
        escrituras_sanas = []
        sano = self._HandleFalso(escrituras_sanas, "sano")
        tee = organize._TeeLog([roto, sano])

        # No debe propagar la excepción del handle roto.
        tee.write("primera\n")
        assert ("write", "primera\n") in roto.llamadas
        assert ("sano", "write", "primera\n") in escrituras_sanas

        # Tras el fallo, el roto queda descartado: la siguiente escritura NO
        # debe volver a llamarlo, pero el sano sigue recibiendo todo.
        llamadas_antes = list(roto.llamadas)
        tee.write("segunda\n")
        assert roto.llamadas == llamadas_antes, "el handle descartado no debe volver a recibir escrituras"
        assert ("sano", "write", "segunda\n") in escrituras_sanas

    def test_handle_roto_en_flush_no_propaga_y_el_otro_sigue_recibiendo(self):
        roto = self._HandleQuePeta(falla_en="flush")
        escrituras_sanas = []
        sano = self._HandleFalso(escrituras_sanas, "sano")
        tee = organize._TeeLog([roto, sano])

        tee.flush()  # no debe propagar
        assert ("flush", None) in roto.llamadas

        llamadas_antes = list(roto.llamadas)
        tee.write("tras el flush roto\n")
        assert roto.llamadas == llamadas_antes, "el handle descartado en flush no debe recibir el write posterior"
        assert ("sano", "write", "tras el flush roto\n") in escrituras_sanas


class TestGuardCarpetaSalidaSplitImages:
    """Guard de `run_task` (task `split_images`): la carpeta de salida debe
    estar vacía, pero `LOGS/` (la crea este mismo run al abrir el log tee) y
    `.organizado/` (manifiesto de reanudación) no cuentan como residuo.

    Se ejercita `run_task` de verdad (no se replica el filtro a mano):
    `HeadlessHost` se sustituye por un doble mínimo cuyo `organizar_plan_apply`
    no hace nada, así que el resto de la corrida (sonda de máquina, medidor de
    recursos, checklist de fases) transcurre con datos vacíos y lo único que
    se sujeta es si el guard deja pasar o aborta ANTES de llegar ahí."""

    class _HostSplitFalso:
        def organizar_plan_apply(self, cfg, pcb, pbar, psum):
            pass

    def _run_split(self, monkeypatch, destino):
        monkeypatch.setattr(organize, "HeadlessHost", lambda: self._HostSplitFalso())
        eventos, emit = _emisor()
        # Estadillo obligatorio (gate de `run_task` antes de arrancar ninguna
        # fase): con `organizar_plan_apply` mockeado a un no-op, la ruta no
        # necesita existir de verdad, solo estar presente para pasar el gate.
        organize.run_task(
            "split_images", {"destino": str(destino), "estadillo": "/no/existe.csv"}, emit)
        return eventos

    def test_no_aborta_si_el_destino_solo_tiene_logs_y_manifiesto(self, monkeypatch, tmp_path):
        destino = tmp_path / "salida"
        destino.mkdir()
        (destino / NOMBRE_CARPETA_MANIFIESTO).mkdir()

        eventos = self._run_split(monkeypatch, destino)

        errores = _de_tipo(eventos, "error")
        assert not any("no está vacía" in str(e) for e in errores), (
            f"el guard no debe abortar con solo LOGS/.organizado de residuo, errores={errores}"
        )
        assert _de_tipo(eventos, "done"), "la corrida debe llegar a terminar (done) si el guard no aborta"

    def test_aborta_si_el_destino_tiene_otro_residuo(self, monkeypatch, tmp_path):
        destino = tmp_path / "salida"
        destino.mkdir()
        (destino / "PB1").mkdir()

        eventos = self._run_split(monkeypatch, destino)

        errores = _de_tipo(eventos, "error")
        assert any("no está vacía" in str(e) for e in errores), (
            "el guard debe abortar cuando hay un residuo real (PB1) en el destino"
        )
        assert not _de_tipo(eventos, "done"), "el guard debe cortar ANTES de llegar a done"

    def test_no_aborta_si_hay_residuo_pero_existe_manifiesto(self, monkeypatch, tmp_path):
        """Destino ya organizado por un cachito anterior: se acumula."""
        destino = tmp_path / "salida"
        (destino / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True)
        _m = Manifiesto(destino / NOMBRE_CARPETA_MANIFIESTO / "manifiesto.db")
        _m.crear_esquema()
        _m.cerrar()
        (destino / "RGB").mkdir()

        eventos = self._run_split(monkeypatch, destino)

        assert not any("no está vacía" in str(e) for e in _de_tipo(eventos, "error"))
        assert _de_tipo(eventos, "done")


def _marcador(fase, n_fallos, n_total, n_hechas=None, csv=None):
    return STATS_ERRORES_PREFIX + json.dumps({
        "fase": fase, "n_fallos": n_fallos, "n_total": n_total,
        "n_hechas": n_total - n_fallos if n_hechas is None else n_hechas,
        "rutas": ["a.jpg"] * min(n_fallos, 1), "rutas_truncadas": False, "csv": csv})


def test_marcador_de_errores_marca_la_fase_en_rojo_y_el_run_en_errors(monkeypatch):
    """Sin ningún texto 'Ha habido N error', el marcador estructurado basta:
    la fase sale con errors>0 (+ n_fallos/n_total/csv) y el run con 'errors'."""

    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Imágenes RGB")
        pcb.emit(_marcador("Imágenes RGB", 341, 1000, csv="X/LOGS/ERRORES_RGB.csv"))
        psum.emit("---> SUBPROCESO: Cierre")

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    fases = _de_tipo(eventos, "phase")
    prev = fases[1]["prev"]
    assert prev["errors"] == 341 and prev["n_fallos"] == 341 and prev["n_total"] == 1000
    assert prev["csv"] == "X/LOGS/ERRORES_RGB.csv"
    assert not any(STATS_ERRORES_PREFIX in str(p) for p in _de_tipo(eventos, "log"))
    done = _de_tipo(eventos, "done")[0]
    assert done["status"] == "errors" and done["errors"] == 341
    assert done["csvs"] == ["X/LOGS/ERRORES_RGB.csv"]


def test_marcador_sin_fallos_no_marca_error(monkeypatch):
    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Imágenes RGB")
        pcb.emit(_marcador("Imágenes RGB", 0, 10))

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    assert _de_tipo(eventos, "done")[0]["status"] == "ok"


def test_resumen_final_no_es_ok_si_procesadas_menos_que_origen(monkeypatch):
    """0 fallos declarados pero solo 7 de 10 llegaron a 'hecho' (3 sin tocar):
    el resumen final no puede ser ok."""

    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Imágenes RGB")
        pcb.emit(_marcador("Imágenes RGB", 0, 10, n_hechas=7))

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    done = _de_tipo(eventos, "done")[0]
    assert done["status"] == "errors" and done["errors"] == 3


def test_marcador_corrupto_se_cuenta_como_error_visible(monkeypatch):
    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Imágenes RGB")
        pcb.emit(STATS_ERRORES_PREFIX + "{no json")

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    assert _de_tipo(eventos, "done")[0]["status"] == "errors"
    assert any("ilegible" in str(p) for p in _de_tipo(eventos, "log"))


def test_regex_de_texto_sigue_de_respaldo_sin_marcador(monkeypatch):
    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Fase A")
        psum.emit("Ha habido 4 errores en la compresión.")

    eventos = _run_con_acciones(monkeypatch, [_emitir])

    assert _de_tipo(eventos, "done")[0]["errors"] == 4


def test_emitir_errores_fase_escribe_csv_y_marcador(tmp_path):
    from types import SimpleNamespace
    from atom_core import apply

    class _Man:
        def conteo_por_tipos(self, tipos, ejecucion_id=None):
            return {"pendiente": 0, "en_curso": 0, "hecho": 8, "fallido": 2}

        def fallidas_por_tipos(self, tipos, ejecucion_id=None):
            return [("a.jpg", "image file is truncated"), ("b.jpg", "otro")]

    emitidos = []
    cb = SimpleNamespace(emit=emitidos.append)
    payload = apply.emitir_errores_fase(
        _Man(), SimpleNamespace(output_folder=str(tmp_path)), cb, "Imágenes RGB", "RGB",
        frozenset({"RGB"}))

    assert payload["n_fallos"] == 2 and payload["n_total"] == 10
    csv_txt = (tmp_path / "LOGS" / "ERRORES_RGB.csv").read_text(encoding="utf-8-sig")
    assert "a.jpg,image file is truncated" in csv_txt
    marcador = [e for e in emitidos if e.startswith(STATS_ERRORES_PREFIX)]
    assert len(marcador) == 1
    assert json.loads(marcador[0][len(STATS_ERRORES_PREFIX):])["csv"].endswith("ERRORES_RGB.csv")


def test_marcador_cierre_fuerza_errors_con_fallidas_de_otro_tipo(monkeypatch):
    """Fallidas no RGB/TERMICA: solo las ve el marcador del cierre."""
    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Cierre")
        pcb.emit(STATS_ERRORES_PREFIX + json.dumps({
            "fase": "Cierre", "cierre": True, "n_fallos": 2, "n_total": 0, "n_hechas": 0,
            "rutas": ["a", "b"], "rutas_truncadas": False, "csv": None}))

    done = _de_tipo(_run_con_acciones(monkeypatch, [_emitir]), "done")[0]
    assert done["status"] == "errors" and done["errors"] == 2 and done["n_fallos"] == 2


def test_marcador_cierre_no_duplica_lo_ya_contado_por_la_fase(monkeypatch):
    """RGB cuenta 3 fallos; el cierre ve los mismos 3: el total sigue siendo 3."""
    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Imágenes RGB")
        pcb.emit(_marcador("Imágenes RGB", 3, 10))
        psum.emit("---> SUBPROCESO: Cierre")
        pcb.emit(STATS_ERRORES_PREFIX + json.dumps({
            "fase": "Cierre", "cierre": True, "n_fallos": 3, "n_total": 0, "n_hechas": 0,
            "rutas": [], "rutas_truncadas": False, "csv": None}))

    done = _de_tipo(_run_con_acciones(monkeypatch, [_emitir]), "done")[0]
    assert done["errors"] == 3 and done["n_fallos"] == 3 and done["n_total"] == 10


def test_emitir_errores_fase_cierre_sin_csv_y_filtra_por_run(tmp_path):
    from types import SimpleNamespace
    from atom_core import apply

    visto = {}

    class _Man:
        def conteo_por_tipos(self, tipos, ejecucion_id=None):
            visto["c"] = (tipos, ejecucion_id)
            return {"pendiente": 0, "en_curso": 0, "hecho": 5, "fallido": 1}

        def fallidas_por_tipos(self, tipos, ejecucion_id=None):
            return [("x.jpg", "m")]

    emitidos = []
    cb = SimpleNamespace(emit=emitidos.append)
    p = apply.emitir_errores_fase(_Man(), SimpleNamespace(output_folder=str(tmp_path)), cb,
                                  "Cierre", "CIERRE", None, ejecucion_id=7, cierre=True)
    assert visto["c"] == (None, 7)
    assert p["cierre"] is True and p["n_fallos"] == 1 and p["n_total"] == 0 and p["csv"] is None
    assert not (tmp_path / "LOGS").exists()


def _man_falso():
    class _Man:
        def conteo_por_tipos(self, tipos, ejecucion_id=None):
            return {"pendiente": 0, "en_curso": 0, "hecho": 1, "fallido": 1}

        def fallidas_por_tipos(self, tipos, ejecucion_id=None):
            return [("a.jpg", "m")]
    return _Man()


@pytest.mark.parametrize("nombre,excepcion", [
    ("aplicar_rgb", RuntimeError("boom")),
    ("aplicar_termicas", __import__("atom_core.cancelacion", fromlist=["x"]).RunCancelado()),
])
def test_marcador_y_csv_salen_en_finally_con_excepcion_o_cancelacion(
        tmp_path, monkeypatch, nombre, excepcion):
    from types import SimpleNamespace
    from atom_core import apply

    def _revienta(*a, **k):
        raise excepcion

    monkeypatch.setattr(apply, "_aplicar_rgb_impl", _revienta)
    monkeypatch.setattr(apply, "_aplicar_termicas_impl", _revienta)
    emitidos = []
    cb = SimpleNamespace(emit=emitidos.append)
    with pytest.raises(type(excepcion)):
        getattr(apply, nombre)(_man_falso(), SimpleNamespace(output_folder=str(tmp_path)),
                               None, cb, None, None, ejecucion_id=4)
    assert any(e.startswith(STATS_ERRORES_PREFIX) for e in emitidos)
    assert list((tmp_path / "LOGS").glob("ERRORES_*.csv"))


def test_run_con_reintento_que_vuelve_a_fallar_cuenta_una_vez(monkeypatch):
    """Tanda previa con 2 fallos + reintento que falla 1: el marcador de ESTE run dice 1."""
    def _emitir(pcb, pbar, psum):
        psum.emit("---> SUBPROCESO: Imágenes RGB")
        pcb.emit(_marcador("Imágenes RGB", 1, 2))

    done = _de_tipo(_run_con_acciones(monkeypatch, [_emitir]), "done")[0]
    assert done["errors"] == 1 and done["n_fallos"] == 1
