'''-------------------------------------------------------------------------------
 Nombre:    ATOM Organizer
 Proposito: Programa que integra diferentes funcionalidades para trabajar con
            la información generada en los vuelos.

 Versión 1.x-2.1.5 (2023):  Manuel Álvarez Souto
 Versión 3.x (2026- ):      Rodrigo Sáez Escobar — Aerotools-UAV
                            Rediseño y desarrollo: nueva interfaz webview,
                            pipeline de rotación, conversor DJI, empaquetado
                            multiplataforma y actualizador automático.

 Creado:     2023
 Copyright:  (c) 2023-2026. All rights reserved. Aerotools-UAV
 Licencia:
 Interprete: Python 3.10+

 Versión:    la fuente única es version.py (__version__). Este módulo es la GUI
             Qt original; desde la 3.x el producto que se distribuye es la
             interfaz webview (app_webview.py), que reutiliza esta lógica.
-------------------------------------------------------------------------------'''

from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import QObject, QRunnable, Signal, Slot, QThreadPool
from qt_designer_files import atom_organizer as aero_gui
from qt_designer_files import log_window_ui as log_gui
from qt_designer_files import config_window_ui as config_gui
import re
import external_tools as config
import pipeline as ci
import exif as meta_location
import pipeline as split
import pipeline as gsf
import utils
from utils import StopGate
import pipeline as e
import pipeline as rgb_processing
import pipeline as rgb_cropping
from utils import OrganizerLogger
from utils import (
    RunConfig,
    CompressRgbsConfig,
    GenMetaLocationConfig,
    SplitImagesConfig,
    GenThumbnailsConfig,
    RenameImagesConfig,
    ExtractionConfig,
    GenStructFolderConfig,
    RgbAerotoolsConfig,
    ManualGeotaggingConfig,
    ConvertToTifConfig,
    RgbCroppingConfig,
    TifRotatingConfig,
)
from utils import can_start
from external_tools import resource_path
# Las fases del pipeline (split_images, gen_thumbnails, do_extraction…) viven
# fuera de este módulo para que el host headless pueda ejecutarlas sin PySide6.
from atom_core.phases import PipelinePhasesMixin
import multiprocessing
import sys
import os

import traceback, sys

from qt_designer_files.qt_models import TableModel

import pandas as pd
import logging

import configparser

# --- Tema global QSS para ATOM Organizer — clonado del login de ATOM Suite -------------------
# Paleta y componentes copiados del frontend de ATOM Suite (naranja marca #EE763C sobre negro
# #0a0a0a, inputs/botones "glass"). El wordmark usa Space Grotesk 700 (cargada en _load_brand_font).
# Follow-up (Fase B): neutralizar los ~80 setStyleSheet inline del .ui generado que aún pisan esto.

try:                        # versión mostrada junto al wordmark en la cabecera
    from version import __version__ as APP_VERSION
except Exception:           # congelado sin version.py al alcance: no romper la GUI
    APP_VERSION = "0.0.0"
BACKGROUND = "#0a0a0a"      # fondo login ATOM Suite
ACCENT = "#EE763C"          # naranja marca (título "SUITE", acentos)
ACCENT_SOFT = "rgba(238,117,58,0.6)"
TEXT = "#f1f5f9"            # texto cuerpo ATOM Suite
MUTED = "#6b7280"          # gris subtítulo (gray-500)

# --- Escalado responsive (determinista, por tamaño de pantalla) -----------------------------
# La UI se dimensiona en "píxeles lógicos" (device-independent) y las fuentes en puntos: el
# escalado a cada monitor lo hace Qt vía el devicePixelRatio que reporta Wayland (portátil 1.25,
# 4K 2.0), no un multiplicador propio. Por eso UI_SCALE se queda en 1.0 — S() es prácticamente
# identidad y solo sigue existiendo para no tocar las ~200 llamadas del código. Al arrastrar la
# ventana entre monitores, Qt la reescala sola. (En X11/xcb Qt no distingue DPI por monitor, así
# que el arranque usa Wayland; ver bloque main.)
UI_SCALE = 1.0

def S(v: float) -> int:
    """Tamaño en píxeles lógicos (Qt lo escala por DPI). Identidad salvo que se fuerce UI_SCALE."""
    return max(1, round(v * UI_SCALE))

def scale_qss(qss: str, k: float) -> str:
    """Multiplica todos los valores `Npx` de una hoja QSS por el factor de escala."""
    if abs(k - 1.0) < 1e-3:
        return qss
    import re
    return re.sub(r"(\d+)px", lambda m: f"{max(1, round(int(m.group(1)) * k))}px", qss)

DARK_QSS = f"""
QMainWindow, QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT};
    font-size: 13px;
}}
QWidget#brand_header {{
    background: transparent;
    border: none;
}}
QTabWidget::pane {{
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    top: -1px;
}}
QTabBar::tab {{
    background: rgba(255,255,255,0.04);
    color: {MUTED};
    border: 1px solid rgba(255,255,255,0.08);
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 7px 18px;
    margin-right: 3px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    background: rgba(238,117,58,0.10);
    border-color: rgba(238,117,58,0.35);
}}
QGroupBox {{
    background-color: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
    margin-top: 1.2em;
    padding-top: 0.6em;
    color: {TEXT};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    color: {ACCENT};
    font-weight: 600;
}}
QPushButton {{
    background-color: rgba(255,255,255,0.05);
    color: {TEXT};
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 10px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {ACCENT_SOFT};
    color: #ffffff;
}}
QPushButton:disabled {{
    color: rgba(255,255,255,0.35);
    border-color: rgba(255,255,255,0.08);
}}
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: rgba(255,255,255,0.05);
    color: rgba(255,255,255,0.92);
    border: 2px solid rgba(255,255,255,0.20);
    border-radius: 10px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 2px solid {ACCENT_SOFT};
    background-color: rgba(255,255,255,0.08);
}}
QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: rgba(255,255,255,0.05);
    color: rgba(255,255,255,0.92);
    border: 2px solid rgba(255,255,255,0.18);
    border-radius: 8px;
    padding: 4px 8px;
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT_SOFT};
}}
QCheckBox, QRadioButton {{
    color: {TEXT};
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px; height: 18px;
    border: 2px solid rgba(255,255,255,0.30);
    border-radius: 5px;
    background: rgba(255,255,255,0.04);
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QLabel {{ color: {TEXT}; background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.15); border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(238,117,58,0.5); }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QMenuBar {{ background: {BACKGROUND}; color: {TEXT}; }}
QMenuBar::item:selected {{ background: rgba(238,117,58,0.15); }}
QMenu {{ background: #141414; color: {TEXT}; border: 1px solid rgba(255,255,255,0.1); }}
QMenu::item:selected {{ background: rgba(238,117,58,0.15); }}
"""
# --- fin tema global ----------------------------------------------------------------------


class WorkerSignals(QObject):
    '''
    Defines the signals available from a running worker thread.

    Supported signals are:

    finished
        No data

    error
        tuple (exctype, value, traceback.format_exc() )

    result
        object data returned from processing, anything

    progress
        int indicating % progress

    '''
    finished = Signal()
    error = Signal(tuple)
    result = Signal(object)
    progress = Signal(str) # Información del proceso en string para mostrar en la ventana del log.
    progress_bar = Signal(int) # Señal que indica el valor de progreso del proceso actual en la ventana del log.
    progress_summarize = Signal(str) # Señal para enviar texto al widget de resumen desde el hilo de trabajo.

class Worker(QRunnable):
    '''
    Worker thread

    Inherits from QRunnable to handler worker thread setup, signals and wrap-up.

    :param callback: The function callback to run on this worker thread. Supplied args and
                        kwargs will be passed through to the runner.
    :type callback: function
    :param args: Arguments to pass to the callback function
    :param kwargs: Keywords to pass to the callback function

    '''

    def __init__(self, fn, *args, **kwargs):
        super(Worker, self).__init__()

        # Store constructor arguments (re-used for processing)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

        # Add the callback to our kwargs
        self.kwargs['progress_callback'] = self.signals.progress
        self.kwargs['progress_bar'] = self.signals.progress_bar
        self.kwargs['progress_summarize'] = self.signals.progress_summarize

    @Slot()
    def run(self):
        '''
        Initialise the runner function with passed args, kwargs.
        '''

        # Retrieve args/kwargs here; and fire processing using them
        try:
            result = self.fn(*self.args, **self.kwargs)
        except:
            traceback.print_exc()
            exctype, value = sys.exc_info()[:2]
            self.signals.error.emit((exctype, value, traceback.format_exc()))
        else:
            self.signals.result.emit(result)  # Return the result of the processing
        finally:
            self.signals.finished.emit()  # Done

class LogWindow(QtWidgets.QWidget, log_gui.Ui_Form):
    """
    Clase que muestra la ventana de log con información enviada durante el proceso.
    
    """
    def __init__(self) -> None:
        super(LogWindow, self).__init__()
        self.setupUi(self)
        self.stop_gate = StopGate()

        self.bt_stop_process.clicked.connect(self.stop_process)
        self.bt_clean_logs.clicked.connect(self.clean_logs)

        self.cb_enable_stop_process.toggled.connect(self.modify_qt_objects)
    
    def set_obj(self, obj: object):
        """
        Función que setea un objeto que lleva a cabo un proceso específico.
        Las funciones a llamar siempre existirán en cada una de las clases, de modo que no es necesario tener
        un objeto para cada clase de procesado.
        
        Arguments:
        ---------
        - obj - objeto que lleva a cabo un procesado específico. Será un split_images_obj, meta_location_obj, compress_image_obj...
        """
        self.obj = obj
    
    def stop_process(self):
        """Para el proceso que actualmente se está llevando a cabo cambiando una variable del objeto, la cual se está comprobando en cada bucle"""
        self.te_summarize_process.appendPlainText("\nBOTÓN PARAR PROCESO PULSADO\n")
        self.stop_gate.request_stop()
        self.obj.set_stop(True)

    def enable_process(self):
        """Arma la puerta de parada (solo tiene efecto real la primera vez tras el último disarm())
        y propaga el estado actual del flag al objeto de procesado enganchado."""
        self.stop_gate.arm()
        self.obj.set_stop(self.stop_gate.is_stopped())

    def modify_qt_objects(self) -> None:
        """Cambia el nombre de ciertos elementos del texto cuando se marcan las casillas correspondientes, de modo que facilite la comprensión de lo que va a hacer
        la aplicación"""
        if self.cb_enable_stop_process.isChecked():
            self.bt_stop_process.setEnabled(True)
            self.bt_stop_process.setStyleSheet("background-color: rgb(28, 28, 29);\n"
"border-radius: 9px;\n"
"border: 2px solid rgb(238, 118, 60);\n"
"color: rgb(255, 255, 255);")
        else:
            self.bt_stop_process.setEnabled(False)
            self.bt_stop_process.setStyleSheet("background-color: rgb(28, 28, 29);\n"
"border-radius: 9px;\n"
"border: 2px solid rgb(144, 144, 144);\n"
"color: rgb(144,144,144);")
    
    def clean_logs(self):
        self.te_log.clear()
        self.te_summarize_process.clear()

class ConfigWindow(QtWidgets.QWidget, config_gui.Ui_Form):
    """
    Clase que muestra la ventana de configuración.
    
    """
    def __init__(self, config_obj) -> None:
        super(ConfigWindow, self).__init__()
        self.setupUi(self)
        self.general_config = config_obj
        self.config_file = ""
        self.bt_load_config.clicked.connect(self.load_new_config)
        self.bt_save_config.clicked.connect(self.save_new_config)
        self.bt_choose_thermoviewer_exe.clicked.connect(self.select_ruta_thermoviewer)
        self.bt_modify_add_cropping_values.clicked.connect(self.add_model_percentage_to_list)
        self.list_rgb_cropping_percentage_by_models.itemClicked.connect(self.load_selected_item_to_fields)
        self.load_new_config()

    def load_new_config(self):
        """Función que carga un archivo de configuración y que muestra los parámetros en la ventana de configuración."""
        self.config_file, _ = QtWidgets.QFileDialog.getOpenFileName()
        if self.config_file == "":
            return
        valores = config.load_config_or_default(self.config_file)
        self.le_choose_thermoviewer_exe.setText(valores["ruta_thermoviewer"])
        self.load_percentage_by_models()
        # print(configuracion.sections())
        # print(configuracion["paths"]["ruta_thermoviewer"])
    
    def save_new_config(self):
        """Función que guarda un archivo de configuración con los datos mostrados en el archivo de configuración."""
        self.config_file, _ = QtWidgets.QFileDialog.getSaveFileName()
        configuracion = configparser.ConfigParser()
        # Evitar que configparser convierta las claves a minúsculas
        configuracion.optionxform = str
        configuracion['paths'] = {'ruta_thermoviewer' : self.le_choose_thermoviewer_exe.text()}
        
        # Leer los valores de la lista y crear la sección percentage_by_models
        percentage_by_models_dict = {}
        for i in range(self.list_rgb_cropping_percentage_by_models.count()):
            item_text = self.list_rgb_cropping_percentage_by_models.item(i).text()
            # El formato es "MODELO: XX%", necesitamos extraer modelo y porcentaje
            if ": " in item_text:
                model, percentage_str = item_text.split(": ", 1)
                # Quitar el símbolo % del porcentaje
                percentage = percentage_str.rstrip('%')
                percentage_by_models_dict[model.upper()] = percentage
        
        # Añadir la sección al archivo de configuración si hay datos
        if percentage_by_models_dict:
            # print(percentage_by_models_dict)
            configuracion['percentage_by_models'] = percentage_by_models_dict
        
        with open(self.config_file, 'w') as archivoconfig:
            configuracion.write(archivoconfig)

        self.general_config.load_new_config(self.config_file)

    def select_ruta_thermoviewer(self) -> None:
        """Función que muestra una ventana de diálogo para elegir un archivo. El path se mostrará en QLineEdit correspondiente."""
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName()
        self.le_choose_thermoviewer_exe.setText(file_name)
    
    def load_percentage_by_models(self, config_file_path: str = None) -> None:
        """
        Lee la sección percentage_by_models del archivo Config.ini y añade cada entrada
        como una línea en self.list_rgb_cropping_percentage_by_models.

        Args:
            config_file_path (str): Ruta al archivo de configuración. Por defecto resource_path("config", "Config.ini")
        """
        if config_file_path is None:
            config_file_path = resource_path("config", "Config.ini")
        configuracion = configparser.ConfigParser()
        configuracion.read(config_file_path)
        
        # Limpiar la lista antes de cargar nuevos datos
        self.list_rgb_cropping_percentage_by_models.clear()
        
        # Verificar si existe la sección percentage_by_models
        if "percentage_by_models" in configuracion:
            for model, percentage in configuracion["percentage_by_models"].items():
                # Formatear la línea como "MODELO: XX%"
                line_text = f"{model.upper()}: {percentage}%"
                self.list_rgb_cropping_percentage_by_models.addItem(line_text)
    
    def add_model_percentage_to_list(self) -> None:
        """
        Lee los valores de self.le_model y self.le_percentage. Si el modelo ya existe en la lista,
        actualiza su porcentaje. Si no existe, lo añade como una nueva línea.
        """
        model = self.le_model.text().strip()
        percentage = self.le_percentage.text().strip()
        
        # Validar que ambos campos tengan contenido
        if not model or not percentage:
            return
        
        # Buscar si ya existe una línea con el mismo modelo
        model_upper = model.upper()
        found = False
        for i in range(self.list_rgb_cropping_percentage_by_models.count()):
            item = self.list_rgb_cropping_percentage_by_models.item(i)
            item_text = item.text()
            
            # Extraer el modelo de la línea existente
            if ": " in item_text:
                existing_model = item_text.split(": ", 1)[0]
                
                # Si el modelo coincide, actualizar el porcentaje
                if existing_model == model_upper:
                    new_text = f"{model_upper}: {percentage}%"
                    item.setText(new_text)
                    found = True
                    break
        
        # Si no se encontró el modelo, añadir una nueva línea
        if not found:
            line_text = f"{model_upper}: {percentage}%"
            self.list_rgb_cropping_percentage_by_models.addItem(line_text)
        
        # Limpiar los campos después de añadir/actualizar
        self.le_model.clear()
        self.le_percentage.clear()
    
    def load_selected_item_to_fields(self, item) -> None:
        """
        Carga los valores de la línea seleccionada en self.list_rgb_cropping_percentage_by_models
        en los campos self.le_model y self.le_percentage.
        
        Args:
            item: El item seleccionado del QListWidget
        """
        # Obtener el texto del item seleccionado
        item_text = item.text()
        
        # El formato es "MODELO: XX%", extraer modelo y porcentaje
        if ": " in item_text:
            model, percentage_str = item_text.split(": ", 1)
            # Quitar el símbolo % del porcentaje
            percentage = percentage_str.rstrip('%')
            
            # Cargar los valores en los campos de texto
            self.le_model.setText(model)
            self.le_percentage.setText(percentage)

class DotGridWidget(QtWidgets.QWidget):
    """Fondo negro con retícula de puntos sutil, como el login de ATOM Suite (DotGridBackground)."""
    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor("#0a0a0a"))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(255, 255, 255, 22))
        step = S(26)
        dot = S(2)
        for y in range(0, self.height(), step):
            for x in range(0, self.width(), step):
                p.drawEllipse(x, y, dot, dot)
        p.end()


class MainWindow(QtWidgets.QMainWindow, aero_gui.Ui_MainWindow, PipelinePhasesMixin):
    """
    Clase que representa el comportamiento de la ventana principal de la aplicación que representa el comportamiento de la ventana principal,
    completamente aislada del diseño, la cual está en el archivo aero_preprocesser_window.py
    Esta clase conecta los botones a funciones específicas, crea el modelo de las diferentes vistas si las hubiera y fija el modelo.

    Methods
    -------
    select_input_folder(self, input_folder_type) -> None
    select_output_folder(self, output_folder_type) -> None
    call_to_compress_image(self) -> None
    gen_meta_location(self) -> None
    change_button_name(self) -> None
    split_images(self) -> None
    """
    def __init__(self, *args, obj=None, **kwargs) -> None:
        super(MainWindow, self).__init__(*args, **kwargs)
        self.setupUi(self)
        # El .ui generado por Qt Designer lleva "ATOM Organizer v2.1.5" escrito a
        # mano en el título (qt_designer_files/atom_organizer.py), así que la
        # ventana anunciaba una versión de hace tres años. Se pisa con la fuente
        # única en vez de editar un fichero generado, que se regenera y vuelve.
        try:
            from version import __version__
            self.setWindowTitle(f"ATOM Organizer v{__version__}")
        except Exception:
            pass
        self._load_brand_font()
        self.setStyleSheet(scale_qss(DARK_QSS + self._extra_qss(), UI_SCALE))
        self._build_brand_header()

        self.threadpool = QThreadPool()
        # print("Multithreading with maximum %d threads" % self.threadpool.maxThreadCount())
        self._busy = False

        #%% Conectamos botones
        # Utilizamos funciones lambda para poder enviar parámetros a las funciones conectadas. Si no, las funciones no admitirían parámetros. De este modo sólo necesitamos una función
        # para crear un FileDialog y enviar la elección al objeto gráfico correspondiente.
        self.bt_gb_split_images_select_input_folder.clicked.connect(lambda: self.select_input_folder("split_images"))
        self.bt_gb_compress_rgbs_select_input_folder.clicked.connect(lambda: self.select_input_folder("compress_images"))
        self.bt_gb_generate_thumbnails_select_input_folder.clicked.connect(lambda: self.select_input_folder("generate_thumbnails"))
        self.bt_gb_gen_meta_location_select_input_folder.clicked.connect(lambda: self.select_input_folder("gen_meta_location"))
        self.bt_gb_rename_images_select_folder.clicked.connect(lambda: self.select_input_folder("rename_images"))
        self.bt_gb_extraction_select_folder.clicked.connect(lambda: self.select_input_folder("extraction"))
        self.bt_gb_rgb_aerotools_select_input_folder.clicked.connect(lambda: self.select_input_folder("rgb_aerotools"))
        self.bt_gb_generate_thumbnails_aerotools_select_input_folder.clicked.connect(lambda: self.select_input_folder("generate_thumbnails_aerotools"))
        self.bt_gb_manual_geotagging_select_images_folder.clicked.connect(lambda: self.select_output_folder("manual_geotagging"))
        self.bt_gb_convert_to_tif_select_folder.clicked.connect(lambda: self.select_input_folder("convert_to_tif"))
        self.bt_gb_cropping_rgb_select_folder.clicked.connect(lambda: self.select_input_folder("rgb_cropping"))
        self.bt_gb_rotate_tif_select_folder.clicked.connect(lambda: self.select_input_folder("tif_rotating"))
        
        self.bt_gb_split_images_select_output_folder.clicked.connect(lambda: self.select_output_folder("split_images"))
        self.bt_gb_compress_rgbs_select_output_folder.clicked.connect(lambda: self.select_output_folder("compress_images"))
        self.bt_gb_gen_folder_struct_select_output_folder.clicked.connect(lambda: self.select_output_folder("gen_struct"))
        self.bt_gb_gen_folder_struct_select_estad.clicked.connect(lambda: self.select_estadillo_for_gen_struct("gb_gen_folder_struct"))
        self.bt_gb_split_images_select_estad.clicked.connect(lambda: self.select_estadillo_for_gen_struct("gb_split_images"))
        self.bt_gb_extraction_select_estad.clicked.connect(lambda: self.select_estadillo_for_gen_struct("gb_pre_extract"))
        self.bt_gb_rgb_aerotools_select_estad.clicked.connect(lambda: self.select_estadillo_for_gen_struct("rgb_aerotools"))
        self.bt_gb_manual_geotagging_select_estad.clicked.connect(lambda: self.select_estadillo_for_gen_struct("manual_geotagging"))
        self.bt_gb_extraction_select_output_folder.clicked.connect(lambda: self.select_output_folder("extraction"))
        self.bt_gb_rgb_aerotools_select_output_folder.clicked.connect(lambda: self.select_output_folder("rgb_aerotools"))
        self.bt_gb_rgb_aerotools_select_logs_folder.clicked.connect(self.select_logs_folder)
        self.bt_gb_manual_geotagging_select_log.clicked.connect(self.select_log_for_manual_geotagging)
        self.bt_gb_manual_geotagging_select_logs_folder.clicked.connect(self.select_logs_folder_for_manual_geotagging)

        self.bt_do_compress.clicked.connect(lambda: self.run_thread("compress_image"))
        # bt_do_split (botón "Separar" original) quedó oculto tras el rediseño; su disparo directo
        # saltaría el volcado de _run_main_organize → NO se reconecta. El botón visible es self.main_run.
        self.bt_do_gen_meta_location.clicked.connect(lambda: self.run_thread("gen_meta_location"))
        self.bt_do_generate_thumbnails.clicked.connect(lambda: self.run_thread("gen_thumbnails"))
        self.bt_do_rename_images.clicked.connect(lambda: self.run_thread("rename_images"))
        self.bt_do_extraction.clicked.connect(lambda: self.run_thread("do_extraction"))
        self.bt_do_gen_struct.clicked.connect(lambda: self.run_thread("gen_struct"))
        self.bt_do_rgb_aerotools_processing.clicked.connect(lambda: self.run_thread("rgb_aerotools"))
        self.bt_do_generate_thumbnails_aerotools.clicked.connect(lambda: self.run_thread("gen_thumbnails_aerotools"))
        self.bt_do_manual_geotagging.clicked.connect(lambda: self.run_thread("manual_geotagging"))
        self.bt_do_convert_to_tif.clicked.connect(lambda: self.run_thread("convert_to_tif"))
        self.bt_do_rgb_cropping.clicked.connect(lambda: self.run_thread("rgb_cropping"))
        self.bt_do_rotate_tif.clicked.connect(lambda: self.run_thread("tif_rotating"))

        # Botones que lanzan un procesado vía run_thread: se deshabilitan mientras hay un Worker en curso
        # para evitar lanzar dos a la vez (ver run_guard.can_start).
        # self.main_run (botón visible de "Ejecutar organización") se añade en _build_main_screen,
        # una vez creado, para que también se deshabilite mientras hay un Worker en curso.
        self._launch_buttons = [
            self.bt_do_compress,
            self.bt_do_gen_meta_location,
            self.bt_do_generate_thumbnails,
            self.bt_do_rename_images,
            self.bt_do_extraction,
            self.bt_do_gen_struct,
            self.bt_do_rgb_aerotools_processing,
            self.bt_do_generate_thumbnails_aerotools,
            self.bt_do_manual_geotagging,
            self.bt_do_convert_to_tif,
            self.bt_do_rgb_cropping,
            self.bt_do_rotate_tif,
        ]

        # Estas funciones comentadas las uso para cando tengo que hacer debug, ya que las anteriores se ejecutan en hilos diferentes y no me permite
        # debuggear.
        # self.bt_do_split.clicked.connect(self.split_images)
        # self.bt_do_compress.clicked.connect(self.call_to_compress_image)
        # self.bt_do_gen_meta_location.clicked.connect(self.gen_meta_location)
        # self.bt_do_generate_thumbnails.clicked.connect(self.gen_thumbnails)
        # self.bt_do_rename_images.clicked.connect(self.rename_images)
        # self.bt_do_extraction.clicked.connect(self.do_extraction)
        # self.bt_do_gen_struct.clicked.connect(self.gen_struct_folder)
        # self.bt_do_rgb_aerotools_processing.clicked.connect(self.do_rgb_aerotools_processing)
        # self.bt_do_convert_to_tif.clicked.connect(self.do_convert_to_tif)
        # self.bt_do_rgb_cropping.clicked.connect(self.do_rgb_cropping)

        self.actionCargar_estadillo.triggered.connect(self.select_estadillo_for_showing)
        self.actionLog_debug.triggered.connect(lambda: self.enable_disable_logger(self.actionLog_debug.isChecked()))
        self.actionTest.triggered.connect(self.test)
        self.actionEditar_Config.triggered.connect(self.editar_config)
        self.actionCargar_config.triggered.connect(self.cargar_config)

        #%% Conectamos checkboxes
        self.cb_gb_split_images_compress_rgb.toggled.connect(self.modify_qt_objects)
        self.rb_gb_choose_mode_size.toggled.connect(self.modify_qt_objects)
        self.rb_gb_choose_mode_sufix.toggled.connect(self.modify_qt_objects)
        self.cb_gb_split_images_organize_images.toggled.connect(self.modify_qt_objects)
        self.cb_gb_gen_folder_struct_organize_images.toggled.connect(self.modify_qt_objects)
        self.cb_gb_extraction_auto.toggled.connect(self.modify_qt_objects)
        self.cb_gb_rgb_aerotools_compress_rgb.toggled.connect(self.modify_qt_objects)
        self.cb_gb_convert_to_tif_temp_auto.toggled.connect(self.modify_qt_objects)
        self.cb_gb_split_images_gen_meta_location.toggled.connect(self.modify_qt_objects)
        self.cb_gb_split_images_calculate_proyected_distance.toggled.connect(self.modify_qt_objects)
        self.cb_gb_gen_meta_location_calculate_proyected_distance.toggled.connect(self.modify_qt_objects)
        self.cb_gb_split_images_gen_thumbnails.toggled.connect(self.modify_qt_objects)
        self.cb_split_images_convert_to_tif.toggled.connect(self.modify_qt_objects)
        self.cb_split_images_convert_to_tif_temp_auto.toggled.connect(self.modify_qt_objects)
        self.cb_gb_split_images_cropping_rgb.toggled.connect(self.modify_qt_objects)

        self.rb_gb_cropping_rgb_choose_mode_auto.toggled.connect(self.modify_qt_objects)
        self.rb_gb_cropping_rgb_choose_mode_manual.toggled.connect(self.modify_qt_objects)
        self.rb_gb_split_images_choose_mode_cropping_mode_auto.toggled.connect(self.modify_qt_objects)
        self.rb_gb_gb_split_images_choose_cropping_mode_manual.toggled.connect(self.modify_qt_objects)
        self.rb_gb_split_images_choose_mode_auto.toggled.connect(self.modify_qt_objects)
        self.rb_gb_split_images_choose_mode_manual.toggled.connect(self.modify_qt_objects)
        self.rb_gb_generate_thumbnails_choose_mode_auto.toggled.connect(self.modify_qt_objects)
        self.rb_gb_generate_thumbnails_choose_mode_manual.toggled.connect(self.modify_qt_objects)

        #%% Creamos objetos de las clases necesarias
        self.organizer_logger_obj = OrganizerLogger(
            name="Organizer",
            max_bytes=50*1024*1024, backup_count = 7, create_file_handler = False)
        self.compress_image_obj = ci.CompressImage(self.organizer_logger_obj)
        self.meta_location_obj = meta_location.MetaLocation(self.organizer_logger_obj)
        self.split_images_obj = split.SplitImages(self.organizer_logger_obj)
        self.gen_struct_folder_obj = gsf.GenStructFolder(self.organizer_logger_obj)
        self.utils_obj = utils.Utils(self.organizer_logger_obj)
        self.extraction_obj = e.Extraction(self.organizer_logger_obj)
        self.config_obj = config.ReadLoadConfig()
        self.rgb_processing_obj = rgb_processing.RGBProcessing(self.organizer_logger_obj)
        self.rgb_cropping_obj = rgb_cropping.RGBCropping(self.organizer_logger_obj)
        
        # Creamos un objeto de LogWindow para mostrarla posteriormente al arrancar un proceso.
        self.new_log_gui = LogWindow()
    
        # Carga un archivo de configuración y se lee desde las diferentes funciones.
        self.config_obj.load_new_config(config._user_config_path())
        
        # Llamo a esta función para actualizar el estado de los checkboxes y su efecto.
        self.modify_qt_objects()

        # Crea el logger.
        # self.init_logger()
        self.init_new_logger()
        self.organizer_logger_obj.logger.info("Multithreading with maximum %d threads" % self.threadpool.maxThreadCount())

        # Pantalla principal rediseñada (estética ATOM Suite): card con los 4 controles + Modo avanzado.
        self._build_main_screen()
        self._install_dot_backdrop()
        if os.environ.get("ATOM_DEBUG_ADV"):
            self.main_adv_btn.setChecked(True)
            QtCore.QTimer.singleShot(600, lambda: self.page_scroll.verticalScrollBar().setValue(int(os.environ["ATOM_DEBUG_ADV"])))
    
    #%% Marca / estética ATOM Suite
    def _load_brand_font(self) -> None:
        """Carga Space Grotesk (OFL) empaquetada para el wordmark, igual que el login de ATOM Suite."""
        from PySide6.QtGui import QFontDatabase
        self._brand_family = "Space Grotesk"
        path = resource_path("assets", "fonts", "SpaceGrotesk-VariableFont.ttf")
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams:
                self._brand_family = fams[0]

    def _build_brand_header(self) -> None:
        """Inserta la cabecera de marca (icono + 'ATOM ORGANIZER') encima del QTabWidget, estilo ATOM Suite."""
        from PySide6 import QtGui
        header = QtWidgets.QWidget()
        header.setObjectName("brand_header")
        h = QtWidgets.QHBoxLayout(header)
        h.setContentsMargins(S(16), S(12), S(16), S(12))
        h.setSpacing(S(12))

        icon_path = resource_path("assets", "atom-icon.svg")
        if os.path.exists(icon_path):
            try:
                from PySide6.QtSvgWidgets import QSvgWidget
                svg = QSvgWidget(icon_path)
                svg.setFixedSize(S(72), S(72))
                h.addWidget(svg)
            except Exception:
                pass

        title = QtWidgets.QLabel(
            '<span style="color:#F4F4F5;">ATOM</span>'
            '<span style="color:#EE763C;"> ORGANIZER</span>'
        )
        f = QtGui.QFont(self._brand_family, S(48))
        f.setWeight(QtGui.QFont.Weight.Bold)
        f.setLetterSpacing(QtGui.QFont.SpacingType.PercentageSpacing, 106)
        title.setFont(f)
        h.addWidget(title)

        # Versión en pequeño, alineada a la base del wordmark.
        ver = QtWidgets.QLabel(f"v{APP_VERSION}")
        ver.setObjectName("brand_version")
        vf = QtGui.QFont(self._brand_family, S(13))
        vf.setWeight(QtGui.QFont.Weight.Medium)
        ver.setFont(vf)
        ver.setStyleSheet(f"color: {MUTED};")
        h.addSpacing(S(8))
        h.addWidget(ver, 0, QtCore.Qt.AlignmentFlag.AlignBottom)

        h.addStretch(1)

        # Segmented control (píldoras) para conmutar sección, en vez de la barra de menús.
        seg = QtWidgets.QFrame()
        seg.setObjectName("seg_control")
        sl = QtWidgets.QHBoxLayout(seg)
        sl.setContentsMargins(S(4), S(4), S(4), S(4))
        sl.setSpacing(S(4))
        self._seg_group = QtWidgets.QButtonGroup(self)
        self._seg_group.setExclusive(True)
        self._seg_buttons = {}
        for key, label in (("main", "Organizar"), ("aero", "AEROTOOLS"), ("otros", "OTROS EQUIPOS")):
            b = QtWidgets.QPushButton(label)
            b.setObjectName("seg_btn")
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(S(34))
            self._seg_group.addButton(b)
            sl.addWidget(b)
            self._seg_buttons[key] = b
        self._seg_buttons["main"].setChecked(True)
        self._seg_buttons["main"].clicked.connect(lambda: self._show_section(None))
        self._seg_buttons["aero"].clicked.connect(lambda: self._show_section(self.tab_aero_tools))
        self._seg_buttons["otros"].clicked.connect(lambda: self._show_section(self.tab_manual))
        h.addWidget(seg)

        # Botón ⚙ con las acciones útiles del menú viejo (estadillo / config / debug).
        gear = QtWidgets.QToolButton()
        gear.setObjectName("gear_btn")
        gear.setText("⚙")
        gear.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        gear.setFixedSize(S(36), S(36))
        gear.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(gear)
        menu.addAction(self.actionCargar_estadillo)
        menu.addSeparator()
        menu.addAction(self.actionCargar_config)
        menu.addAction(self.actionEditar_Config)
        menu.addSeparator()
        menu.addAction(self.actionLog_debug)
        menu.addAction(self.actionTest)
        gear.setMenu(menu)
        h.addSpacing(S(6))
        h.addWidget(gear)

        # Fuera la barra de menús fea: todo vive ahora en la cabecera.
        self.menubar.setVisible(False)

        # Insertar encima del QTabWidget dentro del gridLayout central.
        self.gridLayout.addWidget(header, 0, 0)
        self.gridLayout.addWidget(self.tab_widget_mode, 1, 0)
        self.gridLayout.setRowStretch(1, 1)
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.gridLayout.setSpacing(0)

    def _extra_qss(self) -> str:
        """QSS que depende de rutas de recursos (checks con imagen) + estilos de la pantalla nueva."""
        check_icon = resource_path("assets", "check.svg").replace("\\", "/")
        dot_icon = resource_path("assets", "dot.svg").replace("\\", "/")
        return f"""
QCheckBox::indicator:checked {{
    image: url({check_icon});
    background: rgba(238,117,58,0.10);
    border-color: rgba(238,117,58,0.75);
}}
QRadioButton::indicator:checked {{
    image: url({dot_icon});
    background: rgba(255,255,255,0.04);
    border-color: rgba(238,117,58,0.75);
}}
QFrame#main_card {{
    background: rgba(255,255,255,0.045);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 16px;
}}
QLabel#field_label {{
    color: #cbd5e1;
    font-size: 13px;
    font-weight: 600;
}}
QLineEdit#main_input {{
    background: rgba(255,255,255,0.05);
    border: 2px solid rgba(255,255,255,0.20);
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 14px;
    color: rgba(255,255,255,0.92);
}}
QLineEdit#main_input:focus {{
    border: 2px solid rgba(238,117,58,0.60);
    background: rgba(255,255,255,0.08);
}}
QPushButton#browse_btn {{
    background: rgba(255,255,255,0.05);
    border: 2px solid rgba(255,255,255,0.20);
    border-radius: 10px;
    padding: 10px 22px;
    font-size: 14px;
    color: #e5e7eb;
    font-weight: 600;
}}
QPushButton#browse_btn:hover {{
    border-color: rgba(238,117,58,0.55);
    color: #ffffff;
}}
QPushButton#run_primary {{
    background: rgba(238,117,58,0.12);
    border: 1px solid rgba(238,117,58,0.45);
    border-radius: 12px;
    color: #ffffff;
    font-size: 15px;
    font-weight: 600;
}}
QPushButton#run_primary:hover {{
    background: rgba(238,117,58,0.22);
    border-color: rgba(238,117,58,0.70);
}}
QToolButton#adv_toggle {{
    background: transparent;
    border: none;
    color: {MUTED};
    font-weight: 600;
    padding: 4px 0;
}}
QToolButton#adv_toggle:hover {{ color: #9aa3af; }}
QGroupBox#adv_section {{
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 14px;
    margin-top: 1.5em;
    padding-top: 0.4em;
    font-size: 13px;
}}
QGroupBox#adv_section::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 2px 4px;
    color: {ACCENT};
    font-weight: 700;
    text-transform: uppercase;
}}
QFrame#seg_control {{
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px;
}}
QPushButton#seg_btn {{
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 6px 16px;
    color: {MUTED};
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#seg_btn:hover {{
    color: #cbd5e1;
}}
QPushButton#seg_btn:checked {{
    background: rgba(238,117,58,0.16);
    color: #ffffff;
}}
QToolButton#gear_btn {{
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 10px;
    color: #cbd5e1;
    font-size: 16px;
}}
QToolButton#gear_btn:hover {{
    border-color: rgba(238,117,58,0.55);
    color: #ffffff;
}}
QToolButton#gear_btn::menu-indicator {{ image: none; width: 0; }}
"""

    #%% Pantalla principal rediseñada (estética ATOM Suite)
    def _file_row(self, parent_layout, caption: str, placeholder: str, on_click, top_gap: int = 14):
        """Crea una fila 'etiqueta + input + Examinar' en la card principal y devuelve el QLineEdit."""
        lbl = QtWidgets.QLabel(caption)
        lbl.setObjectName("field_label")
        parent_layout.addSpacing(top_gap)
        parent_layout.addWidget(lbl)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        le = QtWidgets.QLineEdit()
        le.setObjectName("main_input")
        le.setPlaceholderText(placeholder)
        le.setMinimumHeight(S(52))
        btn = QtWidgets.QPushButton("Examinar")
        btn.setObjectName("browse_btn")
        btn.setMinimumHeight(S(52))
        btn.setMinimumWidth(S(130))
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(on_click)
        row.addWidget(le, 1)
        row.addWidget(btn)
        parent_layout.addSpacing(6)
        parent_layout.addLayout(row)
        return le

    def _build_main_screen(self) -> None:
        """Construye la pantalla 'Organizar' limpia: card con 4 controles + Modo avanzado (UI original).

        No se re-cablea el pipeline: los 4 controles nuevos vuelcan sus valores en los widgets
        originales (que quedan intactos dentro del panel avanzado) justo antes de ejecutar.
        """
        # 1. Sacar "Organizar completo" del QTabWidget para reutilizarla como panel avanzado.
        adv_idx = self.tab_widget_mode.indexOf(self.tab)
        if adv_idx != -1:
            self.tab_widget_mode.removeTab(adv_idx)
        self.tab_widget_mode.tabBar().hide()  # AEROTOOLS / OTROS EQUIPOS quedan como funciones separadas

        # 2. Card con los 4 controles visibles.
        card = QtWidgets.QFrame()
        card.setObjectName("main_card")
        card.setMaximumWidth(S(1120))
        card.setMinimumWidth(S(560))
        cv = QtWidgets.QVBoxLayout(card)
        cv.setContentsMargins(S(38), S(34), S(38), S(34))
        cv.setSpacing(S(2))

        self.main_origen = self._file_row(cv, "Carpeta origen", "Selecciona la carpeta con el material crudo…", self._pick_main_origen, top_gap=0)
        self.main_destino = self._file_row(cv, "Carpeta final", "Dónde se guardará el resultado organizado…", self._pick_main_destino)
        self.main_estadillo = self._file_row(cv, "Estadillo", "Archivo del estadillo de vuelo (.xlsx / .csv)…", self._pick_main_estadillo)

        self.main_rename = QtWidgets.QCheckBox("Renombrar imágenes según el estadillo")
        self.main_rename.setChecked(self.cb_gb_split_images_rename_images.isChecked())
        self.main_rename.setStyleSheet("font-size: 14px;")
        cv.addSpacing(22)
        cv.addWidget(self.main_rename)

        self.main_run = QtWidgets.QPushButton("Ejecutar organización")
        self.main_run.setObjectName("run_primary")
        self.main_run.setMinimumHeight(S(54))
        self.main_run.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.main_run.clicked.connect(self._run_main_organize)
        self._launch_buttons.append(self.main_run)  # se deshabilita durante una corrida, como los demás
        cv.addSpacing(20)
        cv.addWidget(self.main_run)

        # 3. Toggle "Modo avanzado" + panel (UI original en un scroll), oculto de primeras.
        self.main_adv_btn = QtWidgets.QToolButton()
        self.main_adv_btn.setObjectName("adv_toggle")
        self.main_adv_btn.setText("  Modo avanzado")
        self.main_adv_btn.setCheckable(True)
        self.main_adv_btn.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.main_adv_btn.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.main_adv_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.main_adv_btn.toggled.connect(self._toggle_advanced)

        # El panel avanzado se expande a su altura NATURAL (sin scroll propio): el scroll es de
        # toda la página, para que card + avanzado bajen juntos y nada se corte abajo.
        self.adv_panel = self._build_advanced_panel()
        self.adv_panel.setVisible(False)

        # 4. Página principal transparente: el fondo de puntos lo pinta un backdrop a pantalla
        #    completa por detrás de todo (ver _install_dot_backdrop), para que los puntos también
        #    pasen tras la cabecera. La página en sí no pinta nada.
        page = QtWidgets.QWidget()
        page.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        page.setStyleSheet("background: transparent;")
        pv = QtWidgets.QVBoxLayout(page)
        pv.setContentsMargins(28, 24, 28, 28)
        pv.setSpacing(14)
        card_row = QtWidgets.QHBoxLayout()
        card_row.addStretch(1)
        card_row.addWidget(card)
        card_row.addStretch(1)
        adv_row = QtWidgets.QHBoxLayout()
        adv_row.addStretch(1)
        adv_row.addWidget(self.main_adv_btn)
        adv_row.addStretch(1)
        pv.addStretch(1)
        pv.addLayout(card_row)
        pv.addLayout(adv_row)
        pv.addWidget(self.adv_panel)
        pv.addStretch(1)

        # 5. Scroll de PÁGINA COMPLETA: al desplegar "Modo avanzado" toda la página baja (no un
        # scroll interno que recorta el contenido).
        self.page_scroll = QtWidgets.QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.page_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setWidget(page)
        # Transparente para que el backdrop de puntos se vea a través del scroll (dots fijos).
        self.page_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.page_scroll.viewport().setAutoFillBackground(False)

        # 6. Stack central: pantalla principal (0) + funciones separadas / tabs viejos (1, vía menú).
        self.central_stack = QtWidgets.QStackedWidget()
        self.central_stack.setStyleSheet("background: transparent;")  # deja ver el backdrop de puntos
        self.central_stack.addWidget(self.page_scroll)          # 0 · Organizar
        self.central_stack.addWidget(self.tab_widget_mode)      # 1 · (legacy, ya no se muestra)
        self.aero_page = self._build_aero_panel()
        self.otros_page = self._build_otros_panel()
        self.central_stack.addWidget(self.aero_page)            # 2 · AEROTOOLS (glass)
        self.central_stack.addWidget(self.otros_page)           # 3 · OTROS EQUIPOS (glass)
        self.gridLayout.addWidget(self.central_stack, 1, 0)
        self.central_stack.setCurrentIndex(0)
        # El acceso a AEROTOOLS / OTROS EQUIPOS vive en el segmented control de la cabecera
        # (_build_brand_header), no en la barra de menús.

    def _install_dot_backdrop(self) -> None:
        """Pinta el fondo de puntos por DETRÁS de toda la ventana (cabecera incluida). El header y
        la página van transparentes encima, así los puntos son continuos y también se ven tras el
        título. El backdrop se redimensiona con la ventana en resizeEvent()."""
        self._backdrop = DotGridWidget(self.centralwidget)
        self._backdrop.setGeometry(self.centralwidget.rect())
        self._backdrop.lower()  # al fondo del z-order, debajo del gridLayout

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_backdrop"):
            self._backdrop.setGeometry(self.centralwidget.rect())
            self._backdrop.lower()

    def _show_section(self, tab) -> None:
        """Conmuta entre la pantalla principal (Organizar) y los tabs originales (AEROTOOLS /
        OTROS EQUIPOS), que agrupan funciones ajenas a la organización general."""
        if tab is None:
            self.central_stack.setCurrentWidget(self.page_scroll)
            self._seg_buttons["main"].setChecked(True)
        elif tab is self.tab_aero_tools:
            self.central_stack.setCurrentWidget(self.aero_page)
            self._seg_buttons["aero"].setChecked(True)
        else:
            self.central_stack.setCurrentWidget(self.otros_page)
            self._seg_buttons["otros"].setChecked(True)

    # --- Helpers de layout glass, compartidos por Modo avanzado / AEROTOOLS / OTROS EQUIPOS ----
    def _glass_section(self, title: str, header_cb=None):
        """Tarjeta-sección glass con título; opcionalmente su checkbox-cabecera arriba."""
        gb = QtWidgets.QGroupBox(title)
        gb.setObjectName("adv_section")
        v = QtWidgets.QVBoxLayout(gb)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        if header_cb is not None:
            v.addWidget(header_cb)
        return gb, v

    def _glass_form(self) -> QtWidgets.QFormLayout:
        f = QtWidgets.QFormLayout()
        f.setContentsMargins(0, 0, 0, 0)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        f.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
        f.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return f

    def _glass_radios(self, gb: QtWidgets.QGroupBox, widgets):
        """Re-maqueta un sub-QGroupBox (radios/checks) que venía con posiciones absolutas."""
        lay = QtWidgets.QVBoxLayout(gb)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(6)
        for w in widgets:
            lay.addWidget(w)
        return gb

    def _glass_file_row(self, button, lineedit) -> QtWidgets.QHBoxLayout:
        """Fila selector: botón (glass) + lineedit que crece. Re-parenta los widgets originales."""
        button.setObjectName("browse_btn")
        r = QtWidgets.QHBoxLayout(); r.setSpacing(8)
        r.addWidget(button)
        r.addWidget(lineedit, 1)
        return r

    def _action_row(self, button) -> QtWidgets.QHBoxLayout:
        """Botón de acción primario (acento naranja), alineado a la derecha de la sección."""
        button.setObjectName("run_primary")
        button.setMinimumHeight(S(42))
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        r = QtWidgets.QHBoxLayout()
        r.addStretch(1)
        r.addWidget(button)
        return r

    def _glass_grid_page(self, secs) -> QtWidgets.QWidget:
        """Coloca las secciones glass en una rejilla adaptable de 2 columnas, alineadas arriba."""
        W = QtWidgets.QWidget()
        W.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        W.setStyleSheet("background: transparent;")
        grid = QtWidgets.QGridLayout(W)
        grid.setContentsMargins(28, 24, 28, 28)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)
        top = QtCore.Qt.AlignmentFlag.AlignTop
        for i, gb in enumerate(secs):
            grid.addWidget(gb, i // 2, i % 2, top)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        # Quitar los fondos oscuros heredados del .ui (rgb(28,28,29)) para respetar el glass.
        for w in W.findChildren(QtWidgets.QWidget):
            ss = w.styleSheet()
            if "28, 28, 29" in ss or "28,28,29" in ss:
                w.setStyleSheet("")
        return W

    def _wrap_page_scroll(self, content: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
        """Envuelve un contenido en un scroll de página completa transparente (backdrop de puntos visible)."""
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        sc.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(content)
        sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        sc.viewport().setAutoFillBackground(False)
        return sc

    def _build_advanced_panel(self) -> QtWidgets.QWidget:
        """Reconstruye el 'Modo avanzado' (todas las opciones de split_images) con layouts REALES
        y estética glass. Re-parenta los MISMOS widgets originales del .ui a QGroupBox/QFormLayout,
        por lo que se conservan intactas sus señales/slots y `_capture_run_config()`. No re-cablea
        el pipeline: solo cambia dónde y cómo se muestran los controles."""
        W = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(W)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(16)

        top = QtCore.Qt.AlignmentFlag.AlignTop

        section = self._glass_section
        form = self._glass_form
        radios = self._glass_radios

        secs = []

        # --- Separación RGB / térmica -------------------------------------------------
        gb, v = section("Separación RGB / térmica")
        r = QtWidgets.QHBoxLayout(); r.setSpacing(14)
        r.addWidget(radios(self.gb_gb_split_images_choose_mode,
                           [self.rb_gb_choose_mode_sufix, self.rb_gb_choose_mode_size]), 0, top)
        f = form()
        f.addRow(self.lb_gb_split_images_rgb, self.le_gb_split_images_end_rgb_files)
        f.addRow(self.lb_gb_split_images_rgb_extra, self.le_gb_split_images_end_rgb_extra_files)
        f.addRow(self.lb_gb_split_images_termicas, self.le_gb_split_images_end_thermo_files)
        f.addRow(self.lb_gb_split_images_max_size, self.sb_gb_split_images_max_size)
        r.addLayout(f, 1)
        v.addLayout(r)
        secs.append(gb)

        # --- Compresión RGB -----------------------------------------------------------
        gb, v = section("Compresión RGB", self.cb_gb_split_images_compress_rgb)
        f = form()
        f.addRow(self.lb_gb_split_images_compress_level, self.sb_gb_split_images_compress_level)
        v.addLayout(f)
        secs.append(gb)

        # --- Organizar imágenes (estadillo + desfase horario) -------------------------
        gb, v = section("Organizar imágenes (estadillo)", self.cb_gb_split_images_organize_images)
        v.addWidget(self.cb_gb_split_images_include_v)
        er = QtWidgets.QHBoxLayout(); er.setSpacing(8)
        er.addWidget(self.bt_gb_split_images_select_estad)
        er.addWidget(self.le_gb_split_images_estad, 1)
        v.addLayout(er)
        f = form()
        f.addRow(self.lb_gb_split_images_seconds_range, self.sb_gb_split_images_seconds_range)
        f.addRow(self.lb_gb_split_images_mismatch_hours, self.sb_gb_split_images_mismatch_hours)
        f.addRow(self.lb_gb_split_images_mismatch_minutes, self.sb_gb_split_images_mismatch_minutes)
        v.addLayout(f)
        secs.append(gb)

        # --- Generar meta y GPS -------------------------------------------------------
        gb, v = section("Generar meta y GPS", self.cb_gb_split_images_gen_meta_location)
        v.addWidget(self.cb_gb_split_images_calculate_proyected_distance)
        f = form()
        f.addRow(self.lb_gb_split_images_flight_height, self.sb_gb_split_images_flight_height)
        v.addLayout(f)
        secs.append(gb)

        # --- Rotación -----------------------------------------------------------------
        gb, v = section("Rotación", self.cb_gb_split_images_gen_thumbnails)
        r = QtWidgets.QHBoxLayout(); r.setSpacing(12)
        r.addWidget(radios(self.gb_gb_split_images_gen_thumbnails,
                           [self.cb_gb_gen_thumbnails_rgb_2, self.cb_gb_gen_thumbnails_termica_2]), 0, top)
        r.addWidget(radios(self.gb_gb_split_images_gen_thumbnails_choose_mode,
                           [self.rb_gb_split_images_choose_mode_auto, self.rb_gb_split_images_choose_mode_manual]), 0, top)
        r.addWidget(radios(self.gb_gb_split_images_gen_thumbnails_rotate_manual_value,
                           [self.rb_gb_split_images_gen_thumbnails_rotate_90, self.rb_gb_split_images_gen_thumbnails_rotate_minus_90]), 0, top)
        r.addStretch(1)
        v.addLayout(r)
        f = form()
        f.addRow(self.lb_gb_split_images_gen_thumbnails_max_error, self.sb_gb_split_images_gen_thumbnails_max_error)
        f.addRow(self.lb_gb_split_images_gen_thumbnails_add_to_angle, self.sb_gb_split_images_gen_thumbnails_add_to_angle)
        f.addRow(self.lb_gb_split_images_gen_thumbnails_subs_to_angle, self.sb_gb_split_images_gen_thumbnails_subs_to_angle)
        v.addLayout(f)
        secs.append(gb)

        # --- Convertir DJI → TIF (térmica) --------------------------------------------
        gb, v = section("Convertir DJI → TIF (térmica)", self.cb_split_images_convert_to_tif)
        r = QtWidgets.QHBoxLayout(); r.setSpacing(14)
        f = form()
        f.addRow(self.lb_split_images_convert_to_tif_emissivity, self.sb_split_images_convert_to_tif_emissivity)
        f.addRow(self.lb_split_images_convert_to_tif_humidity, self.sb_split_images_convert_to_tif_humidity)
        f.addRow(self.lb_split_images_convert_to_tif_up_temperature, self.sb_split_images_convert_to_tif_up_temperature)
        f.addRow(self.lb_split_images_convert_to_tif_low_temperature, self.sb_split_images_convert_to_tif_low_temperature)
        f.addRow(self.lb_split_images_convert_to_tif_dron_selector, self.cob_split_images_convert_to_tif_dron_selector)
        r.addLayout(f, 1)
        r.addWidget(radios(self.gb_split_images_convert_to_tiff_rotate,
                           [self.rb_split_images_convert_to_tiff_no_rotate, self.rb_split_images_convert_to_tiff_rotate_auto,
                            self.rb_split_images_convert_to_tiff_rotate_90, self.rb_split_images_convert_to_tiff_rotate_minus_90]), 0, top)
        v.addLayout(r)
        v.addWidget(self.cb_split_images_convert_to_tif_temp_auto)
        v.addWidget(self.cb_split_images_convert_to_tif_create_gray_scale_images)
        v.addWidget(self.cb_split_images_convert_to_tif_solo_seleccion_atom)
        secs.append(gb)

        # --- Recorte RGB --------------------------------------------------------------
        gb, v = section("Recorte RGB", self.cb_gb_split_images_cropping_rgb)
        r = QtWidgets.QHBoxLayout(); r.setSpacing(14)
        r.addWidget(radios(self.gb_gb_split_images_choose_cropping_mode,
                           [self.rb_gb_split_images_choose_mode_cropping_mode_auto, self.rb_gb_gb_split_images_choose_cropping_mode_manual]), 0, top)
        f = form()
        f.addRow(self.lb_gb_split_images_crop_percentage, self.sb_gb_split_images_crop_percentage)
        r.addLayout(f, 1)
        v.addLayout(r)
        secs.append(gb)

        # Colocar las secciones en rejilla de 2 columnas, alineadas arriba.
        for i, gb in enumerate(secs):
            grid.addWidget(gb, i // 2, i % 2, top)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        # Quitar los fondos oscuros heredados del .ui (rgb(28,28,29)) para que respiren en glass.
        for w in W.findChildren(QtWidgets.QWidget):
            ss = w.styleSheet()
            if "28, 28, 29" in ss or "28,28,29" in ss:
                w.setStyleSheet("")

        return W

    def _build_aero_panel(self) -> QtWidgets.QScrollArea:
        """Reconstruye AEROTOOLS (tab_aero_tools) con layouts glass reales, re-parentando los
        MISMOS widgets del .ui (señales/slots intactos). Mismo patrón que Modo avanzado."""
        secs = []
        top = QtCore.Qt.AlignmentFlag.AlignTop

        # --- Térmica: extracción (.TMC → frames) --------------------------------------
        gb, v = self._glass_section("Térmica · extracción")
        v.addLayout(self._glass_file_row(self.bt_gb_extraction_select_folder, self.le_gb_extraction_folder))
        v.addLayout(self._glass_file_row(self.bt_gb_extraction_select_output_folder, self.le_gb_extraction_output_folder))
        v.addLayout(self._glass_file_row(self.bt_gb_extraction_select_estad, self.le_gb_extraction_estad))
        v.addWidget(self.cb_gb_extraction_auto)
        f = self._glass_form()
        f.addRow(self.lb_gb_extraction_temp_min, self.sb_gb_extraction_temp_min)
        f.addRow(self.lb_gb_extraction_temp_max, self.sb_gb_extraction_temp_max)
        v.addLayout(f)
        cr = QtWidgets.QHBoxLayout(); cr.setSpacing(14)
        cr.addWidget(self.cb_gb_extraction_pre_extraction)
        cr.addWidget(self.cb_gb_extraction_extraction)
        cr.addStretch(1)
        v.addLayout(cr)
        v.addLayout(self._action_row(self.bt_do_extraction))
        secs.append(gb)

        # --- Procesamiento RGB --------------------------------------------------------
        gb, v = self._glass_section("Procesamiento RGB")
        v.addLayout(self._glass_file_row(self.bt_gb_rgb_aerotools_select_input_folder, self.le_gb_rgb_aerotools_input_folder))
        v.addLayout(self._glass_file_row(self.bt_gb_rgb_aerotools_select_output_folder, self.le_gb_rgb_aerotools_output_folder))
        v.addWidget(self.cb_gb_rgb_aerotools_compress_rgb)
        f = self._glass_form()
        f.addRow(self.lb_gb_rgb_aerotools_compress_level, self.sb_gb_rgb_aerotools_compress_level)
        v.addLayout(f)
        v.addWidget(self.cb_gb_rgb_aerotools_rename_images)
        v.addLayout(self._glass_file_row(self.bt_gb_rgb_aerotools_select_estad, self.le_gb_rgb_aerotools_estad))
        f = self._glass_form()
        f.addRow(self.lb_gb_rgb_aerotools_seconds_range_image_estad, self.sb_gb_rgb_aerotools_seconds_range_image_estad)
        f.addRow(self.lb_gb_rgb_aerotools_mismatch_hours, self.sb_gb_rgb_aerotools_mismatch_hours)
        f.addRow(self.lb_gb_rgb_aerotools_mismatch_minutes, self.sb_gb_rgb_aerotools_mismatch_minutes)
        v.addLayout(f)
        v.addLayout(self._glass_file_row(self.bt_gb_rgb_aerotools_select_logs_folder, self.le_gb_rgb_aerotools_logs_folder))
        f = self._glass_form()
        f.addRow(self.lb_gb_rgb_aerotools_seconds_range_log_estad, self.sb_gb_rgb_aerotools_seconds_range_log_estad)
        v.addLayout(f)
        v.addWidget(self.cb_gb_rgb_aerotools_geotagging)
        v.addWidget(self.cb_gb_rgb_aerotools_organize_images)
        v.addLayout(self._action_row(self.bt_do_rgb_aerotools_processing))
        secs.append(gb)

        # --- Rotación -----------------------------------------------------------------
        gb, v = self._glass_section("Rotación")
        v.addLayout(self._glass_file_row(self.bt_gb_generate_thumbnails_aerotools_select_input_folder, self.le_gb_generate_thumbnails_aerotools_input_folder))
        v.addWidget(self._glass_radios(self.gb_gb_gen_thumbnails_aerotools,
                    [self.cb_gb_gen_thumbnails_aerotools_rgb, self.cb_gb_gen_thumbnails_aerotools_termica]))
        f = self._glass_form()
        f.addRow(self.lb_gb_gen_thumbnails_aerotools, self.sb_gb_generate_thumbnails_aerotools_max_error)
        f.addRow(self.lb_gb_gen_thumbnails_aerotools_add_to_angle, self.sb_gb_generate_thumbnails_aerotools_add_to_angle)
        f.addRow(self.lb_gb_gen_thumbnails_aerotools_subs_to_angle, self.sb_gb_generate_thumbnails_aerotools_subs_to_angle)
        v.addLayout(f)
        v.addLayout(self._action_row(self.bt_do_generate_thumbnails_aerotools))
        secs.append(gb)

        # --- Geoetiquetado manual -----------------------------------------------------
        gb, v = self._glass_section("Geoetiquetado manual")
        v.addLayout(self._glass_file_row(self.bt_gb_manual_geotagging_select_images_folder, self.le_gb_manual_geotagging_select_images_folder))
        v.addLayout(self._glass_file_row(self.bt_gb_manual_geotagging_select_log, self.le_gb_manual_geotagging_select_log))
        v.addLayout(self._glass_file_row(self.bt_gb_manual_geotagging_select_estad, self.le_gb_manual_geotagging_estad))
        v.addLayout(self._glass_file_row(self.bt_gb_manual_geotagging_select_logs_folder, self.le_gb_manual_geotagging_logs_folder))
        v.addLayout(self._action_row(self.bt_do_manual_geotagging))
        secs.append(gb)

        return self._wrap_page_scroll(self._glass_grid_page(secs))

    def _build_otros_panel(self) -> QtWidgets.QScrollArea:
        """Reconstruye OTROS EQUIPOS (tab_manual) con layouts glass reales, re-parentando los
        MISMOS widgets del .ui (señales/slots intactos). Mismo patrón que Modo avanzado."""
        secs = []
        top = QtCore.Qt.AlignmentFlag.AlignTop

        # --- Comprimir imágenes -------------------------------------------------------
        gb, v = self._glass_section("Comprimir imágenes")
        v.addLayout(self._glass_file_row(self.bt_gb_compress_rgbs_select_input_folder, self.le_gb_compress_rgbs_input_folder))
        v.addLayout(self._glass_file_row(self.bt_gb_compress_rgbs_select_output_folder, self.le_gb_compress_rgbs_output_folder))
        v.addWidget(self.cb_gb_compress_rgbs_include_subfolders)
        f = self._glass_form()
        f.addRow(self.lb_gb_compress_level, self.sb_compress_level)
        v.addLayout(f)
        v.addLayout(self._action_row(self.bt_do_compress))
        secs.append(gb)

        # --- Generar meta y location --------------------------------------------------
        gb, v = self._glass_section("Generar meta y location")
        v.addLayout(self._glass_file_row(self.bt_gb_gen_meta_location_select_input_folder, self.le_gb_gen_meta_location_input_folder))
        v.addWidget(self._glass_radios(self.gb_gb_gen_meta_location,
                    [self.rb_gb_gen_meta, self.rb_gb_gen_location, self.rb_gb_gen_location_and_meta]))
        f = self._glass_form()
        f.addRow(self.lb_gb_gen_meta_location_flight_height, self.sb_gb_gen_meta_location_flight_height)
        v.addLayout(f)
        v.addWidget(self.cb_gb_gen_meta_location_calculate_proyected_distance)
        v.addLayout(self._action_row(self.bt_do_gen_meta_location))
        secs.append(gb)

        # --- Estructura de carpetas / organizar ---------------------------------------
        gb, v = self._glass_section("Estructura de carpetas · organizar")
        v.addLayout(self._glass_file_row(self.bt_gb_gen_folder_struct_select_estad, self.le_gb_gen_folder_struct_estad))
        v.addLayout(self._glass_file_row(self.bt_gb_gen_folder_struct_select_output_folder, self.le_gb_gen_folder_struct_output_folder))
        v.addWidget(self.cb_gb_gen_folder_struct_organize_images)
        v.addWidget(self.cb_gb_gen_folder_struct_include_v)
        f = self._glass_form()
        f.addRow(self.lb_gb_gen_folder_struct_seconds_range, self.sb_gb_gen_folder_struct_seconds_range)
        f.addRow(self.lb_gb_gen_folder_struct_mismatch_hours, self.sb_gb_gen_folder_struct_mismatch_hours)
        f.addRow(self.lb_gb_gen_folder_struct_mismatch_minutes, self.sb_gb_gen_folder_struct_mismatch_minutes)
        v.addLayout(f)
        v.addLayout(self._action_row(self.bt_do_gen_struct))
        secs.append(gb)

        # --- Rotación -----------------------------------------------------------------
        gb, v = self._glass_section("Rotación")
        v.addLayout(self._glass_file_row(self.bt_gb_generate_thumbnails_select_input_folder, self.le_gb_generate_thumbnails_input_folder))
        r = QtWidgets.QHBoxLayout(); r.setSpacing(12)
        r.addWidget(self._glass_radios(self.gb_gb_gen_thumbnails,
                    [self.cb_gb_gen_thumbnails_rgb, self.cb_gb_gen_thumbnails_termica]), 0, top)
        r.addWidget(self._glass_radios(self.gb_gb_gen_thumbnails_choose_mode,
                    [self.rb_gb_generate_thumbnails_choose_mode_auto, self.rb_gb_generate_thumbnails_choose_mode_manual]), 0, top)
        r.addWidget(self._glass_radios(self.gb_gb_gen_thumbnails_rotate_manual_value,
                    [self.rb_gb_gen_thumbnails_rotate_90, self.rb_gb_gen_thumbnails_rotate_minus_90]), 0, top)
        r.addStretch(1)
        v.addLayout(r)
        f = self._glass_form()
        f.addRow(self.lb_gb_gen_thumbnails, self.sb_gb_generate_thumbnails_max_error)
        f.addRow(self.lb_gb_gen_thumbnails_add_to_angle, self.sb_gb_generate_thumbnails_add_to_angle)
        f.addRow(self.lb_gb_gen_thumbnails_subs_to_angle, self.sb_gb_generate_thumbnails_subs_to_angle)
        v.addLayout(f)
        v.addLayout(self._action_row(self.bt_do_generate_thumbnails))
        secs.append(gb)

        # --- Renombrado de imágenes ---------------------------------------------------
        gb, v = self._glass_section("Renombrado de imágenes")
        v.addLayout(self._glass_file_row(self.bt_gb_rename_images_select_folder, self.le_gb_rename_images_output_folder))
        v.addLayout(self._action_row(self.bt_do_rename_images))
        secs.append(gb)

        # --- Convertir DJI → TIF ------------------------------------------------------
        gb, v = self._glass_section("Convertir DJI → TIF")
        v.addLayout(self._glass_file_row(self.bt_gb_convert_to_tif_select_folder, self.le_gb_convert_to_tif_input_folder))
        f = self._glass_form()
        f.addRow(self.lb_gb_convert_to_tif_emissivity, self.sb_gb_convert_to_tif_emissivity)
        f.addRow(self.lb_gb_convert_to_tif_humidity, self.sb_gb_convert_to_tif_humidity)
        f.addRow(self.lb_gb_convert_to_tif_dron_selector, self.cob_gb_convert_to_tif_dron_selector)
        v.addLayout(f)
        v.addWidget(self.cb_gb_convert_to_tif_temp_auto)
        f = self._glass_form()
        f.addRow(self.lb_gb_convert_to_tif_up_temperature, self.sb_gb_convert_to_tif_up_temperature)
        f.addRow(self.lb_gb_convert_to_tif_low_temperature, self.sb_gb_convert_to_tif_low_temperature)
        v.addLayout(f)
        v.addWidget(self._glass_radios(self.gb_gb_convert_to_tiff_rotate,
                    [self.rb_gb_convert_to_tiff_no_rotate, self.rb_gb_convert_to_tiff_rotate_90,
                     self.rb_gb_convert_to_tiff_rotate_minus_90, self.rb_gb_convert_to_tiff_rotate_auto]))
        v.addWidget(self.cb_gb_convert_to_tif_solo_seleccion_atom)
        v.addWidget(self.cb_gb_convert_to_tif_create_gray_scale_images)
        v.addLayout(self._action_row(self.bt_do_convert_to_tif))
        secs.append(gb)

        # --- Recorte RGB --------------------------------------------------------------
        gb, v = self._glass_section("Recorte RGB")
        v.addLayout(self._glass_file_row(self.bt_gb_cropping_rgb_select_folder, self.le_gb_cropping_rgb_output_folder))
        v.addWidget(self._glass_radios(self.gb_gb_cropping_rgb_choose_mode,
                    [self.rb_gb_cropping_rgb_choose_mode_auto, self.rb_gb_cropping_rgb_choose_mode_manual]))
        f = self._glass_form()
        f.addRow(self.lb_gb_cropping_rgb_crop_percentage, self.sb_gb_cropping_rgb_crop_percentage)
        v.addLayout(f)
        v.addLayout(self._action_row(self.bt_do_rgb_cropping))
        secs.append(gb)

        # --- Girar imágenes TIF -------------------------------------------------------
        gb, v = self._glass_section("Girar imágenes TIF")
        v.addLayout(self._glass_file_row(self.bt_gb_rotate_tif_select_folder, self.le_gb_rotate_tif_input_folder_2))
        v.addWidget(self._glass_radios(self.gb_gb_rotate_tiff_rotate_options,
                    [self.rb_gb_rotate_tiff_rotate_90, self.rb_gb_rotate_tiff_rotate_minus_90, self.rb_gb_rotate_tiff_rotate_180]))
        v.addLayout(self._action_row(self.bt_do_rotate_tif))
        secs.append(gb)

        return self._wrap_page_scroll(self._glass_grid_page(secs))

    # Diálogo no-nativo de Qt: bajo Hyprland/XWayland el nativo (portal/GTK) atrapa el ratón.
    _DLG_OPTS = QtWidgets.QFileDialog.Option.DontUseNativeDialog

    def _pick_main_origen(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Carpeta origen", "", self._DLG_OPTS)
        if d:
            self.main_origen.setText(d)

    def _pick_main_destino(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Carpeta final", "", self._DLG_OPTS)
        if d:
            self.main_destino.setText(d)

    def _pick_main_estadillo(self) -> None:
        f, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Estadillo", "", "Estadillos (*.xlsx *.xls *.csv);;Todos los archivos (*)", options=self._DLG_OPTS)
        if f:
            self.main_estadillo.setText(f)

    def _toggle_advanced(self, on: bool) -> None:
        self.adv_panel.setVisible(on)
        self.main_adv_btn.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow)
        if on:  # revelar el panel dentro del scroll de página al desplegarlo.
            QtCore.QTimer.singleShot(0, lambda: self.page_scroll.ensureWidgetVisible(self.main_adv_btn))

    def _run_main_organize(self) -> None:
        """Vuelca los 4 controles de la card en los widgets originales y lanza el pipeline split_images."""
        self.le_gb_split_images_input_folder.setText(self.main_origen.text())
        self.le_gb_split_images_output_folder.setText(self.main_destino.text())
        estad = self.main_estadillo.text().strip()
        self.le_gb_split_images_estad.setText(estad)
        # Con estadillo => organizar imágenes activo (el checkbox vive en el panel avanzado).
        self.cb_gb_split_images_organize_images.setChecked(bool(estad))
        self.cb_gb_split_images_rename_images.setChecked(self.main_rename.isChecked())
        self.modify_qt_objects()
        self.run_thread("split_images")

    #%% Funciones específicas de la interfaz.
    def select_input_folder(self, input_folder_type: str) -> None:
        """Función que muestra una ventana de diálogo para elegir una carpeta de entrada. El path se mostrará en QLineEdit correspondiente."""
        dir_name = QtWidgets.QFileDialog.getExistingDirectory()
        if dir_name:
            if input_folder_type == "split_images":
                self.le_gb_split_images_input_folder.setText(dir_name)
            elif input_folder_type == "compress_images":
                self.le_gb_compress_rgbs_input_folder.setText(dir_name)
            elif input_folder_type == "generate_thumbnails":
                self.le_gb_generate_thumbnails_input_folder.setText(dir_name)
            elif input_folder_type == "gen_meta_location":
                self.le_gb_gen_meta_location_input_folder.setText(dir_name)
            elif input_folder_type == "rename_images":
                self.le_gb_rename_images_output_folder.setText(dir_name)
            elif input_folder_type == "extraction":
                self.le_gb_extraction_folder.setText(dir_name)
            elif input_folder_type == "rgb_aerotools":
                self.le_gb_rgb_aerotools_input_folder.setText(dir_name)
            elif input_folder_type == "generate_thumbnails_aerotools":
                self.le_gb_generate_thumbnails_aerotools_input_folder.setText(dir_name)
            elif input_folder_type == "convert_to_tif":
                self.le_gb_convert_to_tif_input_folder.setText(dir_name)
            elif input_folder_type == "rgb_cropping":
                self.le_gb_cropping_rgb_output_folder.setText(dir_name)
            elif input_folder_type == "tif_rotating":
                self.le_gb_rotate_tif_input_folder_2.setText(dir_name)           
                
    def select_output_folder(self, output_folder_type: str) -> None:
        """Función que muestra una ventana de diálogo para elegir una carpeta de salida. El path se mostrará en QLineEdit correspondiente."""
        dir_name = QtWidgets.QFileDialog.getExistingDirectory()
        if dir_name:
            if output_folder_type == "split_images":
                self.le_gb_split_images_output_folder.setText(dir_name)
            elif output_folder_type == "compress_images":
                self.le_gb_compress_rgbs_output_folder.setText(dir_name)
            elif output_folder_type == "gen_struct":
                self.le_gb_gen_folder_struct_output_folder.setText(dir_name)
            elif output_folder_type == "extraction":
                self.le_gb_extraction_output_folder.setText(dir_name)
            elif output_folder_type == "rgb_aerotools":
                self.le_gb_rgb_aerotools_output_folder.setText(dir_name)
            elif output_folder_type == "manual_geotagging":
                self.le_gb_manual_geotagging_select_images_folder.setText(dir_name)
    
    def select_estadillo_for_showing(self):
        """Esta función lee el csv del estadillo y genera las variables necesarias para mostrarlo en una pestaña usando un modelo MVC"""
        self.table = QtWidgets.QTableView()  # Creamos la vista
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName()
        # TODO: Si el delimitador es una ",", nos va a dar error.
        data = pd.read_csv(file_name, sep=';')
        self.model = TableModel(data)  # Creamos el modelo a parir del TableModel definido en qt_models.py
        self.table.setModel(self.model)  # Seteamos el modelo en la vista
        tab_index = self.tab_widget_mode.addTab(self.table, os.path.basename(file_name))  # Añadimos la pestaña
        self.tab_widget_mode.setCurrentIndex(tab_index)  # Visualizamos la nueva pestaña

    def select_estadillo_for_gen_struct(self, gb_type :str):
        """Esta función selecciona el estadillo para generar la estructura de carpetas"""
        # TODO: Posiblemente podamos unir en una función más funciones como ésta como hicimos con select_input_folder y output
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName()
        if gb_type == "gb_split_images":
            self.le_gb_split_images_estad.setText(file_name)
        elif gb_type == "gb_gen_folder_struct":
            self.le_gb_gen_folder_struct_estad.setText(file_name)
        elif gb_type == "gb_pre_extract":
            self.le_gb_extraction_estad.setText(file_name)
        elif gb_type == "rgb_aerotools":
            self.le_gb_rgb_aerotools_estad.setText(file_name)
        elif gb_type == "manual_geotagging":
            self.le_gb_manual_geotagging_estad.setText(file_name)

    def select_logs_folder(self):
        dir_name = QtWidgets.QFileDialog.getExistingDirectory()
        self.le_gb_rgb_aerotools_logs_folder.setText(dir_name)

    def select_log_for_manual_geotagging(self):
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName()
        self.le_gb_manual_geotagging_select_log.setText(file_name)

    def select_logs_folder_for_manual_geotagging(self):
        dir_name = QtWidgets.QFileDialog.getExistingDirectory()
        self.le_gb_manual_geotagging_logs_folder.setText(dir_name)

    def modify_qt_objects(self) -> None:
        """Cambia el nombre de ciertos elementos del texto cuando se marcan las casillas correspondientes, de modo que facilite la comprensión de lo que va a hacer
        la aplicación"""
        if self.rb_gb_choose_mode_size.isChecked():
            self.lb_gb_split_images_max_size.setEnabled(True)
            self.sb_gb_split_images_max_size.setEnabled(True)
            self.le_gb_split_images_end_thermo_files.setEnabled(False)
            self.le_gb_split_images_end_rgb_files.setEnabled(False)
            self.le_gb_split_images_end_rgb_extra_files.setEnabled(False)
            self.lb_gb_split_images_rgb.setEnabled(False)
            self.lb_gb_split_images_termicas.setEnabled(False)
            self.lb_gb_split_images_rgb_extra.setEnabled(False)
            # Style
            self.lb_gb_split_images_max_size.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_max_size.setStyleSheet("color: rgb(238, 118, 60)")
            self.le_gb_split_images_end_thermo_files.setStyleSheet("border: 1px solid rgb(144, 144, 144);")
            self.le_gb_split_images_end_rgb_files.setStyleSheet("border: 1px solid rgb(144, 144, 144);")
            self.le_gb_split_images_end_rgb_extra_files.setStyleSheet("border: 1px solid rgb(144, 144, 144);")
            self.lb_gb_split_images_rgb.setStyleSheet("color: rgb(144, 144, 144);")
            self.lb_gb_split_images_termicas.setStyleSheet("color: rgb(144, 144, 144);")
            self.lb_gb_split_images_rgb_extra.setStyleSheet("color: rgb(144, 144, 144);")
        elif self.rb_gb_choose_mode_sufix.isChecked():
            self.lb_gb_split_images_max_size.setEnabled(False)
            self.sb_gb_split_images_max_size.setEnabled(False)
            self.le_gb_split_images_end_thermo_files.setEnabled(True)
            self.le_gb_split_images_end_rgb_files.setEnabled(True)
            self.le_gb_split_images_end_rgb_extra_files.setEnabled(True)
            self.lb_gb_split_images_rgb.setEnabled(True)
            self.lb_gb_split_images_termicas.setEnabled(True)
            self.lb_gb_split_images_rgb_extra.setEnabled(True)
            # Style
            self.lb_gb_split_images_max_size.setStyleSheet("color: rgb(144, 144, 144);")
            self.sb_gb_split_images_max_size.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.le_gb_split_images_end_thermo_files.setStyleSheet("border: 1px solid rgb(238, 118, 60);")
            self.le_gb_split_images_end_rgb_files.setStyleSheet("border: 1px solid rgb(238, 118, 60);")
            self.le_gb_split_images_end_rgb_extra_files.setStyleSheet("border: 1px solid rgb(238, 118, 60);")
            self.lb_gb_split_images_rgb.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_termicas.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_rgb_extra.setStyleSheet("color: rgb(238, 118, 60)")

        # También se podría usar la siguiente opción:
        # radio_button = self.sender()
        # if radio_button.isChecked():
        # ...
        if self.cb_gb_split_images_compress_rgb.checkState().value:
            self.bt_do_split.setText("Separar y comprimir imágenes RGB")
            self.sb_gb_split_images_compress_level.setEnabled(True)
            self.lb_gb_split_images_compress_level.setEnabled(True)
            self.sb_gb_split_images_compress_level.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_compress_level.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.bt_do_split.setText("Separar imágenes")
            self.sb_gb_split_images_compress_level.setEnabled(False)
            self.lb_gb_split_images_compress_level.setEnabled(False)
            self.sb_gb_split_images_compress_level.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_split_images_compress_level.setStyleSheet("color: rgb(144, 144, 144);")

        if self.cb_gb_split_images_organize_images.checkState().value:
            self.bt_gb_split_images_select_estad.setEnabled(True)
            self.le_gb_split_images_estad.setEnabled(True)
            self.lb_gb_split_images_seconds_range.setEnabled(True)
            self.sb_gb_split_images_seconds_range.setEnabled(True)
            self.lb_gb_split_images_mismatch_hours.setEnabled(True)
            self.lb_gb_split_images_mismatch_minutes.setEnabled(True)
            self.sb_gb_split_images_mismatch_hours.setEnabled(True)
            self.sb_gb_split_images_mismatch_minutes.setEnabled(True)
            # Style
            self.bt_gb_split_images_select_estad.setStyleSheet("""
        QPushButton {
                background-color: rgb(28, 28, 29);
                border-radius: 10px;
                border: 2px solid rgb(238, 118, 60);
                color: rgb(255, 255, 255);
        }
        QPushButton:hover {
                background-color: rgb(238, 118, 60);
                color: rgb(0, 0, 0);
        }
        QPushButton:pressed {
                background-color: rgb(180, 80, 20);
                border: 2px solid rgb(255, 255, 255);
                color: rgb(255, 255, 255);
        }
        """)
            self.le_gb_split_images_estad.setStyleSheet("border: 1px solid rgb(238, 118, 60);")
            self.lb_gb_split_images_seconds_range.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_seconds_range.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_mismatch_hours.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_mismatch_minutes.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_mismatch_hours.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_mismatch_minutes.setStyleSheet("color: rgb(238, 118, 60)")

        else:
            self.bt_gb_split_images_select_estad.setEnabled(False)
            self.le_gb_split_images_estad.setEnabled(False)
            self.lb_gb_split_images_seconds_range.setEnabled(False)
            self.sb_gb_split_images_seconds_range.setEnabled(False)
            self.lb_gb_split_images_mismatch_hours.setEnabled(False)
            self.lb_gb_split_images_mismatch_minutes.setEnabled(False)
            self.sb_gb_split_images_mismatch_hours.setEnabled(False)
            self.sb_gb_split_images_mismatch_minutes.setEnabled(False)
            # Style
            self.bt_gb_split_images_select_estad.setStyleSheet("background-color: rgb(28, 28, 29);\n"
"border-radius: 9px;\n"
"border: 2px solid rgb(144, 144, 144);\n"
"color: rgb(144,144,144);")
            self.le_gb_split_images_estad.setStyleSheet("border: 1px solid rgb(144, 144, 144);")
            self.lb_gb_split_images_seconds_range.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_split_images_seconds_range.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_split_images_mismatch_hours.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_mismatch_minutes.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_split_images_mismatch_hours.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_split_images_mismatch_minutes.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")

        if self.cb_gb_gen_folder_struct_organize_images.checkState().value:
            self.lb_gb_gen_folder_struct_seconds_range.setEnabled(True)
            self.sb_gb_gen_folder_struct_seconds_range.setEnabled(True)
            self.lb_gb_gen_folder_struct_mismatch_hours.setEnabled(True)
            self.lb_gb_gen_folder_struct_mismatch_minutes.setEnabled(True)
            self.sb_gb_gen_folder_struct_mismatch_hours.setEnabled(True)
            self.sb_gb_gen_folder_struct_mismatch_minutes.setEnabled(True)
            # Style
            self.lb_gb_gen_folder_struct_seconds_range.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_gen_folder_struct_seconds_range.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_gen_folder_struct_mismatch_hours.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_gen_folder_struct_mismatch_minutes.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_gen_folder_struct_mismatch_hours.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_gen_folder_struct_mismatch_minutes.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.lb_gb_gen_folder_struct_seconds_range.setEnabled(False)
            self.sb_gb_gen_folder_struct_seconds_range.setEnabled(False)
            self.lb_gb_gen_folder_struct_mismatch_hours.setEnabled(False)
            self.lb_gb_gen_folder_struct_mismatch_minutes.setEnabled(False)
            self.sb_gb_gen_folder_struct_mismatch_hours.setEnabled(False)
            self.sb_gb_gen_folder_struct_mismatch_minutes.setEnabled(False)
            # Style
            self.lb_gb_gen_folder_struct_seconds_range.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_gen_folder_struct_seconds_range.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_gen_folder_struct_mismatch_hours.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_gen_folder_struct_mismatch_minutes.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_gen_folder_struct_mismatch_hours.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_gen_folder_struct_mismatch_minutes.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            
        if self.cb_gb_extraction_auto.checkState().value:
            self.lb_gb_extraction_temp_max.setEnabled(False)
            self.lb_gb_extraction_temp_min.setEnabled(False)
            self.sb_gb_extraction_temp_max.setEnabled(False)
            self.sb_gb_extraction_temp_min.setEnabled(False)
            # Style
            self.lb_gb_extraction_temp_max.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_extraction_temp_min.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_extraction_temp_max.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_extraction_temp_min.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
        else:
            self.lb_gb_extraction_temp_max.setEnabled(True)
            self.lb_gb_extraction_temp_min.setEnabled(True)
            self.sb_gb_extraction_temp_max.setEnabled(True)
            self.sb_gb_extraction_temp_min.setEnabled(True)
            # Style
            self.lb_gb_extraction_temp_max.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_extraction_temp_min.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_extraction_temp_max.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_extraction_temp_min.setStyleSheet("color: rgb(238, 118, 60)")
        
        if self.cb_gb_rgb_aerotools_compress_rgb.checkState().value:
            self.sb_gb_rgb_aerotools_compress_level.setEnabled(True)
            self.lb_gb_rgb_aerotools_compress_level.setEnabled(True)
            self.sb_gb_rgb_aerotools_compress_level.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_rgb_aerotools_compress_level.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.sb_gb_rgb_aerotools_compress_level.setEnabled(False)
            self.lb_gb_rgb_aerotools_compress_level.setEnabled(False)
            self.sb_gb_rgb_aerotools_compress_level.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_rgb_aerotools_compress_level.setStyleSheet("color: rgb(144, 144, 144)")
            
        if self.cb_gb_convert_to_tif_temp_auto.checkState().value:
            self.sb_gb_convert_to_tif_low_temperature.setEnabled(False)
            self.sb_gb_convert_to_tif_up_temperature.setEnabled(False)
            self.lb_gb_convert_to_tif_low_temperature.setEnabled(False)
            self.lb_gb_convert_to_tif_up_temperature.setEnabled(False)
            # Style
            self.sb_gb_convert_to_tif_low_temperature.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_convert_to_tif_up_temperature.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_convert_to_tif_low_temperature.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_convert_to_tif_up_temperature.setStyleSheet("color: rgb(144, 144, 144)")
        else:
            self.sb_gb_convert_to_tif_low_temperature.setEnabled(True)
            self.sb_gb_convert_to_tif_up_temperature.setEnabled(True)
            self.lb_gb_convert_to_tif_low_temperature.setEnabled(True)
            self.lb_gb_convert_to_tif_up_temperature.setEnabled(True)
            # Style
            self.sb_gb_convert_to_tif_low_temperature.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_convert_to_tif_up_temperature.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_convert_to_tif_low_temperature.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_convert_to_tif_up_temperature.setStyleSheet("color: rgb(238, 118, 60)")
        
        if self.cb_gb_split_images_calculate_proyected_distance.checkState().value:
            self.sb_gb_split_images_flight_height.setEnabled(True)
            self.lb_gb_split_images_flight_height.setEnabled(True)
            self.sb_gb_split_images_flight_height.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_flight_height.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.sb_gb_split_images_flight_height.setEnabled(False)
            self.lb_gb_split_images_flight_height.setEnabled(False)
            self.sb_gb_split_images_flight_height.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_split_images_flight_height.setStyleSheet("color: rgb(144, 144, 144)")

        if self.cb_gb_split_images_gen_meta_location.checkState().value:
            self.cb_gb_split_images_calculate_proyected_distance.setEnabled(True)
            self.cb_gb_split_images_calculate_proyected_distance.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.cb_gb_split_images_calculate_proyected_distance.setEnabled(False)
            self.cb_gb_split_images_calculate_proyected_distance.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_split_images_flight_height.setEnabled(False)
            self.lb_gb_split_images_flight_height.setEnabled(False)
            self.sb_gb_split_images_flight_height.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_split_images_flight_height.setStyleSheet("color: rgb(144, 144, 144)")

        if self.cb_gb_gen_meta_location_calculate_proyected_distance.checkState().value:
            self.sb_gb_gen_meta_location_flight_height.setEnabled(True)
            self.lb_gb_gen_meta_location_flight_height.setEnabled(True)
            self.sb_gb_gen_meta_location_flight_height.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_gen_meta_location_flight_height.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.sb_gb_gen_meta_location_flight_height.setEnabled(False)
            self.lb_gb_gen_meta_location_flight_height.setEnabled(False)
            self.sb_gb_gen_meta_location_flight_height.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_gen_meta_location_flight_height.setStyleSheet("color: rgb(144, 144, 144)")

        if self.rb_gb_split_images_choose_mode_auto.isChecked():
            self.lb_gb_split_images_gen_thumbnails_auto.setEnabled(True)
            self.lb_gb_split_images_gen_thumbnails_max_error.setEnabled(True)
            self.sb_gb_split_images_gen_thumbnails_max_error.setEnabled(True)
            self.lb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(True)
            self.sb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(True)
            self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(True)
            self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(True)
            self.gb_gb_split_images_gen_thumbnails_rotate_manual_value.setEnabled(False)
            self.lb_gb_split_images_gen_thumbnails_auto.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.gb_gb_split_images_gen_thumbnails_rotate_manual_value.setStyleSheet("color: rgb(144, 144, 144)")
        elif self.rb_gb_split_images_choose_mode_manual.isChecked():
            self.lb_gb_split_images_gen_thumbnails_auto.setEnabled(False)
            self.lb_gb_split_images_gen_thumbnails_max_error.setEnabled(False)
            self.sb_gb_split_images_gen_thumbnails_max_error.setEnabled(False)
            self.lb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(False)
            self.sb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(False)
            self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(False)
            self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(False)
            self.gb_gb_split_images_gen_thumbnails_rotate_manual_value.setEnabled(True)

            self.lb_gb_split_images_gen_thumbnails_auto.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.gb_gb_split_images_gen_thumbnails_rotate_manual_value.setStyleSheet("color: rgb(238, 118, 60)")

        if self.cb_gb_split_images_gen_thumbnails.checkState().value:
            self.gb_gb_split_images_gen_thumbnails.setEnabled(True)
            # self.lb_gb_split_images_gen_thumbnails_auto.setEnabled(True)
            # self.lb_gb_split_images_gen_thumbnails_max_error.setEnabled(True)
            # self.sb_gb_split_images_gen_thumbnails_max_error.setEnabled(True)
            # self.lb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(True)
            # self.sb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(True)
            # self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(True)
            # self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(True)
            self.gb_gb_split_images_gen_thumbnails_choose_mode.setEnabled(True)
            self.rb_gb_split_images_choose_mode_auto.setEnabled(True)
            self.rb_gb_split_images_choose_mode_manual.setEnabled(True)

            self.gb_gb_split_images_gen_thumbnails.setStyleSheet("color: rgb(238, 118, 60)")
            # self.lb_gb_split_images_gen_thumbnails_auto.setStyleSheet("color: rgb(238, 118, 60)")
            # self.lb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("color: rgb(238, 118, 60)")
            # self.lb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            # self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            # self.sb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("color: rgb(238, 118, 60)")
            # self.sb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            # self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.gb_gb_split_images_gen_thumbnails_choose_mode.setStyleSheet("color: rgb(238, 118, 60)")
            self.rb_gb_split_images_choose_mode_auto.setStyleSheet("color: rgb(238, 118, 60)")
            self.rb_gb_split_images_choose_mode_manual.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.gb_gb_split_images_gen_thumbnails.setEnabled(False)
            self.lb_gb_split_images_gen_thumbnails_max_error.setEnabled(False)
            self.sb_gb_split_images_gen_thumbnails_max_error.setEnabled(False)
            self.lb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(False)
            self.sb_gb_split_images_gen_thumbnails_add_to_angle.setEnabled(False)
            self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(False)
            self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setEnabled(False)
            self.gb_gb_split_images_gen_thumbnails_choose_mode.setEnabled(True)
            self.lb_gb_split_images_gen_thumbnails_auto.setEnabled(False)
            self.rb_gb_split_images_choose_mode_auto.setEnabled(False)
            self.rb_gb_split_images_choose_mode_manual.setEnabled(False)
            self.gb_gb_split_images_gen_thumbnails_rotate_manual_value.setEnabled(False)

            self.gb_gb_split_images_gen_thumbnails.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_split_images_gen_thumbnails_max_error.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_split_images_gen_thumbnails_add_to_angle.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.sb_gb_split_images_gen_thumbnails_subs_to_angle.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.gb_gb_split_images_gen_thumbnails_choose_mode.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_gen_thumbnails_auto.setStyleSheet("color: rgb(144, 144, 144)")
            self.rb_gb_split_images_choose_mode_auto.setStyleSheet("color: rgb(144, 144, 144)")
            self.rb_gb_split_images_choose_mode_manual.setStyleSheet("color: rgb(144, 144, 144)")
            self.gb_gb_split_images_gen_thumbnails_rotate_manual_value.setStyleSheet("color: rgb(144, 144, 144)")

        if self.cb_split_images_convert_to_tif_temp_auto.checkState().value:
            self.lb_split_images_convert_to_tif_up_temperature.setEnabled(False)
            self.sb_split_images_convert_to_tif_up_temperature.setEnabled(False)
            self.lb_split_images_convert_to_tif_low_temperature.setEnabled(False)
            self.sb_split_images_convert_to_tif_low_temperature.setEnabled(False)
            self.lb_split_images_convert_to_tif_up_temperature.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_split_images_convert_to_tif_up_temperature.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_split_images_convert_to_tif_low_temperature.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_split_images_convert_to_tif_low_temperature.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
        else:
            self.lb_split_images_convert_to_tif_up_temperature.setEnabled(True)
            self.sb_split_images_convert_to_tif_up_temperature.setEnabled(True)
            self.lb_split_images_convert_to_tif_low_temperature.setEnabled(True)
            self.sb_split_images_convert_to_tif_low_temperature.setEnabled(True)
            self.lb_split_images_convert_to_tif_up_temperature.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_split_images_convert_to_tif_up_temperature.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_split_images_convert_to_tif_low_temperature.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_split_images_convert_to_tif_low_temperature.setStyleSheet("color: rgb(238, 118, 60)")

        if self.cb_split_images_convert_to_tif.checkState().value:
            self.lb_split_images_convert_to_tif_emissivity.setEnabled(True)
            self.sb_split_images_convert_to_tif_emissivity.setEnabled(True)
            self.lb_split_images_convert_to_tif_humidity.setEnabled(True)
            self.sb_split_images_convert_to_tif_humidity.setEnabled(True)
            self.cb_split_images_convert_to_tif_temp_auto.setEnabled(True)
            self.gb_split_images_convert_to_tiff_rotate.setEnabled(True)
            self.cb_split_images_convert_to_tif_create_gray_scale_images.setEnabled(True)
            self.cb_split_images_convert_to_tif_solo_seleccion_atom.setEnabled(True)
            self.lb_split_images_convert_to_tif_dron_selector.setEnabled(True)
            self.cob_split_images_convert_to_tif_dron_selector.setEnabled(True)

            self.lb_split_images_convert_to_tif_emissivity.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_split_images_convert_to_tif_emissivity.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_split_images_convert_to_tif_humidity.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_split_images_convert_to_tif_humidity.setStyleSheet("color: rgb(238, 118, 60)")
            self.gb_split_images_convert_to_tiff_rotate.setStyleSheet("color: rgb(238, 118, 60)")
            self.cb_split_images_convert_to_tif_temp_auto.setStyleSheet("color: rgb(238, 118, 60)")
            self.cb_split_images_convert_to_tif_create_gray_scale_images.setStyleSheet("color: rgb(238, 118, 60)")
            self.cb_split_images_convert_to_tif_solo_seleccion_atom.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_split_images_convert_to_tif_dron_selector.setStyleSheet("color: rgb(238, 118, 60)")
            self.cob_split_images_convert_to_tif_dron_selector.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.lb_split_images_convert_to_tif_emissivity.setEnabled(False)
            self.sb_split_images_convert_to_tif_emissivity.setEnabled(False)
            self.lb_split_images_convert_to_tif_humidity.setEnabled(False)
            self.sb_split_images_convert_to_tif_humidity.setEnabled(False)
            self.cb_split_images_convert_to_tif_temp_auto.setEnabled(False)
            self.gb_split_images_convert_to_tiff_rotate.setEnabled(False)
            self.lb_split_images_convert_to_tif_up_temperature.setEnabled(False)
            self.sb_split_images_convert_to_tif_up_temperature.setEnabled(False)
            self.lb_split_images_convert_to_tif_low_temperature.setEnabled(False)
            self.sb_split_images_convert_to_tif_low_temperature.setEnabled(False)
            self.cb_split_images_convert_to_tif_create_gray_scale_images.setEnabled(False)
            self.cb_split_images_convert_to_tif_solo_seleccion_atom.setEnabled(False)
            self.lb_split_images_convert_to_tif_dron_selector.setEnabled(False)
            self.cob_split_images_convert_to_tif_dron_selector.setEnabled(False)

            self.lb_split_images_convert_to_tif_emissivity.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_split_images_convert_to_tif_emissivity.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_split_images_convert_to_tif_humidity.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_split_images_convert_to_tif_humidity.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.cb_split_images_convert_to_tif_temp_auto.setStyleSheet("color: rgb(144, 144, 144)")
            self.gb_split_images_convert_to_tiff_rotate.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_split_images_convert_to_tif_up_temperature.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_split_images_convert_to_tif_up_temperature.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_split_images_convert_to_tif_low_temperature.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_split_images_convert_to_tif_low_temperature.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.cb_split_images_convert_to_tif_create_gray_scale_images.setStyleSheet("color: rgb(144, 144, 144)")
            self.cb_split_images_convert_to_tif_solo_seleccion_atom.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_split_images_convert_to_tif_dron_selector.setStyleSheet("color: rgb(144, 144, 144)")
            self.cob_split_images_convert_to_tif_dron_selector.setStyleSheet("color: rgb(144, 144, 144)")

        if self.rb_gb_cropping_rgb_choose_mode_auto.isChecked():
            self.lb_gb_cropping_rgb_crop_percentage.setEnabled(False)
            self.sb_gb_cropping_rgb_crop_percentage.setEnabled(False)
            self.lb_gb_cropping_rgb_crop_percentage.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_cropping_rgb_crop_percentage.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
        elif self.rb_gb_cropping_rgb_choose_mode_manual.isChecked():
            self.lb_gb_cropping_rgb_crop_percentage.setEnabled(True)
            self.sb_gb_cropping_rgb_crop_percentage.setEnabled(True)
            self.lb_gb_cropping_rgb_crop_percentage.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_cropping_rgb_crop_percentage.setStyleSheet("color: rgb(238, 118, 60)")

        if self.rb_gb_split_images_choose_mode_cropping_mode_auto.isChecked():
            self.lb_gb_split_images_crop_percentage.setEnabled(False)
            self.sb_gb_split_images_crop_percentage.setEnabled(False)
            self.lb_gb_split_images_crop_percentage.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_split_images_crop_percentage.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
        elif self.rb_gb_gb_split_images_choose_cropping_mode_manual.isChecked():
            self.lb_gb_split_images_crop_percentage.setEnabled(True)
            self.sb_gb_split_images_crop_percentage.setEnabled(True)
            self.lb_gb_split_images_crop_percentage.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_split_images_crop_percentage.setStyleSheet("color: rgb(238, 118, 60)")

        if self.cb_gb_split_images_cropping_rgb.checkState().value:
            self.gb_gb_split_images_choose_cropping_mode.setEnabled(True)
            self.gb_gb_split_images_choose_cropping_mode.setStyleSheet("color: rgb(238, 118, 60)")
        else:
            self.gb_gb_split_images_choose_cropping_mode.setEnabled(False)
            self.gb_gb_split_images_choose_cropping_mode.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_split_images_crop_percentage.setEnabled(False)
            self.lb_gb_split_images_crop_percentage.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_split_images_crop_percentage.setEnabled(False)
            self.sb_gb_split_images_crop_percentage.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")

        if self.rb_gb_generate_thumbnails_choose_mode_auto.isChecked():
            self.lb_gb_gen_thumbnails_auto.setEnabled(True)
            self.lb_gb_gen_thumbnails.setEnabled(True)
            self.sb_gb_generate_thumbnails_max_error.setEnabled(True)
            self.lb_gb_gen_thumbnails_add_to_angle.setEnabled(True)
            self.sb_gb_generate_thumbnails_add_to_angle.setEnabled(True)
            self.lb_gb_gen_thumbnails_subs_to_angle.setEnabled(True)
            self.sb_gb_generate_thumbnails_subs_to_angle.setEnabled(True)
            self.gb_gb_gen_thumbnails_rotate_manual_value.setEnabled(False)
            self.lb_gb_gen_thumbnails_auto.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_gen_thumbnails.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.lb_gb_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_generate_thumbnails_max_error.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_generate_thumbnails_add_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.sb_gb_generate_thumbnails_subs_to_angle.setStyleSheet("color: rgb(238, 118, 60)")
            self.gb_gb_gen_thumbnails_rotate_manual_value.setStyleSheet("color: rgb(144, 144, 144)")
        elif self.rb_gb_generate_thumbnails_choose_mode_manual.isChecked():
            self.lb_gb_gen_thumbnails_auto.setEnabled(False)
            self.lb_gb_gen_thumbnails.setEnabled(False)
            self.sb_gb_generate_thumbnails_max_error.setEnabled(False)
            self.lb_gb_gen_thumbnails_add_to_angle.setEnabled(False)
            self.sb_gb_generate_thumbnails_add_to_angle.setEnabled(False)
            self.lb_gb_gen_thumbnails_subs_to_angle.setEnabled(False)
            self.sb_gb_generate_thumbnails_subs_to_angle.setEnabled(False)
            self.gb_gb_gen_thumbnails_rotate_manual_value.setEnabled(True)

            self.lb_gb_gen_thumbnails_auto.setStyleSheet("color: rgb(144, 144, 144)")
            self.lb_gb_gen_thumbnails.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_generate_thumbnails_max_error.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_gen_thumbnails_add_to_angle.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_generate_thumbnails_add_to_angle.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.lb_gb_gen_thumbnails_subs_to_angle.setStyleSheet("color: rgb(144, 144, 144)")
            self.sb_gb_generate_thumbnails_subs_to_angle.setStyleSheet("border: 1px solid rgb(144,144,144);color: rgb(144, 144, 144);")
            self.gb_gb_gen_thumbnails_rotate_manual_value.setStyleSheet("color: rgb(238, 118, 60)")
       
           
    
    def test(self):
        """Función usada para testear funcionalidades"""
        #### Test images
        # from exif_data import exif_management
        # self.exif_management_obj = exif_management.GeneralInformationFromImage()
        # Chek xmp data
        # keys, list_keys = self.exif_management_obj.check_xmp_data_using_pyexiv2("D:/Documentos/Projects/In_Development/28-AERO_PR0028_2022/5-ExperimentalData/ATOM-PREPROCESSOR/Test_input_folder/PRU/DJI_0002_W.JPG")
        # if keys: print(list_keys)
        # else: print("OK")
        # keys, list_keys = self.exif_management_obj.check_xmp_data_using_pyexiv2("D:/Documentos/Projects/In_Development/28-AERO_PR0028_2022/5-ExperimentalData/ATOM-PREPROCESSOR/Test_input_folder/20251217_113043_DJI_0002_W_Solo_Compresion.JPG")
        # if keys: print(list_keys)
        # else: print("OK")
        # keys, list_keys = self.exif_management_obj.check_xmp_data_using_pyexiv2("D:/Documentos/Projects/In_Development/28-AERO_PR0028_2022/5-ExperimentalData/ATOM-PREPROCESSOR/Test_input_folder/20251217_113043_DJI_0002_W_Comprimida_y_Girada.JPG")
        # if keys: print(list_keys)
        # else: print("OK")

        # dir = "D:\\Documentos\\Projects\\In_Development\\28-AERO_PR0028_2022\\5-ExperimentalData\\DJI_Thermal_analysis\\Images\\test"
        # for image in self.utils_obj.get_images_from_dir(dir):
        #     # self.exif_management_obj.obtener_gimbal_yaw(os.path.join(dir, image))
        #     print("##############################################")
        #     print(self.exif_management_obj.get_all_exif_data(os.path.join(dir, image)))
        #     print(self.exif_management_obj.get_gimbal_yaw_pitch(os.path.join(dir, image)))
        #     print(self.exif_management_obj.use_exifread(os.path.join(dir, image)))
        # self.lb_gb_split_images_mismatch_hours.setToolTip("Test")
        # print(self.cob_gb_convert_to_tif_dron_selector.currentText())
        # self.split_images_obj.set_stop(True)
        # self.show_log_window("PRUEBA")
        # pb_vuelo = self.rgb_processing_obj.get_pbx_vx(self.le_gb_rgb_aerotools_estad.text(), int(self.le_gb_rgb_aerotools_input_folder.text()))
        # if pb_vuelo[0] is not None and pb_vuelo[1] is not None:
        #     print("El PB es el {0} y el Vuelo es el {1}".format(pb_vuelo[0], pb_vuelo[1]))
        #     print("El vuelo del PB {0} y Vuelo {1} es el número {2}".format(pb_vuelo[0], pb_vuelo[1], self.rgb_processing_obj.get_flight_line(self.le_gb_rgb_aerotools_estad.text(), pb_vuelo[0], pb_vuelo[1])))
        # self.rgb_processing_obj.geo_etiquetar_pbx_vx_folder_with_errors("D:\\Documentos\\Projects\\In_Development\\28-AERO_PR0028_2022\\5-ExperimentalData\\ATOM-PREPROCESSOR\\Test_output_folder\\RGB\\PB15\\PB15_V2")

    def editar_config(self):
        """Función que muestra una ventana centrada en configurar parámetros"""
        self.new_config_gui = ConfigWindow(self.config_obj)
        self.new_config_gui.show()

    def cargar_config(self):
        """Función que carga una configuración para mostrar en la pantalla de configuración a partir de una archivo"""
        file_name, _ = QtWidgets.QFileDialog.getOpenFileName()
        if file_name != "":
            self.config_obj.load_new_config(file_name)

    def show_log_window(self, title: str):
        """
        Función que muestra la pantalla de log con información del proceso actual
        
        Arguments:
        ---------
        - title - Título de la ventana del log.
        """
        self.new_log_gui.setWindowTitle(title)
        self.new_log_gui.show()

    def _capture_run_config(self) -> RunConfig:
        """Captura TODOS los valores de los widgets de la UI en el hilo GUI, ANTES de lanzar el Worker.
        Debe llamarse siempre desde run_thread, nunca desde dentro de una función de negocio ya en el hilo worker."""
        return RunConfig(
            compress_rgbs=CompressRgbsConfig(
                input_folder=self.le_gb_compress_rgbs_input_folder.text(),
                output_folder=self.le_gb_compress_rgbs_output_folder.text(),
                compress_level=int(self.sb_compress_level.text()),
                include_subfolders=self.cb_gb_compress_rgbs_include_subfolders.checkState().value,
            ),
            gen_meta_location=GenMetaLocationConfig(
                input_folder=self.le_gb_gen_meta_location_input_folder.text(),
                flight_height=float(self.sb_gb_gen_meta_location_flight_height.text().replace(',', '.')),
                calculate_proyected_distance=self.cb_gb_gen_meta_location_calculate_proyected_distance.isChecked(),
                mode_meta=self.rb_gb_gen_meta.isChecked(),
                mode_location=self.rb_gb_gen_location.isChecked(),
                mode_location_and_meta=self.rb_gb_gen_location_and_meta.isChecked(),
            ),
            split_images=SplitImagesConfig(
                input_folder=self.le_gb_split_images_input_folder.text(),
                output_folder=self.le_gb_split_images_output_folder.text(),
                end_rgb_extra_files=self.le_gb_split_images_end_rgb_extra_files.text(),
                end_thermo_files=self.le_gb_split_images_end_thermo_files.text(),
                end_rgb_files=self.le_gb_split_images_end_rgb_files.text(),
                estad=self.le_gb_split_images_estad.text(),
                choose_mode_size=self.rb_gb_choose_mode_size.isChecked(),
                max_size=self.sb_gb_split_images_max_size.text(),
                compress_rgb=self.cb_gb_split_images_compress_rgb.isChecked(),
                compress_level=int(self.sb_gb_split_images_compress_level.text()),
                rename_images=self.cb_gb_split_images_rename_images.isChecked(),
                mismatch_hours=int(self.sb_gb_split_images_mismatch_hours.text()),
                mismatch_minutes=int(self.sb_gb_split_images_mismatch_minutes.text()),
                organize_images=self.cb_gb_split_images_organize_images.isChecked(),
                cropping_rgb=self.cb_gb_split_images_cropping_rgb.isChecked(),
                cropping_mode_auto=self.rb_gb_split_images_choose_mode_cropping_mode_auto.isChecked(),
                crop_percentage=self.sb_gb_split_images_crop_percentage.text(),
                gen_meta_location=self.cb_gb_split_images_gen_meta_location.isChecked(),
                gen_thumbnails=self.cb_gb_split_images_gen_thumbnails.isChecked(),
                seconds_range=float(self.sb_gb_split_images_seconds_range.text()),
                include_v=self.cb_gb_split_images_include_v.isChecked(),
                calculate_proyected_distance=self.cb_gb_split_images_calculate_proyected_distance.isChecked(),
                flight_height=float(self.sb_gb_split_images_flight_height.text().replace(',', '.')),
                gen_thumbnails_rotate_90=self.rb_gb_split_images_gen_thumbnails_rotate_90.isChecked(),
                gen_thumbnails_add_to_angle=int(self.sb_gb_split_images_gen_thumbnails_add_to_angle.text()),
                gen_thumbnails_max_error=int(self.sb_gb_split_images_gen_thumbnails_max_error.text()),
                gen_thumbnails_subs_to_angle=int(self.sb_gb_split_images_gen_thumbnails_subs_to_angle.text()),
                choose_mode_auto=self.rb_gb_split_images_choose_mode_auto.isChecked(),
                gen_thumbnails_rgb=self.cb_gb_gen_thumbnails_rgb_2.isChecked(),
                gen_thumbnails_termica=self.cb_gb_gen_thumbnails_termica_2.isChecked(),
                convert_to_tif=self.cb_split_images_convert_to_tif.isChecked(),
                convert_to_tif_dron_selector=self.cob_split_images_convert_to_tif_dron_selector.currentText(),
                convert_to_tif_emissivity=float(self.sb_split_images_convert_to_tif_emissivity.text().replace(",", ".")),
                convert_to_tif_humidity=float(self.sb_split_images_convert_to_tif_humidity.text().replace(",", ".")),
                convert_to_tif_temp_auto=self.cb_split_images_convert_to_tif_temp_auto.checkState().value,
                convert_to_tif_up_temperature=float(self.sb_split_images_convert_to_tif_up_temperature.text().replace(",", ".")),
                convert_to_tif_low_temperature=float(self.sb_split_images_convert_to_tif_low_temperature.text().replace(",", ".")),
                convert_to_tiff_rotate_90=self.rb_split_images_convert_to_tiff_rotate_90.isChecked(),
                convert_to_tiff_rotate_minus_90=self.rb_split_images_convert_to_tiff_rotate_minus_90.isChecked(),
                convert_to_tiff_rotate_auto=self.rb_split_images_convert_to_tiff_rotate_auto.isChecked(),
                convert_to_tif_solo_seleccion_atom=self.cb_split_images_convert_to_tif_solo_seleccion_atom.isChecked(),
                convert_to_tif_create_gray_scale_images=self.cb_split_images_convert_to_tif_create_gray_scale_images.isChecked(),
            ),
            gen_thumbnails=GenThumbnailsConfig(
                input_folder=self.le_gb_generate_thumbnails_input_folder.text(),
                rgb=self.cb_gb_gen_thumbnails_rgb.isChecked(),
                termica=self.cb_gb_gen_thumbnails_termica.isChecked(),
                termica_2=self.cb_gb_gen_thumbnails_termica_2.isChecked(),
                add_to_angle=int(self.sb_gb_generate_thumbnails_add_to_angle.text()),
                subs_to_angle=int(self.sb_gb_generate_thumbnails_subs_to_angle.text()),
                max_error=int(self.sb_gb_generate_thumbnails_max_error.text()),
                choose_mode_auto=self.rb_gb_generate_thumbnails_choose_mode_auto.isChecked(),
                rotate_90=self.rb_gb_gen_thumbnails_rotate_90.isChecked(),
                aerotools_input_folder=self.le_gb_generate_thumbnails_aerotools_input_folder.text(),
                aerotools_rgb=self.cb_gb_gen_thumbnails_aerotools_rgb.isChecked(),
                aerotools_termica=self.cb_gb_gen_thumbnails_aerotools_termica.isChecked(),
                aerotools_add_to_angle=int(self.sb_gb_generate_thumbnails_aerotools_add_to_angle.text()),
                aerotools_subs_to_angle=int(self.sb_gb_generate_thumbnails_aerotools_subs_to_angle.text()),
                aerotools_max_error=int(self.sb_gb_generate_thumbnails_aerotools_max_error.text()),
            ),
            rename_images=RenameImagesConfig(
                output_folder=self.le_gb_rename_images_output_folder.text(),
            ),
            extraction=ExtractionConfig(
                folder=self.le_gb_extraction_folder.text(),
                output_folder=self.le_gb_extraction_output_folder.text(),
                estad=self.le_gb_extraction_estad.text(),
                auto=self.cb_gb_extraction_auto.isChecked(),
                pre_extraction=self.cb_gb_extraction_pre_extraction.isChecked(),
                extraction=self.cb_gb_extraction_extraction.isChecked(),
                temp_max=int(self.sb_gb_extraction_temp_max.text()),
                temp_min=int(self.sb_gb_extraction_temp_min.text()),
            ),
            gen_struct_folder=GenStructFolderConfig(
                output_folder=self.le_gb_gen_folder_struct_output_folder.text(),
                estad=self.le_gb_gen_folder_struct_estad.text(),
                organize_images=self.cb_gb_gen_folder_struct_organize_images.isChecked(),
                include_v=self.cb_gb_gen_folder_struct_include_v.isChecked(),
                seconds_range=float(self.sb_gb_gen_folder_struct_seconds_range.text()),
                mismatch_hours=int(self.sb_gb_gen_folder_struct_mismatch_hours.text()),
                mismatch_minutes=int(self.sb_gb_gen_folder_struct_mismatch_minutes.text()),
            ),
            rgb_aerotools=RgbAerotoolsConfig(
                input_folder=self.le_gb_rgb_aerotools_input_folder.text(),
                output_folder=self.le_gb_rgb_aerotools_output_folder.text(),
                estad=self.le_gb_rgb_aerotools_estad.text(),
                logs_folder=self.le_gb_rgb_aerotools_logs_folder.text(),
                compress_rgb=self.cb_gb_rgb_aerotools_compress_rgb.isChecked(),
                compress_level=int(self.sb_gb_rgb_aerotools_compress_level.text()),
                geotagging=self.cb_gb_rgb_aerotools_geotagging.isChecked(),
                organize_images=self.cb_gb_rgb_aerotools_organize_images.isChecked(),
                rename_images=self.cb_gb_rgb_aerotools_rename_images.isChecked(),
                mismatch_hours=int(self.sb_gb_rgb_aerotools_mismatch_hours.text()),
                mismatch_minutes=int(self.sb_gb_rgb_aerotools_mismatch_minutes.text()),
                seconds_range_image_estad=float(self.sb_gb_rgb_aerotools_seconds_range_image_estad.text()),
                seconds_range_log_estad=float(self.sb_gb_rgb_aerotools_seconds_range_log_estad.text()),
            ),
            manual_geotagging=ManualGeotaggingConfig(
                select_images_folder=self.le_gb_manual_geotagging_select_images_folder.text(),
                select_log=self.le_gb_manual_geotagging_select_log.text(),
                logs_folder=self.le_gb_manual_geotagging_logs_folder.text(),
                estad=self.le_gb_manual_geotagging_estad.text(),
                rgb_aerotools_logs_folder=self.le_gb_rgb_aerotools_logs_folder.text(),
            ),
            convert_to_tif=ConvertToTifConfig(
                input_folder=self.le_gb_convert_to_tif_input_folder.text(),
                create_gray_scale_images=self.cb_gb_convert_to_tif_create_gray_scale_images.isChecked(),
                solo_seleccion_atom=self.cb_gb_convert_to_tif_solo_seleccion_atom.isChecked(),
                temp_auto=self.cb_gb_convert_to_tif_temp_auto.checkState().value,
                emissivity=float(self.sb_gb_convert_to_tif_emissivity.text().replace(",", ".")),
                humidity=float(self.sb_gb_convert_to_tif_humidity.text().replace(",", ".")),
                low_temperature=float(self.sb_gb_convert_to_tif_low_temperature.text().replace(",", ".")),
                up_temperature=float(self.sb_gb_convert_to_tif_up_temperature.text().replace(",", ".")),
                rotate_90=self.rb_gb_convert_to_tiff_rotate_90.isChecked(),
                rotate_minus_90=self.rb_gb_convert_to_tiff_rotate_minus_90.isChecked(),
                rotate_auto=self.rb_gb_convert_to_tiff_rotate_auto.isChecked(),
                dron_selector=self.cob_gb_convert_to_tif_dron_selector.currentText(),
            ),
            rgb_cropping=RgbCroppingConfig(
                output_folder=self.le_gb_cropping_rgb_output_folder.text(),
                choose_mode_auto=self.rb_gb_cropping_rgb_choose_mode_auto.isChecked(),
                crop_percentage=self.sb_gb_cropping_rgb_crop_percentage.text(),
            ),
            tif_rotating=TifRotatingConfig(
                input_folder_2=self.le_gb_rotate_tif_input_folder_2.text(),
                rotate_180=self.rb_gb_rotate_tiff_rotate_180.isChecked(),
                rotate_90=self.rb_gb_rotate_tiff_rotate_90.isChecked(),
                rotate_minus_90=self.rb_gb_rotate_tiff_rotate_minus_90.isChecked(),
            ),
        )

    #%% Funciones centradas en los threads.
    def run_thread(self, function_to_call: str):
        """
        Función que arranca el hilo asociado a un procesado específico.
        
        Arguments:
        ---------
        - function_to_call - función a la que llamar y que se procesará en un hilo aparte del principal dentro de QThreadPool.
        """
        if not can_start(self._busy, self.threadpool.activeThreadCount()):
            self.new_log_gui.te_summarize_process.appendPlainText("\nYa hay un procesado en curso. Espera a que termine.\n")
            return
        self._busy = True
        self._set_launch_buttons_enabled(False)

        # Se captura TODA la configuración de la UI aquí, en el hilo GUI, ANTES de arrancar ningún Worker.
        # Las funciones de negocio (call_to_compress_image, split_images, etc.) ya NO leen self.le_*/self.sb_*/self.cb_*/self.rb_* directamente:
        # reciben su sub-config correspondiente como argumento, evitando así el acceso a widgets Qt desde el hilo del QThreadPool.
        cfg = self._capture_run_config()

        if function_to_call == "split_images":
            # Se crea un Worker con la función especificada por function_to_call
            worker = Worker(self.split_images, cfg.split_images) # Any other args, kwargs are passed to the run function
            # A partir de aquí se resetean ciertas variables necesarias para empezar otra función y otro hilo.
            # También se muestra información en la ventana de log.
            self.prepare_log("Separar imágenes", "PROCESO PRINCIPAL: ORGANIZAR COMPLETO", self.split_images_obj, self.le_gb_split_images_input_folder.text())
        elif function_to_call == "gen_meta_location":
            worker = Worker(self.gen_meta_location, cfg.gen_meta_location) # Any other args, kwargs are passed to the run function
            self.prepare_log("Generar meta/location", "PROCESO PRINCIPAL: GENERAR META/LOCATION", self.meta_location_obj, self.le_gb_gen_meta_location_input_folder.text())
        elif function_to_call == "compress_image":
            self.prepare_log("Comprimir imágenes", "PROCESO PRINCIPAL: COMPRIMIR IMÁGENES", self.compress_image_obj, self.le_gb_compress_rgbs_input_folder.text())
            worker = Worker(self.call_to_compress_image, cfg.compress_rgbs) # Any other args, kwargs are passed to the run function
        elif function_to_call == "gen_thumbnails":
            worker = Worker(self.gen_thumbnails, cfg.gen_thumbnails) # Any other args, kwargs are passed to the run function

            # if self.cb_gb_gen_thumbnails_rgb.isChecked() and not self.cb_gb_gen_thumbnails_termica.isChecked():
            #     dir_input_folder = os.path.join(self.le_gb_generate_thumbnails_input_folder.text(), "RGB")
            # elif self.cb_gb_gen_thumbnails_termica.isChecked() and not self.cb_gb_gen_thumbnails_rgb.isChecked():
            #     dir_input_folder = os.path.join(self.le_gb_generate_thumbnails_input_folder.text(), "TERMICA")
            # elif self.cb_gb_gen_thumbnails_rgb.isChecked() and self.cb_gb_gen_thumbnails_termica.isChecked():

            # Ahora le damos como parámetro de entrada la carpeta que sea que hayamos elegido en el interfaz. En una versión anterior, dependiendo de si habíamos elegido RGB o TERMICA o las dos, el dir_input_folder cambiaba y ahora no. Es debido a un requerimiento de ellos, ya que querían generar las miniaturas en cualquier carpeta.
            # Ahora podemos preparar los datos del log con cualquier carpeta que se elija.
            dir_input_folder =self.le_gb_generate_thumbnails_input_folder.text()

            self.prepare_log("Rotar","PROCESO PRINCIPAL: ROTAR", self.gen_struct_folder_obj, dir_input_folder)
        elif function_to_call == "rename_images":
            worker = Worker(self.rename_images, cfg.rename_images) # Any other args, kwargs are passed to the run function
            self.prepare_log("Renombrar imágenes", "PROCESO PRINCIPAL: RENOMBRAR IMÁGENES", self.gen_struct_folder_obj, self.le_gb_rename_images_output_folder.text())
        elif function_to_call == "do_extraction":
            worker = Worker(self.do_extraction, cfg.extraction) # Any other args, kwargs are passed to the run function
            self.prepare_log("Pre-extracción/Extracción", "PROCESO PRINCIPAL: PRE-EXTRACCIÓN/EXTRACCIÓN", self.extraction_obj, self.le_gb_extraction_folder.text(), tmc=True)
        elif function_to_call == "gen_struct":
            worker = Worker(self.gen_struct_folder, cfg.gen_struct_folder) # Any other args, kwargs are passed to the run function
            self.prepare_log("Generar estructura", "PROCESO PRINCIPAL: GENERAR ESTRUCTURA DE CARPETAS", self.gen_struct_folder_obj, self.le_gb_gen_folder_struct_output_folder.text())
        elif function_to_call == "rgb_aerotools":
            worker = Worker(self.do_rgb_aerotools_processing, cfg.rgb_aerotools) # Any other args, kwargs are passed to the run function
            self.prepare_log("Procesamiento de RGB de AEROTOOLS", "PROCESO PRINCIPAL: PROCESAMIENTO DE RGB DE AEROTOOLS", self.rgb_processing_obj, os.path.join(self.le_gb_rgb_aerotools_input_folder.text(),"RGB"))
        elif function_to_call == "gen_thumbnails_aerotools":
            worker = Worker(self.gen_thumbnails_for_aerotools, cfg.gen_thumbnails) # Any other args, kwargs are passed to the run function

            # if self.cb_gb_gen_thumbnails_aerotools_rgb.isChecked() and not self.cb_gb_gen_thumbnails_aerotools_termica.isChecked():
            #     dir_input_folder = os.path.join(self.le_gb_generate_thumbnails_aerotools_input_folder.text(), "RGB")
            # elif self.cb_gb_gen_thumbnails_aerotools_termica.isChecked() and not self.cb_gb_gen_thumbnails_aerotools_rgb.isChecked():
            #     dir_input_folder = os.path.join(self.le_gb_generate_thumbnails_aerotools_input_folder.text(), "TERMICA")
            # elif self.cb_gb_gen_thumbnails_aerotools_rgb.isChecked() and self.cb_gb_gen_thumbnails_aerotools_termica.isChecked():

            # De igual modo que para "gen_thumbnails", ahora le damos como parámetro de entrada la carpeta que sea que hayamos elegido en el interfaz. En una versión anterior, dependiendo de si habíamos elegido RGB o TERMICA o las dos, el dir_input_folder cambiaba y ahora no. Es debido a un requerimiento de ellos, ya que querían generar las miniaturas en cualquier carpeta.
            # Ahora podemos preparar los datos del log con cualquier carpeta que se elija.
            dir_input_folder = self.le_gb_generate_thumbnails_aerotools_input_folder.text()

            self.prepare_log("Rotar AEROTOOLS","PROCESO PRINCIPAL: ROTAR IMÁGENES AEROTOOLS", self.gen_struct_folder_obj, dir_input_folder)
        elif function_to_call == "manual_geotagging":
            worker = Worker(self.do_manual_geotagging, cfg.manual_geotagging) # Any other args, kwargs are passed to the run function
            self.prepare_log("Geoetiquetar manualmente una serie de imágenes","PROCESO PRINCIPAL: GEOETIQUETADO MANUAL", self.rgb_processing_obj, self.le_gb_manual_geotagging_select_images_folder.text())
        elif function_to_call == "convert_to_tif":
            worker = Worker(self.do_convert_to_tif, cfg.convert_to_tif) # Any other args, kwargs are passed to the run function
            self.prepare_log("Convertir imágenes DJI a TIF", "PROCESO PRINCIPAL: CONVERSIÓN DE IMÁGENES DJI A TIF", self.split_images_obj, self.le_gb_convert_to_tif_input_folder.text())
        elif function_to_call == "rgb_cropping":
            worker = Worker(self.do_rgb_cropping, cfg.rgb_cropping) # Any other args, kwargs are passed to the run function
            self.prepare_log("Recortar imágenes RGB", "PROCESO PRINCIPAL: RECORTAR IMÁGENES RGB", self.rgb_cropping_obj, self.le_gb_cropping_rgb_output_folder.text())
        elif function_to_call == "tif_rotating":
            worker = Worker(self.do_tif_rotating, cfg.tif_rotating) # Any other args, kwargs are passed to the run function
            self.prepare_log("Girar imágenes TIFF", "PROCESO PRINCIPAL: GIRAR IMÁGENES TIFF", self.gen_struct_folder_obj, self.le_gb_rotate_tif_input_folder_2.text())


        # Unimos cada señal con su función específica.
        worker.signals.error.connect(self.print_error)
        worker.signals.result.connect(self.print_output)
        worker.signals.finished.connect(self.thread_complete)
        worker.signals.progress.connect(self.thread_progress)
        worker.signals.progress_bar.connect(self.thread_progress_bar)
        worker.signals.progress_summarize.connect(self.thread_progress_summarize)

        # Execute
        self.threadpool.start(worker)

    def prepare_log(self, log_window_title: str, log_process_title: str, obj: object, input_folder: str, tmc: bool = False):
        """
        Función que realiza ciertas funciones necesarias antes de cada procesado con el objetivo de que la función llamada muestre
        información en la ventana del log durante el proceso.
        Inicialmente escribe en la ventana el proceso que se inicia.

        Arguments:
        ---------
        - log_window_title - Es el título de la propia ventana del log. Se modifica cada vez que se inicia un procesado.
        - log_process_title - Título del procesado en el propio log, con el objetivo de poder diferenciar cuando se inicia uno nuevo.
        - obj - El objeto de la clase que incluye las funciones de procesado.
        - input_folder - La carpeta que tiene todas las imágenes a procesar.
        - tmc - indica si tenemos que contar imágenes o archivos tmc.
        """
        self.new_log_gui.te_summarize_process.appendPlainText("###################################################################")
        self.new_log_gui.te_summarize_process.appendPlainText(log_process_title)
        self.new_log_gui.te_summarize_process.appendPlainText("###################################################################")

        self.new_log_gui.te_log.appendPlainText("###################################################################")
        self.new_log_gui.te_log.appendPlainText(log_process_title)
        self.new_log_gui.te_log.appendPlainText("###################################################################")

        self.new_log_gui.set_obj(obj)
        self.new_log_gui.enable_process()
        self.new_log_gui.pb_process.setValue(0)
        obj.reset_variables()
        obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(input_folder, tmc=tmc, exclude_patterns=["_CROP"], exclude_folders=["MINIATURAS", "CSVs"])
        self.new_log_gui.te_summarize_process.appendPlainText("Número total de imágenes a procesar: {0}".format(obj.total_images_number, 0))
        self.show_log_window(log_window_title)

    def print_output(self, s: str) -> None:
        """
        Función que muestra por consola el resultado del hilo dentro del QThreadPool
        
        Arguments:
        ---------
        - s - información enviada por el hilo.
        """
        self.organizer_logger_obj.logger.info(s)
    
    def print_error(self, s: tuple) -> None:
        """
        Función que muestra el error del hilo tanto en consola como en la ventana de log.
        
        Arguments:
        ---------
        - s - tupla (exctype, value, traceback_str) enviada por el hilo al producirse una excepción.
        """
        exctype, value, traceback_str = s
        error_msg = f"\nERROR EN EL PROCESO:\n{traceback_str}\n"
        self.organizer_logger_obj.logger.debug(error_msg)
        try:
            self.new_log_gui.te_log.appendPlainText(error_msg)
            self.new_log_gui.te_summarize_process.appendPlainText(f"\nERROR: {exctype.__name__}: {value}\n")
        except Exception:
            pass

    def thread_complete(self):
        """Función que muestra información por pantalla cuando el hilo se ha finalizado"""
        self.organizer_logger_obj.logger.info("PROCESS AND THREAD COMPLETE!")
        try:
            # summarize_dict = self.new_log_gui.obj.get_summarize()
            # for clave, valor in summarize_dict.items():
            #     self.new_log_gui.te_summarize_process.appendPlainText(clave + ": " + str(valor))
            self.new_log_gui.te_log.appendPlainText("\nPROCESADO FINALIZADO!\n")
            self.new_log_gui.te_summarize_process.appendPlainText("\nPROCESADO FINALIZADO!\n")
        except Exception as e:
            self.organizer_logger_obj.logger.debug(e.__str__)
            self.organizer_logger_obj.logger.debug(e)
        self._busy = False
        self.new_log_gui.stop_gate.disarm()
        self._set_launch_buttons_enabled(True)

    def _set_launch_buttons_enabled(self, enabled: bool) -> None:
        """Habilita/deshabilita todos los botones que lanzan un procesado vía run_thread, para impedir
        que se lance un segundo Worker mientras el primero sigue corriendo."""
        for button in self._launch_buttons:
            button.setEnabled(enabled)


    def thread_progress(self, s: str) -> None:
        """
        Función que muestra información por pantalla enviada por el hilo durante el procesado.
        
        Arguments:
        ---------
        - s - información enviada por el hilo.
        """
        self.new_log_gui.te_log.moveCursor(QTextCursor.MoveOperation.End)
        self.new_log_gui.te_log.insertPlainText(s)
        # print(s)
    
    def thread_progress_bar(self, n: int) -> None:
        """
        Función que actualiza el estado de la barra de progreso, teniendo en cuenta información enviada por el hilo.
        Normalmente se refiere a la cantidad de imágenes a procesar respecto a la cantidad actual de imágenes procesadas.
        
        Arguments:
        ---------
        - n - información de la proporción de procesado actual enviada por el hilo y que se muestra en una barra de progreso.
        """
        self.new_log_gui.pb_process.setValue(n)

    def thread_progress_summarize(self, s: str) -> None:
        """
        Función que recibe texto desde el hilo de trabajo y lo escribe en el widget de resumen en el hilo principal.
        
        Arguments:
        ---------
        - s - texto enviado por el hilo para mostrar en te_summarize_process.
        """
        self.new_log_gui.te_summarize_process.appendPlainText(s)

    def init_logger(self):
        """Función que crea un archivo de log con el objetivo de almacenar datos del procesado. Si existe un log se crea otro con 
        un número correlativo"""
        self.utils_obj.prepare_output_folder(".", ["Logs"])
        new_file_name = self.utils_obj.rename_log("Logs/logs_info.log")
        logging.basicConfig(
            format = '%(asctime)-5s %(name)-15s %(levelname)-8s %(message)s',
            level  = logging.INFO,      # Nivel de los eventos que se registran en el logger
            filename = new_file_name, # Fichero en el que se escriben los logs
            filemode = "a"              # a ("append"), en cada escritura, si el archivo de logs ya existe,
                                        # se abre y añaden nuevas lineas.
        )
        logger = logging.getLogger()
        logger.disabled = True
    
    def enable_disable_logger(self, enable):
        """Función para habilitar o deshabilitar la escritura en el archivo del log"""
        # logger = logging.getLogger()
        # logger.disabled = not enable
        # print(f"Enable está a {enable}")

        if not hasattr(self.organizer_logger_obj, "file_handler"):
            self.organizer_logger_obj.create_file_handler()

        if (enable):
            self.init_new_logger()
        
        if(not enable):
            self.organizer_logger_obj.set_handler_enabled("file", enable)

    def init_new_logger(self):
        self.organizer_logger_obj.set_handlers()
        self.organizer_logger_obj.set_formatter()
        self.organizer_logger_obj.set_exception_handler()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    # Escalado propio y determinista por pantalla: desactivamos el HiDPI de Qt y descartamos
    # cualquier QT_SCALE_FACTOR fijo (que hacía la UI enorme en pantallas pequeñas como el portátil).
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    # Escalado por DPI a cargo de Qt (no un multiplicador propio en px). Requiere que Qt sepa la
    # escala real de cada monitor: eso lo da Wayland, no XWayland/xcb (que reporta 96 DPI y dpr=1
    # para todos). Si no se forzó una plataforma y hay sesión Wayland, usarla.
    if not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
    # Respetar escalas fraccionarias (1.25 del portátil) en vez de redondearlas a 2.0.
    QtGui.QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QtWidgets.QApplication(sys.argv)
    _f = app.font()
    _f.setPointSizeF(10.0)  # puntos: escalan con el DPI del monitor donde esté la ventana
    app.setFont(_f)
    window = MainWindow()
    window.show()
    app.exec()