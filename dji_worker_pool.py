"""
Pool de procesos persistentes para el SDK térmico DJI en Linux no-x86 (Raspberry
Pi, box64). Ver `dji_irp_linux.py` para el modo "servidor" que consume este pool.

Motivación: bajo box64+Python x86-64 emulado, arrancar el intérprete cuesta
~370 ms POR IMAGEN aunque el cómputo real de la térmica sea mucho más barato.
Manteniendo N procesos vivos (uno por hilo de la fase, creados perezosamente
según la demanda real de concurrencia) y hablando con ellos por stdin/stdout,
ese coste se paga una sola vez por proceso en vez de una vez por imagen.

Solo se activa cuando `external_tools.dji_linux_launcher()` devuelve el
lanzador emulado (box64 + Python x86-64), es decir, en máquinas no-x86_64. En
Windows y en Linux x86-64 (dev, Cloud Run) este módulo no interviene: el
lanzador es el propio intérprete y el ahorro de arranque no existe.

Desactivable con `ATOM_DJI_PERSISTENT=0` (vuelve al subprocess efímero de
siempre). Cualquier fallo de un worker (muerte del proceso, protocolo roto, o
el propio SDK reportando error) descarta ESE worker (no se reintenta con él)
y propaga la excepción: quien llama (`pipeline._dji_measure_to_raw_linux`)
reintenta la imagen por la vía antigua (subprocess efímero de
`dji_irp_linux.py`), que es la ruta ya validada byte a byte.
"""
import atexit
import json
import os
import queue
import subprocess
import threading

import external_tools

_DJI_IRP_LINUX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dji_irp_linux.py")

# Techo de procesos persistentes simultáneos. No es el paralelismo real (ese lo
# marca cuántos hilos de la fase piden un worker a la vez, ver `_Pool._checkout`);
# es solo una cota de seguridad para que una fase con un `max_workers` anómalo no
# dispare procesos sin límite.
_MAX_WORKERS = int(os.environ.get("ATOM_DJI_PERSISTENT_MAX", "64"))


def persistent_enabled() -> bool:
    """True si esta ejecución debe usar el pool persistente.

    Solo tiene sentido donde hoy se emula el proceso entero (ver
    `external_tools.dji_linux_launcher`): en x86-64 nativo el lanzador ya es el
    intérprete actual, así que no hay arranque de box64 que amortizar."""
    if os.environ.get("ATOM_DJI_PERSISTENT", "1") == "0":
        return False
    return not external_tools.is_x86_64()


class _Worker:
    """Un proceso `dji_irp_linux.py --server` vivo, con su propio candado (las
    peticiones a un mismo worker se sirven una a una; el paralelismo lo da tener
    varios workers, no varias peticiones concurrentes al mismo)."""

    def __init__(self, lib_dir: str):
        lanzador, env_extra = external_tools.dji_linux_launcher(lib_dir)
        env = os.environ.copy()
        env.update(env_extra)
        self.proc = subprocess.Popen(
            lanzador + [_DJI_IRP_LINUX, "--server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, env=env,
        )
        self.lock = threading.Lock()

    def measure(self, img: str, raw_out: str, humidity: float, emissivity: float, lib_dir: str) -> None:
        req = json.dumps({
            "img": img, "raw_out": raw_out,
            "humidity": float(humidity), "emissivity": float(emissivity),
            "lib_dir": lib_dir,
        })
        with self.lock:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    "worker DJI persistente ya estaba muerto (rc={0})".format(self.proc.returncode))
            try:
                self.proc.stdin.write(req + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except (BrokenPipeError, OSError) as e:
                raise RuntimeError("worker DJI persistente: fallo de E/S ({0})".format(e)) from e
            if not line:
                stderr = ""
                try:
                    stderr = self.proc.stderr.read(4000) or ""
                except Exception:
                    pass
                raise RuntimeError(
                    "worker DJI persistente cerró stdout sin responder: {0}".format(stderr.strip()))
        try:
            resp = json.loads(line)
        except Exception as e:
            raise RuntimeError("worker DJI persistente: respuesta no-JSON {0!r} ({1})".format(line, e)) from e
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error") or "el worker DJI persistente devolvió un error sin detalle")

    def close(self) -> None:
        with self.lock:
            if self.proc.poll() is not None:
                return
            try:
                self.proc.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                self.proc.stdin.flush()
                self.proc.stdin.close()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class _Pool:
    """Pool perezoso: no crea ningún worker hasta la primera imagen. Crece según
    la demanda de concurrencia real (cuántos hilos piden a la vez), hasta
    `_MAX_WORKERS`. Un worker que falla se descarta (nunca vuelve a la cola de
    disponibles) y el hueco lo ocupa el siguiente `_checkout`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._idle: "queue.Queue[_Worker]" = queue.Queue()
        self._n_created = 0
        self._closed = False

    def _checkout(self, lib_dir: str) -> _Worker:
        with self._lock:
            if self._closed:
                raise RuntimeError("pool DJI persistente ya cerrado")
            try:
                return self._idle.get_nowait()
            except queue.Empty:
                pass
            if self._n_created < _MAX_WORKERS:
                self._n_created += 1
                crear_nuevo = True
            else:
                crear_nuevo = False
        if crear_nuevo:
            try:
                return _Worker(lib_dir)
            except BaseException:
                # Si el worker no llega a nacer (p. ej. falta el runtime x86), el
                # cupo reservado debe volver: si no, tras _MAX_WORKERS fallos el
                # pool se cree lleno y todos los hilos esperan en `_idle.get()`
                # para siempre (cuelgue KL19 Pi 2026-09-11).
                with self._lock:
                    self._n_created = max(0, self._n_created - 1)
                raise
        # Cupo lleno: esperar a que otro hilo libere su worker.
        return self._idle.get()

    def _checkin(self, w: "_Worker", descartar: bool) -> None:
        if descartar:
            with self._lock:
                self._n_created = max(0, self._n_created - 1)
            w.close()
            return
        with self._lock:
            if self._closed:
                w.close()
                return
        self._idle.put(w)

    def measure(self, img: str, raw_out: str, humidity: float, emissivity: float, lib_dir: str) -> None:
        w = self._checkout(lib_dir)
        try:
            w.measure(img, raw_out, humidity, emissivity, lib_dir)
        except Exception:
            self._checkin(w, descartar=True)
            raise
        else:
            self._checkin(w, descartar=False)

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
        vivos = []
        while True:
            try:
                vivos.append(self._idle.get_nowait())
            except queue.Empty:
                break
        for w in vivos:
            w.close()


_pool: "_Pool | None" = None
_pool_lock = threading.Lock()


def _get_pool() -> _Pool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = _Pool()
        return _pool


def measure(img: str, raw_out: str, humidity: float, emissivity: float, lib_dir: str) -> None:
    """Convierte `img` a `raw_out` usando el pool de workers persistentes.

    Lanza RuntimeError si el worker asignado falla por cualquier motivo (muerto,
    protocolo roto, o el propio SDK reportando error): el llamador debe
    reintentar por la vía antigua (subprocess efímero)."""
    _get_pool().measure(img, raw_out, humidity, emissivity, lib_dir)


def shutdown() -> None:
    """Cierra todos los workers vivos. Se llama al final de la fase térmica y en
    `atexit` como red de seguridad si algo interrumpe el proceso antes."""
    global _pool
    with _pool_lock:
        p, _pool = _pool, None
    if p is not None:
        p.close_all()


atexit.register(shutdown)
