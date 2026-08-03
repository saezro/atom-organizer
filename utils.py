import logging
import os
import queue
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

import pipeline
from natsort import natsorted


class OrganizerLogger:
    """
    Clase que aglutina las funciones centradas en la creación de un logger.

    Thread-safe mediante QueueHandler + QueueListener:
    - Los hilos de trabajo solo escriben LogRecords en una queue.Queue a través
      del QueueHandler, operación O(1) sin I/O y sin bloqueos.
    - Un QueueListener serializa toda la I/O real (archivo, consola) desde un
      único hilo dedicado, eliminando locking contention y riesgos de corrupción.
    - Modificar o sustituir handlers siempre pasa por _stop_listener /
      _start_listener, garantizando que el listener nunca accede a un handler
      cerrado o en proceso de sustitución.
    """

    def __init__(
        self,
        name: str,
        log_dir: str | None = None,
        max_bytes: int = 50 * 1024 * 1024,
        backup_count: int = 7,
        create_file_handler: bool = True,
    ) -> None:
        # None -> carpeta de logs del perfil del usuario. El default de antes era el
        # literal "Logs" (relativo al CWD), impredecible en una app instalada.
        if log_dir is None:
            from external_tools import user_log_dir
            log_dir = user_log_dir()
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        # Evita que los mensajes se propaguen al root logger (evita duplicados)
        self.logger.propagate = False

        self._log_dir = log_dir
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._listener: QueueListener | None = None

        # La cola es el único punto de contacto de los hilos con el sistema de
        # logging. queue.Queue es thread-safe por diseño (usa un Lock interno).
        self._log_queue: queue.Queue = queue.Queue(-1)  # sin límite de tamaño

        # Solo se añade al logger el QueueHandler. Los handlers reales
        # (archivo, consola) son gestionados exclusivamente por el listener,
        # en su propio hilo, y nunca se acceden desde los hilos de trabajo.
        self._queue_handler = QueueHandler(self._log_queue)
        self.logger.addHandler(self._queue_handler)

        self.console_handler = logging.StreamHandler()

        # OJO: NO crear aquí el directorio de logs. Estaba fuera del `if` y se
        # ejecutaba SIEMPRE, incluso con create_file_handler=False (el caso del
        # host headless en atom_core/organize.py, que es el que usa la UI webview).
        # Con el CWD apuntando a un sitio no escribible eso abortaba la corrida
        # entera antes de la primera fase con `PermissionError: [WinError 5]
        # Acceso denegado: 'Logs'`. create_file_handler() ya crea el directorio,
        # y sólo cuando de verdad se va a escribir en él.
        if create_file_handler:
            self.create_file_handler(log_dir, max_bytes, backup_count)

    # ------------------------------------------------------------------
    # Gestión interna del QueueListener
    # ------------------------------------------------------------------

    def _get_active_handlers(self) -> list[logging.Handler]:
        handlers: list[logging.Handler] = []
        if hasattr(self, "console_handler"):
            handlers.append(self.console_handler)
        if hasattr(self, "file_handler"):
            handlers.append(self.file_handler)
        return handlers

    def _start_listener(self) -> None:
        """Arranca el QueueListener con los handlers reales actualmente activos."""
        self._stop_listener()
        handlers = self._get_active_handlers()
        if handlers:
            # respect_handler_level=True: el listener filtra por el nivel de
            # cada handler real en lugar de emitir todos los registros.
            self._listener = QueueListener(
                self._log_queue, *handlers, respect_handler_level=True
            )
            self._listener.start()

    def _stop_listener(self) -> None:
        """Para el QueueListener de forma segura si está en marcha.

        QueueListener.stop() vacía la cola antes de unirse al hilo, garantizando
        que no se pierdan registros pendientes.
        """
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    # ------------------------------------------------------------------
    # Creación / sustitución del RotatingFileHandler
    # ------------------------------------------------------------------

    def create_file_handler(
        self,
        log_dir: str | None = None,
        max_bytes: int = 50 * 1024 * 1024,
        backup_count: int = 7,
    ) -> None:
        if log_dir is None:
            from external_tools import user_log_dir
            log_dir = user_log_dir()
        # Parar el listener ANTES de tocar los handlers reales para evitar que
        # el hilo del listener acceda a un handler cerrado o semiiniciado.
        self._stop_listener()

        if hasattr(self, "file_handler"):
            self.file_handler.close()

        # Crear directorio de logs si no existe (el CWD puede haber cambiado)
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = log_path / f"app_{timestamp}.log"

        self.file_handler = RotatingFileHandler(
            log_filename,
            mode="a",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Configuración de niveles en los handlers reales
    # ------------------------------------------------------------------

    def set_console_handler(self) -> None:
        self.console_handler.setLevel(logging.INFO)

    def set_file_handler(self) -> None:
        self.file_handler.setLevel(logging.DEBUG)

    def set_handlers(self) -> None:
        """Configura niveles en los handlers reales y (re)arranca el listener."""
        if hasattr(self, "console_handler"):
            self.set_console_handler()
        if hasattr(self, "file_handler"):
            self.set_file_handler()
        self._start_listener()

    def show_handlers(self) -> None:
        print("Logger handlers:", self.logger.handlers)
        if self._listener:
            print("QueueListener handlers:", self._listener.handlers)

    # ------------------------------------------------------------------
    # Formateo
    # ------------------------------------------------------------------

    def set_formatter(self) -> None:
        formatter = logging.Formatter(
            # "{asctime} - {name} - {levelname} - {module}.{funcName}:{lineno} - {message}",
            "{asctime} - {levelname} - {module}.{funcName}:{lineno} - {message}",
            style="{",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        if hasattr(self, "console_handler"):
            self.console_handler.setFormatter(formatter)
        if hasattr(self, "file_handler"):
            self.file_handler.setFormatter(formatter)

    # ------------------------------------------------------------------
    # Manejo de excepciones no capturadas
    # ------------------------------------------------------------------

    def handle_uncaught_exception(self, exc_type, exc_value, exc_traceback):
        self.logger.critical(
            "uncaught exception, application will terminate.",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def set_exception_handler(self) -> None:
        """Configura el manejador de excepciones no capturadas."""
        sys.excepthook = lambda exc_type, exc_value, exc_traceback: (
            self.handle_uncaught_exception(exc_type, exc_value, exc_traceback)
        )

    # ------------------------------------------------------------------
    # Habilitar / deshabilitar handlers en caliente
    # ------------------------------------------------------------------

    def set_handler_enabled(self, handler_type: str, enabled: bool) -> None:
        """
        Habilita o deshabilita un handler específico ajustando su nivel de
        filtrado. Al estar los handlers en el listener, el cambio de nivel
        es la única operación necesaria; no hace falta detener el listener.

        Args:
            handler_type: "file" o "console"
            enabled: True para habilitar, False para deshabilitar
        """
        handler = self.file_handler if handler_type == "file" else self.console_handler
        if enabled:
            handler.setLevel(logging.DEBUG if handler_type == "file" else logging.INFO)
        else:
            handler.setLevel(logging.CRITICAL + 1)


class ExternalToolError(RuntimeError):
    pass


def safe_pct(current: int, total: int) -> int:
    """
    Calcula el porcentaje de progreso de forma segura, evitando división por cero
    y recortando el resultado al rango [0, 100].

    Arguments:
    ---------
    - current - valor actual del progreso.
    - total - valor total sobre el que se calcula el porcentaje.
    """
    if total <= 0:
        return 0
    pct = int(current / total * 100)
    return max(0, min(100, pct))


def unique_dest(dest_path: str) -> str:
    """
    Devuelve una ruta de destino que no colisiona con un archivo existente.
    Si `dest_path` no existe, lo devuelve tal cual. Si existe, añade un sufijo
    numérico correlativo (`_1`, `_2`, ...) antes de la extensión hasta encontrar
    una ruta libre.

    Arguments:
    ---------
    - dest_path - ruta de destino candidata.
    """
    if not os.path.exists(dest_path):
        return dest_path

    nombre, extension = os.path.splitext(dest_path)
    contador = 1
    nuevo_dest = f"{nombre}_{contador}{extension}"
    while os.path.exists(nuevo_dest):
        contador += 1
        nuevo_dest = f"{nombre}_{contador}{extension}"
    return nuevo_dest


def safe_move(src: str, dest: str) -> str:
    """
    Mueve `src` a un destino único derivado de `dest` (sin sobreescribir archivos
    existentes). Relanza `OSError` añadiendo contexto de las rutas implicadas.

    Arguments:
    ---------
    - src - ruta de origen del archivo a mover.
    - dest - ruta de destino deseada.
    """
    destino_final = unique_dest(dest)
    try:
        shutil.move(src, destino_final)
    except OSError as e:
        raise OSError(f"Error moviendo '{src}' a '{destino_final}': {e}") from e
    return destino_final


def safe_copy2(src: str, dest: str) -> str:
    """
    Copia `src` (preservando metadatos) a un destino único derivado de `dest`
    (sin sobreescribir archivos existentes). Relanza `OSError` añadiendo contexto
    de las rutas implicadas.

    Arguments:
    ---------
    - src - ruta de origen del archivo a copiar.
    - dest - ruta de destino deseada.
    """
    destino_final = unique_dest(dest)
    try:
        shutil.copy2(src, destino_final)
    except OSError as e:
        # Preservar la subclase concreta (p. ej. FileNotFoundError): algunos
        # llamadores hacen `except FileNotFoundError` a propósito para tolerar
        # CSVs ausentes. Reenvolver en un OSError base rompía ese manejo y
        # abortaba todo el pipeline en vez de saltar el archivo que falta.
        raise type(e)(f"Error copiando '{src}' a '{destino_final}': {e}") from e
    return destino_final


def run_external(cmd: list[str], timeout: float | None = 300) -> subprocess.CompletedProcess:
    """
    Ejecuta un comando externo de forma segura (siempre en lista, nunca shell string),
    capturando stdout/stderr. Lanza `ExternalToolError` si el proceso termina con
    returncode distinto de 0, o si se supera el timeout.

    Arguments:
    ---------
    - cmd - lista con el comando y sus argumentos.
    - timeout - segundos máximos de espera antes de abortar (None = sin límite).
    """
    try:
        resultado = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ExternalToolError(f"Timeout ejecutando {cmd}: {e}") from e

    if resultado.returncode != 0:
        raise ExternalToolError(
            f"Comando {cmd} falló con returncode {resultado.returncode}: {resultado.stderr}"
        )
    return resultado


class Utils:
    """
    Clase que utilizaremos para programar funciones auxiliares y generales necesarias para llevar a cabo diferentes procesos y que no
    tienen cabida en otras clases.

    Methods
    -------
    get_images_from_dir(self, input_folder) -> list[str]
    extract_number_from_string(self, input_string) -> str
    """

    def __init__(self, organizer_logger: OrganizerLogger) -> None:
        self.organizer_logger = organizer_logger

    def get_images_from_dir(self, input_folder: str, exclude_patterns: list[str] = None) -> list[str]:
        """
        Función que obtiene las imágenes del directorio de entrada.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - exclude_patterns - lista de strings que si están presentes en el nombre del archivo, lo excluyen del resultado.
        """
        files = natsorted(os.listdir(input_folder))
        images = [file for file in files if file.endswith(('jpg', 'png', 'JPG'))] # Si queremos obtener imágenes con diferentes extensiones, las cambiamos aquí.

        if exclude_patterns:
            images = [img for img in images if not any(pattern in img for pattern in exclude_patterns)]

        return images

    def get_tiff_images_from_dir(self, input_folder: str, exclude_patterns: list[str] = None) -> list[str]:
        """
        Función que obtiene las imágenes del directorio de entrada.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        - exclude_patterns - lista de strings que si están presentes en el nombre del archivo, lo excluyen del resultado.
        """
        files = natsorted(os.listdir(input_folder))
        images = [file for file in files if file.endswith(('tif', 'tiff', 'TIF', 'TIFF'))] # Si queremos obtener imágenes con diferentes extensiones, las cambiamos aquí.

        if exclude_patterns:
            images = [img for img in images if not any(pattern in img for pattern in exclude_patterns)]

        return images

    def get_tmcs_from_dir(self, input_folder: str) -> list[str]:
        """
        Función que obtiene los archivos tmc del directorio de entrada.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        """
        files = os.listdir(input_folder)
        tmcs = [file for file in files if file.endswith(('TMC', 'tmc'))]   # Si queremos obtener archivos con diferentes extensiones, las cambiamos aquí.
        return tmcs

    def get_logs_from_dir(self, input_folder: str) -> list[str]:
        """
        Función que obtiene los archivos log del directorio de entrada.

        Arguments:
        ---------
        - input_folder - carpeta de entrada.
        """
        files = os.listdir(input_folder)
        logs = [file for file in files if os.path.splitext(file)[1] == ".log"]   # Si queremos obtener archivos con diferentes extensiones, las cambiamos aquí.
        return logs

    def extract_number_from_string(self, input_string: str) -> str:
        """
        Función que extrae los números que existen dentro de un string, junto con "-" y ".", para tener en cuenta los negativos.

        Arguments:
        ---------
        - input_string - string de entrada del que queremos.
        """
        return ''.join([n for n in input_string if n.isdigit() or n=="-"or n=="." ])


    def prepare_output_folder(self, output_folder: str, words_list: list[str]) -> None:
        """
        Función que gestiona la carpeta de salida, comprobando que las carpetas que se encuentran en la lista de entrada existen. Si no es así las crea.

        Arguments:
        ---------
        - output_folder - carpeta de salida.
        - words_list - lista de carpetas para comprobar y, si no existen, crear.
        """
        list_dir = os.listdir(output_folder)
        for w in words_list:
            if w not in list_dir:
                os.makedirs(os.path.join(output_folder, w))  # Crea carpeta si no existe

    def get_nombres_columnas(self, columns_list: list[str]) -> dict[str, str]:
        """
        Función que devuelve un diccionario con clave, la variable que quiero leer y el valor de esa variable en el idioma del estadillo

        Arguments:
        ---------
        - columns_list: los valores de las columnas de un dataframe que se comprobarán.
        """

        EN = {"Empresa":"Enterprise","Trabajo":"Project","Fecha":"Date","Piloto":"Pilot","Equipo_de_vuelo":"Flight_equipment","Pitch":"Pitch",
              "Hora_de_inicio":"Initial_hour","Hora_final":"Final_hour","PB":"PB","Vuelo":"Flight","Desplazado":"Displaced","Vel_vuelo":"Flight_speed",
              "Alt_vuelo":"Flight_height","Vel_de_aire":"Air_speed","Temp_aire":"Air_temp","Nubes":"Clouds","Radiacion":"Radiation","Tiempo_vuelo":"Flight_time",
              "Dist_Recorrida":"Dist_Travelled","Set_Bat_1":"Set_Bat_1","Set_Bat_2":"Set_Bat_2","Set_Bat_3":"Set_Bat_3","Volt_inicial":"Initial_volt",
              "Volt_final":"Final_volt","GB1/":"GB1/","GB2/":"GB2/","Anotaciones":"Annotations","Termica":"REC","RGB":"RGB","Cali_Ini":"Initial_Cali"
              ,"Cali_Final":"Final_Cali","Tipologia":"Typology","Vuelo_abortado":"Flight_aborted"}

        ES = {"Empresa":"Empresa","Trabajo":"Trabajo","Fecha":"Fecha","Piloto":"Piloto","Equipo_de_vuelo":"Equipo_de_vuelo","Pitch":"Pitch",
              "Hora_de_inicio":"Hora_de_inicio","Hora_final":"Hora_final","PB":"PB","Vuelo":"Vuelo","Desplazado":"Desplazado","Vel_vuelo":"Vel_vuelo",
              "Alt_vuelo":"Alt_vuelo","Vel_de_aire":"Vel_de_aire","Temp_aire":"Temp_aire","Nubes":"Nubes","Radiacion":"Radiacion","Tiempo_vuelo":"Tiempo_vuelo",
              "Dist_Recorrida":"Dist_Recorrida","Set_Bat_1":"Set_Bat_1","Set_Bat_2":"Set_Bat_2","Set_Bat_3":"Set_Bat_3","Volt_inicial":"Volt_inicial",
              "Volt_final":"Volt_final","GB1/":"GB1/","GB2/":"GB2/","Anotaciones":"Anotaciones","Termica":"Termica","RGB":"RGB","Cali_Ini":"Cali_Ini"
              ,"Cali_Final":"Cali_Final","Tipologia":"Tipologia","Vuelo_abortado":"Vuelo_abortado"}

        if "Vuelo" in columns_list:  # Comprobamos que en la lista de los nombres de las columnas está Vuelo (nombres por lo tanto en español), si no lo está,
            # por ahora será inglés la otra opción.
            return ES
        else:
            return EN

    def get_program_dir_installation(self, program_name: str):
        """
        Returns the installation directory for the specified program_name. No siempre funciona, pero puede ser útil para otros desarrollos.

        Arguments:
        ---------
        - program_name - nombre del programa que se quiere buscar.
        """
        # winreg solo existe en Windows: import diferido para que el resto del
        # módulo (y la app) pueda importarse en Linux/macOS. Fuera de Windows no
        # hay registro que consultar, así que degradamos al valor por defecto.
        try:
            import winreg
        except ImportError:
            self.organizer_logger.logger.info(
                f"winreg no disponible en esta plataforma; no se puede localizar {program_name}."
            )
            return None

        # Open the uninstall registry key
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall")

        # Get the number of subkeys (installed programs)
        num_subkeys = winreg.QueryInfoKey(key)[0]

        # Loop through each subkey
        for i in range(num_subkeys):
            # Get the name of the subkey
            subkey_name = winreg.EnumKey(key, i)

            # Open the subkey
            subkey = winreg.OpenKey(key, subkey_name)

            # Get the display name and installation directory values
            try:
                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                install_dir = winreg.QueryValueEx(subkey, "InstallLocation")[0]
            except OSError:
                # If the DisplayName or InstallLocation values don't exist, skip this subkey
                continue

            if display_name.lower() == program_name.lower():
                self.organizer_logger.logger.info(f"El directorio de instalación para {program_name} es {install_dir}")
                return install_dir


        # If the program_name is not found, return None
        self.organizer_logger.logger.info(f"{program_name} no está instalado en este ordenador.")
        return None

    def rename_log(self, nombre_archivo):
        """
        Renombra el log con otro nombre si ya existe, utilizando números correlativos.

        Arguments:
        ---------
        - nombre_archivo - nombre del archivo que no se quiere sobrescribir.
        """
        # Verificar si el archivo ya existe
        if os.path.exists(nombre_archivo):
            # Obtener el nombre y la extensión del archivo
            nombre, extension = os.path.splitext(nombre_archivo)

            # Inicializar un contador
            contador = 1

            # Generar un nuevo nombre de archivo con un número correlativo
            nuevo_nombre = f"{nombre}_{contador}{extension}"

            # Mientras el archivo con el nuevo nombre ya exista, seguir aumentando el contador
            while os.path.exists(nuevo_nombre):
                contador += 1
                nuevo_nombre = f"{nombre}_{contador}{extension}"

            # Renombrar el archivo con el nuevo nombre
            # os.rename(nombre_archivo, nuevo_nombre)
            self.organizer_logger.logger.info(f"El archivo {nombre_archivo} ha sido renombrado como {nuevo_nombre}")
            return nuevo_nombre

        return nombre_archivo

    def return_thermoviewer_path(self) -> str:
        """
        Función para encontrar la ruta del Thermoviewer.
        A priori se añaden solo dos posibles Paths al programa ThermoViewer, si el programa está en otros distintos el script se interrumpirá
        """

        ruta_thermoviewer = r"C:/Program Files (x86)/ThermoViewer/ThermoViewer.exe"
        if not os.path.exists(ruta_thermoviewer):
            ruta_thermoviewer = r"C:/Windows/ThermoViewer/ThermoViewer.exe"
            if not os.path.exists(ruta_thermoviewer):
                self.organizer_logger.logger.info('ERROR: NO SE HA ENCONTRADO INSTALACION DEL PROGRAMA THERMOVIEWER')
        return ruta_thermoviewer


    def check_size(self, file: str, min_size: int) -> bool:
        """
        Comprueba si el tamaño del archivo de entrada es mayor que el tamaño mínimo del argumento. Si es mayor, devuelve un True y si es menor, devuelve un False.

        Arguments:
        ---------
        - file - nombre del archivo del que se quiere comparar el tamaño.
        - min_size - tamaño mínimo que tiene que tener el archivo a comparar.
        """
        size_file = os.path.getsize(os.path.join(file))
        self.organizer_logger.logger.info("El tamaño del archivo {0} es: {1}".format(file, str(size_file)))
        if size_file > min_size:
            return True
        else:
            return False

    def contar_imagenes_or_tmc(self, folder: str, tmc: bool = False, exclude_patterns: list[str] = None, exclude_folders: list[str] = None) -> int:
        """
        Cuenta las imágenes que hay dentro de todos los directorios que existen en la carpeta de entrada.
        Devuelve el número de imágenes existentes dentro de la carpeta de entrada y de todos sus subdirectorios.

        Arguments:
        ---------
        - folder - nombre de la carpeta en la que se quieren contar las imágenes.
        - tmc - si True, cuenta archivos TMC en lugar de imágenes.
        - exclude_patterns - lista de strings que si están presentes en el nombre del archivo, lo excluyen del conteo.
        - exclude_folders - lista de nombres de carpetas que se excluirán del recorrido junto con todos sus subdirectorios.
        """
        excluded_folders_set = set(exclude_folders) if exclude_folders else set()
        # SIN_ORDENAR es una carpeta de aparcado de imágenes huérfanas (fuera del
        # estadillo): nunca es contenido válido que deba contarse en el total
        # esperado de ninguna fase. Excluirla siempre evita el falso "no hay
        # correspondencia entre número inicial y final" en las fases posteriores
        # a la Estructura de carpetas (que cuentan sobre output_folder).
        excluded_folders_set.add("SIN_ORDENAR")
        contador = 0
        for ruta, directorios, archivos in os.walk(folder):
            directorios[:] = [d for d in directorios if d not in excluded_folders_set]
            for archivo in archivos:
                if exclude_patterns and any(pattern in archivo for pattern in exclude_patterns):
                    continue
                if not tmc and archivo.endswith(('jpg', 'png', 'JPG')):  # mismo predicado que get_images_from_dir (evita descuadre total/procesadas → falso "no correspondencia" en fase Separación)
                    contador += 1
                elif tmc and archivo.endswith(('tmc', 'TMC')):
                    contador += 1
        return contador

    def change_to_rational(self, number):
        """convert a number to rational
        Keyword arguments: number
        return: tuple like (1, 2), (numerator, denominator)
        """
        f = Fraction(str(number))
        return (f.numerator, f.denominator)

    def check_suffix_within_the_name(self, file_name: str, suffix_list: str):
        """Esta función comprueba si alguno de las terminaciones que se encuentran en la lista están incluidas dentro del nombre del archivo

        Arguments:
        ---------
        file_name: el nombre del archivo que se quiere comprobar
        suffix_list: la lista de terminaciones que se cubren desde el interfaz
        """
        for suffix in tuple(suffix_list.rsplit(",")):
            position = file_name.find(suffix)
            if position != -1:
                return file_name[:position + len(suffix)] # Devuelve las letras del archivo desde 0 hasta la última letra del sufijo. No devuelve lo que hay posteriormente.
        return file_name

    def logging_time(self, progress_callback = None, start_time: float = 0.0, start: bool = True):
        if start:
            return time.perf_counter()
        else:
            processed_time = time.perf_counter() - start_time

            # Calculamos horas, minutos y segundos completos.
            hours = int(processed_time // 3600)
            minutes = int((processed_time % 3600) // 60)
            seconds = int(processed_time % 60)

            # Construimos el mensaje según la duración.
            if hours > 0:
                progress_callback.emit(f"\nEl tiempo de procesado ha sido de {hours} horas, {minutes} minutos y {seconds} segundos.")
                return f"El tiempo de procesado ha sido de {hours} horas, {minutes} minutos y {seconds} segundos."
            elif minutes > 0:
                progress_callback.emit(f"\nEl tiempo de procesado ha sido de {minutes} minutos y {seconds} segundos.")
                return f"El tiempo de procesado ha sido de {minutes} minutos y {seconds} segundos."
            else:
                progress_callback.emit(f"\nEl tiempo de procesado ha sido de {processed_time:.2f} segundos.")
                return f"El tiempo de procesado ha sido de {processed_time:.2f} segundos."


"""Configuraciones inmutables que capturan el estado de la UI en el hilo GUI.

Módulo PURO (solo `dataclasses` de stdlib, sin dependencias de Qt) para que las funciones
de negocio de `gui.py` puedan recibir todos sus parámetros ya
resueltos (`RunConfig`) en vez de leer widgets `self.le_*`/`self.sb_*`/`self.cb_*`/`self.rb_*`
directamente desde el hilo del `Worker` (QThreadPool), que es indefinido en Qt.

`MainWindow._capture_run_config()` es el único punto que debe leer los widgets, y siempre
se ejecuta en el hilo GUI, antes de arrancar el `Worker`.
"""


@dataclass(frozen=True)
class CompressRgbsConfig:
    input_folder: str
    output_folder: str
    compress_level: int
    include_subfolders: bool


@dataclass(frozen=True)
class GenMetaLocationConfig:
    input_folder: str
    flight_height: float
    calculate_proyected_distance: bool
    mode_meta: bool
    mode_location: bool
    mode_location_and_meta: bool


@dataclass(frozen=True)
class SplitImagesConfig:
    input_folder: str
    output_folder: str
    end_rgb_extra_files: str
    end_thermo_files: str
    end_rgb_files: str
    estad: str
    choose_mode_size: bool
    max_size: str
    compress_rgb: bool
    compress_level: int
    rename_images: bool
    mismatch_hours: int
    mismatch_minutes: int
    organize_images: bool
    cropping_rgb: bool
    cropping_mode_auto: bool
    crop_percentage: str
    gen_meta_location: bool
    gen_thumbnails: bool
    seconds_range: float
    include_v: bool
    calculate_proyected_distance: bool
    flight_height: float
    gen_thumbnails_rotate_90: bool
    gen_thumbnails_add_to_angle: int
    gen_thumbnails_max_error: int
    gen_thumbnails_subs_to_angle: int
    choose_mode_auto: bool
    gen_thumbnails_rgb: bool
    gen_thumbnails_termica: bool
    convert_to_tif: bool
    convert_to_tif_dron_selector: str
    convert_to_tif_emissivity: float
    convert_to_tif_humidity: float
    convert_to_tif_temp_auto: int
    convert_to_tif_up_temperature: float
    convert_to_tif_low_temperature: float
    convert_to_tiff_rotate_90: bool
    convert_to_tiff_rotate_minus_90: bool
    convert_to_tiff_rotate_auto: bool
    convert_to_tif_solo_seleccion_atom: bool
    convert_to_tif_create_gray_scale_images: bool


@dataclass(frozen=True)
class GenThumbnailsConfig:
    input_folder: str
    rgb: bool
    termica: bool
    termica_2: bool
    add_to_angle: int
    subs_to_angle: int
    max_error: int
    choose_mode_auto: bool
    rotate_90: bool
    aerotools_input_folder: str
    aerotools_rgb: bool
    aerotools_termica: bool
    aerotools_add_to_angle: int
    aerotools_subs_to_angle: int
    aerotools_max_error: int


@dataclass(frozen=True)
class RenameImagesConfig:
    output_folder: str


@dataclass(frozen=True)
class ExtractionConfig:
    folder: str
    output_folder: str
    estad: str
    auto: bool
    pre_extraction: bool
    extraction: bool
    temp_max: int
    temp_min: int


@dataclass(frozen=True)
class GenStructFolderConfig:
    output_folder: str
    estad: str
    organize_images: bool
    include_v: bool
    seconds_range: float
    mismatch_hours: int
    mismatch_minutes: int


@dataclass(frozen=True)
class RgbAerotoolsConfig:
    input_folder: str
    output_folder: str
    estad: str
    logs_folder: str
    compress_rgb: bool
    compress_level: int
    geotagging: bool
    organize_images: bool
    rename_images: bool
    mismatch_hours: int
    mismatch_minutes: int
    seconds_range_image_estad: float
    seconds_range_log_estad: float


@dataclass(frozen=True)
class ManualGeotaggingConfig:
    select_images_folder: str
    select_log: str
    logs_folder: str
    estad: str
    rgb_aerotools_logs_folder: str


@dataclass(frozen=True)
class ConvertToTifConfig:
    input_folder: str
    create_gray_scale_images: bool
    solo_seleccion_atom: bool
    temp_auto: int
    emissivity: float
    humidity: float
    low_temperature: float
    up_temperature: float
    rotate_90: bool
    rotate_minus_90: bool
    rotate_auto: bool
    dron_selector: str


@dataclass(frozen=True)
class RgbCroppingConfig:
    output_folder: str
    choose_mode_auto: bool
    crop_percentage: str


@dataclass(frozen=True)
class TifRotatingConfig:
    input_folder_2: str
    rotate_180: bool
    rotate_90: bool
    rotate_minus_90: bool


@dataclass(frozen=True)
class RunConfig:
    compress_rgbs: CompressRgbsConfig
    gen_meta_location: GenMetaLocationConfig
    split_images: SplitImagesConfig
    gen_thumbnails: GenThumbnailsConfig
    rename_images: RenameImagesConfig
    extraction: ExtractionConfig
    gen_struct_folder: GenStructFolderConfig
    rgb_aerotools: RgbAerotoolsConfig
    manual_geotagging: ManualGeotaggingConfig
    convert_to_tif: ConvertToTifConfig
    rgb_cropping: RgbCroppingConfig
    tif_rotating: TifRotatingConfig


def can_start(busy: bool, active_threads: int) -> bool:
    """
    Determina si se puede lanzar un nuevo procesado.

    Arguments:
    ---------
    - busy - flag manual que indica si MainWindow considera que ya hay un procesado en curso.
    - active_threads - QThreadPool.globalInstance().activeThreadCount() (o el pool de la instancia), número de hilos actualmente ejecutando un Worker.
    """
    return not busy and active_threads == 0


class StopGate:
    """
    Puerta que gestiona el flag de parada de un procesado completo (un run_thread), garantizando
    que solo se resetea a False UNA vez al inicio de dicho procesado (arm()), y que ninguna llamada
    intermedia durante las subfases puede pisar un stop=True ya solicitado por el usuario.
    """

    def __init__(self) -> None:
        self._stop = False
        self._armed = False

    def arm(self) -> None:
        """
        Prepara la puerta para un nuevo procesado. Debe llamarse UNA vez al arrancar (equivalente
        a prepare_log:1932). Si ya está armada (hay un procesado en curso y alguna subfase
        intermedia vuelve a llamar a esta función, como ocurre hoy en 17 puntos de
        gui.py), es un no-op: no resetea `self._stop`.
        """
        if not self._armed:
            self._stop = False
            self._armed = True

    def request_stop(self) -> None:
        """Marca que el usuario ha pulsado 'Parar'. Persiste hasta el próximo disarm()."""
        self._stop = True

    def is_stopped(self) -> bool:
        """Devuelve el estado actual del flag de parada."""
        return self._stop

    def disarm(self) -> None:
        """
        Se llama al terminar TODO el run_thread (thread_complete), para permitir un arm() limpio
        en el siguiente procesado que lance el usuario.
        """
        self._armed = False


def run_batch(items, worker_fn, worker_args_fn, on_progress=None, max_workers=None):
    """
    Reparte `items` en un ProcessPoolExecutor, ejecutando worker_fn(*worker_args_fn(item)) para
    cada uno. Un item que lanza excepción NO tumba el lote: se captura, se registra, y se sigue
    con los demás. Llama a on_progress(pct) tras completar cada item.

    Arguments:
    ---------
    - items - lista de items a procesar.
    - worker_fn - función de módulo (picklable) a ejecutar en cada proceso worker.
    - worker_args_fn - función que, dado un item, devuelve la tupla de argumentos posicionales
      para worker_fn.
    - on_progress - callback opcional invocado con el porcentaje (0-100) tras cada item completado.
    - max_workers - número de procesos del pool; None delega en ProcessPoolExecutor.

    Returns:
    --------
    dict con "results" (lista de resultados de los items exitosos) y "errors"
    (lista de tuplas (item, str(exception)) de los que fallaron).
    """
    results = []
    errors = []
    total = len(items)
    if total == 0:
        return {"results": results, "errors": errors}

    current = 0
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(worker_fn, *worker_args_fn(item)): item for item in items
        }
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            current += 1
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append((item, str(exc)))
            if on_progress is not None:
                on_progress(safe_pct(current, total))

    return {"results": results, "errors": errors}
