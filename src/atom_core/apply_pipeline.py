"""Pipeline de la fase `apply` unificada: RGB (E/S-bound) + térmica (CPU-bound) en
UNA task, para que la CPU reservada nunca quede ociosa.

Tres etapas sobre la lista de filas de la task, en el orden en que llegan
(`ManifiestoShard` ya las intercala por tipo):

  (a) DESCARGA  hilos de E/S que bajan el origen a `/tmp` (cuenta como RAM en
      Cloud Run gen2). Cola acotada por BYTES (`ATOM_APPLY_COLA_MIB`, 384 MiB).
  (b) PROCESO   UN solo aforo de CPU de `plazas` (= vCPU, ver
      `paralelismo.plazas_cpu_apply`). Lo comparten el RGB (`ProcessPoolExecutor`,
      leyendo del fichero local) y la conversión térmica (dji_irp/exiftool, en un
      hilo que espera a esos procesos). La E/S queda FUERA del aforo: no hay
      sobresuscripción de procesos.
  (c) SUBIDA    hilos de E/S con `crear_sin_pisar` (`if_generation_match=0`) +
      verificación de tamaño/md5. Destino existente: si es idéntico (md5) se da por
      bueno (reintento), si no es un CHOQUE: se registra y NUNCA se sobrescribe.
      El `*_T.JPG` va por `rewrite` server-side (sin pasar por /tmp).

Los deltas de reanudación se escriben por fila completada (`manifiesto.marcar_*`
de `ManifiestoShard`), igual que en la ruta por tipos. Sin fallbacks silenciosos:
el error de una fila queda en el manifiesto y en el log con su causa.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool

import dji_worker_pool
import external_tools
from atom_core import almacen as almacen_mod
from atom_core import apply as apply_mod
from atom_core import cancelacion
from atom_core import indice as indice_mod
from atom_core import lectura_segura
from atom_core import paralelismo
from atom_core import simulacion

MIB = 1024 * 1024
COLA_MIB_DEFECTO = 384
HILOS_DESCARGA_DEFECTO = 8
HILOS_SUBIDA_DEFECTO = 8


def _env_entero(nombre: str, defecto: int) -> int:
    bruto = os.environ.get(nombre, "").strip()
    if not bruto:
        return defecto
    try:
        valor = int(bruto)
    except ValueError:
        raise ValueError(f"{nombre}={bruto!r}: no es un entero") from None
    if valor < 1:
        raise ValueError(f"{nombre}={bruto!r}: debe ser >= 1")
    return valor


def limite_cola_bytes() -> int:
    return _env_entero("ATOM_APPLY_COLA_MIB", COLA_MIB_DEFECTO) * MIB


class AlmacenApply:
    """Operaciones de almacenamiento del pipeline (sustituible en tests)."""

    def tamano(self, ruta: str) -> int:
        return apply_mod._tamano_ruta(ruta)

    def descargar(self, origen: str, local: str) -> int:
        return almacen_mod.descargar_a(origen, local)

    def crear(self, local: str, destino: str, metadatos: "dict | None" = None) -> None:
        """Sube sin pisar; `FileExistsError` (`ChoqueDestino`) si el destino existe."""
        almacen_mod.crear_sin_pisar(local, destino, metadatos)

    def objeto(self, destino: str) -> "tuple[int, dict] | None":
        """`(generation, metadata)` del destino o `None` (no existe / no es gs://)."""
        return almacen_mod.info_objeto(destino)

    def reescribir(self, local: str, destino: str, generacion: int, metadatos: "dict | None") -> None:
        almacen_mod.sobrescribir_propio(local, destino, generacion, metadatos)

    def md5(self, ruta: str) -> "str | None":
        return almacen_mod.md5_base64_de(ruta)

    def copiar_servidor(self, origen: str, destino: str) -> str:
        return almacen_mod.copiar_servidor(origen, destino)


class PresupuestoBytes:
    """Cola acotada por bytes. `reservar(n)` bloquea mientras `entrada + n` supere
    el límite (un elemento más grande que el límite entra solo, con la cola vacía,
    para no bloquear para siempre). Las salidas pendientes de subir se cuentan con
    `forzar` (ya existen en disco) y frenan nuevas reservas hasta que se suben."""

    def __init__(self, limite: int) -> None:
        self.limite = int(limite)
        self._cond = threading.Condition()
        self._entrada = 0
        self._salida = 0
        self.pico_entrada = 0
        self.pico_total = 0

    def reservar(self, n: int) -> bool:
        with self._cond:
            while (self._entrada + self._salida) > 0 and (
                    self._entrada + self._salida + n > self.limite):
                if cancelacion.cancelado():
                    return False
                self._cond.wait(timeout=1)
            self._entrada += n
            self.pico_entrada = max(self.pico_entrada, self._entrada)
            self.pico_total = max(self.pico_total, self._entrada + self._salida)
            return True

    def liberar_entrada(self, n: int) -> None:
        with self._cond:
            self._entrada -= n
            self._cond.notify_all()

    def forzar_salida(self, n: int) -> None:
        with self._cond:
            self._salida += n
            self.pico_total = max(self.pico_total, self._entrada + self._salida)

    def liberar_salida(self, n: int) -> None:
        with self._cond:
            self._salida -= n
            self._cond.notify_all()


class _Tarea:
    __slots__ = ("fila", "es_termica", "n_entrada", "dir", "entrada_local", "n_salida",
                 "cerrada", "t_choques")

    def __init__(self, fila: dict, es_termica: bool) -> None:
        self.fila = fila
        self.es_termica = es_termica
        self.n_entrada = 0
        self.dir = None
        self.entrada_local = None
        self.n_salida = 0
        self.cerrada = False
        self.t_choques = 0


class PipelineApply:
    def __init__(self, manifiesto, cfg, split_images_obj, progress_callback, progress_bar,
                 progress_summarize, contador_rotacion=None, *, almacen=None, plazas=None,
                 limite_bytes=None, hilos_descarga=None, hilos_subida=None,
                 fabrica_ejecutor_rgb=None, procesar_rgb=None, convertir_termica=None,
                 aforo=None, ejecucion="", shard_index=0):
        self.manifiesto = manifiesto
        # Marca de propiedad de lo que sube esta task (ajuste A): permite reescribir
        # SU propia salida si un reintento la regenera con bytes distintos.
        self._ejecucion = str(ejecucion or "").strip()
        self._metadatos = ({"atom-ejecucion": self._ejecucion, "atom-shard": str(int(shard_index))}
                           if self._ejecucion else None)
        self.reescrituras: list[str] = []
        self.cfg = cfg
        self.split_images_obj = split_images_obj
        self.cb = progress_callback
        self.progress_bar = progress_bar
        self.progress_summarize = progress_summarize
        self.contador = contador_rotacion or apply_mod._ContadorRotacion()
        self.almacen = almacen or AlmacenApply()
        self.plazas = int(plazas) if plazas is not None else paralelismo.plazas_cpu_apply()
        self.presupuesto = PresupuestoBytes(
            limite_bytes if limite_bytes is not None else limite_cola_bytes())
        self.hilos_descarga = hilos_descarga or _env_entero(
            "ATOM_APPLY_HILOS_DESCARGA", HILOS_DESCARGA_DEFECTO)
        self.hilos_subida = hilos_subida or _env_entero(
            "ATOM_APPLY_HILOS_SUBIDA", HILOS_SUBIDA_DEFECTO)
        self._fabrica_rgb = fabrica_ejecutor_rgb or (
            lambda n: ProcessPoolExecutor(**apply_mod._kwargs_pool_rgb(n)))
        self._procesar_rgb = procesar_rgb or apply_mod._trabajo_fila_local
        self._convertir = convertir_termica or apply_mod._convertir_una_termica
        self.aforo = aforo or apply_mod._AforoDinamico(self.plazas)

        self.resultado = {"hecho": 0, "fallido": 0}
        self.choques: list[str] = []
        self._lock = threading.Lock()
        self._cond_fin = threading.Condition()
        self._vivas = 0
        self._hechas_rgb = 0
        self._hechas_term = 0
        self._completadas = 0
        self._total = 0
        self._medidor = apply_mod._MedidorVelocidad()
        self._emisor = apply_mod._EmisorProgreso(progress_bar)
        self._staging_raiz = None
        self._pool_exif = None
        self._exiftool_exe = None
        self._dji_utility = None
        self._ejecutor_rgb = None
        self._lock_rgb = threading.Lock()
        self._p_descarga = self._p_proceso = self._p_subida = None

    # ---------------------------------------------------------------- cierre de fila
    def _terminar(self, t: _Tarea) -> None:
        """Libera recursos de la tarea y la da por terminada (UNA vez)."""
        with self._lock:
            if t.cerrada:
                return
            t.cerrada = True
        if t.n_entrada:
            self.presupuesto.liberar_entrada(t.n_entrada)
            t.n_entrada = 0
        if t.n_salida:
            self.presupuesto.liberar_salida(t.n_salida)
            t.n_salida = 0
        if t.dir:
            shutil.rmtree(t.dir, ignore_errors=True)
        with self._cond_fin:
            self._vivas -= 1
            self._cond_fin.notify_all()

    def _cerrar(self, t: _Tarea, verificacion=None, error: Exception = None) -> None:
        fila = t.fila
        try:
            self.contador.registrar(fila["angulo_giro"] or 0)
            if error is not None:
                self.manifiesto.marcar_fallida(fila["id"], str(error))
                self.cb.emit("\nERROR procesando {0}: {1}: {2}\n".format(
                    fila["ruta_origen"], type(error).__name__, error))
            else:
                self.manifiesto.marcar_hecha(fila["id"], verificacion)
            with self._lock:
                if error is not None:
                    self.resultado["fallido"] += 1
                else:
                    self.resultado["hecho"] += 1
                    if t.es_termica:
                        self._hechas_term += 1
                    else:
                        self._hechas_rgb += 1
                self._completadas += 1
                hechas = self._completadas
                rgb, term = self._hechas_rgb, self._hechas_term
            self._medidor.registrar()
            if hechas % apply_mod._EMITIR_STATS_CADA == 0 or hechas == self._total:
                apply_mod._emitir_stats_apply(
                    self.cb, "Apply (RGB + térmica)", hechas, self._total, rgb=rgb,
                    termica=term, medidor=self._medidor, contador_rotacion=self.contador)
            self._emisor.emit(int(hechas / self._total * 100))
        finally:
            self._terminar(t)

    def _fallar(self, t: _Tarea, exc: Exception) -> None:
        try:
            self._cerrar(t, error=exc)
        except Exception as exc2:  # noqa: BLE001 — el manifiesto falló: se ve y se sigue
            self.cb.emit(f"\nERROR no se pudo cerrar la fila {t.fila['ruta_origen']}: {exc2}\n")
            self._terminar(t)

    # ---------------------------------------------------------------- etapas
    def _etapa(self, nombre, funcion, t: _Tarea, *args) -> None:
        """Ejecuta una etapa: cualquier excepción cierra la fila con su causa."""
        try:
            if cancelacion.cancelado():
                self._terminar(t)  # queda 'pendiente' para el reintento
                return
            funcion(t, *args)
        except apply_mod.ExiftoolNoDisponible:
            self._terminar(t)  # fallo masivo: la fase aborta al final, fila sin tocar
        except Exception as exc:  # noqa: BLE001
            self._fallar(t, exc)

    def _descargar(self, t: _Tarea) -> None:
        fila = t.fila
        t.dir = tempfile.mkdtemp(dir=self._staging_raiz, prefix="f_")
        carpeta_in = os.path.join(t.dir, "in")
        os.makedirs(carpeta_in)
        local = os.path.join(carpeta_in, os.path.basename(fila["ruta_origen"]))
        with simulacion.fase("lectura"):
            real = self.almacen.descargar(fila["ruta_origen"], local)
        simulacion.registrar_lectura(t.es_termica, real)
        esperado = int(fila.get("bytes_origen") or 0)
        if esperado and real != esperado:
            raise lectura_segura.FicheroCambiado(fila["ruta_origen"], real, esperado)
        t.entrada_local = local
        self._p_proceso.submit(self._etapa, "proceso", self._procesar, t)

    def _ejecutor(self):
        with self._lock_rgb:
            if self._ejecutor_rgb is None:
                self._ejecutor_rgb = self._fabrica_rgb(self.plazas)
            return self._ejecutor_rgb

    def _recrear_ejecutor(self, roto) -> None:
        with self._lock_rgb:
            if self._ejecutor_rgb is roto:
                try:
                    roto.shutdown(wait=False, cancel_futures=True)
                except Exception:  # noqa: BLE001
                    pass
                self._ejecutor_rgb = self._fabrica_rgb(self.plazas)

    def _rgb(self, t: _Tarea) -> list:
        carpeta_out = os.path.join(t.dir, "out")
        os.makedirs(carpeta_out, exist_ok=True)
        for intento in (1, 2):
            ejecutor = self._ejecutor()
            try:
                return list(ejecutor.submit(
                    self._procesar_rgb, t.fila, self.cfg, t.entrada_local, carpeta_out).result())
            except BrokenProcessPool:
                self._recrear_ejecutor(ejecutor)
                if intento == 2:
                    raise BrokenProcessPool(
                        "un proceso de la fase RGB murió de forma abrupta (posible falta de RAM)")
        raise AssertionError("inalcanzable")

    def _procesar(self, t: _Tarea) -> None:
        with simulacion.fase("computo"):
            self._procesar_medido(t)

    def _procesar_medido(self, t: _Tarea) -> None:
        if not self.aforo.adquirir():  # cancelado esperando aforo
            self._terminar(t)
            return
        try:
            if t.es_termica:
                diferido: dict = {}
                self._convertir(
                    t.fila, self.cfg, self.split_images_obj, self._exiftool_exe,
                    self._dji_utility, self.cb, self.progress_bar, self._staging_raiz,
                    copiar_exif=self._pool_exif.copiar, staging_previo=t.dir,
                    diferido=diferido)
                subidas = list(diferido.get("subidas", []))
                copias = list(diferido.get("copias", []))
            else:
                salidas = self._rgb(t)
                subidas = [(local, destino) for destino, local in salidas]
                copias = []
        finally:
            self.aforo.liberar()
        # Entrada procesada: se libera su sitio en la cola y se cuentan las salidas.
        pendientes_subir = [local for local, _ in subidas]
        for origen, destino, rjpeg in copias:
            if almacen_mod.es_uri_gcs(origen) and almacen_mod.es_uri_gcs(destino):
                if os.path.exists(rjpeg):
                    os.remove(rjpeg)  # el `_T.JPG` va por rewrite server-side: /tmp libre ya
            else:
                pendientes_subir.append(rjpeg)
        t.n_salida = sum(os.path.getsize(p) for p in pendientes_subir)
        self.presupuesto.forzar_salida(t.n_salida)
        self.presupuesto.liberar_entrada(t.n_entrada)
        t.n_entrada = 0
        if not t.es_termica and t.entrada_local and os.path.exists(t.entrada_local):
            os.remove(t.entrada_local)
        self._p_subida.submit(self._etapa, "subida", self._subir, t, subidas, copias)

    def _subir_sin_pisar(self, local: str, destino: str) -> None:
        try:
            if self._metadatos:
                self.almacen.crear(local, destino, self._metadatos)
            else:
                self.almacen.crear(local, destino)
            return
        except FileExistsError:
            pass
        md5_local = almacen_mod.md5_base64_de(local)
        md5_remoto = self.almacen.md5(destino)
        if md5_remoto and md5_remoto == md5_local:
            return  # reintento de una fila ya subida: idéntico, nada que hacer
        if self._ejecucion:
            info = self.almacen.objeto(destino)
            if info is not None and info[1].get("atom-ejecucion") == self._ejecucion:
                try:
                    self.almacen.reescribir(local, destino, info[0], self._metadatos)
                except almacen_mod.ChoqueDestino:
                    pass  # cambió entre la lectura y la escritura: ya no es seguro, CHOQUE
                else:
                    aviso = (f"reescritura propia: {destino} (ejecución {self._ejecucion}, "
                             f"generation {info[0]}) regenerada por un reintento")
                    with self._lock:
                        self.reescrituras.append(aviso)
                    self.cb.emit(f"\n[reintento] {aviso}\n")
                    return
        msg = (f"CHOQUE: {destino} ya existe con contenido distinto (md5 {md5_remoto} != "
               f"{md5_local}); no se sobrescribe")
        with self._lock:
            self.choques.append(msg)
        self.cb.emit(f"\n[choque] {msg}\n")
        raise almacen_mod.ChoqueDestino(msg)

    def _subir(self, t: _Tarea, subidas: list, copias: list) -> None:
        with simulacion.fase("escritura_simulada"):
            self._subir_medido(t, subidas, copias)

    def _subir_medido(self, t: _Tarea, subidas: list, copias: list) -> None:
        fila = t.fila
        piezas: list[str] = []
        if t.es_termica:
            for origen, destino, rjpeg in copias:
                if almacen_mod.es_uri_gcs(origen) and almacen_mod.es_uri_gcs(destino):
                    try:
                        self.almacen.copiar_servidor(origen, destino)
                    except almacen_mod.ChoqueDestino as exc:
                        with self._lock:
                            self.choques.append(str(exc))
                        self.cb.emit(f"\n[choque] {exc}\n")
                        raise
                    # Simulacion: el destino no existe (no se copio); se usa el tamano de origen.
                    tam = (int(fila.get("bytes_origen") or 0)
                           or self.almacen.tamano(origen if simulacion.activa() else destino))
                else:
                    self._subir_sin_pisar(rjpeg, destino)
                    tam = os.path.getsize(rjpeg)
                piezas.append(f"{destino}:{tam}")
            for local, destino in subidas:
                self._subir_sin_pisar(local, destino)
                piezas.append(f"{destino}:{os.path.getsize(local)}")
        else:
            for local, destino in subidas:
                self._subir_sin_pisar(local, destino)
                piezas.append(f"{destino}:{os.path.getsize(local)}")
        self._cerrar(t, verificacion="; ".join(piezas))

    # ---------------------------------------------------------------- bucle
    def ejecutar(self) -> dict:
        m, cfg, cb = self.manifiesto, self.cfg, self.cb
        self.progress_summarize.emit("---> SUBPROCESO: Apply (RGB + térmica)")
        apply_mod._reportar_colisiones_destino(m, cb)

        todas = [dict(f) for f in m.pendientes(origen=getattr(cfg, "input_folder", None))
                 if f["tipo"] in indice_mod.TIPOS_RGB or f["tipo"] == "TERMICA"]
        antes_del_veto = len(todas)
        todas = apply_mod._vetar_sobrescrituras(m, todas, cb)
        rgb_ok = apply_mod._validar_jpeg_origen(
            m, [f for f in todas if f["tipo"] in indice_mod.TIPOS_RGB], cb)
        ids_rgb = {f["id"] for f in rgb_ok}
        filas = [f for f in todas if f["tipo"] == "TERMICA" or f["id"] in ids_rgb]
        self.resultado["fallido"] = antes_del_veto - len(filas)
        self._total = len(filas)
        if not filas:
            return self.resultado

        hay_termica = any(f["tipo"] == "TERMICA" for f in filas)
        hay_rgb = any(f["tipo"] != "TERMICA" for f in filas)
        self._staging_raiz = tempfile.mkdtemp(prefix="apply_pipeline_")
        cb.emit(f"\n[paralelismo] Apply unificado: {self.plazas} plaza(s) de CPU compartidas "
                f"(RGB {sum(1 for f in filas if f['tipo'] != 'TERMICA')} + térmica "
                f"{sum(1 for f in filas if f['tipo'] == 'TERMICA')}), cola de descarga "
                f"{self.presupuesto.limite // MIB} MiB, {self.hilos_descarga} hilo(s) de "
                f"descarga, {self.hilos_subida} de subida.\n")
        cancelado = False
        if hay_rgb:
            apply_mod.perfil_rgb.escribir_cabecera()
            if apply_mod.rgb_gpu.activo():
                cb.emit("\n[gpu] AVISO: el apply unificado no usa la GPU; RGB va por CPU.\n")
        try:
            if hay_termica:
                self._exiftool_exe = external_tools.resource_path("programas_externos", "exiftool.exe")
                self._dji_utility = external_tools.dji_utility_path()
                self._pool_exif = apply_mod._PoolExiftool(
                    self._exiftool_exe, apply_mod._procesos_exif())
                atexit.register(self._pool_exif.cerrar)
            self._p_descarga = ThreadPoolExecutor(self.hilos_descarga, thread_name_prefix="ap-desc")
            self._p_proceso = ThreadPoolExecutor(self.plazas, thread_name_prefix="ap-proc")
            self._p_subida = ThreadPoolExecutor(self.hilos_subida, thread_name_prefix="ap-sub")
            try:
                for fila in filas:
                    if cancelacion.cancelado():
                        cancelado = True
                        break
                    t = _Tarea(fila, fila["tipo"] == "TERMICA")
                    try:
                        n = int(fila.get("bytes_origen") or 0) or self.almacen.tamano(fila["ruta_origen"])
                    except Exception as exc:  # noqa: BLE001
                        with self._cond_fin:
                            self._vivas += 1
                        self._fallar(t, exc)
                        continue
                    if not self.presupuesto.reservar(n):
                        cancelado = True
                        break
                    t.n_entrada = n
                    with self._cond_fin:
                        self._vivas += 1
                    self._p_descarga.submit(self._etapa, "descarga", self._descargar, t)
            finally:
                with self._cond_fin:
                    while self._vivas > 0:
                        self._cond_fin.wait(timeout=1)
                self._p_descarga.shutdown(wait=True)
                self._p_proceso.shutdown(wait=True)
                self._p_subida.shutdown(wait=True)
                if self._ejecutor_rgb is not None:
                    self._ejecutor_rgb.shutdown(wait=True)
        finally:
            dji_worker_pool.shutdown()
            if self._pool_exif is not None:
                self._pool_exif.cerrar()
                atexit.unregister(self._pool_exif.cerrar)
            shutil.rmtree(self._staging_raiz, ignore_errors=True)

        if cancelado or cancelacion.cancelado():
            raise cancelacion.RunCancelado()
        apply_mod._emitir_resumen_fase(cb, "Apply (RGB + térmica)", "plaza(s) CPU",
                                       self._total, self.aforo.resumen(self.plazas))
        apply_mod._emitir_resumen_perfil_rgb(cb)
        cb.emit(f"[cola] pico de entrada {self.presupuesto.pico_entrada // MIB} MiB "
                f"(total con salidas {self.presupuesto.pico_total // MIB} MiB, límite "
                f"{self.presupuesto.limite // MIB} MiB); choques: {len(self.choques)}.\n")
        if self._pool_exif is not None and self._pool_exif.fatal:
            raise apply_mod.ExiftoolNoDisponible(
                "Fase térmica abortada: {0} Las filas no procesadas siguen pendientes; "
                "revisa la instalación de exiftool y reintenta.".format(self._pool_exif.fatal))
        sin_procesar = [
            fila for fila in m.todas()
            if fila["tipo"] == "TERMICA" and fila["estado"] in ("pendiente", "en_curso")
            and not fila["unassigned"] and fila["pb"] and fila["vuelo"]
            and apply_mod._fila_del_origen(fila, getattr(cfg, "input_folder", None))]
        if sin_procesar:
            msg = (f"Fase térmica incompleta: {len(sin_procesar)} térmica(s) con vuelo "
                   f"asignado NO se han procesado (ni giradas ni convertidas). "
                   f"Ejemplo: {sin_procesar[0]['ruta_origen']}")
            cb.emit("ERROR: " + msg + "\n")
            raise RuntimeError(msg)
        return self.resultado


def aplicar_pipeline(manifiesto, cfg, split_images_obj, progress_callback, progress_bar,
                     progress_summarize, contador_rotacion=None, **kwargs) -> dict:
    """Corre el pipeline unificado y devuelve `{"hecho", "fallido"}`."""
    return PipelineApply(manifiesto, cfg, split_images_obj, progress_callback, progress_bar,
                         progress_summarize, contador_rotacion, **kwargs).ejecutar()
