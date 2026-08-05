"""
La fase 1 (separación RGB / térmica) pasa de bucle secuencial a `utils.run_batch`.

Es la fase más cara del proceso: 188 ms por imagen medidos sobre las RGB reales de
ANTOLIN, de los que el 94 % es el decode+encode de `compress_image`. Iba imagen a
imagen mientras el resto de la máquina miraba.

Paralelizarla mete los riesgos que fijan estos tests, ninguno visible leyendo el código:

1. **La salida tiene que ser byte a byte la del bucle secuencial.** Son las fotos que
   entrega Aerotools: esto es un cambio de rendimiento, no de producto.
2. **El renombrado se calculaba en el bucle padre** y ahora lo hace cada worker. Si el
   nombre destino saliera distinto, las imágenes acabarían con otro nombre sin que
   nadie lo notara hasta ver la carpeta.
3. **Los contadores de error viven en el padre.** Lo que falle dentro de un proceso
   hijo tiene que volver: si no, `get_summarize` diría «sin errores» con imágenes
   perdidas.
4. **La barra de progreso cuenta por imagen**, y `current_image_number` es además lo
   que `get_summarize` compara contra el total para detectar imágenes perdidas.
"""
import hashlib
import os
import types

import pipeline
import utils


RGB = ("DJI_0001_W.JPG", "DJI_0002_W.JPG", "DJI_0003_W.JPG")
TERMICAS = ("DJI_0001_T.JPG", "DJI_0002_T.JPG")


def _progress():
    """Signal falso que recoge todo lo emitido (textos de log y enteros de la barra)."""
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


def _sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _entrada(tmp_path, make_dji_jpeg, nombre="in"):
    """Una carpeta de vuelo con tres RGB y dos térmicas, como sale de la tarjeta."""
    carpeta = tmp_path / nombre
    carpeta.mkdir(parents=True)
    for n in RGB + TERMICAS:
        make_dji_jpeg(str(carpeta / n))
    return carpeta


def _salida(tmp_path, nombre="out"):
    destino = tmp_path / nombre
    (destino / "RGB").mkdir(parents=True)
    (destino / "TERMICA").mkdir(parents=True)
    return destino


def _obj(logger, total=len(RGB) + len(TERMICAS)):
    obj = pipeline.SplitImages(logger)
    obj.total_images_number = total
    obj.current_image_number = 0
    return obj


def _separar(obj, entrada, salida, rename=True):
    progress, mensajes = _progress()
    obj.split_images(
        str(entrada), str(salida), mode=False, min_size="5", thermal_sufix="_T",
        rgb_sufix="_W", compress_checked=True, quality=70,
        progress_callback=progress, rename=rename, progress_bar=progress,
        mismatch_hours=0, mismatch_minutes=0,
    )
    return mensajes


def _arbol(raiz):
    """{ruta relativa: sha256} de todo lo que hay bajo `raiz`."""
    salida = {}
    for base, _dirs, ficheros in os.walk(raiz):
        for f in ficheros:
            completo = os.path.join(base, f)
            salida[os.path.relpath(completo, raiz)] = _sha256(completo)
    return salida


# --------------------------------------------------------------- lo que no puede cambiar

def test_salida_identica_al_bucle_secuencial(tmp_path, logger, make_dji_jpeg):
    """
    El mismo vuelo separado en paralelo y separado a mano en este proceso tiene que
    dar exactamente los mismos ficheros, con los mismos nombres y los mismos bytes.
    """
    entrada = _entrada(tmp_path, make_dji_jpeg)
    par = _salida(tmp_path, "out_par")
    sec = _salida(tmp_path, "out_sec")

    _separar(_obj(logger), entrada, par)

    # El bucle de antes: nombre calculado en el padre y `split_image` una a una.
    obj_sec = _obj(logger)
    progress, _ = _progress()
    for imagen in sorted(obj_sec.utils_obj.get_images_from_dir(str(entrada))):
        nuevo = obj_sec.nombre_destino(imagen, str(entrada), rename=True, mismatch_hours=0, mismatch_minutes=0)
        obj_sec.split_image(
            imagen, str(entrada), str(sec), False, "5", "_T", "_W", True, 70,
            nuevo, True, progress,
        )

    assert _arbol(par) == _arbol(sec), (
        "La separación en paralelo no da el mismo resultado que el bucle secuencial. "
        f"Paralelo: {sorted(_arbol(par))}\nSecuencial: {sorted(_arbol(sec))}"
    )
    assert len(_arbol(par)) == len(RGB) + len(TERMICAS), "Falta alguna imagen en la salida."


def test_el_renombrado_lo_calcula_el_worker_igual_que_el_padre(tmp_path, logger, make_dji_jpeg):
    """
    `nombre_destino` es ahora el ÚNICO sitio donde se decide el nombre de salida, y lo
    llama el worker. El fixture pone DateTimeOriginal 2024-06-01 10:30:00, así que el
    nombre esperado es determinista.
    """
    entrada = _entrada(tmp_path, make_dji_jpeg)
    salida = _salida(tmp_path)

    _separar(_obj(logger), entrada, salida)

    esperados = {f"20240601_103000_{n}" for n in RGB}
    assert set(os.listdir(salida / "RGB")) == esperados
    assert set(os.listdir(salida / "TERMICA")) == {f"20240601_103000_{n}" for n in TERMICAS}


def test_sin_renombrado_conserva_el_nombre_original(tmp_path, logger, make_dji_jpeg):
    """Control del anterior: con `rename=False` el nombre no se toca."""
    entrada = _entrada(tmp_path, make_dji_jpeg)
    salida = _salida(tmp_path)

    _separar(_obj(logger), entrada, salida, rename=False)

    assert set(os.listdir(salida / "RGB")) == set(RGB)
    assert set(os.listdir(salida / "TERMICA")) == set(TERMICAS)


# ------------------------------------------------------------------ barra y contadores

def test_cuenta_un_tick_por_imagen(tmp_path, logger, make_dji_jpeg):
    """
    `current_image_number` no es solo la barra: `get_summarize` lo compara con el total
    y avisa de «no hay correspondencia» si no cuadra. Contar de menos convierte un run
    correcto en un run con error en la ventana de log.
    """
    entrada = _entrada(tmp_path, make_dji_jpeg)
    obj = _obj(logger)

    _separar(obj, entrada, _salida(tmp_path))

    assert obj.current_image_number == len(RGB) + len(TERMICAS), (
        f"La barra contó {obj.current_image_number} de {len(RGB) + len(TERMICAS)} imágenes."
    )


def test_los_errores_del_worker_llegan_a_los_contadores_del_padre(tmp_path, logger, make_dji_jpeg):
    """
    Una RGB ilegible falla dentro de `compress_image`, que corre en el proceso hijo.
    Su contador y su ruta tienen que volver al padre: son los que lee `get_summarize`.
    """
    entrada = _entrada(tmp_path, make_dji_jpeg)
    (entrada / "DJI_0009_W.JPG").write_bytes(b"esto no es un JPEG")
    obj = _obj(logger, total=len(RGB) + len(TERMICAS) + 1)

    _separar(obj, entrada, _salida(tmp_path))

    assert obj.compress_image_obj.error_compress >= 1, (
        "El fallo se quedó en el proceso hijo: el padre cree que no hubo errores."
    )
    assert any("DJI_0009_W" in p for p in obj.compress_image_obj.images_error_compress), (
        f"La imagen fallida no se apuntó: {obj.compress_image_obj.images_error_compress}"
    )


def test_una_imagen_corrupta_no_tumba_el_lote(tmp_path, logger, make_dji_jpeg):
    """El resto del vuelo tiene que salir entero: un fallo por imagen, no por lote."""
    entrada = _entrada(tmp_path, make_dji_jpeg)
    (entrada / "DJI_0009_W.JPG").write_bytes(b"esto no es un JPEG")
    salida = _salida(tmp_path)

    _separar(_obj(logger, total=len(RGB) + len(TERMICAS) + 1), entrada, salida)

    assert len(os.listdir(salida / "RGB")) == len(RGB), (
        "Una imagen corrupta se ha llevado por delante las RGB buenas."
    )
    assert len(os.listdir(salida / "TERMICA")) == len(TERMICAS)


def test_los_mensajes_del_worker_llegan_al_log_del_padre(tmp_path, logger, make_dji_jpeg):
    """
    Lo que el worker manda a su `progress_callback` viaja en el resultado y lo re-emite
    el padre. Sin esto, el usuario no vería el aviso de la imagen que falló.
    """
    entrada = _entrada(tmp_path, make_dji_jpeg)
    (entrada / "DJI_0009_W.JPG").write_bytes(b"esto no es un JPEG")

    mensajes = _separar(_obj(logger, total=len(RGB) + len(TERMICAS) + 1), entrada, _salida(tmp_path))

    textos = [m for m in mensajes if isinstance(m, str)]
    assert any("DJI_0009_W" in m for m in textos), (
        f"El aviso se quedó en el proceso hijo. Solo llegó: {[m for m in textos if m != '.']}"
    )


# ------------------------------------------------------------- quién entra al pool

def test_la_separacion_va_por_run_batch(tmp_path, logger, make_dji_jpeg, monkeypatch):
    """Si alguien vuelve al bucle secuencial, la fase más cara se dispara en silencio."""
    entrada = _entrada(tmp_path, make_dji_jpeg)
    llamadas = []
    original = utils.run_batch

    def espia(items, *args, **kwargs):
        llamadas.append(sorted(items))
        return original(items, *args, **kwargs)

    monkeypatch.setattr(utils, "run_batch", espia)

    _separar(_obj(logger), entrada, _salida(tmp_path))

    assert llamadas == [sorted(RGB + TERMICAS)], (
        f"La separación no pasó por utils.run_batch (llamadas: {llamadas})."
    )


def test_con_stop_no_se_lanza_el_lote(tmp_path, logger, make_dji_jpeg, monkeypatch):
    """
    El botón de parar de la ventana de log. Con el bucle secuencial se comprobaba imagen
    a imagen; con el pool se comprueba antes de lanzar el lote, igual que hace
    `compress_images` desde la 3.4.10. Lo que no puede pasar es que se procese igual.
    """
    entrada = _entrada(tmp_path, make_dji_jpeg)
    salida = _salida(tmp_path)
    obj = _obj(logger)
    obj.set_stop(True)

    llamadas = []
    monkeypatch.setattr(utils, "run_batch", lambda items, *a, **k: llamadas.append(list(items)))

    _separar(obj, entrada, salida)

    assert llamadas == [], "Con stop activo no se debe lanzar el pool."
    assert os.listdir(salida / "RGB") == [], "Con stop activo no se debe procesar ninguna imagen."
