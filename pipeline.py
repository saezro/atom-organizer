"""
pipeline.py

Módulo fusionado (Grupo 4) que aglutina el pipeline de procesado de imágenes y
extracción de datos: compresión/recorte de imágenes, generación de estructura
de carpetas, separación RGB/térmica, procesado RGB de AEROTOOLS y extracción
de TMCs. Resultado de fusionar (refactor mecánico, sin cambios de lógica):
- image_processing/compress_image.py
- gen_struct/gen_struct_folder.py
- gen_struct/split_images.py
- image_processing/rgb_cropping.py
- rgb_aerotools_processing/rgb_processing.py
- atom_extractor/extraction.py
- atom_extractor/atom_extractor_main.py

IMPORTANTE: `process_one_image` e `ImageProcessConfig` deben permanecer
definidos a nivel de MÓDULO (no método, no closure) para seguir siendo
picklables y poder enviarse a un ProcessPoolExecutor (ver utils.run_batch).
"""
import datetime
import datetime as dt
from datetime import timedelta
from dataclasses import dataclass
import gc
import glob
import logging
import os
import pathlib
import shutil
import struct
import subprocess
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import numpy as np
import pandas as pd
import PIL
import PIL.Image
from PIL import Image
import matplotlib
from matplotlib import cm

import utils  # acceso diferido (utils.X) para evitar ciclo de import con utils.py, que hace `import pipeline`.

import exif as exif_management
import exif as em

import sys
import external_tools
from atom_core import rjpeg, sharding


def _is_windows() -> bool:
    return sys.platform.startswith("win")


# Ruta al helper de conversión térmica en Linux (equivalente a dji_irp.exe).
# Vive junto a pipeline.py; se invoca como subproceso efímero por imagen.
_DJI_IRP_LINUX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dji_irp_linux.py")

# --- Copia rápida con reflink (Btrfs/XFS copy-on-write) ----------------------
# En la separación copiamos cada imagen del origen al destino. En un filesystem
# copy-on-write (Btrfs, el disco de trabajo) el reflink clona los extents al
# instante SIN duplicar bytes, y es una COPIA independiente: el original queda
# intacto (si luego se escribiera la copia, el CoW separa los extents). En
# Windows/NTFS o si el clon falla (p. ej. cross-device) cae a shutil.copy2 sin
# regresión. El origen SOLO se lee — nunca puede corromperse.
try:
    import fcntl as _fcntl
    _FICLONE = 0x40049409  # _IOW(0x94, 9, int) en Linux x86_64
except ImportError:  # Windows u otros sin fcntl
    _fcntl = None
    _FICLONE = None

#: ¿Soporta reflink el destino? None = aún no se sabe; False = ya falló una vez.
#: En gcsfuse (Cloud Run) el ioctl NUNCA funciona, y reintentarlo por imagen sale
#: carísimo: el open(dst,"wb") sube un objeto vacío a GCS, el ioctl falla y hay que
#: borrarlo — tres RPCs tiradas antes de cada copia real. Con miles de imágenes eso
#: son miles de round-trips inútiles, así que se prueba UNA vez y se recuerda.
_REFLINK_SOPORTADO: bool | None = None


def _reflink_or_copy(src: str, dst: str) -> None:
    """Copia src→dst conservando el original. Intenta reflink (CoW) y cae a
    shutil.copy2. dst puede ser un directorio (igual que acepta shutil.copy2)."""
    global _REFLINK_SOPORTADO
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    if _fcntl is not None and _REFLINK_SOPORTADO is not False:
        try:
            with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
                _fcntl.ioctl(fdst.fileno(), _FICLONE, fsrc.fileno())
            shutil.copystat(src, dst)
            _REFLINK_SOPORTADO = True
            return
        except OSError:
            # reflink no soportado (cross-device, FS sin CoW, gcsfuse…): limpiamos
            # el dst a medias, lo anotamos para no volver a intentarlo y usamos la
            # copia byte a byte normal.
            _REFLINK_SOPORTADO = False
            try:
                if os.path.exists(dst):
                    os.remove(dst)
            except OSError:
                pass
    shutil.copy2(src, dst)


_LUT_SIZE = 1024
_LUT_CACHE: dict[str, np.ndarray] = {}


def _get_cmap(colormap_name: str):
    """El colormap por nombre, sirva la matplotlib que sirva.

    `matplotlib.cm.get_cmap` estaba deprecado desde la 3.7 y lo BORRARON en la 3.9.
    requirements*.txt pinnea la 3.8.0, así que hoy no revienta en producción, pero
    cualquier entorno con una matplotlib más nueva (el venv de tests, ya) se lleva
    un AttributeError en cuanto se piden imágenes con colormap. `matplotlib.colormaps`
    es la API nueva (3.5+); el fallback cubre las viejas.
    """
    try:
        return matplotlib.colormaps[colormap_name]
    except AttributeError:
        return cm.get_cmap(colormap_name)


def _get_thermal_lut(colormap_name: str) -> np.ndarray:
    """LUT uint8 (1024, 3) precomputada una sola vez por nombre de colormap.
    Usa bin edges izquierdos (endpoint=False): matplotlib cuantiza sus colormaps
    a N=256 niveles internamente, y 1024 = 4*256, por lo que cada uno de los 4
    sub-bins de la LUT cae siempre dentro del mismo bin de 256 que usa la
    referencia continua (cm.get_cmap(name)(normalized)), evitando saltos de
    color >1/255 cerca de los límites de bin.
    """
    lut = _LUT_CACHE.get(colormap_name)
    if lut is None:
        lut = (_get_cmap(colormap_name)(np.linspace(0, 1, _LUT_SIZE, endpoint=False))[:, :3] * 255).astype(np.uint8)
        _LUT_CACHE[colormap_name] = lut
    return lut


@dataclass(frozen=True)
class ImageProcessConfig:
    """Configuración inmutable para process_one_image. Sustituye a los parámetros hoy leídos
    de self/widgets Qt repartidos entre compress_image() y rotate_and_save()."""
    output_path: str
    quality: int = 85
    crop_box: tuple[int, int, int, int] | None = None
    rotate_degrees: int | None = None
    crop_centered_pct: float | None = None


def process_one_image(path: str, cfg: ImageProcessConfig) -> str:
    """
    Abre la imagen en `path` UNA sola vez, aplica crop_box y rotate_degrees en memoria si están
    definidos, y la guarda una única vez en cfg.output_path. Sustituye el patrón de dos ciclos
    open->save independientes que hoy usan CompressImage.compress_image (líneas 152-251) y
    CompressImage.rotate_and_save (líneas 253-363) cuando una misma imagen pasa por ambas fases.

    Función de MÓDULO (no método, no closure): debe ser picklable para poder enviarse a un
    ProcessPoolExecutor (ver Task 17).

    Arguments:
    ---------
    - path - ruta absoluta de la imagen de entrada.
    - cfg - ImageProcessConfig con la ruta de salida, calidad y transformaciones a aplicar.

    Returns:
    --------
    La ruta de salida (cfg.output_path).
    """
    img = Image.open(path)
    try:
        exif = img.getexif()
        if cfg.crop_box is not None and cfg.crop_centered_pct is not None:
            raise ValueError("crop_box y crop_centered_pct son excluyentes: define solo uno de los dos.")
        if cfg.crop_box is not None:
            img = img.crop(cfg.crop_box)
        elif cfg.crop_centered_pct is not None:
            w, h = img.size
            cw = int(w * cfg.crop_centered_pct)
            ch = int(h * cfg.crop_centered_pct)
            left = (w - cw) // 2
            top = (h - ch) // 2
            box = (left, top, left + cw, top + ch)
            img = img.crop(box)
        if cfg.rotate_degrees is not None:
            img = img.transpose(cfg.rotate_degrees)
        # Sin optimize=True: medido en 12 MP cuesta un +66 % de tiempo de encode (195 ms
        # frente a 118 ms) y solo recorta un 6,5 % de tamaño. No cambia un solo píxel —
        # optimize solo recalcula las tablas de Huffman, no recuantiza— así que sale del
        # camino caliente. Mismo criterio que los saves de la fase 5 desde la 3.4.10.
        img.save(cfg.output_path, quality=cfg.quality, exif=exif)
    finally:
        img.close()
    return cfg.output_path


# --- Rotación en paralelo (fase 5) -------------------------------------------
# La rotación era la única fase que seguía yendo en bucle secuencial. Su trabajo es
# decode + transpose + encode del original y de su `_CROP`, que es justo lo que escala
# en procesos. Todo lo que cruza al worker tiene que ser picklable, y ni el logger ni
# los signals de Qt lo son: se sustituyen por los stand-ins de abajo, locales al
# proceso hijo, y el padre re-emite lo que haya que sacar por pantalla.

_ROTATION_JPEG_QUALITY = 45  # La calidad con la que se ha guardado siempre el giro.


class _WorkerLogger:
    """Stand-in de `utils.OrganizerLogger` dentro de un proceso worker.

    Un hijo del pool NO debe construir un OrganizerLogger real: abriría un segundo
    handler sobre el mismo fichero de log que el padre (en Windows lo dejaría
    bloqueado) y su QueueListener moriría con el proceso. Lo que el código de
    rotación manda al log de fichero se descarta aquí; lo que va a la ventana de log
    del usuario viaja de vuelta en `_CollectingProgress` y lo emite el padre.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger("atom.rotate_worker")
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())
        self.logger.propagate = False


class _CollectingProgress:
    """Stand-in del signal Qt `progress_callback` dentro de un proceso worker.

    Los signals de Qt no son picklables y no cruzan a otro proceso. El worker acumula
    aquí los mensajes y el padre los re-emite en el signal de verdad al recoger el
    resultado, así que el usuario ve exactamente lo mismo que con el bucle secuencial.
    """

    def __init__(self) -> None:
        self.messages = []

    def emit(self, payload=None, *args, **kwargs) -> None:
        if payload is not None:
            self.messages.append(payload)


_ROTATE_WORKER_OBJ = None


def _rotate_worker_obj() -> "CompressImage":
    """El `CompressImage` que reutilizan todas las imágenes que caen en el mismo worker.

    Construirlo por imagen sería re-crear Utils + GeneralInformationFromImage decenas de
    veces por vuelo sin motivo. Sus contadores de error se limpian en cada llamada
    (`rotate_one_image`), porque se reportan por imagen.
    """
    global _ROTATE_WORKER_OBJ
    if _ROTATE_WORKER_OBJ is None:
        _ROTATE_WORKER_OBJ = CompressImage(_WorkerLogger())
    return _ROTATE_WORKER_OBJ


def rotate_one_image(image_name: str, input_folder: str, degrees, quality: int) -> dict:
    """
    Gira una imagen RGB (y su `_CROP`) in-place, dentro de un proceso worker.

    Es el equivalente paralelizable de `CompressImage.rotate_and_save`: llama a la MISMA
    función, con el logger y el `progress_callback` sustituidos por stand-ins locales al
    proceso. Función de MÓDULO (no método, no closure) para ser picklable y poder
    enviarse a un ProcessPoolExecutor (ver `utils.run_batch` y el aviso de la cabecera).

    Returns:
    --------
    dict con "image" (el nombre), "cropped" (si además se giró un `_CROP`, que la barra
    de progreso cuenta como una imagen más), "messages" (lo que hay que emitir a la
    ventana de log) y "errors" (rutas que fallaron, para los contadores del padre).
    """
    obj = _rotate_worker_obj()
    obj.error_compress = 0
    obj.images_error_compress = []
    progress = _CollectingProgress()
    cropped = obj.rotate_and_save(image_name, input_folder, degrees, quality, progress)
    return {
        "image": image_name,
        "cropped": bool(cropped),
        "messages": progress.messages,
        "errors": list(obj.images_error_compress),
    }


# --- Separación RGB / térmica en paralelo (fase 1) ---------------------------
# Era la fase más cara del proceso entero y la última grande que seguía en un bucle
# secuencial: 188 ms por imagen RGB medidos sobre ANTOLIN, de los que el 94 % es el
# decode+encode de `compress_image`. Reutiliza los stand-ins de la rotación
# (`_WorkerLogger`, `_CollectingProgress`), porque el problema es el mismo: ni el logger
# de fichero ni los signals de Qt cruzan a un proceso hijo.


@dataclass(frozen=True)
class SplitJobConfig:
    """
    Los parámetros de la separación que son iguales para todas las imágenes de la carpeta.

    Va en un dataclass en vez de sueltos porque tiene que viajar picklado a cada worker;
    `frozen=True` deja claro que ningún proceso hijo puede alterarlo para los demás.
    """

    input_folder: str
    output_folder: str
    mode: bool
    min_size: str
    thermal_sufix: str
    rgb_sufix: str
    compress_checked: bool
    quality: int
    rename: bool
    mismatch_hours: int
    mismatch_minutes: int
    extra_suffix: bool = False


_SPLIT_WORKER_OBJ = None


def _split_worker_obj() -> "SplitImages":
    """El `SplitImages` que reutilizan todas las imágenes que caen en el mismo worker.

    Igual que `_rotate_worker_obj`: construirlo por imagen re-crearía Utils +
    GeneralInformationFromImage cientos de veces por carpeta. Sus contadores de error se
    limpian en cada llamada (`split_one_image`), porque se reportan por imagen.
    """
    global _SPLIT_WORKER_OBJ
    if _SPLIT_WORKER_OBJ is None:
        _SPLIT_WORKER_OBJ = SplitImages(_WorkerLogger())
    return _SPLIT_WORKER_OBJ


def split_one_image(image: str, cfg: "SplitJobConfig") -> dict:
    """
    Separa una imagen (copia o comprime a RGB/TERMICA) dentro de un proceso worker.

    Es el equivalente paralelizable del cuerpo del bucle de `SplitImages.split_images`:
    calcula el nombre de destino con el MISMO `nombre_destino` que usaba el padre y llama
    a la MISMA `split_image`. Función de MÓDULO (no método, no closure) para ser picklable
    y poder enviarse a un ProcessPoolExecutor (ver `utils.run_batch` y el aviso de la
    cabecera del fichero).

    Returns:
    --------
    dict con "image", "new_name" (el nombre con el que se guardó), "messages" (lo que hay
    que emitir a la ventana de log) y los contadores de error que el padre necesita para
    `get_summarize`: compresión y metadatos, cada uno con su lista de rutas.
    """
    obj = _split_worker_obj()
    obj.compress_image_obj.error_compress = 0
    obj.compress_image_obj.images_error_compress = []
    obj.exif_management_obj.error_exif_data = 0
    obj.exif_management_obj.images_error_exif_data = []
    progress = _CollectingProgress()

    new_name = obj.nombre_destino(image, cfg.input_folder, cfg.rename, cfg.mismatch_hours, cfg.mismatch_minutes)
    obj.split_image(
        image, cfg.input_folder, cfg.output_folder, cfg.mode, cfg.min_size, cfg.thermal_sufix,
        cfg.rgb_sufix, cfg.compress_checked, cfg.quality, new_name, cfg.rename, progress,
        cfg.extra_suffix,
    )
    return {
        "image": image,
        "new_name": new_name,
        "messages": progress.messages,
        "error_compress": obj.compress_image_obj.error_compress,
        "images_error_compress": list(obj.compress_image_obj.images_error_compress),
        "error_exif_data": obj.exif_management_obj.error_exif_data,
        "images_error_exif_data": list(obj.exif_management_obj.images_error_exif_data),
    }


class CompressImage:
    """
    Clase que aglutina las funciones centradas en la compresión de las imágenes.

    Methods
    -------
    - start_compression
    - iter- ate_folders
    - compress_images
    - compress_image
    """
    def __init__(self, organizer_logger: "utils.OrganizerLogger") -> None:
        self.utils = utils.Utils(organizer_logger)
        self.exif_management_obj = exif_management.GeneralInformationFromImage(organizer_logger)
        self.organizer_logger = organizer_logger
        self.error_compress = 0
        self.images_error_compress = []
        self.stop = False
        self.current_image_number = 0
        self.total_images_number = 0
    
    def set_stop(self, stop: bool):
        """
        Función que para el proceso modificando el estado de la variable self.stop

        Arguments:
        ---------
        - stop - variable que indica si se puede llevar a cabo o no el procesado. A True se para o no arranca, y a False se lleva a cabo.
        """
        self.stop = stop
        
    def reset_variables(self):
        """
        Resetea las variables necesarias para mostrar la información correctamente en la ventana de log.

        """
        self.current_image_number = 0
        self.total_images_number = 0
        self.error_compress = 0
        self.images_error_compress.clear()
        self.exif_management_obj.error_exif_data = 0
        self.exif_management_obj.images_error_exif_data.clear()

    def get_summarize(self) -> dict:
        """Función que resume diferentes datos al finalizar el proceso. Devuelve un diccionario en el que cada clave es una información del proceso, junto
        con su correspondiente valor, de modo que se pueda mostrar en la ventana del log al finalizar el proceso."""
        summarize_dict= { "Número total de imágenes": self.current_image_number}
        error = False
        if self.total_images_number != self.current_image_number:
            error = True
            self.organizer_logger.logger.info(f"Número total de imágenes en gen_struct_folder: {self.total_images_number}")
            self.organizer_logger.logger.info(f"Número final de imágenes en gen_struct_folder: {self.current_image_number}")
            summarize_dict["Error imágenes"] = f"No hay correspondencia entre número inicial {self.total_images_number} y final de imágenes {self.current_image_number}."

        if self.error_compress > 0:
            error = True
            summarize_dict["Error en compresión"] = "Ha habido {0} errores en la compresión.".format(self.error_compress)
            summarize_dict["Imágenes con error"] = self.images_error_compress

        if self.exif_management_obj.error_exif_data > 0:
            error = True
            summarize_dict["Error en los metadatos"] = "Ha habido {0} errores en los metadatos.".format(self.exif_management_obj.error_exif_data)
            summarize_dict["Imágenes con error"] = self.exif_management_obj.images_error_exif_data

        if not error:
            summarize_dict["Sin Errores"] = "Sin errores durante el proceso"
        else:
            summarize_dict["ERROR"] = "HAN EXISTIDO ERRORES"
        return summarize_dict
    
    # TODO: Podría crear una clase abstracta para implementar una serie de funciones de iteración de carpetas.
    def start_compression(self, input_folder: str, output_folder: str, quality: int, include_subfolders: bool, progress_callback, progress_bar) -> None:
        """
        Función que empieza la compresión de las imágenes. Dependiendo de si incluye o no las subcarpetas comprime solo el directorio raiz
        o empieza a iterar.

        Arguments:
        ---------

        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - quality - calidad de la compresión.
        - include_subfolders - itera por todos los subdirectorios de la carpeta de entrada.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        if include_subfolders:
            self.iterate_folders(input_folder, output_folder, quality, progress_callback, progress_bar)
        else:
            self.compress_images(input_folder, output_folder, quality, progress_callback, progress_bar)
    
    def iterate_folders(self, input_folder: str, output_folder: str, quality: int, progress_callback, progress_bar) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta lleva a cabo la función compress images.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - quality - calidad de la compresión.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        # TODO: Quizás esta función no tenga porqué pertenecer a esta clase. Realmente es como una gestión de carpetas que podremos usar en más sitios.
        self.compress_images(input_folder, output_folder, quality, progress_callback, progress_bar)  # Comprimimos las imágenes que están dentro de input_folder y las guardamos en output_folder.
        for dir in next(os.walk(input_folder))[1]:
            list_dir = os.listdir(output_folder)
            if dir not in list_dir:
                os.makedirs(os.path.join(output_folder,dir))  # Si no existe, creamos un directorio en output_folder con el mismo nombre que en input_folder.
            if not self.stop:
                self.iterate_folders(os.path.join(input_folder,dir), os.path.join(output_folder,dir), quality, progress_callback, progress_bar)

    def compress_images(self, input_folder: str, output_folder: str, quality: int, progress_callback, progress_bar) -> None:
        """
        Función que obtiene las imágenes existentes en el directorio de entrada, recorre dichas imágenes en un bucle y 
        procesa cada una de las imágenes en una función independiente para comprimirlas y copiarlas en el directorio de salida.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - quality - calidad de la compresión.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        # Aquí no estoy distinguiendo entre térmicas y RGBs, estoy comprimiendo todo a saco.
        images = self.utils.get_images_from_dir(input_folder)
        if len(images) > 0:
            self.organizer_logger.logger.debug("Directorio de entrada que contiene imágenes: " + input_folder)
            self.organizer_logger.logger.debug("Directorio de salida: " + output_folder)

            progress_callback.emit("\nProcesando {0} imágenes en el directorio {1}".format(len(images), input_folder) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.

            if not self.stop:  # Se comprueba que no se quiere parar el proceso desde la ventana del log antes de lanzar el batch completo.
                def _worker_args_fn(image):
                    return (
                        os.path.join(input_folder, image),
                        ImageProcessConfig(output_path=os.path.join(output_folder, image), quality=quality),
                    )

                def _on_progress(_pct):
                    self.current_image_number += 1
                    p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                    # y la cantidad actual de imágenes procesadas.
                    progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
                    progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.

                result = utils.run_batch(images, process_one_image, _worker_args_fn, on_progress=_on_progress)
                for image, error_str in result["errors"]:
                    image_output_path = os.path.join(output_folder, image)
                    self.organizer_logger.logger.warning(f"ERROR: No se ha podido comprimir la imagen {image_output_path}: {error_str}")
                    self.error_compress += 1
                    self.images_error_compress.append(image_output_path)

    def compress_image(self, image_name: str, input_folder: str, output_folder: str, quality: int, new_name: str, progress_callback, aerotools_devices: bool = False) -> None:
        """
        Función que comprime la imagen de entrada con la calidad elegida y la copia en el directorio de salida.

        Arguments:
        ---------
        - image_name - nombre de la imagen.
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - quality - calidad de la compresión.
        - new_name - nuevo nombre que se le quiere dar al archivo comprimido.
        - aerotools_devices - indica a la función si estamos tratando imágenes de los dispositivos de AEROTOOLS.
        """
        self.organizer_logger.logger.debug("Factor de calidad: " + str(quality))
        self.organizer_logger.logger.debug("Nombre de la imagen: " + image_name)
        self.organizer_logger.logger.debug("Nuevo nombre: " + new_name)

        img = None
        try:
            img = Image.open(os.path.join(input_folder,image_name))
            # print("Datos EXIF sin modificar")
            # print(exif_management.GeneralInformationFromImage().get_all_exif_data(os.path.join(input_folder, image_name)))

            exif = img.getexif()  # Obtengo los datos exif de la imagen original.
            # self.organizer_logger.logger.debug("Exif size: " + str(len(exif.items())))
            
            if not aerotools_devices:  # Comprobamos si no son de AEROTOOLS, con lo que obtenemos los datos XMP. Si son de AEROTOOLS no tenemos dichos datos y no hace falta llamar a estas funciones.
                gimbal_data = self.exif_management_obj.get_gimbal_yaw_pitch(os.path.join(input_folder, image_name))  # Obtenemos los datos de gimbal del archivo original y antes de guardarlo,
                # por si el directorio de salida es el mismo que el de entrada y sobrescribimos las imágenes. Además, si cambiamos el nombre, necesitamos obtener los datos del gimbal
                # antes de cambiarlo.
                xmp_all_data = self.exif_management_obj.get_xmp_data(os.path.join(input_folder, image_name))
            if new_name == "":  # No hay renombrado.
                pass
            else:
                image_name = new_name  # Hay renombrado y guardamos y comprimimos con el nuevo nombre.
            
            # print("Output Folder: {0}".format(output_folder))
            # print("Image Name: {0}".format(image_name))
            # print("Quality: {0}".format(quality))
            # print("exif: {0}".format(exif.items))
            # print("La altitud es: {0}".format(exif.get_ifd(34853)[6]))
            # exif.get_ifd(34853)[6] = 36.761
            # print("La altitud es: {0}".format(exif.get_ifd(34853)[6]))

            # print("Datos EXIF modificados")
            # exif.items()

            img.save(os.path.join(output_folder, image_name), quality=quality, exif=exif)  # Comprimo la imagen grabando los datos exif original. Si no tiene, no da error. Sin optimize=True: ver process_one_image.
            # self.exif_management_obj.copy_xmp_data(os.path.join(input_folder,image_name),os.path.join(output_folder, image_name))  # Intento de grabar los datos xmp en el archivo comprimido.
            # no me fío mucho, pues algún archivo me ha dado error.
            img.close()
            if not aerotools_devices: # Comprobamos si no son de AEROTOOLS, con lo que grabamos los datos XMP en las imágenes al comprimir para no perder ningún dato. Si son de AEROTOOLS no tenemos dichos datos y no hace falta llamar a estas funciones.
                # Una sola escritura en vez de dos: cada `saving_*_in_xmp` reescribía el JPEG
                # entero, así que grabar gimbal y resto por separado costaba dos pasadas
                # completas sobre el fichero de destino. Ver `exif.saving_all_xmp_data`.
                self.exif_management_obj.saving_all_xmp_data(os.path.join(output_folder, image_name), gimbal_data, xmp_all_data)  # Se graban los datos en el archivo de destino.
                self.check_and_fix_xmp_data(output_folder,image_name, gimbal_data, xmp_all_data, progress_callback)
                

        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: No se encuentra la imagen {os.path.join(output_folder, image_name)} para que PIL la pueda abrir.")
            progress_callback.emit("\nERROR: No se encuentra la imagen {0} para que PIL la pueda abrir.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1
            self.images_error_compress.append(os.path.join(output_folder, image_name)) 
        except PIL.UnidentifiedImageError as u:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: La imagen {os.path.join(output_folder, image_name)} no puede ser abierta e identificada.")
            progress_callback.emit("\nERROR: La imagen {0} no puede ser abierta e identificada.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(u.__str__)
            self.organizer_logger.logger.exception(u)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1         
            self.images_error_compress.append(os.path.join(output_folder, image_name)) 
        except ValueError as v:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: El formato de salida para la imagen {os.path.join(output_folder, image_name)} no ha sido identificado.")
            progress_callback.emit("\nERROR: El formato de salida para la imagen {0} no ha sido identificado.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(v.__str__)
            self.organizer_logger.logger.exception(v)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1         
            self.images_error_compress.append(os.path.join(output_folder, image_name)) 
        except OSError as o:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: La imagen {os.path.join(output_folder, image_name)} no ha sido escrita.")
            progress_callback.emit("\nERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(o.__str__)
            self.organizer_logger.logger.exception(o)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1         
            self.images_error_compress.append(os.path.join(output_folder, image_name)) 
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(os.path.join(output_folder, image_name)))
            progress_callback.emit("\nERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1
            self.images_error_compress.append(os.path.join(output_folder, image_name))
        finally:
            if img is not None and getattr(img, "fp", None) is not None:
                img.close()

    def rotate_and_save(self, image_name: str, input_folder: str, degrees: str, quality: int, progress_callback) -> bool:
        """
        Función que rota las imágenes RGB in-place (realmente hace un transpose) con los grados proporcionados por el parámetro degrees.

        Solo aplica a RGB: la térmica ya no pasa por aquí. Su `*_T.JPG` crudo NO se
        puede re-encodar (se perdería el payload radiométrico de DJI) y, desde que se
        quitaron las miniaturas, no hay nada más que escribir en el paso de rotación;
        el `*_T.JPG` se gira al final, después de la conversión a TIFF.

        Arguments:
        ---------
        - image_name - el nombre de la imagen.
        - input_folder - carpeta de entrada. La imagen se sobrescribe girada aquí mismo.
        - degrees - los ángulos que se quieren girar.
        - quality - calidad de la compresión.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        """
        output_folder = input_folder  # Se gira in-place: entrada y salida son la misma carpeta.
        self.organizer_logger.logger.debug("--------------------------------------------------------------------")
        self.organizer_logger.logger.debug("Image name: " + image_name)
        self.organizer_logger.logger.debug("Input folder: " + input_folder)
        self.organizer_logger.logger.debug("Quality factor: " + str(quality))
        self.organizer_logger.logger.debug("Angle: " + str(degrees))  # 2 es 90º. 4 creo que -90º. Es porque transpose acepta constantes definidas como IMAGE.ROTATE_270 y IMAGE.ROTATE_90

        cropped_images = False
        image_open = None
        crop_imagen_open = None
        try:
            image_open = Image.open(os.path.join(input_folder,image_name))
            file_splitted = os.path.splitext(image_name)
            crop_image_name = file_splitted[0] + "_CROP" + file_splitted[1]
            if os.path.exists(os.path.join(input_folder, crop_image_name)):
                crop_imagen_open = Image.open(os.path.join(input_folder, crop_image_name))

            exif = image_open.getexif()  # Obtengo los datos exif de la imagen original.
            # self.organizer_logger.logger.debug("Exif size: " + str(len(exif.items())))
            image_open = image_open.transpose(degrees)  # Se gira la imagen mediante transpose para mantener las resoluciones correctamente. Si giramos normalmente, la imagen queda
            # recortada.
            if crop_imagen_open:
                crop_imagen_open = crop_imagen_open.transpose(degrees) 
                cropped_images = True

            gimbal_data = self.exif_management_obj.get_gimbal_yaw_pitch(os.path.join(input_folder, image_name))  # Obtenemos los datos de gimbal del archivo antes de guardarlo.
            xmp_all_data = self.exif_management_obj.get_xmp_data(os.path.join(input_folder, image_name))  # Obtenemos el resto de datos xmp del archivo antes de guardarlo.
            # Sin optimize=True a propósito: en un JPEG de 48 MP cuesta un +68 % de tiempo de
            # encode (0,306 s vs 0,182 s medidos) para ahorrar un 3-5 % de tamaño. Este fichero
            # se vuelve a tocar después (conversión/compresión), no es el entregable final.
            image_open.save(os.path.join(input_folder, image_name), quality=quality, exif=exif)
            image_open.close()

            if crop_imagen_open:
                crop_imagen_open.save(os.path.join(input_folder, crop_image_name), quality=quality)  # Sin optimize=True: ver el save del original.
                crop_imagen_open.close()

            self.exif_management_obj.saving_all_xmp_data(os.path.join(input_folder, image_name), gimbal_data, xmp_all_data)  # Se graban los datos en el mismo archivo de entrada, pero rotado, en una sola escritura.

            self.check_and_fix_xmp_data(input_folder,image_name, gimbal_data, xmp_all_data, progress_callback)
            return cropped_images
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: No se encuentra la imagen {os.path.join(output_folder, image_name)} para que PIL la pueda abrir.")
            progress_callback.emit("\nERROR: No se encuentra la imagen {0} para que PIL la pueda abrir.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1
            self.images_error_compress.append(os.path.join(output_folder, image_name))
            return False
        except PIL.UnidentifiedImageError as u:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: La imagen {os.path.join(output_folder, image_name)} no puede ser abierta e identificada.")
            progress_callback.emit("\nERROR: La imagen {0} no puede ser abierta e identificada.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(u.__str__)
            self.organizer_logger.logger.exception(u)
            self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1
            self.images_error_compress.append(os.path.join(output_folder, image_name))
            return False
        except ValueError as v:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: El formato de salida para la imagen {os.path.join(output_folder, image_name)} no ha sido identificado.")
            progress_callback.emit("\nERROR: El formato de salida para la imagen {0} no ha sido identificado.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(v.__str__)
            self.organizer_logger.logger.exception(v)
            self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1
            self.images_error_compress.append(os.path.join(output_folder, image_name))
            return False
        except OSError as o:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: La imagen {os.path.join(output_folder, image_name)} no ha sido escrita.")
            progress_callback.emit("\nERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(o.__str__)
            self.organizer_logger.logger.exception(o)
            self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1
            self.images_error_compress.append(os.path.join(output_folder, image_name))
            return False
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(os.path.join(output_folder, image_name)))
            progress_callback.emit("\nERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(os.path.join(output_folder, image_name)) + "\n")
            self.organizer_logger.logger.error(e.__str__)
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
            self.error_compress += 1
            self.images_error_compress.append(os.path.join(output_folder, image_name))
            return False
        finally:
            if image_open is not None and getattr(image_open, "fp", None) is not None:
                image_open.close()
            if crop_imagen_open is not None and getattr(crop_imagen_open, "fp", None) is not None:
                crop_imagen_open.close()

    def check_and_fix_xmp_data(self, folder, image_name, gimbal_data, xmp_all_data, progress_callback) -> None:
        """
        Función que encapsula la comprobación de la existencia de los datos xmp, y en caso de que no existan, intenta grabarlos un número de veces.
        
        :param folder: carpeta en la que se encuentra la imagen a comprobar.
        :param image_name: nombre de la imagen a comprobar.
        :param gimbal_data: Los datos del gimbal.
        :param xmp_all_data: El resto de datos xmp.
        :param progress_callback: envío de mensajes a la ventana del log.
        """
        # Comprobamos que los datos xmp están en el archivo comprimido
        xmp_data_missing, list_of_missing_keys = self.exif_management_obj.check_xmp_data_using_pyexiv2(os.path.join(folder, image_name))
        # Si no están, se intenta volver a grabarlos dos veces, y si no se puede se lanza un mensaje a la ventana de log.
        if xmp_data_missing:
            attempts = 2
            for i in range(1, attempts + 1):
                self.exif_management_obj.saving_all_xmp_data(os.path.join(folder, image_name), gimbal_data, xmp_all_data)
                xmp_data_missing, list_of_missing_keys = self.exif_management_obj.check_xmp_data_using_pyexiv2(os.path.join(folder, image_name))
                if xmp_data_missing:
                    # progress_callback.emit(f"Attempt {i}")
                    continue
                else:
                    return
            progress_callback.emit(f"\nERROR: las claves {list_of_missing_keys} no están en la imagen comprimida {os.path.join(folder, image_name)}\n")

class GenStructFolder:
    """
    Clase que aglutina las funciones centradas en la generación de la estructura de carpetas.

    Methods
    -------

    """

    def __init__(self, organizer_logger: "utils.OrganizerLogger") -> None:
        self.utils_obj = utils.Utils(organizer_logger)
        self.compress_image_obj = CompressImage(organizer_logger)
        self.exif_management_obj = exif_management.GeneralInformationFromImage(organizer_logger)
        self.stop = False
        self.current_image_number = 0
        self.modo_destino = utils.MODO_SOBRESCRIBIR
        # Imagenes que NO se movieron porque ya habia algo en su ruta exacta
        # (solo en modo `obviar`). Cuentan como procesadas para el cuadre, pero
        # se reportan aparte: "no he hecho nada" y "he movido" no son lo mismo.
        self.skipped_image_number = 0
        self.total_images_number = 0
        self.error_gen_struct_folder = 0
        self.errors_type_gen_struct_folder = []
        # Avisos NO fatales (imágenes fuera de todo vuelo del estadillo -> SIN_ORDENAR).
        # Se cuentan aparte de los errores para que el run termine en ámbar, no en rojo.
        self.warning_gen_struct_folder = 0
        self.warnings_type_gen_struct_folder = []
        self.organizer_logger = organizer_logger
        self.final_results = []
        # Nº de operaciones de E/S en paralelo (leer EXIF, mover ficheros). Sobre GCS
        # (gcsfuse) el cuello de botella es la LATENCIA por fichero, no la CPU: cada
        # imagen es un round-trip. Con hilos se solapan, y como PIL/os.replace sueltan
        # el GIL en la parte de E/S, escalan bien aunque sean threads.
        self.max_io_workers = utils.max_io_workers()
        # Protege los contadores compartidos (current_image_number, errores) entre workers.
        self._stats_lock = threading.Lock()
        # Caché de timestamps EXIF: {ruta_absoluta: datetime|None}. Se rellena una sola
        # vez por corrida para no releer el EXIF de la misma imagen en cada vuelo.
        self._timestamps_cache = {}
        # Registro por carpeta de vuelo (PBx_Vy) del giro aplicado, para poder
        # reportarlo después. {nombre_vuelo: {"vuelo", "grados", "imagenes", "modo", "detalle"}}
        self.giros_por_vuelo = {}

    def set_stop(self, stop: bool):
        """
        Función que para el proceso modificando el estado de la variable self.stop

        Arguments:
        ---------
        - stop - variable que indica si se puede llevar a cabo o no el procesado. A True se para o no arranca, y a False se lleva a cabo.
        """
        self.stop = stop

    def get_giros_por_vuelo(self) -> list:
        """
        Devuelve el registro de giros aplicados por carpeta de vuelo (PBx_Vy),
        ordenado por nombre de vuelo. `[]` si no se registró ninguno.
        """
        return [self.giros_por_vuelo[vuelo] for vuelo in sorted(self.giros_por_vuelo.keys())]

    def reset_variables(self, main_process = True, gen_thumbnails = False, convert_to_tiff = False, progress_callback = None):
        """
        Resetea las variables necesarias para mostrar la información correctamente en la ventana de log.
        
        Arguments:
        ---------
        - main_process - Indica que se trata del proceso principal dentro de un hilo o de un proceso secundario, como una opción a mayores a realizar.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        """

        self.current_image_number = 0
        self.skipped_image_number = 0
        self.total_images_number = 0
        self.exif_management_obj.error_exif_data = 0
        self.exif_management_obj.images_error_exif_data.clear()
        self.compress_image_obj.error_compress = 0
        self.compress_image_obj.images_error_compress.clear()
        self.error_gen_struct_folder = 0
        self.errors_type_gen_struct_folder.clear()

        if not main_process and not gen_thumbnails:
            progress_callback.emit("\n----------------------------------------------------\n")
            progress_callback.emit("SUBPROCESO: GENERAR ESTRUCTURA DE CARPETAS\n")  # Enviamos el texto para indicar que arranca el proceso de generar la estructura de carpetas, pero que no es el proceso principal, siendo posterior.
            progress_callback.emit('Organización de las imágenes en cada carpeta de vuelo\n')
            progress_callback.emit("----------------------------------------------------\n")

        if not main_process and gen_thumbnails:
            progress_callback.emit("----------------------------------------------------\n")
            progress_callback.emit("SUBPROCESO: ROTACIÓN\n")  # Enviamos el texto para indicar que arranca el proceso de rotación, pero que no es el proceso principal, siendo posterior.
            progress_callback.emit("----------------------------------------------------")
            
        if not main_process and convert_to_tiff:
            pass
                    
    def get_summarize(self) -> dict:
        """Función que resume diferentes datos al finalizar el proceso. Devuelve un diccionario en el que cada clave es una información del proceso, junto
        con su correspondiente valor, de modo que se pueda mostrar en la ventana del log al finalizar el proceso."""
        summarize_dict= { "Número total de imágenes": self.current_image_number}
        error = False
        if self.total_images_number != self.current_image_number:
            error = True
            self.organizer_logger.logger.info(f"Número total de imágenes en gen_struct_folder: {self.total_images_number}")
            self.organizer_logger.logger.info(f"Número final de imágenes en gen_struct_folder: {self.current_image_number}")
            summarize_dict["Error imágenes"] = f"No hay correspondencia entre número inicial {self.total_images_number} y final de imágenes {self.current_image_number}."
        
        if self.exif_management_obj.error_exif_data > 0:
            error = True
            summarize_dict["Error en los metadatos"] = "Ha habido {0} errores en los metadatos.".format(self.exif_management_obj.error_exif_data)
            summarize_dict["Imágenes con error"] = self.exif_management_obj.images_error_exif_data

        if self.compress_image_obj.error_compress > 0:
            error = True
            summarize_dict["Error al rotar"] = "Ha habido {0} errores en la rotación.".format(self.compress_image_obj.error_compress)
            summarize_dict["Imágenes con error"] = self.compress_image_obj.images_error_compress

        if self.error_gen_struct_folder > 0:
            error = True
            summarize_dict["Error en gen_struct_folder"] = "Ha habido {0} errores en el procesado.".format(self.error_gen_struct_folder)
            summarize_dict["Errores"] = self.errors_type_gen_struct_folder

        if not error:
            summarize_dict["Sin Errores"] = "Sin errores durante el proceso"
        else:
            summarize_dict["ERROR"] = "HAN EXISTIDO ERRORES"

        # Avisos NO fatales (SIN_ORDENAR). Marcador único para que el orquestador
        # headless lo cuente una sola vez y ponga el run en ámbar. El texto NO
        # contiene "error" para no disparar el conteo de errores.
        # Omitidas por `--modo-destino obviar`: ya habia un fichero en la ruta
        # exacta. No es un fallo — es literalmente lo que se pidio — pero tiene
        # que verse, o una reorganizacion que no reorganizo nada pasaria por
        # verde indistinguible de una que si.
        if self.skipped_image_number > 0:
            summarize_dict["Imagenes omitidas"] = (
                f"{self.skipped_image_number} imagenes ya existian en su ruta de "
                f"destino y no se movieron (modo {self.modo_destino}).")
            summarize_dict["AVISO"] = "HA HABIDO AVISOS"

        if self.warning_gen_struct_folder > 0:
            summarize_dict["Detalle avisos"] = self.warnings_type_gen_struct_folder
            summarize_dict["AVISO"] = "HA HABIDO AVISOS"
        return summarize_dict

    def checking_results_gen_struct_folder(self, output_folder, progress_callback):
        # Imágenes que quedaron en la raíz de TERMICA/RGB tras gen_folder_struct =
        # no encajan en ningún vuelo del estadillo (p. ej. un clip huérfano cuyo
        # rango horario no cubre el estadillo). Antes se contaban como ERROR FATAL
        # (rojo). Decisión de Cas (2026-07-22): NO es fatal -> se apartan a la
        # carpeta SIN_ORDENAR y el run termina en AVISO (ámbar), sin perder datos.
        thermal_folder = self.utils_obj.get_images_from_dir(os.path.join(output_folder, "TERMICA"))
        rgb_folder = self.utils_obj.get_images_from_dir(os.path.join(output_folder, "RGB"))
        thermal_folder_list_length = len(thermal_folder)
        rgb_folder_list_length = len(rgb_folder)
        if thermal_folder_list_length > 0 or rgb_folder_list_length > 0:
            self.utils_obj.prepare_output_folder(output_folder, ["SIN_ORDENAR"])
        if thermal_folder_list_length > 0:
            self.utils_obj.prepare_output_folder(os.path.join(output_folder, "SIN_ORDENAR"), ["TERMICA"])
            for imagen in thermal_folder:
                # `descartar_origen_si_existe`: con `obviar` y una pasada previa,
                # la copia ya esta en SIN_ORDENAR. Sin esto el origen se quedaria
                # varado en la raiz y la imagen acabaria en los dos sitios — el
                # contador de abajo la suma igual, asi que el duplicado se colaba
                # en silencio (62 imagenes en la op 9 de la inspeccion 330).
                utils.safe_move(os.path.join(output_folder, "TERMICA", imagen),
                                os.path.join(output_folder, "SIN_ORDENAR", "TERMICA", imagen),
                                modo=self.modo_destino, descartar_origen_si_existe=True)
            # Cuentan como procesadas (apartadas a SIN_ORDENAR): así current == total
            # y no se dispara el falso "No hay correspondencia" que teñiría de rojo.
            self.current_image_number += thermal_folder_list_length
            self.warning_gen_struct_folder += 1
            self.warnings_type_gen_struct_folder.append(f"{thermal_folder_list_length} imágenes térmicas fuera del estadillo movidas a SIN_ORDENAR")
            progress_callback.emit(f"AVISO: {thermal_folder_list_length} imágenes térmicas fuera del estadillo movidas a SIN_ORDENAR.\n")
        if rgb_folder_list_length > 0:
            self.utils_obj.prepare_output_folder(os.path.join(output_folder, "SIN_ORDENAR"), ["RGB"])
            for imagen in rgb_folder:
                utils.safe_move(os.path.join(output_folder, "RGB", imagen),
                                os.path.join(output_folder, "SIN_ORDENAR", "RGB", imagen),
                                modo=self.modo_destino, descartar_origen_si_existe=True)
            self.current_image_number += rgb_folder_list_length
            self.warning_gen_struct_folder += 1
            self.warnings_type_gen_struct_folder.append(f"{rgb_folder_list_length} imágenes rgb fuera del estadillo movidas a SIN_ORDENAR")
            progress_callback.emit(f"AVISO: {rgb_folder_list_length} imágenes rgb fuera del estadillo movidas a SIN_ORDENAR.\n")
        # Se devuelven los conteos para que el llamador pueda detectar el caso
        # degenerado "TODO fuera del estadillo" y abortar con un mensaje útil,
        # en vez de dejar que las fases siguientes procesen 0 imágenes en verde.
        return thermal_folder_list_length, rgb_folder_list_length

    def gen_folder_struct(self, path_estadillo: str, input_folder: str, output_folder: str, organize_images: bool, seconds_range: float, desfase_horas: int, desfase_minutos: int,
                          progress_callback, progress_bar, extra_suffix: bool = False, include_v: bool = True,
                          shard_index: int = 0, shard_count: int = 1) -> None:
        """
        Función que genera la estructura de carpetas necesaria a partir del estadillo cargado y, si organize_images está marcado, mueve las imágenes existentes en
        input_folder a su carpeta de vuelo correspondiente.
        En el caso de organizar las imágenes, éstas tienen que estar ya separadas entre RGB y térmicas dentro de sus carpetas correspondientes (RGB y TERMICA).

        Arguments:
        ---------
        - path_estadillo - ubicación del estadillo.
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - organize_images - si está a True, envía cada imagen a su directorio PVX_VX correspondiente. A False, solo genera las carpetas.
        - seconds_range - Variable para controlar el margen de segundos que se da a la hora de inicio de vuelo y a la hora final. Se resta estos segundos
        a la hora de inicio marcada por el estadillo y se suman estos segundos a la hora final.
        - desfase_horas - Variable que indica el posible desfase entre fecha del estadillo y la fecha de las imágenes medido en horas. Desfase = hora_imagen - hora_estadillo.
        Este valor se le suma a la hora del estadillo para equipararlo a la hora de la imagen. Puede ser un valor negativo si la hora de la imagen va por detrás de la del estadillo.
        - desfase_minutos - Variable que indica el posible desfase entre fecha del estadillo y la fecha de las imágenes medido en minutos. Desfase = hora_imagen - hora_estadillo.
        Este valor se le suma a la hora del estadillo para equipararlo a la hora de la imagen. Puede ser un valor negativo si la hora de la imagen va por detrás de la del estadillo.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - extra_suffix - si es True indica que el sufijo rgb es el extra que se añade en el interfaz. En caso contrario, seguimos el flujo habitual.
        - shard_index / shard_count - reparto entre tareas paralelas (ver
        `atom_core.sharding.toca_imagen`). Cada tarea lee el estadillo entero y
        crea todas las carpetas —es idempotente y sale más barato que
        coordinarse—, pero solo mueve las imágenes que le tocan. Por defecto
        `(0, 1)` = una sola tarea, el comportamiento de siempre.
        """
        self.organizer_logger.logger.debug("---------------------------------------------------------------------")
        self.organizer_logger.logger.debug("Estadillo: " + path_estadillo)
        self.organizer_logger.logger.debug("Output folder: " + output_folder)
        
        self.utils_obj.prepare_output_folder(output_folder, ["RGB", "TERMICA","ESTADILLOS"])

        output_path_estadillo = os.path.join(output_folder,"ESTADILLOS")

        # output_path_estadillo es una carpeta, mientras que path_estadillo es el archivo. En la siguiente línea juntamos output_path_estadillo con el nombre.
        dest_estadillo = os.path.join(output_path_estadillo, os.path.basename(path_estadillo))
        # La copia del estadillo la hace SOLO la tarea 0. El bloque de abajo
        # resuelve el choque de nombres añadiendo un contador (`_1`, `_2`…), que
        # con N tareas a la vez no protege: las ocho ven la carpeta sin su copia,
        # las ocho copian, y en ESTADILLOS/ acaban ocho estadillos idénticos
        # numerados. Es el único fichero que escriben todas por igual.
        # Comparamos transformando en minúsculas e igualando los / o \
        if shard_index == 0 and os.path.normcase(os.path.abspath(path_estadillo)) != os.path.normcase(os.path.abspath(dest_estadillo)):
            self.organizer_logger.logger.info(f"Los estadillos son diferentes") # Son diferentes, así que hay que copiarlos y compruebo si hay algún estadillo en la carpeta de destino con el mismo nombre y en el caso de haberlo genero un nombre distinto añadiendo un contador al nombre.
            dest = pathlib.Path(output_folder) / "ESTADILLOS" / pathlib.Path(path_estadillo).name
            counter = 1
            while dest.exists():
                self.organizer_logger.logger.info(f"Había un estadillo con el mismo nombre. Usamos un counter - Valor: {counter}")
                dest = dest.with_stem(f"{dest.stem}_{counter}")
                counter += 1
            shutil.copy2(path_estadillo, dest)
        else:
            self.organizer_logger.logger.info(f"Los estadillos son iguales. No se copia nada.")
  
        # Leer el estadillo usando Pandas
        estadillo = pd.read_csv(path_estadillo, sep=";")
        nombres_columnas = self.utils_obj.get_nombres_columnas(list(estadillo.columns.values))
        
        # Guardamos en una variable el número de vuelos
        # TODO: Para mostrar la barra de progreso con este proceso vamos a tener en cuenta el número de vuelos.
        numeroVuelos = estadillo.shape[0]

        # Franja horaria y carpetas de destino de cada vuelo, EN EL ORDEN DEL
        # ESTADILLO. Ese orden es el desempate cuando dos vuelos solapan: la imagen
        # se la queda el primero que la reclama, igual que antes —cuando el bucle
        # relistaba la carpeta en cada vuelo y el primero se llevaba lo suyo.
        ventanas: list[dict] = []

        for vuelo in range(numeroVuelos):
            if not self.stop:
                # ---INFORMACION DEL VUELO---
                # Leemos el PB al que corresponde ese vuelo
                pb = estadillo[nombres_columnas['PB']][vuelo]
                # leemos el número de vuelo dentro del PB
                vuelo_pb = estadillo[nombres_columnas['Vuelo']][vuelo]

                # Leemos la fecha del estadillo
                fecha_estadillo = estadillo[nombres_columnas['Fecha']][vuelo]

                # Leemos la hora de comienzo del vuelo
                horaInicio = estadillo[nombres_columnas['Hora_de_inicio']][vuelo]
                # Transformamos horaInicio en string. De hecho, al leer normalmente ya es un string, pero si está vacío en el csv se lee como un float, y es un nan.
                # Así que lo transformamos igualmente para luego comprobarlo.
                horaInicio = str(horaInicio)
                
                # Leemos la hora de finalizacion del vuelo
                horaFinal = estadillo[nombres_columnas['Hora_final']][vuelo]
                # Transformamos horaFinal en string. De hecho, al leer normalmente ya es un string, pero si está vacío en el csv se lee como un float, y es un nan.
                # Así que lo transformamos igualmente para luego comprobarlo.
                horaFinal = str(horaFinal)
                # --- ---
                
                if(horaInicio == "" or horaInicio is None or horaInicio == "nan"):
                    progress_callback.emit("\nWARNING: No existe hora inicio en PB {0} y vuelo {1}\n".format(pb, vuelo_pb))
                    self.error_gen_struct_folder += 1
                    self.errors_type_gen_struct_folder.append("No existe hora inicio en PB {0} y vuelo {1}\n".format(pb, vuelo_pb)) 
                    continue

                if(horaFinal == "" or horaFinal is None or horaFinal == "nan"):
                    progress_callback.emit("\nWARNING: No existe hora final en PB {0} y vuelo {1}\n".format(pb, vuelo_pb))
                    self.error_gen_struct_folder += 1
                    self.errors_type_gen_struct_folder.append("No existe hora final en PB {0} y vuelo {1}\n".format(pb, vuelo_pb)) 
                    continue

                self.organizer_logger.logger.info(f'Analizando el PB:{pb}, vuelo:{vuelo_pb}. Hora inicio vuelo:{horaInicio}. Hora fin vuelo:{horaFinal}')
                progress_callback.emit(f'\nAnalizando el PB:{pb}, vuelo:{vuelo_pb}. Hora inicio vuelo:{horaInicio}. Hora fin vuelo:{horaFinal}')

                # Creamos el nombre para la carpeta del PB
                nombreCarpeta_PB = 'PB'+str(pb)
                # Creamos el nombre para la carpeta del vuelo del PB
                if include_v:
                    nombreCarpeta_PB_vuelo = nombreCarpeta_PB+'_V'+str(vuelo_pb)
                else:
                    nombreCarpeta_PB_vuelo = nombreCarpeta_PB+'_'+str(vuelo_pb)

                # Las carpetas las crean TODAS las tareas, no solo la 0: son
                # `makedirs(exist_ok=True)`, cuestan un round-trip y así ninguna
                # tarea depende de que otra haya llegado antes para tener dónde
                # dejar sus imágenes (entre tareas del Job no hay ninguna barrera).
                #-- Carpeta Termica--
                # Creamos el path a la carpeta del PB dentro de la carpeta Termica
                pathTermica_PB = os.path.join(os.path.join(output_folder,"TERMICA"),nombreCarpeta_PB)

                # Si no existe todavia la carpeta del PB dentro de termica, la creamos
                os.makedirs(pathTermica_PB, exist_ok=True)

                # Creamos el path a la carpeta del vuelo dentro de la carpeta PB
                pathTermica_PB_vuelo = os.path.join(pathTermica_PB,nombreCarpeta_PB_vuelo)

                # Si no existe todavia la carpeta del vuelo dentro del PB, la creamos
                os.makedirs(pathTermica_PB_vuelo, exist_ok=True)

                #-- Carpeta RGB--
                # Creamos el path a la carpeta del PB dentro de la carpeta RGB
                pathRGB_PB = os.path.join(os.path.join(output_folder,"RGB"),nombreCarpeta_PB)

                # Si no existe todavia la carpeta de RGB, la creamos
                os.makedirs(pathRGB_PB, exist_ok=True)

                # Creamos el path a la carpeta del vuelo dentro de la carpeta PB
                pathRGB_PB_vuelo = os.path.join(pathRGB_PB,nombreCarpeta_PB_vuelo)

                # Si no existe todavia la carpeta del vuelo, la creamos
                os.makedirs(pathRGB_PB_vuelo, exist_ok=True)
                #-- --

                if extra_suffix:
                    #-- En este caso tenemos que crear la carpeta RGB_Extra y organizar dentro las imágenes --
                    # Creamos el path a la carpeta del PB dentro de la carpeta RGB_Extra
                    pathRGB_Extra_PB = os.path.join(os.path.join(output_folder,"RGB_extra"),nombreCarpeta_PB)

                    # Si no existe todavia la carpeta de RGB_Extra, la creamos
                    os.makedirs(pathRGB_Extra_PB, exist_ok=True)

                    # Creamos el path a la carpeta del vuelo dentro de la carpeta PB
                    pathRGB_Extra_PB_vuelo = os.path.join(pathRGB_Extra_PB,nombreCarpeta_PB_vuelo)

                    # Si no existe todavia la carpeta del vuelo, la creamos
                    os.makedirs(pathRGB_Extra_PB_vuelo, exist_ok=True)

                if organize_images:
                    # Solo se anota a quién pertenece cada franja horaria. El reparto
                    # de las imágenes se hace después, de una vez, en
                    # `organizar_imagenes_en_vuelos` (ver ahí el porqué).
                    destinos = {"TERMICA": pathTermica_PB_vuelo, "RGB": pathRGB_PB_vuelo}
                    if extra_suffix:
                        destinos["RGB_extra"] = pathRGB_Extra_PB_vuelo
                    inicio, fin = self.ventana_horaria_vuelo(
                        fecha_estadillo, horaInicio, horaFinal, seconds_range,
                        desfase_horas, desfase_minutos)
                    ventanas.append({"inicio": inicio, "fin": fin, "destinos": destinos})

        if organize_images and not self.stop:
            self.organizar_imagenes_en_vuelos(input_folder, ventanas, extra_suffix,
                                              progress_callback, progress_bar,
                                              shard_index, shard_count)

    def organizar_imagenes_en_vuelos(self, input_folder: str, ventanas: list[dict],
                                     extra_suffix: bool, progress_callback, progress_bar,
                                     shard_index: int = 0, shard_count: int = 1) -> None:
        """Mueve cada imagen del destino plano a la carpeta del vuelo que la reclama.

        Es el bucle de `gen_folder_struct` del revés: antes era «por vuelo, mira
        todas las imágenes» y ahora es «por imagen, busca su vuelo». El resultado
        es el mismo —las ventanas se recorren en el orden del estadillo, así que un
        solape lo sigue ganando el primer vuelo del CSV—, pero el trabajo cambia de
        forma en dos sitios que sobre gcsfuse se pagan caros:

        - Un solo `listdir` por carpeta. El bucle viejo relistaba `TERMICA/` y
          `RGB/` enteras **en cada vuelo** para ver qué quedaba sin repartir.
        - Un solo pool de hilos para TODOS los movimientos. Antes había uno por
          vuelo y carpeta: pools cortos, con listas pequeñas, montándose y
          desmontándose sin llegar a saturar la latencia de red que domina aquí.

        Arguments:
        ---------
        - input_folder - destino ya separado en RGB/TERMICA (aún plano).
        - ventanas - `{inicio, fin, destinos}` por vuelo, en el orden del estadillo.
        - extra_suffix - si hay que repartir también `RGB_extra`.
        - progress_callback / progress_bar - los signals de siempre.
        - shard_index / shard_count - reparto entre tareas. Cada una se queda con
          las imágenes cuyo NOMBRE le asigna `sharding.toca_imagen` —no con un
          tramo del listado, que aquí no vale: la carpeta se está vaciando
          mientras se lista, así que el índice de una foto depende de cuándo mire
          cada tarea. Ver el porqué completo en `toca_imagen`.
        """
        subcarpetas = ["TERMICA", "RGB"] + (["RGB_extra"] if extra_suffix else [])
        etiquetas = {"TERMICA": "Térmicas", "RGB": "RGB", "RGB_extra": "RGB Extra"}

        def _es_mia(nombre: str) -> bool:
            return sharding.toca_imagen(nombre, shard_index, shard_count)

        # El EXIF, en paralelo y antes de nada: a partir de aquí la asignación a
        # vuelos es pura aritmética sobre la caché. Solo el de las imágenes
        # PROPIAS: leer el de las 2.516 en las ocho tareas son ocho veces el
        # mismo trabajo de red, que es justo lo que se está intentando repartir.
        self.precargar_timestamps([os.path.join(input_folder, s) for s in subcarpetas],
                                  progress_callback, filtro=_es_mia)

        movimientos: list[tuple[str, str]] = []
        for sub in subcarpetas:
            carpeta = os.path.join(input_folder, sub)
            if not os.path.isdir(carpeta):
                continue
            por_destino: dict[str, int] = {}
            for imagen in sorted(os.listdir(carpeta)):
                if not imagen.endswith(('jpg', 'JPG')):
                    continue
                if not _es_mia(imagen):
                    continue
                origen = os.path.join(carpeta, imagen)
                try:
                    fecha_hora_imagen = self._get_timestamp_cached(origen)
                except Exception as e:
                    self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
                    self.organizer_logger.logger.warning("ERROR: Error al obtener datos de fecha y hora de la imagen {0}".format(origen))
                    self.organizer_logger.logger.error(e.__str__)
                    self.organizer_logger.logger.exception(e)
                    self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
                    self.error_gen_struct_folder += 1
                    self.errors_type_gen_struct_folder.append(origen)
                    continue
                if fecha_hora_imagen is None:
                    continue
                # El primer vuelo del estadillo que la reclama se la queda. Las que
                # no caen en ninguna ventana se quedan donde están: se las lleva
                # después `checking_results_gen_struct_folder` a SIN_ORDENAR.
                destino = next((v["destinos"][sub] for v in ventanas
                                if sub in v["destinos"]
                                and v["inicio"] < fecha_hora_imagen < v["fin"]), None)
                if destino is None:
                    continue
                movimientos.append((origen, os.path.join(destino, imagen)))
                por_destino[destino] = por_destino.get(destino, 0) + 1

            for destino, cuantas in por_destino.items():
                mensaje = "\nMoviendo {0} imágenes {1} al directorio {2}\n".format(
                    cuantas, etiquetas[sub], destino)
                self.organizer_logger.logger.info(mensaje)
                progress_callback.emit(mensaje)

        self._mover_pares(movimientos, progress_callback, progress_bar)


    def obtenerListaImagenesVuelo(self, input_folder:str, fecha: str, horaInicio: str, horaFinal: str, listaImagenes: list[str], margen_segundos: float, desfase_horas: int, desfase_minutos: int) -> list[str]:
        """
        Funcion para obtener las imagenes que estan dentro de la franja horaria del vuelo.

        Arguments:
        ---------
        - fecha - Fecha en la que se ha realizado el vuelo.
        - horaInicio - Hora de inicio del vuelo.
        - horaFinal - Hora de finalización del vuelo.
        - listaImagenes - Lista con todas las imágenes, de la que se obtendrá el rango de ellas dentro de las horas inicial y final.
        - margen_segundos - Variable para controlar el margen de segundos que se da a la hora de inicio de vuelo y a la hora final. 
        Se resta estos segundos a la hora de inicio marcada por el estadillo y se suman estos segundos a la hora final.
        - desfase_horas - Variable que indica el posible desfase entre fecha del estadillo y la fecha de las imágenes medido en horas. Desfase = hora_imagen - hora_estadillo.
        Este valor se le suma a la hora del estadillo para equipararlo a la hora de la imagen. Puede ser un valor negativo si la hora de la imagen va por detrás de la del estadillo.
        - desfase_minutos - Variable que indica el posible desfase entre fecha del estadillo y la fecha de las imágenes medido en minutos. Desfase = hora_imagen - hora_estadillo.
        Este valor se le suma a la hora del estadillo para equipararlo a la hora de la imagen. Puede ser un valor negativo si la hora de la imagen va por detrás de la del estadillo.
        """

        # Creamos una lista vacia donde guardar las imagenes del vuelo
        listaImagenesVuelo = []

        fecha_hora_inicio, fecha_hora_fin = self.ventana_horaria_vuelo(
            fecha, horaInicio, horaFinal, margen_segundos, desfase_horas, desfase_minutos)

        # Bucle para toda la lista de fotos
        for imagen in listaImagenes:
            try:
                fecha_hora_imagen = self._get_timestamp_cached(os.path.join(input_folder, imagen))

                # Si la hora de la imagen
                if fecha_hora_imagen is not None and fecha_hora_inicio < fecha_hora_imagen and fecha_hora_imagen < fecha_hora_fin:
                    listaImagenesVuelo.append(imagen)
            except Exception as e:
                self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
                self.organizer_logger.logger.warning("ERROR: Error al obtener datos de fecha y hora de la imagen {0}".format(os.path.join(input_folder, imagen)))
                self.organizer_logger.logger.error(e.__str__)
                self.organizer_logger.logger.exception(e)
                self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')       
                self.error_gen_struct_folder += 1
                self.errors_type_gen_struct_folder.append(os.path.join(input_folder, imagen)) 

        return listaImagenesVuelo

    def ventana_horaria_vuelo(self, fecha: str, horaInicio: str, horaFinal: str,
                              margen_segundos: float, desfase_horas: int,
                              desfase_minutos: int) -> tuple:
        """Franja `(inicio, fin)` en la que una imagen pertenece a este vuelo.

        Estaba dentro de `obtenerListaImagenesVuelo`, que la recalculaba en cada
        llamada. Sale aparte porque `gen_folder_struct` necesita las ventanas de
        TODOS los vuelos por adelantado para poder preguntar, imagen a imagen, de
        quién es (ver ahí el porqué del cambio de orden).

        Arguments:
        ---------
        - fecha / horaInicio / horaFinal - lo que dice el estadillo de ese vuelo.
        - margen_segundos - holgura que se resta al inicio y se suma al final.
        - desfase_horas / desfase_minutos - diferencia entre el reloj del estadillo
          y el de la cámara; se suma a las dos puntas.
        """
        fecha_hora_inicio_estadillo = datetime.datetime.strptime(fecha+'_'+horaInicio,'%Y:%m:%d_%H:%M:%S')
        fecha_hora_fin_estadillo = datetime.datetime.strptime(fecha+'_'+horaFinal,'%Y:%m:%d_%H:%M:%S')

        desfase = datetime.timedelta(hours=desfase_horas, minutes=desfase_minutos)
        inicio = fecha_hora_inicio_estadillo - datetime.timedelta(seconds=margen_segundos) + desfase
        fin = fecha_hora_fin_estadillo + datetime.timedelta(seconds=margen_segundos) + desfase
        return inicio, fin

    def _get_timestamp_cached(self, ruta_imagen: str):
        """
        Devuelve el timestamp EXIF de una imagen usando la caché de la corrida.

        El EXIF de una imagen no cambia mientras dure el proceso, pero gen_folder_struct
        recorre la lista completa de imágenes UNA VEZ POR VUELO del estadillo: sin caché,
        una planta de V vuelos y N imágenes hacía del orden de V*N lecturas de EXIF, cada
        una un round-trip a GCS. Con la caché es N.

        Arguments:
        ---------
        - ruta_imagen - ruta completa de la imagen de la que obtener el timestamp.
        """
        if ruta_imagen in self._timestamps_cache:
            return self._timestamps_cache[ruta_imagen]
        timestamp = self.exif_management_obj.get_timestamp_from_image(ruta_imagen)
        with self._stats_lock:
            self._timestamps_cache[ruta_imagen] = timestamp
        return timestamp

    def precargar_timestamps(self, carpetas: list[str], progress_callback=None, filtro=None) -> None:
        """
        Rellena la caché de timestamps EXIF de todas las imágenes de `carpetas` en paralelo.

        Es una optimización pura: sin ella el resultado es idéntico, solo que cada lectura
        de EXIF se hace en serie y bajo demanda. Al hacerlas por lotes con un pool de hilos
        se solapa la latencia de GCS, que es lo que domina el tiempo de esta etapa.

        Arguments:
        ---------
        - carpetas - lista de carpetas cuyas imágenes se van a precargar.
        - progress_callback - Callback opcional para informar al log.
        - filtro - callable nombre_de_fichero -> bool. A None se precargan todas.
        Lo usa la etapa `struct` repartida para leer solo el EXIF de las imágenes
        que le tocan a esta tarea: precargar el de TODAS en las N tareas multiplica
        por N las lecturas contra GCS, que son lo caro de esta fase.
        """
        rutas = []
        for carpeta in carpetas:
            if not carpeta or not os.path.isdir(carpeta):
                continue
            for archivo in os.listdir(carpeta):
                if archivo.endswith(('jpg', 'JPG')):
                    if filtro is not None and not filtro(archivo):
                        continue
                    ruta = os.path.join(carpeta, archivo)
                    if ruta not in self._timestamps_cache:
                        rutas.append(ruta)

        if not rutas:
            return

        self.organizer_logger.logger.info(f"Precargando el EXIF de {len(rutas)} imágenes con {self.max_io_workers} hilos.")
        if progress_callback is not None:
            progress_callback.emit(f"\nLeyendo la fecha de {len(rutas)} imágenes...\n")

        with ThreadPoolExecutor(max_workers=self.max_io_workers) as executor:
            list(executor.map(self._get_timestamp_cached, rutas))

    def moverListaImagenes(self, pathCarpetaOrigen: str, pathCarpetaDestino: str, listaImagenes: list[str], progress_callback, progress_bar) -> None:
        """
        Funcion para mover una lista de imagenes desde una carpeta origen a una carpeta destino.

        Arguments:
        ---------
        - pathCarpetaOrigen - Carpeta de origen en la que se encuentran las imágenes que hay que mover
        - pathCarpetaDestino - Carpeta de destino a la que se moverán las imágenes.
        - listaImagenes - Lista de imágenes que hay que mover de carpeta.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        self._mover_pares([(os.path.join(pathCarpetaOrigen, imagen),
                            os.path.join(pathCarpetaDestino, imagen))
                           for imagen in listaImagenes], progress_callback, progress_bar)

    def _mover_pares(self, movimientos: list[tuple[str, str]], progress_callback, progress_bar) -> None:
        """Mueve una lista de `(origen, destino)` con un único pool de hilos.

        Cada move es un round-trip a disco/GCS y apenas gasta CPU, así que en serie el
        proceso se pasa el tiempo esperando latencia. Con un pool de hilos se solapan.
        Los destinos son distintos entre sí (uno por imagen), de modo que los workers
        no compiten por la misma ruta.

        Recibe pares y no `(carpeta, lista)` para que quien reparte por vuelo pueda
        pasar TODOS los movimientos de la corrida en una sola llamada, en vez de
        arrancar un pool corto por cada carpeta de destino.
        """
        if not movimientos:
            return

        def _mover(par: tuple[str, str]) -> None:
            if self.stop:  # Se comprueba que no se quiere parar el proceso desde la ventana del log.
                return
            origen, destino = par
            # `descartar_origen_si_existe`: aqui el destino es `PBx/PBx_Vy/` y el
            # origen la raiz plana de TERMICA/RGB del MISMO destino. Con `obviar`
            # y una pasada previa la imagen ya esta colocada, asi que la copia de
            # la raiz sobra. Dejarla ademas la haria pasar por "fuera del
            # estadillo" en `checking_results_gen_struct_folder` y acabaria
            # aparcada en SIN_ORDENAR, que es exactamente lo contrario de lo que
            # ocurrio: la imagen SI encajo en un vuelo.
            movida = utils.safe_move(origen, destino, modo=self.modo_destino,
                                     descartar_origen_si_existe=True)
            with self._stats_lock:
                # La omitida cuenta como procesada: el cuadre de la fase compara
                # "imagenes de entrada" contra "imagenes resueltas", y una que ya
                # estaba en su sitio esta resuelta. Se lleva aparte para poder
                # decirlo en el resumen.
                self.current_image_number += 1
                if movida is None:
                    self.skipped_image_number += 1
                p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                # y la cantidad actual de imágenes procesadas.
            progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
            progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.

        if self.max_io_workers <= 1 or len(movimientos) == 1:
            for par in movimientos:
                _mover(par)
            return

        with ThreadPoolExecutor(max_workers=self.max_io_workers) as executor:
            list(executor.map(_mover, movimientos))

    def check_input_folder_and_iterate(self, input_folder: str, folders_to_check: list[str], max_error: int, lim_max_270: int, lim_min_270: int, lim_max_90: int, lim_min_90: int, rotation_mode_auto: bool, rotation_value_90: bool, progress_callback, progress_bar, only_pb: list[str] | None = None) -> bool:
        """
        Función que comprueba que los directorios TERMICA y RGB se encuentran en input folder. Si existen, recorre todas las carpetas y todas las imágenes 
        dentro de las carpetas y genera las miniaturas y las rota (en el caso TERMICA), o solo las rota (caso RGB).

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - folders_to_check - carpetas que se comprueba que existen.
        - max_error - máximo error permitido en el número de imágenes a rotar.
        - lim_max_270 - límite máximo de margen para las imágenes que hay que rotar 270 grados (-90).
        - lim_min_270 - límite mínimo de margen para las imágenes que hay que rotar 270 grados (-90).
        - lim_max_90 - límite máximo de margen para las imágenes que hay que rotar 90 grados.
        - lim_min_90 - límite mínimo de margen para las imágenes que hay que rotar 90 grados.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - only_pb - si se pasa, se rotan SOLO esos vuelos (rutas relativas `PBx/PBx_Vy`)
          en vez del árbol entero.
          Es lo que permite repartir la rotación entre varias tareas paralelas: el criterio
          de giro se decide POR carpeta `PBx_Vy` (con el yaw de sus imágenes y de ninguna
          otra), así que procesar un subconjunto de PB da el mismo resultado que
          procesarlos todos. A None (por defecto) el comportamiento es el de siempre.
        """
        # ÚLTIMA BARRERA del criterio de rotación, y la única por la que pasan las tres
        # rutas (webview, GUI Qt "Generar miniaturas" y la de Aerotools).
        #
        # Los umbrales llegan ya calculados como (90 - subs_to_angle, 90 + add_to_angle) y
        # (-90 - subs_to_angle, -90 + add_to_angle), y se comparan con `<` ESTRICTO. Con los
        # márgenes a 0 el intervalo queda lim_min == lim_max, o sea VACÍO: ningún yaw puede
        # caer dentro -- ni siquiera 90 clavado --, los contadores salen 0 y 0 y todo el
        # vuelo se va por la rama "no se deben rotar" (:1209). El usuario ve que no se gira
        # NADA y el log escribe "OK", sin una sola pista de por qué. Eso pasó en las cuatro
        # corridas del 2026-08-04. Aquí se reconstruyen los márgenes, se sanean y se
        # recalculan los límites, para que un 0 llegado por CUALQUIER camino no pueda dejar
        # el proceso sin rotar.
        if rotation_mode_auto and (lim_min_90 >= lim_max_90 or lim_min_270 >= lim_max_270):
            add_to_angle, subs_to_angle, max_error = utils.sane_rotation_criteria(
                lim_max_90 - 90, 90 - lim_min_90, max_error)
            lim_max_90, lim_min_90 = 90 + add_to_angle, 90 - subs_to_angle
            lim_max_270, lim_min_270 = -90 + add_to_angle, -90 - subs_to_angle
            aviso = ("\nAVISO: el margen de yaw venía a 0, con lo que ninguna imagen podría "
                     f"cumplir el criterio de giro. Se usa un margen de ±{add_to_angle}° y un "
                     f"{max_error}% mínimo de imágenes que deben girar igual.\n")
            self.organizer_logger.logger.warning(
                f"Margen de yaw vacío; se corrige a 90 in ({lim_min_90}, {lim_max_90}) y "
                f"-90 in ({lim_min_270}, {lim_max_270}), max_error={max_error}.")
            progress_callback.emit(aviso)

        list_dir = os.listdir(input_folder)
        self.root_folder = input_folder  # Almacenamos el input_folder, que puede ser cualquiera.
        self.csvs_root_folder = os.path.join(self.root_folder, "CSVs") # Almacenamos el path de CSVs, donde vive el criterio de giro.

        # Recorremos las carpetas del parámetro folders_to_check, para saber si es RGB o no (TERMICA).
        for folder in folders_to_check:
            if folder == "RGB":
                rgb_processing = True
            else:
                self.utils_obj.prepare_output_folder(input_folder, ["CSVs"])  # Generamos la carpeta CSVs si no está. Ya no se generan miniaturas.
                rgb_processing = False
            
            # Aquí radica el que podamos seguir trabajando en el caso de que se eligiera la carpeta con RGB y TERMICA como root. Si la carpeta de folders_to_check está en la
            # lista de directorios de la carpeta de entrada, entonces su carpeta de entrada la hacemos con os.path.join para hacer la correspondiente a RGB o TERMICA.
            # Si no lo está, entonces llevamos a cabo el proceso indicado en la carpeta de entrada, sea la que sea, sin diferenciar RGB o TERMICA. Es algo que tendrán
            # que tener en cuenta ellos.
            if folder in list_dir:
                # Con reparto, se arranca en cada vuelo propio en vez de en la raíz de
                # RGB/TERMICA. `gen_thumbnails_and_rotate` es recursiva y no mira dónde
                # empieza el árbol, y `root_folder`/`csvs_root_folder` (de donde sale el
                # criterio de giro) se han fijado arriba a la raíz de verdad, así que el
                # resultado es idéntico.
                if only_pb is None:
                    raices = [os.path.join(input_folder, folder)]
                else:
                    base = os.path.join(input_folder, folder)
                    raices = [sharding.ruta_de_relativo(base, rel) for rel in only_pb
                              if os.path.isdir(sharding.ruta_de_relativo(base, rel))]
                for raiz in raices:
                    if rotation_mode_auto:
                        self.gen_thumbnails_and_rotate(raiz, rgb_processing, max_error, lim_max_270, lim_min_270, lim_max_90, lim_min_90, progress_callback, progress_bar)
                    else:
                        self.gen_thumbnails_and_rotate_manual(raiz, rgb_processing, rotation_value_90, progress_callback, progress_bar)

            else:
                if rotation_mode_auto:
                    self.gen_thumbnails_and_rotate(input_folder, rgb_processing, max_error, lim_max_270, lim_min_270, lim_max_90, lim_min_90, progress_callback, progress_bar)
                else:
                    self.gen_thumbnails_and_rotate_manual(input_folder, rgb_processing, rotation_value_90, progress_callback, progress_bar)

        return True

    def copy_flight_csvs_to_csvs_folder(self, input_folder: str, file: str) -> None:
        """
        Copia a `CSVs/` el meta.csv del vuelo térmico (`file`) y el location.csv
        de su carpeta RGB equivalente.

        PLANO, sin subcarpeta por vuelo: los nombres ya llevan el vuelo delante
        (`PB24_V1_meta.csv`), así que no colisionan, y en `CSVs/` solo debe haber CSVs.
        La subcarpeta era herencia de cuando esto vivía en `MINIATURAS/<vuelo>/`; al
        moverlo aquí se arrastró la jerarquía y dejaba un duplicado byte a byte del
        meta/location que el paso de meta/location ya escribe en la raíz de `CSVs/`.

        SOBRESCRIBE a propósito. Se usaba `safe_copy2`, que en vez de sobrescribir versiona
        (`unique_dest` -> `_1`, `_2`...): re-procesar el mismo destino dejaba
        `<vuelo>_location.csv`, `<vuelo>_location_1.csv`, `<vuelo>_location_2.csv`, todos
        idénticos y sin forma de saber cuál vale. El meta/location regenerado de un vuelo es
        el mismo dato, no una versión nueva. Ese versionado sí tiene sentido para imágenes
        distintas que colisionan de nombre, no aquí.
        """
        os.makedirs(self.csvs_root_folder, exist_ok=True)
        rgb_dir = input_folder.replace("TERMICA", "RGB")
        rgb_file = file.replace("meta", "location")
        for origen, destino in ((os.path.join(input_folder, file), os.path.join(self.csvs_root_folder, os.path.basename(file))),
                                (os.path.join(rgb_dir, rgb_file), os.path.join(self.csvs_root_folder, os.path.basename(rgb_file)))):
            try:
                shutil.copy2(origen, destino)
            except FileNotFoundError:
                pass

    def copy_flight_csvs(self, input_folder: str, progress_callback) -> None:
        """
        Copia el meta/location.csv del vuelo térmico a `CSVs/`, avisando si
        alguno de los dos no está donde se espera.

        Antes se copiaban DOS veces (a MINIATURAS y a CSVs). Al quitar las miniaturas queda
        solo la de CSVs; el aviso de "no se ha encontrado meta/location.csv" tiene que seguir
        saliendo igual, así que se comprueba la existencia en vez de esperar la excepción de
        la copia (`copy_flight_csvs_to_csvs_folder` ignora en silencio los que faltan).

        Solo se mira el meta: el par se deriva de él (`meta` -> `location`). Antes se iteraba
        sobre TODOS los `.csv` de la carpeta y se copiaba el par entero una vez por cada uno,
        así que un vuelo con más de un CSV repetía la copia del location tantas veces como
        CSVs hubiera.
        """
        for file in os.listdir(input_folder):
            if not file.endswith(".csv") or "meta" not in file:
                continue
            # Sustituimos en el input folder TERMICA por RGB, pues tendría que ser el mismo directorio, pero cambiando sólo esa parte.
            # Para el archivo es lo mismo, pero meta por location.
            rgb_dir = input_folder.replace("TERMICA", "RGB")
            rgb_file = file.replace("meta", "location")
            self._warn_if_flight_csv_missing(os.path.join(input_folder, file), "meta.csv", progress_callback)
            self._warn_if_flight_csv_missing(os.path.join(rgb_dir, rgb_file), "location.csv", progress_callback)
            self.copy_flight_csvs_to_csvs_folder(input_folder, file)

    def _warn_if_flight_csv_missing(self, path: str, nombre: str, progress_callback) -> None:
        """Registra el error si el meta/location del vuelo no existe en `path`."""
        if os.path.exists(path):
            return
        self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
        self.organizer_logger.logger.info(f"ERROR: No se ha encontrado el archivo {nombre}")
        self.organizer_logger.logger.info(f"Ruta esperada: {path}")
        self.organizer_logger.logger.info('------------------------------------------------------------------------------------------------------')
        progress_callback.emit(f"\nERROR: No se ha encontrado el archivo {nombre}\n")
        self.error_gen_struct_folder += 1
        self.errors_type_gen_struct_folder.append(f"No se ha encontrado el archivo {nombre}")

    def _rotate_original_if_rgb(self, image: str, input_folder: str, degrees, rgb_processing: bool, progress_callback) -> bool:
        """
        Gira el original in-place cuando es RGB. Con térmicas no hay nada que girar en este
        paso: el `*_T.JPG` crudo no se puede re-encodar (perdería el payload radiométrico) y
        las miniaturas ya no se generan; el `*_T.JPG` se gira al final, después de la
        conversión a TIFF y con el mismo criterio (`read_auto_rotate_degree`).

        Devuelve True si el vuelo tiene un `_CROP` asociado a esta imagen, para que la barra
        de progreso siga contándolo igual en RGB y en térmica.
        """
        if rgb_processing:
            return self.compress_image_obj.rotate_and_save(image, input_folder, degrees, _ROTATION_JPEG_QUALITY, progress_callback)

        file_splitted = os.path.splitext(image)
        return os.path.exists(os.path.join(input_folder, file_splitted[0] + "_CROP" + file_splitted[1]))

    def _rotate_images_batch(self, images: list[str], input_folder: str, degrees, rgb_processing: bool, progress_callback, progress_bar) -> None:
        """
        Gira el lote entero de imágenes de un vuelo; en paralelo cuando son RGB.

        La rotación era la única fase del pipeline que seguía yendo en bucle secuencial:
        ~0,74 s por par de ficheros (original + `_CROP`) × las imágenes del vuelo. Se
        reparte con `utils.run_batch`, el mismo ProcessPool que ya usan la compresión, el
        recorte y la separación. Cada worker escribe in-place sobre ficheros distintos, así
        que no hay carrera entre ellos.

        Con térmicas no se lanza pool: en este paso no hay nada que girar (el `*_T.JPG`
        crudo no se puede re-encodar y se gira al final, tras la conversión a TIFF), solo
        hay que contar los `_CROP` para que la barra avance igual que en RGB.

        Arguments:
        ---------
        - images - nombres de las imágenes del vuelo, sin los `_CROP`.
        - input_folder - carpeta del vuelo. Se gira in-place.
        - degrees - la constante de PIL con la que rotar (`Image.ROTATE_90` / `ROTATE_270`).
        - rgb_processing - True si son RGB, False si son térmicas.
        - progress_callback - signal de texto a la ventana de log.
        - progress_bar - signal de porcentaje a la barra de progreso.
        """
        if not rgb_processing:
            for image in images:
                self.send_progress_to_bar(progress_bar, progress_callback)
                if self._rotate_original_if_rgb(image, input_folder, degrees, rgb_processing, progress_callback):
                    self.send_progress_to_bar(progress_bar, progress_callback)  # Si hay imagen recortada, contamos una más.
            return

        if self.stop:  # Se comprueba que no se quiere parar el proceso antes de lanzar el batch completo.
            return

        def _worker_args_fn(image):
            return (image, input_folder, degrees, _ROTATION_JPEG_QUALITY)

        def _on_progress(_pct):
            self.send_progress_to_bar(progress_bar, progress_callback)

        # Se resuelve aquí, y no dentro de run_batch, para dejarlo escrito en el log: si un
        # día una máquina va lenta, lo primero que hay que saber es con cuántos procesos giró.
        workers = utils.workers_para_lote()
        self.organizer_logger.logger.info(f"Rotando {len(images)} imágenes con {workers} procesos en paralelo.")
        result = utils.run_batch(images, rotate_one_image, _worker_args_fn, on_progress=_on_progress, max_workers=workers)

        for payload in result["results"]:
            for message in payload["messages"]:  # Lo que el worker quiso escribir en la ventana de log.
                progress_callback.emit(message)
            for image_path in payload["errors"]:
                self.organizer_logger.logger.warning(f"ERROR: La imagen {image_path} no se ha podido rotar.")
                self.compress_image_obj.error_compress += 1
                self.compress_image_obj.images_error_compress.append(image_path)
            if payload["cropped"]:
                self.send_progress_to_bar(progress_bar, progress_callback)  # Si hay imagen recortada, contamos una más.

        for image, error_str in result["errors"]:  # El worker murió: rotate_and_save ni llegó a capturarlo.
            image_path = os.path.join(input_folder, image)
            self.organizer_logger.logger.warning(f"ERROR: No se ha podido rotar la imagen {image_path}: {error_str}")
            progress_callback.emit("\nERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(image_path) + "\n")
            self.compress_image_obj.error_compress += 1
            self.compress_image_obj.images_error_compress.append(image_path)

    def write_videofiles_csv(self, input_folder: str, df_videofiles) -> None:
        """
        Escribe el criterio de giro del vuelo (`<PBX_VXX>_Videofiles.csv`, columna `Degree`)
        en `CSVs/_criterio/`.

        Vivía dentro de MINIATURAS hasta que se eliminaron las miniaturas, luego en
        `CSVs/<vuelo>/` y luego plano en `CSVs/`. NO es un subproducto descartable: es
        lo único que le dice a la conversión a TIFF y al giro del JPG térmico cuánto
        tienen que girar (`read_auto_rotate_degree`). Si falta, ambas dejan de rotar en
        silencio. Pero tampoco es un entregable, y plano en `CSVs/` se mezclaba con los
        CSVs que el usuario sí consume (meta, location), así que va apartado en una
        subcarpeta interna.
        """
        criterio_folder = os.path.join(self.csvs_root_folder, utils.CRITERIO_DIRNAME)
        os.makedirs(criterio_folder, exist_ok=True)
        self.utils_obj.hide_folder_on_windows(criterio_folder)
        df_videofiles.to_csv(os.path.join(criterio_folder, os.path.basename(input_folder) + "_Videofiles.csv"),
                             sep=",", header=True, index=False)

    def gen_thumbnails_and_rotate(self, input_folder: str, rgb_processing: bool, max_error: int, lim_max_270: int, lim_min_270: int, lim_max_90: int, lim_min_90: int, progress_callback, progress_bar) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta se generan las miniaturas y se rotan si
        es necesario.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - rgb_processing - indica si estamos procesando una imagen rgb o térmica. True es rgb, False es térmica.
        - max_error - máximo error permitido en el número de imágenes a rotar.
        - lim_max_270 - límite máximo de margen para las imágenes que hay que rotar 270 grados (-90).
        - lim_min_270 - límite mínimo de margen para las imágenes que hay que rotar 270 grados (-90).
        - lim_max_90 - límite máximo de margen para las imágenes que hay que rotar 90 grados.
        - lim_min_90 - límite mínimo de margen para las imágenes que hay que rotar 90 grados.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        if os.path.basename(input_folder).startswith("PB") and "_V" in os.path.basename(input_folder):  # Necesitamos llevar a cabo el procesado solo dentro de las carpetas PBX_VXX
            nombresColumnas = ['New Name','Original Name','Degree']
            df_videofiles = pd.DataFrame(columns=nombresColumnas)  # Creamos un dataframe para crear posteriormente un csv

            images = self.utils_obj.get_images_from_dir(input_folder, ["_CROP"])
            # se enviará 0 imágenes.
            rotate_90 = 0
            rotate_270 = 0
            if len(images) != 0:
                progress_callback.emit("\nProcesando {0} imágenes en el directorio {1}".format(len(images), input_folder) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
                self.organizer_logger.logger.info(f"Procesando el directorio: {input_folder}.")
                for index, image in enumerate(images):
                    gimbal_yaw = self.exif_management_obj.get_gimbal_yaw_pitch(os.path.join(input_folder,image))[0]
                    if lim_max_90 > float(gimbal_yaw) > lim_min_90:
                        rotate_90 = rotate_90 + 1  # Para contar el número de imágenes que rotamos 90º
                    elif lim_min_270 < float(gimbal_yaw) < lim_max_270:
                        rotate_270 = rotate_270 + 1  # Para contar el número de imágenes que rotamos 270º
                        
                images_no_rotated = len(images) - (rotate_90 + rotate_270)
                self.organizer_logger.logger.info("Número de imágenes : " + str(len(images)))
                self.organizer_logger.logger.info("Número de imágenes rotadas 270: " + str(rotate_270))
                self.organizer_logger.logger.info("Número de imágenes rotadas 90: " + str(rotate_90))
                self.organizer_logger.logger.info("Número de imágenes sin rotar: " + str(images_no_rotated))
                
                # Enviamos info a la ventana del log acerca de las imágenes que son necesarias (o no) rotar.
                progress_callback.emit("\nNúmero de imágenes rotadas 270: {0}\nNúmero de imágenes rotadas 90: {1}\nNúmero de imágenes sin rotar: {2}\n".format(str(rotate_270), str(rotate_90), str(images_no_rotated)))

                # logging.info("Image quantity : " + str(len(images)))
                # logging.info("Image quantity rotated 270: " + str(rotate_270))
                # logging.info("Image quantity rotated 90: " + str(rotate_90))
                # logging.info("Image quantity without rotating: " + str(images_no_rotated))

                images_to_rotate = True
                # Un panorama (PBPano_V*) es un barrido: cada foto apunta a un yaw distinto, así que por
                # definición nunca habrá un giro dominante y el chequeo del max_error siempre fallaría.
                # Se trata como carpeta normal sin retocar: no se rota nada (Degree 0) y no es error.
                es_panorama = utils.es_carpeta_panoramica(input_folder)
                if es_panorama:
                    self.organizer_logger.logger.info("Carpeta panorámica detectada: se procesa sin rotar ninguna imagen.")
                    progress_callback.emit("\nCarpeta panorámica: se procesa tal cual, sin rotar.\n")
                # Comprobamos que no ha habido un porcentaje mayor del 95% entre las diferentes cantidades. Lo que hacemos es comprobar que una de las cantidades de imágenes ya sobrepasa el 95%,
                # con lo que el resto de cantidades estaría en el 5%.
                if (not es_panorama and rotate_270 != 0 and (rotate_270/len(images)) > (max_error/100)):
                    self.organizer_logger.logger.info(f"OK: Más de un {max_error}% de las imágenes del vuelo tienen que rotar 270")
                    # logging.info(f"OK: More than {max_error}% of flight images need to be rotated 270")
                    progress_callback.emit(f"\nOK: Más de un {max_error}% de las imágenes del vuelo tienen que rotar 270\n")
                    # Cuando se rota con PIL se rota en sentido antihorario. De ahí que pongamos ROTATE_90, ya que rotará los -90 si fuera en sentido horario
                    self._rotate_images_batch(images, input_folder, Image.ROTATE_90, rgb_processing, progress_callback, progress_bar)
                    for index, image in enumerate(images):
                        df_videofiles.loc[len(df_videofiles)] = {"New Name": os.path.basename(input_folder) + "_" + str(index + 1).zfill(4) + ".JPG", "Original Name": image, "Degree": 270}

                    self.organizer_logger.logger.info("Imágenes rotadas 270º")
                    # logging.info("Images rotated 270º")
                    self.giros_por_vuelo[os.path.basename(input_folder)] = {
                        "vuelo": os.path.basename(input_folder), "grados": 270,
                        "imagenes": len(images), "modo": "auto", "detalle": None,
                    }
                elif (not es_panorama and rotate_90 != 0 and (rotate_90/len(images)) > (max_error/100)):
                    self.organizer_logger.logger.info(f"OK: Más de un {max_error}% de las imágenes del vuelo tienen que rotar 90")
                    # logging.info(f"OK: More than {max_error}% of flight images need to be rotated 90")
                    progress_callback.emit(f"\nOK: Más de un {max_error}% de las imágenes del vuelo tienen que rotar 90\n")
                    # Cuando se rota con PIL se rota en sentido antihorario. De ahí que pongamos ROTATE_270, ya que rotará los 90 si fuera en sentido horario
                    self._rotate_images_batch(images, input_folder, Image.ROTATE_270, rgb_processing, progress_callback, progress_bar)
                    for index, image in enumerate(images):
                        df_videofiles.loc[len(df_videofiles)] = {"New Name": os.path.basename(input_folder) + "_" + str(index + 1).zfill(4) + ".JPG", "Original Name": image, "Degree": 90}
                    self.organizer_logger.logger.info("Imágenes rotadas 90")
                    # logging.info("Images rotated 90")
                    self.giros_por_vuelo[os.path.basename(input_folder)] = {
                        "vuelo": os.path.basename(input_folder), "grados": 90,
                        "imagenes": len(images), "modo": "auto", "detalle": None,
                    }
                elif (es_panorama or (images_no_rotated != 0 and (images_no_rotated/len(images)) > (max_error/100))):
                    if not es_panorama:
                        self.organizer_logger.logger.info(f"OK: Más de un {max_error}% de las imágenes del vuelo NO se deben rotar")
                        # logging.info(f"OK: More than {max_error}% of flight images must not be rotated")
                        progress_callback.emit(f"\nOK: Más de un {max_error}% de las imágenes del vuelo NO se deben rotar\n")
                    self.giros_por_vuelo[os.path.basename(input_folder)] = {
                        "vuelo": os.path.basename(input_folder), "grados": 0,
                        "imagenes": len(images), "modo": "panoramica" if es_panorama else "auto",
                        "detalle": "carpeta panorámica, no se rota" if es_panorama else "ninguna orientación supera el umbral",
                    }
                    for index, image in enumerate(images):
                        self.send_progress_to_bar(progress_bar, progress_callback)
                        file_splitted = os.path.splitext(image)
                        crop_image_name = file_splitted[0] + "_CROP" + file_splitted[1]
                        if os.path.exists(os.path.join(input_folder, crop_image_name)):
                            self.send_progress_to_bar(progress_bar, progress_callback) # Si hay imágenes recortadas, contamos una más. Lo hago diferente aquí porque no estoy llamando a rotate_and_save
                        
                        # gimbal_yaw = self.exif_management_obj.get_gimbal_yaw_pitch(os.path.join(input_folder,image))[0]
                        # No se rota nada (ni RGB ni térmica) y las miniaturas ya no se generan: aquí solo queda dejar constancia del Degree 0.
                        df_videofiles.loc[len(df_videofiles)] = {"New Name": os.path.basename(input_folder) + "_" + str(index + 1).zfill(4) + ".JPG", "Original Name": image, "Degree": 0}
                    self.organizer_logger.logger.info("Imágenes no rotadas")
                else:
                    images_to_rotate = False
                    self.organizer_logger.logger.info("ERROR: Hay demasiadas imágenes que no rotan igual")
                    self.organizer_logger.logger.info("ERROR: No se han rotado imágenes")
                    progress_callback.emit("\nERROR: Hay demasiadas imágenes que no rotan igual - No se han rotado imágenes\n")
                    self.giros_por_vuelo[os.path.basename(input_folder)] = {
                        "vuelo": os.path.basename(input_folder), "grados": 0,
                        "imagenes": len(images), "modo": "error",
                        "detalle": "demasiadas imágenes que no rotan igual",
                    }
                   

                if images_to_rotate and not rgb_processing:  # Comprobamos que procesamos térmicas y que además hemos rotado imágenes.
                    self.copy_flight_csvs(input_folder, progress_callback)

                # Entiendo que sólo generamos el archivo _Videofiles.csv en el caso de las térmicas.
                if not rgb_processing:
                    self.write_videofiles_csv(input_folder, df_videofiles)
                return  # En este caso cortamos la recursividad para este directorio, porque si no, seguiría buscando en el directorio creado,
                    # por lo que volvería a crear otro dentro y así sucesivamente.

        for dir in next(os.walk(input_folder))[1]:
            if not self.stop and dir not in ("MINIATURAS", "CSVs"):  # Comprobamos que no queremos parar el proceso. Si se para en medio de un procesado, acaba de procesarse ese directorio.
                # MINIATURAS ya no se genera, pero se sigue excluyendo: una carpeta procesada por
                # una versión anterior la tiene, y sus subcarpetas `PBX_VXX_miniaturas` casan con
                # el patrón `PB*_V*` y se reprocesarían.
                self.gen_thumbnails_and_rotate(os.path.join(input_folder,dir), rgb_processing, max_error, lim_max_270, lim_min_270, lim_max_90, lim_min_90, progress_callback, progress_bar)  # Volvemos a llamar recursivamente a la función para los demás directorios.

    def gen_thumbnails_and_rotate_manual(self, input_folder: str, rgb_processing: bool, rotation_value_90: bool, progress_callback, progress_bar):
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta se generan las miniaturas y se rotan si
        es necesario. El valor de la rotación en este caso no se calcula, sino que viene dado por el valor de rotation_value, el cual viene dado a su vez por el HMI, pudiendo ser 90 o -90 grados. Es decir, 90 o 270 grados.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - rgb_processing - indica si estamos procesando una imagen rgb o térmica. True es rgb, False es térmica.
        - rotation_value - valor de rotación, 90 o 270.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        if os.path.basename(input_folder).startswith("PB") and "_V" in os.path.basename(input_folder):  # Necesitamos llevar a cabo el procesado solo dentro de las carpetas PBX_VXX
            nombresColumnas = ['New Name','Original Name','Degree']
            df_videofiles = pd.DataFrame(columns=nombresColumnas)  # Creamos un dataframe para crear posteriormente un csv

            images = self.utils_obj.get_images_from_dir(input_folder, ["_CROP"])
            if len(images) != 0:
                progress_callback.emit("\nProcesando {0} imágenes en el directorio {1}".format(len(images), input_folder) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
                self.organizer_logger.logger.info("\nProcesando {0} imágenes en el directorio {1}".format(len(images), input_folder) + "\n")
                if rotation_value_90:
                    progress_callback.emit("\nRotan todas las imágenes 90 grados\n")
                else:
                    progress_callback.emit("\nRotan todas las imágenes -90 grados\n")

                # PIL rota antihorario: un giro manual de 90 se corrige con ROTATE_270 y al revés.
                degrees = Image.ROTATE_270 if rotation_value_90 else Image.ROTATE_90
                self._rotate_images_batch(images, input_folder, degrees, rgb_processing, progress_callback, progress_bar)

                for index, image in enumerate(images):
                    # El Degree tiene que reflejar lo que se acaba de rotar: es lo que lee
                    # después el criterio de giro del TIFF. Escribir 270 fijo hacía que un
                    # vuelo rotado a mano 90 girase el TIFF al revés.
                    df_videofiles.loc[len(df_videofiles)] = {"New Name": os.path.basename(input_folder) + "_" + str(index + 1).zfill(4) + ".JPG", "Original Name": image, "Degree": 90 if rotation_value_90 else 270}

                self.giros_por_vuelo[os.path.basename(input_folder)] = {
                    "vuelo": os.path.basename(input_folder), "grados": 90 if rotation_value_90 else 270,
                    "imagenes": len(images), "modo": "manual", "detalle": None,
                }

                if not rgb_processing:  # Comprobamos que procesamos térmicas
                    self.copy_flight_csvs(input_folder, progress_callback)

                if not rgb_processing:
                    self.write_videofiles_csv(input_folder, df_videofiles)
                return  # En este caso cortamos la recursividad para este directorio, porque si no, seguiría buscando en el directorio creado,
                    # por lo que volvería a crear otro dentro y así sucesivamente.

        for dir in next(os.walk(input_folder))[1]:
            if not self.stop and dir not in ("MINIATURAS", "CSVs"):  # Comprobamos que no queremos parar el proceso. Si se para en medio de un procesado, acaba de procesarse ese directorio.
                self.gen_thumbnails_and_rotate_manual(os.path.join(input_folder,dir), rgb_processing, rotation_value_90, progress_callback, progress_bar)  # Volvemos a llamar recursivamente a la función para los demás directorios.

    def iterate_folders_and_rename(self, input_folder: str, progress_callback, progress_bar, desfase_horas: int = 0, desfase_minutos: int = 0) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta lleva a cabo la función rename_images.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - desfase_horas - horas de desfase horario a aplicar sobre la fecha/hora EXIF de la imagen.
        - desfase_minutos - minutos de desfase horario a aplicar sobre la fecha/hora EXIF de la imagen.
        """
        self.organizer_logger.logger.info("----------------------------------------------------------")
        self.organizer_logger.logger.info(f"Directorio de entrada: {input_folder}")
        self.rename_images(input_folder, progress_callback, progress_bar, desfase_horas, desfase_minutos)
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.iterate_folders_and_rename(os.path.join(input_folder,dir), progress_callback, progress_bar, desfase_horas, desfase_minutos)

    def rename_images(self, input_folder: str, progress_callback, progress_bar, desfase_horas: int = 0, desfase_minutos: int = 0) -> None:
        """
        Función que renombra las imágenes que se encuentran en la carpeta de entrada. No las cambia de carpeta.
        El nuevo nombre se basa en la fecha y hora obtenidas de los datos exif (con el desfase horario aplicado, si se indica), seguido del nombre original del archivo.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - desfase_horas - horas de desfase horario a aplicar sobre la fecha/hora EXIF de la imagen.
        - desfase_minutos - minutos de desfase horario a aplicar sobre la fecha/hora EXIF de la imagen.
        """
        images = self.utils_obj.get_images_from_dir(input_folder)
        if len(images) != 0:
                progress_callback.emit("\nProcesando {0} imágenes en el directorio {1}".format(len(images), input_folder) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
             
        for image in images:
            if not self.stop:  # Se comprueba que no se quiere parar el proceso desde la ventana del log.
                self.current_image_number += 1
                p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                # y la cantidad actual de imágenes procesadas.
                progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
                progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.                
                self.organizer_logger.logger.info("Carpeta de entrada con imágenes: " + input_folder)
                # Obtenemos el nuevo nombre en el caso de querer renombrar el archivo, aplicando el desfase horario indicado.
                desfase = datetime.timedelta(hours=desfase_horas, minutes=desfase_minutos)
                timestamp_image = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, image))
                if timestamp_image is not None:
                    new_date_time_image = timestamp_image + desfase
                    new_name = str(new_date_time_image).replace(" ", "_").replace("-", "").replace(":", "")
                else:
                    new_name = None
                if new_name is not None:
                    new_name = new_name + "_" + image
                    new_name = os.path.basename(utils.safe_move(os.path.join(input_folder, image), os.path.join(input_folder, new_name)))
                    self.organizer_logger.logger.info("Nombre viejo: " + image + " " + "Nombre nuevo: " + new_name)
    
    def send_progress_to_bar(self, progress_bar, progress_callback):
        self.current_image_number += 1
        p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
        # y la cantidad actual de imágenes procesadas.
        progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
        progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.
         
    def iterate_folder_for_rotating_tiff(self, input_folder: str, rotate_angle: int, progress_callback, progress_bar, progress_summarize):
        self.rotate_tiff_images(input_folder, rotate_angle, progress_callback, progress_bar, progress_summarize)
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.iterate_folder_for_rotating_tiff(os.path.join(input_folder,dir), rotate_angle, progress_callback, progress_bar, progress_summarize)

    def rotate_tiff_images(self, input_folder: str, rotate_angle: int, progress_callback, progress_bar, progress_summarize):
        images = self.utils_obj.get_tiff_images_from_dir(input_folder)
       
        if(len(images) > 0):
            progress_callback.emit("\nAnalizando directorio: " + input_folder + "\n")
            progress_callback.emit("Procesando y girando {0} imágenes TIFF".format(len(images)) + "\n") # Se envía información al iniciar el procesado de un directorio. Si no hay imágenes

            self.organizer_logger.logger.info(f"Analizando directorio: {input_folder}")
            self.organizer_logger.logger.info(f"Procesando y recortando {len(images)} imágenes")

        for image in images:
            if not self.stop:
                self.current_image_number += 1
                p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                # y la cantidad actual de imágenes procesadas.
                progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.
                progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
                
                if rotate_angle == 90:
                    self.rotate_tiff_image(input_folder, image, Image.ROTATE_270, progress_callback, progress_bar, progress_summarize)
                elif rotate_angle == -90:
                    self.rotate_tiff_image(input_folder, image, Image.ROTATE_90, progress_callback, progress_bar, progress_summarize)
                elif rotate_angle == 180:
                    self.rotate_tiff_image(input_folder, image, Image.ROTATE_180, progress_callback, progress_bar, progress_summarize)

    def rotate_tiff_image(self, input_folder: str, image_name: str, degrees: int, progress_callback, progress_bar, progress_summarize):
        image_full_path = os.path.join(input_folder,image_name)
        try:
            image_open = Image.open(image_full_path)
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error(f"ERROR: No se encuentra la imagen {image_full_path} para que PIL la pueda abrir.")
            progress_callback.emit(f"ERROR: No se encuentra la imagen {image_full_path} para que PIL la pueda abrir.")
            self.organizer_logger.logger.exception(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_gen_struct_folder += 1
            self.errors_type_gen_struct_folder.append(image_full_path)
            return
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error("ERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(image_full_path))
            progress_callback.emit("ERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(image_full_path))
            self.organizer_logger.logger.exception(e.__str__)
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_gen_struct_folder += 1
            self.errors_type_gen_struct_folder.append(image_full_path)
            return
        image_open = image_open.transpose(degrees)  # Se gira la imagen mediante transponse para mantener las resoluciones correctamente.
        image_open.save(os.path.join(input_folder, image_name), quality=96, subsampling = 0)  # quality/subsampling intactos: mandan sobre la imagen. optimize fuera, que solo cuesta tiempo (ver process_one_image).
        image_open.close()

def apply_thermal_colormap(array: np.ndarray, temp_min: float, temp_max: float, colormap_name: str = "inferno") -> np.ndarray:
    """
    Mapea un array de temperaturas a una paleta de color homogénea (misma temperatura = mismo color en toda la tirada),
    usando SIEMPRE el rango [temp_min, temp_max] fijado por el usuario (no el min/max de esta imagen concreta).
    Los valores fuera de rango saturan al color extremo correspondiente. No modifica el array de entrada.
    Devuelve un array uint8 de forma (H, W, 3), pensado como salida de visualización adicional (NO sustituye al TIFF radiométrico).
    """
    clipped = np.clip(array, temp_min, temp_max)
    if temp_max > temp_min:
        normalized = (clipped - temp_min) / (temp_max - temp_min)
        idx = np.minimum((normalized * _LUT_SIZE).astype(np.uint16), _LUT_SIZE - 1)
    else:
        idx = np.zeros(clipped.shape, dtype=np.uint16)
    return _get_thermal_lut(colormap_name)[idx]


class SplitImages:
    
    def __init__(self, organizer_logger: "utils.OrganizerLogger") -> None:
        self.compress_image_obj = CompressImage(organizer_logger)
        self.utils_obj = utils.Utils(organizer_logger)
        self.exif_management_obj = em.GeneralInformationFromImage(organizer_logger)
        self.stop = False
        self.current_image_number = 0
        self.total_images_number = 0
        self.error_splitting_images = 0
        self.images_error_splitting_images = []
        self.organizer_logger = organizer_logger
        # Nº de conversiones DJI->TIFF en paralelo. dji_irp.exe es un proceso
        # externo (libera el GIL), así que threads valen. El tope era un `min(8, ...)`
        # fijo que dejaba parada media máquina en equipos de más de 8 núcleos; ahora lo
        # dimensiona `utils.workers_para_lote`, el mismo criterio que el resto de fases
        # (núcleos menos los reservados, y capado por la RAM libre). Cada hilo tiene su
        # dji_irp.exe detrás, así que la RAM por worker se cuenta igual que en un pool
        # de procesos.
        self.max_dji_workers = utils.workers_para_lote()
        # Protege las mutaciones de contadores de error compartidos entre workers.
        self._stats_lock = threading.Lock()
        # Criterio de giro AUTO ya resuelto, por carpeta de vuelo. Es constante para
        # todo el directorio (sale del CSV que escribió la fase de rotación, anterior),
        # pero read_auto_rotate_degree se llamaba una vez POR IMAGEN: hasta 4
        # os.path.exists + un pd.read_csv completo por cada térmica del vuelo.
        self._criterio_giro_cache = {}
        self._criterio_giro_lock = threading.Lock()

    def set_stop(self, stop: bool):
        """
        Función que para el proceso modificando el estado de la variable self.stop

        Arguments:
        ---------
        - stop - variable que indica si se puede llevar a cabo o no el procesado. A True se para o no arranca, y a False se lleva a cabo.
        """
        self.stop = stop

    def reset_variables(self):
        """
        Resetea las variables necesarias para mostrar la información correctamente en la ventana de log.

        """
        self.current_image_number = 0
        self.total_images_number = 0
        self.compress_image_obj.error_compress = 0
        self.exif_management_obj.error_exif_data = 0
        self.error_splitting_images = 0
        self.images_error_splitting_images.clear()
        self.compress_image_obj.images_error_compress.clear()
        self.exif_management_obj.images_error_exif_data.clear()
        # El criterio de giro se relee en la siguiente corrida: entre una y otra el
        # usuario puede haber cambiado el criterio de rotación de esa misma carpeta.
        with self._criterio_giro_lock:
            self._criterio_giro_cache.clear()
    
    def get_summarize(self) -> dict:
        """Función que resume diferentes datos al finalizar el proceso. Devuelve un diccionario en el que cada clave es una información del proceso, junto
        con su correspondiente valor, de modo que se pueda mostrar en la ventana del log al finalizar el proceso."""
        summarize_dict= { "Número total de imágenes": self.current_image_number}
        error = False
        if self.total_images_number != self.current_image_number:
            error = True
            self.organizer_logger.logger.info(f"Número total de imágenes en gen_struct_folder: {self.total_images_number}")
            self.organizer_logger.logger.info(f"Número final de imágenes en gen_struct_folder: {self.current_image_number}")
            summarize_dict["Error imágenes"] = f"No hay correspondencia entre número inicial {self.total_images_number} y final de imágenes {self.current_image_number}."
            
        if self.compress_image_obj.error_compress > 0:
            error = True
            summarize_dict["Error en compresión"] = "Ha habido {0} errores en la compresión.".format(self.compress_image_obj.error_compress)
            summarize_dict["Imágenes con error"] = self.compress_image_obj.images_error_compress

        if self.exif_management_obj.error_exif_data > 0:
            error = True
            summarize_dict["Error en los metadatos"] = "Ha habido {0} errores en los metadatos.".format(self.exif_management_obj.error_exif_data)
            summarize_dict["Imágenes con error"] = self.exif_management_obj.images_error_exif_data
        
        if self.error_splitting_images > 0:
            error = True
            summarize_dict["Error en split_images"] = "Ha habido {0} errores.".format(self.error_splitting_images)
            summarize_dict["Imágenes con error"] = self.images_error_splitting_images

        if not error:
            summarize_dict["Sin Errores"] = "Sin errores durante el proceso"
        else:
            summarize_dict["ERROR"] = "HAN EXISTIDO ERRORES"
        return summarize_dict
    
    def checking_convert_to_tif(self, input_folder: str, progress_callback, progress_summarize, only_pb: list[str] | None = None) -> dict:
        """
        Detecta las carpetas dentro de input_folder que empiezan por 'PB' y, para cada
        subcarpeta que contengan, comprueba que el número de imágenes con extensión .tiff
        coincide con el número de imágenes con extensión .JPG o .JPEG.

        Arguments:
        ---------
        - input_folder - Carpeta raíz desde la que iniciar la búsqueda.

        Returns:
        --------
        Diccionario con la ruta de cada subcarpeta analizada como clave y un dict con:
            - "tiff_count": número de imágenes .tiff.
            - "jpg_count": número de imágenes .JPG/.JPEG.
            - "match": True si ambos valores son iguales, False en caso contrario.
        """
        results = {}
        errors = []

        if not os.path.exists(input_folder):
            self.organizer_logger.logger.warning(f"La carpeta de entrada no existe: {input_folder}")
            return results

        try:
            pb_folders = sorted([
                d for d in os.listdir(input_folder)
                if d.startswith("PB") and os.path.isdir(os.path.join(input_folder, d))
            ])
        except Exception as e:
            self.organizer_logger.logger.error(f"Error al listar '{input_folder}': {e}")
            return results

        # Con el trabajo repartido, esta tarea solo verifica SUS vuelos: las otras
        # están convirtiendo los suyos ahora mismo y verlos a medias daría un
        # "jpg y tiff no coinciden" que no es un fallo, sino una foto movida.
        # `only_pb` trae rutas relativas `PBx/PBx_Vy` (o `PBx` a secas), por eso el
        # filtro se hace en dos pasos: el PB aquí y el vuelo dentro del bucle.
        seleccion = None if only_pb is None else sharding.indice_seleccion(only_pb)
        if seleccion is not None:
            pb_folders = [d for d in pb_folders if d in seleccion]

        if not pb_folders:
            self.organizer_logger.logger.info(f"No se encontraron carpetas PB en: {input_folder}")
            return results

        for pb_folder in pb_folders:
            pb_folder_path = os.path.join(input_folder, pb_folder)
            try:
                subfolders = sorted([
                    d for d in os.listdir(pb_folder_path)
                    if os.path.isdir(os.path.join(pb_folder_path, d))
                ])
            except Exception as e:
                self.organizer_logger.logger.error(f"Error al listar '{pb_folder_path}': {e}")
                continue

            for subfolder in subfolders:
                if seleccion is not None and not sharding.seleccion_incluye(seleccion, pb_folder, subfolder):
                    continue
                subfolder_path = os.path.join(pb_folder_path, subfolder)
                try:
                    all_files = os.listdir(subfolder_path)
                except Exception as e:
                    self.organizer_logger.logger.error(f"Error al listar '{subfolder_path}': {e}")
                    continue

                if len(all_files) == 0:
                    continue

                tiff_count = sum(1 for f in all_files if f.lower().endswith(".tiff"))
                # Las copias `_ROT` de una carpeta procesada con 3.4.5 NO son originales
                # que deban tener TIFF: contarlas daría un jpg_count del doble y un falso
                # "no coinciden". Las versiones nuevas giran in-place y ya no las generan.
                jpg_count = sum(1 for f in all_files
                                if f.endswith(("JPG", "jpg", "JPEG", "jpeg"))
                                and utils.ROTATED_JPG_SUFFIX not in f)
                match = tiff_count == jpg_count

                results[subfolder_path] = {
                    "tiff_count": tiff_count,
                    "jpg_count": jpg_count,
                    "match": match,
                }

                if match:
                    self.organizer_logger.logger.info(
                        f"OK - {subfolder_path}: {jpg_count} imágenes JPG, {tiff_count} imágenes TIFF."
                    )
                    progress_callback.emit(f"\nOK - {subfolder_path}: {jpg_count} imágenes JPG, {tiff_count} imágenes TIFF.\n")
                else:
                    self.organizer_logger.logger.warning(
                        f"ERROR - {subfolder_path}: {jpg_count} imágenes JPG, {tiff_count} imágenes TIFF. No coinciden."
                    )
                    self.error_splitting_images += 1
                    self.images_error_splitting_images.append(f"ERROR - {subfolder_path}: {jpg_count} imágenes JPG, {tiff_count} imágenes TIFF. No coinciden.") 
                    
                    errors.append(subfolder_path)

        if errors:
            self.organizer_logger.logger.error(
                f"Se encontraron {len(errors)} carpeta(s) con discrepancias entre JPG y TIFF: {errors}"
            )
        elif results:
            self.organizer_logger.logger.info("Verificación completada sin errores.")
            progress_summarize.emit("Verificación completada sin errores.\n")
            progress_callback.emit("Verificación completada sin errores.\n")
        else:
            self.organizer_logger.logger.info(f"No se encontraron subcarpetas en las carpetas PB de: {input_folder}")

        return results

    def iterate_folders(self, input_folder: str, output_folder: str, mode: bool, min_size: str, thermal_sufix: str, rgb_sufix: str,
                        compress_checked: bool, quality: int, progress_callback, rename: bool, progress_bar, mismatch_hours: int, mismatch_minutes: int, extra_suffix: bool = False) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta lleva a cabo la función split_images.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - output_folder - carpeta de salida
        - mode - modo de separación. Puede ser por terminación o por tamaño del archivo. Si es True es or tamaño, si es False, por terminación.
        - min_size - Tamaño mínimo
        - thermal_sufix - terminación de las imágnes térmicas
        - rgb_sufix - terminación de las imágnes RGB
        - compress_checked - si es True se lleva a cabo la compresión. En caso contrario, no.
        - quality - calidad de la compresión
        - progress_callback - se devuelve información al hilo principal
        - rename - si es True se lleva a cabo el renombrado de las imágenes. En caso contrario, no.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - extra_suffix - si es True indica que el sufijo rgb es el extra que se añade en el interfaz. En caso contrario, seguimos el flujo habitual con sufijo térmico y rgb.
        """ 
        
        self.organizer_logger.logger.info("Directorio de entrada: " + input_folder)
        self.organizer_logger.logger.info("Directorio de salida: " + output_folder)
        
        progress_callback.emit("Analizando directorio: " + input_folder + "\n")
        
        self.split_images(input_folder, output_folder, mode, min_size, thermal_sufix, rgb_sufix, compress_checked, quality, progress_callback, rename, progress_bar, mismatch_hours, mismatch_minutes, extra_suffix)
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.iterate_folders(os.path.join(input_folder,dir), output_folder, mode, min_size, thermal_sufix, rgb_sufix, compress_checked, quality, progress_callback, rename, progress_bar, mismatch_hours, mismatch_minutes, extra_suffix)
        
    def split_images(self, input_folder: str, output_folder: str, mode: bool,  min_size: str, thermal_sufix: str, rgb_sufix: str,
                     compress_checked: bool, quality: int, progress_callback, rename: bool, progress_bar, mismatch_hours: int, mismatch_minutes: int, extra_suffix: bool = False,
                     solo_imagenes: list[str] | None = None) -> None:
        """
        Función que obtiene las imágenes existentes en el directorio de entrada, recorre dichas imágenes en un bucle y procesa cada una de las imágenes
        en una función independiente, copiándolas (y/o comprimiéndolas y/o renombrándolas) si se ha marcado la opción correspondiente) en el directorio de salida.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - mode - modo de separación. Puede ser por terminación o por tamaño del archivo. Si es True es por tamaño, si es False, por terminación.
        - min_size - Tamaño mínimo.
        - thermal_sufix - terminación de las imágnes térmicas.
        - rgb_sufix - terminación de las imágnes RGB.
        - compress_checked - si es True se lleva a cabo la compresión. En caso contrario, no.
        - quality - calidad de la compresión.
        - progress_callback - se devuelve información al hilo principal.
        - rename - si es True se lleva a cabo el renombrado de las imágenes. En caso contrario, no.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - extra_suffix - si es True indica que el sufijo rgb es el extra que se añade en el interfaz. En caso contrario, seguimos el flujo habitual con sufijo térmico y rgb.
        - solo_imagenes - si se pasa, se procesan SOLO esos nombres de fichero de la carpeta
          en vez de todos. Es lo que permite repartir la separación entre varias tareas
          paralelas con la IMAGEN como unidad y no la carpeta: un vuelo real puede tener
          2.500 fotos en dos carpetas, y por carpeta no habría forma de usar más de dos
          tareas (ver atom_core/sharding). A None (por defecto), todas las de la carpeta.
        """
        images = self.utils_obj.get_images_from_dir(input_folder)
        if solo_imagenes is not None:
            # Se INTERSECA con lo que hay en disco en vez de usar la lista tal cual:
            # entre el reparto y este momento la carpeta pudo cambiar, y un nombre
            # que ya no existe reventaría dentro del worker en vez de aquí.
            permitidas = set(solo_imagenes)
            images = [img for img in images if img in permitidas]
        progress_callback.emit("Procesando {0} imágenes".format(len(images)) + "\n") # Se envía información al iniciar el procesado de un directorio. Si no hay imágenes
        # se enviará 0 imágenes.

        # El botón de parar se comprueba ANTES de lanzar el lote, no imagen a imagen: un
        # ProcessPoolExecutor ya en marcha no se cancela a mitad. Es el mismo compromiso que
        # aceptó `compress_images` al paralelizarse, y como esta función se llama una vez por
        # carpeta, parar sigue surtiendo efecto entre carpetas.
        if self.stop:
            return

        cfg = SplitJobConfig(
            input_folder=input_folder, output_folder=output_folder, mode=mode, min_size=min_size,
            thermal_sufix=thermal_sufix, rgb_sufix=rgb_sufix, compress_checked=compress_checked,
            quality=quality, rename=rename, mismatch_hours=mismatch_hours,
            mismatch_minutes=mismatch_minutes, extra_suffix=extra_suffix,
        )

        def _worker_args_fn(image):
            return (image, cfg)

        def _on_progress(_pct):
            self.current_image_number += 1
            p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
            # y la cantidad actual de imágenes procesadas.
            progress_callback.emit(".") # Por cada imagen procesada, se emite un "." a la ventana de log.
            progress_bar.emit(p) # Por cada imagen procesada, se emite el porcentaje para mostrar en la barra de progreso.

        result = utils.run_batch(images, split_one_image, _worker_args_fn, on_progress=_on_progress)

        # Todo lo que pasó dentro de los procesos hijos vuelve aquí: los mensajes que el
        # usuario tiene que ver en la ventana de log y los contadores de error que luego
        # lee `get_summarize`. Sin esto, un fallo dentro de un worker sería invisible.
        for payload in result["results"]:
            for mensaje in payload["messages"]:
                progress_callback.emit(mensaje)
            self.organizer_logger.logger.debug("Nuevo nombre de la imagen: " + payload["new_name"])
            self.compress_image_obj.error_compress += payload["error_compress"]
            self.compress_image_obj.images_error_compress.extend(payload["images_error_compress"])
            self.exif_management_obj.error_exif_data += payload["error_exif_data"]
            self.exif_management_obj.images_error_exif_data.extend(payload["images_error_exif_data"])

        # Segunda red: un worker que muere antes de llegar a los try/except de `split_image`
        # (por ejemplo, si el proceso se queda sin memoria) no deja rastro en los contadores
        # de arriba, solo aquí.
        for image, error_str in result["errors"]:
            self.organizer_logger.logger.warning(f"ERROR: No se ha podido separar la imagen {image}: {error_str}")
            self.error_splitting_images += 1
            self.images_error_splitting_images.append(os.path.join(input_folder, image))

    def nombre_destino(self, image: str, input_folder: str, rename: bool, mismatch_hours: int, mismatch_minutes: int) -> str:
        """
        Devuelve el nombre con el que se guardará la imagen, o "" si no hay renombrado.

        Punto ÚNICO donde se decide el nombre de salida. Estaba en el bucle de
        `split_images`; al paralelizar la fase lo calcula cada worker, y tenerlo en un solo
        sitio es lo que garantiza que el nombre sea el mismo que daba el bucle secuencial.

        Arguments:
        ---------
        - image - nombre del fichero de origen.
        - input_folder - carpeta donde está la imagen.
        - rename - si es False se devuelve "" y la imagen conserva su nombre.
        - mismatch_hours / mismatch_minutes - desfase horario a aplicar al timestamp.
        """
        if not rename:
            # Si no lo queremos, se enviará un string vacío.
            return ""
        desfase = datetime.timedelta(hours=mismatch_hours, minutes=mismatch_minutes)
        timestamp_image = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, image))
        if timestamp_image is None:
            return ""
        # Obtenemos el nuevo nombre en el caso de querer renombrar el archivo
        new_date_time_image_name = timestamp_image + desfase
        new_name = (str(new_date_time_image_name)).replace(" ", "_").replace("-", "").replace(":", "")
        return new_name + "_" + image


    def _rgb_destination_folder(self, output_folder: str, image_name: str) -> str:
        """
        Devuelve la carpeta destino para una imagen RGB. Si el nombre contiene "_Z" (zoom), la carpeta destino
        es RGB/ZOOM (creada si no existe); en caso contrario, RGB.
        """
        if "_Z" in image_name:
            dest = os.path.join(output_folder, "RGB", "ZOOM")
        else:
            dest = os.path.join(output_folder, "RGB")
        os.makedirs(dest, exist_ok=True)
        return dest

    def split_image(self, image: str, input_folder: str, output_folder: str, mode_size: bool, min_size: str, thermal_sufix: str, rgb_sufix: str,
                    compress_checked: bool, quality: int, new_name: str, rename: bool, progress_callback, extra_suffix: bool = False):
        """
        Función que copia la imagen de entrada a la carpeta correspondiente y si es RGB y se quiere comprimir, se comprime con la calidad elegida en el GUI.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - mode_size - modo de separación. Puede ser por terminación o por tamaño del archivo. Si es True es por tamaño, si es False, por terminación.
        - min_size - Tamaño mínimo.
        - thermal_sufix - terminación de las imágnes térmicas.
        - rgb_sufix - terminación de las imágnes RGB.
        - compress_checked - si es True se lleva a cabo la compresión. En caso contrario, no.
        - quality - calidad de la compresión.
        - new_name - el nuevo nombre que se le dará a la imagen. Si es "", se asume que no se renombrará.
        - rename - si es True se lleva a cabo el renombrado de las imágenes. En caso contrario, no. No se usa actualmente.
        - extra_suffix - si es True indica que el sufijo rgb es el extra que se añade en el interfaz. En caso contrario, seguimos el flujo habitual con sufijo térmico y rgb.
        """ 
        # TODO: Posiblemente pueda encapsular algo más esta función, pues hay código repetido (cuando compruebo compress_checked)
        if not extra_suffix:
            if mode_size is True:  # Diferenciamos RGB de térmicas por tamaño
                self.organizer_logger.logger.debug("Dividiendo por tamaño")
                double_min_size = float(min_size.replace(",","."))*1000000  # Transformamos el min_size de entrada, el cual es un string.
                size_file = os.path.getsize(os.path.join(input_folder,image))
                if size_file > double_min_size:
                    # Siempre enviamos new_name, aunque sea vacío. La función se encargará de tratarlo de forma automática.
                    if compress_checked:
                        self.organizer_logger.logger.debug("Comprimiendo la imagen RGB")
                        self.compress_image_obj.compress_image(image, input_folder,self._rgb_destination_folder(output_folder, image), quality, new_name, progress_callback=progress_callback)
                    else:
                        self.organizer_logger.logger.debug("Copiando la imagen RGB")
                        _reflink_or_copy(os.path.join(input_folder, image),os.path.join(self._rgb_destination_folder(output_folder, image), new_name))
                else:
                    self.organizer_logger.logger.debug("Copiando la imagen térmica")  # Las imágenes térmicas no las comprimimos.
                    _reflink_or_copy(os.path.join(input_folder, image),os.path.join(os.path.join(output_folder,"TERMICA"), new_name))
            elif mode_size is False:  # Diferenciamos RGB de térmicas por terminación
                self.organizer_logger.logger.debug("Dividiendo por sufijo")
                image_no_extension = image.rsplit( ".", 1 )[ 0 ]
                # print(image_no_extension)
                # print(self.utils_obj.check_suffix_within_the_name(image_no_extension, rgb_sufix + "," + thermal_sufix))

                # En la siguiente función cortamos lo que hay después de las terminaciones, por si los archivos de las fotos tienen una terminación posterior que no interesa.
                if rgb_sufix == "":
                    image_no_extension = self.utils_obj.check_suffix_within_the_name(image_no_extension, thermal_sufix)
                elif thermal_sufix == "":
                    image_no_extension = self.utils_obj.check_suffix_within_the_name(image_no_extension, rgb_sufix)
                else:    
                    image_no_extension = self.utils_obj.check_suffix_within_the_name(image_no_extension, rgb_sufix + "," + thermal_sufix)

                # Las condiciones programadas se explican de la siguiente manera. Comprobamos que el sufijo no está vacío y, por lo tanto, tenemos en cuenta la terminación o,
                # en el caso de estar vacío, la extensión del archivo no acaba con la extensión puesta al otro tipo de imágenes. De este modo, no se mezcla en un mismo directorio.
                # En el elif, comprobaría lo mismo, pero para el otro tipo de imágenes. 
                if image_no_extension.endswith(tuple(rgb_sufix.rsplit(","))) and rgb_sufix != "" or (rgb_sufix == "" and not image_no_extension.endswith(tuple(thermal_sufix.rsplit(",")))):
                    if compress_checked:
                        self.organizer_logger.logger.debug("Comprimiendo la imagen RGB")
                        self.compress_image_obj.compress_image(image, input_folder,self._rgb_destination_folder(output_folder, image), quality, new_name, progress_callback=progress_callback)
                    else:
                        self.organizer_logger.logger.debug("Copiando la imagen RGB")
                        _reflink_or_copy(os.path.join(input_folder, image),os.path.join(self._rgb_destination_folder(output_folder, image), new_name))
                elif image_no_extension.endswith(tuple(thermal_sufix.rsplit(","))) and thermal_sufix != "" or (thermal_sufix == "" and not image_no_extension.endswith(tuple(rgb_sufix.rsplit(",")))):
                    self.organizer_logger.logger.debug("Copiando la imagen térmica")  # Las imágenes térmicas no las comprimimos.
                    _reflink_or_copy(os.path.join(input_folder, image),os.path.join(os.path.join(output_folder,"TERMICA"), new_name))
                else:
                    pass
        else:
            self.organizer_logger.logger.debug("Dividiendo por el sufijo extra de la RGB")
            image_no_extension = image.rsplit( ".", 1 )[ 0 ]

            # En la siguiente función cortamos lo que hay después de las terminaciones, por si los archivos de las fotos tienen una terminación posterior que no interesa.
            image_no_extension = self.utils_obj.check_suffix_within_the_name(image_no_extension, rgb_sufix)

            # Comprobamos que el sufijo no está vacío y, por lo tanto, tenemos en cuenta la terminación.
            if image_no_extension.endswith(tuple(rgb_sufix.rsplit(","))) and rgb_sufix != "":
                if compress_checked:
                    self.organizer_logger.logger.debug("Comprimiendo la imagen RGB Extra")
                    self.compress_image_obj.compress_image(image, input_folder,os.path.join(output_folder,"RGB_Extra"), quality, new_name, progress_callback=progress_callback)
                else:
                    self.organizer_logger.logger.debug("Copiando la imagen RGB Extra")
                    _reflink_or_copy(os.path.join(input_folder, image),os.path.join(os.path.join(output_folder,"RGB_Extra"), new_name))
        
    def iterate_folders_for_DJI(self, input_folder: str, exiftool_exe:str, dji_utility: str, progress_callback, progress_bar, emissivity = 0.9, humidity = 50.0, auto_temp = False, up_threshold_temperature = 0, low_threshold_temperature = 500.0, rotate_90: bool = False, rotate_minus_90: bool = False, auto_rotate: bool = False, just_atom_selection = False, generate_gray_scale_images: bool = False, generate_colormap_images: bool = False) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta lleva a cabo la función split_images.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - exiftool_exe - ruta del exiftool.exe.
        - dji_utility - ruta de las utilidades DJI.
        - progress_callback - se devuelve información al hilo principal
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - emissivity - valor de emisividad necesaria para calcular la temperatura.
        - humidity - valor de humedad necesaria para calcular la temperatura.
        - auto_temp - booleano que a True indica que el gradiente de temperatura es el generado por la utilidad de DJI. A false tiene en cuenta los parámetros up_threshold_temperature y low_threshold_temperature.
        - up_threshold_temperature - Todos los valores por encima de esta temperatura tendrán el valor máximo de temperatura de la imagen térmica.
        - low_threshold_temperature - Todos los valores por debajo de esta temperatura tendrán el valor mínimo de temperatura de la imagen térmica.
        - rotate_90 - booleano que indica que la imagen TIFF se rotará 90 grados en sentido de las agujas del reloj.
        - rotate_minus_90 - booleano que indica que la imagen TIFF se rotará 90 grados en sentido contrario a las agujas del reloj.
        """ 
        # Con qué criterio de giro entra la conversión. Sin esto, un TIFF que sale sin
        # rotar no se puede atribuir: ¿flag apagado, o criterio ilegible? (caso v3.4.3).
        progress_callback.emit(
            "\nGiro del TIFF: auto={0} 90={1} -90={2}\n".format(
                auto_rotate, rotate_90, rotate_minus_90))
        self.convert_dji_images_to_tif(input_folder, exiftool_exe, dji_utility, progress_callback, progress_bar, emissivity, humidity, auto_temp, up_threshold_temperature, low_threshold_temperature, rotate_90, rotate_minus_90, auto_rotate, just_atom_selection, generate_gray_scale_images, generate_colormap_images)
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.iterate_folders_for_DJI(os.path.join(input_folder,dir), exiftool_exe, dji_utility, progress_callback, progress_bar, emissivity, humidity, auto_temp, up_threshold_temperature, low_threshold_temperature, rotate_90, rotate_minus_90, auto_rotate, just_atom_selection, generate_gray_scale_images, generate_colormap_images)

    def convert_dji_images_to_tif(self, input_folder: str, exiftool_exe:str, dji_utility: str, progress_callback, progress_bar, emissivity: float = 0.9, humidity: float = 50.0, auto_temp = False, up_threshold_temperature = 0, low_threshold_temperature = 500.0, rotate_90: bool = False, rotate_minus_90: bool = False, auto_rotate: bool = False, just_atom_selection = False, generate_gray_scale_images: bool = False, generate_colormap_images: bool = False):
        """
        Función que recorre todas las imágenes JPG que se encuentren an la carpeta de entrada input_folder y llama después a la función convert_dji_image_to_tif

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - exiftool_exe - ruta del exiftool.exe.
        - dji_utility - ruta de las utilidades DJI.
        - progress_callback - se devuelve información al hilo principal
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - emissivity - valor de emisividad necesaria para calcular la temperatura.
        - humidity - valor de humedad necesaria para calcular la temperatura.
        - auto_temp - booleano que a True indica que el gradiente de temperatura es el generado por la utilidad de DJI. A false tiene en cuenta los parámetros up_threshold_temperature y low_threshold_temperature.
        - up_threshold_temperature - Todos los valores por encima de esta temperatura tendrán el valor máximo de temperatura de la imagen térmica.
        - low_threshold_temperature - Todos los valores por debajo de esta temperatura tendrán el valor mínimo de temperatura de la imagen térmica.
        - rotate_90 - booleano que indica que la imagen TIFF se rotará 90 grados en sentido de las agujas del reloj.
        - rotate_minus_90 - booleano que indica que la imagen TIFF se rotará 90 grados en sentido contrario a las agujas del reloj.
        """ 
        if os.path.basename(input_folder)== "Escala_de_grises": # Evito realizar el proceso dentro de esta carpeta, pues dará error.
            return
        # Se excluyen las copias `_ROT` heredadas de 3.4.5: si se re-procesa una carpeta
        # de aquella versión, intentaría convertirlas (son JPG normales, ya sin payload
        # radiométrico -> fallo por imagen) y descuadraría `jpg_count == tiff_count`.
        # Desde 3.4.6 el giro es in-place y no se generan copias nuevas.
        images = self.utils_obj.get_images_from_dir(input_folder, [utils.ROTATED_JPG_SUFFIX])
        
        # print(f"Procesando {len(images)} imágenes")

        if (len(images) > 0):
            progress_callback.emit("\nAnalizando directorio: {0}\n".format(input_folder))
            progress_callback.emit("Procesando {0} imágenes\n".format(len(images))) # Se envía información al iniciar el procesado de un directorio.
            self.organizer_logger.logger.info(f"Analizando directorio: {input_folder}")
            self.organizer_logger.logger.info(f"Procesando {len(images)} imágenes")

        # Comento estas líneas pues querían que las imágenes tif generadas se guardaran en la misma carpeta que las JPG y lo que hacía era guardarlas en otra carpeta.
        # output_folder = ""
        # if len(images) > 0:
        #     self.utils_obj.prepare_output_folder(input_folder,["TIFF"])
        #     output_folder = os.path.join(input_folder, "TIFF")
        if not just_atom_selection or (just_atom_selection and os.path.basename(input_folder)== "Seleccion_ATOM"):
            # Pre-creamos las subcarpetas de salida ANTES del pool: prepare_output_folder
            # no es thread-safe (os.listdir + makedirs sin exist_ok -> race entre workers).
            # Al existir ya, la comprobación interna las ve y no intenta crearlas.
            if generate_gray_scale_images:
                self.utils_obj.prepare_output_folder(input_folder, ["Escala_de_grises"])
            if generate_colormap_images:
                self.utils_obj.prepare_output_folder(input_folder, ["Color_gradiente"])

            # Los flags de conversión son idénticos para todas las imágenes del directorio.
            convert_kwargs = dict(
                emissivity=emissivity, humidity=humidity, auto_temp=auto_temp,
                up_threshold_temperature=up_threshold_temperature,
                low_threshold_temperature=low_threshold_temperature,
                rotate_90=rotate_90, rotate_minus_90=rotate_minus_90, auto_rotate=auto_rotate,
                just_atom_selection=just_atom_selection,
                generate_gray_scale_images=generate_gray_scale_images,
                generate_colormap_images=generate_colormap_images,
            )
            errors_before = self.error_splitting_images
            pending_exif = []
            try:
                # Conversión DJI->TIFF en paralelo. Cada imagen escribe archivos con
                # nombre propio (.raw, .tiff) -> sin colisión. El batch EXIF (H1) se
                # mantiene secuencial al final, igual que antes.
                # Nota cancelación: cada worker comprueba self.stop al arrancar; al pulsar
                # "parar" pueden completarse hasta max_dji_workers imágenes ya en vuelo
                # (antes, secuencial, era 1). No afecta al resultado, solo a la latencia de parada.
                with ThreadPoolExecutor(max_workers=max(1, self.max_dji_workers)) as executor:
                    futures = [
                        executor.submit(
                            self._convert_one_safe, input_folder, image, exiftool_exe,
                            dji_utility, progress_callback, progress_bar, convert_kwargs)
                        for image in images
                    ]
                    for future in as_completed(futures):
                        pair = future.result()  # _convert_one_safe nunca relanza
                        if pair:
                            pending_exif.append(pair)
            finally:
                self._run_exif_batch(pending_exif, exiftool_exe, progress_callback)
            # Reporte final: cuántas imágenes de ESTE directorio no se pudieron procesar.
            nuevos_fallos = self.error_splitting_images - errors_before
            if nuevos_fallos > 0:
                progress_callback.emit(
                    "\n{0} imagen(es) no se pudieron procesar en este directorio y se omitieron. "
                    "El resto del vuelo continuó.\n".format(nuevos_fallos))

    def _convert_one_safe(self, input_folder, image, exiftool_exe, dji_utility,
                          progress_callback, progress_bar, convert_kwargs):
        """
        Worker de un solo item para el pool de conversión DJI->TIFF. Emite progreso,
        llama a convert_dji_image_to_tif y AÍSLA cualquier excepción no controlada:
        registra el fallo (mismo mecanismo que los except internos) y devuelve None,
        para que una imagen problemática NO tumbe el vuelo entero. Devuelve el par
        (src_jpg, dst_tiff) para el batch EXIF diferido, o None si se omitió/falló.
        """
        if self.stop:
            return None
        with self._stats_lock:
            self.current_image_number += 1
            p = utils.safe_pct(self.current_image_number, self.total_images_number)
        progress_callback.emit(".")  # un "." por imagen a la ventana de log
        progress_bar.emit(p)         # porcentaje a la barra de progreso
        try:
            # input_folder == output_folder para que el TIFF se guarde junto al JPG.
            return self.convert_dji_image_to_tif(
                input_folder, input_folder, image, exiftool_exe, dji_utility,
                progress_callback, progress_bar, defer_exif=True, **convert_kwargs)
        except Exception as e:
            with self._stats_lock:
                self.error_splitting_images += 1
                _first = self.error_splitting_images == 1  # ¿la primera de la tanda? (bajo lock, sin race)
                self.images_error_splitting_images.append(os.path.join(input_folder, image))
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error(
                "ERROR no controlado procesando {0}: {1}. Se omite la imagen y se continúa el vuelo.".format(
                    os.path.join(input_folder, image), e))
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            # En headless el logger de fichero está desactivado (create_file_handler=False),
            # así que la causa real solo llega si la metemos en el progress_callback (nuestro
            # tee a /tmp + panel). Surfaceamos tipo + mensaje de la excepción, y para la
            # PRIMERA de la tanda, además el traceback comprimido (dónde reventó).
            _tb = ""
            if _first:
                _tb = " | traceback: " + " <- ".join(
                    traceback.format_exc().strip().splitlines()[-4:])
            progress_callback.emit(
                "\nERROR procesando {0}: {1}: {2}. Se omite y se continúa el vuelo.{3}\n".format(
                    image, type(e).__name__, e, _tb))
            return None

    def _register_image_error(self, path):
        """Registra una imagen fallida de forma thread-safe (contador + lista)."""
        with self._stats_lock:
            self.error_splitting_images += 1
            self.images_error_splitting_images.append(path)

    def _run_exif_batch(self, pairs, exiftool_exe, progress_callback=None):
        """Copia los tags EXIF de todos los (src_jpg, dst_tiff) con UN solo proceso
        exiftool -stay_open. Equivale a los N subprocess.run inline pero sin re-arrancar
        el intérprete Perl por imagen (~5x)."""
        if not pairs:
            return
        import tempfile
        argfile = None
        try:
            fd, argfile = tempfile.mkstemp(suffix="_exifargs.txt", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for src, dst in pairs:
                    f.write("-tagsfromfile\n{0}\n-overwrite_original_in_place\n{1}\n-execute\n".format(src, dst))
                f.write("-stay_open\nFalse\n-execute\n")  # cierre: sin esto exiftool cuelga
            exe = exiftool_exe if _is_windows() else external_tools.resolve_tool("exiftool")
            result = subprocess.run(
                [exe, "-stay_open", "True", "-@", argfile],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            if result.returncode != 0 and progress_callback is not None:
                progress_callback.emit("\nAviso: exiftool batch devolvió código {0}.\n".format(result.returncode))
        finally:
            if argfile and os.path.exists(argfile):
                try:
                    os.remove(argfile)
                except OSError:
                    pass

    def _dji_measure_to_raw_linux(self, image_path: str, raw_path: str, humidity: float, emissivity: float, lib_dir: str):
        """
        Equivalente Linux de `dji_irp -s IMG -a measure --humidity H --emissivity E
        --measurefmt float32 -o IMG.raw`. Genera el mismo .raw (buffer plano float32,
        °C, row-major, resolución del sensor) que el ejecutable de Windows.

        Se ejecuta en un SUBPROCESO EFÍMERO (dji_irp_linux.py), igual que Windows lanza
        un dji_irp.exe por imagen: aísla el proceso y permite paralelizar sin dudas de
        thread-safety de la librería nativa.

        IMPORTANTE — el rc NO es fiable: los hilos nativos de libdirp segfaultean
        SIEMPRE en el teardown del proceso (rc 139), pero es BENIGNO: la medida se
        completa y el .raw se escribe de forma atómica (.part + fsync + rename) ANTES
        del crash. Verificado: el .raw sale byte-idéntico en cada ejecución. Igual que
        Windows, que ya ignora el rc de dji_irp.exe, validamos el .raw, no el rc: si
        existe y su tamaño es un múltiplo de float32 no vacío, la conversión fue OK;
        si falta (la escritura atómica nunca deja un .raw a medias), fue un fallo real.
        """
        result = subprocess.run(
            [sys.executable, _DJI_IRP_LINUX, image_path, raw_path,
             repr(float(humidity)), repr(float(emissivity)), lib_dir],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        raw_ok = (os.path.exists(raw_path) and os.path.getsize(raw_path) > 0
                  and os.path.getsize(raw_path) % 4 == 0)
        if not raw_ok:
            raise RuntimeError(
                "Conversor térmico Linux falló (rc={0}) en {1}: {2}".format(
                    result.returncode, image_path, (result.stderr or "").strip()))

    def rotate_thermal_jpgs_in_place(self, input_folder: str, progress_callback, progress_bar,
                                     rotate_90: bool = False, rotate_minus_90: bool = False,
                                     auto_rotate: bool = False) -> int:
        """Gira EN SU SITIO cada JPG térmico, conservando su nombre de siempre.

        Hasta v3.4.5 esto escribía una copia aparte `<nombre>_ROT.JPG` y dejaba el
        original sin girar, así que la salida venía DUPLICADA. Cas (2026-08-05):
        «no debes poner la girada con otro nombre, simplemente giras la jpg y la
        tiff». Ahora la carpeta queda con un solo fichero por imagen: el
        `*_T.JPG` girado y su `*_T.tiff` girado, ambos con el mismo encuadre.

        ⚠️ Consecuencia asumida: el `*_T.JPG` de DJI es un R-JPEG con payload
        radiométrico propietario, y re-guardarlo con PIL lo destruye. Una vez
        girado, ese fichero ya NO se puede volver a convertir a TIFF. Por eso el
        paso corre al FINAL, cuando el TIFF ya está escrito y verificado, y por
        eso importa que el original íntegro siga en la carpeta de ORIGEN, que el
        pipeline solo lee y copia (`_reflink_or_copy`), nunca modifica.

        Gira lo MISMO que giró el TIFF (mismo criterio, `read_auto_rotate_degree`),
        para que el par TIFF/JPG case. Devuelve el número de imágenes giradas.

        Arguments:
        ---------
        - input_folder - raíz de TERMICA; se recorre recursivamente.
        - progress_callback - se devuelve información al hilo principal.
        - progress_bar - porcentaje a la barra de progreso.
        - rotate_90 / rotate_minus_90 / auto_rotate - mismos flags de la conversión.
        """
        if not (rotate_90 or rotate_minus_90 or auto_rotate):
            # Salida silenciosa NO: los tres flags a False es indistinguible, desde el
            # log, de "el criterio salió 0" o de "el paso ni se ejecutó". Costó una
            # ronda entera de diagnóstico con Daniel (v3.4.3): el TIFF salía sin girar
            # y aquí no se giraba nada, sin una sola línea que dijera por qué.
            progress_callback.emit(
                "\nNo se giran los JPG térmicos: el modo de giro del TIFF está en «Sin giro» "
                "(rotate_90=False, rotate_minus_90=False, auto=False).\n")
            return 0  # sin criterio de giro no hay nada que hacer
        if not os.path.isdir(input_folder):
            self.organizer_logger.logger.warning(
                f"No se pueden girar los JPG térmicos: no existe {input_folder}")
            return 0

        giradas = 0
        for ruta, _, _ in os.walk(input_folder):
            if self.stop:
                break
            if os.path.basename(ruta) in ("Escala_de_grises", "Color_gradiente"):
                continue
            # La exclusión `_ROT` se mantiene por LEGADO: una carpeta procesada con
            # 3.4.5 tiene copias `_ROT` que no hay que volver a girar ni contar.
            # Las versiones nuevas ya no las generan.
            images = self.utils_obj.get_images_from_dir(ruta, [utils.ROTATED_JPG_SUFFIX])
            if not images:
                continue

            if auto_rotate:
                degree = self.read_auto_rotate_degree(ruta, progress_callback)
            elif rotate_90:
                degree = 90
            else:
                degree = 270
            if degree not in (90, 270):
                continue  # sin criterio (0) no se gira

            # PIL gira en sentido ANTIhorario, el criterio está en horario: 90º
            # horario == ROTATE_270. Mismo mapeo que `rotate_tiff_images`; si
            # divergiera, el JPG saldría del revés respecto a su TIFF.
            transpose = Image.ROTATE_270 if degree == 90 else Image.ROTATE_90

            progress_callback.emit(
                "\nGirando {0} imágenes térmicas ({1}º) en el directorio {2}\n".format(
                    len(images), degree, ruta))
            for image in images:
                if self.stop:
                    break
                giradas += self._rotate_one_thermal_jpg_in_place(
                    ruta, image, transpose, progress_callback)

        if giradas:
            self.organizer_logger.logger.info(
                f"Imágenes térmicas giradas en su sitio: {giradas}.")
        return giradas

    def _rotate_one_thermal_jpg_in_place(self, folder: str, image_name: str, transpose: int,
                                         progress_callback) -> int:
        """Gira una térmica sobre sí misma. Devuelve 1 si se giró, 0 si se omitió o falló.

        Aísla los fallos por imagen igual que el resto del pipeline: una foto
        corrupta no puede tumbar el vuelo entero, y menos en un paso que corre
        DESPUÉS de que lo importante (el TIFF) ya esté en disco.

        Se escribe a un temporal y se reemplaza con `os.replace` (atómico): si el
        guardado se corta a medias, la térmica original sigue entera en su sitio en
        vez de quedar truncada. Con el giro in-place esto ya no es un derivado
        descartable — es el único fichero que queda.
        """
        origen = os.path.join(folder, image_name)
        temporal = origen + ".rot.tmp"
        try:
            with Image.open(origen) as image_open:
                if image_open.height > image_open.width:
                    # Las térmicas DJI son apaisadas de fábrica (640x512): si esta ya
                    # viene vertical, es que se giró en una pasada anterior sobre la
                    # misma carpeta. Volver a girarla la dejaría a 180º.
                    self.organizer_logger.logger.info(
                        f"Ya estaba girada, se deja como está: {origen}")
                    return 0
                # El EXIF se arrastra: lleva la geolocalización y la fecha, que es
                # justo lo que se consulta luego sobre estas fotos.
                exif = image_open.info.get("exif")
                girada = image_open.transpose(transpose)
                # quality=95: el re-encodado es inevitable (PIL no gira sin
                # descomprimir), así que al menos que no añada artefactos visibles
                # sobre la única copia que va a quedar.
                if exif:
                    girada.save(temporal, format="JPEG", quality=95, exif=exif)
                else:
                    girada.save(temporal, format="JPEG", quality=95)
            os.replace(temporal, origen)
            return 1
        except Exception as e:
            if os.path.exists(temporal):
                try:
                    os.remove(temporal)
                except OSError:
                    pass
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error(
                "ERROR: no se pudo girar la imagen térmica {0}: {1}".format(origen, e))
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            progress_callback.emit(
                "ERROR: no se pudo girar la imagen térmica {0}.\n".format(origen))
            self._register_image_error(origen)
            return 0

    def read_auto_rotate_degree(self, input_folder: str, progress_callback) -> int:
        """Criterio de giro AUTO de un vuelo, MEMOIZADO por carpeta.

        El criterio depende solo de `input_folder` y no cambia durante la corrida: lo
        deja escrito la fase de rotación, que va ANTES que la conversión. Resolverlo
        una vez por vuelo en vez de una vez por imagen ahorra, por cada térmica, hasta
        4 `os.path.exists` y un `pd.read_csv` entero.

        Cambio de comportamiento deliberado en el caso degradado: cuando el CSV no se
        puede leer, el aviso al log y el registro del error ocurren **una vez por
        vuelo**, no una vez por imagen. Antes el mismo path del CSV entraba 1300 veces
        en «Imágenes con error» y repetía el mismo aviso en el log.
        """
        with self._criterio_giro_lock:
            if input_folder in self._criterio_giro_cache:
                return self._criterio_giro_cache[input_folder]
            degree = self._leer_criterio_giro(input_folder, progress_callback)
            self._criterio_giro_cache[input_folder] = degree
            return degree

    def _leer_criterio_giro(self, input_folder: str, progress_callback) -> int:
        """Lectura real del criterio de giro. Ver `read_auto_rotate_degree`, que la memoiza.

        Criterio de giro AUTO de un vuelo: 0, 90 o 270 grados (horario).

        La decisión NO se recalcula aquí: la toma el paso de rotación y la deja
        escrita en `CSVs/<PBX_VXX>_Videofiles.csv`
        (columna `Degree`). Este método es la ÚNICA lectura de ese criterio, para
        que la conversión a TIFF y la copia girada del JPG no puedan divergir:
        ambas tienen que girar lo mismo o el par TIFF/JPG deja de casar.

        Ese CSV vivía en `MINIATURAS/<vuelo>_miniaturas/` hasta que se eliminaron las
        miniaturas, luego en `CSVs/<vuelo>/` (v3.4.0-v3.4.1), luego plano en `CSVs/`
        (v3.4.2-v3.4.4) y ahora en `CSVs/_criterio/`. Se mantienen TODOS los fallbacks
        de lectura para poder re-procesar carpetas generadas por versiones anteriores
        sin que el vuelo deje de rotar en silencio.

        Sin criterio legible (CSV ausente, vacío o sin la columna) devuelve 0 —
        no rotar — y lo dice; nunca revienta el vuelo.

        Arguments:
        ---------
        - input_folder - carpeta del vuelo (dentro de TERMICA).
        - progress_callback - se devuelve información al hilo principal.
        """
        if os.path.basename(input_folder) == "Seleccion_ATOM":
            pb_v_name = os.path.basename(input_folder.rstrip("/\\").removesuffix("Seleccion_ATOM").rstrip("/\\"))
        else:
            pb_v_name = os.path.basename(input_folder)

        root_folder = input_folder.split("TERMICA")[0].rstrip("/\\")
        csv_name = pb_v_name + "_Videofiles.csv"
        csvs_folder = os.path.join(root_folder, "CSVs")
        candidatos = (
            os.path.join(csvs_folder, utils.CRITERIO_DIRNAME, csv_name),                           # Actual: CSVs/_criterio/.
            os.path.join(csvs_folder, csv_name),                                                   # v3.4.2-v3.4.4: CSVs/ plano.
            os.path.join(csvs_folder, pb_v_name, csv_name),                                        # v3.4.0-v3.4.1: subcarpeta por vuelo.
            os.path.join(root_folder, "MINIATURAS", pb_v_name + "_miniaturas", csv_name),          # Legacy: las carpetas antiguas sí llevaban el sufijo _miniaturas.
        )
        criterio_path = candidatos[0]
        for candidato in candidatos:
            if os.path.exists(candidato):
                criterio_path = candidato  # Carpeta procesada por una versión anterior: el criterio sigue donde lo dejó.
                break
        try:
            df_pb_csv = pd.read_csv(criterio_path)
            # El CSV puede existir y estar VACÍO (solo cabecera): pasa cuando el paso
            # de rotación se fue por la rama "hay demasiadas imágenes que no rotan
            # igual" y no escribió ninguna fila. Ahí `df["Degree"][0]` lanzaba KeyError,
            # que no captura el `except FileNotFoundError` de abajo, y reventaba la
            # conversión de CADA imagen del vuelo sin decir por qué. Sin fila no hay
            # criterio: no se rota, pero se dice en el log.
            if df_pb_csv.empty or "Degree" not in df_pb_csv.columns:
                progress_callback.emit(
                    "\nEl criterio de giro ({0}) está vacío: el paso de rotación no llegó a "
                    "decidir un ángulo para este vuelo. El TIFF se genera SIN rotar.\n".format(
                        pb_v_name + "_Videofiles.csv"))
                self.organizer_logger.logger.warning(
                    f"{pb_v_name}_Videofiles.csv sin filas: no hay Degree que aplicar, TIFF sin rotar.")
                return 0
            return int(df_pb_csv["Degree"][0])
        # EmptyDataError = el CSV existe pero tiene 0 bytes (ni cabecera). Mismo
        # desenlace que si faltara: no hay criterio, no se rota, y se avisa.
        except (FileNotFoundError, pd.errors.EmptyDataError) as file_not_found:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error(file_not_found.__str__)
            self.organizer_logger.logger.exception(file_not_found)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            progress_callback.emit("\nNo se pudo leer el criterio de giro {0}. No se rota la imagen.\n".format(csv_name))
            self._register_image_error(criterio_path)
            return 0

    def degree_de_giro(self, rotate_90: bool, rotate_minus_90: bool, auto_rotate: bool, input_folder: str, progress_callback) -> int:
        """
        Resuelve en GRADOS el giro que toca aplicar a la térmica: 90 (horario), 270
        (antihorario) o 0 (ninguno). Los flags manuales mandan sobre el automático.

        El criterio automático sale de `read_auto_rotate_degree`, que es el punto ÚNICO
        de lectura (columna `Degree` del `<vuelo>_Videofiles.csv`). El `_T.JPG` se gira
        con ese mismo criterio, así que TIFF, JPG y escala de grises quedan siempre en
        la misma orientación: norte arriba.
        """
        if rotate_90:
            return 90
        if rotate_minus_90:
            return 270
        if auto_rotate:
            degree = self.read_auto_rotate_degree(input_folder, progress_callback)
            if degree in (90, 270):
                return degree
        return 0

    def rotar_arrays_termicos(self, arr, array_to_normalize, degree: int):
        """
        Gira los DOS arrays térmicos con el mismo criterio y devuelve la pareja.

        `arr` es el que acaba en el .tiff (float32 de temperaturas) y `array_to_normalize`
        el que alimenta la escala de grises y el mapa de color. Tienen que girar JUNTOS:
        si divergen, la vista en gris deja de servir para comprobar la orientación del
        TIFF, que es justo para lo que se mira (el .tiff float32 se abre blanco en
        Windows). Estaban duplicados en seis ramas; aquí es un único sitio.
        """
        if degree == 90:
            return np.rot90(arr, 1, (1, 0)), np.rot90(array_to_normalize, 1, (1, 0))  # Clockwise
        if degree == 270:
            return np.rot90(arr, 1, (0, 1)), np.rot90(array_to_normalize, 1, (0, 1))  # Counterclockwise
        return arr, array_to_normalize

    def convert_dji_image_to_tif(self, input_folder: str, output_folder: str, image_name: str, exiftool_exe:str, dji_utility: str, progress_callback, progress_bar, emissivity: float = 0.9, humidity: float = 50.0, auto_temp = False, up_threshold_temperature = 20, low_threshold_temperature = 0, rotate_90: bool = False, rotate_minus_90: bool = False, auto_rotate: bool = False, just_atom_selection = False, generate_gray_scale_images: bool = False, generate_colormap_images: bool = False, defer_exif: bool = False):
        """
        Función que convierte la imagen JPG dada por el parámetro image_name a formato TIFF.
        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - exiftool_exe - ruta del exiftool.exe.
        - image_name - nombre de la imagen a convertir a TIFF.
        - dji_utility - ruta de las utilidades DJI.
        - progress_callback - se devuelve información al hilo principal
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - emissivity - valor de emisividad necesaria para calcular la temperatura.
        - humidity - valor de humedad necesaria para calcular la temperatura.
        - auto_temp - booleano que a True indica que el gradiente de temperatura es el generado por la utilidad de DJI. A false tiene en cuenta los parámetros up_threshold_temperature y low_threshold_temperature.
        - up_threshold_temperature - Todos los valores por encima de esta temperatura tendrán el valor máximo de temperatura de la imagen térmica.
        - low_threshold_temperature - Todos los valores por debajo de esta temperatura tendrán el valor mínimo de temperatura de la imagen térmica.
        - rotate_90 - booleano que indica que la imagen TIFF se rotará 90 grados en sentido de las agujas del reloj.
        - rotate_minus_90 - booleano que indica que la imagen TIFF se rotará 90 grados en sentido contrario a las agujas del reloj.
        """ 
        
        # self.organizer_logger.logger.debug("Seleccion_ATOM: {0}".format(just_atom_selection))
        # self.organizer_logger.logger.debug("Girar auto: {0}".format(auto_rotate))

        raw_path = os.path.join(input_folder, image_name + ".raw")
        # Resultado de la invocación al conversor. Se arrastra hasta el punto donde se
        # abre el .raw: si el fichero no aparece PERO el conversor devolvió 0, el fallo
        # es silencioso y sin este dato no hay forma de distinguirlo de un fallo ruidoso.
        dji_rc = None
        dji_salida = ""

        # Radiografía del fichero ANTES de tocarlo el conversor. Cuando el SDK
        # devuelve -16 ("create R-JPEG dirp handle failed") no dice si el problema
        # es suyo o de la imagen que le llega, y sin esta línea no hay forma de
        # distinguirlo desde el log — que es el único canal de diagnóstico, porque
        # el usuario final no ejecuta nada por terminal. El hash permite además
        # comparar el fichero de salida contra el original de la tarjeta sin
        # pedirle nada al usuario. Es SOLO diagnóstico: no aborta la conversión,
        # el conversor sigue teniendo la última palabra.
        _rjpeg_info = rjpeg.inspect_rjpeg(os.path.join(input_folder, image_name))
        _rjpeg_linea = rjpeg.describe(_rjpeg_info, image_name)
        progress_callback.emit("\n{0}\n".format(_rjpeg_linea))
        if not _rjpeg_info["ok"]:
            self.organizer_logger.logger.error(_rjpeg_linea)

        if _is_windows():
            # dji_utility apunta a programas_externos/DJI/dji_irp.exe (carpeta única)
            subproceso = '"{0}" -s "{1}" -a measure --humidity {2} --emissivity {3} --measurefmt float32 -o "{4}"'.format(
                dji_utility, os.path.join(input_folder, image_name), humidity, emissivity, raw_path)
            # Se captura el resultado: si dji_irp.exe falla, el único síntoma era un .raw
            # que no aparece ("Posible error del conversor DJI") y el motivo real (DLL que
            # falta, permisos, disco lleno, argumento rechazado) se perdía. No se aborta
            # aquí: el flujo de abajo ya trata el .raw ausente.
            try:
                proc = subprocess.run(
                    subproceso,
                    capture_output=True, text=True, errors="replace",
                    # dji_irp.exe es un binario de CONSOLA y la app se congela sin
                    # consola (atom_organizer_webview.spec: console=False), así que los
                    # handles estándar que hereda son inválidos. capture_output ya cubre
                    # stdout/stderr, pero stdin seguía heredándose: DEVNULL le da uno
                    # válido en vez de un handle muerto.
                    stdin=subprocess.DEVNULL,
                    # cwd = carpeta del SDK. Es lo correcto (libdirp.dll lee
                    # `libv_list.ini` por nombre relativo, o sea contra el CWD, para
                    # saber qué libv_*.dll cargar), pero que quede claro que NO era la
                    # causa del fallo de LA_ISLA, aunque el commit que lo introdujo lo
                    # diera por hecho: con este cwd el SDK arranca bien y aun asi falla.
                    # Log de la corrida del 2026-08-04 15:12 ya con 3.2.6:
                    #     DIRP API version number : 0x13          <- el SDK carga OK
                    #     ERROR: create R-JPEG dirp handle failed  <- y RECHAZA la imagen
                    # es decir, dirp_create_from_rjpeg devuelve -16 (0xFFFFFFF0) sobre un
                    # M2EA autodetectado correctamente. El problema esta en la imagen que
                    # le llega, no en como se invoca. Sin cerrar: ver [[ATOM Organizer]].
                    cwd=os.path.dirname(dji_utility) or None,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW: sin parpadeo de consola
                )
            except FileNotFoundError:
                # El .exe no está donde se espera: instalación incompleta. Desde la
                # unificación del SDK solo hay UNA ruta posible, así que no puede ser
                # un selector de dron apuntando a una carpeta inexistente.
                self.organizer_logger.logger.error(
                    f"No se encuentra el conversor DJI en {dji_utility}. La conversión a TIFF no puede ejecutarse.")
                progress_callback.emit(
                    f"\nERROR: No se encuentra el conversor DJI en {dji_utility}.\n")
                self._register_image_error(os.path.join(input_folder, image_name))
                return
            # El rc va con signo: sin esto el -16 del SDK salía como 4294967280.
            dji_rc = rjpeg.rc_con_signo(proc.returncode)
            dji_salida = rjpeg.resumir_salida_sdk(
                (proc.stderr or proc.stdout or "").strip()[:300])
            if dji_rc != 0:
                self.organizer_logger.logger.error(
                    f"El conversor DJI ha fallado con {os.path.join(input_folder, image_name)}: "
                    f"código {dji_rc}. Salida: {dji_salida!r}")
                # En la ruta webview el organizer_logger NO tiene handler de fichero
                # (atom_core/organize.py, create_file_handler=False): lo que se escribe
                # ahí no llega a ningún sitio. El único canal que acaba en el log de
                # corrida en disco es progress_callback, así que el motivo va también
                # por aquí o se pierde igual que antes.
                progress_callback.emit(
                    "\nEl conversor DJI ha fallado con {0}: código {1}. Salida: {2}\n".format(
                        image_name, dji_rc, dji_salida or "(vacía)"))
                # El -16 (0xFFFFFFF0) es `create R-JPEG dirp handle failed`: el SDK
                # rechaza la imagen. Traducirlo a las dos únicas causas posibles
                # ahorra la ronda de preguntas que costó el caso del 28/07.
                if dji_rc == -16:
                    if _rjpeg_info["ok"]:
                        _causa = ("la imagen SÍ conserva su payload radiométrico ({0} bytes), "
                                  "así que el fichero no es el problema: mira si un antivirus la "
                                  "está bloqueando, si la ruta tiene permisos, o si falta alguna "
                                  "DLL del SDK junto al conversor").format(_rjpeg_info["payload"])
                    else:
                        _causa = "la imagen llegó dañada al conversor: {0}".format(_rjpeg_info["motivo"])
                    # Sin repetir la frase del SDK: ya va en la línea de arriba.
                    progress_callback.emit(
                        "  -> El SDK de DJI ha rechazado la imagen. Diagnóstico: {0}.\n".format(_causa))
                    self.organizer_logger.logger.error(
                        "SDK DJI rechaza {0}: {1}".format(image_name, _causa))
        else:
            # En Linux no hay ejecutable dji_irp: usamos libdirp.so vía ctypes.
            # Las librerías del SDK viven junto al .exe teórico -> carpeta única del SDK.
            lib_dir = os.path.dirname(dji_utility)
            self._dji_measure_to_raw_linux(
                os.path.join(input_folder, image_name), raw_path, humidity, emissivity, lib_dir)
        try:
            img = Image.open(os.path.join(input_folder, image_name))
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error(f"ERROR: No se encuentra la imagen {os.path.join(input_folder, image_name)} para que PIL la pueda abrir.")
            progress_callback.emit(f"\nERROR: No se encuentra la imagen {os.path.join(input_folder, image_name)} para que PIL la pueda abrir.\n")
            self.organizer_logger.logger.exception(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self._register_image_error(os.path.join(input_folder, image_name))
            return 
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error("ERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(os.path.join(input_folder, image_name)))
            progress_callback.emit("\nERROR: Hay algún tipo de error con los datos de la imagen {0}.\n".format(os.path.join(input_folder, image_name)))
            self.organizer_logger.logger.exception(e.__str__)
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self._register_image_error(os.path.join(input_folder, image_name))
            return
        
        size = img.size
        img.close()
        self.organizer_logger.logger.info(f"Procesando imagen {os.path.join(input_folder, image_name)} con tamaño {size}")

        # El código de debajo sería para probar con imageio
        # imageio.imwrite(output_path, array, format='TIFF', exif=exif_data)
        # exif_data = imageio.get_exif_data(input_path)

        try:
            f = open(os.path.join(input_folder, image_name + ".raw"), "rb")
        except FileNotFoundError as file_not_found:
            # Un .raw ausente con rc == 0 es un fallo SILENCIOSO (el conversor se dio
            # por bueno sin escribir nada) y apunta a un sitio muy distinto que un
            # rc != 0. Ese matiz es lo único que hay que decir aquí: si el rc no era
            # cero, el error ya se reportó arriba con su causa, y repetir el volcado
            # del SDK solo duplicaba el muro de texto.
            if dji_rc in (None, 0):
                _diag = "" if dji_rc is None else (
                    " El conversor terminó con código 0 pero no escribió nada:"
                    " fallo silencioso, no un rechazo de la imagen.")
            else:
                _diag = " Causa ya indicada arriba (código {0}).".format(dji_rc)
            progress_callback.emit("\nNo existe el archivo {0}. Posible error del conversor DJI.{1}\n".format(os.path.join(input_folder, image_name + ".raw"), _diag))
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error(f"No existe el archivo {os.path.join(input_folder, image_name + '.raw')}. Posible error del conversor DJI.")
            self.organizer_logger.logger.exception(file_not_found.__str__)
            self.organizer_logger.logger.exception(file_not_found)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            # Se registra la IMAGEN, no el .raw: el .raw es un intermedio que nunca
            # llegó a existir, y ponerlo en «Imágenes con error» mandaba al usuario a
            # buscar un fichero inexistente en vez de a la térmica que falló.
            self._register_image_error(os.path.join(input_folder, image_name))
            return

        data = f.read()

        # self.organizer_logger.logger.info("The length of the data is {0}".format(len(data)))

        # Hay imágenes térmicas de mayor tamaño, pero el sensor es igual de 640x512, por lo que el reshape tenemos que hacerlo del mismo modo. De hecho, podríamos usar siempre la línea "arr = arr.reshape(512, 640)", pero por ahora vamos a distinguir ambos casos.
        # El .raw que escribe el SDK (--measurefmt float32) es un array plano de float32
        # little-endian: np.frombuffer lo mapea sin recorrerlo. Antes esto era
        # `np.array(struct.unpack("<N>f", data))`, que materializaba una tupla de 327.680
        # objetos float de Python por imagen -- 20,5 ms de intérprete RETENIENDO EL GIL,
        # que era lo que impedía que el pool de conversión escalase con los hilos.
        # Medido: 20,46 -> 0,16 ms por imagen (128x), con el .astype ya incluido.
        # El .astype(np.float64) reproduce el tipo que daba struct.unpack (floats de
        # Python, doble precisión). NO es lo que fija el formato del .tiff: PIL guarda
        # en modo 'F', que es float32, así que el fichero entregado era y sigue siendo
        # float32 -- verificado por sha256 en el A/B de 60 térmicas reales. Lo que
        # preserva es la ARITMÉTICA intermedia: los umbrales y la normalización de la
        # escala de grises operaban en float64. Hace además el array escribible, que
        # np.frombuffer por sí solo no da (devuelve una vista de solo lectura).
        arr = np.frombuffer(data, dtype="<f4").astype(np.float64)
        if size == (1280, 1024):
            arr = arr.reshape(512, 640)
        else:
            arr = arr.reshape(size[1], size[0])

        # self.organizer_logger.logger.info("The size of the array is {0}".format(arr.size))
        # self.organizer_logger.logger.info("The shape of the array is {0}".format(arr.shape))
        # self.organizer_logger.logger.info("The max of the array is {0}".format(arr.max()))
        # self.organizer_logger.logger.info("The min of the array is {0}".format(arr.min()))

        array_to_normalize = arr.copy()
        if not auto_temp:
            array_to_normalize[array_to_normalize > up_threshold_temperature] = up_threshold_temperature # replace all elements greater than up_threshold_temperature with up_threshold_temperature.
            array_to_normalize[array_to_normalize < low_threshold_temperature] = low_threshold_temperature # replace all elements less than low_threshold_temperature with up_threshold_temperature.
    
        degree = self.degree_de_giro(rotate_90, rotate_minus_90, auto_rotate, input_folder, progress_callback)
        arr, array_to_normalize = self.rotar_arrays_termicos(arr, array_to_normalize, degree)

        if generate_gray_scale_images:
            normalizedData = (array_to_normalize-np.min(array_to_normalize))/(np.max(array_to_normalize)-np.min(array_to_normalize))*255 # Normalizamos datos para grabar la imagen en escala de grises. Da igual si con auto_temp o no, ya que se ajusta a los datos del array.
            normalizedData = normalizedData.astype(np.uint8)
            im_normalized = Image.fromarray(normalizedData, 'L')
            # La carpeta ya se pre-crea en convert_dji_images_to_tif, antes del pool.
            # Llamar aquí a prepare_output_folder repetía un os.listdir() de la carpeta
            # del vuelo -- con sus 1300+ ficheros dentro -- una vez POR IMAGEN, para
            # comprobar algo que ya es cierto. Además era la llamada no thread-safe que
            # obligó a pre-crearlas.
            im_normalized.save(os.path.join(output_folder,"Escala_de_grises", image_name), format='JPEG')

        if generate_colormap_images:
            rgb_colormap = apply_thermal_colormap(array_to_normalize, low_threshold_temperature, up_threshold_temperature)
            Image.fromarray(rgb_colormap, 'RGB').save(os.path.join(output_folder, "Color_gradiente", image_name), format='JPEG')

        im = Image.fromarray(arr)
        # Buscar tiffinfo como parámetro para save.
        im.save(os.path.join(output_folder, image_name.removesuffix(".JPG") + ".tiff"), format='TIFF')
        f.close()
        im.close()
        # os.remove(os.path.join(input_folder, image_name + ".raw"))
        # Intentamos eliminar el .raw de forma segura. En Windows puede dar PermissionError si
        # otro proceso aún mantiene el fichero abierto, así que reintentamos varias veces.
        self._safe_remove(os.path.join(input_folder, image_name + ".raw"), progress_callback)
        src_exif = os.path.join(input_folder, image_name)
        dst_exif = os.path.join(output_folder, image_name.removesuffix(".JPG") + ".tiff")
        if defer_exif:
            return (src_exif, dst_exif)
        if _is_windows():
            subproceso_exiftool = '"{0}" -tagsfromfile "{1}" "{2}" -overwrite_original_in_place'.format(exiftool_exe, src_exif, dst_exif)
            subprocess.run(subproceso_exiftool)
        else:
            subprocess.run([external_tools.resolve_tool("exiftool"), "-tagsfromfile", src_exif, dst_exif, "-overwrite_original_in_place"])

    def _safe_remove(self, path: str, progress_callback=None, attempts: int = 5, delay: float = 0.5):
        """
        Elimina un fichero intentando varios reintentos si hay PermissionError u OSError.
        Emite un mensaje por progress_callback.
        """
        for i in range(1, attempts + 1):
            try:
                if os.path.exists(path):
                    os.remove(path)
                return
            except PermissionError as pe:
                self.organizer_logger.logger.warning("PermissionError al eliminar %s (intento %d/%d): %s", path, i, attempts, pe)
                self.organizer_logger.logger.exception(pe)
                if progress_callback is not None:
                    try:
                        progress_callback.emit("\nAviso: no se pudo eliminar {0} (intento {1}/{2}). Reintentando...\n".format(path, i, attempts))
                    except Exception:
                        pass
                # Forzar recogida de basura y esperar antes de reintentar
                try:
                    gc.collect()
                except Exception:
                    pass
                time.sleep(delay)
                delay *= 1.5
            except OSError as oe:
                self.organizer_logger.logger.warning("OSError al eliminar %s (intento %d/%d): %s", path, i, attempts, oe)
                self.organizer_logger.logger.exception(oe)
                if progress_callback is not None:
                    try:
                        progress_callback.emit("\nAviso: error al eliminar {0} (intento {1}/{2}). Reintentando...\n".format(path, i, attempts))
                    except Exception:
                        pass
                time.sleep(delay)
                delay *= 1.5
        # Si llegamos aquí no se pudo eliminar
        self.organizer_logger.logger.debug("No se pudo eliminar el fichero %s tras %d intentos.", path, attempts)
        if progress_callback is not None:
            try:
                progress_callback.emit("\nError Convertir a TIF: no se pudo eliminar {0} tras {1} intentos.\n".format(path, attempts))
                self._register_image_error(path)  # thread-safe (bajo _stats_lock)
            except Exception:
                pass

"""
Clase que aglutina funciones usadas para el recorte de las imágenes RGB y asemejarse en tamaño a las imágenes térmicas.
"""
class RGBCropping:
    def __init__(self, organizer_logger: "utils.OrganizerLogger"):
        self.stop = False
        self.current_image_number = 0
        self.total_images_number = 0
        self.exif_management_obj = em.GeneralInformationFromImage(organizer_logger)
        self.utils_obj = utils.Utils(organizer_logger)
        self.organizer_logger = organizer_logger
        self.error_rgb_cropping = 0
        self.images_error_rgb_cropping = []

    def set_stop(self, stop: bool) -> None:
        """
        Función que para el proceso modificando el estado de la variable self.stop

        Arguments:
        ---------
        - stop - variable que indica si se puede llevar a cabo o no el procesado. A True se para o no arranca, y a False se lleva a cabo.
        """
        self.stop = stop
        
    def reset_variables(self) -> None:
        """
        Resetea las variables necesarias para mostrar la información correctamente en la ventana de log.

        """
        self.current_image_number = 0
        self.total_images_number = 0
        self.exif_management_obj.error_exif_data = 0
        self.exif_management_obj.images_error_exif_data.clear()
        self.error_rgb_cropping = 0
        self.images_error_rgb_cropping.clear()


    def get_summarize(self) -> dict:
        """Función que resume diferentes datos al finalizar el proceso. Devuelve un diccionario en el que cada clave es una información del proceso, junto
        con su correspondiente valor, de modo que se pueda mostrar en la ventana del log al finalizar el proceso."""
        summarize_dict= { "Número total de imágenes": self.current_image_number}
        error = False
        if self.total_images_number != self.current_image_number:
            error = True
            self.organizer_logger.logger.info(f"Número total de imágenes en gen_struct_folder: {self.total_images_number}")
            self.organizer_logger.logger.info(f"Número final de imágenes en gen_struct_folder: {self.current_image_number}")
            summarize_dict["Error imágenes"] = f"No hay correspondencia entre número inicial {self.total_images_number} y final de imágenes {self.current_image_number}."
            
        if self.error_rgb_cropping > 0:
            error = True
            summarize_dict["Error en RGB cropping"] = "Ha habido {0} errores en el cropping.".format(self.error_rgb_cropping)
            summarize_dict["Imágenes con error"] = self.images_error_rgb_cropping
        
        if self.exif_management_obj.error_exif_data > 0:
            error = True
            summarize_dict["Error en los metadatos"] = "Ha habido {0} errores en los metadatos.".format(self.exif_management_obj.error_exif_data)
            summarize_dict["Imágenes con error"] = self.exif_management_obj.images_error_exif_data
        
        if not error:
            summarize_dict["Sin Errores"] = "Sin errores durante el proceso"
        else:
            summarize_dict["ERROR"] = "HAN EXISTIDO ERRORES"
        return summarize_dict
    
    def checking_results_rgb_cropping(self, input_folder: str, progress_callback, progress_summarize, only_pb: list[str] | None = None) -> dict:
        """
        Comprueba que, para cada subcarpeta dentro de las carpetas que empiezan por 'PB'
        en input_folder/RGB, el número de imágenes con la palabra CROP en el nombre coincide
        con el número de imágenes que no la contienen.

        Arguments:
        ---------
        - input_folder - Carpeta raíz que contiene la subcarpeta RGB.

        Returns:
        --------
        Diccionario con la ruta de cada subcarpeta analizada como clave y un dict con:
            - "crop": número de imágenes con CROP en el nombre.
            - "non_crop": número de imágenes sin CROP en el nombre.
            - "match": True si ambos valores son iguales, False en caso contrario.
        """
        results = {}

        if not os.path.exists(input_folder):
            self.organizer_logger.logger.warning(f"La carpeta RGB no existe en: {input_folder}")
            return results

        try:
            pb_folders = sorted([
                d for d in os.listdir(input_folder)
                if d.startswith("PB") and os.path.isdir(os.path.join(input_folder, d))
            ])
        except Exception as e:
            self.organizer_logger.logger.error(f"Error al listar la carpeta RGB '{input_folder}': {e}")
            return results

        # Igual que en checking_convert_to_tif: con reparto, cada tarea verifica
        # solo los vuelos que ha recortado ella.
        seleccion = None if only_pb is None else sharding.indice_seleccion(only_pb)
        if seleccion is not None:
            pb_folders = [d for d in pb_folders if d in seleccion]

        if not pb_folders:
            self.organizer_logger.logger.info(f"No se encontraron carpetas PB en: {input_folder}")
            return results

        errors = []
        for pb_folder in pb_folders:
            pb_folder_path = os.path.join(input_folder, pb_folder)
            try:
                subfolders = sorted([
                    d for d in os.listdir(pb_folder_path)
                    if os.path.isdir(os.path.join(pb_folder_path, d))
                ])
            except Exception as e:
                self.organizer_logger.logger.error(f"Error al listar '{pb_folder_path}': {e}")
                continue

            for subfolder in subfolders:
                if seleccion is not None and not sharding.seleccion_incluye(seleccion, pb_folder, subfolder):
                    continue
                subfolder_path = os.path.join(pb_folder_path, subfolder)
                try:
                    all_images = self.utils_obj.get_images_from_dir(subfolder_path)
                except Exception as e:
                    self.organizer_logger.logger.error(f"Error al obtener imágenes de '{subfolder_path}': {e}")
                    continue
                
                if len(all_images) == 0:
                    continue

                crop_images = [img for img in all_images if "CROP" in img]
                non_crop_images = [img for img in all_images if "CROP" not in img]
                n_crop = len(crop_images)
                n_non_crop = len(non_crop_images)
                match = n_crop == n_non_crop

                results[subfolder_path] = {
                    "crop": n_crop,
                    "non_crop": n_non_crop,
                    "match": match
                }

                if match:
                    self.organizer_logger.logger.info(
                        f"OK - {subfolder_path}: {n_non_crop} imágenes originales, {n_crop} recortadas."
                    )
                    progress_callback.emit(f"\nOK - {subfolder_path}: {n_non_crop} imágenes originales, {n_crop} recortadas.\n")
                else:
                    self.organizer_logger.logger.warning(
                        f"ERROR - {subfolder_path}: {n_non_crop} imágenes originales, {n_crop} recortadas. No coinciden."
                    )
                    self.error_rgb_cropping += 1
                    self.images_error_rgb_cropping.append(f"ERROR - {subfolder_path}: {n_non_crop} imágenes originales, {n_crop} recortadas. No coinciden.") 
                    errors.append(subfolder_path)

        if errors:
            self.organizer_logger.logger.error(
                f"Se encontraron {len(errors)} carpeta(s) con diferencias entre imágenes CROP y originales: {errors}"
            )
        else:
            self.organizer_logger.logger.info("Verificación completada sin errores.")
            progress_summarize.emit("Verificación completada sin errores.\n")
            progress_callback.emit("Verificación completada sin errores.\n")

        return results
    
    def iterate_folders_for_rgb_cropping(self, input_folder: str, progress_callback, progress_bar, percentage_cropping_dict, percentage_cropping_auto:bool = True, percentage_cropping_manual: int = 0) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta lleva a cabo la función crop_centered_images.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - Carpeta de entrada
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - percentage_cropping_dict - Diccionario con los modelos y sus respectivos porcentajes.
        - percentage_cropping_auto - Booleano que indica si el porcentaje se obtiene de la información exif de la imagen (True) o del valor proveniente de la interfaz (False).
        - percentage_cropping_manual - Valor entero con el porcentaje de recorte en el caso de que el recorte sea manual (Parámetro pecentage_cropping_auto a False)
        """
        if os.path.basename(input_folder)== "TERMICA": # Evito realizar el proceso dentro de esta carpeta, pues dará error.
            return
        
        self.crop_centered_images(input_folder, progress_callback, progress_bar, percentage_cropping_dict, percentage_cropping_auto, percentage_cropping_manual)
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.iterate_folders_for_rgb_cropping(os.path.join(input_folder,dir), progress_callback, progress_bar, percentage_cropping_dict, percentage_cropping_auto, percentage_cropping_manual)

    def crop_centered_images(self,input_folder: str, progress_callback, progress_bar, percentage_cropping_dict, percentage_cropping_auto:bool = True, percentage_cropping_manual: int = 0) -> None:
        """
        Función que recorre todas las imágenes JPG que se encuentren an la carpeta de entrada input_folder y llama después a la función crop_centered_image para aplicar el recorte
        
        Arguments:
        ---------
        - input_folder - Carpeta de entrada
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - percentage_cropping_dict - Diccionario con los modelos y sus respectivos porcentajes.
        - percentage_cropping_auto - Booleano que indica si el porcentaje se obtiene de la información exif de la imagen (True) o del valor proveniente de la interfaz (False).
        - percentage_cropping_manual - Valor entero con el porcentaje de recorte en el caso de que el recorte sea manual (Parámetro pecentage_cropping_auto a False)
        
        """
        images = self.utils_obj.get_images_from_dir(input_folder, ["_CROP"])  # excluir _CROP: el recorte nunca debe re-procesar sus propias salidas (evita _CROP_CROP y "image file is truncated" si el dir de salida venía sucio). Igual que renombrado (1058/1213).
        if(len(images) > 0):
            progress_callback.emit("\nAnalizando directorio: " + input_folder + "\n")
            progress_callback.emit("Procesando y recortando {0} imágenes".format(len(images)) + "\n") # Se envía información al iniciar el procesado de un directorio. Si no hay imágenes

            self.organizer_logger.logger.info(f"Analizando directorio: {input_folder}")
            self.organizer_logger.logger.info(f"Procesando y recortando {len(images)} imágenes")

        if not self.stop:  # Se comprueba que no se quiere parar el proceso desde la ventana del log antes de lanzar el batch completo.
            def _advance_progress():
                self.current_image_number += 1
                p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                # y la cantidad actual de imágenes procesadas.
                progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.
                progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.

            valid_items = []  # (image, crop_centered_pct fraccional 0-1) de las imágenes con porcentaje resuelto correctamente.
            for image in images:
                if percentage_cropping_auto:
                    model = self.exif_management_obj.get_model(os.path.join(input_folder, image), progress_callback)
                    if model is None:
                        _advance_progress()
                        self.error_rgb_cropping += 1
                        self.images_error_rgb_cropping.append(os.path.join(input_folder, image))
                        continue
                    final_percentage = self.get_percentage_by_model(model.strip('\x00'), percentage_cropping_dict)
                else:
                    final_percentage = int(percentage_cropping_manual)
                valid_items.append((image, final_percentage / 100))

            def _worker_args_fn(item):
                image, pct = item
                file_splitted = os.path.splitext(os.path.join(input_folder, image))
                output_path = file_splitted[0] + "_CROP" + file_splitted[1]
                return (
                    os.path.join(input_folder, image),
                    ImageProcessConfig(output_path=output_path, crop_centered_pct=pct),
                )

            def _on_progress(_pct):
                _advance_progress()

            result = utils.run_batch(valid_items, process_one_image, _worker_args_fn, on_progress=_on_progress)
            for (image, _pct), error_str in result["errors"]:
                image_full_path = os.path.join(input_folder, image)
                self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
                self.organizer_logger.logger.error(f"ERROR: No se ha podido recortar la imagen {image_full_path}: {error_str}")
                progress_callback.emit(f"ERROR: No se ha podido recortar la imagen {image_full_path}: {error_str}")
                self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
                self.error_rgb_cropping += 1
                self.images_error_rgb_cropping.append(image_full_path)

    def crop_centered_image(self, input_folder: str, image_name: str, percentage_cropping_dict, percentage_cropping_auto:bool = True, percentage_cropping_manual: int = 0, progress_callback = None) -> Image.Image:
        """
        Recorta una imagen de forma centrada con el tamaño especificado.
        
        El centro del recorte coincide con el centro de la imagen original.
    
        Arguments:
        ---------
        - input_folder - Carpeta de entrada
        - image_name - El nombre de la imagen a recortar.
        - percentage_cropping_dict - Diccionario con los modelos y sus respectivos porcentajes.
        - percentage_cropping_auto - Booleano que indica si el porcentaje se obtiene de la información exif de la imagen (True) o del valor proveniente de la interfaz (False).
        - percentage_cropping_manual - Valor entero con el porcentaje de recorte en el caso de que el recorte sea manual (Parámetro pecentage_cropping_auto a False)
        ---------
        
        Returns:
            PIL.Image.Image: Imagen recortada con el tamaño especificado
        
        Raises:
            ValueError: Si el tamaño del recorte es mayor que la imagen original
        """
        # image = Image.open(os.path.join(input_folder, image_name))
        image_full_path = os.path.join(input_folder, image_name)
        try:
            image = Image.open(image_full_path)
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error(f"ERROR: No se encuentra la imagen {image_full_path} para que PIL la pueda abrir.")
            progress_callback.emit(f"ERROR: No se encuentra la imagen {image_full_path} para que PIL la pueda abrir.")
            self.organizer_logger.logger.exception(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_rgb_cropping += 1
            self.images_error_rgb_cropping.append(image_full_path) 
            return None 
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error("ERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(image_full_path))
            progress_callback.emit("ERROR: Hay algún tipo de error con los datos de la imagen {0}.".format(image_full_path))
            self.organizer_logger.logger.exception(e.__str__)
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_rgb_cropping += 1
            self.images_error_rgb_cropping.append(image_full_path) 
            return None
        
        try:
            # Obtener dimensiones de la imagen original
            orig_width, orig_height = image.size
            final_percentage = int(percentage_cropping_manual)

            # self.organizer_logger.logger.info(f"La imagen es: {image_name}")

            if percentage_cropping_auto:
                model = self.exif_management_obj.get_model(image_full_path, progress_callback)
                if model is None:
                    return None
                # self.organizer_logger.logger.info(f"El modelo es: {model}")
                final_percentage = self.get_percentage_by_model(model.strip('\x00'), percentage_cropping_dict)

            # self.organizer_logger.logger.info(f"El porcentaje es: {final_percentage}")
            width = orig_width * (final_percentage/100)
            height = width/1.25 # EL valor de 1.25 es la proporción de la imagen térmica que es 640x512.

            # self.organizer_logger.logger.info(f"El tamaño aplicado el porcentaje es: {width}x{height}")

            # Redondear al número par más próximo
            width = int(width)
            height = int(height)
            width = width if width % 2 == 0 else width + 1
            height = height if height % 2 == 0 else height + 1
            # self.organizer_logger.logger.info(f"El tamaño final es: {width}x{height}")

            # Validar que el recorte no sea mayor que la imagen original
            if width > orig_width or height > orig_height:
                raise ValueError(
                    f"El tamaño del recorte ({width}x{height}) no puede ser mayor "
                    f"que la imagen original ({orig_width}x{orig_height})"
                )

            # Calcular el centro de la imagen original
            center_x = orig_width / 2
            center_y = orig_height / 2

            # Calcular las coordenadas del recorte centrado
            left = int(center_x - width / 2)
            top = int(center_y - height / 2)
            right = int(center_x + width / 2)
            bottom = int(center_y + height / 2)

            # Realizar el recorte
            cropped_image = image.crop((left, top, right, bottom))
            file_splitted = os.path.splitext(image_full_path)
            cropped_image.save((file_splitted[0] + "_CROP" + file_splitted[1]))

            # self.organizer_logger.logger.info(self.exif_management_obj.get_all_exif_data(image_full_path))
            # self.organizer_logger.logger.info(self.exif_management_obj.use_exifread(image_full_path))

            return cropped_image
        finally:
            image.close()

    def get_percentage_by_model(self, model:str, percentage_cropping_dict:dict):
        """
        Función que devuelve el porcentaje del modelo almacenado en el diccionario.

        Arguments:
        ---------
        - model: Description
        - percentage_cropping_dict: Description
        """
        return percentage_cropping_dict[model]
        

class RGBProcessing:
    """
    Clase que aglutina las funciones centradas en el procesamiento de imágenes RGB de los equipos de AEROTOOLS.

    Methods
    -------
    
    """
    
    def __init__(self, organizer_logger: "utils.OrganizerLogger") -> None:
        self.gen_struct_folder_obj = GenStructFolder(organizer_logger)
        self.compress_image_obj = CompressImage(organizer_logger)
        self.exif_management_obj = em.GeneralInformationFromImage(organizer_logger)
        self.utils_obj = utils.Utils(organizer_logger)
        self.stop = False
        self.current_image_number = 0
        self.total_images_number = 0
        self.error_flights_for_postprocessing = { }
        self.organizer_logger = organizer_logger

    def set_stop(self, stop: bool):
        """
        Función que para el proceso modificando el estado de la variable self.stop

        Arguments:
        ---------
        - stop - variable que indica si se puede llevar a cabo o no el procesado. A True se para o no arranca, y a False se lleva a cabo.
        """
        self.stop = stop

    def reset_variables(self, main_process = True, progress_callback = None):
        """
        Resetea las variables necesarias para mostrar la información correctamente en la ventana de log.

        """
        self.current_image_number = 0
        self.total_images_number = 0
        self.compress_image_obj.error_compress = 0
        if not main_process:
            progress_callback.emit("\nSUBPROCESO: RENOMBRAR LOGS, GEOETIQUETAR Y CREACIÓN DEL LOCATION.CSV\n")  # Enviamos el texto para indicar que arranca el proceso de extracción, pero que no es el proceso principal, siendo posterior.
            

    def get_summarize(self) -> dict:
        """Función que resume diferentes datos al finalizar el proceso. Devuelve un diccionario en el que cada clave es una información del proceso, junto
        con su correspondiente valor, de modo que se pueda mostrar en la ventana del log al finalizar el proceso."""
        summarize_dict= { "Número total de imágenes": self.current_image_number}
        error = False
        if self.total_images_number != self.current_image_number:
            error = True
            self.organizer_logger.logger.info(f"Número total de imágenes en gen_struct_folder: {self.total_images_number}")
            self.organizer_logger.logger.info(f"Número final de imágenes en gen_struct_folder: {self.current_image_number}")
            summarize_dict["Error imágenes"] = f"No hay correspondencia entre número inicial {self.total_images_number} y final de imágenes {self.current_image_number}."
            
        if self.compress_image_obj.error_compress > 0:
            error = True
            summarize_dict["Error en compresión"] = "Ha habido {0} errores en la compresión.".format(self.compress_image_obj.error_compress)
            summarize_dict["Imágenes con error"] = self.compress_image_obj.images_error_compress

        if not error:
            summarize_dict["Sin Errores"] = "Sin errores durante el proceso"
        else:
            summarize_dict["ERROR"] = "HAN EXISTIDO ERRORES"
        return summarize_dict
        
    def iterate_folders(self, input_folder: str, output_folder: str, compress_checked: bool, quality: int, progress_callback,
                        rename: bool, progress_bar) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta lleva a cabo la función split_images.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - output_folder - carpeta de salida
        - compress_checked - si es True se lleva a cabo la compresión. En caso contrario, no.
        - quality - calidad de la compresión
        - progress_callback - se devuelve información al hilo principal
        - rename - si es True se lleva a cabo el renombrado de las imágenes. En caso contrario, no.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """ 
        
        self.organizer_logger.logger.info("-------------------------------------------------------------")
        self.organizer_logger.logger.info("Carpeta de entrada: " + input_folder)
        self.organizer_logger.logger.info("Carpeta de salida: " + output_folder)

        # print("Directorio de entrada: ", input_folder)
        # print("Directorio de salida: ", output_folder)
        self.move_images(input_folder, output_folder, compress_checked, quality, progress_callback, rename, progress_bar)
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.iterate_folders(os.path.join(input_folder,dir), output_folder, compress_checked, quality, progress_callback, rename, progress_bar)

    def move_images(self, input_folder: str, output_folder: str, compress_checked: bool, quality: int, progress_callback, rename: bool, progress_bar) -> None:
        """
        Función que obtiene las imágenes existentes en el directorio de entrada, recorre dichas imágenes en un bucle y procesa cada una de las imágenes
        en una función independiente, copiándolas (y/o comprimiéndolas y/o renombrándolas) si se ha marcado la opción correspondiente) en el directorio de salida.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - mode - modo de separación. Puede ser por terminación o por tamaño del archivo. Si es True es por tamaño, si es False, por terminación.
        - min_size - Tamaño mínimo.
        - thermal_sufix - terminación de las imágnes térmicas.
        - rgb_sufix - terminación de las imágnes RGB.
        - compress_checked - si es True se lleva a cabo la compresión. En caso contrario, no.
        - quality - calidad de la compresión.
        - progress_callback - se devuelve información al hilo principal.
        - rename - si es True se lleva a cabo el renombrado de las imágenes. En caso contrario, no.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """               
        images = self.utils_obj.get_images_from_dir(input_folder)
        if len(images) != 0:
            progress_callback.emit("\nProcesando {0} imágenes en el directorio {1}".format(len(images), input_folder) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.

        if not self.stop and len(images) != 0:  # Se comprueba que no se quiere parar el proceso desde la ventana del log antes de lanzar el batch completo.
            new_names = {}
            for image in images:
                self.organizer_logger.logger.debug("Nombre de la imagen: " + image)
                self.organizer_logger.logger.debug("Estado del renombre: " + str(rename))
                if rename:
                    # Obtenemos el nuevo nombre en el caso de querer renombrar el archivo
                    new_name = self.exif_management_obj.fechaHora_DJI(os.path.join(input_folder, image), progress_callback)
                    if new_name is not None:
                        new_name = new_name + "_" + image
                    else:
                        new_name = ""
                else:
                    # Si no lo queremos, se enviará un string vacío.
                    new_name = ""
                self.organizer_logger.logger.debug("Nuevo nombre de imagen: " + new_name)
                new_names[image] = new_name

            def _advance_progress():
                self.current_image_number += 1
                p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                # y la cantidad actual de imágenes procesadas.
                progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.
                progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.

            if compress_checked:
                self.organizer_logger.logger.debug("Comprimiendo imagen RGB")

                def _worker_args_fn(image):
                    output_name = new_names[image] if new_names[image] != "" else image
                    return (
                        os.path.join(input_folder, image),
                        ImageProcessConfig(output_path=os.path.join(output_folder, "RGB", output_name), quality=quality),
                    )

                result = utils.run_batch(images, process_one_image, _worker_args_fn, on_progress=lambda _pct: _advance_progress())
                for image, error_str in result["errors"]:
                    output_name = new_names[image] if new_names[image] != "" else image
                    image_output_path = os.path.join(output_folder, "RGB", output_name)
                    self.organizer_logger.logger.warning(f"ERROR: No se ha podido comprimir la imagen RGB {image_output_path}: {error_str}")
                    self.compress_image_obj.error_compress += 1
                    self.compress_image_obj.images_error_compress.append(image_output_path)
            else:
                self.organizer_logger.logger.debug("Copiando imagen RGB")
                for image in images:
                    output_name = new_names[image] if new_names[image] != "" else image
                    shutil.copy2(os.path.join(input_folder, image), os.path.join(output_folder, "RGB", output_name))
                    _advance_progress()


    def move_image(self, image: str, input_folder: str, output_folder: str, compress_checked: bool, quality: int, new_name: str, progress_callback):
        """
        Función que copia la imagen de entrada a la carpeta correspondiente y si es RGB y se quiere comprimir, se comprime con la calidad elegida en el GUI.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - output_folder - carpeta de salida.
        - mode_size - modo de separación. Puede ser por terminación o por tamaño del archivo. Si es True es por tamaño, si es False, por terminación.
        - min_size - Tamaño mínimo.
        - thermal_sufix - terminación de las imágnes térmicas.
        - rgb_sufix - terminación de las imágnes RGB.
        - compress_checked - si es True se lleva a cabo la compresión. En caso contrario, no.
        - quality - calidad de la compresión.
        - new_name - el nuevo nombre que se le dará a la imagen. Si es "", se asume que no se renombrará.
        - rename - si es True se lleva a cabo el renombrado de las imágenes. En caso contrario, no. No se usa actualmente.
        """ 
        # Siempre enviamos new_name, aunque sea vacío. La función se encargará de tratarlo de forma automática.
        if compress_checked:
            self.organizer_logger.logger.debug("Comprimiendo imagen RGB")
            # En la siguiente línea comprimimos la imagen y le decimos que provienen de dispositivos AEROTOOLS, con lo que no obtenemos datos XMP
            # ni los guardamos después de comprimnir.
            self.compress_image_obj.compress_image(image, input_folder,os.path.join(output_folder,"RGB"), quality, new_name, progress_callback=progress_callback, aerotools_devices=True)
        else:
            self.organizer_logger.logger.debug("Copiando imagen RGB")
            shutil.copy2(os.path.join(input_folder, image),os.path.join(os.path.join(output_folder,"RGB"), new_name))                
    
    def rename_and_move_logs(self, input_folder: str, path_estadillo: str, output_folder: str, seconds_range: float, progress_callback, progress_bar):
        """
        Función que obtiene los logs de la carpeta de entrada (extensión *.log), detecta los que corresponden con una línea
        específica del estadillo (PB y Vuelo) y los copia en la misma carpeta de entrada de los log, modificando el nombre del log,
        añadiendo el nombre de la carpeta PBX_VX al nombre original.
        Anteriormente, el log era copiado a la carpeta de vuelo PBX_VX que corresponde a ese log, pero era algo erróneo.
        Si las carpetas de vuelo no están creadas, se crearán dentro de la función.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - path_estadillo - el path del estadillo relacionado con los logs adquiridos durante los vuelos.
        - output_folder - la carpeta de salida con la carpeta RGB ya creada.
        - seconds_range - Variable para controlar el margen de segundos entre la hora del log y la hora de inicio del vuelo.
        """
        log_files = self.utils_obj.get_logs_from_dir(input_folder) # Obtiene los archivos *.log que existen en el directorio
        estadillo = pd.read_csv(path_estadillo, sep=';') # Lee el CSV del path_estadillo
        nombres_columnas = self.utils_obj.get_nombres_columnas(list(estadillo.columns.values))

        if estadillo.shape[0] != len(log_files):
            progress_callback.emit("\nWARNING: El número de vuelos en el estadillo ({0}) no concuerda con el número de logs existentes ({1}).\n".format(estadillo.shape[0], len(log_files)))
            
        for log_file in log_files:
            if not self.stop and "PB" not in log_file:
                date_log = log_file.split(' ')[0].replace('-',':') 
                if date_log == estadillo[nombres_columnas['Fecha']][0]:
                    numeroVuelos = estadillo.shape[0]
                    vuelo_sin_detectar = True  # Utilizado para detectar si hay algún log cuya diferencia en el tiempo con el vuelo es demasiado grande
                    for vuelo in range(numeroVuelos):
                        hora_inicio_vuelo = datetime.datetime.strptime(estadillo[nombres_columnas['Hora_de_inicio']].iloc[vuelo], '%H:%M:%S') #Hora de inicio de cada línea del estadillo
                        hora_log = datetime.datetime.strptime(log_file.split(' ')[1].replace('-',':').split('.')[0], '%H:%M:%S')
                    
                        rango_tiempos_segundos = (hora_log-hora_inicio_vuelo).total_seconds()
                        
                        if abs(rango_tiempos_segundos) < seconds_range:
                            vuelo_sin_detectar = False
                            self.current_image_number += 1
                            p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                            # y la cantidad actual de imágenes procesadas.
                            progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
                            progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.   
                            nombre_archivo_log_pre = os.path.join(input_folder,log_file)

                            nombreCarpeta_PB = 'PB'+str(estadillo[nombres_columnas['PB']].iloc[vuelo])
                            #-- Carpeta RGB--
                            # Creamos el path a la carpeta del PB dentro de la carpeta RGB
                            pathRGB_PB = os.path.join(os.path.join(output_folder,"RGB"),nombreCarpeta_PB)

                            # Si no existe todavia la carpeta de RGB, la creamos
                            os.makedirs(pathRGB_PB, exist_ok=True)
                            
                            nombreCarpeta_PB_vuelo = nombreCarpeta_PB+'_V'+str(estadillo[nombres_columnas['Vuelo']].iloc[vuelo])

                            # Creamos el path a la carpeta del vuelo dentro de la carpeta PB
                            pathRGB_PB_vuelo = os.path.join(pathRGB_PB,nombreCarpeta_PB_vuelo)

                            # Si no existe todavia la carpeta del vuelo, la creamos
                            os.makedirs(pathRGB_PB_vuelo, exist_ok=True)

                            # nombre_archivo_log_post = os.path.join(pathRGB_PB_vuelo,\
                            #     log_file.split('.')[0]+'_'+'PB'+str(estadillo[nombres_columnas['PB']].iloc[vuelo])+\
                            #         '_'+'V'+str(estadillo[nombres_columnas['Vuelo']].iloc[vuelo])+'.'+log_file.split('.')[1])
                            
                            # shutil.copy(nombre_archivo_log_pre, nombre_archivo_log_post)  # Copia el archivo log en la carpeta de vuelo correspondiente, cambiando el nombre,
                            # añadiendo el nombre de la carpeta de vuelo.

                            nombre_archivo_log_post = os.path.join(input_folder,\
                                log_file.split('.')[0]+'_'+'PB'+str(estadillo[nombres_columnas['PB']].iloc[vuelo])+\
                                    '_'+'V'+str(estadillo[nombres_columnas['Vuelo']].iloc[vuelo])+'.'+log_file.split('.')[1])
                            
                            shutil.copy(nombre_archivo_log_pre, nombre_archivo_log_post) 
                    if vuelo_sin_detectar:
                        progress_callback.emit("\nERROR: El log {0} tiene una diferencia de tiempo con la hora de inicio de vuelo grande de más de {1} segundos".format(log_file, seconds_range) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
       
    
    def geo_etiquetar(self, input_folder: str, logs_input_folder: str, progress_callback, progress_bar, manual_log_file: str = None, manual_geotagging: bool = False) -> None:
        """
        Función que geoetiqueta las imágenes existentes en la carpeta de entrada y que están ya almacenadas en su correspondiente
        carpeta de vuelo con el formato PBX_VX.
        También genera el archivo location.csv para dicha carpeta de vuelo.
        
        Además, la carpeta tiene que tener ya el archivo log correspondiente a ese vuelo, de modo que se puedan obtener los datos de GPS.

        Arguments:
        ---------
        - input_folder - carpeta de entrada que tiene la carpeta RGB ya creada con imágenes.
        - logs_input_folder - carpeta de entrada que tiene todos los logs almacenados.
        - progress_callback - se devuelve información al hilo principal.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.       
        - manual_log_file - Archivo log a leer en el caso del geoetiquetado manual. Por defecto es None.
        - manual_geotagging - Nos indica que el geoetiquetado es manual o automático. Por defecto está a False, indicando que es automático.
        """
        if "PB" and "_V" in os.path.basename(input_folder):
            self.organizer_logger.logger.info("La carpeta es {0}".format(os.path.basename(input_folder)))
            images_number = self.utils_obj.contar_imagenes_or_tmc(input_folder)
            lineas_detectadas = []

            n_logs_pbx_vx = 0  # Esta variable nos sirve para comprobar que tenemos un log para la carpeta PBX_VX o si tenemos más de uno.
            if not manual_geotagging:
                lista_logs = self.utils_obj.get_logs_from_dir(logs_input_folder)
                for log_file in lista_logs:
                    if os.path.basename(input_folder) in log_file:
                        n_logs_pbx_vx = n_logs_pbx_vx + 1  # Si solo sumamos 1, entonces tenemos un log, de lo contrario tendríamos o ninguno o más de uno, lo que indica que hay algún error en los datos
                        with open(os.path.join(logs_input_folder, log_file), 'r') as archivo:
                            for linea in archivo:
                                if linea.startswith('CAM'):
                                    lineas_detectadas.append(linea.strip()) # Añadimos a lineas_detectadas las líneas que empieza con CAM y que tiene los datos GPS
            else:
                n_logs_pbx_vx = 1  # Lo igualamos a 1, ya que es manual y solo elegiremos el correcto.
                with open(manual_log_file, 'r') as archivo:
                    for linea in archivo:
                        if linea.startswith('CAM'):
                            lineas_detectadas.append(linea.strip()) # Añadimos a lineas_detectadas las líneas que empieza con CAM y que tiene los datos GPS
            
            if n_logs_pbx_vx == 1:
                if len(lineas_detectadas) == images_number:  # Líneas que inician con CAM e imágenes concuerdan.
                    progress_callback.emit("\nProcesando {0} imágenes en el directorio {1}".format(images_number, input_folder) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
                    images = self.utils_obj.get_images_from_dir(input_folder)
                    nombresColumnas = ['Foto','Lat','Lon']
                    df = pd.DataFrame(columns=nombresColumnas)  # Creamos un dataframe para crear posteriormente un csv. Será nuestro location.csv
                    for indice, image in enumerate(images):
                        if not self.stop:
                            self.current_image_number += 1
                            p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                            # y la cantidad actual de imágenes procesadas.
                            progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
                            progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.                
                            linea_cam = lineas_detectadas[indice].split(", ")
                            lat = linea_cam[4]
                            long = linea_cam[5]
                            alt = linea_cam[6]
                            df.loc[indice] = [image, lat, long]
                            self.exif_management_obj.saving_gps_data(os.path.join(input_folder, image), [lat,long, alt])
                    df.to_csv(os.path.join(input_folder, os.path.basename(input_folder) + "_" + "location.csv"), sep = ",", header=False, index=False)                    
                else:
                    progress_callback.emit("\nERROR: En el directorio {0} no se corresponde el número de imágenes ({1}) con el número de líneas en el log ({2})".format(input_folder, images_number, len(lineas_detectadas)) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
                    # Lo que hago es introducir los datos de los errores localizados en un diccionario para posteriormente procesar cada caso.
                    if images_number > len(lineas_detectadas): # Caso mayor número de imágenes que de CAM msgs.
                        self.error_flights_for_postprocessing["more_images_" + os.path.basename(input_folder)] = os.path.basename(input_folder) + "_" + str(images_number) + "_" + str(len(lineas_detectadas))
                    else: # Caso mayor CAM msgs que de número de imágenes.
                        self.error_flights_for_postprocessing["less_images_" + os.path.basename(input_folder)] = os.path.basename(input_folder) + "_" + str(images_number) + "_" + str(len(lineas_detectadas))
                    # self.create_csv_for_comparing(lineas_detectadas,input_folder,images_number)  # Crear CSV para analizar.
            else:
                progress_callback.emit("\nERROR: El número de logs correspondiente al vuelo {0} es {1}\n".format(os.path.basename(input_folder), n_logs_pbx_vx))

        
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.geo_etiquetar(os.path.join(input_folder,dir), logs_input_folder, progress_callback, progress_bar)

    def geo_etiquetar_pbx_vx_folder_with_errors(self, input_folder: str, final_dataframe: pd.DataFrame, detected_cam_lines: list[str], less_images: bool) -> None:
        """
        Función que geoetiqueta las imágenes existentes en la carpeta de entrada y que están ya almacenadas en su correspondiente
        carpeta de vuelo con el formato PBX_VX.
        También genera el archivo location.csv para dicha carpeta de vuelo.
        
        Además, la carpeta tiene que tener ya el archivo log correspondiente a ese vuelo, de modo que se puedan obtener los datos de GPS.

        La carpeta procesada en esta función tiene la peculiaridad de que el número de imágenes y el número de líneas CAM no son iguales, por lo que 
        se ha tenido que hacer un procesado previo para poder encajar cada imagen con su línea CAM correspondiente.

        Arguments:
        ---------
        - input_folder - carpeta de entrada que tiene la carpeta RGB ya creada con imágenes y con el archivo log.
        - final_dataframe - dataframe que almacena las relaciones entre las imágenes y los CAM message.
        - detected_cam_lines - lista las líneas provenientes del log con el prefijo CAM. En el caso de que less_images esté a False, esta lista está modificada añadiendo tantas líneas
        como la diferencia con la lista de imágenes.
        - less_images - booleano que indica si hay menos imágenes que CAM msgs (valor a True) o más imágenes que CAM msgs (valor a False).
        """
        
        nombresColumnas = ['Foto','Lat','Lon']
        df = pd.DataFrame(columns=nombresColumnas)  # Creamos un dataframe para crear posteriormente un csv. Será nuestro location.csv
        
        if less_images:  # Hay menos imágenes que CAM msgs.
            images = final_dataframe["Nombre_imagen"].to_list()
            
            for indice, image in enumerate(images):  # En este caso recorremos la lista de imágenes
                if image != "None" and image != "":
                    linea_cam = detected_cam_lines[indice].split(", ")
                    lat = linea_cam[4]
                    long = linea_cam[5]
                    alt = linea_cam[6]
                    df.loc[indice] = [image, lat, long]
                    self.exif_management_obj.saving_gps_data(os.path.join(input_folder, image), [lat,long, alt])
        else:  # Hay más imágenes que CAM msgs.
            images = final_dataframe["Nombre_imagen"].to_list()
            
            for indice, line in enumerate(detected_cam_lines):  # En este caso recorremos la lista de líneas CAM.
                if line != "None" and line != "":
                    linea_cam = detected_cam_lines[indice].split(", ")
                    lat = linea_cam[4]
                    long = linea_cam[5]
                    alt = linea_cam[6]
                    df.loc[indice] = [images[indice], lat, long]
                    self.exif_management_obj.saving_gps_data(os.path.join(input_folder, images[indice]), [lat,long, alt])

        df.to_csv(os.path.join(input_folder, os.path.basename(input_folder) + "_" + "location.csv"), sep = ",", header=False, index=False)

    def calcular_fecha_resultante(self, fecha_base: datetime.datetime, semanas_transcurridas: float, milisegundos_adicionales: float) -> datetime.datetime:
        """
        Función que calcula la fecha resultante a sumarle a una fecha las semanas y milisegundos que aparecen como parámetros.

        Arguments:
        ---------
        - fecha_base - fecha a la que hay que sumar los parámetros.
        - semanas_transcurridas - semanas que hay que sumar.
        - milisegundos_adicionales - milisegundos que hay que sumar.
        """
        fecha_sumada = fecha_base + timedelta(weeks=semanas_transcurridas)
        fecha_resultante = fecha_sumada + timedelta(milliseconds=milisegundos_adicionales)
        # print("La fecha después de sumarle {0} semanas es {1}. Los milisegundos son: {2}".format(semanas_transcurridas, fecha_sumada, milisegundos_adicionales))
        return fecha_resultante
    
    def post_processing_rgb_errors(self, input_folder: str, logs_input_folder: str, path_estadillo: str, pb: int, v: int, images_quantity: int, cam_msgs_quantity: int, progress_callback, progress_bar, manual_log_file: str = None, manual_geotagging: bool = False) -> None:
        """
        Función que empieza el procesamiento de los vuelos que han tenido errores en la obtención de las imágenes RGB.
        El objetivo es geoetiquetar las imágenes que han quedado sin hacerlo.

        Arguments:
        ---------
        - input_folder - carpeta de entrada que tiene la carpeta RGB ya creada con imágenes.
        - logs_input_folder - carpeta de entrada que tiene todos los logs almacenados.
        - path_estadillo - path del estadillo relacionado con las imágenes existentes en la carpeta de entrada.
        - pb - Power Block que ha sobrevolado el dron y que queramos geoetiquetar.
        - v - Número de vuelo del PB que queramos geoetiquetar.
        - images_quantity - Número de imágenes.
        - cam_msgs_quantity - Número de CAM messages.
        - progress_callback - se devuelve información al hilo principal.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - manual_log_file - Archivo log a leer en el caso del geoetiquetado manual. Por defecto es None.
        - manual_geotagging - Nos indica que el geoetiquetado es manual o automático. Por defecto está a False, indicando que es automático.
        """
        progress_callback.emit("\n----------------------------------------------------------------------------------------------")
        progress_callback.emit("\nPOST-PROCESADO del PB {0} y vuelo {1}".format(pb, v))
        progress_callback.emit("\nCantidad de imágenes: {0}. Cantidad de líneas CAM: {1}".format(images_quantity, cam_msgs_quantity) + "\n")
        full_pbx_vx_path = self.get_pbx_vx_path(input_folder, pb, v)
        lineas_detectadas = []  # Líneas detectadas en el log que empiezan con CAM

        if not manual_geotagging:  # Si no es geoetiquetado manual. leemos el log de la carpeta de los logs
            lista_logs = self.utils_obj.get_logs_from_dir(logs_input_folder)
            name_log_file = ""
            for log_file in lista_logs:
                if os.path.basename(full_pbx_vx_path) in log_file:
                    name_log_file = log_file.removesuffix("_" + os.path.basename(full_pbx_vx_path) + ".log")
                    with open(os.path.join(logs_input_folder, log_file), 'r') as archivo:
                        for linea in archivo:
                            if linea.startswith('CAM'):
                                lineas_detectadas.append(linea.strip()) # Añadimos a lineas_detectadas las líneas que empieza con CAM y que tienes los datos GPS
        else:  # Si es geoetiquetado manual. leemos el log de elegido en la GUI
            name_log_file = os.path.basename(manual_log_file).removesuffix(".log")
            with open(manual_log_file, 'r') as archivo:
                for linea in archivo:
                    if linea.startswith('CAM'):
                        lineas_detectadas.append(linea.strip()) # Añadimos a lineas_detectadas las líneas que empieza con CAM y que tiene los datos GPS
            
        flight_number, position = self.get_flight_line(path_estadillo, pb, v)  # Obtengo el número de línea en el estadillo y si es primera, última o en el
        # medio del pb y v que estoy analizando y que tiene una diferencia entre imágenes y CAM messages.
        pb_post = ""
        v_post = ""
        pb_ant = ""
        v_ant = ""

        # Procedemos a obtener los PB y V de los vuelos anterior y/o posterior al PB y V que tienen error.
        if position == "first":
            pb_post, v_post = self.get_pbx_vx(path_estadillo, flight_number + 1)
            pb_ant = None
            v_ant = None
        elif position == "last":
            pb_ant, v_ant = self.get_pbx_vx(path_estadillo, flight_number - 1)
            pb_post = None
            v_post = None
        elif position == "mid":            
            pb_ant, v_ant = self.get_pbx_vx(path_estadillo, flight_number - 1)
            pb_post, v_post = self.get_pbx_vx(path_estadillo, flight_number + 1)
        
        media_ant = None
        # Calculamos la media del vuelo anterior en el caso que exista.
        if pb_ant is not None and v_ant is not None:
            pbx_vx_path_ant = self.get_pbx_vx_path(input_folder, pb_ant, v_ant)
            self.organizer_logger.logger.debug("El PB {0} y el vuelo anterior {1} está en el path {2}".format(pb_ant, v_ant, pbx_vx_path_ant))
            is_flight_correct = self.is_flight_correct(pbx_vx_path_ant, logs_input_folder)  # Comprobamos que el vuelo es correcto
            # TODO: Es necesario hacer un bucle para no solo ver el anterior o el posterior si no los siguientes, en el caso de que 
            # el vuelo no sea correcto.
            if is_flight_correct[0]:
                # Calculamos la media de la columna Diferencia_fecha_log_foto a partir del dataframe calculado
                media_ant = self.create_df_cam_image_data(is_flight_correct[1], pbx_vx_path_ant)[0]["Diferencia_fecha_log_foto"].mean()      
                self.organizer_logger.logger.debug("La media es {0}".format(media_ant))
            else:
                self.organizer_logger.logger.debug("No hay media en el PB {0} y V {1}".format(pb_ant, v_ant))

        # Calculamos la media del vuelo posterior en el caso que exista.
        media_post = None
        if pb_post is not None and v_post is not None:
            pbx_vx_path_post = self.get_pbx_vx_path(input_folder, pb_post, v_post)
            self.organizer_logger.logger.debug("El PB {0} y el vuelo posterior {1} está en el path {2}".format(pb_post, v_post, pbx_vx_path_post))
            is_flight_correct = self.is_flight_correct(pbx_vx_path_post, logs_input_folder)  # Comprobamos que el vuelo es correcto
            # TODO: Es necesario hacer un bucle para no solo ver el anterior o el posterior si no los siguientes, en el caso de que 
            # el vuelo no sea correcto.
            if is_flight_correct[0]:
                # Calculamos la media de la columna Diferencia_fecha_log_foto a partir del dataframe calculado
                media_post = self.create_df_cam_image_data(is_flight_correct[1], pbx_vx_path_post)[0]["Diferencia_fecha_log_foto"].mean()
                self.organizer_logger.logger.debug("La media es {0}".format(media_post))
            else:
                self.organizer_logger.logger.debug("No hay media en el PB {0} y V {1}".format(pb_post, v_post))

        final_average = self.check_averages_range_and_get_average(media_ant, media_post, 0.8)
        if final_average is not None:
            progress_callback.emit("\nLa media de la diferencia entre log y foto calculada es {0}".format(final_average) + "\n")
            self.organizer_logger.logger.debug("La media para usar sería {0}".format(final_average))

            number_of_fixed_lines = 0  # Variable que me dice el número de líneas que voy arreglando durante el procesado.
            difference_between_images_cammsgs = cam_msgs_quantity - images_quantity
            
            if difference_between_images_cammsgs > 0:  # Hay menos imágenes que CAM msgs.
                self.organizer_logger.logger.debug("Hay MENOS imágenes que CAM msgs")
                current_df = self.create_df_cam_image_data(lineas_detectadas, full_pbx_vx_path)
                self.organizer_logger.logger.debug("La media del df actual es {0}".format(current_df[1]))
                df_to_analyze = current_df[0]
                # En cada paso del siguiente while puedo estar arreglando una línea o más de una.
                while number_of_fixed_lines < difference_between_images_cammsgs:  # paramos el bucle cuando hallamos encajado todas las imágenes. Es decir, todas las líneas encajadas.
                    # df_to_analyze.to_csv(os.path.join(full_pbx_vx_path, str(number_of_fixed_lines) + "_TimeModified.csv"), sep = ",", header=True, index=False)
                    response = self.generate_df_modified(full_pbx_vx_path, df_to_analyze, final_average, images_quantity, cam_msgs_quantity, number_of_fixed_lines, lineas_detectadas, progress_callback, progress_bar)
                    if response[0] is not None:
                        number_of_fixed_lines = number_of_fixed_lines + response[0]
                        self.organizer_logger.logger.debug("Número de bucles: {0}".format(number_of_fixed_lines))
                        df_to_analyze = response[1]
                    else:
                        self.organizer_logger.logger.debug("El resto de las imágenes que faltan están todas al final")
                        progress_callback.emit("Las imágenes que faltan están todas al final" + "\n")
                        break
                # Grabamos la relación final entre las imágenes y los CAM messages en un csv
                df_to_analyze.to_csv(os.path.join(logs_input_folder, name_log_file + "_Relacion_final_imagenes_log_" + os.path.basename(full_pbx_vx_path) + ".csv"), sep = ",", header=True, index=False)

                if self.check_final_dataframe(df_to_analyze, final_average, 0.8):
                    self.organizer_logger.logger.debug("The final dataframe is ok")
                    progress_callback.emit("\nLa relación entre imágenes y líneas CAM es correcta" + "\n")
                    progress_callback.emit("\nSe inicia el geoetiquetado de las imágenes y la generación del location.csv" + "\n")
                    self.geo_etiquetar_pbx_vx_folder_with_errors(full_pbx_vx_path, df_to_analyze, lineas_detectadas, True)
                else:
                    progress_callback.emit("\nERROR: La relación entre imágenes y líneas CAM NO es correcta. No se puede geoetiquetar y generar el location.csv de manera automática" + "\n")
                    self.organizer_logger.logger.debug("ERROR: La relación entre imágenes y líneas CAM NO es correcta. No se puede geoetiquetar y generar el location.csv de manera automática")

            else:
                self.organizer_logger.logger.debug("Hay MÁS imágenes que CAM msgs")
                current_df = self.create_df_cam_image_data_more_images(lineas_detectadas, full_pbx_vx_path)
                self.organizer_logger.logger.debug("La media del df actual es {0}".format(current_df[1]))
                df_to_analyze = current_df[0]

                # En cada paso del siguiente while puedo estar arreglando una línea o más de una.
                while number_of_fixed_lines < (difference_between_images_cammsgs * -1):  # paramos el bucle cuando hallamos encajado todas las imágenes. Es decir, todas las líneas encajadas.
                    # df_to_analyze.to_csv(os.path.join(full_pbx_vx_path, str(number_of_fixed_lines) + "_TimeModified.csv"), sep = ",", header=True, index=False)
                    response = self.generate_df_modified_more_images(full_pbx_vx_path, lineas_detectadas, df_to_analyze, final_average, images_quantity, cam_msgs_quantity, number_of_fixed_lines, progress_callback, progress_bar)
                    if response[0] is not None:
                        number_of_fixed_lines = number_of_fixed_lines + response[0]
                        self.organizer_logger.logger.debug("Número de bucles: {0}".format(number_of_fixed_lines))
                        df_to_analyze = response[1]
                        lineas_detectadas = response[2]
                    else:
                        self.organizer_logger.logger.debug("El resto de las imágenes que faltan están todas al final")
                        progress_callback.emit("Las imágenes que faltan están todas al final" + "\n")
                        break
                # Grabamos la relación final entre las imágenes y los CAM messages en un csv
                df_to_analyze.to_csv(os.path.join(logs_input_folder, name_log_file + "_Relacion_final_imagenes_log_" + os.path.basename(full_pbx_vx_path) + ".csv"), sep = ",", header=True, index=False)
                
                if self.check_final_dataframe(df_to_analyze, final_average, 0.8):
                    self.organizer_logger.logger.debug("The final dataframe is ok")
                    progress_callback.emit("\nLa relación entre imágenes y líneas CAM es correcta" + "\n")
                    progress_callback.emit("\nSe inicia el geoetiquetado de las imágenes y la generación del location.csv" + "\n")
                    self.geo_etiquetar_pbx_vx_folder_with_errors(full_pbx_vx_path, df_to_analyze, lineas_detectadas, False)
                else:
                    progress_callback.emit("\nERROR: La relación entre imágenes y líneas CAM NO es correcta. No se puede geoetiquetar y generar el location.csv de manera automática" + "\n")
                    self.organizer_logger.logger.debug("ERROR: La relación entre imágenes y líneas CAM NO es correcta. No se puede geoetiquetar y generar el location.csv de manera automática")
        else:
            # TODO: Si la diferencia entre medias es mayor que la que se ha puesto como argumento en check_averages_range_and_get_average,
            # entonces tenemos que probar con la media anterior y/o después con la media posterior.
            # Solo tendríamos que probar aquí con ambas si el problema viene por la diferencia entre medias.
            progress_callback.emit("\nERROR: La diferencia en la media entre el vuelo anterior y el posterior excede de la cantidad máxima permitida.")
            progress_callback.emit("\nERROR: No se puede geoetiquetar y generar el location.csv de manera automática" + "\n")
            self.organizer_logger.logger.debug("ERROR: La diferencia en la media entre el vuelo anterior y el posterior excede de la cantidad máxima permitida.")

    def check_final_dataframe(self, final_dataframe: pd.DataFrame, average_used: float, average_max_difference: float) -> bool:
        """
        Función que comprueba si el dataframe final cumple las características necesarias para considerar que la relación entre las imágenes y las líneas CAM
        es la correcta.

        Se comprueba, en las líneas correctas, que:
        - La media de la columna "Diferencia_fecha_log_foto" es similar a la diferencia entre la media usada durante el procesado.
        - Que el valor de la columna "Tramos_diferencia_fecha_log_foto" no excede de 2.0.

        Arguments:
        ---------
        - final_dataframe - dataframe que almacena las relaciones entre las imágenes y los CAM message.
        - average_used - media de la columna "Diferencia_fecha_log_foto" calculada previamente y usada durante el procesado.
        - average_max_difference - Máxima diferencia permitida entre average_used y la media de la columna "Diferencia_fecha_log_foto" del final_dataframe.
        """
        suma_diferencia_fecha_log_foto = 0.0
        number_of_lines = 0
        for i in range(len(final_dataframe)):
            # Utilizo el valor absoluto porque en el caso de más número de imágenes la columna "Diferencia_fecha_log_foto" me da negativo en valores grandes.
            if abs(float(final_dataframe.iloc[i]['Diferencia_fecha_log_foto'])) < 500000.0:
                suma_diferencia_fecha_log_foto += float(final_dataframe.iloc[i]['Diferencia_fecha_log_foto'])
                number_of_lines += 1
            if abs(float(final_dataframe.iloc[i]['Diferencia_fecha_log_foto'])) < 500000.0 and abs(float(final_dataframe.iloc[i]['Tramos_diferencia_fecha_log_foto'])) < 500000.0 and float(final_dataframe.iloc[i]['Tramos_diferencia_fecha_log_foto']) > 2.0 and final_dataframe.iloc[i]['Nombre_imagen'] != "None" and final_dataframe.iloc[i]['Nombre_imagen'] != "":
                self.organizer_logger.logger.debug("El valor en Tramos_diferencia_fecha_log_foto es {0}".format(float(final_dataframe.iloc[i]['Tramos_diferencia_fecha_log_foto'])))                
                return False
        if abs((average_used - (suma_diferencia_fecha_log_foto/number_of_lines)) < average_max_difference):
            return True
        else:
            self.organizer_logger.logger.debug("La diferencia entre medias es {0}".format(average_used - (suma_diferencia_fecha_log_foto/number_of_lines)))
            return False
    
    def generate_df_modified(self, input_folder: str, current_dataframe: pd.DataFrame, average: float, images_quantity: int, cam_msgs_quantity: int, number_of_loops: int, lineas_detectadas: list[str], progress_callback, progress_bar) -> list[int, pd.DataFrame] | list[None,None]:
        """
        Función que realiza un doble bucle recorriendo las columnas Fecha_foto_average_added y Fecha_captura_log, con el objetivo de encajar cada una de las líneas de imagen
        con su correspondiente línea en el log, mediante la resta de cada línea de Fecha_foto_average_added con cada una de las líneas de Fecha_captura_log.

        Si las líneas ya están encajadas, seguimos en el bucle para la siguiente línea. Si no están, generamos un nuevo dataframe modificado, en el que las líneas no encajadas localizadas
        se encajan.

        Arguments:
        ---------
        - input_folder - carpeta de entrada que tiene la carpeta RGB ya creada con imágenes y con el archivo log.
        - current_dataframe - dataframe usado para realizar los cálculos en este paso del bucle.
        - average - media que se usará para sumar a la columna Fecha_foto_average_added.
        - images_quantity - número de imágenes.
        - cam_msgs_quantity - número de CAM messages.
        - number_of_loops - usado para testear si el número de pasos es el correcto.
        - lineas_detectadas - Lista con las líneas CAM messages obtenidas del log correspondiente.
        - progress_callback - se devuelve información al hilo principal.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        fecha_foto_datetime = pd.to_datetime(current_dataframe["Fecha_foto"])
        df_timedelta = pd.to_timedelta(average, unit="seconds")
        current_dataframe["Fecha_foto_average_added"] = fecha_foto_datetime + df_timedelta  # Creamos la columna "Fecha_foto_average_added".
        fecha_captura_log = pd.to_datetime(current_dataframe["Fecha_captura_log"])
        fechas_foto_average_added = pd.to_datetime(current_dataframe["Fecha_foto_average_added"])
        calculate_min = []
        # A cada línea de la nueva columna Fecha_foto_average_added le restamos cada línea de la columna Fecha_captura_log
        # con el objetivo de localizar el mínimo de esa nueva serie de restas (calculate_min), que será la línea que concuerde.
        # El valor mínimo será la línea con la que habrá que encajar las líneas de ambas columnas.
        for index_in_df_fechas_foto_average_added, fecha_foto_average_added in enumerate(fechas_foto_average_added):
            if(fecha_foto_average_added.year != 1980):  # Comprobamos que el año es el correcto, ya que en las líneas añadidas,
                # en las que no hay imágenes, no tenemos fecha real.
                for fecha_captura in fecha_captura_log:
                    if(fecha_foto_average_added >= fecha_captura): #TODO: No tengo claro si es necesario hacer esto o no.
                        calculate_min.append(fecha_foto_average_added-fecha_captura)
                    else:
                        calculate_min.append(fecha_captura-fecha_foto_average_added)
                calculate_min_series = pd.Series(calculate_min)
                # calculate_min_series.to_csv(os.path.join(input_folder, str(number_of_loops) + "_Calculate_min_series.csv"), sep = ",", header=False, index=False)
                if calculate_min_series.argmin() == index_in_df_fechas_foto_average_added: # Si el índice del mínimo valor de la serie coincide con el índice de la columna
                    # Fecha_foto_average_added que actualmente estamos restando, entonces ambas líneas están ya encajadas. Pasamos a la siguiente.
                    calculate_min.clear()
                else:
                    self.organizer_logger.logger.debug("El mínimo es {0} y su posición es {1}. El índice del df Fecha_foto_average_added {2}".format(calculate_min_series.min(), calculate_min_series.argmin(), index_in_df_fechas_foto_average_added))
                    self.organizer_logger.logger.debug("\nEl número de líneas que hay que insertar es: {0}".format(calculate_min_series.argmin() - index_in_df_fechas_foto_average_added) + "\n")
                    self.organizer_logger.logger.debug("El índice para insertar es {0}".format(index_in_df_fechas_foto_average_added) + "\n")
                    # Quiere decir que hay que desplazar tantas posiciones como la diferencia entre calculate_min_series.argmin() e index_in_df_fechas_foto_average_added, las columnas de imagen por encima de index_in_df_fechas_foto_average_added
                    # Hay que hacer calculate_min_series.argmin() - index_in_df_fechas_foto_average_added = Líneas a insertar.
                    # Se insertan las líneas antes de index_in_df_fechas_foto_average_added
                    df_modified = self.create_dataframe_modified(input_folder, current_dataframe["Nombre_imagen"].values.tolist(), (calculate_min_series.argmin() - index_in_df_fechas_foto_average_added), index_in_df_fechas_foto_average_added, lineas_detectadas)
                    if (calculate_min_series.argmin() - index_in_df_fechas_foto_average_added) < 0:
                        self.organizer_logger.logger.debug("La diferencia entre calculate_min_series.argmin() - index_in_df_fechas_foto_average_added es {0}".format(calculate_min_series.argmin() - index_in_df_fechas_foto_average_added))
                        self.organizer_logger.logger.debug("Diferencia negativa")
                        # No debería de pasar nunca, ya que hemos ido desde la primera línea de "Fecha_foto_average_added" continuamente. No deberíamos de encontrar nunca un mínimo
                        # de una línea de la columna "Fecha_captura_log" por encima de la columna "Fecha_foto_average_added"
                        # TODO: Si entramos aquí, hay un error en el algoritmo o ha pasado algo con los datos que no casan entre sí.
                        return None, None
                    else:
                        return (calculate_min_series.argmin() - index_in_df_fechas_foto_average_added), df_modified
            else:
                # Aquí entra cuando en la columna Fecha_foto_average_added tenemos como año 1980, ya que no hay foto y hemos puesto como fecha foto, la fecha base de 1980.
                # print("La fecha es {0} y la posición es {1}".format(fecha_foto_average_added,index_in_df_fechas_foto_average_added))
                pass
        self.organizer_logger.logger.debug("Acabamos el bucle")
        return None, None
    
        # current_dataframe.to_csv(os.path.join(input_folder, "Time.csv"), sep = ",", header=True, index=False)
    
    def generate_df_modified_more_images(self, input_folder: str, current_detected_cam_lines, current_dataframe: pd.DataFrame, average: float, images_quantity: int, cam_msgs_quantity: int, number_of_loops: int, progress_callback, progress_bar) -> list[int, pd.DataFrame, list[str]] | list[None, None, None]:
        """
        Función similar a "generate_df_modified", pero que se usa cuando hay más imágenes que CAM msgs. En este caso se realiza igualmente un doble bucle, pero en este caso
        recorriendo primero la columna Fecha_captura_log y luego la columna Fecha_foto_average_added, con el objetivo de encajar cada una de las líneas de imagen
        con su correspondiente línea en el log, mediante la resta de cada línea de Fecha_captura_log  con cada una de las líneas de Fecha_foto_average_added.

        Si las líneas ya están encajadas, seguimos en el bucle para la siguiente línea. Si no están, generamos un nuevo dataframe modificado, en el que las líneas no encajadas localizadas
        se encajan.

        Arguments:
        ---------
        - input_folder - carpeta de entrada que tiene la carpeta RGB ya creada con imágenes y con el archivo log.
        - current_detected_cam_lines - Es una lista que almacena la líneas CAM del log. En cada paso del bucle la lista es modificada añadiendo las líneas que 
        no habían sido almacenadas en el log.
        - current_dataframe - dataframe usado para realizar los cálculos en este paso del bucle.
        - average - media que se usará para sumar a la columna Fecha_foto_average_added.
        - images_quantity - número de imágenes.
        - cam_msgs_quantity - número de CAM messages.
        - number_of_loops - usado para testear si el número de pasos es el correcto.
        - progress_callback - se devuelve información al hilo principal.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        fecha_foto_datetime = pd.to_datetime(current_dataframe["Fecha_foto"])
        df_timedelta = pd.to_timedelta(average, unit="seconds")
        current_dataframe["Fecha_foto_average_added"] = fecha_foto_datetime + df_timedelta  # Creamos la columna "Fecha_foto_average_added".
        fecha_captura_log = pd.to_datetime(current_dataframe["Fecha_captura_log"])
        fechas_foto_average_added = pd.to_datetime(current_dataframe["Fecha_foto_average_added"])
        calculate_min = []
        # A cada línea de la nueva columna Fecha_captura_logle restamos cada línea de la columna Fecha_foto_average_added 
        # con el objetivo de localizar el mínimo de esa nueva serie de restas (calculate_min), que será la línea que concuerde.
        # El valor mínimo será la línea con la que habrá que encajar las líneas de ambas columnas.
        for index_in_fecha_captura_log, fecha_captura_log_item in enumerate(fecha_captura_log):
            if(fecha_captura_log_item.year != 1980):  # Comprobamos que el año es el correcto, ya que en las líneas añadidas,
                # en las que no hay imágenes, no tenemos fecha real.
                for fecha_foto_average_added in fechas_foto_average_added:
                    if(fecha_captura_log_item >= fecha_foto_average_added): #TODO: No tengo claro si es necesario hacer esto o no.
                        calculate_min.append(fecha_captura_log_item-fecha_foto_average_added)
                    else:
                        calculate_min.append(fecha_foto_average_added-fecha_captura_log_item)
                calculate_min_series = pd.Series(calculate_min)
                # calculate_min_series.to_csv(os.path.join(input_folder, str(number_of_loops) + "_Calculate_min_series.csv"), sep = ",", header=False, index=False)
                if calculate_min_series.argmin() == index_in_fecha_captura_log: # Si el índice del mínimo valor de la serie coincide con el índice de la columna
                    # Fecha_captura_log que actualmente estamos restando, entonces ambas líneas están ya encajadas. Pasamos a la siguiente.
                    calculate_min.clear()
                else:
                    self.organizer_logger.logger.debug("El mínimo es {0} y su posición es {1}. El índice del df Fecha_foto_average_added {2}".format(calculate_min_series.min(), calculate_min_series.argmin(), index_in_fecha_captura_log))
                    self.organizer_logger.logger.debug("\nEl número de líneas que hay que insertar es: {0}".format(calculate_min_series.argmin() - index_in_fecha_captura_log) + "\n")
                    self.organizer_logger.logger.debug("El índice para insertar es {0}".format(index_in_fecha_captura_log) + "\n")
                    # Quiere decir que hay que desplazar tantas posiciones como la diferencia entre calculate_min_series.argmin() e index_in_df_fechas_foto_average_added, las columnas de imagen por encima de index_in_df_fechas_foto_average_added
                    # Hay que hacer calculate_min_series.argmin() - index_in_df_fechas_foto_average_added = Líneas a insertar.
                    # Se insertan las líneas antes de index_in_df_fechas_foto_average_added
                    df_modified = self.create_dataframe_modified_more_images(input_folder, current_detected_cam_lines, current_dataframe["Nombre_imagen"].values.tolist(), (calculate_min_series.argmin() - index_in_fecha_captura_log), index_in_fecha_captura_log)
                    if (calculate_min_series.argmin() - index_in_fecha_captura_log) < 0:
                        self.organizer_logger.logger.debug("La diferencia entre calculate_min_series.argmin() - index_in_df_fechas_foto_average_added es {0}".format(calculate_min_series.argmin() - index_in_fecha_captura_log))
                        self.organizer_logger.logger.debug("Diferencia negativa")
                        # No debería de pasar nunca, ya que hemos ido desde la primera línea de "Fecha_captura_log" continuamente. No deberíamos de encontrar nunca un mínimo
                        # de una línea de la columna "Fecha_foto_average_added" por encima de la columna "Fecha_captura_log"
                        # TODO: Si entramos aquí, hay un error en el algoritmo o ha pasado algo con los datos que no casan entre sí.
                        return None, None, None
                    else:
                        return (calculate_min_series.argmin() - index_in_fecha_captura_log), df_modified[0], df_modified[1]
            else:
                # Aquí entra cuando en la columna Fecha_foto_average_added tenemos como año 1980, ya que no hay foto y hemos puesto como fecha foto, la fecha base de 1980.
                # print("La fecha es {0} y la posición es {1}".format(fecha_foto_average_added,index_in_df_fechas_foto_average_added))
                pass
        # Si acabamos el bucle y llegamos aquí, quiere decir que el resto de líneas que quedan sin encajar están al final del dataframe.
        self.organizer_logger.logger.debug("Acabamos el bucle")
        return None, None, None
    
    def create_dataframe_modified_more_images(self, input_folder: str, cam_detected_lines: list[str], images: list[str], number_of_lines_to_insert: int, index_where_insert: int) -> list[pd.DataFrame, int]:
        """
        Función similar a create_dataframe_modified pero cuando hay más imágenes que CAM msgs.
        Genero un nuevo dataframe obtenido con los datos de imágenes, CAM msgs y log teniendo en cuenta el paso anterior del procesado.
        Se añaden líneas para encajar cada una de las imágenes con su línea del log correspondiente. 
        
        Arguments:
        ---------
        - input_folder - carpeta de entrada que tiene la carpeta RGB ya creada con imágenes y con el archivo log.
        - cam_detected_lines - líneas CAM del log del paso anterior del bucle. La primera lista proviene de la primera lectura del log.
        - images - lista con las imágenes del directorio. En este caso no se modifican para cada paso del bucle.
        - number_of_lines_to_insert - Número de líneas que hay que insertar.
        - index_where_insert - Índice de la lista en la que hay que insertar el número de líneas definidas por number_of_lines_to_insert
        """        
        fecha_base = datetime.datetime(1980, 1, 6)
        linea_cam = cam_detected_lines[0].split(", ")

        # No puedo volver a leer las líneas CAM detectadas del log, ya que volvería a empezar de nuevo. Tengo que partir de las líneas detectadas del dataframe anterior.
        if cam_detected_lines[0] != "None" and cam_detected_lines[0] != "":
            primera_fecha_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
        else:
            primera_fecha_log = fecha_base
        nombresColumnas = ['Nombre_imagen', 'Fecha_captura_log', 'Fecha_foto', 'Diferencia_fecha_log_foto',"Tramos_captura_log","Tramos_captura_imagen","Tramos_diferencia_fecha_log_foto"]
        
        # En este caso podría volver a leer las imágenes del directorio, ya que la columna de imágenes no la modifico, pero para no andar leyendo del disco duro
        # parto de las imágenes del dataframe anterior.
        # images = self.utils_obj.get_images_from_dir(input_folder)

        df = pd.DataFrame(columns=nombresColumnas)
        
        if index_where_insert >= 0:
            self.organizer_logger.logger.debug("El índice para insertar antes es {0}".format(index_where_insert))
            self.organizer_logger.logger.debug("El número de líneas que hay que insertar es: {0}".format(number_of_lines_to_insert))
            for x in range(number_of_lines_to_insert):
                cam_detected_lines.insert(index_where_insert, "None")

        fecha_foto_anterior = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[0]))
        
        fecha_captura_log_anterior = primera_fecha_log
        resta_fechas_log_foto_anterior = fecha_captura_log_anterior - fecha_foto_anterior       
        
        # Bucle para ir añadiendo líneas al dataframe.
        for indice, line in enumerate(images):
            if(indice < len(cam_detected_lines) and cam_detected_lines[indice] != "None" and cam_detected_lines[indice] != ""):  
                linea_cam = cam_detected_lines[indice].split(", ")
                fecha_captura_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
            else: # Ocurre cuando el número de imágenes es menor que el número de CAM messages, o no tenemos nombre de imagen.
                fecha_captura_log = datetime.datetime(1980, 1, 6)

            fecha_foto = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, line))

            diferencia_fecha_log_foto = fecha_captura_log - fecha_foto
            # if fecha_foto !=  datetime.datetime(1980, 1, 6):
            #     diferencia_fecha_log_foto_suma += diferencia_fecha_log_foto.total_seconds()

            nombre_imagen = line
            
            if diferencia_fecha_log_foto > resta_fechas_log_foto_anterior:
                tramos_resta_fechas_log_foto=diferencia_fecha_log_foto-resta_fechas_log_foto_anterior
            else:
                tramos_resta_fechas_log_foto=resta_fechas_log_foto_anterior-diferencia_fecha_log_foto
            df.loc[indice] = [nombre_imagen, fecha_captura_log, fecha_foto, diferencia_fecha_log_foto.total_seconds(), fecha_captura_log-fecha_captura_log_anterior, fecha_foto-fecha_foto_anterior, tramos_resta_fechas_log_foto.total_seconds()]
            
            fecha_captura_log_anterior = fecha_captura_log
            fecha_foto_anterior = fecha_foto
            resta_fechas_log_foto_anterior = diferencia_fecha_log_foto

        return df, cam_detected_lines

    def create_dataframe_modified(self, input_folder: str, images: list[str], number_of_lines_to_insert: int, index_where_insert: int, lineas_detectadas: list[str]) -> pd.DataFrame:
        """
        Genero un nuevo dataframe obtenido con los datos de imágenes y log teniendo en cuenta el paso anterior del procesado.
        Se añaden líneas para encajar cada una de las imágenes con su línea del log correspondiente. 
        
        Arguments:
        ---------
        - input_folder - carpeta de entrada que tiene la carpeta RGB ya creada con imágenes y con el archivo log.
        - images - lista con las imágenes del dataframe del paso anterior del bucle. La primera lista proviene de la lectura de la carpeta PBx_Vx.
        - number_of_lines_to_insert - Número de líneas que hay que insertar.
        - index_where_insert - Índice de la lista en la que hay que insertar el número de líneas definidas por number_of_lines_to_insert.
        - lineas_detectadas - Lista con las líneas CAM messages obtenidas del log correspondiente.
        """
        # lineas_detectadas = []
        # lista_logs = self.utils_obj.get_logs_from_dir(input_folder)
        # with open(os.path.join(input_folder, lista_logs[0]), 'r') as archivo:
        #     for linea in archivo:
        #         if linea.startswith('CAM'):
        #             lineas_detectadas.append(linea.strip()) # Añadimos a lineas_detectadas las líneas que empieza con CAM y que tienes los datos GPS

        fecha_base = datetime.datetime(1980, 1, 6)
        linea_cam = lineas_detectadas[0].split(", ")
        primera_fecha_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
        nombresColumnas = ['Nombre_imagen', 'Fecha_captura_log', 'Fecha_foto', 'Diferencia_fecha_log_foto',"Tramos_captura_log","Tramos_captura_imagen","Tramos_diferencia_fecha_log_foto"]
        # No puedo volver a leer las imágenes del directorio, ya que volvería a empezar de nuevo. Tengo que partir de las imágenes del dataframe anterior.
        # images = self.utils_obj.get_images_from_dir(input_folder)
        
        df = pd.DataFrame(columns=nombresColumnas)
        
        if index_where_insert >= 0:
            self.organizer_logger.logger.debug("El índice para insertar antes es {0}".format(index_where_insert))
            self.organizer_logger.logger.debug("El número de líneas que hay que insertar es: {0}".format(number_of_lines_to_insert))
            for x in range(number_of_lines_to_insert):
                images.insert(index_where_insert, "None")

        if(images[0] != "None" and images[0] != ""): # Como estamos leyendo la columna images del paso anterior, hay valores vacíos (del primer dataframe) y valores None
            # (que hemos añadido posteriormente)
            fecha_foto_anterior = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[0]))
        else:
            fecha_foto_anterior = fecha_base

        fecha_captura_log_anterior = primera_fecha_log
        resta_fechas_log_foto_anterior = fecha_captura_log_anterior - fecha_foto_anterior       
        
        # Bucle para ir añadiendo líneas al dataframe.
        for indice, line in enumerate(lineas_detectadas):
            if(indice < len(images) and images[indice] != "None" and images[indice] != ""):  
                fecha_foto = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[indice]))
            else: # Ocurre cuando el número de imágenes es menor que el número de CAM messages, o no tenemos nombre de imagen.
                fecha_foto = datetime.datetime(1980, 1, 6)
            linea_cam = line.split(", ")
            fecha_captura_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
            diferencia_fecha_log_foto = fecha_captura_log - fecha_foto
            # if fecha_foto !=  datetime.datetime(1980, 1, 6):
            #     diferencia_fecha_log_foto_suma += diferencia_fecha_log_foto.total_seconds()

            nombre_imagen = ""
            if(indice < len(images)):
                nombre_imagen = images[indice]
            
            if diferencia_fecha_log_foto > resta_fechas_log_foto_anterior:
                tramos_resta_fechas_log_foto=diferencia_fecha_log_foto-resta_fechas_log_foto_anterior
            else:
                tramos_resta_fechas_log_foto=resta_fechas_log_foto_anterior-diferencia_fecha_log_foto
            df.loc[indice] = [nombre_imagen, fecha_captura_log, fecha_foto, diferencia_fecha_log_foto.total_seconds(), fecha_captura_log-fecha_captura_log_anterior, fecha_foto-fecha_foto_anterior, tramos_resta_fechas_log_foto.total_seconds()]
            
            fecha_captura_log_anterior = fecha_captura_log
            fecha_foto_anterior = fecha_foto
            resta_fechas_log_foto_anterior = diferencia_fecha_log_foto

        return df        

    def check_averages_range_and_get_average(self, average_ant: float, average_post: float, max_difference: float) -> bool:
        """
        Halla la media de la diferencia entre la fecha del CAM msg y la fecha de la imagen que debemos usar para encajar las imágenes descolocadas.
        
        Arguments:
        ---------
        - average_ant - La media del vuelo anterior
        - average_post - La media del vuelo posterior
        - max_difference - Diferencia máxima que tendrían que tener las medias anterior y posterior para considerarlas iguales. Es decir, que la cámara no se tuvo
        que volver a sincronizar.
        """
        if average_ant is None and average_post is not None:
            return average_post
        elif average_post is None and average_ant is not None:
            return average_ant
        elif average_post is not None and average_ant is not None and abs(average_ant-average_post) <= max_difference:
            return (average_post + average_ant)/2
        else:
            return None
    
    def is_flight_correct(self, full_pbx_vx_path: str, logs_input_folder: str) -> list[bool, list[str]]:
        """
        Función que comprueba si el vuelo del path de entrada tiene igual el número de imágenes y el número de líneas CAM messages.
        Devuelve una lista con el primer elemento un booleano que indica si el vuelo es correcto.
        En el caso de que así sea, el segundo elemento devuelve un string con las líneas CAM messages del log existente en la carpeta de entrada.

        Arguments:
        ---------
        - full_pbx_vx_path - Full path de la carpeta de entrada PBX_VX
        - logs_input_folder - Carpeta de entrada donde están almacenados los logs
        """
        lista_logs = self.utils_obj.get_logs_from_dir(logs_input_folder)
        lineas_detectadas = []
        # print("El número de logs es: {0}".format(len(lista_logs)))

        error_log_pbx_vx = True
        n_logs_pbx_vx = 0

        for log_file in lista_logs:
            if os.path.basename(full_pbx_vx_path) in log_file:
                error_log_pbx_vx = False
                n_logs_pbx_vx = n_logs_pbx_vx + 1
                with open(os.path.join(logs_input_folder, log_file), 'r') as archivo:
                    for linea in archivo:
                        if linea.startswith('CAM'):
                            lineas_detectadas.append(linea.strip()) # Añadimos a lineas_detectadas las líneas que empieza con CAM y que tienes los datos GPS  

        if(error_log_pbx_vx or n_logs_pbx_vx > 1):  # Comprobamos si no hay logs pertenecientes al PBX y VX correspondiente o hay más de uno
            return False, None
            
        # with open(os.path.join(full_pbx_vx_path, lista_logs[0]), 'r') as archivo:
        #     for linea in archivo:
        #         if linea.startswith('CAM'):
        #             lineas_detectadas.append(linea.strip()) # Añadimos a lineas_detectadas las líneas que empieza con CAM y que tienes los datos GPS

        images_number = self.utils_obj.contar_imagenes_or_tmc(full_pbx_vx_path)
        if len(lineas_detectadas) == images_number:
            return True, lineas_detectadas
        else:
            return False, None

    def get_pbx_vx_path(self, base_input_folder: str, pb: int, v: int) -> str:
        """
        Función que devuelve el full path correspondiente a un PB y a un Vuelo a partir de la carpeta base, siendo ésta la carpeta RGB de salida,
        despues del procesamiento de las RGBs de AEROTOOLS.
        
        Arguments:
        ---------
        - base_input_folder - carpeta en la que están las subcarpetas PB y PBX_VX. Es la carpeta RGB de salida, despues del procesamiento de las RGBs de AEROTOOLS..
        - pb - Power Block que ha sobrevolado el dron.
        - v - Número de vuelo.
        """
        pbx_folder = "PB" + str(pb)
        pbx_vx_folder_name = pbx_folder + "_V" + str(v)
        return os.path.join(os.path.join(os.path.join(base_input_folder, pbx_folder), pbx_vx_folder_name))
    
    def get_pbx_vx(self, path_estadillo: str, vuelo: int) -> list:
        """
        Función que devuelve en una lista el PB y el vuelo específico a partir del estadillo y del número de vuelo.
        
        Arguments:
        ---------
        - path_estadillo - el path del estadillo del que se quiere saber el PB y el vuelo.
        - vuelo -  el número de vuelo del estadillo. Empieza el índice en 0.

        """
        estadillo = pd.read_csv(path_estadillo, sep=';') # Lee el CSV del path_estadillo
        if vuelo < 0 or vuelo >= estadillo.shape[0]:
            self.organizer_logger.logger.debug("No existe el vuelo con número {0}".format(vuelo))
            return None, None
        else:
            nombres_columnas = self.utils_obj.get_nombres_columnas(list(estadillo.columns.values))
            # TODO: Detectar si hay alguna línea sin la fecha final, o quizás sin alguno de los datos necesarios o principales.
            nombreCarpeta_PB = 'PB'+str(estadillo[nombres_columnas['PB']].iloc[vuelo])
            nombreCarpeta_PB_vuelo = nombreCarpeta_PB+'_V'+str(estadillo[nombres_columnas['Vuelo']].iloc[vuelo])
                            
            if vuelo == 0:
                self.organizer_logger.logger.debug("El vuelo {0} es el primero".format(nombreCarpeta_PB_vuelo))
            elif vuelo == (estadillo.shape[0] - 1):
                self.organizer_logger.logger.debug("El vuelo {0} es el último".format(nombreCarpeta_PB_vuelo))

            # self.organizer_logger.logger.debug("El vuelo {0} del estadillo {1} se corresponde con  la carpeta {2}".format(vuelo, path_estadillo,  nombreCarpeta_PB_vuelo))
            return str(estadillo[nombres_columnas['PB']].iloc[vuelo]), str(estadillo[nombres_columnas['Vuelo']].iloc[vuelo])
    
    def get_flight_line(self, path_estadillo: str, pb_number: int, flight_number: int) -> list:
        """
        Función que devuelve el número de línea del estadillo a partir del estadillo, el PB y el vuelo.
        Además, indica si es la primera línea o la última.
        
        Arguments:
        ---------
        - path_estadillo - el path del estadillo del que se quiere saber la línea
        - pb_number -  el PB sobre el que se realizó el vuelo
        - flight_number -  el número de vuelo del PB.
        """
        estadillo = pd.read_csv(path_estadillo, sep=';') # Lee el CSV del path_estadillo
        nombres_columnas = self.utils_obj.get_nombres_columnas(list(estadillo.columns.values))
        for vuelo in range(estadillo.shape[0]):
            pb = estadillo[nombres_columnas['PB']].iloc[vuelo]
            v = estadillo[nombres_columnas['Vuelo']].iloc[vuelo]
            if int(pb_number) == pb and flight_number == int(v):
                if vuelo == 0:
                    return vuelo, "first"
                elif vuelo == (estadillo.shape[0] - 1):
                    return vuelo, "last"
                else:
                    return vuelo, "mid"

    def create_df_cam_image_data(self, cam_detected_lines: list[str], input_folder: str) -> list[pd.DataFrame, float]:
        """
        Función que devuelve una lista cuyo primer elemento es un dataframe con datos generados a partir de las imágenes
        existentes en la carpeta de entrada y los CAM messages existentes en el log.
        Las columnas son:
        ['Nombre_imagen', 'Fecha_captura_log', 'Fecha_foto', 'Diferencia_fecha_log_foto',"Tramos_captura_log","Tramos_captura_imagen","Tramos_diferencia_fecha_log_foto"]
        
        El segundo elemento devuelto es la media de la columna Diferencia_fecha_log_foto pero teniendo en cuenta que 
        solo sumamos la fecha que no corresponde con datetime.datetime(1980, 1, 6), usada para rellenar cuando hay menos imágenes que CAM mesages.
        
        Arguments:
        ---------
        - lineas_detectadas - líneas con CAM message existentes en el log.
        - input_folder - carpeta de vuelo PBX_VX.
        """
        fecha_base = datetime.datetime(1980, 1, 6)
        linea_cam = cam_detected_lines[0].split(", ")
        primera_fecha_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
        nombresColumnas = ['Nombre_imagen', 'Fecha_captura_log', 'Fecha_foto', 'Diferencia_fecha_log_foto',"Tramos_captura_log","Tramos_captura_imagen","Tramos_diferencia_fecha_log_foto"]
        images = self.utils_obj.get_images_from_dir(input_folder)
        diferencia_fecha_log_foto_suma = 0.0  # Inicializo este valor para devolver la media de la columna Diferencia_fecha_log_foto
        df = pd.DataFrame(columns=nombresColumnas)
        fecha_foto_anterior = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[0]))
        fecha_captura_log_anterior = primera_fecha_log
        resta_fechas_log_foto_anterior = fecha_captura_log_anterior - fecha_foto_anterior
        for indice, line in enumerate(cam_detected_lines):
            if(indice < len(images)):  
                fecha_foto = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[indice]))
            else: # Ocurre cuando el número de imágenes es menor que el número de CAM messages, por lo que el valor en este caso es el de la fecha base.
                fecha_foto = datetime.datetime(1980, 1, 6)
            linea_cam = line.split(", ")
            fecha_captura_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
            diferencia_fecha_log_foto = fecha_captura_log - fecha_foto
            if fecha_foto !=  datetime.datetime(1980, 1, 6):
                diferencia_fecha_log_foto_suma += diferencia_fecha_log_foto.total_seconds()  # Usado para hallar la media de la columna Diferencia_fecha_log_foto si el dataframe tiene líneas con fecha no válida

            nombre_imagen = ""
            if(indice < len(images)):
                nombre_imagen = images[indice]
            
            if diferencia_fecha_log_foto > resta_fechas_log_foto_anterior:
                tramos_resta_fechas_log_foto=diferencia_fecha_log_foto-resta_fechas_log_foto_anterior
            else:
                tramos_resta_fechas_log_foto=resta_fechas_log_foto_anterior-diferencia_fecha_log_foto
            df.loc[indice] = [nombre_imagen, fecha_captura_log, fecha_foto, diferencia_fecha_log_foto.total_seconds(), fecha_captura_log-fecha_captura_log_anterior, fecha_foto-fecha_foto_anterior, tramos_resta_fechas_log_foto.total_seconds()]
            
            fecha_captura_log_anterior = fecha_captura_log
            fecha_foto_anterior = fecha_foto
            resta_fechas_log_foto_anterior = diferencia_fecha_log_foto
        
        # df.to_csv(os.path.join(input_folder, "Time.csv"), sep = ",", header=True, index=False)

        return df, diferencia_fecha_log_foto_suma/len(images)
    
    def create_df_cam_image_data_more_images(self, cam_detected_lines, input_folder) -> list[pd.DataFrame, float]:
        """
        Función similar a create_df_cam_image_data que devuelve una lista cuyo primer elemento es un dataframe con datos generados a partir de las imágenes
        existentes en la carpeta de entrada y los CAM messages existentes en el log.
        La diferencia radica en que el bucle principal recorre la lista de imágenes en lugar de la lista de las líneas CAM del log, ya que la cantidad de imágenes es mayor
        que el número de líneas del log.
        Las columnas son:
        ['Nombre_imagen', 'Fecha_captura_log', 'Fecha_foto', 'Diferencia_fecha_log_foto',"Tramos_captura_log","Tramos_captura_imagen","Tramos_diferencia_fecha_log_foto"]
        
        El segundo elemento devuelto es la media de la columna Diferencia_fecha_log_foto pero teniendo en cuenta que 
        solo sumamos la fecha que no corresponde con datetime.datetime(1980, 1, 6), usada para rellenar cuando hay menos imágenes que CAM mesages.
        
        Arguments:
        ---------
        - cam_detected_lines - líneas con CAM message existentes en el log.
        - input_folder - carpeta de vuelo PBX_VX.
        """
        fecha_base = datetime.datetime(1980, 1, 6)
        linea_cam = cam_detected_lines[0].split(", ")
        primera_fecha_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
        nombresColumnas = ['Nombre_imagen', 'Fecha_captura_log', 'Fecha_foto', 'Diferencia_fecha_log_foto',"Tramos_captura_log","Tramos_captura_imagen","Tramos_diferencia_fecha_log_foto"]
        images = self.utils_obj.get_images_from_dir(input_folder)
        diferencia_fecha_log_foto_suma = 0.0  # Inicializo este valor para devolver la media de la columna Diferencia_fecha_log_foto
        df = pd.DataFrame(columns=nombresColumnas)
        fecha_foto_anterior = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[0]))
        fecha_captura_log_anterior = primera_fecha_log
        resta_fechas_log_foto_anterior = fecha_captura_log_anterior - fecha_foto_anterior

        for indice_images, line in enumerate(images):

            if(indice_images < len(cam_detected_lines)):
                linea_cam = cam_detected_lines[indice_images].split(", ")
                fecha_captura_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
            else: # Ocurre cuando el número de imágenes es mayor que el número de CAM messages, por lo que el valor en este caso es el de la fecha base.
                fecha_captura_log = datetime.datetime(1980, 1, 6)

            fecha_foto = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[indice_images]))
            diferencia_fecha_log_foto = fecha_captura_log - fecha_foto
            
            
            if fecha_captura_log !=  datetime.datetime(1980, 1, 6):
                diferencia_fecha_log_foto_suma += diferencia_fecha_log_foto.total_seconds()  # Usado para hallar la media de la columna Diferencia_fecha_log_foto si el dataframe tiene líneas con fecha no válida

            nombre_imagen = images[indice_images]
            
            if diferencia_fecha_log_foto > resta_fechas_log_foto_anterior:
                tramos_resta_fechas_log_foto=diferencia_fecha_log_foto-resta_fechas_log_foto_anterior
            else:
                tramos_resta_fechas_log_foto=resta_fechas_log_foto_anterior-diferencia_fecha_log_foto
            df.loc[indice_images] = [nombre_imagen, fecha_captura_log, fecha_foto, diferencia_fecha_log_foto.total_seconds(), fecha_captura_log-fecha_captura_log_anterior, fecha_foto-fecha_foto_anterior, tramos_resta_fechas_log_foto.total_seconds()]
            
            fecha_captura_log_anterior = fecha_captura_log
            fecha_foto_anterior = fecha_foto
            resta_fechas_log_foto_anterior = diferencia_fecha_log_foto
        
        # df.to_csv(os.path.join(input_folder, "Dataframe more images.csv"), sep = ",", header=True, index=False)

        return df, diferencia_fecha_log_foto_suma/len(images)

    def create_csv_for_comparing(self, cam_detected_lines: list[str], input_folder:str, image_number: int) -> None:
        """
        Esta función es la que se ha creado primeramente para poder obtener los datos que podríamos necesitar en un df para pasarlo a un csv
        y poder comentarlo con Alberto.
        Ha sido la base de las funciones create_df_cam_image_data y create_df_cam_image_data_more_images

        Arguments:
        ---------
        - cam_detected_lines - líneas con CAM message existentes en el log.
        - input_folder - carpeta de vuelo PBX_VX
        - image_number - cantidad de imágenes en la carpeta PBX_VX
        """
        self.organizer_logger.logger.debug("\nERROR: En el directorio {0} no se corresponde el número de imágenes ({1}) con el número de líneas en el log ({2})".format(input_folder, image_number, len(cam_detected_lines)) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
        fecha_base = datetime.datetime(1980, 1, 6)
        linea_cam = cam_detected_lines[0].split(", ")
        hora_a_sumar = timedelta(hours=1)  # lo he puesto porque hay diferencia de 1 hora entre log y foto por el horario de verano. En invierno la diferencia es de 2 horas.
        # Lo usé para que fuera visible de forma más fácil las diferencias entre log y foto.
        # En la función final, no lo uso pues tendría que estar siempre atento a cuando estamos en invierno o verano. Además, no debería de hacer fatla.
        primera_fecha_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
        nombresColumnas = ['Nombre_imagen','Tiempo_desde_disparo_inicial_log','Fecha_captura_log', 'Fecha_foto', 'Diferencia_fecha_log_foto',"Tramos_captura_log","Tramos_captura_imagen","Tramos_diferencia_fecha_log_foto"]
        images = self.utils_obj.get_images_from_dir(input_folder)
        df = pd.DataFrame(columns=nombresColumnas)
        fecha_foto_anterior = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[0]))
        fecha_captura_log_anterior = primera_fecha_log
        resta_fechas_log_foto_anterior = (fecha_captura_log_anterior + hora_a_sumar) - fecha_foto_anterior
        for indice, line in enumerate(cam_detected_lines):
            if(indice < len(images)):
                fecha_foto = self.exif_management_obj.get_timestamp_from_image(os.path.join(input_folder, images[indice]))
            else:
                fecha_foto = datetime.datetime(1980, 1, 6)                        
            linea_cam = line.split(", ")
            fecha_captura_log = self.calcular_fecha_resultante(fecha_base, float(linea_cam[3]), float(linea_cam[2]))
            resta_fechas_log_foto = (fecha_captura_log + hora_a_sumar) - fecha_foto
            nombre_imagen = ""
            if(indice < len(images)):
                nombre_imagen = images[indice]
            if resta_fechas_log_foto > resta_fechas_log_foto_anterior:
                tramos_resta_fechas_log_foto=resta_fechas_log_foto-resta_fechas_log_foto_anterior
            else:
                tramos_resta_fechas_log_foto=resta_fechas_log_foto_anterior-resta_fechas_log_foto
            if tramos_resta_fechas_log_foto >= timedelta(seconds=2) and tramos_resta_fechas_log_foto < timedelta(days=10):
                self.organizer_logger.logger.debug("*****************************************")
                if(indice < len(images)):
                    self.organizer_logger.logger.debug("Antes de esta imagen: {0} podría faltar alguna captura".format(images[indice]))
                else:
                    self.organizer_logger.logger.debug("Antes de la imagen número {0} podría faltar alguna captura".format(indice))
                self.organizer_logger.logger.debug("*****************************************")
            elif tramos_resta_fechas_log_foto > timedelta(days=10):
                self.organizer_logger.logger.debug("*****************************************")
                self.organizer_logger.logger.debug("A partir de la línea {0} del log no hay más capturas".format(indice))
                self.organizer_logger.logger.debug("*****************************************")
                break  # Salimos del bucle ya que en las siguientes líneas entraría en el if anterior continuamente.

            df.loc[indice] = [nombre_imagen, fecha_captura_log-primera_fecha_log, fecha_captura_log, fecha_foto, resta_fechas_log_foto.total_seconds(), fecha_captura_log-fecha_captura_log_anterior, fecha_foto-fecha_foto_anterior, tramos_resta_fechas_log_foto.total_seconds()]
            fecha_captura_log_anterior = fecha_captura_log
            fecha_foto_anterior = fecha_foto
            resta_fechas_log_foto_anterior = resta_fechas_log_foto
        # df.to_csv(os.path.join(input_folder, "Time.csv"), sep = ",", header=True, index=False)


class Extraction:

    def __init__(self, organizer_logger: "utils.OrganizerLogger") -> None:
        self.utils_obj = utils.Utils(organizer_logger)
        self.stop = False
        self.current_image_number = 0
        self.total_images_number = 0
        self.organizer_logger = organizer_logger
        
    def set_stop(self, stop: bool):
        """
        Función que para el proceso modificando el estado de la variable self.stop

        Arguments:
        ---------
        - stop - Variable que indica si se puede llevar a cabo o no el procesado. A True se para o no arranca, y a False se lleva a cabo.
        """
        self.stop = stop
        
    def reset_variables(self, main_process = True, progress_callback = None):
        """
        Resetea las variables necesarias para mostrar la información correctamente en la ventana de log.
        
        Arguments:
        ---------
        - main_process - Indica que se trata del proceso principal dentro de un hilo o de un proceso secundario, como una opción a mayores a realizar.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        """

        self.current_image_number = 0
        self.total_images_number = 0
        
        if not main_process:
            progress_callback.emit("\nEXTRACCIÓN\n")  # Enviamos el texto para indicar que arranca el proceso de extracción, pero que no es el proceso principal, siendo posterior.

    def get_summarize(self) -> dict:
        """Función que resume diferentes datos al finalizar el proceso. Devuelve un diccionario en el que cada clave es una información del proceso, junto
        con su correspondiente valor, de modo que se pueda mostrar en la ventana del log al finalizar el proceso."""
        summarize_dict= { "Número total de imágenes": self.current_image_number}
        error = False
        if self.total_images_number != self.current_image_number:
            error = True
            self.organizer_logger.logger.info(f"Número total de imágenes en gen_struct_folder: {self.total_images_number}")
            self.organizer_logger.logger.info(f"Número final de imágenes en gen_struct_folder: {self.current_image_number}")
            summarize_dict["Error imágenes"] = f"No hay correspondencia entre número inicial {self.total_images_number} y final de imágenes {self.current_image_number}."
            
        if not error:
            summarize_dict["Sin Errores"] = "Sin errores durante el proceso"
        else:
            summarize_dict["ERROR"] = "HAN EXISTIDO ERRORES"
        return summarize_dict
    
  
    def get_date_taken(self, path: str) -> str:
        """
        Devuelve la fecha de una imagen a partir de su exif, sabiendo que el codigo de la fecha es 36867

        Arguments:
        ---------
        - path - El path de la imagen.
        """
        return(PIL.Image.open(path)._getexif()[36867])

    def ordenar_TMCs(self, carpeta_raiz: str, estadillo: str, ruta_thermoviewer: str, output_folder: str, progress_callback, progress_bar) -> None:
        """
        Esta funcion lleva a cabo diferentes procesados previos a la extracción:
        - Comprueba el número de archivos TMC en cada carpeta P000000X. Si hay más de uno, los junta.
        - Si hay archivos que tengan pocos frames no los procesa.
        - Comprueba que la hora de los frames de los archivos TMC son aproximadamente a la misma hora. Si no concuerda, no los procesa.
        - Comprueba a qué PB y vuelo corresponde cada TMC, teniendo en cuenta el estadillo.
        - Copia el archivo TMC a la carpeta correspondiente PBX_VX de la carpeta de salida con el nombre de la carpeta.

        Arguments:
        ---------
        - carpeta_raiz - Directorio en el que están todas las carpetas P000000X
        - estadillo - Estadillo con la información de los vuelos correspondientes a los archivos TMC.
        - ruta_thermoviewer - Ruta del ejecutable del programa Thermoviewer.
        - output_folder - Carpeta de salida, con la estructura de carpetas previamente creada, en la que se guardarán los archivos TMC.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        ### 1 Lectura del estadillo y de las rutas de la carpeta raiz
        df_estadillo = pd.read_csv(estadillo,sep=';')
        nombres_columnas = self.utils_obj.get_nombres_columnas(list(df_estadillo.columns.values))
        
        rutas = glob.glob(carpeta_raiz + "\*")
        
        hora_foto_antigua = ""

        for ruta in rutas:   #Para cada carpeta P0000000X
            if not self.stop:
                self.organizer_logger.logger.info('La ruta analizada es: {0}'.format(ruta))
                progress_callback.emit("\nLa ruta analizada es: {0}\n".format(ruta))
                tmc = self.check_number_of_videos(ruta_thermoviewer, ruta, progress_callback, progress_bar) # Se comprueba el número de archivos TMC dentro de la carpeta. Si hay más de uno, se juntan.
                # Devuelve la ruta del archivo que hay que tratar, ya sea un TMC producto de una unión de varios, o no.
                # TODO: En el caso de que el valor retornado tmc sea None, deberíamos de hacer un else en el que escribamos en el log un Warning.
                if tmc is not None:
                    self.organizer_logger.logger.debug("El archivo es {0}".format(tmc))
                        
                    subproceso = '"{0}" -i "{1}" -cp iron -expa "{2}" -exfn "IMAGEN_EXTRAIDA" -exsf 35 -exef 40 -exfo rjpg -tl high -serial mavlink -c'.format(ruta_thermoviewer, tmc, os.path.dirname(tmc))
                    subprocess.run(subproceso)  # LLeva a cabo la extracción de 6 imágenes para comprobar la fecha.
                    
                    error_diferencia_fotos = False # Variable auxiliar para tener en cuenta si ha habido error en la fecha de las imágenes pre-extraídas.
                    fotos_preextraidas = glob.glob(ruta+'/*.jpg')
                    if len(fotos_preextraidas) == 0:
                        self.organizer_logger.logger.warning("WARNING: No se ha podido pre-extraer ninguna imagen del archivo TMC {0}.".format(tmc))
                        progress_callback.emit("\nWARNING: No se ha podido pre-extraer ninguna imagen del archivo TMC en la ruta: {0}\n".format(ruta))
                        error_diferencia_fotos = True
                    hora_foto_tmc = None
                    for foto in fotos_preextraidas:
                        hora_foto_tmc = dt.datetime.strptime(self.get_date_taken(os.path.join(ruta,foto)), '%Y:%m:%d %H:%M:%S') #Saca la hora de captura de la foto
                        # Vamos comparando cada hora de una imagen pre-extraída con la hora de la imagen previa.
                        # print('La hora de la foto es ' + str(hora_foto_tmc))
                        if hora_foto_antigua == "":
                            hora_foto_antigua = hora_foto_tmc
                        else:
                            if not self.check_hours(hora_foto_tmc, hora_foto_antigua, 10):
                                self.organizer_logger.logger.info("ERROR: La hora de la foto es distinta entre las 5 imágenes")
                                progress_callback.emit("\nWARNING: La hora de las imágenes del archivo TMC no concuerda en la ruta: {0}\n".format(ruta))
                                error_diferencia_fotos = True
                                break
                        # os.remove(foto) # Una vez que comparamos, borramos la imagen que hemos pre-extraído.

                    hora_foto_antigua = ""
                    if not error_diferencia_fotos:
                        for index, row in df_estadillo.iterrows():                               
                            horaInicio = str(row[nombres_columnas['Hora_de_inicio']])
                            if(horaInicio == "" or horaInicio is None or horaInicio == "nan"):
                                progress_callback.emit("\nWARNING: No existe hora inicio en PB {0} y vuelo {1}\n".format(row[nombres_columnas['PB']], row[nombres_columnas['Vuelo']]))
                                continue

                            horaFinal = str(row[nombres_columnas['Hora_final']])
                            if(horaFinal == "" or horaFinal is None or horaFinal == "nan"):
                                progress_callback.emit("\nWARNING: No existe hora final en PB {0} y vuelo {1}\n".format(row[nombres_columnas['PB']], row[nombres_columnas['Vuelo']]))
                                continue
                            
                            hora_inicio_time_obj = dt.datetime.strptime(row[nombres_columnas['Fecha']] + " " + row[nombres_columnas['Hora_de_inicio']], '%Y:%m:%d %H:%M:%S') - dt.timedelta(minutes=1,seconds=30) # Hora de inicio de cada línea del estadillo
                            hora_final_time_obj = dt.datetime.strptime(row[nombres_columnas['Fecha']] + " " + row[nombres_columnas['Hora_final']], '%Y:%m:%d %H:%M:%S') + dt.timedelta(minutes=1,seconds=30) # Hora final de cada línea del estadillo
                            
                            self.organizer_logger.logger.info('Vuelo: {0} --- {1}'.format(hora_inicio_time_obj, hora_final_time_obj))
                            
                            if hora_foto_tmc is not None and hora_foto_tmc < hora_final_time_obj and hora_foto_tmc > hora_inicio_time_obj:
                                progress_callback.emit("\nEl archivo TMC corresponde al PB: {0} Vuelo: {1}\n".format(row[nombres_columnas['PB']], row[nombres_columnas['Vuelo']]))
                                self.organizer_logger.logger.info('El archivo TMC corresponde al PB: {0} Vuelo: {1}'.format(row[nombres_columnas['PB']], row[nombres_columnas['Vuelo']]))
                                
                                nombre_PB = 'PB{0}'.format(row[nombres_columnas['PB']])
                                nombre_PB_V = 'PB{0}_V{1}'.format(row[nombres_columnas['PB']], row[nombres_columnas['Vuelo']])
                                # En la siguiente línea movemos el archivo TMC a la carpeta correspondiente PBX_VX con el nombre modificado.
                                # shutil.copy2(tmc, os.path.join(os.path.join(os.path.join(os.path.join(output_folder,"TERMICA"), nombre_PB), nombre_PB_V),nombre_PB_V + ".TMC"))
                                shutil.move(tmc, os.path.join(os.path.join(os.path.join(os.path.join(output_folder,"TERMICA"), nombre_PB), nombre_PB_V),nombre_PB_V + ".TMC"))
                                break

                    for foto in glob.glob(ruta+'/*.jpg'):
                        try:
                            os.remove(foto) # Una vez que hemos movido el archivo TMC borramos la imagen. No sé porqué, pero no dejaba borrar la imagen 40 cuando hacíamos la pre-extraccción.
                        except IOError:
                            progress_callback.emit("\nWARNING: No se puede borrar el archivo {0} por estar siendo utilizado por otro proceso.\n".format(foto))
                            
                    
    
    def check_number_of_videos(self, ruta_thermoviewer: str, ruta: str, progress_callback, progress_bar) -> str | None:
        """
        Esta funcion comprueba si hay más de un archivo TMC en cada carpeta de vuelo en la preextracción. En ese caso procede a unirlos en un solo archivo.
        Además, comprueba (mediante llamada a la función check_size y en el caso de que haya solo un archivo en el directorio) si el archivo es menor de un tamaño
        mínimo de bytes.
        Si no hay ningún vídeo en la ruta de entrada o el tamaño del archivo es menor que un tamaño mínimmo (por defecto: 10 Mb), devuelve un None. En caso contrario
        devuelve la ruta del archivo.

        Arguments:
        ---------
        - ruta_thermoviewer - Ruta del ejecutable del programa Thermoviewer.
        - ruta - Carpeta en la que se encuentran los archivos TMC.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        ruta_tmc = glob.glob(ruta+'/*.TMC')
        self.current_image_number += len(ruta_tmc)
        p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
        # y la cantidad actual de imágenes procesadas.
        progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
        progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log. 
        if len(ruta_tmc) > 1: # Hay más de un vídeo TMC. Hay que unirlos.
            self.organizer_logger.logger.info("Hay más de un archivo TMC en el vuelo. Se procede a unir los archivos.")
            progress_callback.emit("\nHay más de un archivo TMC en el vuelo. Se procede a unir los archivos.\n")
            files_tmc = ""
            # El siguiente código borra un posible archivo llamado VIDEO_MERGED.TMC. Si dentro de la carpeta sigue estando este archivo, el programa Thermoviewer, no sé que hace que
            # empieza a crecer ese archivo en tamaño de forma desmesurada.
            # Y es por tener en el mismo directorio un archivo con el mismo nombre que le estoy dando de salida al comando de Thermoviewer.
            for tmc in ruta_tmc:
                if os.path.basename(tmc) == "VIDEO_MERGED.TMC":
                    try:
                        os.remove(tmc)  # Borramos el archivo encontrado.
                    except IOError:
                        progress_callback.emit("\nWARNING: No se puede borrar el archivo VIDEO_MERGED.TMC por estar siendo utilizado por otro proceso. ES NECESARIO BORRARLO MANUALMENTE.\n")

            for tmc in ruta_tmc:
                if os.path.basename(tmc) != "VIDEO_MERGED.TMC":  # Aunque lo borremos anteriormente, está en ruta_tmc, así que no lo unimos en files_tmc
                    files_tmc = files_tmc + tmc + " "
            subproceso = '{0} -expa {1} -exfn VIDEO_MERGED.TMC -merge {2}'.format(ruta_thermoviewer, ruta, files_tmc)
            subprocess.run(subproceso)
            return os.path.join(ruta, "VIDEO_MERGED.TMC")
        elif len(ruta_tmc) == 1: # Sólo hay un archivo. Comprobamos tamaño.
            if self.utils_obj.check_size(os.path.join(ruta, ruta_tmc[0]), 10000000):
                return os.path.join(ruta, ruta_tmc[0])
            else:
                self.organizer_logger.logger.warning("El archivo {0} no tiene una cantidad mínima de frames".format(os.path.join(ruta, ruta_tmc[0])))
                progress_callback.emit("\nWARNING: El archivo {0} no tiene una cantidad mínima de frames\n".format(os.path.join(ruta, ruta_tmc[0])))
                return None
        else: # No hay ningún archivo TMC en la carpeta.
            self.organizer_logger.logger.info("No hay vídeos en el vuelo.")
            progress_callback.emit("\nNo hay vídeos en el vuelo.\n")
            return None
    

    def iterate_folders(self, ruta_thermoviewer: str, folder: str, extraction_temp_auto: bool, extraction_temp_max: int, extraction_temp_min: int, progress_callback, progress_bar) -> None:
        """
        Función que itera a través del arbol de directorios existente en folder. Para cada carpeta lleva a cabo la función extraccion.
        Después de llamar a la función comprueba las carpetas que existen dentro de folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - ruta_thermoviewer -  Ruta del ejecutable del programa Thermoviewer.
        - folder - Carpeta de entrada. Buscará en todo el árbol de carpetas las que tengan archivos TMC para realizar la extracción.
        - extraction_temp_auto - Booleano que indica si el umbral de las temperaturas máxima y mínima de la extracción serán automáticas (a True) o no (a False).
        - extraction_temp_max - En el caso de extraction_temp_auto a True, este parámetro indica el umbral de la temperatura máxima.
        - extraction_temp_min - En el caso de extraction_temp_auto a True, este parámetro indica el umbral de la temperatura mínima.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """ 
        # print("Directorio de entrada: ", folder)
        self.extraccion(ruta_thermoviewer, folder, extraction_temp_auto, extraction_temp_max, extraction_temp_min, progress_callback, progress_bar)
        for dir in next(os.walk(folder))[1]:
            self.iterate_folders(ruta_thermoviewer, os.path.join(folder, dir), extraction_temp_auto, extraction_temp_max, extraction_temp_min, progress_callback, progress_bar)


    def extraccion(self, ruta_thermoviewer: str, folder: str, extraction_temp_auto: bool, extraction_temp_max: int, extraction_temp_min: int, progress_callback, progress_bar) -> None:
        """
        Función que extrae todas las imágenes del archivo TMC que se encuentra en el directorio de entrada.
        
        Arguments:
        ---------
        - ruta_thermoviewer -  Ruta del ejecutable del programa Thermoviewer.
        - folder - Carpeta de entrada. Buscará si tiene archivos TMC para realizar la extracción.
        - extraction_temp_auto - Booleano que indica si el umbral de las temperaturas máxima y mínima de la extracción serán automáticas (a True) o no (a False).
        - extraction_temp_max - En el caso de extraction_temp_auto a True, este parámetro indica el umbral de la temperatura máxima.
        - extraction_temp_min - En el caso de extraction_temp_auto a True, este parámetro indica el umbral de la temperatura mínima.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """ 
        tmcs = self.utils_obj.get_tmcs_from_dir(folder)
        if len(tmcs) > 0:
            progress_callback.emit("\nProcesando {0} TMCs en el directorio {1}".format(len(tmcs), folder) + "\n") # Se envía información al iniciar el procesado de un directorio solo si hay imágenes.
        
        for i in tmcs: # Para cada archivo TMC en el directorio. Entramos en el bucle si hay archivos TMC, y tendrían que estar en la carpeta de vuelo.
            if not self.stop:  # Comprobamos que no se para el proceso desde el interfaz.
                self.current_image_number += 1
                p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                # y la cantidad actual de imágenes procesadas.
                progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.
                progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.
                self.organizer_logger.logger.info("Extracción del archivo: ", os.path.join(folder,i))
                if extraction_temp_auto:  # En modo auto no definimos expresamente las temperaturas máxima y mínima al realizar la extracción.
                    subproceso_1 = '"{0}" -i "{1}" -cp iron -expa "{2}" -exfn "{3}" -fs 2 -exfo rjpg -tl high -serial mavlink -exmeta Pix4D -c'.format(ruta_thermoviewer, os.path.join(folder,i), folder, os.path.basename(folder))
                else:
                    subproceso_1 = '"{0}" -i "{1}" -cp iron -expa "{2}" -exfn "{3}" -fs 2 -ltt "{4}" -utt "{5}" -exfo rjpg -tl high -serial mavlink -exmeta Pix4D -c'.format(ruta_thermoviewer, os.path.join(folder,i), folder, os.path.basename(folder), extraction_temp_min, extraction_temp_max)
                
                subprocess.run(subproceso_1)
                self.organizer_logger.logger.info("Extracción del archivo terminada")
                progress_callback.emit("Extracción del archivo terminada")
    
    def check_hours(self, hora_nueva: dt.datetime, hora_antigua: dt.datetime, dif_max: int) -> bool:
        """
        Función que compara las horas de entrada entre sí para saber si la diferencia entre hora_nueva y hora_antigua es mayor que un valor dado.
        Si es mayor, devuelve un False y si es menor, devuelve un True
        
        Arguments:
        ---------
        - hora_nueva - La hora de la segunda imagen a comparar.
        - hora_antigua - La hora de la primera imagen a comparar.
        - dif_max - Diferencia que debe de tener para considerarse igual.
        """        
        self.organizer_logger.logger.debug("Checking time difference between the pre-extracted images. The time difference in seconds is: " + str((hora_nueva - hora_antigua).seconds))
        if ((hora_nueva - hora_antigua).seconds < dif_max):
            return True
        else:
            return False
    



class AtomExtractor:
    
    def __init__(self) -> None:
        pass

    def extraction(self):
        pass
