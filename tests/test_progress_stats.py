"""Tests del tracker de progreso en vivo (`atom_core.progress_stats`).

El módulo es puro a propósito (sin Qt, sin importar `gui`), así que se puede
importar y ejercitar directamente — al contrario que `atom_core.organize`, que
arrastra PySide6 y solo se puede verificar por AST.

Lo que se protege aquí: que las cadenas REALES que emite `pipeline.py` se
parseen (si alguien cambia el literal del pipeline, estos tests caen), que los
contadores por fase se reinicien y los del run no, y que el throttling no se
coma la última tanda de imágenes.
"""
import ast
import os

import pytest

from atom_core.progress_stats import (
    IMAGE_EMIT_EVERY,
    StatsTracker,
    classify_folder,
)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- clasificación RGB / térmica ---------------------------------------------

@pytest.mark.parametrize("path,esperado", [
    ("/salida/TERMICA", "termica"),
    ("/salida/TERMICA/PB1/vuelo1", "termica"),
    ("/salida/RGB", "rgb"),
    ("/salida/RGB/PB1/vuelo1", "rgb"),
    ("/salida/RGB_EXTRA", "rgb"),
    (r"C:\salida\TERMICA\PB1", "termica"),
    ("/salida/ESTADILLOS", None),
    ("", None),
])
def test_classify_folder(path, esperado):
    assert classify_folder(path) == esperado


def test_classify_toma_el_segmento_mas_profundo():
    """Un destino llamado 'RGB' no debe teñir de RGB las térmicas de debajo."""
    assert classify_folder("/trabajo/RGB/TERMICA/PB1") == "termica"


# --- parsing de las cadenas reales del pipeline -------------------------------

def test_procesando_con_directorio_reparte_por_tipo():
    st = StatsTracker()
    assert st.on_line("\nProcesando 120 imágenes en el directorio /out/RGB/PB1\n")
    assert st.on_line("\nProcesando 30 imágenes en el directorio /out/TERMICA/PB1\n")
    snap = st.snapshot()
    assert snap["total"] == 150
    assert snap["rgb"] == 120
    assert snap["termica"] == 30


def test_procesando_sin_directorio_suma_al_total_pero_no_al_reparto():
    st = StatsTracker()
    assert st.on_line("Procesando 40 imágenes\n")
    assert st.on_line("Procesando y girando 12 imágenes TIFF\n")
    assert st.on_line("Procesando y recortando 8 imágenes\n")
    snap = st.snapshot()
    assert snap["total"] == 60
    assert snap["rgb"] == 0 and snap["termica"] == 0


def test_conteo_de_rotacion():
    st = StatsTracker()
    assert st.on_line(
        "\nNúmero de imágenes rotadas 270: 5\n"
        "Número de imágenes rotadas 90: 3\n"
        "Número de imágenes sin rotar: 12\n"
    )
    snap = st.snapshot()
    assert (snap["rot270"], snap["rot90"], snap["rot_none"]) == (5, 3, 12)


def test_rotacion_suma_entre_directorios():
    """El pipeline emite el conteo una vez por directorio: deben acumularse."""
    st = StatsTracker()
    st.on_line("Número de imágenes rotadas 270: 5\nNúmero de imágenes sin rotar: 1\n")
    st.on_line("Número de imágenes rotadas 270: 2\nNúmero de imágenes rotadas 90: 4\n")
    snap = st.snapshot()
    assert (snap["rot270"], snap["rot90"], snap["rot_none"]) == (7, 4, 1)


def test_linea_irrelevante_no_cambia_nada():
    st = StatsTracker()
    antes = st.snapshot()
    assert st.on_line("---> SUBPROCESO: Recorte RGB.") is False
    assert st.on_line("Ha habido 3 errores") is False
    assert st.snapshot() == antes


def test_los_literales_del_pipeline_siguen_siendo_los_parseados():
    """Guarda contra deriva: si `pipeline.py` cambia el texto que emite, el
    parser deja de ver nada y el modal se queda mudo SIN que falle nada más."""
    fuente = open(os.path.join(REPO, "pipeline.py"), encoding="utf-8").read()
    for literal in ("Procesando {0} imágenes en el directorio {1}",
                    "Número de imágenes rotadas 270: {0}"):
        assert literal in fuente, (
            f"El pipeline ya no emite «{literal}»: actualiza los regex de "
            "atom_core/progress_stats.py o el modal perderá las estadísticas.")


# --- contadores por fase vs contadores del run --------------------------------

def test_start_phase_reinicia_lo_de_fase_y_conserva_lo_del_run():
    st = StatsTracker()
    st.on_line("Procesando 10 imágenes en el directorio /out/RGB\n")
    st.on_image()
    st.on_line("Número de imágenes rotadas 90: 4\n")

    st.start_phase(2, "Convertir a TIF")
    snap = st.snapshot()
    assert snap["phase_index"] == 2 and snap["phase_name"] == "Convertir a TIF"
    assert (snap["done"], snap["total"], snap["rgb"]) == (0, 0, 0)
    assert snap["rot90"] == 4  # el run no se reinicia


# --- throttling de imágenes ---------------------------------------------------

def test_on_image_emite_cada_n_imagenes():
    st = StatsTracker()
    emitidos = [i for i in range(1, IMAGE_EMIT_EVERY * 2 + 1) if st.on_image()]
    assert emitidos == [IMAGE_EMIT_EVERY, IMAGE_EMIT_EVERY * 2]
    assert st.snapshot()["done"] == IMAGE_EMIT_EVERY * 2


def test_on_image_emite_al_completar_el_total_aunque_no_toque_el_multiplo():
    """Sin esto, un lote de 3 imágenes nunca llegaría a pintarse como completo."""
    st = StatsTracker()
    st.on_line("Procesando 3 imágenes en el directorio /out/TERMICA\n")
    assert [st.on_image() for _ in range(3)] == [False, False, True]


# --- contrato con organize.py (verificado por AST, sin importar Qt) -----------

def test_organize_cablea_el_tracker_y_emite_stats():
    fuente = open(os.path.join(REPO, "atom_core", "organize.py"), encoding="utf-8").read()
    arbol = ast.parse(fuente)

    importa = any(
        isinstance(n, ast.ImportFrom) and n.module == "atom_core.progress_stats"
        for n in ast.walk(arbol)
    )
    assert importa, "organize.py debe usar el tracker, no re-implementar el parseo"

    emits = {
        n.args[0].value
        for n in ast.walk(arbol)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "emit" and n.args
        and isinstance(n.args[0], ast.Constant)
    }
    assert "stats" in emits, "organize.py no emite el evento 'stats'"


def test_el_puente_reenvia_stats_como_dato_estructurado():
    """En app_webview el payload de 'stats' es un dict: si no está en la rama
    de `data` se serializaría con str() y React recibiría texto."""
    fuente = open(os.path.join(REPO, "app_webview.py"), encoding="utf-8").read()
    assert '("plan", "phase", "stats", "done")' in fuente


# --- Contadores que mentían en el log real del 2026-08-05 --------------------
# Las cadenas de esta sección están copiadas LITERALMENTE del log de corrida de
# la v3.3.0 (atom-organizer-run_20260805_103904_pid11260.log.txt), donde el
# panel mostraba cosas imposibles: "10/0 img" (más hechas que anunciadas) y
# "RGB 0 / térmica 0" mientras procesaba cinco térmicas.

def test_procesando_sin_articulo_cuenta_igual():
    """La fase de meta/geolocalización dice "en directorio", sin "el". Exigir el
    artículo dejaba esa fase entera con total=0."""
    t = StatsTracker()
    t.on_line("Procesando 5 imágenes en directorio "
              r"C:\Users\Kais\Desktop\DATOS_ORGANIZAR\Nueva carpeta\RGB\PB24\PB24_V1")

    assert t.snapshot()["total"] == 5
    assert t.snapshot()["rgb"] == 5


def test_moviendo_imagenes_cuenta_en_la_fase_de_estructura():
    t = StatsTracker()
    t.on_line("Moviendo 5 imágenes Térmicas al directorio "
              r"C:\Users\Kais\Desktop\DATOS_ORGANIZAR\Nueva carpeta\TERMICA\PB24\PB24_V1")
    t.on_line("Moviendo 5 imágenes RGB al directorio "
              r"C:\Users\Kais\Desktop\DATOS_ORGANIZAR\Nueva carpeta\RGB\PB24\PB24_V1")

    s = t.snapshot()
    assert s["total"] == 10, "la fase de estructura avanzaba con total=0"
    assert s["termica"] == 5 and s["rgb"] == 5


def test_el_directorio_anunciado_aparte_clasifica_las_imagenes():
    """En la conversión a TIFF el directorio va en su propia línea y el recuento
    llega desnudo: sin recordarlo, cinco térmicas salían como "térmica 0"."""
    t = StatsTracker()
    t.on_line("Analizando directorio: "
              r"C:\Users\Kais\Desktop\DATOS_ORGANIZAR\Nueva carpeta\TERMICA\PB24\PB24_V1")
    t.on_line("Procesando 5 imágenes")

    s = t.snapshot()
    assert s["total"] == 5
    assert s["termica"] == 5, "el tipo debe heredarse del directorio anunciado antes"


def test_nunca_se_anuncia_un_total_menor_que_lo_hecho():
    """Red de seguridad: si un recuento se emite con una redacción no prevista,
    el panel debe quedarse corto, nunca mostrar "10/0"."""
    t = StatsTracker()
    for _ in range(10):
        t.on_image()

    assert t.snapshot()["total"] == 10
