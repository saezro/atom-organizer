"""Comparador de dos árboles de organizado (motor viejo vs. motor nuevo).

Estos tests sujetan la red de seguridad que se usará antes de sustituir el
motor de organización: sin ella, cualquier regresión silenciosa del motor
nuevo (un byte de TIFF corrompido, un EXIF que se pierde al reescribir, una
fila de CSV desplazada) pasaría desapercibida hasta que un operador la
detectara a mano en campo, mucho más tarde y mucho más caro.

Lo que estos tests comprueban:

1. Dos árboles idénticos son equivalentes (código 0): caso base, sin el cual
   el comparador daría falsos positivos de discrepancia en el caso normal.
2. Un fichero de más en uno de los lados se reporta en `solo_en_a`/
   `solo_en_b` y hace fallar la comparación: si esto no se detectara, un
   motor nuevo que "se olvida" de organizar un fichero pasaría inadvertido.
3. El caso CENTRAL de todo el comparador: un JPEG recomprimido a otra
   calidad (bytes de fichero distintos) pero con los MISMOS píxeles a
   efectos prácticos (dentro de la tolerancia de recompresión) y el MISMO
   EXIF debe considerarse equivalente. El motor nuevo hace menos encodes que
   el viejo, así que sus JPEG finales nunca serán byte a byte ni píxel a
   píxel idénticos aunque el contenido sea el mismo; si comparásemos por
   hash exacto de píxeles sin tolerancia, el comparador dispararía miles de
   falsos positivos en cada corrida y reventaría la confianza en la
   herramienta desde el primer uso.
3b. Dos imágenes con contenido claramente distinto deben seguir marcándose
    como discrepancia, con el motivo incluyendo `dif_max`: la tolerancia de
    recompresión no puede volverse una coladera que oculte una imagen
    realmente distinta.
3c. Con `--estricto` (tolerancias a 0) dos JPEG recomprimidos SÍ deben
    marcarse como discrepancia: es la vía de escape para cuando de verdad
    hace falta exigir píxeles idénticos.
3d. Dos imágenes con dimensiones distintas deben reportarse como
    discrepancia con el motivo indicando ambas resoluciones, antes de
    intentar restar arrays de formas incompatibles.
4. Un JPEG con los mismos píxeles pero EXIF alterado (p. ej. la fecha) SÍ es
   una discrepancia real: el EXIF lleva georreferenciación y metadatos de
   vuelo que el operador necesita intactos, perderlos sería un fallo grave
   aunque la imagen "se vea igual".
5. Un `.tiff` con un solo byte distinto es una discrepancia: las térmicas
   crudas no llevan recompresión de por medio, así que aquí SÍ toca
   byte a byte estricto sin excepciones.
6. Dos CSV (estadillos) que difieren en una fila intermedia deben reportar
   el índice exacto de la fila y el contenido de ambos lados, para que quien
   lea el informe no tenga que hacer un diff manual de ficheros de miles de
   líneas.
"""
import csv
import json

import pytest

from tools.comparar_organizados import comparar_arboles, main


def _escribir_arbol_simple(directorio):
    """Crea un árbol mínimo con un fichero de texto plano en SIN_ORDENAR y
    otro en una subcarpeta normal, para probar que ambos entran en la
    comparación sin distinción.
    """
    (directorio / "SIN_ORDENAR").mkdir(parents=True)
    (directorio / "SIN_ORDENAR" / "raro.dat").write_bytes(b"contenido raro")

    (directorio / "PB1_V01" / "RGB").mkdir(parents=True)
    (directorio / "PB1_V01" / "RGB" / "listado.txt").write_text("hola\n")


def test_arboles_identicos_son_equivalentes(tmp_path):
    """Caso base: si ambos lados son un calco exacto, no hay nada que
    reportar y el código de salida debe ser 0.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    _escribir_arbol_simple(dir_a)
    _escribir_arbol_simple(dir_b)

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is True
    assert informe["solo_en_a"] == []
    assert informe["solo_en_b"] == []
    assert informe["discrepancias_contenido"] == []

    codigo = main([str(dir_a), str(dir_b)])
    assert codigo == 0


def test_fichero_de_mas_en_b_rompe_la_equivalencia(tmp_path):
    """Un motor nuevo que organiza un fichero de más (o de menos) es una
    regresión real de árbol, no de contenido: debe aparecer en
    `solo_en_b`/`solo_en_a` y el código de salida debe pasar a 1.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    _escribir_arbol_simple(dir_a)
    _escribir_arbol_simple(dir_b)
    (dir_b / "PB1_V01" / "RGB" / "extra.jpg").write_bytes(b"no deberia estar")

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is False
    assert informe["solo_en_a"] == []
    assert informe["solo_en_b"] == ["PB1_V01/RGB/extra.jpg"]

    codigo = main([str(dir_a), str(dir_b)])
    assert codigo == 1


def test_jpeg_recomprimido_mismos_pixeles_y_exif_es_equivalente(
    tmp_path, make_dji_jpeg
):
    """El caso central: dos JPEG con bytes de fichero DISTINTOS (una de las
    dos copias se guarda con distinta calidad de recompresión) pero con
    píxeles equivalentes dentro de la tolerancia de recompresión y el MISMO
    EXIF deben considerarse equivalentes. Si esto fallase, el comparador
    sería inútil en la práctica porque el motor nuevo (menos encodes que el
    viejo) nunca produce un JPEG byte a byte ni píxel a píxel idéntico
    aunque el contenido sea fiel.

    La imagen de `make_dji_jpeg` es de color plano (120,130,140), así que la
    recompresión a calidad 40 introduce una diferencia de píxel mínima y
    determinista (dif_max=2, rmse≈1.41, comprobado empíricamente), muy por
    debajo de la tolerancia por defecto (8 / 2.0): el test no depende de
    suerte.
    """
    from PIL import Image

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    ruta_original = str(dir_a / "DJI_0001_D.JPG")
    make_dji_jpeg(ruta_original)

    # Recomprime a otra calidad, preservando el EXIF original, para simular
    # un motor que reescribe el fichero sin alterar imagen ni metadatos.
    ruta_recomprimida = dir_b / "DJI_0001_D.JPG"
    with Image.open(ruta_original) as img:
        exif_original = img.info.get("exif")
        img.convert("RGB").save(
            ruta_recomprimida, format="JPEG", quality=40, exif=exif_original
        )

    assert (dir_a / "DJI_0001_D.JPG").read_bytes() != ruta_recomprimida.read_bytes()

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is True
    assert informe["discrepancias_contenido"] == []


def test_imagenes_con_contenido_distinto_no_son_equivalentes(tmp_path):
    """La tolerancia de recompresión no debe convertirse en una coladera:
    dos imágenes con contenido claramente distinto (no una recompresión de
    la misma imagen) deben seguir marcándose como discrepancia, con el
    motivo incluyendo `dif_max` para poder diagnosticarlo.
    """
    from PIL import Image

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    Image.new("RGB", (64, 48), color=(120, 130, 140)).save(
        dir_a / "DJI_0001_D.JPG", format="JPEG"
    )
    Image.new("RGB", (64, 48), color=(10, 200, 30)).save(
        dir_b / "DJI_0001_D.JPG", format="JPEG"
    )

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is False
    assert len(informe["discrepancias_contenido"]) == 1
    discrepancia = informe["discrepancias_contenido"][0]
    assert discrepancia["ruta"] == "DJI_0001_D.JPG"
    assert "dif_max" in discrepancia["motivo"]


def test_jpeg_recomprimido_con_estricto_no_es_equivalente(tmp_path, make_dji_jpeg):
    """`--estricto` lleva las tolerancias a 0: la misma pareja de JPEG que en
    el caso central se considera equivalente con tolerancia por defecto debe
    marcarse como discrepancia si se exige píxel a píxel idéntico.
    """
    from PIL import Image

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    ruta_original = str(dir_a / "DJI_0001_D.JPG")
    make_dji_jpeg(ruta_original)

    ruta_recomprimida = dir_b / "DJI_0001_D.JPG"
    with Image.open(ruta_original) as img:
        exif_original = img.info.get("exif")
        img.convert("RGB").save(
            ruta_recomprimida, format="JPEG", quality=40, exif=exif_original
        )

    informe_tolerante = comparar_arboles(dir_a, dir_b)
    assert informe_tolerante["equivalentes"] is True

    informe_estricto = comparar_arboles(
        dir_a, dir_b, tolerancia_max=0, tolerancia_rmse=0
    )
    assert informe_estricto["equivalentes"] is False

    codigo = main([str(dir_a), str(dir_b), "--estricto"])
    assert codigo == 1


def test_imagenes_con_dimensiones_distintas_es_discrepancia(tmp_path):
    """Dos imágenes con distinta resolución deben reportarse como
    discrepancia con el motivo indicando ambas dimensiones, sin intentar
    restar arrays de formas incompatibles (lo que lanzaría una excepción).
    """
    from PIL import Image

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    Image.new("RGB", (64, 48), color=(120, 130, 140)).save(
        dir_a / "DJI_0001_D.JPG", format="JPEG"
    )
    Image.new("RGB", (32, 24), color=(120, 130, 140)).save(
        dir_b / "DJI_0001_D.JPG", format="JPEG"
    )

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is False
    assert len(informe["discrepancias_contenido"]) == 1
    discrepancia = informe["discrepancias_contenido"][0]
    assert discrepancia["ruta"] == "DJI_0001_D.JPG"
    assert "dimensiones distintas" in discrepancia["motivo"]
    assert "64x48" in discrepancia["motivo"]
    assert "32x24" in discrepancia["motivo"]


def test_jpeg_mismos_pixeles_pero_exif_alterado_es_discrepancia(
    tmp_path, make_dji_jpeg
):
    """Aunque la imagen "se vea igual", si el EXIF cambia (aquí la fecha de
    captura) es una discrepancia real: ese metadato es lo que georreferencia
    y data la imagen, perderlo o alterarlo es un fallo grave del motor.
    """
    import datetime as dt

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    make_dji_jpeg(
        str(dir_a / "DJI_0001_D.JPG"), dt_val=dt.datetime(2024, 6, 1, 10, 30, 0)
    )
    make_dji_jpeg(
        str(dir_b / "DJI_0001_D.JPG"), dt_val=dt.datetime(2024, 6, 1, 11, 45, 0)
    )

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is False
    assert len(informe["discrepancias_contenido"]) == 1
    discrepancia = informe["discrepancias_contenido"][0]
    assert discrepancia["ruta"] == "DJI_0001_D.JPG"
    assert "exif" in discrepancia["motivo"]


def test_tiff_con_un_byte_distinto_es_discrepancia(tmp_path):
    """Las térmicas crudas (.tiff) no pasan por recompresión: aquí la
    comparación debe ser byte a byte estricta, sin ninguna tolerancia. Un
    solo byte distinto ya es un fallo.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    contenido = bytearray(b"II*\x00" + b"\x00" * 100)  # cabecera TIFF simplona
    (dir_a / "DJI_0001_T.tiff").write_bytes(bytes(contenido))

    contenido_alterado = bytearray(contenido)
    contenido_alterado[50] ^= 0xFF
    (dir_b / "DJI_0001_T.tiff").write_bytes(bytes(contenido_alterado))

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is False
    assert len(informe["discrepancias_contenido"]) == 1
    discrepancia = informe["discrepancias_contenido"][0]
    assert discrepancia["ruta"] == "DJI_0001_T.tiff"
    assert discrepancia["motivo"] == "byte a byte distinto"


def test_csv_que_difieren_en_fila_3_reporta_indice_y_contenido(tmp_path):
    """Un estadillo con una fila desplazada o corrupta debe señalarse por su
    índice exacto y el contenido de ambos lados: sin esto, detectar la fila
    que falla en un CSV de cientos de líneas exigiría un diff manual.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    filas_comunes = [
        ["imagen", "vuelo", "mesa"],
        ["DJI_0001.JPG", "PB1_V01", "M01"],
        ["DJI_0002.JPG", "PB1_V01", "M02"],
    ]

    with open(dir_a / "estadillo.csv", "w", newline="") as fh:
        csv.writer(fh).writerows(
            filas_comunes + [["DJI_0003.JPG", "PB1_V01", "M03"]]
        )

    with open(dir_b / "estadillo.csv", "w", newline="") as fh:
        csv.writer(fh).writerows(
            filas_comunes + [["DJI_0003.JPG", "PB1_V01", "M99"]]
        )

    informe = comparar_arboles(dir_a, dir_b)

    assert informe["equivalentes"] is False
    assert len(informe["discrepancias_contenido"]) == 1
    discrepancia = informe["discrepancias_contenido"][0]
    assert discrepancia["ruta"] == "estadillo.csv"
    assert discrepancia["fila"] == 3
    assert discrepancia["contenido_a"] == ["DJI_0003.JPG", "PB1_V01", "M03"]
    assert discrepancia["contenido_b"] == ["DJI_0003.JPG", "PB1_V01", "M99"]


def test_informe_json_se_escribe_completo(tmp_path):
    """El flag `--json` debe volcar el informe completo, no solo el resumen
    por stdout: es lo que consumirán herramientas externas o CI.
    """
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    _escribir_arbol_simple(dir_a)
    _escribir_arbol_simple(dir_b)
    (dir_b / "PB1_V01" / "RGB" / "extra.jpg").write_bytes(b"no deberia estar")

    ruta_json = tmp_path / "informe.json"
    codigo = main([str(dir_a), str(dir_b), "--json", str(ruta_json)])

    assert codigo == 1
    contenido = json.loads(ruta_json.read_text())
    assert contenido["equivalentes"] is False
    assert contenido["solo_en_b"] == ["PB1_V01/RGB/extra.jpg"]
