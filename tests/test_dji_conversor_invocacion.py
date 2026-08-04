"""
Invocación del conversor térmico de DJI (dji_irp.exe, del DJI Thermal SDK).

Contexto: corrida real del 2026-08-04 con 199/199 imágenes fallando con ".raw no
existe. Posible error del conversor DJI", sin ninguna pista del motivo en ningún
log de la máquina del usuario. Estos tests fijan las tres condiciones que hacían
ese fallo indiagnosticable:

1. El proceso se lanza con cwd = carpeta del dron. El SDK resuelve libdirp.dll,
   libv_*.dll y libv_list.ini desde el directorio de trabajo; con el CWD de la
   app instalada no los encuentra y aborta sin escribir el .raw.
2. stdin = DEVNULL. dji_irp.exe es un binario de consola y la app se congela sin
   consola (console=False), así que el handle estándar que heredaría es inválido.
3. El código de salida acaba en progress_callback. En la ruta webview el
   organizer_logger no tiene handler de fichero, así que lo que se escribe ahí no
   llega a ningún sitio: progress_callback es el único canal que termina en el
   log de corrida en disco.
"""
import os
import subprocess
import types


def _sink_progress(sink):
    """Callback que acumula lo emitido (strings; los porcentajes se ignoran)."""
    def _emit(*a, **k):
        if a and isinstance(a[0], str):
            sink.append(a[0])
    return types.SimpleNamespace(emit=_emit)


def _preparar(tmp_path, make_dji_jpeg, monkeypatch, rc, salida=""):
    """Monta una térmica de prueba y un dji_irp.exe falso con el rc pedido.

    Devuelve (obj, args_de_llamada, kwargs_capturados, mensajes_emitidos).
    El .raw nunca se escribe: es exactamente el escenario que se está diagnosticando.
    """
    import pipeline

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    image_name = "DJI_0001_T.JPG"
    make_dji_jpeg(str(input_folder / image_name))

    dron_dir = tmp_path / "programas_externos" / "M2EA"
    dron_dir.mkdir(parents=True)
    dji_utility = str(dron_dir / "dji_irp.exe")

    # La rama de invocación por subprocess es la de Windows; en Linux se usa
    # libdirp.so por ctypes y no hay proceso externo que configurar.
    monkeypatch.setattr(pipeline, "_is_windows", lambda: True)

    capturado = {}

    def fake_run(cmd, *args, **kwargs):
        capturado.update(kwargs)
        capturado["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=salida)

    monkeypatch.setattr(subprocess, "run", fake_run)

    return input_folder, image_name, dji_utility, capturado, dron_dir


def test_conversor_dji_se_lanza_en_su_carpeta_y_con_stdin_valido(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    import pipeline

    input_folder, image_name, dji_utility, capturado, dron_dir = _preparar(
        tmp_path, make_dji_jpeg, monkeypatch, rc=0)

    obj = pipeline.SplitImages(logger)
    mensajes = []
    progress = _sink_progress(mensajes)
    obj.convert_dji_image_to_tif(
        str(input_folder), str(input_folder), image_name, "exiftool",
        dji_utility, progress, progress)

    assert capturado.get("cwd") == str(dron_dir), (
        "dji_irp.exe debe ejecutarse con cwd = su propia carpeta, o no encuentra "
        "las DLLs del DJI Thermal SDK ni libv_list.ini")
    assert capturado.get("stdin") is subprocess.DEVNULL, (
        "stdin debe ser DEVNULL: en un .exe sin consola el handle heredado es inválido")


def test_fallo_del_conversor_llega_al_log_de_corrida(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    import pipeline

    input_folder, image_name, dji_utility, _cap, _dir = _preparar(
        tmp_path, make_dji_jpeg, monkeypatch, rc=2, salida="libdirp.dll no encontrada")

    obj = pipeline.SplitImages(logger)
    mensajes = []
    progress = _sink_progress(mensajes)
    obj.convert_dji_image_to_tif(
        str(input_folder), str(input_folder), image_name, "exiftool",
        dji_utility, progress, progress)

    texto = "\n".join(mensajes)
    assert "código 2" in texto, (
        "El código de salida del conversor debe emitirse por progress_callback; "
        "el organizer_logger no tiene destino en la ruta webview")
    assert "libdirp.dll no encontrada" in texto, "La salida del conversor debe llegar al log"


def test_raw_ausente_con_rc_cero_se_distingue_del_fallo_ruidoso(
    tmp_path, logger, make_dji_jpeg, monkeypatch
):
    """Un .raw que no aparece PESE a rc == 0 es un fallo silencioso, y sin el rc
    en el mensaje es indistinguible de un conversor que sí protestó."""
    import pipeline

    input_folder, image_name, dji_utility, _cap, _dir = _preparar(
        tmp_path, make_dji_jpeg, monkeypatch, rc=0)

    obj = pipeline.SplitImages(logger)
    mensajes = []
    progress = _sink_progress(mensajes)
    obj.convert_dji_image_to_tif(
        str(input_folder), str(input_folder), image_name, "exiftool",
        dji_utility, progress, progress)

    ausente = [m for m in mensajes if "Posible error del conversor DJI" in m]
    assert ausente, "Debe seguir avisando de que el .raw no existe"
    assert "código 0" in ausente[0], (
        "El mensaje del .raw ausente debe llevar el código de salida del conversor")
    assert not os.path.exists(os.path.join(str(input_folder), image_name + ".raw"))
