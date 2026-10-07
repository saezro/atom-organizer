"""`atom_core.subida_resultado`: selección por modo, partición, subida y disparo
automático. Todo con ficheros temporales y el GCS falso de `test_cloud_upload`:
nunca red real."""
from __future__ import annotations

from pathlib import Path

import pytest

from atom_core import cloud_upload as cu
from atom_core import subida_resultado as sr
from atom_core.manifiesto import Manifiesto, NOMBRE_CARPETA_MANIFIESTO, NOMBRE_FICHERO_MANIFIESTO

PREFIJO = "KL05/INSPECCIONES/TERMICA_MODULOS/2026/"


def _arbol(raiz: Path, ficheros: dict[str, bytes]) -> Path:
    for rel, datos in ficheros.items():
        p = raiz / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(datos)
    return raiz


SALIDA_TIPICA = {
    "TERMICA/PB1/V1/A_T.tiff": b"t1",
    "TERMICA/PB1/V1/A_T.JPG": b"t2",
    "RGB/PB1/V1/A_W_CROP.JPG": b"c1",
    "RGB/PB1/V1/A_W.JPG": b"w1",
    "CSVs/V1_meta.csv": b"m",
    "CSVs/V1_location.csv": b"l",
    "CSVs/_criterio/V1_criterio.csv": b"k",
    "CSVs/otro.csv": b"o",
    "ESTADILLOS/estadillo.xlsx": b"e",
    "INDICE_KL05.xlsx": b"i",
    "LOGS/run.log": b"log",
    "RGB_Extra/PB1/V1/X.JPG": b"x",
    "Thumbs.db": b"basura",
}


@pytest.mark.parametrize("rel,esperado", [
    ("TERMICA/PB1/V1/A_T.tiff", sr.URGENTE),
    ("TERMICA/PB1/V1/A_T.TIFF", sr.URGENTE),
    ("RGB/PB1/V1/A_W_CROP.JPG", sr.URGENTE),
    ("CSVs/V1_meta.csv", sr.URGENTE),
    ("CSVs/V1_location.csv", sr.URGENTE),
    ("ESTADILLOS/estadillo.xlsx", sr.URGENTE),
    ("TERMICA/PB1/V1/A_T.JPG", sr.NORMAL),
    ("RGB/PB1/V1/A_W.JPG", sr.NORMAL),
    ("INDICE_KL05.xlsx", sr.NORMAL),
    ("INDICE_KL05_20261007_101500.xlsx", sr.NORMAL),
    ("CSVs/_criterio/V1_criterio.csv", sr.NORMAL),
    ("CSVs/otro.csv", None),
    ("CSVs/sub/V1_meta.csv", None),
    ("ESTADILLOS/sub/x.xlsx", None),
    ("RGB_Extra/PB1/V1/X.JPG", None),
    ("RGB/PB1/V1/A_Z.JPG", None),
    ("TERMICA/PB1/V1/A_T.png", None),
    ("RGB/A_W.JPG", sr.NORMAL),
    ("suelto.txt", None),
    ("LOGS/run.log", None),
    (".organizado/manifiesto.db", None),
])
def test_clasificar(rel, esperado):
    assert sr.clasificar(rel) == esperado


@pytest.mark.parametrize("rel,esperado", [
    ("CSVs/V1_meta.csv", True),
    ("CSVs/_criterio/V1_criterio.csv", True),
    ("ESTADILLOS/estadillo.xlsx", True),
    ("INDICE_KL05.xlsx", True),
    ("TERMICA/PB1/V1/A_T.tiff", False),
    ("RGB/PB1/V1/A_W_CROP.JPG", False),
    ("SIN_ORDENAR/RGB/B_W.JPG", False),
    ("MI_INDICE_KL05.xlsx", False),
])
def test_es_sobrescribible(rel, esperado):
    assert sr.es_sobrescribible(rel) is esperado


def _manifiesto_con_sin_ordenar(raiz: Path) -> None:
    """Dos ejecuciones: la 1ª dejó A.JPG sin asignar, la 2ª (última) B.JPG y su crop."""
    (raiz / NOMBRE_CARPETA_MANIFIESTO).mkdir(parents=True, exist_ok=True)
    m = Manifiesto(raiz / NOMBRE_CARPETA_MANIFIESTO / NOMBRE_FICHERO_MANIFIESTO)
    m.crear_esquema()
    e1 = m.abrir_ejecucion("/origen1", "3.4.115")
    e2 = m.abrir_ejecucion("/origen2", "3.4.115")
    con = m._conexion()
    filas = [
        ("a", e1, "SIN_ORDENAR/RGB/A_W.JPG", None, 1, "hecho"),
        ("b", e2, "SIN_ORDENAR/RGB/B_W.JPG", "SIN_ORDENAR/RGB/B_W_CROP.JPG", 1, "hecho"),
        ("c", e2, "RGB/PB1/V1/C_W.JPG", None, 0, "hecho"),
        ("d", e2, "SIN_ORDENAR/RGB/D_W.JPG", None, 1, "fallido"),
    ]
    with con:
        for clave, ej, orig, crop, unas, estado in filas:
            con.execute(
                "INSERT INTO imagenes (ruta_origen, clave, tipo, ruta_salida_original, "
                "ruta_salida_crop, unassigned, estado, ejecucion_id) VALUES (?,?,?,?,?,?,?,?)",
                (f"/o/{clave}", clave, "RGB", str(raiz / orig),
                 str(raiz / crop) if crop else None, unas, estado, ej))
    m.cerrar()


def test_sin_ordenar_ultima_ejecucion_solo_hechas_sin_asignar_de_la_ultima(tmp_path):
    _arbol(tmp_path, {"SIN_ORDENAR/RGB/A_W.JPG": b"a", "SIN_ORDENAR/RGB/B_W.JPG": b"b",
                      "SIN_ORDENAR/RGB/B_W_CROP.JPG": b"bc", "SIN_ORDENAR/RGB/D_W.JPG": b"d"})
    _manifiesto_con_sin_ordenar(tmp_path)
    rels, avisos = sr.sin_ordenar_ultima_ejecucion(tmp_path)
    assert rels == {"SIN_ORDENAR/RGB/B_W.JPG", "SIN_ORDENAR/RGB/B_W_CROP.JPG"}
    assert avisos == []


def test_sin_manifiesto_avisa_y_no_selecciona_nada(tmp_path):
    rels, avisos = sr.sin_ordenar_ultima_ejecucion(tmp_path)
    assert rels == set()
    assert len(avisos) == 1 and "manifiesto" in avisos[0].lower()


def test_seleccionar_reparte_urgentes_y_resto_y_cuenta_sin_clasificar(tmp_path):
    # El conftest crea `tmp_path/Logs-subidas/subidas.log`: la salida va en una subcarpeta.
    tmp_path = tmp_path / "salida"
    _arbol(tmp_path, {**SALIDA_TIPICA, "SIN_ORDENAR/RGB/A_W.JPG": b"a",
                      "SIN_ORDENAR/RGB/B_W.JPG": b"b", "SIN_ORDENAR/RGB/B_W_CROP.JPG": b"bc"})
    _manifiesto_con_sin_ordenar(tmp_path)
    sel = sr.seleccionar(tmp_path, PREFIJO)
    rem = lambda items: sorted(i.remote.removeprefix(PREFIJO) for i in items)  # noqa: E731
    assert rem(sel.urgentes) == [
        "CSVs/V1_location.csv", "CSVs/V1_meta.csv", "ESTADILLOS/estadillo.xlsx",
        "RGB/PB1/V1/A_W_CROP.JPG", "TERMICA/PB1/V1/A_T.tiff"]
    assert rem(sel.resto) == [
        "CSVs/_criterio/V1_criterio.csv", "INDICE_KL05.xlsx", "RGB/PB1/V1/A_W.JPG",
        "SIN_ORDENAR/RGB/B_W.JPG", "SIN_ORDENAR/RGB/B_W_CROP.JPG", "TERMICA/PB1/V1/A_T.JPG"]
    # CSVs/otro.csv y RGB_Extra/...: ni urgentes ni resto, pero contados.
    assert sel.sin_clasificar == 2
    assert any("sin clasificar" in a.lower() for a in sel.avisos)
    # Nunca .organizado, LOGS ni basura del sistema.
    todos = {i.remote for i in sel.urgentes + sel.resto}
    assert not any(".organizado" in r or "/LOGS/" in r or "Thumbs" in r for r in todos)
    # SIN_ORDENAR/RGB/A_W.JPG (1ª ejecución) NO se sube.
    assert PREFIJO + "SIN_ORDENAR/RGB/A_W.JPG" not in todos


def test_seleccionar_prefijo_sin_barra_final_y_tamanos(tmp_path):
    _arbol(tmp_path, {"TERMICA/PB1/V1/A_T.tiff": b"12345"})
    sel = sr.seleccionar(tmp_path, PREFIJO.rstrip("/"))
    assert [(i.remote, i.size) for i in sel.urgentes] == [(PREFIJO + "TERMICA/PB1/V1/A_T.tiff", 5)]


def test_seleccionar_sin_manifiesto_avisa_pero_selecciona_lo_demas(tmp_path):
    _arbol(tmp_path, SALIDA_TIPICA)
    sel = sr.seleccionar(tmp_path, PREFIJO)
    assert len(sel.urgentes) == 5
    assert any("manifiesto" in a.lower() for a in sel.avisos)
