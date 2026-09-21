"""RGB en GPU NVIDIA (nvImageCodec + CuPy) — PRUEBA, opt-in.

Solo se activa con `ORGANIZER_RGB_GPU=1` y si hay nvImageCodec, CuPy y una GPU
CUDA visible. Replica `apply._escribir_salidas_de_fila` (mismas rutas, misma
caja 5:4, mismas calidades, EXIF + XMP pegados) pero decodifica/recorta/gira/
codifica por lotes en la GPU. No es bit-idéntico a PIL (PSNR ~46 dB).

Hilo principal = GPU (una sola cola CUDA). Lectura y escritura en hilos. Toda
fila que falle en GPU se rehace en CPU con `fallback_cpu(fila)`. El manifiesto
se toca SOLO desde el hilo principal (`cerrar_fila`).

Variables: ORGANIZER_RGB_GPU_LOTE (2), ORGANIZER_RGB_GPU_HILOS (8),
ORGANIZER_RGB_GPU_NVHILOS (4).
"""
from __future__ import annotations

import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from exif import extraer_bloque_xmp_crudo

_ESTADO: dict = {}


def activo() -> bool:
    if os.environ.get("ORGANIZER_RGB_GPU") != "1":
        return False
    if "ok" in _ESTADO:
        return _ESTADO["ok"]
    try:
        import cupy as cp
        from nvidia import nvimgcodec as nv
        ok = cp.cuda.runtime.getDeviceCount() > 0
        if ok:
            nt = int(os.environ.get("ORGANIZER_RGB_GPU_NVHILOS", "4"))
            _ESTADO.update(
                cp=cp, nv=nv,
                dec=nv.Decoder(max_num_cpu_threads=nt),
                enc=nv.Encoder(max_num_cpu_threads=nt),
                dparams=nv.DecodeParams(apply_exif_orientation=False),
                eparams={},
            )
    except Exception as exc:  # sin deps / sin driver -> CPU
        _ESTADO["error"] = repr(exc)
        ok = False
    _ESTADO["ok"] = ok
    return ok


def _eparams(calidad: int):
    nv = _ESTADO["nv"]
    if calidad not in _ESTADO["eparams"]:
        _ESTADO["eparams"][calidad] = nv.EncodeParams(
            quality_type=nv.QualityType.QUALITY, quality_value=calidad,
            chroma_subsampling=nv.ChromaSubsampling.CSS_420)
    return _ESTADO["eparams"][calidad]


def _leer(fila, pipeline_mod):
    ruta = fila["ruta_origen"]
    with open(ruta, "rb") as fh:
        crudo = fh.read()
    with pipeline_mod.Image.open(ruta) as cab:
        exif = cab.getexif().tobytes()
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif if exif else b""
    return crudo, app1, extraer_bloque_xmp_crudo(ruta) or b""


def _escribir(destino: str, datos: bytes, app1: bytes, xmp: bytes) -> str:
    carpeta = os.path.dirname(destino)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    raiz, extension = os.path.splitext(destino)
    parcial = f"{raiz}.parcial{extension}"
    try:
        with open(parcial, "wb") as fh:
            fh.write(datos[:2] + app1 + datos[2:] + xmp)
    except Exception:
        if os.path.exists(parcial):
            os.remove(parcial)
        raise
    os.replace(parcial, destino)
    return destino


def _rot(cp, arr, angulo: int):
    # Mismo criterio que `_transpose_para_angulo`: 90 horario = PIL ROTATE_270
    # = rot90 k=-1; 270 horario = PIL ROTATE_90 = rot90 k=1.
    if angulo == 90:
        return cp.rot90(arr, -1)
    if angulo == 270:
        return cp.rot90(arr, 1)
    return arr


def aplicar(filas, cfg, pipeline_mod, apply_mod, cerrar_fila, fallback_cpu,
            progress_bar, progress_callback) -> None:
    cp = _ESTADO["cp"]
    lote = max(1, int(os.environ.get("ORGANIZER_RGB_GPU_LOTE", "2")))
    hilos = max(1, int(os.environ.get("ORGANIZER_RGB_GPU_HILOS", "8")))
    total = len(filas)
    progress_callback.emit(
        f"\n[gpu] Imágenes RGB en GPU: lote={lote}, hilos IO={hilos}, {total} imagen(es).\n")
    lotes = [filas[k:k + lote] for k in range(0, total, lote)]
    pendientes: deque = deque()  # (fila, [futuros escritura] | None, error)
    stats = {"gpu": 0, "cpu": 0, "t0": time.monotonic()}
    completadas = 0

    def _drenar(bloquear: bool) -> None:
        nonlocal completadas
        while pendientes:
            fila, futuros = pendientes[0]
            if not bloquear and not all(f.done() for f in futuros):
                return
            pendientes.popleft()
            try:
                rutas = [f.result() for f in futuros]
            except Exception:
                try:
                    verificacion = fallback_cpu(fila)
                    stats["cpu"] += 1
                except Exception as exc:
                    cerrar_fila(fila, error=exc)
                else:
                    cerrar_fila(fila, verificacion=verificacion)
            else:
                stats["gpu"] += 1
                cerrar_fila(fila, verificacion="; ".join(
                    f"{r}:{os.path.getsize(r)}" for r in rutas))
            completadas += 1
            progress_bar.emit(int(completadas / total * 100))

    def _a_cpu(fila) -> None:
        pendientes.append((fila, [pool.submit(_lanzar_fallo)]))

    def _lanzar_fallo():
        raise RuntimeError("gpu")

    with ThreadPoolExecutor(hilos) as pool:
        lecturas = {}

        def _pedir(i):
            if i < len(lotes) and i not in lecturas:
                lecturas[i] = [pool.submit(_leer, f, pipeline_mod) for f in lotes[i]]

        for i, filas_lote in enumerate(lotes):
            _pedir(i)
            _pedir(i + 1)  # lectura adelantada mientras la GPU trabaja
            _pedir(i + 2)
            leidas = []
            for fila, fut in zip(filas_lote, lecturas.pop(i)):
                try:
                    leidas.append((fila, fut.result()))
                except Exception:
                    _a_cpu(fila)
            if not leidas:
                _drenar(False)
                continue
            try:
                imgs = _ESTADO["dec"].decode([c for _, (c, _, _) in leidas],
                                             params=_ESTADO["dparams"])
            except Exception:
                imgs = [None] * len(leidas)
            trabajos = {}  # calidad -> [(idx, destino, array)]
            validas = []
            for idx, ((fila, (_, app1, xmp)), img) in enumerate(zip(leidas, imgs)):
                if img is None:
                    _a_cpu(fila)
                    continue
                try:
                    arr = cp.asarray(img)
                    h, w = arr.shape[:2]
                    angulo = fila["angulo_giro"] or 0
                    q_orig = pipeline_mod._ROTATION_JPEG_QUALITY if angulo else cfg.compress_level
                    q_crop = pipeline_mod._ROTATION_JPEG_QUALITY if angulo else apply_mod._CROP_JPEG_QUALITY
                    salidas = []
                    if fila["ruta_salida_crop"]:
                        l, t, r, b = apply_mod._caja_recorte_termico(w, h, fila["pct_recorte"])
                        crop = cp.ascontiguousarray(_rot(cp, arr[t:b, l:r], angulo))
                        salidas.append((q_crop, fila["ruta_salida_crop"], crop))
                    orig = cp.ascontiguousarray(_rot(cp, arr, angulo))
                    salidas.append((q_orig, fila["ruta_salida_original"], orig))
                except Exception:
                    _a_cpu(fila)
                    continue
                validas.append((fila, app1, xmp, [d for _, d, _ in salidas]))
                for q, destino, a in salidas:
                    trabajos.setdefault(q, []).append((len(validas) - 1, destino, a))
            codificados = {}  # (idx_valida, destino) -> bytes | None
            for q, lst in trabajos.items():
                try:
                    cs = _ESTADO["enc"].encode([a for _, _, a in lst], ".jpg", params=_eparams(q))
                except Exception:
                    cs = [None] * len(lst)
                for (iv, destino, _), c in zip(lst, cs):
                    codificados.setdefault(iv, []).append((destino, bytes(c) if c is not None else None))
            del imgs, trabajos
            for iv, (fila, app1, xmp, destinos) in enumerate(validas):
                datos_por_destino = dict(codificados.get(iv, []))
                if any(datos_por_destino.get(d) is None for d in destinos):
                    _a_cpu(fila)
                    continue
                # _CROP antes que el original, como el camino CPU.
                futuros = [pool.submit(_escribir, d, datos_por_destino[d], app1, xmp)
                           for d in destinos]
                pendientes.append((fila, futuros))
            cp.get_default_memory_pool().free_all_blocks()
            _drenar(False)
        _drenar(True)

    seg = time.monotonic() - stats["t0"]
    progress_callback.emit(
        f"\n[gpu] Imágenes RGB: {stats['gpu']} en GPU, {stats['cpu']} en CPU (fallback), "
        f"{seg:.1f} s — {total / seg if seg else 0:.1f} img/s.\n")
