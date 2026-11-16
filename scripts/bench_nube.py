"""Banco baseline del ATOM Organizer en Cloud Run Job (sin Rust).

Descarga un subconjunto determinista de N PARES (térmica + visible del mismo
disparo) de un corpus en GCS, y mide con los scripts de bench ya existentes:

1. `scripts/bench_rgb.py`            -> /work/baseline.json (+ perfil_rgb.csv)
2. `bench/perfilar_termicas.py N`    -> /work/perfil_termicas.json

Después escribe `meta.json` (hardware, corpus, s/GB, img/s por etapa) y sube
todo a `--salida` (gs://datos_para_organizar/_bench/..., NUNCA gs://plantas_pv_nl).

Emparejado: la MISMA clave que usa el Organizer (`atom_core.pares.clave_pareja`,
idx DJI + sufijo T/W/V). Un idx repetido en un lado se descarta (igual que
`emparejar_por_idx`: es ambiguo), nunca se adivina.

NOTA: `organize_cli.py --solo-plan` solo existe con `--fase copiar` (no planifica
un corpus), así que el "plan" se valida con la paridad T/W de `clave_pareja`.

Descarga: secuencial (o, como mucho, 4 hilos). Nunca masiva.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_RAIZ = Path(__file__).resolve().parent.parent
for _p in (_RAIZ / "src", _RAIZ):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_EXT_IMG = (".jpg", ".jpeg")
_MAX_HILOS = 4
_PREFIJO_PROHIBIDO = "gs://plantas_pv_nl"


class BenchError(RuntimeError):
    """Error visible del banco (sin fallbacks silenciosos)."""


# --------------------------------------------------------------------------
# Funciones puras
# --------------------------------------------------------------------------

def parse_gs(uri: str) -> "tuple[str, str]":
    if not uri.startswith("gs://"):
        raise BenchError(f"URI no gs://: {uri}")
    resto = uri[5:]
    bucket, _, prefijo = resto.partition("/")
    if not bucket:
        raise BenchError(f"URI sin bucket: {uri}")
    return bucket, prefijo


def validar_salida(uri: str) -> None:
    if uri.rstrip("/").startswith(_PREFIJO_PROHIBIDO):
        raise BenchError("la salida no puede ir a gs://plantas_pv_nl (solo plantas)")
    parse_gs(uri)


def seleccionar_pares(nombres: "list[str]", n_pares: int) -> "list[tuple[str, str]]":
    """Primeros `n_pares` pares (térmica, visible) por orden de nombre de la térmica.

    Pareja por `clave_pareja` (idx DJI). Los idx duplicados en un lado se
    descartan. Si no hay `n_pares` pares completos lanza BenchError."""
    from atom_core.pares import clave_pareja  # noqa: PLC0415

    ts: "dict[int, list[str]]" = {}
    ws: "dict[int, list[str]]" = {}
    for nombre in sorted(set(nombres)):
        if not nombre.lower().endswith(_EXT_IMG):
            continue
        c = clave_pareja(nombre)
        if c is None:
            continue
        (ts if c[1] == "T" else ws).setdefault(c[0], []).append(nombre)
    pares = [(ts[i][0], ws[i][0]) for i in ts
             if len(ts[i]) == 1 and len(ws.get(i, [])) == 1]
    pares.sort(key=lambda p: p[0])
    validar_n_pares(len(pares), n_pares)
    return pares[:n_pares]


def validar_n_pares(disponibles: int, pedidos: int) -> None:
    if pedidos <= 0:
        raise BenchError(f"--pares debe ser > 0 (recibido {pedidos})")
    if disponibles < pedidos:
        raise BenchError(
            f"el corpus solo tiene {disponibles} pares T+V completos y se piden {pedidos}")


def segundos_por_gb(segundos: float, bytes_corpus: int) -> "float | None":
    if not bytes_corpus or segundos is None:
        return None
    return segundos / (bytes_corpus / 1e9)


def imagenes_por_segundo(n_imagenes: int, segundos: float) -> "float | None":
    if not segundos or not n_imagenes:
        return None
    return n_imagenes / segundos


def metricas_etapas(etapas: "dict[str, float]", bytes_corpus: int,
                    n_pares: int, n_rgb: "int | None" = None) -> "dict[str, dict]":
    """s/GB e img/s por etapa. Imágenes: RGB = n_rgb (CSV de perfil) o n_pares;
    el resto de etapas = n_pares."""
    salida = {}
    for etapa, seg in etapas.items():
        n = (n_rgb or n_pares) if etapa == "RGB" else n_pares
        salida[etapa] = {
            "segundos": seg,
            "s_por_gb": segundos_por_gb(seg, bytes_corpus),
            "img_por_s": imagenes_por_segundo(n, seg),
        }
    return salida


def ahora_madrid() -> str:
    return datetime.now(ZoneInfo("Europe/Madrid")).isoformat(timespec="seconds")


def memoria_bytes() -> "int | None":
    for ruta in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = Path(ruta).read_text().strip()
            if v.isdigit() and int(v) < 1 << 50:
                return int(v)
        except OSError:
            pass
    try:
        for linea in Path("/proc/meminfo").read_text().splitlines():
            if linea.startswith("MemTotal:"):
                return int(linea.split()[1]) * 1024
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------
# GCS
# --------------------------------------------------------------------------

def _cliente():
    from google.cloud import storage  # noqa: PLC0415
    return storage.Client()


def listar(cliente, uri: str) -> "dict[str, int]":
    """{nombre_base: tamaño} de los objetos DIRECTOS bajo `uri` (solo metadatos)."""
    bucket, prefijo = parse_gs(uri)
    if prefijo and not prefijo.endswith("/"):
        prefijo += "/"
    return {b.name[len(prefijo):]: int(b.size or 0)
            for b in cliente.list_blobs(bucket, prefix=prefijo, delimiter="/")
            if b.name != prefijo}


def descargar(cliente, uri_dir: str, nombres: "list[str]", destino: Path,
              hilos: int = 1) -> int:
    """Descarga SOLO `nombres`. Devuelve bytes totales. Máx. 4 hilos."""
    hilos = max(1, min(hilos, _MAX_HILOS))
    bucket_n, prefijo = parse_gs(uri_dir)
    if prefijo and not prefijo.endswith("/"):
        prefijo += "/"
    bucket = cliente.bucket(bucket_n)
    destino.mkdir(parents=True, exist_ok=True)

    def una(nombre: str) -> int:
        ruta = destino / nombre
        bucket.blob(prefijo + nombre).download_to_filename(str(ruta))
        return ruta.stat().st_size

    if hilos == 1:
        return sum(una(n) for n in nombres)
    with ThreadPoolExecutor(max_workers=hilos) as ex:
        return sum(ex.map(una, nombres))


def subir(cliente, carpeta: Path, uri_salida: str) -> "list[str]":
    bucket_n, prefijo = parse_gs(uri_salida)
    prefijo = prefijo.rstrip("/")
    bucket = cliente.bucket(bucket_n)
    subidos = []
    for f in sorted(carpeta.iterdir()):
        if f.is_file():
            clave = f"{prefijo}/{f.name}" if prefijo else f.name
            bucket.blob(clave).upload_from_filename(str(f))
            subidos.append(f"gs://{bucket_n}/{clave}")
    return subidos


# --------------------------------------------------------------------------
# Ejecución
# --------------------------------------------------------------------------

def _ejecutar(cmd: "list[str]", env: "dict | None" = None) -> None:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, env={**os.environ, **(env or {})})
    if r.returncode != 0:
        raise BenchError(f"falló (exit {r.returncode}): {' '.join(cmd)}")


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Banco baseline del Organizer en Cloud Run Job.")
    p.add_argument("--corpus", required=True, help="gs://bucket/carpeta con las JPG crudas.")
    p.add_argument("--estadillo", required=True, help="gs://…/estadillo.csv")
    p.add_argument("--pares", type=int, default=150)
    p.add_argument("--repeticiones", type=int, default=3)
    p.add_argument("--n-termicas", dest="n_termicas", type=int, default=8)
    p.add_argument("--salida", required=True, help="gs://datos_para_organizar/_bench/…")
    p.add_argument("--work", default="/work")
    p.add_argument("--hilos-descarga", dest="hilos", type=int, default=1,
                   help=f"1 (secuencial) a {_MAX_HILOS}.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = _parse_args(argv)
    validar_salida(a.salida)
    work = Path(a.work)
    origen, destino_rgb, destino_t = work / "origen", work / "destino_rgb", work / "destino_term"
    work.mkdir(parents=True, exist_ok=True)
    inicio = ahora_madrid()
    cli = _cliente()

    tamanos = listar(cli, a.corpus)
    pares = seleccionar_pares(list(tamanos), a.pares)
    nombres = [n for par in pares for n in par]
    print(f"corpus: {len(tamanos)} objetos, {len(pares)} pares elegidos "
          f"({pares[0][0]} .. {pares[-1][0]})", flush=True)
    bytes_corpus = descargar(cli, a.corpus, nombres, origen, a.hilos)

    est_bucket, est_clave = parse_gs(a.estadillo)
    estadillo = work / Path(est_clave).name
    cli.bucket(est_bucket).blob(est_clave).download_to_filename(str(estadillo))

    # "Plan": paridad T/W real sobre lo descargado (organize_cli --solo-plan no aplica).
    from atom_core.pares import paridad_tw  # noqa: PLC0415
    t_sin_w, w_sin_t = paridad_tw([str(p) for p in origen.iterdir()])
    if t_sin_w or w_sin_t:
        raise BenchError(f"el Organizer no empareja el corpus: T sin W={t_sin_w[:5]} W sin T={w_sin_t[:5]}")

    perfil_csv = work / "perfil_rgb.csv"
    _ejecutar([sys.executable, str(_RAIZ / "scripts" / "bench_rgb.py"),
               "--origen", str(origen), "--destino", str(destino_rgb),
               "--estadillo", str(estadillo), "--perfil-csv", str(perfil_csv),
               "--repeticiones", str(a.repeticiones), "--json", str(work / "baseline.json")],
              env={"ORGANIZER_PERFIL_RGB": str(perfil_csv)})

    destino_t.mkdir(parents=True, exist_ok=True)
    _ejecutar([sys.executable, str(_RAIZ / "bench" / "perfilar_termicas.py"), str(a.n_termicas),
               "--origen", str(origen), "--destino", str(destino_t),
               "--estadillo", str(estadillo), "--sin-rotacion",
               "--perfil-json", str(work / "perfil_termicas.json")])

    informe = json.loads((work / "baseline.json").read_text())
    etapas = informe.get("etapas_minimo", {})
    meta = {
        "inicio_madrid": inicio, "fin_madrid": ahora_madrid(),
        "vcpu": os.cpu_count(), "memoria_bytes": memoria_bytes(),
        "imagen": os.environ.get("BENCH_IMAGE"),
        "corpus": a.corpus, "estadillo": a.estadillo,
        "n_pares": len(pares), "n_ficheros": len(nombres),
        "gb_corpus": bytes_corpus / 1e9,
        "repeticiones": a.repeticiones, "n_termicas": a.n_termicas,
        "pares_primero_ultimo": [pares[0][0], pares[-1][0]],
        "metricas_etapas": metricas_etapas(etapas, bytes_corpus, len(pares),
                                           informe.get("n_imagenes") or None),
    }
    (work / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    # Solo se suben los resultados, no el corpus ni los destinos.
    res = work / "resultados"
    res.mkdir(exist_ok=True)
    for nombre in ("baseline.json", "perfil_rgb.csv", "perfil_termicas.json", "meta.json"):
        f = work / nombre
        if not f.exists():
            raise BenchError(f"falta el resultado {nombre}")
        (res / nombre).write_bytes(f.read_bytes())
    print("subido:", subir(cli, res, a.salida), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BenchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
