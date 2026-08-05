"""Diagnóstico del R-JPEG antes de invocar el conversor térmico de DJI.

Contexto: el 2026-08-05 las cinco térmicas del 28/07 fallaron en casa del usuario
con `create R-JPEG dirp handle failed` / código -16, mientras esa MISMA imagen
convertía sin problema en otra máquina con el mismo SDK. El log no traía ni el
tamaño ni el hash del fichero que se le pasaba al conversor, así que no había
manera de saber si lo que llegaba al SDK seguía siendo el original — y el caso se
quedó bloqueado esperando a que alguien mandara ficheros por WhatsApp.

Estos tests fijan que el log, por sí solo, permita cerrar esa pregunta.
"""
import os
import struct
import subprocess
import types

from atom_core import rjpeg


def _sink_progress(sink):
    def _emit(*a, **k):
        if a and isinstance(a[0], str):
            sink.append(a[0])
    return types.SimpleNamespace(emit=_emit)


def _hacer_rjpeg(path_jpeg_normal, destino, n_app3=3, tam=64000):
    """Convierte un JPEG normal en un R-JPEG sintético insertando APP3 tras SOI.

    No es una imagen radiométrica real — no hace falta: lo que se comprueba es el
    recorrido de marcadores, y el payload de DJI es opaco también para nosotros.
    """
    with open(path_jpeg_normal, "rb") as fh:
        data = fh.read()
    segmentos = b""
    for _ in range(n_app3):
        cuerpo = b"\x00" * tam
        segmentos += b"\xff\xe3" + struct.pack(">H", len(cuerpo) + 2) + cuerpo
    with open(destino, "wb") as fh:
        fh.write(data[:2] + segmentos + data[2:])
    return destino


def test_rjpeg_valido_se_reconoce_con_su_payload(tmp_path, make_dji_jpeg):
    normal = make_dji_jpeg(str(tmp_path / "normal.JPG"))
    termica = _hacer_rjpeg(normal, str(tmp_path / "DJI_0001_T.JPG"), n_app3=3, tam=64000)

    info = rjpeg.inspect_rjpeg(termica)

    assert info["ok"], info["motivo"]
    assert info["segmentos"][0xE3][0] == 3, "deben contarse los tres APP3"
    assert info["payload"] >= 3 * 64000
    assert "OK" in rjpeg.describe(info, "DJI_0001_T.JPG")


def test_jpeg_sin_payload_radiometrico_se_detecta_antes_del_conversor(tmp_path, make_dji_jpeg):
    """Un R-JPEG re-encodado sigue abriéndose como imagen y se ve igual, pero ya
    no lleva temperaturas. Es el caso que el -16 del SDK no sabía explicar."""
    normal = make_dji_jpeg(str(tmp_path / "DJI_0002_T.JPG"))

    info = rjpeg.inspect_rjpeg(normal)

    assert not info["ok"]
    assert "APP3" in info["motivo"], "el motivo debe nombrar los segmentos que faltan"
    assert info["size"] > 0 and info["sha256"], "aun así debe dar tamaño y hash"


def test_el_hash_identifica_el_fichero(tmp_path, make_dji_jpeg):
    """El hash existe para comparar la copia procesada contra el original de la
    tarjeta: tiene que coincidir en copias idénticas y cambiar si cambia un byte."""
    a = make_dji_jpeg(str(tmp_path / "a.JPG"))
    with open(a, "rb") as fh:
        data = fh.read()
    copia = str(tmp_path / "copia.JPG")
    with open(copia, "wb") as fh:
        fh.write(data)
    tocado = str(tmp_path / "tocado.JPG")
    with open(tocado, "wb") as fh:
        fh.write(data[:-1] + bytes([data[-1] ^ 0xFF]))

    assert rjpeg.inspect_rjpeg(a)["sha256"] == rjpeg.inspect_rjpeg(copia)["sha256"]
    assert rjpeg.inspect_rjpeg(a)["sha256"] != rjpeg.inspect_rjpeg(tocado)["sha256"]


def test_ficheros_invalidos_no_revientan(tmp_path):
    """Es diagnóstico: pase lo que pase, no puede tumbar la conversión."""
    vacio = tmp_path / "vacio.JPG"
    vacio.write_bytes(b"")
    basura = tmp_path / "basura.JPG"
    basura.write_bytes(b"esto no es un jpeg")

    ausente = rjpeg.inspect_rjpeg(str(tmp_path / "no-existe.JPG"))
    assert not ausente["existe"] and not ausente["ok"]
    assert "vacío" in rjpeg.inspect_rjpeg(str(vacio))["motivo"]
    assert "JPEG" in rjpeg.inspect_rjpeg(str(basura))["motivo"]


def _preparar(tmp_path, monkeypatch, rc, imagen_bytes_desde, salida=""):
    """Monta la conversión con un dji_irp.exe falso que devuelve `rc`."""
    import pipeline

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    image_name = "DJI_0003_T.JPG"
    with open(imagen_bytes_desde, "rb") as fh:
        (input_folder / image_name).write_bytes(fh.read())

    dron_dir = tmp_path / "programas_externos" / "DJI"
    dron_dir.mkdir(parents=True)

    monkeypatch.setattr(pipeline, "_is_windows", lambda: True)
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, rc, stdout="", stderr=salida))

    return str(input_folder), image_name, str(dron_dir / "dji_irp.exe")


def _convertir(logger, input_folder, image_name, dji_utility):
    import pipeline

    mensajes = []
    progress = _sink_progress(mensajes)
    pipeline.SplitImages(logger).convert_dji_image_to_tif(
        input_folder, input_folder, image_name, "exiftool", dji_utility, progress, progress)
    return "\n".join(mensajes)


def test_el_log_registra_tamano_y_hash_antes_de_invocar_al_conversor(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    normal = make_dji_jpeg(str(tmp_path / "src.JPG"))
    termica = _hacer_rjpeg(normal, str(tmp_path / "src_T.JPG"))
    tam = os.path.getsize(termica)
    input_folder, image_name, dji_utility = _preparar(
        tmp_path, monkeypatch, rc=0, imagen_bytes_desde=termica)

    texto = _convertir(logger, input_folder, image_name, dji_utility)

    assert "[rjpeg]" in texto, "la radiografía del fichero debe llegar al log de corrida"
    assert str(tam) in texto, "sin el tamaño no se puede comparar contra el original"
    assert "sha256:" in texto, "sin el hash no se puede comparar contra el original"


def test_el_menos_16_con_imagen_sana_apunta_al_entorno(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    """Imagen íntegra + SDK que la rechaza = el problema está fuera del fichero.
    Ese matiz es justo el que faltaba en el log del 05/08."""
    normal = make_dji_jpeg(str(tmp_path / "src.JPG"))
    termica = _hacer_rjpeg(normal, str(tmp_path / "src_T.JPG"))
    input_folder, image_name, dji_utility = _preparar(
        tmp_path, monkeypatch, rc=-16, imagen_bytes_desde=termica,
        salida="ERROR: create R-JPEG dirp handle failed")

    texto = _convertir(logger, input_folder, image_name, dji_utility)

    assert "rechazado la imagen" in texto
    assert "antivirus" in texto, "debe orientar a las causas de entorno"
    assert "payload radiométrico" in texto


def test_el_menos_16_con_imagen_rota_lo_dice_claro(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    normal = make_dji_jpeg(str(tmp_path / "src.JPG"))
    input_folder, image_name, dji_utility = _preparar(
        tmp_path, monkeypatch, rc=-16, imagen_bytes_desde=normal,
        salida="ERROR: create R-JPEG dirp handle failed")

    texto = _convertir(logger, input_folder, image_name, dji_utility)

    assert "llegó dañada" in texto
    assert "APP3" in texto, "debe nombrar lo que le falta al fichero"


def test_la_inspeccion_no_aborta_la_conversion(tmp_path, logger, make_dji_jpeg, monkeypatch):
    """Un JPEG sin payload no debe cortar el flujo: el conversor sigue teniendo la
    última palabra, y el aviso del .raw ausente se mantiene."""
    normal = make_dji_jpeg(str(tmp_path / "src.JPG"))
    input_folder, image_name, dji_utility = _preparar(
        tmp_path, monkeypatch, rc=0, imagen_bytes_desde=normal)

    texto = _convertir(logger, input_folder, image_name, dji_utility)

    assert "Posible error del conversor DJI" in texto


# --- Legibilidad del log del conversor ---------------------------------------
# En el log de la v3.3.0, cinco térmicas fallidas ocuparon ~80 líneas: el volcado
# del SDK salía DOS veces por imagen (una en el fallo, otra en el aviso del .raw
# ausente) y tres de sus cinco líneas eran un banner idéntico en todas.

SALIDA_SDK_REAL = (
    "DIRP API version number : 0x13\n"
    "DIRP API magic version  : d4c7dea\n"
    "R-JPEG file path : C:\\Users\\Kais\\...\\20260728_100801_DJI_0002_T.JPG\n"
    "ERROR: create R-JPEG dirp handle failed\n"
    "Test done with return code -16"
)


def test_el_codigo_de_salida_se_muestra_con_signo():
    """Windows lo entrega sin signo: el -16 del SDK salía como 4294967280, que no
    se parece a lo que imprime el propio conversor ni se puede buscar."""
    assert rjpeg.rc_con_signo(4294967280) == -16
    assert rjpeg.rc_con_signo(0) == 0
    assert rjpeg.rc_con_signo(2) == 2
    assert rjpeg.rc_con_signo(None) is None


def test_la_salida_del_sdk_se_queda_en_lo_que_informa():
    resumen = rjpeg.resumir_salida_sdk(SALIDA_SDK_REAL)

    assert "create R-JPEG dirp handle failed" in resumen, "el error debe sobrevivir"
    assert "DIRP API version" not in resumen, "el banner se repite en cada imagen"
    assert "R-JPEG file path" not in resumen, "la ruta ya va en el resto del mensaje"


def test_una_salida_que_es_solo_banner_no_se_queda_vacia():
    """Si todo lo que dijo el conversor es banner, mejor enseñarlo que no decir nada."""
    resumen = rjpeg.resumir_salida_sdk("DIRP API version number : 0x13")

    assert "0x13" in resumen


def test_el_fallo_no_se_vuelca_dos_veces_por_imagen(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    normal = make_dji_jpeg(str(tmp_path / "src.JPG"))
    termica = _hacer_rjpeg(normal, str(tmp_path / "src_T.JPG"))
    input_folder, image_name, dji_utility = _preparar(
        tmp_path, monkeypatch, rc=4294967280, imagen_bytes_desde=termica,
        salida=SALIDA_SDK_REAL)

    texto = _convertir(logger, input_folder, image_name, dji_utility)

    assert texto.count("create R-JPEG dirp handle failed") == 1, (
        "el error del SDK debe aparecer UNA vez, no en el fallo y otra vez en el "
        "aviso del .raw ausente")
    assert "código -16" in texto and "4294967280" not in texto
    assert "Posible error del conversor DJI" in texto, "el aviso del .raw se mantiene"


def test_el_fallo_silencioso_sigue_distinguiendose(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    """rc 0 sin .raw es un fallo silencioso y NO debe confundirse con un rechazo."""
    normal = make_dji_jpeg(str(tmp_path / "src.JPG"))
    termica = _hacer_rjpeg(normal, str(tmp_path / "src_T.JPG"))
    input_folder, image_name, dji_utility = _preparar(
        tmp_path, monkeypatch, rc=0, imagen_bytes_desde=termica)

    texto = _convertir(logger, input_folder, image_name, dji_utility)

    assert "código 0" in texto
    assert "fallo silencioso" in texto


def test_el_error_apunta_a_la_imagen_y_no_al_raw_inexistente(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    """«Imágenes con error» listaba rutas .raw que nunca llegaron a existir."""
    import pipeline

    normal = make_dji_jpeg(str(tmp_path / "src.JPG"))
    termica = _hacer_rjpeg(normal, str(tmp_path / "src_T.JPG"))
    input_folder, image_name, dji_utility = _preparar(
        tmp_path, monkeypatch, rc=4294967280, imagen_bytes_desde=termica,
        salida=SALIDA_SDK_REAL)

    obj = pipeline.SplitImages(logger)
    mensajes = []
    progress = _sink_progress(mensajes)
    obj.convert_dji_image_to_tif(
        input_folder, input_folder, image_name, "exiftool", dji_utility, progress, progress)

    registrados = [str(r) for r in obj.images_error_splitting_images]
    assert registrados, "el fallo debe quedar registrado"
    assert not any(r.endswith(".raw") for r in registrados), (
        "debe registrarse la térmica que falló, no un .raw que nunca existió")
