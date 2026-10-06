"""`atom_core.estadillo.detectar_estadillos`: detección automática de
estadillos dentro de una carpeta, sin que el operario tenga que elegirlos a
mano. Cubre lo que la UI necesita antes de subir: cuántos estadillos hay,
cuáles se descartan por no parecer un estadillo, y el caso "no hay ninguno"
(no es un error)."""
import pandas as pd

from atom_core import estadillo


def _csv(path, filas, columnas=("PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final")):
    lineas = [";".join(columnas)]
    lineas += [";".join(str(c) for c in fila) for fila in filas]
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(path)


def test_detecta_2_estadillos_en_subcarpetas_distintas(tmp_path):
    (tmp_path / "dia1").mkdir()
    (tmp_path / "dia2").mkdir()
    e1 = _csv(tmp_path / "dia1" / "e1.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    e2 = _csv(tmp_path / "dia2" / "e2.csv", [("2", "1", "2026:03:18", "11:00:00", "11:05:00")])

    res = estadillo.detectar_estadillos(str(tmp_path))

    assert res["rutas"] == sorted([e1, e2])
    assert res["descartados"] == []

    info = estadillo.read_estadillo_info(res["rutas"])
    assert "error" not in info
    assert info["num_vuelos"] == 2
    assert info["fechas"] == ["2026:03:17", "2026:03:18"]


def test_csv_que_no_es_estadillo_se_descarta(tmp_path):
    ok = _csv(tmp_path / "ok.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    no_estadillo = tmp_path / "notas.csv"
    no_estadillo.write_text("Columna_A;Columna_B\nfoo;bar\n", encoding="utf-8")

    res = estadillo.detectar_estadillos(str(tmp_path))

    assert res["rutas"] == [ok]
    assert res["descartados"] == [str(no_estadillo)]


def test_carpeta_sin_estadillos_no_es_error(tmp_path):
    (tmp_path / "solo_fotos").mkdir()
    (tmp_path / "solo_fotos" / "foto.jpg").write_text("no soy un estadillo", encoding="utf-8")

    res = estadillo.detectar_estadillos(str(tmp_path))

    assert res == {"rutas": [], "descartados": []}


def test_ignora_temporal_de_office(tmp_path):
    ok = _csv(tmp_path / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    temporal = tmp_path / "~$e.xlsx"
    temporal.write_text("basura", encoding="utf-8")

    res = estadillo.detectar_estadillos(str(tmp_path))

    assert res["rutas"] == [ok]
    assert str(temporal) not in res["rutas"]
    assert str(temporal) not in res["descartados"]


def test_incluir_recibidos_suma_los_de_la_carpeta_de_estadillos_recibidos(tmp_path, monkeypatch):
    """`incluir_recibidos=True` (solo escritorio, `app_webview.estadillos_detectar`)
    suma como candidatos los CSV/XLSX sueltos en `estadillos_recibidos_dir()`,
    además de los de la carpeta del vuelo. Sin el flag (default), esa carpeta
    ni se mira."""
    from atom_core import google_auth

    carpeta_vuelo = tmp_path / "vuelo"
    carpeta_vuelo.mkdir()
    e_vuelo = _csv(carpeta_vuelo / "e_vuelo.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])

    carpeta_recibidos = tmp_path / "estadillos_recibidos"
    carpeta_recibidos.mkdir()
    e_recibido = _csv(
        carpeta_recibidos / "e_recibido.csv", [("2", "1", "2026:03:18", "11:00:00", "11:05:00")]
    )
    monkeypatch.setattr(google_auth, "estadillos_recibidos_dir", lambda: carpeta_recibidos)

    # Sin el flag: el recibido no aparece.
    res_sin = estadillo.detectar_estadillos(str(carpeta_vuelo))
    assert res_sin["rutas"] == [e_vuelo]

    # Con el flag: se suma.
    res_con = estadillo.detectar_estadillos(str(carpeta_vuelo), incluir_recibidos=True)
    assert res_con["rutas"] == sorted([e_vuelo, e_recibido])


def test_detectar_estadillos_no_mira_el_padre(tmp_path):
    """Decisión del responsable (caso de campo): un estadillo suelto en la carpeta
    PADRE (PLANTA_C/estadillo.csv con PLANTA_C/FOTOS como origen) NO sale de
    `detectar_estadillos` -solo lo puede usar el operario a mano, vía
    `detectar_estadillos_en_padre` + confirmación explícita en la UI."""
    (tmp_path / "FOTOS").mkdir()
    (tmp_path / "OTRA").mkdir()
    _csv(tmp_path / "estadillo_PLANTA_C.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    _csv(tmp_path / "OTRA" / "ajeno.csv", [("2", "1", "2026:03:18", "11:00:00", "11:05:00")])

    res = estadillo.detectar_estadillos(str(tmp_path / "FOTOS"))

    assert res["rutas"] == []


def test_detectar_estadillos_en_padre_encuentra_el_suelto_sin_barrer_hermanas(tmp_path):
    """`detectar_estadillos_en_padre` sí ve el estadillo suelto en el padre
    (PLANTA_C/estadillo.csv con PLANTA_C/FOTOS como origen), sin bajar a carpetas
    hermanas (PLANTA_C/OTRA)."""
    (tmp_path / "FOTOS").mkdir()
    (tmp_path / "OTRA").mkdir()
    e = _csv(tmp_path / "estadillo_PLANTA_C.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    _csv(tmp_path / "OTRA" / "ajeno.csv", [("2", "1", "2026:03:18", "11:00:00", "11:05:00")])

    res = estadillo.detectar_estadillos_en_padre(str(tmp_path / "FOTOS"))

    assert res["rutas"] == [e]


def test_caso_campo_carpeta_propia_gana_sobre_estadillo_viejo_del_padre(tmp_path):
    """Caso real de campo: seleccionó una carpeta de planta que trae su
    propio estadillo, y en la carpeta padre hay otro (viejo, sacado a
    propósito) también válido. El automático debe usar SOLO el de dentro."""
    carpeta = tmp_path / "PLANTA"
    carpeta.mkdir()
    e_dentro = _csv(carpeta / "estadillo_nuevo.csv",
                     [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    e_padre = _csv(tmp_path / "estadillo_viejo.csv",
                    [("2", "1", "2026:01:01", "09:00:00", "09:05:00")])

    res = estadillo.detectar_estadillos(str(carpeta))
    res_padre = estadillo.detectar_estadillos_en_padre(str(carpeta))

    assert res["rutas"] == [e_dentro]
    assert res_padre["rutas"] == [e_padre]


# --- Raíz con varios días sobre Drive: poda de carpetas de vuelo/imágenes ----

def _arbol_chile(raiz, n_jpg=2000):
    cols = ("PB", "Vuelo", "Fecha", "Hora_de_inicio", "Hora_final")
    esperados = []
    for i, dia in enumerate(["2026_09_28", "2026_09_30", "2026_10_03", "2026_10_04"]):
        d = raiz / f"{dia}_Willka"
        d.mkdir(parents=True)
        esperados.append(_csv(d / f"{dia}_estadillo.csv", [(str(i), "1", "2026:09:28", "10:00:00", "10:05:00")]))
        (d / "Conteo_Data.txt").write_text("x")
        (d / "mapa.png").write_bytes(b"")
        rev = d / "Revision_Poliginos_Termales"
        rev.mkdir()
        for k in range(300):
            (rev / f"p{k}.png").write_bytes(b"")
        for v in range(2):
            dji = d / f"DJI_20260928152{v}_00{v}_X"
            dji.mkdir()
            for k in range(n_jpg):
                (dji / f"DJI_{k:04d}_T.JPG").write_bytes(b"")
    return sorted(esperados)


def test_raiz_con_varios_dias_no_lista_carpetas_de_vuelo_ni_de_imagenes(tmp_path, monkeypatch):
    import os
    esperados = _arbol_chile(tmp_path)
    listados = []
    real_scandir = os.scandir
    real_listdir = os.listdir

    def scandir_espia(ruta="."):
        listados.append(os.fspath(ruta))
        return real_scandir(ruta)

    def listdir_espia(ruta="."):
        listados.append(os.fspath(ruta))
        return real_listdir(ruta)

    monkeypatch.setattr(os, "scandir", scandir_espia)
    monkeypatch.setattr(os, "listdir", listdir_espia)

    res = estadillo.detectar_estadillos(str(tmp_path))

    assert res["rutas"] == esperados
    assert res["descartados"] == []
    assert not [r for r in listados if os.path.basename(r).startswith("DJI_")]
    # la carpeta de imágenes se lista una vez (para contarlas) pero no se desciende
    assert not [r for r in listados if os.path.dirname(r).endswith("Revision_Poliginos_Termales")]


def test_csv_sin_columna_esencial_sigue_descartado_en_arbol_con_dias(tmp_path):
    d = tmp_path / "2026_09_28_Willka"
    d.mkdir()
    malo = _csv(d / "malo.csv", [("1", "1", "2026:09:28", "10:00:00")],
                columnas=("PB", "Vuelo", "Fecha", "Hora_de_inicio"))
    res = estadillo.detectar_estadillos(str(tmp_path))
    assert res["rutas"] == []
    assert res["descartados"] == [malo]


def test_profundidad_2_dentro_de_carpeta_no_de_vuelo_y_no_mas(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    ok = _csv(tmp_path / "a" / "b" / "ok.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    _csv(tmp_path / "a" / "b" / "c" / "fuera.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    assert estadillo.detectar_estadillos(str(tmp_path))["rutas"] == [ok]


def test_csv_en_carpeta_con_mas_de_50_imagenes_se_detecta(tmp_path):
    d = tmp_path / "fotos"
    d.mkdir()
    for k in range(estadillo._MAX_IMAGENES_POR_CARPETA + 5):
        (d / f"img_{k}.jpg").write_bytes(b"")
    ok = _csv(d / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    assert estadillo.detectar_estadillos(str(tmp_path))["rutas"] == [ok]


def test_carpeta_dji_notas_no_se_poda_pero_dji_con_fecha_si(tmp_path):
    (tmp_path / "dji_notas").mkdir()
    (tmp_path / "DJI_202609301234").mkdir()
    ok = _csv(tmp_path / "dji_notas" / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    _csv(tmp_path / "DJI_202609301234" / "e.csv", [("1", "1", "2026:03:17", "10:00:00", "10:05:00")])
    assert estadillo._es_carpeta_de_vuelo("DJI_202609301234")
    assert estadillo._es_carpeta_de_vuelo("dji_202609301234_004")
    assert not estadillo._es_carpeta_de_vuelo("dji_notas")
    assert estadillo.detectar_estadillos(str(tmp_path))["rutas"] == [ok]
