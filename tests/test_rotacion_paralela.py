"""
La fase de rotación pasa de bucle secuencial a `utils.run_batch` (ProcessPool).

Era la última fase que giraba imagen a imagen: ~0,74 s por par de ficheros
(original + su `_CROP`) × las imágenes del vuelo. Paralelizarla mete tres riesgos
que estos tests fijan, porque ninguno se ve leyendo el código:

1. **Lo que cruza al proceso hijo tiene que ser picklable.** El logger de fichero y
   los signals de Qt no lo son, así que el worker usa stand-ins (`_WorkerLogger`,
   `_CollectingProgress`) y el padre re-emite. Si alguien devuelve el logger real
   al worker, el pool revienta con un error de pickle que no dice nada.
2. **La salida tiene que ser idéntica a la del bucle secuencial.** Estas son las
   fotos que entrega Aerotools: paralelizar no puede cambiar ni un byte.
3. **La térmica NO puede entrar al pool.** Su `*_T.JPG` es un R-JPEG de DJI: si PIL
   lo re-encoda se pierde el payload radiométrico y ya no se puede convertir a TIFF
   nunca más. Además `test_rotacion_90_sentido.py` monkeypatchea
   `_rotate_original_if_rgb`, y un monkeypatch NO cruza al proceso hijo: si la
   térmica se paralelizara, ese test dejaría de comprobar lo que cree comprobar.
"""
import hashlib
import os
import types

from PIL import Image

import pipeline
import utils


def _recording_progress():
    """Signal falso que además cuenta los ticks de la barra de progreso."""
    mensajes = []
    return types.SimpleNamespace(emit=lambda payload=None, *a, **k: mensajes.append(payload)), mensajes


def _obj(logger, planta_folder):
    obj = pipeline.GenStructFolder(logger)
    obj.root_folder = str(planta_folder)
    obj.csvs_root_folder = str(planta_folder / "CSVs")
    obj.total_images_number = 10
    obj.current_image_number = 0
    return obj


def _vuelo_rgb(tmp_path, make_dji_jpeg, con_crop=True, nombre="PLANTA"):
    """Vuelo RGB con dos imágenes y, opcionalmente, su `_CROP` al lado."""
    planta_folder = tmp_path / nombre
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    for n in ("DJI_0001_D.JPG", "DJI_0002_D.JPG"):
        make_dji_jpeg(str(flight_folder / n), gimbal_yaw=-90.0)
        if con_crop:
            base, ext = os.path.splitext(n)
            make_dji_jpeg(str(flight_folder / f"{base}_CROP{ext}"), gimbal_yaw=-90.0)
    return planta_folder, flight_folder


def _sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _rotar(obj, flight_folder, rgb_processing=True):
    progress, mensajes = _recording_progress()
    obj.gen_thumbnails_and_rotate(
        str(flight_folder), rgb_processing=rgb_processing, max_error=0,
        lim_max_270=-10, lim_min_270=-170, lim_max_90=170, lim_min_90=10,
        progress_callback=progress, progress_bar=progress,
    )
    return mensajes


# --------------------------------------------------------- lo que tiene que girar

def test_gira_el_original_y_su_crop(tmp_path, logger, make_dji_jpeg):
    """El `_CROP` viaja con su original: el worker gira los dos o ninguno."""
    planta_folder, flight_folder = _vuelo_rgb(tmp_path, make_dji_jpeg)
    antes = {p: Image.open(flight_folder / p).size for p in os.listdir(flight_folder)}

    _rotar(_obj(logger, planta_folder), flight_folder)

    for nombre, (ancho, alto) in antes.items():
        nuevo = Image.open(flight_folder / nombre).size
        assert nuevo == (alto, ancho), (
            f"{nombre} no se giró (sigue en {nuevo}). Si es un _CROP, el worker está "
            "girando solo el original y la pareja queda descuadrada."
        )


def test_salida_identica_al_bucle_secuencial(tmp_path, logger, make_dji_jpeg):
    """
    El mismo vuelo, girado en paralelo y girado a mano en el proceso padre, tiene
    que dar ficheros byte a byte iguales. Es el invariante que protege al cliente:
    la paralelización es un cambio de rendimiento, no de producto.
    """
    planta_par, vuelo_par = _vuelo_rgb(tmp_path, make_dji_jpeg, nombre="PARALELO")
    planta_sec, vuelo_sec = _vuelo_rgb(tmp_path, make_dji_jpeg, nombre="SECUENCIAL")

    _rotar(_obj(logger, planta_par), vuelo_par)

    # El bucle de antes: `rotate_and_save` llamada una a una desde este mismo proceso.
    obj_sec = _obj(logger, planta_sec)
    progress, _ = _recording_progress()
    for imagen in sorted(n for n in os.listdir(vuelo_sec) if "_CROP" not in n):
        obj_sec.compress_image_obj.rotate_and_save(
            imagen, str(vuelo_sec), Image.ROTATE_90, pipeline._ROTATION_JPEG_QUALITY, progress,
        )

    for nombre in sorted(os.listdir(vuelo_par)):
        assert _sha256(vuelo_par / nombre) == _sha256(vuelo_sec / nombre), (
            f"{nombre} sale distinta del pool que del bucle secuencial. La rotación en "
            "paralelo tiene que ser indistinguible de la de siempre."
        )


# ------------------------------------------------------------ barra de progreso

def test_cuenta_un_tick_por_fichero_girado(tmp_path, logger, make_dji_jpeg):
    """
    La barra avanza por FICHERO, no por imagen: 2 originales + 2 `_CROP` = 4 ticks.
    El `_CROP` lo cuenta el padre al recoger el resultado (`payload["cropped"]`),
    porque el worker no puede tocar el signal.
    """
    planta_folder, flight_folder = _vuelo_rgb(tmp_path, make_dji_jpeg, con_crop=True)
    obj = _obj(logger, planta_folder)

    _rotar(obj, flight_folder)

    assert obj.current_image_number == 4, (
        f"La barra contó {obj.current_image_number} de 4 ficheros. Con el bucle "
        "secuencial contaba original + _CROP; el pool tiene que contar igual."
    )


def test_sin_crop_cuenta_solo_los_originales(tmp_path, logger, make_dji_jpeg):
    """Control del anterior: sin `_CROP` son 2 ticks, no 4."""
    planta_folder, flight_folder = _vuelo_rgb(tmp_path, make_dji_jpeg, con_crop=False)
    obj = _obj(logger, planta_folder)

    _rotar(obj, flight_folder)

    assert obj.current_image_number == 2, (
        f"La barra contó {obj.current_image_number} de 2: se está sumando un _CROP "
        "que no existe."
    )


# ------------------------------------------------- lo que el worker manda de vuelta

def test_los_mensajes_del_worker_llegan_al_log_del_padre(tmp_path, logger, make_dji_jpeg):
    """
    Lo que el worker escribe en su `progress_callback` viaja en el resultado y lo
    re-emite el padre. Sin esto, un fallo dentro del proceso hijo sería invisible
    para el usuario: el pipeline seguiría y la ventana de log no diría nada.
    """
    planta_folder, flight_folder = _vuelo_rgb(tmp_path, make_dji_jpeg, con_crop=False)
    (flight_folder / "DJI_0003_D.JPG").write_bytes(b"esto no es un JPEG")
    obj = _obj(logger, planta_folder)

    mensajes = _rotar(obj, flight_folder)

    # `mensajes` recoge los dos signals a la vez: los textos del log y los enteros
    # de la barra de progreso. Aquí solo interesan los textos.
    textos = [m for m in mensajes if isinstance(m, str)]
    avisos = [m for m in textos if "DJI_0003_D.JPG" in m]
    assert avisos, (
        "El aviso de la imagen ilegible se quedó en el proceso hijo. Los mensajes del "
        f"worker no se están re-emitiendo; solo llegó: {[m for m in textos if m != '.']}"
    )
    assert any("DJI_0003_D.JPG" in p for p in obj.compress_image_obj.images_error_compress), (
        "La imagen fallida no se apuntó en los contadores de error del padre."
    )


def test_una_imagen_corrupta_no_tumba_el_lote(tmp_path, logger, make_dji_jpeg):
    """
    Un vuelo con una foto ilegible tiene que salir con el resto girado. El bucle
    secuencial lo lograba porque `rotate_and_save` captura sus propias excepciones;
    con el pool hay una segunda red (`result["errors"]`) por si el worker muere antes
    de llegar a ese try.
    """
    planta_folder, flight_folder = _vuelo_rgb(tmp_path, make_dji_jpeg, con_crop=False)
    (flight_folder / "DJI_0003_D.JPG").write_bytes(b"esto no es un JPEG")
    antes = {n: Image.open(flight_folder / n).size for n in ("DJI_0001_D.JPG", "DJI_0002_D.JPG")}

    _rotar(_obj(logger, planta_folder), flight_folder)

    for nombre, (ancho, alto) in antes.items():
        assert Image.open(flight_folder / nombre).size == (alto, ancho), (
            f"{nombre} no se giró: una imagen corrupta se ha llevado por delante el lote entero."
        )
    assert (flight_folder / "DJI_0003_D.JPG").read_bytes() == b"esto no es un JPEG", (
        "La imagen ilegible se ha reescrito; debe quedarse como estaba."
    )


# --------------------------------------------------- quién entra al pool y quién no

def test_la_rgb_va_por_run_batch(tmp_path, logger, make_dji_jpeg, monkeypatch):
    """Si alguien vuelve al bucle secuencial, el tiempo de la fase se dispara en silencio."""
    planta_folder, flight_folder = _vuelo_rgb(tmp_path, make_dji_jpeg, con_crop=False)
    llamadas = []
    original = utils.run_batch

    def espia(items, *args, **kwargs):
        llamadas.append(list(items))
        return original(items, *args, **kwargs)

    monkeypatch.setattr(utils, "run_batch", espia)

    _rotar(_obj(logger, planta_folder), flight_folder)

    assert llamadas == [["DJI_0001_D.JPG", "DJI_0002_D.JPG"]], (
        f"La rotación RGB no pasó por utils.run_batch (llamadas: {llamadas})."
    )


def test_la_termica_no_entra_al_pool_ni_se_reencoda(tmp_path, logger, make_dji_jpeg, monkeypatch):
    """
    Doble invariante de la rama térmica, y los dos importan:

    - el `*_T.JPG` sale byte a byte como entró (su payload radiométrico es lo que
      luego lee el conversor DJI);
    - no se lanza pool, porque `test_rotacion_90_sentido.py` espía
      `_rotate_original_if_rgb` y un monkeypatch no cruza al proceso hijo.
    """
    planta_folder = tmp_path / "PLANTA"
    flight_folder = planta_folder / "PB1_V01"
    flight_folder.mkdir(parents=True)
    for n in ("DJI_0001_T.JPG", "DJI_0002_T.JPG"):
        make_dji_jpeg(str(flight_folder / n), gimbal_yaw=-90.0)
    antes = {n: _sha256(flight_folder / n) for n in os.listdir(flight_folder)}

    llamadas = []
    monkeypatch.setattr(utils, "run_batch", lambda items, *a, **k: llamadas.append(list(items)))

    _rotar(_obj(logger, planta_folder), flight_folder, rgb_processing=False)

    assert llamadas == [], "La térmica se ha metido en el pool: rompe el espía de test_rotacion_90_sentido.py."
    for nombre, digest in antes.items():
        assert _sha256(flight_folder / nombre) == digest, (
            f"{nombre} se ha reescrito. Un R-JPEG re-encodado por PIL pierde el payload "
            "radiométrico y ya no se puede convertir a TIFF."
        )
