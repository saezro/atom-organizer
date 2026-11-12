"""`propuesta.json` (contrato v1): función pura y CLI."""
import json
import os

import pytest

from atom_core.indice import VERSION_ALGORITMO_GIRO as V
from atom_core.manifiesto import FilaManifiesto, Manifiesto
from atom_core.propuesta import ErrorPropuesta, construir_propuesta, main

GEN = "2026-11-12T10:00:00Z"


def _fila(ruta, tipo, ts, pb, vuelo, angulo=0, unassigned=False, bytes_origen=100):
    return FilaManifiesto(
        ruta_origen=ruta, tipo=tipo, timestamp_exif=ts, modelo=None, pb=pb, vuelo=vuelo,
        nombre_nuevo=os.path.basename(ruta), angulo_giro=angulo, pct_recorte=None, comprime=False,
        ruta_salida_original="/o/" + os.path.basename(ruta), ruta_salida_crop=None,
        ruta_salida_tiff=None, unassigned=unassigned, bytes_origen=bytes_origen)


def _vuelo(pb, vuelo, angulo, estado="ok", motivo=None, sufijo=""):
    return dict(pb=pb, vuelo=vuelo, sufijo=sufijo, angulo=angulo, n_total=4, n_90=0, n_270=4, n_0=0,
                pct_90=0.0, pct_270=100.0, pct_0=0.0, pct_ganadora=100.0, estado=estado,
                motivo=motivo, version_algoritmo=V)


def _par(dir_, idx, hora, pb, vuelo, angulo):
    n = f"DJI_20261108{hora}_{idx:04d}"
    return [_fila(f"{dir_}/{n}_T.JPG", "TERMICA", hora_iso(hora), pb, vuelo, angulo),
            _fila(f"{dir_}/{n}_W.JPG", "RGB", hora_iso(hora), pb, vuelo, angulo)]


def hora_iso(h):
    return f"2026-11-08T{h[:2]}:{h[2:4]}:{h[4:6]}"


def _resumen(**kw):
    r = {"version": 1, "ejecucion_indice": "e1", "hash_listado": "h", "n_imagenes": 99,
         "n_pares": 5, "n_pb": 3, "n_vuelos": 3, "n_sin_ordenar": 3,
         "pb": [{"pb": "24", "vuelo": "1", "imagenes": 4, "pares": 2},
                {"pb": "24", "vuelo": "2", "imagenes": 2, "pares": 1},
                {"pb": "25", "vuelo": "1", "imagenes": 2, "pares": 1},
                {"pb": "GENERALES", "vuelo": "1", "imagenes": 2, "pares": 0}],
         "fuera_de_orden": [{"motivo": "fuera-de-ventana", "n": 2, "ejemplos": ["x"]},
                            {"motivo": "sin-clasificar", "n": 1, "ejemplos": ["y"]},
                            {"motivo": "no-dji", "n": 7, "ejemplos": ["a.txt", "b.txt"]}]}
    r.update(kw)
    return r


def _crear(tmp_path, vuelos=None, filas_extra=(), con_sin_ordenar=True):
    db = str(tmp_path / "m.db")
    m = Manifiesto(db)
    m.crear_esquema()
    filas = []
    filas += _par("/in/A", 1, "104800", "24", "1", 270)
    filas += _par("/in/A", 2, "111130", "24", "1", 270)
    filas += _par("/in/B", 1, "130000", "24", "2", 90)[:2]
    filas += _par("/in/C", 1, "090000", "25", "1", 0)
    filas += [_fila("/in/G/g1.JPG", "RGB", None, "GENERALES", "1"),
              _fila("/in/G/g2.JPG", "RGB", None, "GENERALES", "1")]
    if con_sin_ordenar:
        filas += [_fila(f"/in/S/s{i}.JPG", "RGB", "2026-11-09T01:00:0%d" % i, None, None,
                        unassigned=True) for i in range(3)]
    filas += list(filas_extra)
    m.insertar_muchas(filas)
    m.guardar_vuelos(vuelos if vuelos is not None else [
        _vuelo("24", "1", 270), _vuelo("24", "2", 90),
        _vuelo("25", "1", 0, "sin_consenso", "ninguna rotacion supera el 80%")])
    con = m._conexion()
    rows = con.execute("SELECT clave, ruta_origen FROM imagenes WHERE unassigned=1 "
                       "ORDER BY ruta_origen").fetchall()
    m.guardar_sin_colocar(1, [], [{"grupo_id": "g1", "motivo": "fuera-de-ventana",
                                   "claves": [rows[0]["clave"], rows[1]["clave"]]}]) if rows else None
    con.execute("UPDATE imagenes SET categoria='fotos_generales' WHERE pb='GENERALES'")
    con.execute("UPDATE imagenes SET categoria='sin_ordenar', motivo_fuera='sin-clasificar' "
                "WHERE unassigned=1")
    for r in rows[:2]:
        con.execute("UPDATE imagenes SET motivo_fuera='fuera-de-ventana' WHERE clave=?", (r["clave"],))
    con.commit()
    m.cerrar()
    return db


@pytest.fixture
def manifiesto_sintetico(tmp_path):
    return _crear(tmp_path)


def _v(p, pb, vuelo):
    return next(x for x in p["vuelos"] if (x["pb"], x["vuelo"]) == (pb, vuelo))


def test_vuelo_ok_copia_giro_y_horas(manifiesto_sintetico):
    p = construir_propuesta(manifiesto_sintetico, _resumen(), "TOCOPILLA", GEN)
    v = _v(p, "24", "1")
    assert p["version"] == 1 and p["planta"] == "TOCOPILLA" and p["generado"] == GEN
    assert v["angulo"] == 270 and v["estado"] == "ok" and v["motivo"] is None
    assert v["inicio_exif"] == "2026-11-08T10:48:00" and v["fin_exif"] == "2026-11-08T11:11:30"
    assert (v["n_pares"], v["n_rgb"], v["n_termica"]) == (2, 2, 2)
    assert v["bytes"] == 400 and v["n_con_exif"] == 4 and v["n_exif_invalido"] == 0
    assert v["version_algoritmo"] == V
    assert p["totales"]["n_imagenes"] == 99 and p["totales"]["n_vuelos"] == 3
    assert p["totales"]["bytes"] == 100 * 13
    assert [(x["pb"], x["vuelo"]) for x in p["vuelos"]] == [("25", "1"), ("24", "1"), ("24", "2")]


def test_sin_consenso_conserva_estado_y_motivo(manifiesto_sintetico):
    v = _v(construir_propuesta(manifiesto_sintetico, _resumen(), "T", GEN), "25", "1")
    assert v["estado"] == "sin_consenso" and "80%" in v["motivo"]


def test_vuelo_sin_fila_en_vuelos_es_error_visible(tmp_path):
    db = _crear(tmp_path, vuelos=[_vuelo("24", "1", 270), _vuelo("24", "2", 90)])
    v = _v(construir_propuesta(db, _resumen(), "T", GEN), "25", "1")
    assert v["estado"] == "sin_registro_giro" and v["motivo"] and v["angulo"] == 0


def test_sufijo_no_vacio_lanza(tmp_path):
    db = _crear(tmp_path, vuelos=[_vuelo("24", "1", 270, sufijo="_20261108")])
    with pytest.raises(ErrorPropuesta, match="sufijo"):
        construir_propuesta(db, _resumen(), "T", GEN)


def test_angulo_invalido_lanza(tmp_path):
    db = _crear(tmp_path, vuelos=[_vuelo("24", "1", 45), _vuelo("24", "2", 90), _vuelo("25", "1", 0)])
    with pytest.raises(ErrorPropuesta, match="ángulo"):
        construir_propuesta(db, _resumen(), "T", GEN)


def test_exif_fraccion_zona_y_nulo(tmp_path):
    extra = [_fila("/in/D/DJI_20261108000000_0001_T.JPG", "TERMICA", "2026-11-08T10:48:00.500", "30", "1"),
             _fila("/in/D/DJI_20261108000001_0002_T.JPG", "TERMICA", "2026-11-08T10:49:00+02:00", "30", "1"),
             _fila("/in/D/DJI_20261108000002_0003_T.JPG", "TERMICA", None, "30", "1")]
    db = _crear(tmp_path, filas_extra=extra,
                vuelos=[_vuelo("24", "1", 270), _vuelo("24", "2", 90), _vuelo("25", "1", 0), _vuelo("30", "1", 0)])
    r = _resumen()
    r["pb"].append({"pb": "30", "vuelo": "1", "imagenes": 3, "pares": 0})
    v = _v(construir_propuesta(db, r, "T", GEN), "30", "1")
    assert v["inicio_exif"] == "2026-11-08T10:48:00" and v["n_exif_invalido"] == 1 and v["n_con_exif"] == 1


def test_vuelo_sin_exif_inicio_null(tmp_path):
    extra = [_fila("/in/D/DJI_20261108000000_0001_T.JPG", "TERMICA", None, "30", "1")]
    db = _crear(tmp_path, filas_extra=extra,
                vuelos=[_vuelo("24", "1", 270), _vuelo("24", "2", 90), _vuelo("25", "1", 0), _vuelo("30", "1", 0)])
    r = _resumen()
    r["pb"].append({"pb": "30", "vuelo": "1", "imagenes": 1, "pares": 0})
    p = construir_propuesta(db, r, "T", GEN)
    v = _v(p, "30", "1")
    assert v["inicio_exif"] is None and v["fin_exif"] is None and v["n_con_exif"] == 0
    assert p["vuelos"][-1] is v


def test_fuera_de_vuelo_generales_sin_ordenar_y_no_indexadas(manifiesto_sintetico):
    p = construir_propuesta(manifiesto_sintetico, _resumen(), "T", GEN)
    cats = {(f["categoria"], f["motivo"]): f for f in p["fuera_de_vuelo"]}
    assert cats[("fotos_generales", "pb-generales")]["n_imagenes"] == 2
    assert cats[("fotos_generales", "pb-generales")]["pb"] == "GENERALES"
    assert cats[("sin_ordenar", "fuera-de-ventana")]["n_imagenes"] == 2
    assert cats[("sin_ordenar", "sin-clasificar")]["n_imagenes"] == 1
    assert cats[("sin_ordenar", "sin-clasificar")]["ejemplos"] == ["S/s2.JPG"]
    nd = cats[("sin_ordenar", "no-dji")]
    assert nd["indexada"] is False and nd["bytes"] is None and nd["n_imagenes"] == 7
    assert all(len(f["ejemplos"]) <= 5 for f in p["fuera_de_vuelo"])


def test_incoherencia_con_resumen_lanza(manifiesto_sintetico):
    r = _resumen()
    r["pb"][0]["pares"] = 9
    with pytest.raises(ErrorPropuesta, match="pares"):
        construir_propuesta(manifiesto_sintetico, r, "T", GEN)
    with pytest.raises(ErrorPropuesta, match="n_sin_ordenar"):
        construir_propuesta(manifiesto_sintetico, _resumen(n_sin_ordenar=4), "T", GEN)
    r = _resumen()
    r["pb"].pop(1)
    with pytest.raises(ErrorPropuesta, match="distintos"):
        construir_propuesta(manifiesto_sintetico, r, "T", GEN)


def test_resumen_sin_ejecucion_lanza(manifiesto_sintetico):
    with pytest.raises(ErrorPropuesta, match="ejecucion_indice"):
        construir_propuesta(manifiesto_sintetico, _resumen(ejecucion_indice=None), "T", GEN)


def test_tipo_desconocido_lanza(tmp_path):
    extra = [_fila("/in/D/x.JPG", "VIDEO", None, "30", "1")]
    db = _crear(tmp_path, filas_extra=extra)
    with pytest.raises(ErrorPropuesta, match="tipo"):
        construir_propuesta(db, _resumen(), "T", GEN)


def test_bd_se_abre_solo_lectura(manifiesto_sintetico):
    antes = (os.stat(manifiesto_sintetico).st_mtime_ns, open(manifiesto_sintetico, "rb").read())
    construir_propuesta(manifiesto_sintetico, _resumen(), "T", GEN)
    assert (os.stat(manifiesto_sintetico).st_mtime_ns, open(manifiesto_sintetico, "rb").read()) == antes
    assert not [f for f in os.listdir(os.path.dirname(manifiesto_sintetico)) if f.startswith("m.db-")]


def _args(db, resumen, salida):
    return ["--manifiesto", db, "--resumen", resumen, "--planta", "TOCOPILLA", "--salida", salida]


def test_cli_escribe_json(manifiesto_sintetico, tmp_path):
    rj = tmp_path / "resumen.json"
    rj.write_text(json.dumps(_resumen()), encoding="utf-8")
    salida = tmp_path / "out.json"
    assert main(_args(manifiesto_sintetico, str(rj), str(salida))) == 0
    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert datos["version"] == 1 and datos["generado"].endswith("Z")
    assert not (tmp_path / "out.json.tmp").exists()


def test_cli_error_no_deja_fichero(tmp_path):
    db = _crear(tmp_path, vuelos=[_vuelo("24", "1", 270, sufijo="x")])
    rj = tmp_path / "resumen.json"
    rj.write_text(json.dumps(_resumen()), encoding="utf-8")
    salida = tmp_path / "out.json"
    assert main(_args(db, str(rj), str(salida))) == 2
    assert not salida.exists()


def test_cli_version_resumen_y_rechaza_gs(manifiesto_sintetico, tmp_path):
    rj = tmp_path / "resumen.json"
    rj.write_text(json.dumps(_resumen(version=2)), encoding="utf-8")
    assert main(_args(manifiesto_sintetico, str(rj), str(tmp_path / "o.json"))) == 2
    rj.write_text(json.dumps(_resumen()), encoding="utf-8")
    assert main(_args(manifiesto_sintetico, str(rj), "gs://b/x.json")) == 2


def test_resumen_malformado_lanza_error_propuesta(manifiesto_sintetico):
    r = _resumen()
    del r["pb"][0]["imagenes"]
    with pytest.raises(ErrorPropuesta, match="malformado"):
        construir_propuesta(manifiesto_sintetico, r, "T", GEN)
    r = _resumen()
    r["fuera_de_orden"][0].pop("n")
    with pytest.raises(ErrorPropuesta, match="fuera_de_orden"):
        construir_propuesta(manifiesto_sintetico, r, "T", GEN)
    r = _resumen()
    r["pb"] = None
    with pytest.raises(ErrorPropuesta, match="'pb'"):
        construir_propuesta(manifiesto_sintetico, r, "T", GEN)


def test_grupos_corruptos_lanza_error_propuesta(manifiesto_sintetico):
    import sqlite3
    con = sqlite3.connect(manifiesto_sintetico)
    con.execute("UPDATE grupos_sin_colocar SET datos = '{no json'")
    con.commit()
    con.close()
    with pytest.raises(ErrorPropuesta, match="grupos_sin_colocar"):
        construir_propuesta(manifiesto_sintetico, _resumen(), "T", GEN)
    con = sqlite3.connect(manifiesto_sintetico)
    con.execute("UPDATE grupos_sin_colocar SET datos = '[1, 2]'")
    con.commit()
    con.close()
    with pytest.raises(ErrorPropuesta, match="grupos_sin_colocar"):
        construir_propuesta(manifiesto_sintetico, _resumen(), "T", GEN)


def test_version_resumen_se_valida_en_funcion_publica(manifiesto_sintetico):
    with pytest.raises(ErrorPropuesta, match="version"):
        construir_propuesta(manifiesto_sintetico, _resumen(version=2), "T", GEN)


def _relajar_columna(db, columna):
    import sqlite3
    con = sqlite3.connect(db)
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='imagenes'").fetchone()[0]
    linea = next(l for l in sql.splitlines() if l.strip().startswith(columna + " "))
    con.execute("PRAGMA writable_schema=ON")
    con.execute("UPDATE sqlite_master SET sql = ? WHERE name='imagenes'",
                (sql.replace(linea, linea.replace(" NOT NULL", "")),))
    con.commit()
    con.close()


def test_sin_registro_giro_y_angulo_null_error_especifico(tmp_path):
    db = _crear(tmp_path, filas_extra=[_fila("/in/D/DJI_20261108000000_0001_T.JPG", "TERMICA", None, "30", "1")])
    _relajar_columna(db, "angulo_giro")
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("UPDATE imagenes SET angulo_giro = NULL WHERE pb='30'")
    con.commit()
    con.close()
    r = _resumen()
    r["pb"].append({"pb": "30", "vuelo": "1", "imagenes": 1, "pares": 0})
    with pytest.raises(ErrorPropuesta, match=r"vuelo 30/1 sin registro de giro y sin angulo_giro"):
        construir_propuesta(db, r, "T", GEN)


def test_bytes_null_lanza_con_conteo_y_vuelo(manifiesto_sintetico):
    import sqlite3
    _relajar_columna(manifiesto_sintetico, "bytes_origen")
    con = sqlite3.connect(manifiesto_sintetico)
    con.execute("UPDATE imagenes SET bytes_origen = NULL WHERE ruta_origen LIKE '/in/A/%'")
    con.commit()
    con.close()
    with pytest.raises(ErrorPropuesta, match=r"vuelo 24/1: 4 imagen\(es\) con bytes_origen NULL"):
        construir_propuesta(manifiesto_sintetico, _resumen(), "T", GEN)


def _borrar_columnas_categoria(db):
    import sqlite3
    con = sqlite3.connect(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(imagenes)")
            if r[1] not in ("categoria", "motivo_fuera")]
    con.execute(f"CREATE TABLE imagenes_vieja AS SELECT {', '.join(cols)} FROM imagenes")
    con.execute("DROP TABLE imagenes")
    con.execute("ALTER TABLE imagenes_vieja RENAME TO imagenes")
    con.commit()
    con.close()


def test_propuesta_usa_columna_categoria(tmp_path):
    db = _crear(tmp_path)
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("UPDATE imagenes SET categoria='fotos_generales' WHERE pb='GENERALES'")
    con.execute("UPDATE imagenes SET categoria='sin_ordenar', motivo_fuera='dia-sin-estadillo' "
                "WHERE unassigned=1")
    con.commit()
    con.close()
    p = construir_propuesta(db, _resumen(), "T", GEN)
    assert p["fuente_categoria"] == "columna"
    ordenar = [f for f in p["fuera_de_vuelo"] if f["categoria"] == "sin_ordenar" and f["indexada"]]
    assert [(f["motivo"], f["n_imagenes"]) for f in ordenar] == [("dia-sin-estadillo", 3)]
    assert [f["categoria"] for f in p["fuera_de_vuelo"]].count("fotos_generales") == 1


def test_propuesta_columna_sin_motivo_en_fila_sin_ordenar_lanza(tmp_path):
    db = _crear(tmp_path)
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("UPDATE imagenes SET categoria='sin_ordenar', motivo_fuera=NULL WHERE unassigned=1")
    con.execute("UPDATE imagenes SET categoria='fotos_generales' WHERE pb='GENERALES'")
    con.commit()
    con.close()
    with pytest.raises(ErrorPropuesta, match="motivo_fuera"):
        construir_propuesta(db, _resumen(), "T", GEN)


def test_propuesta_manifiesto_antiguo_deriva_v1(tmp_path):
    db = _crear(tmp_path)
    _borrar_columnas_categoria(db)
    p = construir_propuesta(db, _resumen(), "T", GEN)
    assert p["fuente_categoria"] == "derivada-v1"
    assert {f["categoria"] for f in p["fuera_de_vuelo"]} == {"fotos_generales", "sin_ordenar"}


def test_propuesta_columna_nativa_contradice_estructura_lanza(tmp_path):
    """Columnas nativas con fila 'vuelo' pese a unassigned: error explícito, sin derivar."""
    db = _crear(tmp_path)
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("UPDATE imagenes SET categoria='vuelo' WHERE unassigned=1")
    con.commit()
    con.close()
    with pytest.raises(ErrorPropuesta, match="incoherente"):
        construir_propuesta(db, _resumen(), "T", GEN)


def test_propuesta_manifiesto_migrado_deriva_v1(tmp_path):
    """Manifiesto antiguo al que `_migrar_columnas` añadió categoria: marca en ajustes -> deriva."""
    db = _crear(tmp_path)
    _borrar_columnas_categoria(db)
    m = Manifiesto(db)
    m.crear_esquema()  # migra: añade columnas y marca
    assert m.leer_ajuste("categoria_migrada") == "1"
    m.cerrar()
    p = construir_propuesta(db, _resumen(), "T", GEN)
    assert p["fuente_categoria"] == "derivada-v1"
