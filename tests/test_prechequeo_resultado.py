"""Tests del prechequeo de resultado (solo lectura) con un destino organizado falso."""
import datetime

import pytest

from atom_core import prechequeo_resultado as pre
from atom_core.manifiesto import FilaManifiesto, Manifiesto

FECHA = "2026-10-06"


def _estado(res, id_):
    return next(c for c in res["checks"] if c["id"] == id_)


def _fila(destino, nombre, hora, tipo="TERMICA"):
    ruta = destino / "PB1_V1" / nombre
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(b"x")
    return FilaManifiesto(
        ruta_origen=f"/origen/{nombre}", tipo=tipo, timestamp_exif=f"{FECHA}T{hora}",
        modelo="M", pb="1", vuelo="1", nombre_nuevo=nombre, angulo_giro=0, pct_recorte=None,
        comprime=False, ruta_salida_original=str(ruta), ruta_salida_crop=None,
        ruta_salida_tiff=None, unassigned=False)


def _destino(tmp_path, horas=("13:47:00", "13:48:00"), csv_filas=None, csv_cabecera_hora=True,
             fuera_ventana_csv=0):
    """Destino organizado: 1 vuelo (13:46:57-13:51:14), una imagen T por hora."""
    destino = tmp_path / "destino"
    (destino / ".organizado").mkdir(parents=True)
    man = Manifiesto(destino / ".organizado" / "manifiesto.db")
    man.crear_esquema()
    nombres = []
    filas = []
    for h in horas:
        nombre = f"DJI_{FECHA.replace('-', '')}{h.replace(':', '')}_T.JPG"
        nombres.append(nombre)
        filas.append(_fila(destino, nombre, h))
    man.insertar_o_reabrir(filas)
    for f in man.todas():
        man.marcar_hecha(f["id"], "original=ok")
    man.cerrar()

    est = destino / "ESTADILLOS"
    est.mkdir()
    (est / "e.csv").write_text(
        "Fecha;Piloto;Equipo_de_vuelo;Hora_de_inicio;Hora_final;PB;Vuelo\n"
        "2026:10:06;P;E;13:46:57;13:51:14;1;1\n", encoding="utf-8")

    csvs = destino / "CSVs"
    csvs.mkdir()
    lineas = csv_filas if csv_filas is not None else [f"{n},1,2" for n in nombres]
    (csvs / "PB1_V1_meta.csv").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return destino


def test_destino_no_organizado_error(tmp_path):
    vacio = tmp_path / "vacio"
    vacio.mkdir()
    res = pre.prechequear(str(vacio))
    assert res["ok"] is False
    assert _estado(res, "manifiesto")["estado"] == "error"
    otros = [c for c in res["checks"] if c["id"] != "manifiesto"]
    assert {c["id"] for c in otros} == {i for i, _ in pre._ORDEN}
    assert all(c["estado"] == "no_comprobable" for c in otros)
    assert not (vacio / ".organizado").exists()  # solo lectura


def test_destino_inexistente_error(tmp_path):
    res = pre.prechequear(str(tmp_path / "nope"))
    assert res["ok"] is False
    assert _estado(res, "manifiesto")["estado"] == "error"


def test_todo_ok(tmp_path):
    destino = _destino(tmp_path)
    def _arbol():
        # -shm/-wal: los crea `subida_resultado.sin_ordenar_ultima_ejecucion` (mode=ro sobre WAL),
        # ajeno a este modulo; se ignoran aqui.
        return sorted(p.relative_to(destino).as_posix() for p in destino.rglob("*")
                      if not p.name.endswith(("-shm", "-wal")))

    antes = _arbol()
    res = pre.prechequear(str(destino))
    detalle = {c["id"]: (c["estado"], c["detalle"]) for c in res["checks"]}
    for id_ in ("manifiesto", "estadillo", "csv_recuentos", "imagenes_en_ventana", "horas_csv"):
        assert detalle[id_][0] == "ok", detalle[id_]
    assert not [c for c in res["checks"] if c["estado"] == "error"], detalle
    assert res["ok"] is True
    despues = _arbol()
    assert antes == despues  # nunca escribe


def test_horas_csv_aviso_con_5_ejemplos_y_conteo(tmp_path):
    horas = ("13:47:00", "13:48:00")
    destino = _destino(tmp_path, horas=horas)
    fuera = [f"DJI_20261006_15{m:02d}00_T.JPG,1,2" for m in range(7)]  # 7 fuera de ventana
    (destino / "CSVs" / "PB1_V1_meta.csv").write_text(
        "\n".join([f"DJI_20261006134700_T.JPG,1,2", f"DJI_20261006134800_T.JPG,1,2"] + fuera) + "\n",
        encoding="utf-8")
    res = pre.prechequear(str(destino))
    c = _estado(res, "horas_csv")
    assert c["estado"] == "aviso"
    assert "7 imagen(es) de CSV fuera" in c["detalle"]
    assert c["detalle"].count("PB1_V1_meta.csv: DJI_") == 5
    assert "(y 2 más)" in c["detalle"]


def test_horas_csv_error_sin_hora(tmp_path):
    destino = _destino(tmp_path)
    (destino / "CSVs" / "PB1_V1_meta.csv").write_text("a,b,c\nfoto_sin_hora.jpg,1,2\n", encoding="utf-8")
    res = pre.prechequear(str(destino))
    c = _estado(res, "horas_csv")
    assert c["estado"] == "error"
    assert "sin hora legible" in c["detalle"]
    assert res["ok"] is False


def test_horas_csv_error_csv_sin_vuelo(tmp_path):
    destino = _destino(tmp_path)
    (destino / "CSVs" / "OTRA_meta.csv").write_text("DJI_20261006134700_T.JPG,1\n", encoding="utf-8")
    c = _estado(pre.prechequear(str(destino)), "horas_csv")
    assert c["estado"] == "error"
    assert "OTRA_meta.csv" in c["detalle"]


def test_csv_recuentos_descuadre(tmp_path):
    destino = _destino(tmp_path, csv_filas=["DJI_20261006134700_T.JPG,1,2"])  # 1 fila, 2 imágenes
    res = pre.prechequear(str(destino))
    c = _estado(res, "csv_recuentos")
    assert c["estado"] == "error"
    assert "no cuadran" in c["detalle"]
    assert res["ok"] is False


def test_tiff_crop_no_comprobable(tmp_path):
    res = pre.prechequear(str(_destino(tmp_path)))
    assert _estado(res, "tiff_crop")["estado"] == "no_comprobable"


def test_imagen_fuera_de_ventana_aviso(tmp_path):
    destino = _destino(tmp_path, horas=("13:47:00", "18:00:00"))
    c = _estado(pre.prechequear(str(destino)), "imagenes_en_ventana")
    assert c["estado"] == "aviso"
    assert "1 imagen(es) fuera" in c["detalle"]


def test_check_que_lanza_no_tumba_prechequear(tmp_path, monkeypatch):
    destino = _destino(tmp_path)

    def roto(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(pre, "_ORDEN", tuple((i, roto if i == "giro" else f) for i, f in pre._ORDEN))
    res = pre.prechequear(str(destino))
    giro = _estado(res, "giro")
    assert giro["estado"] == "error"
    assert "RuntimeError" in giro["detalle"] and "boom" in giro["detalle"]
    assert res["ok"] is False
    assert _estado(res, "horas_csv")["estado"] == "ok"  # los demás siguen
