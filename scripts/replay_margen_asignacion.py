"""Replay SOLO LECTURA: ¿qué fotos de tandas reales cambiarían de destino si el
margen de asignación pasa de 30 s a 0 (franja exacta)? También lista falsos
positivos de la regla de coherencia (tiff/crop) sobre esos manifiestos.

Uso:
  python scripts/replay_margen_asignacion.py \
      --tanda COPIA_manifiesto.db=estadillo.csv [--tanda ...] \
      [--desfase-h 0] [--desfase-m 0] [--margen 30] [--salida /tmp/claude-1000/replay-margen.csv] \
      [--tif] [--crop]

Trabaja sobre COPIAS locales de los manifiestos (no sobre el destino vivo) y las
abre en modo ro. NO descarga masivas: una copia por tanda, a mano.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sqlite3
import sys
import types
from collections import Counter
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from atom_core import indice  # noqa: E402


def etiqueta_destino(ventana) -> str:
    if ventana is None:
        return "SIN_ORDENAR"
    if indice._es_ventana_generales(ventana):
        return "FOTOS_GENERALES"
    return f"PB{ventana['pb']}_V{ventana['vuelo']}"


def _etiqueta_manifiesto(fila) -> str:
    if fila["unassigned"] or not fila["pb"] or not fila["vuelo"]:
        return "SIN_ORDENAR"
    if str(fila["pb"]).upper() == "GENERALES":
        return "FOTOS_GENERALES"
    return f"PB{fila['pb']}_V{fila['vuelo']}"


def comparar_asignacion(filas, ventanas_con_margen, ventanas_exactas) -> dict:
    cambian, transiciones, en_borde, incoherentes = [], Counter(), [], []
    total = 0
    for fila in filas:
        if not fila["timestamp_exif"]:
            continue
        total += 1
        ts = dt.datetime.fromisoformat(fila["timestamp_exif"])
        v_m = indice.elegir_ventana(ts, ventanas_con_margen)
        v_e = indice.elegir_ventana(ts, ventanas_exactas)
        d_m, d_e = etiqueta_destino(v_m), etiqueta_destino(v_e)
        if d_m != _etiqueta_manifiesto(fila):
            incoherentes.append(fila["nombre_original"])
        for v in (v_m, v_e):
            if v is not None and ts in (v["inicio_exacto"], v["fin_exacto"]):
                en_borde.append(fila["nombre_original"])
                break
        if d_m != d_e:
            ref = v_m or {}
            fuera = 0
            if ref:
                fuera = int(max((ref["inicio_exacto"] - ts).total_seconds(),
                                (ts - ref["fin_exacto"]).total_seconds(), 0))
            cambian.append({"nombre": fila["nombre_original"], "tipo": fila["tipo"],
                            "timestamp": fila["timestamp_exif"], "destino_margen": d_m,
                            "destino_exacto": d_e, "seg_fuera": fuera})
            transiciones[f"{d_m} -> {d_e}"] += 1
    return {"total": total, "cambian": cambian, "transiciones": dict(transiciones),
            "en_borde": en_borde, "incoherentes_con_manifiesto": incoherentes}


def _ventanas_de_estadillo(ruta_estadillo, margen, desfase_h, desfase_m):
    import pipeline  # noqa: PLC0415
    from atom_core import estadillo as estadillo_mod  # noqa: PLC0415
    df = estadillo_mod.combinar_estadillos([ruta_estadillo])
    cols = indice._sin_utils_helper().get_nombres_columnas(list(df.columns.values))
    cfg = types.SimpleNamespace(seconds_range=margen, mismatch_hours=desfase_h, mismatch_minutes=desfase_m)
    cb = types.SimpleNamespace(emit=lambda *a, **k: None)
    pipe = types.SimpleNamespace(ventana_horaria_vuelo=lambda *a: pipeline.GenStructFolder.ventana_horaria_vuelo(None, *a))
    con_margen = indice._ventanas_por_vuelo(df, cols, pipe, cfg, cb)
    exactas = [{**v, "inicio": v["inicio_exacto"], "fin": v["fin_exacto"]} for v in con_margen]
    return con_margen, exactas


def _leer_filas(copia_db):
    con = sqlite3.connect(f"file:{copia_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return list(con.execute(
            "SELECT id, nombre_original, ruta_origen, tipo, timestamp_exif, pb, vuelo, unassigned, "
            "estado, ruta_salida_tiff, ruta_salida_crop FROM imagenes ORDER BY id"))
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tanda", action="append", required=True, metavar="MANIFIESTO=ESTADILLO")
    ap.add_argument("--margen", type=float, default=30.0)
    ap.add_argument("--desfase-h", type=int, default=0)
    ap.add_argument("--desfase-m", type=int, default=0)
    ap.add_argument("--salida", default="/tmp/claude-1000/replay-margen.csv")
    ap.add_argument("--tif", action="store_true", help="aplicar regla tiff != T en el chequeo de coherencia")
    ap.add_argument("--crop", action="store_true", help="aplicar regla crop != W en el chequeo de coherencia")
    args = ap.parse_args(argv)

    from atom_core.cierre import etiqueta_vuelo, recuentos_por_vuelo  # noqa: PLC0415
    cambios_csv = []
    for par in args.tanda:
        db, _, estad = par.partition("=")
        filas = _leer_filas(db)
        con_margen, exactas = _ventanas_de_estadillo(estad, args.margen, args.desfase_h, args.desfase_m)
        r = comparar_asignacion(filas, con_margen, exactas)
        print(f"\n== {db}")
        print(f"fotos con hora: {r['total']} | cambian de destino: {len(r['cambian'])} | "
              f"en borde exacto: {len(r['en_borde'])} | no reproduce manifiesto: {len(r['incoherentes_con_manifiesto'])}")
        for k, n in sorted(r["transiciones"].items()):
            print(f"  {k}: {n}")
        if r["incoherentes_con_manifiesto"]:
            print("  AVISO: el replay no reproduce la asignación real de "
                  f"{len(r['incoherentes_con_manifiesto'])} foto(s) (p.ej. estadillo multi-carpeta o desfase distinto): "
                  "tratar los números con cautela.")
        for c in r["cambian"]:
            cambios_csv.append({"tanda": db, **c})
        rec = recuentos_por_vuelo(filas, None, convert_to_tif=args.tif, cropping_rgb=args.crop)
        malos = [(k, v["motivo"]) for k, v in rec.items() if v["estado"] == "ERROR"]
        print(f"  coherencia (solo tiff/crop, sin CSV): {len(malos)} vuelo(s) ERROR de {len(rec)}")
        for (pb, vuelo), motivo in malos[:10]:
            print(f"    {etiqueta_vuelo(pb, vuelo)}: {motivo}")
    Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
    with open(args.salida, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["tanda", "nombre", "tipo", "timestamp", "destino_margen",
                                           "destino_exacto", "seg_fuera"])
        w.writeheader()
        w.writerows(cambios_csv)
    print(f"\nLista completa de fotos que cambian: {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
