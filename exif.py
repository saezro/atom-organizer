import os
import re
import math
import shutil
import datetime
import numpy as np
import pandas as pd
import PIL
import PIL.Image
import PIL.ExifTags
from PIL.ExifTags import TAGS, GPSTAGS
import exifread
import pyexiv2
import utils
from geopy import distance
from geopy.point import Point

# El namespace drone-dji se registra una única vez al importar el módulo.
# Llamar a registerNs varias veces con el mismo namespace puede provocar un abort() en libexiv2.
pyexiv2.registerNs('Dronedji Namespace', 'drone-dji')

class GeneralInformationFromImage:
    """
    Clase que utilizaremos para pruebas con el exif de las imágenes o para obtener información general del mismo.

    Methods
    -------

    """
    def __init__(self, organizer_logger: "utils.OrganizerLogger") -> None:
        self.utils_obj = utils.Utils(organizer_logger)
        self.organizer_logger = organizer_logger
        self.error_exif_data = 0
        self.images_error_exif_data = []


    def get_all_exif_data(self, filename: str) -> dict:
        """Returns a dictionary from the exif data of an PIL Image item. Also converts the GPS Tags"""
        image = PIL.Image.open(filename)
        exif_data = {}
        info = image._getexif()
        if info:
            for tag, value in info.items():
                decoded = TAGS.get(tag, tag)
                if decoded == "GPSInfo":
                    gps_data = {}
                    for t in value:
                        sub_decoded = GPSTAGS.get(t, t)
                        gps_data[sub_decoded] = value[t]
                    exif_data[decoded] = gps_data
                else:
                    exif_data[decoded] = value
        image.close()
        return exif_data

    def use_exifread(self, filename: str) -> None:
        """Prints all the tags in the exif file, except 'JPEGThumbnail', 'TIFFThumbnail', 'Filename' or the tags that you don't want"""
        # Open image file for reading (must be in binary mode)
        f = open(filename, 'rb')
        # Return Exif tags
        tags = exifread.process_file(f, details=True)
        self.organizer_logger.logger.info(tags.keys())
        for tag in tags.keys():
            if tag not in ('JPEGThumbnail', 'TIFFThumbnail', 'Filename'):
                self.organizer_logger.logger.info(tag, tags[tag])

    def get_model(self, filename: str, progress_callback) -> str:
        try:
            f = open(filename, 'rb')
            tags = exifread.process_file(f, details=True)
            f.close()
            return str(tags["Image Model"])
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error("ERROR: No se encuentra la imagen {0} para poder obtener el modelo de dron.".format(filename))
            progress_callback.emit("ERROR: No se encuentra la imagen {0} para poder obtener el modelo de dron.".format(filename))
            self.organizer_logger.logger.exception(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_exif_data += 1
            self.images_error_exif_data.append(filename)
            return None
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.error("ERROR: Hay algún tipo de error con los datos de la imagen {0} y no se puede obtener el modelo de dron.".format(filename))
            progress_callback.emit("ERROR: Hay algún tipo de error con los datos de la imagen {0} y no se puede obtener el modelo de dron.".format(filename))
            self.organizer_logger.logger.exception(e.__str__)
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_exif_data += 1
            self.images_error_exif_data.append(filename)
            return None

    def fechaHora_DJI(self, pathImagen: str, progress_callback) -> str | None:
        """
        Función para obtener de las imágenes DJI su fecha y hora en un formato específico

        Arguments:
        ---------
        - pathImagen - path de la imagen de la que extraer los datos
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        """
        try:
            img = PIL.Image.open(pathImagen) #Abrimos el Exif
            datos_exif = img._getexif() # Obtenemos los datos del exif, que es un diccionario
            img.close() # Cerramos la imagen

            if datos_exif is not None:
                # La clave numero '36867' contiene los datos de la fecha de captura de la imagen.
                if 36867 not in datos_exif:
                    self.organizer_logger.logger.warning("ERROR: La imagen {0} no tiene el tag EXIF DateTimeOriginal (36867).".format(pathImagen))
                    progress_callback.emit("\nERROR: La imagen {0} no tiene el tag EXIF DateTimeOriginal.\n".format(pathImagen))
                    self.error_exif_data += 1
                    self.images_error_exif_data.append(pathImagen)
                    return None
                fechaHora = datos_exif[36867].split(' ')

                # El primer elemento de fecha_hora es la fecha, dividimos la fecha en sus distintas componentes
                fecha = fechaHora[0].split(':')

                # El segundo elemento de fecha_hora es la hora, dividimos la hora en sus distintas componentes
                hora = fechaHora[1].split(':')

                # Creamos el nuevo nombre para la imagen AñoMesDia_HoraMinutoSegundo
                fecha_hora = "".join(fecha)+"_"+"".join(hora)

                self.organizer_logger.logger.debug("Fecha y hora: " + fecha_hora)

                return fecha_hora
            else:
                return None
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: No se encuentra la imagen {0} para que PIL la pueda abrir.".format(pathImagen))
            progress_callback.emit("\nERROR: No se encuentra la imagen {0} para que PIL la pueda abrir.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_exif_data += 1
            self.images_error_exif_data.append(pathImagen)
            return None
        except PIL.UnidentifiedImageError as u:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: La imagen {0} no puede ser abierta e identificada.".format(pathImagen))
            progress_callback.emit("\nERROR: La imagen {0} no puede ser abierta e identificada.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(u.__str__)
            self.organizer_logger.logger.exception(u)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_exif_data += 1
            self.images_error_exif_data.append(pathImagen)
            return None
        except ValueError as v:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: El formato de salida no ha sido identificado para la imagen {0}.".format(pathImagen))
            progress_callback.emit("\nERROR: El formato de salida no ha sido identificado para la imagen {0}.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(v.__str__)
            self.organizer_logger.logger.exception(v)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_exif_data += 1
            self.images_error_exif_data.append(pathImagen)
            return None
        except TypeError as t:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: Excepción de tipo TypeError para la imagen {0}.".format(pathImagen))
            progress_callback.emit("\nERROR: Excepción de tipo TypeError para la imagen {0}.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(t.__str__)
            self.organizer_logger.logger.exception(t)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_exif_data += 1
            self.images_error_exif_data.append(pathImagen)
            return None

    def get_timestamp_from_image(self, pathImagen: str) -> datetime.datetime | None:
        """
        Función para obtener de las imágenes su fecha y hora en formato datetime

        Arguments:
        ---------
        - pathImagen - path de la imagen de la que extraer los datos
        """
        try:
            img = PIL.Image.open(pathImagen) #Abrimos el Exif
            datos_exif = img._getexif() # Obtenemos los datos del exif, que es un diccionario
            img.close() # Cerramos la imagen

            if datos_exif is not None:
                # La clave numero '36867' contiene los datos de la fecha de captura de la imagen.
                if 36867 not in datos_exif:
                    self.organizer_logger.logger.warning("ERROR: La imagen {0} no tiene el tag EXIF DateTimeOriginal (36867).".format(pathImagen))
                    return None
                fechaHora = datos_exif[36867].split(' ')
                timestamp_image = datetime.datetime.strptime(fechaHora[0]+'_'+fechaHora[1],'%Y:%m:%d_%H:%M:%S')
                return timestamp_image
            else:
                return None
            #TODO: Si la función devuelve None, no lo trato en las funciones que llaman a esta función, que por cierto, todas son de rgb_processing
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: No se encuentra la imagen {0} para que PIL la pueda abrir.".format(pathImagen))
            self.organizer_logger.logger.error(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            return None
        except PIL.UnidentifiedImageError as u:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: La imagen {0} no puede ser abierta e identificada.".format(pathImagen))
            self.organizer_logger.logger.error(u.__str__)
            self.organizer_logger.logger.exception(u)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            return None
        except ValueError as v:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: El formato de salida no ha sido identificado para la imagen {0}.".format(pathImagen))
            self.organizer_logger.logger.error(v.__str__)
            self.organizer_logger.logger.exception(v)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            return None
        except TypeError as t:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: Excepción de tipo TypeError para la imagen {0}.".format(pathImagen))
            self.organizer_logger.logger.error(t.__str__)
            self.organizer_logger.logger.exception(t)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            return None

    def get_gimbal_yaw_pitch(self, filename: str) -> list[str]:
        """
        Función que obtiene el GimbalYawDegree (Primer elemento de la lista) y el GimbalPitchDegree (Segundo elemento de la lista) de la imagen de entrada

        Arguments:
        ---------
        - filename - nombre de la imagen de la que extraer los datos
        """
        gimbal_yaw_degree = "0"
        gimbal_pitch_degree = "0"
        fd = open(filename, encoding = 'latin-1')  # Abrimos la imagen como si fuera un archivo normal
        d= fd.read()
        fd.close()
        xmp_start = d.find('<x:xmpmeta')
        xmp_end = d.find('</x:xmpmeta')
        xmp_str = d[xmp_start:xmp_end+12]  # Nos quedamos con la información xmp
        xmp_str_split = xmp_str.split('drone-dji:')  # Dividimos el string teniendo en cuenta "!"drone-dji:""
        GimbalYawDegree_found = False
        GimbalPitchDegree_found = False
        # print(xmp_str_split)
        for xml in xmp_str_split:
            # TODO: Quizás se podría encapsular el código siguiente. Por ahora, queda así.
            if xml.rfind('GimbalYawDegree') != -1 and GimbalYawDegree_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                gimbal_yaw_degree = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(gimbal_yaw_degree)  # Comprobamos que el string tiene formato de float
                    GimbalYawDegree_found = True
                except ValueError:
                    GimbalYawDegree_found = False
            if xml.rfind('GimbalPitchDegree') != -1 and GimbalPitchDegree_found == False: # Hacemos lo mismo que antes pero para GimbalPitchDegree
                gimbal_pitch_degree = self.utils_obj.extract_number_from_string(xml) # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(gimbal_pitch_degree)  # Comprobamos que el string tiene formato de float
                    GimbalPitchDegree_found = True
                except ValueError:
                    GimbalPitchDegree_found = False

        # self.organizer_logger.logger.debug(f"La imagen es {filename}")
        self.organizer_logger.logger.debug("GimbalYawDegree: " + gimbal_yaw_degree + " GimbalPitchDegree: " + gimbal_pitch_degree)

        return gimbal_yaw_degree, gimbal_pitch_degree

    def saving_gimbal_data_in_xmp(self, filename: str, gimbal_data: list[str]) -> None:
        """
        Función que guarda los datos del gimbal en formato XMP en el archivo dado como parámetro usando la librería pyexiv2.

        Arguments:
        ---------
        - filename - nombre del archivo en el que se guardan los datos.
        - gimbal_data - los datos del gimbal. Es una lista en el que el primer elemento es el GimbalYawDegree y el segundo GimbalPitchDegree
        """
        try:
            with pyexiv2.Image(filename) as image_opened:
                dict_gimbal = {'Xmp.drone-dji.GimbalYawDegree': gimbal_data[0],
                            'Xmp.drone-dji.GimbalPitchDegree': gimbal_data[1]}
                image_opened.modify_xmp(dict_gimbal)
                # close() se ejecuta automáticamente al salir del with
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"Error al modificar los datos XMP del archivo {filename}.")
            self.organizer_logger.logger.error(str(e))
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')

    def get_xmp_data(self, filename: str) -> list[str]:
        """
        Función que obtiene todos los datos que hay en las imágenes RGB, quitando el gimbal yaw y el gimbal pitch, que lo obtenemos en la función get_gimbal_yaw_pitch.
        Devuelve una lista de strings con los datos en el siguiente orden: AbsoluteAltitude, RelativeAltitude, GimbalRollDegree, FlightRollDegree,
        FlightYawDegree, FlightPitchDegree, CamReverse, GimbalReverse, RtkFlag.

        Arguments:
        ---------
        - filename - nombre de la imagen de la que extraer los datos
        """
        absolute_altitude = "0"
        relative_altitude = "0"
        gimbal_roll_degree = "0"
        flight_roll_degree = "0"
        flight_yaw_degree = "0"
        flight_pitch_degree = "0"
        cam_reverse = "0"
        gimbal_reverse = "0"
        rtk_flag = "0"

        fd = open(filename, encoding = 'latin-1')  # Abrimos la imagen como si fuera un archivo normal
        d= fd.read()
        fd.close()
        xmp_start = d.find('<x:xmpmeta')
        xmp_end = d.find('</x:xmpmeta')
        xmp_str = d[xmp_start:xmp_end+12]  # Nos quedamos con la información xmp
        xmp_str_split = xmp_str.split('drone-dji:')  # Dividimos el string teniendo en cuenta "!"drone-dji:""
        AbsoluteAltitude_found = False
        RelativeAltitude_found = False
        GimbalRollDegree_found = False
        FlightRollDegree_found = False
        FlightYawDegree_found = False
        FlightPitchDegree_found = False
        CamReverse_found = False
        GimbalReverse_found = False
        RtkFlag_found = False
        # TODO: Dentro del siguiente bucle repito mucho código. Debería encapsularlo en una función. Sin embargo, probando en el momento del desarrollo
        # no funcionaba correctamente usando una función. Hay que pensarlo mejor.
        for xml in xmp_str_split:
            if xml.rfind('AbsoluteAltitude') != -1 and AbsoluteAltitude_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                absolute_altitude = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(absolute_altitude)  # Comprobamos que el string tiene formato de float
                    AbsoluteAltitude_found = True
                except ValueError:
                    AbsoluteAltitude_found = False

            if xml.rfind('RelativeAltitude') != -1 and RelativeAltitude_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                relative_altitude = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(relative_altitude)  # Comprobamos que el string tiene formato de float
                    RelativeAltitude_found = True
                except ValueError:
                    RelativeAltitude_found = False

            if xml.rfind('GimbalRollDegree') != -1 and GimbalRollDegree_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                gimbal_roll_degree = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(gimbal_roll_degree)  # Comprobamos que el string tiene formato de float
                    GimbalRollDegree_found = True
                except ValueError:
                    GimbalRollDegree_found = False

            if xml.rfind('FlightRollDegree') != -1 and FlightRollDegree_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                flight_roll_degree = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(flight_roll_degree)  # Comprobamos que el string tiene formato de float
                    FlightRollDegree_found = True
                except ValueError:
                    FlightRollDegree_found = False

            if xml.rfind('FlightYawDegree') != -1 and FlightYawDegree_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                flight_yaw_degree = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(flight_yaw_degree)  # Comprobamos que el string tiene formato de float
                    FlightYawDegree_found = True
                except ValueError:
                    FlightYawDegree_found = False

            if xml.rfind('FlightPitchDegree') != -1 and FlightPitchDegree_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                flight_pitch_degree = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(flight_pitch_degree)  # Comprobamos que el string tiene formato de float
                    FlightPitchDegree_found = True
                except ValueError:
                    FlightPitchDegree_found = False

            if xml.rfind('CamReverse') != -1 and CamReverse_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                cam_reverse = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(cam_reverse)  # Comprobamos que el string tiene formato de float
                    CamReverse_found = True
                except ValueError:
                    CamReverse_found = False

            if xml.rfind('GimbalReverse') != -1 and GimbalReverse_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                gimbal_reverse = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(gimbal_reverse)  # Comprobamos que el string tiene formato de float
                    GimbalReverse_found = True
                except ValueError:
                    GimbalReverse_found = False

            if xml.rfind('RtkFlag') != -1 and RtkFlag_found == False:  # Comprobamos que GimbalYawDegree se encuentra en el  string
                # y que además no lo hemos encontrado antes (esto se debe a que hay un tipo de imagen con etiquetas xml de inicio y fin, por lo que
                # el nombre de GimbalYawDegree aparece dos veces en xmp_str_split)
                rtk_flag = self.utils_obj.extract_number_from_string(xml)  # Extraemos el número del string, que tiene más caracteres, además de números.
                try:
                    float(rtk_flag)  # Comprobamos que el string tiene formato de float
                    RtkFlag_found = True
                except ValueError:
                    RtkFlag_found = False

        self.organizer_logger.logger.debug("AbsoluteAltitude: " + absolute_altitude + " RelativeAltitude: " + relative_altitude + " GimbalRollDegree: " + gimbal_roll_degree +
                     " FlightRollDegree: " + flight_roll_degree + " FlightYawDegree: " + flight_yaw_degree + " FlightPitchDegree: " + flight_pitch_degree +
                     " CamReverse: " + cam_reverse + " GimbalReverse: " + gimbal_reverse + " RtkFlag: " + rtk_flag)
        return absolute_altitude, relative_altitude, gimbal_roll_degree, flight_roll_degree, flight_yaw_degree, flight_pitch_degree, cam_reverse, gimbal_reverse, rtk_flag

    def saving_xmp_data_in_xmp(self, filename: str, xmp_data: list[str]) -> None:
        """
        Función que guarda los datos en formato XMP en el archivo dado como parámetro usando la librería pyexiv2.

        Arguments:
        ---------
        - filename - nombre del archivo en el que se guardan los datos.
        - xmp_data - los datos xmp. Es una lista en el que los elementos vienen dados en el siguiente orden: AbsoluteAltitude, RelativeAltitude, GimbalRollDegree,
        FlightRollDegree, FlightYawDegree, FlightPitchDegree, CamReverse, GimbalReverse, RtkFlag.
        """
        try:
            with pyexiv2.Image(filename) as image_opened: # Abrimos la imagen
                dict_gimbal = {'Xmp.drone-dji.AbsoluteAltitude': xmp_data[0], 'Xmp.drone-dji.RelativeAltitude': xmp_data[1], 'Xmp.drone-dji.GimbalRollDegree': xmp_data[2]
                            , 'Xmp.drone-dji.FlightRollDegree': xmp_data[3], 'Xmp.drone-dji.FlightYawDegree': xmp_data[4], 'Xmp.drone-dji.FlightPitchDegree': xmp_data[5]
                            , 'Xmp.drone-dji.CamReverse': xmp_data[6], 'Xmp.drone-dji.GimbalReverse': xmp_data[7], 'Xmp.drone-dji.RtkFlag': xmp_data[8]}
                image_opened.modify_xmp(dict_gimbal)
                # close() se ejecuta automáticamente al salir del with
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("Error al modificar los datos XMP del archivo.")
            self.organizer_logger.logger.error(e.__str__)
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')

    def check_xmp_data_using_pyexiv2(self, filename: str, show_results: bool = False) -> bool:
        """
        Función que comprueba si todos los datos xmp que están en un listado están almacenados en filename.

        Arguments:
        ---------
        - filename: El path de la imagen que se quiere comprobar.
        - show_results: Si queremos mostrar datos con print. Defult = false
        - return: devuelve una tupla de dos valores. Un booleano que indica si los datos no existen (True) o sí existen (False).
        - rtype: bool
        """
        try:
            with pyexiv2.Image(filename) as image_opened:
                # La siguiente lista sería todos los datos XMP de una de las imágenes.
                # xmp_list_to_check = ['Xmp.xmp.ModifyDate', 'Xmp.xmp.CreateDate', 'Xmp.tiff.Make', 'Xmp.tiff.Model', 'Xmp.dc.format', 'Xmp.drone-dji.GpsLatitude', 'Xmp.drone-dji.GpsLongitude', 'Xmp.drone-dji.AbsoluteAltitude', 'Xmp.drone-dji.RelativeAltitude', 'Xmp.drone-dji.GimbalRollDegree', 'Xmp.drone-dji.GimbalYawDegree', 'Xmp.drone-dji.GimbalPitchDegree', 'Xmp.drone-dji.FlightRollDegree', 'Xmp.drone-dji.FlightYawDegree', 'Xmp.drone-dji.FlightPitchDegree', 'Xmp.drone-dji.CamReverse', 'Xmp.drone-dji.GimbalReverse', 'Xmp.drone-dji.SelfData', 'Xmp.crs.Version', 'Xmp.crs.HasSettings', 'Xmp.crs.HasCrop', 'Xmp.crs.AlreadyApplied']
                xmp_list_to_check = ['Xmp.drone-dji.AbsoluteAltitude', 'Xmp.drone-dji.RelativeAltitude', 'Xmp.drone-dji.GimbalRollDegree', 'Xmp.drone-dji.GimbalYawDegree', 'Xmp.drone-dji.GimbalPitchDegree', 'Xmp.drone-dji.FlightRollDegree', 'Xmp.drone-dji.FlightYawDegree', 'Xmp.drone-dji.FlightPitchDegree', 'Xmp.drone-dji.CamReverse', 'Xmp.drone-dji.GimbalReverse']
                dict_xmp = image_opened.read_xmp()
                keys_not_exist = False

                if show_results:
                    self.organizer_logger.logger.info(f"Los datos xmp son: {dict_xmp}")

                # Verificar las claves presentes y faltantes
                claves_presentes = []
                claves_faltantes = []

                for clave in xmp_list_to_check:
                    if clave in dict_xmp:
                        claves_presentes.append(clave)
                    else:
                        claves_faltantes.append(clave)

                # Mostrar resultados
                if show_results:
                    self.organizer_logger.logger.info(f"\n--- Verificación de claves XMP ---")
                    self.organizer_logger.logger.info(f"Claves presentes ({len(claves_presentes)}/{len(xmp_list_to_check)}):")
                    for clave in claves_presentes:
                        self.organizer_logger.logger.info(f"  ✓ {clave}: {dict_xmp[clave]}")

                if claves_faltantes:
                    keys_not_exist = True
                    if show_results: self.organizer_logger.logger.info(f"\nClaves faltantes ({len(claves_faltantes)}/{len(xmp_list_to_check)}):")
                    for clave in claves_faltantes:
                        if show_results: self.organizer_logger.logger.info(f"  ✗ {clave}")
                else:
                    if show_results: self.organizer_logger.logger.info(f"\n✓ Todas las claves están presentes en el archivo.")

                return keys_not_exist, claves_faltantes
        except Exception as e:
            self.organizer_logger.logger.info("Error al leer los comprobar los datos XMP del archivo.")
            self.organizer_logger.logger.info(e.__str__)
            self.organizer_logger.logger.info(e)
            return True, []

    def saving_gps_data(self, image_name: str, gps_data: list[str]):
        """
        Función que guarda los datos gps en la imagen de entrada a partir de los datos GPS.

        Arguments:
        ---------
        - image_name - nombre de la imagen a la que se quiere modificar o añadir los datos GPS.
        - gps_data - Los datos GPS que se quieren almacenar en la imagen. Los datos GPS tienen queestar en formato decimal.
        """
        try:
            with pyexiv2.Image(image_name) as image_opened:  # Abrimos la imagen
                # Transforman los datos de entrada en formato grados, minutos y segundos, además de devolverla referencia.
                lat_deg = self.to_deg(float(gps_data[0]), ["S", "N"])
                lng_deg = self.to_deg(float(gps_data[1]), ["W", "E"])

                # Modificamos los datos obtenidos anteriormente a formato Rational. Es decir, de grados, minutos y segundos se transforman a una fracción utilizando la función change_to_rational
                lat_deg_rational = (self.utils_obj.change_to_rational(lat_deg[0]), self.utils_obj.change_to_rational(lat_deg[1]), self.utils_obj.change_to_rational(lat_deg[2]))
                lng_deg_rational = (self.utils_obj.change_to_rational(lng_deg[0]), self.utils_obj.change_to_rational(lng_deg[1]), self.utils_obj.change_to_rational(lng_deg[2]))
                altitude =  self.utils_obj.change_to_rational(gps_data[2])
                altitude_str = str(altitude[0]) + "/" + str(altitude[1])
                # Por último, el formato que tienen que tener los datos GPS de latitud y longitud es un string del siguiente modo: 39/1 37/1 320511/6250
                # De modo que la función convert_to_string_gps_exif_format transforma los datos en rational a partir de un list en un string con el formato anterior.
                # Se genera un diccionario con la información que se quiera almacenar en la imagen.
                # En el caso de la altitud es similar, pero se hace en las líneas anteriores.
                dict_gps = {"Exif.GPSInfo.GPSVersionID": "2 0 0 0","Exif.GPSInfo.GPSLatitude": self.convert_to_string_gps_exif_format(lat_deg_rational), "Exif.GPSInfo.GPSLongitude": self.convert_to_string_gps_exif_format(lng_deg_rational), "Exif.GPSInfo.GPSLongitudeRef": lng_deg[3], "Exif.GPSInfo.GPSLatitudeRef": lat_deg[3], "Exif.GPSInfo.GPSAltitude": altitude_str}

                image_opened.modify_exif(dict_gps)
        except Exception as e:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"Error al modificar los datos GPS del archivo {image_name}.")
            self.organizer_logger.logger.error(str(e))
            self.organizer_logger.logger.exception(e)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')

    def to_deg(self, value: float, loc: list[str]) -> list[int, int, float, str]:
        """
        Función que guarda los datos gps en la imagen de entrada a partir de los datos GPS.

        Arguments:
        ---------
        - value - Dato de posicionamiento GPS. Puede ser longitud o latitud. El formato tiene que ser un decimal en formato float.
        - loc - Las posibles referenicas para la latitud: S o N, o para la longitud: W o E.
        """
        if value < 0:
            loc_value = loc[0]
        elif value > 0:
            loc_value = loc[1]
        else:
            loc_value = ""
        abs_value = abs(value)
        deg =  int(abs_value)
        t1 = (abs_value-deg)*60
        min = int(t1)
        sec = round((t1 - min)* 60, 5)
        return (deg, min, sec, loc_value)

    def convert_to_string_gps_exif_format(self, deg_rational_list: list[list[int]]) -> str:
        """
        Función que convierte la lista de entrada, que a su vez es una tupla de 2 valores (numerador y denominador),
        en un string con formato "39/1 37/1 320511/6250".

        Arguments:
        ---------
        deg_rational_list - Una lista de 3 valores (grados, minutos y segundos) los cuales son tuplas de dos valores, que son numerador y denominador.
        Es decir, están en formato Rational.
        """
        response = ""
        for deg_rational in deg_rational_list:
            response = response + " " + str(deg_rational[0]) + "/" + str(deg_rational[1])  # Creamos el string con el formato definido
        return response.removeprefix(" ")  # Borramos el espacio vacío al inicio del string.


class MetaLocation:
    """
    Clase que se centra en la generación de los archivos meta y location.

    Methods
    -------
    read_folder(self, input_folder: str, filename:str) -> None

    """
    def __init__(self, organizer_logger: "utils.OrganizerLogger") -> None:
        self.utils_obj = utils.Utils(organizer_logger)
        self.exif_management_obj = GeneralInformationFromImage(organizer_logger)
        self.stop = False
        self.current_image_number = 0
        self.total_images_number = 0
        self.error_meta_location = 0
        self.images_error_meta_location = []
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

        Arguments:
        ---------
        - main_process - Indica que se trata del proceso principal dentro de un hilo o de un proceso secundario, como una opción a mayores a realizar.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        """

        self.current_image_number = 0
        self.total_images_number = 0
        self.error_meta_location = 0
        self.images_error_meta_location.clear()
        self.exif_management_obj.error_exif_data = 0
        self.exif_management_obj.images_error_exif_data.clear()

        if not main_process:
            progress_callback.emit("\n----------------------------------------------------\n")
            progress_callback.emit("SUBPROCESO: GENERAR META\LOCATION\n")  # Enviamos el texto para indicar que arranca el proceso de extracción, pero que no es el proceso principal, siendo posterior.
            progress_callback.emit("----------------------------------------------------\n")

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

        if self.error_meta_location > 0:
            error = True
            summarize_dict["Error en meta-location"] = "Ha habido {0} errores en el meta-location.".format(self.error_meta_location)
            summarize_dict["Imágenes con error"] = self.images_error_meta_location

        if self.exif_management_obj.error_exif_data > 0:
            error = True
            summarize_dict["Error en los metadatos"] = "Ha habido {0} errores en los metadatos.".format(self.exif_management_obj.error_exif_data)
            summarize_dict["Imágenes con error"] = self.exif_management_obj.images_error_exif_data

        if not error:
            summarize_dict["Sin Errores"] = "Sin errores durante el proceso"
        else:
            summarize_dict["ERROR"] = "HAN EXISTIDO ERRORES"
        return summarize_dict

    def checking_results_meta_location(self, input_folder: str, progress_callback, progress_summarize) -> dict:
        """
        Recorre el árbol de directorios de input_folder buscando archivos *.csv.
        Para cada carpeta que contenga algún csv, comprueba que el número de líneas
        del csv coincide con el número de imágenes (sin CROP) presentes en esa carpeta.

        Arguments:
        ---------
        - input_folder - Carpeta raíz desde la que iniciar la búsqueda.

        Returns:
        --------
        Diccionario con la ruta de cada carpeta analizada como clave y un dict con:
            - "csv_file": nombre del archivo csv encontrado.
            - "csv_lines": número de líneas del csv.
            - "image_count": número de imágenes sin CROP en la carpeta.
            - "match": True si ambos valores son iguales, False en caso contrario.
        """
        results = {}
        errors = []

        if not os.path.exists(input_folder):
            self.organizer_logger.logger.warning(f"La carpeta de entrada no existe: {input_folder}")
            return results

        excluded_folders = {"CSVs", "ESTADILLOS", "MINIATURAS"}
        for dirpath, dirnames, filenames in os.walk(input_folder):
            dirnames[:] = [d for d in dirnames if d not in excluded_folders]
            csv_files = [f for f in filenames if f.lower().endswith(".csv")]
            if not csv_files:
                continue

            for csv_file in csv_files:
                csv_path = os.path.join(dirpath, csv_file)
                try:
                    with open(csv_path, "r", encoding="utf-8") as fh:
                        csv_lines = sum(1 for line in fh if line.strip())
                except Exception as e:
                    self.organizer_logger.logger.error(f"Error al leer '{csv_path}': {e}")
                    continue

                try:
                    images = self.utils_obj.get_images_from_dir(dirpath, ["_CROP"])
                except Exception as e:
                    self.organizer_logger.logger.error(f"Error al obtener imágenes de '{dirpath}': {e}")
                    continue

                image_count = len(images)
                match = csv_lines == image_count

                results[dirpath] = {
                    "csv_file": csv_file,
                    "csv_lines": csv_lines,
                    "image_count": image_count,
                    "match": match,
                }

                if match:
                    self.organizer_logger.logger.info(
                        f"OK - {dirpath}: csv '{csv_file}' tiene {csv_lines} líneas y hay {image_count} imágenes."
                    )
                    progress_callback.emit(f"OK - {dirpath}: csv '{csv_file}' tiene {csv_lines} líneas y hay {image_count} imágenes.\n")
                else:
                    self.organizer_logger.logger.warning(
                        f"ERROR - {dirpath}: csv '{csv_file}' tiene {csv_lines} líneas pero hay {image_count} imágenes. No coinciden."
                    )
                    errors.append(dirpath)
                    self.error_meta_location += 1
                    self.images_error_meta_location.append(f"ERROR - {dirpath}: csv '{csv_file}' tiene {csv_lines} líneas pero hay {image_count} imágenes. No coinciden.")

        if errors:
            self.organizer_logger.logger.error(
                f"Se encontraron {len(errors)} carpeta(s) con discrepancias entre csv e imágenes: {errors}"
            )
        elif results:
            self.organizer_logger.logger.info("Verificación completada sin errores.")
            progress_summarize.emit("Verificación completada sin errores.\n")
            progress_callback.emit("Verificación completada sin errores.\n")
        else:
            self.organizer_logger.logger.info(f"No se encontraron archivos csv en: {input_folder}")

        return results

    def check_gimbal_yaw_pitch_values(self, meta_location_dataframe: pd.DataFrame, flight_height, progress_callback) -> pd.DataFrame:
        gimbal_yaw_degree_corrected = meta_location_dataframe['GimbalYawDegree'].astype(object).copy()
        gimbal_pitch_degree_corrected = meta_location_dataframe['GimbalPitchDegree'].astype(object).copy()
        image_theoretical_position = dict()

        zero_found = False
        for i in range(len(meta_location_dataframe)):
            for column in ['GimbalYawDegree', 'GimbalPitchDegree']:
                if float(meta_location_dataframe.loc[i, column]) == 0:
                    zero_found = True
                    # Find the previous non-zero value (convertimos a float para comparar bien strings vs números)
                    prev_series = meta_location_dataframe.loc[:i-1, column].apply(lambda x: float(x)).where(lambda x: x != 0).dropna() if i > 0 else pd.Series(dtype=float)
                    prev_non_zero = prev_series.iloc[-1] if not prev_series.empty else None

                    # Find the next non-zero value (convertimos a float para comparar bien strings vs números)
                    next_series = meta_location_dataframe.loc[i+1:, column].apply(lambda x: float(x)).where(lambda x: x != 0).dropna() if i < len(meta_location_dataframe) - 1 else pd.Series(dtype=float)
                    next_non_zero = next_series.iloc[0] if not next_series.empty else None

                    # Calculate the average if both prev and next values exist
                    if prev_non_zero is not None and next_non_zero is not None:
                        corrected_value = (float(prev_non_zero) + float(next_non_zero)) / 2
                        if column == 'GimbalYawDegree':
                            gimbal_yaw_degree_corrected.iloc[i] = corrected_value
                        else:  # GimbalPitchDegree
                            gimbal_pitch_degree_corrected.iloc[i] = corrected_value

        # Add the corrected columns to the dataframe
        meta_location_dataframe['GimbalYawDegree'] = gimbal_yaw_degree_corrected
        meta_location_dataframe['GimbalPitchDegree'] = gimbal_pitch_degree_corrected

        if zero_found:
            #TODO: Aquí recorre todo el dataframe de nuevo y calcula todo de nuevo también. Lo ideal sería que solamente recorriera las columnas que necesiten ser recalculadas.
            for i, line in meta_location_dataframe.iterrows():
                if float(line['GimbalPitchDegree']) <= -85 and float(line['GimbalPitchDegree']) >= -95:
                    dron_distance_proyected = 0
                    image_theoretical_position['latitud_final'] = line['Lat']
                    image_theoretical_position['longitud_final'] = line['Lon']
                else:
                    image_theoretical_position, dron_distance_proyected = self.image_theoretical_position(flight_height,float(line['GimbalPitchDegree']), float(line["GimbalYawDegree"]), line['Lat'], line['Lon'], progress_callback)

                meta_location_dataframe.at[i, 'CalculatedDistance'] = dron_distance_proyected
                meta_location_dataframe.at[i, 'LatitudFoto'] = image_theoretical_position["latitud_final"]
                meta_location_dataframe.at[i, 'LongitudFoto'] = image_theoretical_position["longitud_final"]

        return meta_location_dataframe

    def safe_float_altitude(self, xmp_data: list, progress_callback) -> float:
        """
        Acceso blindado a la RelativeAltitude devuelta por get_xmp_data. Si el índice no existe o el valor
        no es convertible a float, devuelve NaN y registra el problema sin propagar la excepción.
        """
        try:
            return float(xmp_data[1])
        except (IndexError, ValueError, TypeError) as e:
            self.organizer_logger.logger.warning(f"No se pudo obtener AlturaRelativa del XMP: {e}")
            progress_callback.emit("\nWARNING: No se pudo obtener AlturaRelativa (XMP RelativeAltitude) de una imagen.\n")
            return float('nan')

    def copy_location_csv_to_flight_folder(self, termica_input_folder: str) -> None:
        """
        Copia el location.csv del vuelo (generado en la carpeta RGB hermana) dentro de la carpeta TERMICA del mismo vuelo,
        para que meta.csv y location.csv queden juntos en la carpeta del vuelo.
        """
        from utils import safe_copy2
        rgb_dir = termica_input_folder.replace("TERMICA", "RGB")
        location_filename = os.path.basename(rgb_dir) + "_location.csv"
        location_path = os.path.join(rgb_dir, location_filename)
        if os.path.exists(location_path):
            try:
                dest_path = os.path.join(termica_input_folder, os.path.basename(location_path))
                safe_copy2(location_path, dest_path)
            except FileNotFoundError:
                pass

    def gen_meta_location(self, input_folder: str, filename: str, progress_callback, progress_bar, csv_folder, flight_height:float, calculate_proyected_distance: bool) -> None:
        """
        Función que genera un archivo csv (normalmente llamado meta o location, junto con el nombre de la carpeta) a partir de los datos
        exif que se encuentran en las imágenes dentro de la carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - filename - nombre del archivo: meta.csv o location.csv
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        image_theoretical_position = dict()
        images = self.utils_obj.get_images_from_dir(input_folder, ["_CROP"])
        # se enviará 0 imágenes.
        if calculate_proyected_distance:
            nombresColumnas = ['Foto','Lat','Lon','GimbalYawDegree','GimbalPitchDegree','AlturaRelativa',"AlturaVuelo", 'CalculatedDistance','LatitudFoto', 'LongitudFoto']
        else:
            nombresColumnas = ['Foto','Lat','Lon','GimbalYawDegree','GimbalPitchDegree','AlturaRelativa']
        df = pd.DataFrame(columns=nombresColumnas)  # Creamos un dataframe para crear posteriormente un csv

        if len(images) > 0:
            progress_callback.emit("\nProcesando {0} imágenes en directorio {1}".format(len(images), input_folder) + "\n") # Se envía información al iniciar el procesado de un directorio que tenga imágenes.
            self.organizer_logger.logger.info(f"Procesando {len(images)} imágenes en directorio {input_folder}") # Se envía información al iniciar el procesado de un directorio que tenga imágenes.

        for indice, image in enumerate(images):  # Recorremos todas las imágenes del directorio de entrada.
            if not self.stop: # Se comprueba que no se quiere parar el proceso desde la ventana del log.
                self.current_image_number += 1
                p = utils.safe_pct(self.current_image_number, self.total_images_number) # Se calcula el porcentaje que queda teniendo en cuenta la cantidad total de imágenes a procesar
                    # y la cantidad actual de imágenes procesadas.
                progress_callback.emit(".") # Por cada imagen que se va a procesar, se emite un "." a la ventana de log.
                progress_bar.emit(p) # Por cada imagen que se va a procesar, se emite el procentaje de imágenes procesadas para mostrar en la barra de progreso.

                coords = self.leerLatitudLongitudAltitud_exif_DJI(os.path.join(input_folder,image), progress_callback)  # Obtenemos las coordenadas de la imagen.
                if coords is not None:  # Si no hay datos exif en la imagen, devuelve un None. De este modo no nos da error aunque la imagen no tenga datos.
                    gimbal = self.exif_management_obj.get_gimbal_yaw_pitch(os.path.join(input_folder,image))
                    xmp_data = self.exif_management_obj.get_xmp_data(os.path.join(input_folder,image))
                    altura_relativa = self.safe_float_altitude(xmp_data, progress_callback)
                    if calculate_proyected_distance:
                        # self.organizer_logger.logger.debug("--------------------------------------------------------")
                        # self.organizer_logger.logger.debug("La imagen es: " + os.path.join(input_folder,image))
                        if float(gimbal[1]) <= -85 and float(gimbal[1]) >= -95:
                            dron_distance_proyected = 0
                            image_theoretical_position['latitud_final'] = coords[1]
                            image_theoretical_position['longitud_final'] = coords[2]
                        else:
                            image_theoretical_position, dron_distance_proyected = self.image_theoretical_position(flight_height,float(gimbal[1]), float(gimbal[0]), float(coords[1]), float(coords[2]), progress_callback)
                            # self.organizer_logger.logger.debug(f"La orientación del dron es: {image_theoretical_position['dron_oriented']}")
                        # self.organizer_logger.logger.debug(f"La Latitud del centro de la foto es: {image_theoretical_position['latitud_final']}")
                        # self.organizer_logger.logger.debug(f"La Longitud del centro de la foto es: {image_theoretical_position['longitud_final']}")

                        df.loc[indice] =[coords[0], coords[1], coords[2], gimbal[0], gimbal[1], altura_relativa, flight_height, dron_distance_proyected, image_theoretical_position["latitud_final"], image_theoretical_position["longitud_final"]]
                    else:
                        df.loc[indice] =[coords[0], coords[1], coords[2], gimbal[0], gimbal[1], altura_relativa]
                    self.organizer_logger.logger.debug("Coordinates and gimball: " + str(coords[0]) + " " + str(coords[1]) + " " + str(coords[2]) + " " + str(gimbal[0]) + " " + str(gimbal[1]))

        if calculate_proyected_distance:
            df = self.check_gimbal_yaw_pitch_values(df, flight_height, progress_callback)

        if len(images) > 0 and not df.empty:  # Comprobamos que hay imágenes en el directorio, y además que hemos obtenido datos de ellas (es decir, hay datos exif).
            progress_callback.emit("\nGenerando csv: " + filename + "\n")
            self.organizer_logger.logger.info("Generating csv: " + filename)
            # df = self.shuffle_csv(df)
            df = self.reorder_csv_from_date(df)
            # self.organizer_logger.logger.info(df)
            df.to_csv(os.path.join(input_folder, os.path.basename(input_folder) + "_" + filename), sep = ",", header=False, index=False)
            for file in os.listdir(input_folder):
                if ".csv" in file:
                    shutil.copy2(os.path.join(input_folder, os.path.basename(input_folder) + "_" + filename), csv_folder)

        if filename == "meta.csv":
            self.copy_location_csv_to_flight_folder(input_folder)


    def iterate_folders(self, input_folder: str, filename: str, progress_callback, progress_bar, csv_folder: str, flight_height: float, calculate_proyected_distance: bool) -> None:
        """
        Función que itera a través del arbol de directorios existente en input_folder. Para cada carpeta lleva a cabo la función gen_meta_location.
        Después de llamar a la función comprueba las carpetas que existen dentro de input_folder y se vuelve a llamar a sí misma para
        llevar a cabo la función correspondiente dentro de esa carpeta.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - filename - nombre del archivo: meta.csv o location.csv
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        - csv_folder - .
        - flight_height - altura del vuelo del dron.
        """
        # self.organizer_logger.logger.info(f"Directorio de entrada: {input_folder}")

        self.gen_meta_location(input_folder, filename, progress_callback, progress_bar, csv_folder, flight_height, calculate_proyected_distance)
        for dir in next(os.walk(input_folder))[1]:
            if not self.stop:
                self.iterate_folders(os.path.join(input_folder,dir), filename, progress_callback, progress_bar,csv_folder, flight_height, calculate_proyected_distance)

    def check_input_folder_and_iterate(self, input_folder: str, progress_callback, progress_bar, csv_folder: str, flight_height: float, calculate_proyected_distance: bool) -> bool:
        """
        Función que comprueba que los directorios TERMICA y RGB se encuentran en input folder, y acto seguido recorre todas las carpetas y todas las imágenes
        dentro de esa carpeta para poder generar los archivos meta y location.

        Arguments:
        ---------
        - input_folder - carpeta de entrada
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        list_dir = os.listdir(input_folder)

        if "TERMICA" not in list_dir or "RGB" not in list_dir:  # Comprobamos que existen las carpetas TEMICA y RGB para poder generar los archivos.
            self.organizer_logger.logger.info("No se encuentran los directorios TERMICA y RGB")
            progress_callback.emit("\nNo se encuentran los directorios TERMICA y RGB\n")
            return False
        else:
            self.iterate_folders(os.path.join(input_folder, "RGB") , "location.csv", progress_callback, progress_bar, csv_folder, flight_height, calculate_proyected_distance)
            self.iterate_folders(os.path.join(input_folder, "TERMICA") , "meta.csv", progress_callback, progress_bar, csv_folder, flight_height, calculate_proyected_distance)
            if "RGB_Extra" in list_dir:  # Si se ha creado la carpeta RGB_Extra, creamos el location.csv dentro de cada carpeta PBX_VX
                self.iterate_folders(os.path.join(input_folder, "RGB_Extra") , "location.csv", progress_callback, progress_bar, csv_folder, flight_height, calculate_proyected_distance)
            return True


    def leerLatitudLongitudAltitud_exif_DJI(self, pathImagen: str, progress_callback):
        """
        Función que calcula la latitud y la longitud de la imagen de entrada a partir de sus datos exif. Devuelve también el nombre de la imagen.
        El orden devuelto en la lista es nombre, latitud, longitud, altitud.

        Arguments:
        ---------
        - pathImagen - path de la imagen de entrada
        """
        try:
            # TODO: Podríamos mover esta función a exif_management
            # Abrimos la imagen
            img = PIL.Image.open(pathImagen)
            # Obtenemos el exif de la foto
            datos_exif = img.getexif()
            # Cerramos la imagen
            img.close()
            if len(datos_exif) > 0:  # Comprobamos si hay datos exif en la imagen. Si no hay, la función devuelve un None.
                # Leemos las coordenadas de la foto del exif
                coordenadas = datos_exif.get_ifd(34853)
                latitudFoto = coordenadas.get(2, 0) # Si no hay datos de latitud, la función devuelve 0
                longitudFoto = coordenadas.get(4, 0) # Si no hay datos de longitud, la función devuelve 0
                altitudFoto = coordenadas.get(6, 0) # Si no hay datos de altitud, la función devuelve 0

                # Transformamos las coordenadas a coordenadas decimales
                lat_ref = coordenadas.get(1)
                lon_ref = coordenadas.get(3)
                if lat_ref is None or lon_ref is None:
                    self.organizer_logger.logger.warning("ERROR: La imagen {0} no tiene GPSLatitudeRef/GPSLongitudeRef en el EXIF.".format(pathImagen))
                    progress_callback.emit("\nERROR: La imagen {0} no tiene referencia GPS completa.\n".format(pathImagen))
                    self.error_meta_location += 1
                    self.images_error_meta_location.append(pathImagen)
                    return None

                # Latitud
                if lat_ref == 'N':
                    latitudDecimalFoto = float(latitudFoto[0])+float(latitudFoto[1])/60+float(latitudFoto[2])/3600
                elif lat_ref == 'S':
                    latitudDecimalFoto = (float(latitudFoto[0])+float(latitudFoto[1])/60+float(latitudFoto[2])/3600)*-1
                else:
                    self.organizer_logger.logger.warning("ERROR: GPSLatitudeRef inválido ({0}) en la imagen {1}.".format(lat_ref, pathImagen))
                    self.error_meta_location += 1
                    self.images_error_meta_location.append(pathImagen)
                    return None
                # Longitud
                if lon_ref == 'E':
                    longitudDecimalFoto = float(longitudFoto[0])+float(longitudFoto[1])/60+float(longitudFoto[2])/3600
                elif lon_ref == 'W':
                    longitudDecimalFoto = (float(longitudFoto[0])+float(longitudFoto[1])/60+float(longitudFoto[2])/3600)*-1
                else:
                    self.organizer_logger.logger.warning("ERROR: GPSLongitudeRef inválido ({0}) en la imagen {1}.".format(lon_ref, pathImagen))
                    self.error_meta_location += 1
                    self.images_error_meta_location.append(pathImagen)
                    return None

                return os.path.basename(pathImagen),latitudDecimalFoto, longitudDecimalFoto, altitudFoto
            else:
                return None
        except OSError as os_error:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning(f"ERROR: Unable to open the image file. OSError: {os_error}")
            progress_callback.emit(f"\nERROR: No se puede abrir la imagen {pathImagen}. Puede que el archivo esté dañado o el nombre de archivo sea inválido.\n")
            self.organizer_logger.logger.error(os_error.__str__)
            self.organizer_logger.logger.exception(os_error)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_meta_location += 1
            self.images_error_meta_location.append(pathImagen)
            return None
        except FileNotFoundError as f:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: The image file is not found to open it by PIL.")
            progress_callback.emit("\nERROR: No se encuentra la imagen {0} para que PIL la pueda abrir.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(f.__str__)
            self.organizer_logger.logger.exception(f)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_meta_location += 1
            self.images_error_meta_location.append(pathImagen)
            return None
        except PIL.UnidentifiedImageError as u:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: The image can not be opened and identified.")
            progress_callback.emit("\nERROR: La imagen {0} no puede ser abierta e identificada.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(u.__str__)
            self.organizer_logger.logger.exception(u)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_meta_location += 1
            self.images_error_meta_location.append(pathImagen)
            return None
        except ValueError as v:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: The output format has not been identified.")
            progress_callback.emit("\nERROR: El formato de salida no ha sido identificado para la imagen {0}.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(v.__str__)
            self.organizer_logger.logger.exception(v)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_meta_location += 1
            self.images_error_meta_location.append(pathImagen)
            return None
        except TypeError as t:
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.organizer_logger.logger.warning("ERROR: The image has not been written.")
            progress_callback.emit("\nERROR: Excepción de tipo TypeError para la imagen {0}.".format(pathImagen) + "\n")
            self.organizer_logger.logger.error(t.__str__)
            self.organizer_logger.logger.exception(t)
            self.organizer_logger.logger.warning('------------------------------------------------------------------------------------------------------')
            self.error_meta_location += 1
            self.images_error_meta_location.append(pathImagen)
            return None

    def image_theoretical_position(self, relative_height, gimbal_pitch, gimbal_yaw, latitud, longitud, progress_callback):
        result = dict()
        alpha = 90 - abs(gimbal_pitch)
        # print(f"La altura relativa es {relative_height}. El gimbal_pitch es {gimbal_pitch}. El gimbal_yaw es {gimbal_yaw}.")

        dron_distance_proyected = relative_height * math.tan(math.radians(alpha))

        # print(f"La distancia proyectada es {dron_distance_proyected} metros.")
        gimbal_yaw_checked = self.check_gimbal_yaw(gimbal_yaw = gimbal_yaw)

        cambio_latitud, cambio_longitud = self.meters_to_decimal_degrees(dron_distance_proyected, gimbal_yaw_checked)
        # print(f"La latitud y longitud de los metros calculados son {cambio_latitud}, {cambio_longitud}.")

        result = self.check_gimbal_yaw(gimbal_yaw, latitud, longitud, cambio_longitud, cambio_latitud)


        # if gimbal_yaw <= -70 and gimbal_yaw >= -110: # Oeste
        #     longitud_final = longitud + cambio_longitud
        #     result["dron_oriented"] = "West"
        #     result["latitud_final"] = latitud
        #     result["longitud_final"] = longitud_final
        # elif gimbal_yaw >= 70 and gimbal_yaw <= 110: # Este
        #     longitud_final = longitud - cambio_longitud
        #     result["dron_oriented"] = "East"
        #     result["latitud_final"] = latitud
        #     result["longitud_final"] = longitud_final
        # elif gimbal_yaw <= 20 and gimbal_yaw >= -20: # Norte
        #     latitud_final = latitud + cambio_latitud
        #     result["dron_oriented"] = "North"
        #     result["latitud_final"] = latitud_final
        #     result["longitud_final"] = longitud
        # elif gimbal_yaw <= 160 and gimbal_yaw >= -160: # Sur
        #     latitud_final = latitud - cambio_latitud
        #     result["dron_oriented"] = "South"
        #     result["latitud_final"] = latitud_final
        #     result["longitud_final"] = longitud
        return result, dron_distance_proyected

    def meters_to_decimal_degrees(self, meters, gimbal_yaw_checked, latitude=0):
        """
        Convierte una distancia en metros a grados decimales de latitud y longitud.

        Args:
        meters (float): Distancia en metros.
        latitude (float): Latitud en grados decimales del punto de referencia.

        Returns:
        tuple: (cambio_latitud, cambio_longitud) en grados decimales.
        """
        # Creamos un punto de origen
        origin = Point(latitude, 0) # Realmente latitude es cero, pues a esta función la llamo sin ese valor parametrizado.

        # Calculamos el destino moviéndonos 'meters' metros hacia el norte
        destination_lat = distance.distance(meters=meters).destination(origin, gimbal_yaw_checked["bearing_lat"])

        # Calculamos el destino moviéndonos 'meters' metros hacia el este
        destination_lon = distance.distance(meters=meters).destination(origin, gimbal_yaw_checked["bearing_long"])

        # Calculamos los cambios en latitud y longitud
        cambio_latitud = destination_lat.latitude - origin.latitude
        cambio_longitud = destination_lon.longitude - origin.longitude

        return cambio_latitud, cambio_longitud

    def check_gimbal_yaw(self, gimbal_yaw: float, latitud = 0 , longitud = 0, cambio_longitud = 0, cambio_latitud = 0):
        result = dict()
        # "FlightYawDegree" refers to the current orientation of the aircraft, where 0 degrees represents true north, 90 degrees is east, 180 or -180 degrees is south, and -90 or 270 degrees is west.
        # La línea anterior nos muesta los valores centrales, mientras que en el código se le añade cierto margen a los lados.
        # print(f'El gimbal_yaw es {gimbal_yaw}')
        # print(f'La longitud es {longitud}')
        # print(f'La latitud es {latitud}')
        if gimbal_yaw <= -45 and gimbal_yaw >= -135: # Oeste
            result["dron_oriented"] = "West"
            result["bearing_lat"] = 0
            result["bearing_long"] = -90
            result["latitud_final"] = latitud
            result["longitud_final"] = longitud + cambio_longitud
            # print("Entra en West")
        elif gimbal_yaw >= 45 and gimbal_yaw <= 135: # Este
            result["dron_oriented"] = "East"
            result["bearing_lat"] = 0
            result["bearing_long"] = 90
            result["latitud_final"] = latitud
            result["longitud_final"] = longitud + cambio_longitud
            # print("Entra en East")
        elif gimbal_yaw <= 44 and gimbal_yaw >= -44: # Norte
            result["dron_oriented"] = "North"
            result["bearing_lat"] = 0
            result["bearing_long"] = 0
            result["latitud_final"] = latitud + cambio_latitud
            result["longitud_final"] = longitud
            # print("Entra en North")
        elif gimbal_yaw >= 136 or gimbal_yaw <= -136: # Sur. Se supone que las relaciones de mayor y menor son correctos, ya que siendo 180 o -180 la dirección sería Sur. Tiene que ser >= 160 para que se cumpla si es 180 o <= -160 para que se cumpla si es -180
            result["dron_oriented"] = "South"
            result["bearing_lat"] = 180
            result["bearing_long"] = 0
            result["latitud_final"] = latitud + cambio_latitud
            result["longitud_final"] = longitud
            # print("Entra en South")

        return result

    def reorder_csv(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Reordena el dataframe a partir del número secuencial extraído del nombre de imagen en la columna 'Foto'.
        El número se extrae buscando el patrón _XXXX_ (4 dígitos entre guiones bajos) en el nombre del archivo.

        Arguments:
        ---------
        - df - DataFrame con la columna 'Foto' que contiene los nombres de las imágenes.

        Returns:
        --------
        DataFrame ordenado por el número secuencial extraído del nombre de imagen.
        """
        def extract_sequence_number(filename: str) -> int:
            # Busca el patrón _DDDD_ (4 dígitos entre guiones bajos) en el nombre del archivo
            match = re.search(r'_(\d{4})_', filename)
            if match:
                return int(match.group(1))
            # Si no hay match con 4 dígitos, busca cualquier secuencia de dígitos entre guiones bajos
            match = re.search(r'_(\d+)_', filename)
            if match:
                return int(match.group(1))
            return 0

        df_sorted = df.copy()
        df_sorted['_sort_key'] = df_sorted['Foto'].apply(extract_sequence_number)
        df_sorted = df_sorted.sort_values('_sort_key').drop(columns=['_sort_key']).reset_index(drop=True)
        return df_sorted

    def reorder_csv_from_date(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Reordena el dataframe a partir de la fecha y hora extraídas del nombre de imagen en la columna 'Foto'.
        El nombre tiene el formato: 20260519_102933_DJI_0764_W.JPG, donde el primer grupo es la fecha
        (YYYYMMDD) y el segundo grupo es la hora (HHMMSS). Las filas se ordenan de menor a mayor fecha/hora.
        Si el formato es del tipo DJI_20260317114022_0001_V_point1, también lo detecta correctamente.
        Si no detecta ninguno de los dos formatos, entonces lo ordena por orden alfabético.

        Arguments:
        ---------
        - df - DataFrame con la columna 'Foto' que contiene los nombres de las imágenes.

        Returns:
        --------
        DataFrame ordenado cronológicamente por fecha y hora.
        """
        def extract_datetime(filename: str):
            date_str = None
            time_str = None

            # Patrón 1: fecha y hora separadas por cualquier separador no numérico (ej. 20260519_102933)
            match = re.search(r'(\d{8})[^0-9](\d{6})', filename)
            if match:
                date_str = match.group(1)  # YYYYMMDD
                time_str = match.group(2)  # HHMMSS
            else:
                # Patrón 2: fecha y hora juntas en 14 dígitos (ej. 20260417122228)
                match = re.search(r'(\d{14})', filename)
                if match:
                    combined = match.group(1)  # YYYYMMDDHHMMSS
                    date_str = combined[0:8]
                    time_str = combined[8:14]

            if date_str and time_str:
                try:
                    return pd.Timestamp(
                        year=int(date_str[0:4]),
                        month=int(date_str[4:6]),
                        day=int(date_str[6:8]),
                        hour=int(time_str[0:2]),
                        minute=int(time_str[2:4]),
                        second=int(time_str[4:6]),
                    )
                except ValueError:
                    return pd.NaT
            return pd.NaT

        df_sorted = df.copy()
        df_sorted['_sort_key'] = df_sorted['Foto'].apply(extract_datetime)

        if df_sorted['_sort_key'].isna().all():
            # Ningún nombre contiene fecha reconocible: ordenar alfabéticamente por 'Foto'
            df_sorted = df_sorted.drop(columns=['_sort_key']).sort_values('Foto').reset_index(drop=True)
        else:
            df_sorted = df_sorted.sort_values('_sort_key').drop(columns=['_sort_key']).reset_index(drop=True)

        return df_sorted
