"""Fases del pipeline de ATOM Organizer — orquestación SIN Qt.

Estos métodos eran de `MainWindow` (gui.py). Solo orquestan: leen la cfg,
llaman a los objetos de negocio (`self.*_obj`, de `pipeline`/`exif`/`utils`) y
publican progreso por los tres callbacks (`progress_callback`, `progress_bar`,
`progress_summarize`), que únicamente necesitan `.emit()`. No tocan widgets.

Se consumen por herencia desde los dos hosts:
  · `gui.MainWindow`      — la GUI Qt original.
  · `atom_core.organize.HeadlessHost` — la ruta webview / servidor.

Vivían dentro de la clase Qt, así que el host headless tenía que hacer
`from gui import MainWindow` y rebindar método a método: eso arrastraba PySide6
(~400 MB) a la imagen de servidor y obligaba a mantener a mano la lista de
métodos copiados (un olvido = AttributeError en runtime, ya pasó con
`_resolve_dron_selector`). Al vivir aquí, el contrato es la herencia y el
pipeline headless no importa Qt.

El host que herede este mixin DEBE proveer:
  organizer_logger_obj, config_obj, utils_obj, new_log_gui (con
  `set_obj`/`enable_process`), compress_image_obj, meta_location_obj,
  split_images_obj, gen_struct_folder_obj, extraction_obj, rgb_processing_obj,
  rgb_cropping_obj.
"""
from __future__ import annotations

import os
import re

import external_tools as config
import utils
from atom_core import sharding
from atom_core.almacen import es_uri_gcs, existe_ruta, unir
from external_tools import resource_path
from utils import (
    CompressRgbsConfig,
    ConvertToTifConfig,
    ExtractionConfig,
    GenMetaLocationConfig,
    GenStructFolderConfig,
    GenThumbnailsConfig,
    ManualGeotaggingConfig,
    RenameImagesConfig,
    RgbAerotoolsConfig,
    RgbCroppingConfig,
    SplitImagesConfig,
    TifRotatingConfig,
)
from rjpeg_a_tiff import EXTS_IMAGEN


def _existe_carpeta(ruta: str) -> bool:
    """`os.path.isdir`, consciente de `gs://…`.

    En local es EXACTAMENTE `os.path.isdir` (mismo comportamiento, y el mismo
    seam que ya monkeypatchean los tests de `test_etapas_pipeline.py` sobre
    `atom_core.phases.os.path.isdir`); en `gs://…` no hay directorios reales
    que comprobar, así que delega en `existe_ruta` (contenido bajo el prefijo).
    """
    if es_uri_gcs(ruta):
        return existe_ruta(ruta)
    return os.path.isdir(ruta)


class PipelinePhasesMixin:
    """Orquestación de las fases del pipeline, independiente del front."""

    # ---- reparto entre tareas (ver atom_core/sharding) ----------------------
    # Estos tres helpers son lo único que sabe de shards dentro de la
    # orquestación. Todos devuelven "todo" cuando no hay reparto, así que las
    # fases se escriben una sola vez y la GUI Qt no cambia de comportamiento.

    def _imagenes_origen_del_shard(self, input_folder: str, shard_index: int,
                                   shard_count: int, progress_callback) -> dict:
        """`{carpeta del origen: [imágenes de esta tarea]}` para la separación.

        Se reparte por IMAGEN y no por carpeta porque los vuelos reales tienen
        muy pocas carpetas: ANTOLIN son 2.516 fotos en DOS `DJI_*`, y por carpeta
        un Job de 8 tareas dejaría seis paradas.
        """
        # `solo_fuente=True`: el origen puede traer `.tif/.tiff/.dng` (una carpeta
        # ya convertida que se re-organiza, o una copia del destino). Son SALIDAS
        # del pipeline, no fuentes: metidas en el lote, `split_one_image` revienta
        # por imagen (`'TiffImageFile' object has no attribute '_getexif'`,
        # `pipeline.split_images`) — 335 errores en la execution `ndl6q`.
        listar_fuentes = lambda ruta: self.utils_obj.get_images_from_dir(ruta, solo_fuente=True)
        carpetas = sharding.carpetas_con_imagenes(input_folder, listar_fuentes)
        reparto = sharding.repartir_imagenes(
            carpetas, listar_fuentes, shard_index, shard_count)
        mias = sum(len(v) for v in reparto.values())
        progress_callback.emit(
            f"\nReparto de la separación: {mias} imágenes de {len(carpetas)} carpeta(s) "
            f"de origen para la tarea {shard_index + 1}/{shard_count}.\n")
        self.organizer_logger_obj.logger.info(
            f"Shard {shard_index + 1}/{shard_count}: {mias} imágenes de "
            f"{sorted(reparto)}")
        if not mias and carpetas:
            # No es un error, pero sí algo que hay que poder ver en el log.
            progress_callback.emit(
                "\nAVISO: a esta tarea no le ha tocado ninguna imagen. No es un "
                "fallo; sobra paralelismo para lo que hay en el origen.\n")
        return reparto

    def _pbs_del_shard(self, output_folder: str, shard_index: int,
                       shard_count: int, progress_callback) -> list[str]:
        """Vuelos hoja (`PBx/PBx_Vy`) del destino que le tocan a esta tarea.

        La unidad es el vuelo y no el PB porque con 7 PB para 8 tareas el reparto
        no puede equilibrarse: el PB mayor marca el suelo del reloj de pared."""
        todos = sharding.vuelos_del_destino(output_folder)
        mios = sharding.repartir(
            todos, shard_index, shard_count,
            peso=lambda ruta: sharding.peso_de_ruta(
                output_folder, ruta, self.utils_obj.contar_imagenes_or_tmc))
        progress_callback.emit(
            f"\nReparto del post-proceso: {len(mios)} de {len(todos)} vuelos para la "
            f"tarea {shard_index + 1}/{shard_count} -> {', '.join(mios) or '(ninguno)'}\n")
        self.organizer_logger_obj.logger.info(
            f"Shard {shard_index + 1}/{shard_count}: vuelos {mios} de {todos}")
        if not mios and todos:
            progress_callback.emit(
                "\nAVISO: a esta tarea no le ha tocado ningún vuelo (hay menos vuelos "
                "que tareas). No es un fallo; sobra paralelismo.\n")
        return mios

    def _exigir_destino_estructurado(self, output_folder: str) -> None:
        """Aborta si el destino no trae la estructura que deja `struct`.

        `post` no crea `RGB/` ni `TERMICA/`: las consume. Si no existe ninguna,
        las etapas previas no han pasado por ESTE destino (destino recien
        estrenado, o se lanzo `--etapa post` a secas) y todo lo que sigue
        (barrido, recorte, meta, rotacion, TIF) trabajaria sobre el vacio.

        Sin este guard el sintoma era el peor posible, y asi salio en la primera
        ejecucion real del Job (2026-08-09, `atom-organizer-pipeline-zgmxb`): la
        tarea 0 reventaba con un `FileNotFoundError: .../TERMICA` crudo desde las
        tripas de `checking_results_gen_struct_folder`, y las otras SIETE
        terminaban en VERDE sin haber hecho nada, porque `vuelos_del_destino` no
        encontraba vuelos que repartir. Un 7/8 "ok" que no proceso una imagen.

        Se comprueba en TODAS las tareas, no solo en la 0, justamente para que el
        run entero salga rojo y no 7 verdes con un rojo al lado.
        """
        presentes = [sub for sub in ("TERMICA", "RGB")
                     if _existe_carpeta(unir(output_folder, sub))]
        if presentes:
            return
        raise FileNotFoundError(
            f"La etapa 'post' necesita un destino ya estructurado y en "
            f"'{output_folder}' no existe ni TERMICA/ ni RGB/. Ejecuta antes las "
            f"etapas 'split' y 'struct' sobre este mismo destino, o usa "
            f"'--etapa todo'.")

    def _barrido_sin_ordenar(self, cfg, progress_callback) -> None:
        """Aparta a `SIN_ORDENAR` lo que quedó suelto en la raíz de RGB/TERMICA y
        aborta si NO sobrevivió nada.

        Cuándo se llama importa más que lo que hace: el criterio es «lo que sigue
        en la raíz no lo quiere ningún vuelo», y eso solo es cierto cuando la
        etapa `struct` ha terminado **entera**. Con `struct` repartido entre N
        tareas, ejecutarlo al final de la propia etapa lo haría con las otras
        tareas todavía moviendo: cada una vería como «sobrantes» las imágenes que
        sus compañeras aún no han colocado y se las llevaría a SIN_ORDENAR. El
        vuelo saldría incompleto y en ÁMBAR (aviso), no en rojo — el peor modo de
        fallo posible, porque se entrega.

        Por eso vive aquí y lo llaman dos sitios: el final de `struct` cuando la
        etapa es `todo` (no hay reparto posible, nadie más está moviendo), y el
        arranque de `post`, donde la barrera entre ejecuciones del Job garantiza
        que `struct` acabó. En `post` repartido lo hace solo la tarea 0: las ocho
        harían el mismo trabajo pisándose, y las otras siete trabajan sobre
        `PBx/PBx_Vy`, que el barrido no toca.
        """
        apartadas_termica, apartadas_rgb = self.gen_struct_folder_obj.checking_results_gen_struct_folder(
            cfg.output_folder, progress_callback)

        # GUARD "todo fuera del estadillo": si tras generar la estructura no
        # sobrevive NINGUNA imagen en TERMICA/RGB y sí se apartaron a
        # SIN_ORDENAR, ninguna foto encajó en la ventana horaria de ningún
        # vuelo. Sin este guard las fases siguientes (recorte, meta,
        # miniaturas) procesan 0 imágenes y salen en VERDE — porque
        # contar_imagenes_or_tmc excluye SIN_ORDENAR del total esperado, así
        # que 0 esperadas == 0 procesadas cuadra — y el run solo revienta al
        # final, en la conversión a TIF, con un error que culpa al dron
        # (no encuentra térmica de muestra) en vez de al estadillo.
        quedan_termica = self.utils_obj.contar_imagenes_or_tmc(os.path.join(cfg.output_folder, "TERMICA"))
        quedan_rgb = self.utils_obj.contar_imagenes_or_tmc(os.path.join(cfg.output_folder, "RGB"))
        apartadas = apartadas_termica + apartadas_rgb
        if quedan_termica == 0 and quedan_rgb == 0 and apartadas > 0:
            msg = (f"Ninguna de las {apartadas} imágenes encaja en la franja horaria de ningún "
                   f"vuelo del estadillo: todas se han apartado a SIN_ORDENAR y no queda nada que "
                   f"procesar. Revisa que el estadillo sea el de este vuelo (fecha correcta) y el "
                   f"desfase horario configurado (ahora: {cfg.mismatch_hours} h, {cfg.mismatch_minutes} min; "
                   f"margen {cfg.seconds_range} s). Las imágenes NO se han perdido: están en "
                   f"{os.path.join(cfg.output_folder, 'SIN_ORDENAR')}.")
            progress_callback.emit("ERROR: " + msg + "\n")
            self.organizer_logger_obj.logger.info(msg)
            raise ValueError(msg)

    def _rutas_del_shard(self, output_folder: str, subcarpetas: list[str],
                         mis_pbs) -> list[str]:
        """Rutas concretas sobre las que iterar: las raíces (`destino/TERMICA`)
        si no hay reparto, o `destino/TERMICA/PBx/PBx_Vy` por cada vuelo propio
        si lo hay.

        Las funciones del pipeline que reciben estas rutas son recursivas y no
        miran dónde empieza el árbol, así que arrancar en el vuelo en vez de en la
        raíz hace exactamente el mismo trabajo sobre menos carpetas."""
        rutas = []
        for sub in subcarpetas:
            raiz = unir(output_folder, sub)
            if mis_pbs is None:
                if _existe_carpeta(raiz):
                    rutas.append(raiz)
                continue
            for relativo in mis_pbs:
                ruta = sharding.ruta_de_relativo(raiz, relativo)
                if _existe_carpeta(ruta):
                    rutas.append(ruta)
        return rutas

    def _contar_del_shard(self, output_folder: str, subcarpetas: list[str], mis_pbs,
                          exclude_patterns=None, exclude_folders=None) -> int:
        """Imágenes que va a procesar ESTA tarea, para que la barra de progreso
        llegue al 100 % en vez de quedarse en la fracción que le tocó."""
        return sum(
            self.utils_obj.contar_imagenes_or_tmc(
                ruta, exclude_patterns=exclude_patterns, exclude_folders=exclude_folders)
            for ruta in self._rutas_del_shard(output_folder, subcarpetas, mis_pbs))

    def _contar_sobrantes_propios(self, output_folder: str, shard_index: int,
                                  shard_count: int) -> int:
        """Imágenes de ESTA tarea que siguen sueltas en la raíz de TERMICA/RGB
        tras generar la estructura: las que no encajan en ningún vuelo.

        Se cuentan, no se mueven. Quien las aparta a `SIN_ORDENAR` es el barrido,
        que con `--etapa struct` corre después, ya en `post` (ver
        `_barrido_sin_ordenar`). Pero `get_summarize` compara el total esperado
        con las procesadas, y una imagen que se queda en la raíz nunca pasa por
        `_mover_pares`: sin sumarlas aquí, un reparto perfectamente sano sale en
        ROJO por «no hay correspondencia entre número inicial y final».

        Mirar la raíz mientras las demás tareas trabajan es seguro porque se
        filtra por `toca_imagen`: las imágenes propias ya están colocadas o
        descartadas, y ninguna otra tarea las toca.
        """
        total = 0
        for sub in ("TERMICA", "RGB", "RGB_extra"):
            carpeta = unir(output_folder, sub)
            if not _existe_carpeta(carpeta):
                continue
            total += sum(1 for imagen in self.utils_obj.get_images_from_dir(carpeta)
                         if sharding.toca_imagen(imagen, shard_index, shard_count))
        return total

    #%% Funciones que llaman a los objetos correspondientes
    def call_to_compress_image(self, cfg: CompressRgbsConfig, progress_callback, progress_bar, progress_summarize) -> None:
        """Función que llama a la clase CompressImage para proceder a la compresión de imágenes.

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().compress_rgbs`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: Compresión de imágenes.")
        self.organizer_logger_obj.logger.info("###################################################################")

        processing_time = self.utils_obj.logging_time()

        self.compress_image_obj.reset_variables()
        self.compress_image_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.input_folder)
        self.new_log_gui.enable_process()

        self.compress_image_obj.start_compression(cfg.input_folder, cfg.output_folder, cfg.compress_level,
        cfg.include_subfolders, progress_callback, progress_bar)

        summarize_dict = self.compress_image_obj.get_summarize()
        self.show_summarize(summarize_dict, progress_summarize)

        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time
        
    
    def gen_meta_location(self, cfg: GenMetaLocationConfig, progress_callback, progress_bar, progress_summarize) -> str:
        """Función que llama a la clase MetaLocation para generar los archivos meta.csv y/o location.csv a partir de los datos exif de las imágenes.

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().gen_meta_location`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """

        if(cfg.calculate_proyected_distance and cfg.flight_height == 0):
            progress_callback.emit("\nERROR: Es necesario cubrir la altura de vuelo\n")
            self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: Es necesario cubrir la altura de vuelo")
        else:
            self.organizer_logger_obj.logger.info("###################################################################")
            self.organizer_logger_obj.logger.info("PROCESO: Generación de meta y location.")
            self.organizer_logger_obj.logger.info("###################################################################")

            processing_time = self.utils_obj.logging_time()

            self.meta_location_obj.reset_variables(main_process=True, progress_callback=progress_callback)
            self.meta_location_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.input_folder, exclude_patterns=["_CROP"], exclude_folders=["MINIATURAS", "CSVs"])
            self.organizer_logger_obj.logger.info(f"El número total de imágenes es: {self.meta_location_obj.total_images_number}")
            self.new_log_gui.enable_process()

            self.utils_obj.prepare_output_folder(cfg.input_folder, ["CSVs"])
            csv_folder = unir(cfg.input_folder, "CSVs")
            if cfg.mode_meta:
                self.meta_location_obj.iterate_folders(cfg.input_folder, "meta.csv", progress_callback, progress_bar, csv_folder, cfg.flight_height, cfg.calculate_proyected_distance)
            elif cfg.mode_location:
                self.meta_location_obj.iterate_folders(cfg.input_folder, "location.csv", progress_callback, progress_bar, csv_folder, cfg.flight_height, cfg.calculate_proyected_distance)
            elif cfg.mode_location_and_meta:
                if self.meta_location_obj.check_input_folder_and_iterate(cfg.input_folder, progress_callback, progress_bar, csv_folder, cfg.flight_height, cfg.calculate_proyected_distance) is False:
                    progress_callback.emit("No se puede iniciar el proceso: No se encuentran los directorios RGB y TERMICA")
                    self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: No se encuentran los directorios RGB y TERMICA")

            self.meta_location_obj.checking_results_meta_location(cfg.input_folder, progress_callback, progress_summarize)

            summarize_dict = self.meta_location_obj.get_summarize()
            self.show_summarize(summarize_dict, progress_summarize)

            processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time

    def split_images(self, cfg: SplitImagesConfig, progress_callback, progress_bar, progress_summarize) -> None:
        """Función que llama a la clase SplitImages para llevar a cabo la separación de imágenes térmicas o RGBs dependiendo de las opciones elegidas en el interfaz

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().split_images`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.

        Reparto entre tareas (opcional): el host puede fijar `etapa`,
        `shard_index` y `shard_count` (los pone `atom_core.organize.run_task` a
        partir de la línea de comandos o de las variables de Cloud Run). Sin
        ellos, los defaults de aquí dejan el método EXACTAMENTE como estaba —
        que es como lo sigue llamando la GUI Qt. Ver `atom_core/sharding`.
        """
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: ORGANIZAR COMPLETO")
        self.organizer_logger_obj.logger.info("###################################################################")

        etapa = getattr(self, "etapa", "todo")
        shard_index, shard_count = sharding.normalizar_shard(
            getattr(self, "shard_index", 0), getattr(self, "shard_count", 1))
        hacer_split = etapa in ("todo", "split")
        hacer_struct = etapa in ("todo", "struct")
        hacer_post = etapa in ("todo", "post")
        repartido = shard_count > 1
        # El modo de destino se fija AQUI, fuera de cualquier `if hacer_*`, y no
        # dentro del bloque de struct. Con `--etapa post` (cada etapa es un
        # proceso nuevo de Cloud Run, con su propio GenStructFolder recien
        # construido) `hacer_struct` es False, pero el barrido de sobrantes SI
        # corre y mueve imagenes con `self.modo_destino`. Si la asignacion vive
        # dentro del bloque de struct, un `--etapa post --modo-destino obviar`
        # -relanzar tras un fallo parcial, que es justo el caso de uso de
        # `obviar`- se queda con el default y sobrescribe EN SILENCIO.
        # `reset_variables` no toca este campo, asi que fijarlo antes es seguro.
        self.gen_struct_folder_obj.modo_destino = getattr(
            cfg, "modo_destino", utils.MODO_SOBRESCRIBIR)
        if etapa != "todo" or repartido:
            progress_callback.emit(
                f"\nEtapa: {etapa} · tarea {shard_index + 1} de {shard_count}\n")
            self.organizer_logger_obj.logger.info(
                f"Etapa={etapa} shard={shard_index + 1}/{shard_count}")

        if(cfg.end_rgb_extra_files != "" and (cfg.end_thermo_files == ""
                                                                         or cfg.end_rgb_files == "")):
            progress_callback.emit("\nNo se puede iniciar el proceso: Si se quiere separar imágenes con un tercer grupo de sufijos RGB, es necesario cubrir los tres campos de terminaciones\n")
        elif(cfg.gen_meta_location and cfg.calculate_proyected_distance and cfg.flight_height == 0):
            progress_callback.emit("\nNo se puede iniciar el proceso: Es necesario cubrir la altura de vuelo\n")
        else:
            processing_time = self.utils_obj.logging_time()

            # Imágenes del ORIGEN que le tocan a esta tarea, agrupadas por
            # carpeta. En modo no repartido es None y se recorre el árbol entero
            # como siempre.
            mis_imagenes_origen = None
            if hacer_split and repartido:
                mis_imagenes_origen = self._imagenes_origen_del_shard(
                    cfg.input_folder, shard_index, shard_count, progress_callback)

            if hacer_split:
                self.organizer_logger_obj.logger.info("---------------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: SEPARACIÓN DE IMÁGENES ENTRE RGB Y TÉRMICAS.")
                self.organizer_logger_obj.logger.info("---------------------------------------------------------------------")

                progress_callback.emit('\n-----------------------------------------------------------------\n')
                progress_callback.emit("SUBPROCESO: SEPARACIÓN DE IMÁGENES ENTRE RGB Y TÉRMICAS.\n")
                progress_callback.emit('-----------------------------------------------------------------\n')

                progress_summarize.emit("__________________")
                progress_summarize.emit("---> SUBPROCESO: SEPARACIÓN DE IMÁGENES ENTRE RGB Y TÉRMICAS.")

                self.utils_obj.prepare_output_folder(cfg.output_folder, ["RGB", "TERMICA"])
                if mis_imagenes_origen is None:
                    self.split_images_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.input_folder)
                    self.split_images_obj.iterate_folders(cfg.input_folder, cfg.output_folder,
                    cfg.choose_mode_size, cfg.max_size, cfg.end_thermo_files,
                    cfg.end_rgb_files, cfg.compress_rgb, cfg.compress_level,
                    progress_callback, cfg.rename_images, progress_bar, cfg.mismatch_hours, cfg.mismatch_minutes)
                else:
                    # Se llama a `split_images` (una carpeta, y solo con las
                    # imágenes de esta tarea) y NO a `iterate_folders`, que
                    # recursaría por el árbol y volvería a procesar lo de las
                    # demás tareas.
                    self.split_images_obj.total_images_number = sum(
                        len(v) for v in mis_imagenes_origen.values())
                    for carpeta, imagenes in sorted(mis_imagenes_origen.items()):
                        if getattr(self.split_images_obj, "stop", False):
                            break
                        progress_callback.emit("Analizando directorio: " + carpeta + "\n")
                        self.split_images_obj.split_images(carpeta, cfg.output_folder,
                        cfg.choose_mode_size, cfg.max_size, cfg.end_thermo_files,
                        cfg.end_rgb_files, cfg.compress_rgb, cfg.compress_level,
                        progress_callback, cfg.rename_images, progress_bar, cfg.mismatch_hours, cfg.mismatch_minutes,
                        solo_imagenes=imagenes)

                summarize_dict = self.split_images_obj.get_summarize()
                self.show_summarize(summarize_dict, progress_summarize)

            # Si se ha cubierto el textbox del sufijo extra, entonces procesamos las imágenes con ese sufijo después de los sufijos, digamos, normales.
            if(hacer_split and cfg.end_rgb_extra_files != ""):
                self.organizer_logger_obj.logger.info("---------------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: CREANDO Y PROCESANDO CARPETA RGB_EXTRA.")
                self.organizer_logger_obj.logger.info("---------------------------------------------------------------------")

                progress_callback.emit('\n-----------------------------------------------------------------\n')
                progress_callback.emit("SUBPROCESO: CREANDO Y PROCESANDO CARPETA RGB_EXTRA.\n")
                progress_callback.emit('-----------------------------------------------------------------\n')

                progress_summarize.emit("__________________")
                progress_summarize.emit("---> SUBPROCESO: CREANDO Y PROCESANDO CARPETA RGB_EXTRA.")

                self.utils_obj.prepare_output_folder(cfg.output_folder, ["RGB_Extra"])
                # Reseteamos las variables para volver a contar.
                self.split_images_obj.reset_variables()
                self.new_log_gui.enable_process()

                if mis_imagenes_origen is None:
                    self.split_images_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.input_folder)
                    self.split_images_obj.iterate_folders(cfg.input_folder, cfg.output_folder,
                    False, "0", "", cfg.end_rgb_extra_files, cfg.compress_rgb,
                    cfg.compress_level, progress_callback, cfg.rename_images, progress_bar, cfg.mismatch_hours, cfg.mismatch_minutes, True)
                else:
                    # Mismo reparto que la separación normal: el sufijo extra se
                    # busca en las MISMAS imágenes que le tocaron a esta tarea,
                    # así ninguna queda sin dueño ni con dos.
                    self.split_images_obj.total_images_number = sum(
                        len(v) for v in mis_imagenes_origen.values())
                    for carpeta, imagenes in sorted(mis_imagenes_origen.items()):
                        if getattr(self.split_images_obj, "stop", False):
                            break
                        progress_callback.emit("Analizando directorio: " + carpeta + "\n")
                        self.split_images_obj.split_images(carpeta, cfg.output_folder,
                        False, "0", "", cfg.end_rgb_extra_files, cfg.compress_rgb,
                        cfg.compress_level, progress_callback, cfg.rename_images, progress_bar, cfg.mismatch_hours, cfg.mismatch_minutes, True,
                        solo_imagenes=imagenes)

            if hacer_struct and cfg.organize_images:
                self.organizer_logger_obj.logger.info("---------------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: GENERAR ESTRUCTURA DE CARPETAS.")
                self.organizer_logger_obj.logger.info("---------------------------------------------------------------------")

                self.gen_struct_folder_obj.reset_variables(main_process=False, progress_callback=progress_callback)
                # El total esperado es el de ESTA tarea y SOLO la entrada de esta
                # fase. Tres matices, los tres aprendidos a base de runs en rojo:
                #  1. Con el reparto, cada tarea mueve solo las imagenes que le
                #     asigna `toca_imagen`; contar sin filtro dejaba a las ocho
                #     comparando ~300 procesadas contra 2.516.
                #  2. `output_folder` es entrada Y salida de esta fase: struct lee
                #     de TERMICA/ y RGB/ y escribe en PBx/PBx_Vy/. Contar el arbol
                #     entero mete en el total lo que ya estaba organizado de una
                #     pasada anterior — imagenes que nadie va a mover — y el run
                #     acaba en "No hay correspondencia" sin que haya fallado nada.
                #     Con `--modo-destino obviar` ese caso es la norma, no la
                #     excepcion: la op 9 de la inspeccion 330 murio con "715
                #     inicial contra 283 final" en las ocho tareas. Por eso
                #     `recursivo=False`: el universo de esta fase es el NIVEL
                #     SUPERIOR de TERMICA/ y RGB/, el mismo que recorren
                #     `os.listdir` en `organizar_imagenes_en_vuelos` y
                #     `get_images_from_dir` en `_contar_sobrantes_propios`.
                #     Excluir `SIN_ORDENAR` (que ya hace `contar_imagenes_or_tmc`)
                #     no bastaba: las subcarpetas `PBx/PBx_Vy/` seguian entrando.
                #  3. Con sufijo RGB extra, `organizar_imagenes_en_vuelos` recorre
                #     TAMBIEN RGB_extra (pipeline.py:1158) y cada movimiento suma a
                #     `current_image_number`. Dejarla fuera del total descuadra por
                #     lo bajo — el mismo run en rojo, por la puerta de atras. Mismo
                #     universo de carpetas que usa `_contar_sobrantes_propios`.
                subcarpetas = ["TERMICA", "RGB"]
                if cfg.end_rgb_extra_files != "":
                    subcarpetas.append("RGB_extra")
                filtro = (None if shard_count <= 1
                          else lambda n: sharding.toca_imagen(n, shard_index, shard_count))
                self.gen_struct_folder_obj.total_images_number = sum(
                    self.utils_obj.contar_imagenes_or_tmc(
                        os.path.join(cfg.output_folder, sub), filtro_nombre=filtro,
                        recursivo=False)
                    for sub in subcarpetas)
                self.new_log_gui.enable_process()

                progress_summarize.emit("__________________")
                progress_summarize.emit("---> SUBPROCESO: GENERAR ESTRUCTURA DE CARPETAS.")

                if cfg.end_rgb_extra_files != "":  # Comprobamos que hemos puesto sufijo extra
                    self.gen_struct_folder_obj.gen_folder_struct(cfg.estad, cfg.output_folder, cfg.output_folder,
                                                        True, cfg.seconds_range, cfg.mismatch_hours, cfg.mismatch_minutes, progress_callback, progress_bar, extra_suffix = True, include_v=cfg.include_v,
                                                        shard_index=shard_index, shard_count=shard_count)
                else:
                    self.gen_struct_folder_obj.gen_folder_struct(cfg.estad, cfg.output_folder, cfg.output_folder,
                                                        True, cfg.seconds_range, cfg.mismatch_hours, cfg.mismatch_minutes, progress_callback, progress_bar, include_v=cfg.include_v,
                                                        shard_index=shard_index, shard_count=shard_count)

                # Barrido de sobrantes SOLO en `todo`: es la única forma de esta
                # etapa en la que nadie más está moviendo imágenes ahora mismo.
                # Con `--etapa struct` (repartida o no) lo hace el arranque de
                # `post`, que es donde hay garantía de que struct ha acabado.
                # Ver `_barrido_sin_ordenar`.
                if etapa == "todo":
                    self._barrido_sin_ordenar(cfg, progress_callback)
                else:
                    # Sin barrido aquí, las imágenes fuera del estadillo se quedan
                    # en la raíz sin pasar por `_mover_pares`. Se cuentan para que
                    # el cuadre de `get_summarize` siga siendo una verificación
                    # real (si falta una de verdad, sigue saliendo en rojo).
                    self.gen_struct_folder_obj.current_image_number += self._contar_sobrantes_propios(
                        cfg.output_folder, shard_index, shard_count)

                summarize_dict = self.gen_struct_folder_obj.get_summarize()
                self.show_summarize(summarize_dict, progress_summarize)

            # Precondicion de `post`: trabaja sobre lo que dejaron `split` y
            # `struct`. Si no esta, se falla aqui y no 200 lineas mas abajo.
            if hacer_post and etapa == "post":
                self._exigir_destino_estructurado(cfg.output_folder)

            # Arranque de `post`: aquí `struct` ya terminó del todo (son
            # ejecuciones distintas del Job y la segunda no arranca hasta que la
            # primera cierra), así que lo que siga suelto en la raíz de RGB/TERMICA
            # es de verdad lo que no quiso ningún vuelo. Lo hace solo la tarea 0.
            if hacer_post and etapa == "post" and shard_index == 0 and cfg.organize_images:
                self._barrido_sin_ordenar(cfg, progress_callback)

            # PBs del destino que le tocan a esta tarea. Las fases que siguen
            # (recorte, meta, rotación, TIF) trabajan sobre el destino YA
            # estructurado y deciden carpeta a carpeta, así que repartir por PB
            # da el mismo resultado que procesarlos todos seguidos.
            mis_pbs = None
            if hacer_post and repartido:
                mis_pbs = self._pbs_del_shard(cfg.output_folder, shard_index,
                                              shard_count, progress_callback)

            if hacer_post and cfg.cropping_rgb:
                progress_callback.emit('\n-----------------------------------------------------------------\n')
                progress_callback.emit("SUBPROCESO: RECORTE DE IMÁGENES RGB.\n")
                progress_callback.emit('-----------------------------------------------------------------\n')

                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: RECORTE DE IMÁGENES RGB.")
                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")

                progress_summarize.emit("__________________")
                progress_summarize.emit("---> SUBPROCESO: RECORTE DE IMÁGENES RGB.")

                rgb_root = os.path.join(cfg.output_folder, "RGB")
                self.rgb_cropping_obj.reset_variables()
                self.new_log_gui.enable_process()

                if mis_pbs is None:
                    self.rgb_cropping_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(rgb_root)
                    self.rgb_cropping_obj.iterate_folders_for_rgb_cropping(rgb_root, progress_callback, progress_bar, self.config_obj.percentage_by_models, cfg.cropping_mode_auto, cfg.crop_percentage)
                else:
                    self.rgb_cropping_obj.total_images_number = sum(
                        self.utils_obj.contar_imagenes_or_tmc(os.path.join(rgb_root, pb))
                        for pb in mis_pbs if os.path.isdir(os.path.join(rgb_root, pb)))
                    for pb in mis_pbs:
                        pb_path = os.path.join(rgb_root, pb)
                        if os.path.isdir(pb_path):
                            self.rgb_cropping_obj.iterate_folders_for_rgb_cropping(pb_path, progress_callback, progress_bar, self.config_obj.percentage_by_models, cfg.cropping_mode_auto, cfg.crop_percentage)

                # La verificación se acota a MIS PBs: las otras tareas están
                # escribiendo en los suyos en este mismo momento y verlos a medias
                # daría un "no coinciden" que no es un fallo, sino una foto movida.
                self.rgb_cropping_obj.checking_results_rgb_cropping(rgb_root, progress_callback, progress_summarize, only_pb=mis_pbs)

                summarize_dict = self.rgb_cropping_obj.get_summarize()
                self.show_summarize(summarize_dict, progress_summarize)

            if hacer_post and cfg.gen_meta_location:
                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: Generación de los archivo meta y location.")
                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")

                self.meta_location_obj.reset_variables(main_process=False, progress_callback=progress_callback)
                self.new_log_gui.enable_process()
                # self.organizer_logger_obj.logger.debug(f"Imágenes para meta y location: {self.meta_location_obj.total_images_number}")

                progress_summarize.emit("__________________")
                progress_summarize.emit("---> SUBPROCESO: GENERACIÓN DE LOS ARCHIVOS META Y LOCATION.")

                self.utils_obj.prepare_output_folder(cfg.output_folder, ["CSVs"])
                csv_folder = unir(cfg.output_folder, "CSVs")

                if mis_pbs is None:
                    self.meta_location_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.output_folder, exclude_patterns=["_CROP"])
                else:
                    self.meta_location_obj.total_images_number = sum(
                        self.utils_obj.contar_imagenes_or_tmc(os.path.join(cfg.output_folder, sub, pb), exclude_patterns=["_CROP"])
                        for sub in ("RGB", "TERMICA", "RGB_Extra") for pb in mis_pbs
                        if os.path.isdir(os.path.join(cfg.output_folder, sub, pb)))

                # Los CSV van todos a `destino/CSVs`, compartido por las N tareas,
                # pero el nombre de cada uno lleva el prefijo `PBx_Vy`: PBs
                # distintos no pueden colisionar, así que no hace falta lock.
                if self.meta_location_obj.check_input_folder_and_iterate(cfg.output_folder, progress_callback, progress_bar, csv_folder, cfg.flight_height, cfg.calculate_proyected_distance, only_pb=mis_pbs) is False:
                    progress_callback.emit("\nNo se puede iniciar el proceso: No se han podido generar los archivos meta y location.\n")
                    self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: No se han podido generar los archivos meta y location.")

                self.meta_location_obj.checking_results_meta_location(cfg.output_folder, progress_callback, progress_summarize, only_pb=mis_pbs)

                summarize_dict = self.meta_location_obj.get_summarize()
                self.show_summarize(summarize_dict, progress_summarize)

            if hacer_post and cfg.gen_thumbnails:
                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: ROTACIÓN.")
                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")

                self.gen_struct_folder_obj.reset_variables(main_process=False, gen_thumbnails=True, progress_callback=progress_callback)
                self.new_log_gui.enable_process()

                progress_summarize.emit("__________________")
                progress_summarize.emit("---> SUBPROCESO: ROTACIÓN.")

                lim_max_90 = cfg.gen_thumbnails_add_to_angle + 90
                lim_max_270 = cfg.gen_thumbnails_add_to_angle - 90
                lim_min_90 = 90 - cfg.gen_thumbnails_subs_to_angle
                lim_min_270 = (-90) - cfg.gen_thumbnails_subs_to_angle

                # self.organizer_logger_obj.logger.debug(f"Lim max 90: {lim_max_90}")
                # self.organizer_logger_obj.logger.debug(f"Lim min 90: {lim_min_90}")
                # self.organizer_logger_obj.logger.debug(f"Lim max 270: {lim_max_270}")
                # self.organizer_logger_obj.logger.debug(f"Lim min 270: {lim_min_270}")

                # El criterio de giro se calcula POR CARPETA `PBx_Vy` (mira el yaw
                # de sus imágenes y decide si gira el vuelo entero), así que
                # repartir por PB no cambia lo que sale: cada carpeta la ve una
                # sola tarea, completa.
                if cfg.gen_thumbnails_rgb and not cfg.gen_thumbnails_termica:
                    carpetas_rot = ["RGB"]
                    aviso_rot = "El directorio RGB no se encuentra en la carpeta de entrada."
                elif cfg.gen_thumbnails_termica and not cfg.gen_thumbnails_rgb:
                    carpetas_rot = ["TERMICA"]
                    aviso_rot = "El directorio TERMICA no se encuentra en la carpeta de entrada."
                elif cfg.gen_thumbnails_rgb and cfg.gen_thumbnails_termica:
                    carpetas_rot = ["TERMICA", "RGB"]
                    aviso_rot = "Los directorios RGB y TERMICA no se encuentran en la carpeta de entrada."
                else:
                    carpetas_rot = []
                    aviso_rot = ""

                if carpetas_rot:
                    self.gen_struct_folder_obj.total_images_number = self._contar_del_shard(
                        cfg.output_folder, carpetas_rot, mis_pbs,
                        exclude_folders=["MINIATURAS", "CSVs"])
                    # logging.info(f"Number of total images for miniatures: {self.gen_struct_folder_obj.total_images_number}")
                    if self.gen_struct_folder_obj.check_input_folder_and_iterate(cfg.output_folder, carpetas_rot, cfg.gen_thumbnails_max_error,
                                                                                lim_max_270, lim_min_270,
                                                                                lim_max_90, lim_min_90, cfg.choose_mode_auto, cfg.gen_thumbnails_rotate_90, progress_callback, progress_bar, only_pb=mis_pbs) is False:
                        progress_callback.emit(f"\nNo se puede iniciar el proceso: {aviso_rot}\n")
                        self.organizer_logger_obj.logger.info(f"No se puede iniciar el proceso: {aviso_rot}")

                summarize_dict = self.gen_struct_folder_obj.get_summarize()
                self.show_summarize(summarize_dict, progress_summarize)

            if hacer_post and cfg.convert_to_tif:
                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: CONVERTIR IMÁGENES DJI A TIFF.")
                self.organizer_logger_obj.logger.info("-----------------------------------------------------------------")

                progress_callback.emit("\n----------------------------------------------------\n")
                progress_callback.emit("SUBPROCESO: CONVERTIR IMÁGENES DJI A TIFF\n")
                progress_callback.emit("----------------------------------------------------\n")

                progress_summarize.emit("__________________")
                progress_summarize.emit("---> SUBPROCESO: CONVERTIR IMÁGENES A TIF.")

                termica_root = os.path.join(cfg.output_folder, "TERMICA")
                self.split_images_obj.reset_variables()
                self.split_images_obj.total_images_number = self._contar_del_shard(
                    cfg.output_folder, ["TERMICA"], mis_pbs,
                    exclude_patterns=[utils.ROTATED_JPG_SUFFIX])  # legado 3.4.5: las copias `_ROT` de una carpeta ya procesada no se convierten, contarlas dejaria la barra a medias
                self.new_log_gui.enable_process()

                exiftool_exe = resource_path("programas_externos", "exiftool.exe")
                # Con la RAÍZ de TERMICA a propósito, también en modo repartido:
                # esta función busca una térmica de muestra para deducir el modelo
                # de dron, y si esta tarea no tuviera ninguna en sus PBs se
                # quedaría sin selector y fallaría la conversión entera.
                self._resolve_dron_selector(cfg.convert_to_tif_dron_selector, termica_root, progress_callback)
                dji_utility = config.dji_utility_path()
                for carpeta_tif in self._rutas_del_shard(cfg.output_folder, ["TERMICA"], mis_pbs):
                    self.split_images_obj.iterate_folders_for_DJI(carpeta_tif, exiftool_exe, dji_utility,
                                                          progress_callback, progress_bar, cfg.convert_to_tif_emissivity,
                                                          cfg.convert_to_tif_humidity, cfg.convert_to_tif_temp_auto,
                                                          cfg.convert_to_tif_up_temperature, cfg.convert_to_tif_low_temperature,
                                                          cfg.convert_to_tiff_rotate_90, cfg.convert_to_tiff_rotate_minus_90,
                                                          cfg.convert_to_tiff_rotate_auto, cfg.convert_to_tif_solo_seleccion_atom,
                                                          cfg.convert_to_tif_create_gray_scale_images)

                self.split_images_obj.checking_convert_to_tif(termica_root, progress_callback, progress_summarize, only_pb=mis_pbs)

                # Giro in-place del JPG térmico, para que case con su TIFF. Va DESPUÉS
                # de la conversión y de su verificación a propósito: girarlo destruye
                # el payload radiométrico del R-JPEG, así que el conversor tiene que
                # haber terminado ya con él.
                #
                # Cuelga del switch maestro de ROTACION (`gen_thumbnails`, el que
                # apaga `--sin-rotacion`): girar el R-JPEG es IRREVERSIBLE —se pierde
                # el APP3/4/5 y con él la radiometría del original—, así que quien
                # pide «sin rotación» no puede acabar con sus R-JPEG destruidos. Sin
                # esta guarda, `--sin-rotacion` solo apagaba las miniaturas y esta
                # tarea seguía corriendo (verificado en la execution `wpv52`,
                # 2026-08-21: 6 R-JPEG de CLARE de 3,8 MB reducidos a 600 KB sin
                # payload). El TIFF ya está escrito y girado a estas alturas, así que
                # lo único que se pierde es que el JPG case visualmente con él.
                if not cfg.gen_thumbnails:
                    progress_callback.emit(
                        "\nNo se giran los JPG térmicos en su sitio: el subproceso "
                        "ROTACION está desactivado. Los R-JPEG quedan intactos.\n")
                else:
                    for carpeta_rot in self._rutas_del_shard(cfg.output_folder, ["TERMICA"], mis_pbs):
                        self.split_images_obj.rotate_thermal_jpgs_in_place(
                            carpeta_rot, progress_callback, progress_bar,
                            cfg.convert_to_tiff_rotate_90, cfg.convert_to_tiff_rotate_minus_90,
                            cfg.convert_to_tiff_rotate_auto)

                summarize_dict = self.split_images_obj.get_summarize()
                self.show_summarize(summarize_dict, progress_summarize)

            processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)

            return processing_time
                
    def gen_struct_folder(self, cfg: GenStructFolderConfig, progress_callback, progress_bar, progress_summarize):
        """Función que llama a la clase GenStructFolder para generar la estructura de las carpetas en un directorio de salida a partir del estadillo

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().gen_struct_folder`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        processing_time = self.utils_obj.logging_time()

        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: Generación de estructura de carpetas.")
        self.organizer_logger_obj.logger.info("###################################################################")

        self.gen_struct_folder_obj.gen_folder_struct(cfg.estad, cfg.output_folder, cfg.output_folder,
                                                     cfg.organize_images, cfg.seconds_range, cfg.mismatch_hours,
                                                     cfg.mismatch_minutes, progress_callback, progress_bar, include_v=cfg.include_v)

        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time

    def gen_thumbnails(self, cfg: GenThumbnailsConfig, progress_callback, progress_bar, progress_summarize):
        """Función que llama a la clase GenStructFolder para generar las miniaturas

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().gen_thumbnails`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        processing_time = self.utils_obj.logging_time()

        self.gen_struct_folder_obj.reset_variables(main_process=True, gen_thumbnails=True, progress_callback=progress_callback)
        self.new_log_gui.enable_process()

        # Por ahora que llamen al directorio en el que quieran crear las miniaturas, que no sea el directorio con RGB y TERMICA. Así, pueden probar
        # con pequeños directorios y ver que todo funciona correctamente.
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: Rotación de imágenes.")
        self.organizer_logger_obj.logger.info("###################################################################")

        lim_max_90 = cfg.add_to_angle + 90
        lim_max_270 = cfg.add_to_angle - 90
        lim_min_90 = 90 - cfg.subs_to_angle
        lim_min_270 = (-90) - cfg.subs_to_angle

        # self.organizer_logger_obj.logger.debug(f"Lim max 90: {lim_max_90}")
        # self.organizer_logger_obj.logger.debug(f"Lim min 90: {lim_min_90}")
        # self.organizer_logger_obj.logger.debug(f"Lim max 270: {lim_max_270}")
        # self.organizer_logger_obj.logger.debug(f"Lim min 270: {lim_min_270}")

        if cfg.rgb and not cfg.termica:
            self.gen_struct_folder_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(os.path.join(cfg.input_folder, "RGB"), exclude_folders=["MINIATURAS", "CSVs"])
            if self.gen_struct_folder_obj.check_input_folder_and_iterate(cfg.input_folder,["RGB"], cfg.max_error,
                                                                         lim_max_270, lim_min_270,
                                                                         lim_max_90, lim_min_90, cfg.choose_mode_auto, cfg.rotate_90, progress_callback, progress_bar) is False:
                progress_callback.emit("\nNo se puede iniciar el proceso: El directorio RGB no se encuentra en la carpeta de entrada.\n")
                self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: RGB folder does not exist in input folder.")
        elif cfg.termica and not cfg.rgb:
            self.gen_struct_folder_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(os.path.join(cfg.input_folder, "TERMICA"), exclude_folders=["MINIATURAS", "CSVs"])
            if self.gen_struct_folder_obj.check_input_folder_and_iterate(cfg.input_folder,["TERMICA"], cfg.max_error,
                                                                         lim_max_270, lim_min_270,
                                                                         lim_max_90, lim_min_90, cfg.choose_mode_auto, cfg.rotate_90, progress_callback, progress_bar) is False:
                progress_callback.emit("\nNo se puede iniciar el proceso: El directorio TERMICA no se encuentra en la carpeta de entrada.\n")
                self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: TERMICA folder does not exist in input folder.")
        elif cfg.rgb and cfg.termica:
            self.gen_struct_folder_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.input_folder, exclude_folders=["MINIATURAS", "CSVs"])
            if self.gen_struct_folder_obj.check_input_folder_and_iterate(cfg.input_folder,["TERMICA", "RGB"], cfg.max_error,
                                                                         lim_max_270, lim_min_270,
                                                                         lim_max_90, lim_min_90, cfg.choose_mode_auto, cfg.rotate_90, progress_callback, progress_bar) is False:
                progress_callback.emit("\nNo se puede iniciar el proceso: Los directorios RGB y TERMICA no se encuentran en la carpeta de entrada.\n")
                self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: RGB and TERMICA folders do not exist in input folder.")

        summarize_dict = self.gen_struct_folder_obj.get_summarize()
        self.show_summarize(summarize_dict, progress_summarize)

        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time
    
    def gen_thumbnails_for_aerotools(self, cfg: GenThumbnailsConfig, progress_callback, progress_bar, progress_summarize):
        """Función que llama a la clase GenStructFolder para generar las miniaturas, pero sólo para las imágenes de AEROTOOLS.
        Funciona del mismo modo que la generación de las miniaturas para OTROS EQUIPOS.

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().gen_thumbnails`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """

        # Por ahora que llamen al directorio en el que quieran crear las miniaturas, que no sea el directorio con RGB y TERMICA. Así, pueden probar
        # con pequeños directorios y ver que todo funciona correctamente.
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: Rotación de las imágenes de AEROTOOLS.")
        self.organizer_logger_obj.logger.info("###################################################################")

        lim_max_90 = cfg.aerotools_add_to_angle + 90
        lim_max_270 = cfg.aerotools_add_to_angle - 90
        lim_min_90 = 90 - cfg.aerotools_subs_to_angle
        lim_min_270 = (-90) - cfg.aerotools_subs_to_angle

        # self.organizer_logger_obj.logger.debug(f"Lim max 90: {lim_max_90}")
        # self.organizer_logger_obj.logger.debug(f"Lim min 90: {lim_min_90}")
        # self.organizer_logger_obj.logger.debug(f"Lim max 270: {lim_max_270}")
        # self.organizer_logger_obj.logger.debug(f"Lim min 270: {lim_min_270}")

        processing_time = self.utils_obj.logging_time()

        if cfg.aerotools_rgb and not cfg.aerotools_termica:
            if self.gen_struct_folder_obj.check_input_folder_and_iterate(cfg.aerotools_input_folder,["RGB"], cfg.aerotools_max_error,
                                                                         lim_max_270, lim_min_270,
                                                                         lim_max_90, lim_min_90, progress_callback, progress_bar) is False:
                progress_callback.emit("\nNo se puede iniciar el proceso: El directorio RGB no se encuentra en la carpeta de entrada.\n")
                self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: El directorio RGB no se encuentra en la carpeta de entrada.")
        elif cfg.aerotools_termica and not cfg.aerotools_rgb:
            if self.gen_struct_folder_obj.check_input_folder_and_iterate(cfg.aerotools_input_folder,["TERMICA"], cfg.aerotools_max_error,
                                                                         lim_max_270, lim_min_270,
                                                                         lim_max_90, lim_min_90, progress_callback, progress_bar) is False:
                progress_callback.emit("\nNo se puede iniciar el proceso: El directorio TERMICA no se encuentra en la carpeta de entrada.\n")
                self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: El directorio TERMICA no se encuentra en la carpeta de entrada.")
        elif cfg.aerotools_rgb and cfg.aerotools_termica:
            if self.gen_struct_folder_obj.check_input_folder_and_iterate(cfg.aerotools_input_folder,["TERMICA", "RGB"], cfg.aerotools_max_error,
                                                                         lim_max_270, lim_min_270,
                                                                         lim_max_90, lim_min_90, progress_callback, progress_bar) is False:
                progress_callback.emit("\nNo se puede iniciar el proceso: Los directorios RGB y TERMICA no se encuentran en la carpeta de entrada.\n")
                self.organizer_logger_obj.logger.info("No se puede iniciar el proceso: Los directorios RGB y TERMICA no se encuentran en la carpeta de entrada.")
        
        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time

    def rename_images(self, cfg: RenameImagesConfig, progress_callback, progress_bar, progress_summarize):
        """Función que renombra las imágenes que se encuentran en la carpeta elegida

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().rename_images`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """

        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: Renombrar imágenes")
        self.organizer_logger_obj.logger.info("###################################################################")

        processing_time = self.utils_obj.logging_time()

        self.gen_struct_folder_obj.reset_variables(main_process=True, gen_thumbnails=True, progress_callback=progress_callback)
        self.new_log_gui.enable_process()
        self.gen_struct_folder_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.output_folder)

        self.gen_struct_folder_obj.iterate_folders_and_rename(cfg.output_folder, progress_callback, progress_bar)
        
        summarize_dict = self.gen_struct_folder_obj.get_summarize()
        self.show_summarize(summarize_dict, progress_summarize)
        
        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time

    def do_extraction(self, cfg: ExtractionConfig, progress_callback, progress_bar, progress_summarize):
        """Función que lleva a cabo la pre-extracción, de modo que se recolocan los archivos TMC en sus carpetas de vuelo y posteriormente
        se procede a la extracción de las imágenes a partir del archivo TMC

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().extraction`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """

        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: Extracción")
        self.organizer_logger_obj.logger.info("###################################################################")

        processing_time = self.utils_obj.logging_time()

        ruta_thermoviewer = self.config_obj.ruta_thermoviewer.replace("\\","/")
        # ruta_thermoviewer = r"C:/Program Files (x86)/ThermoViewer/ThermoViewer.exe"
        # if not os.path.exists(ruta_thermoviewer):
        #     ruta_thermoviewer = r"C:/Windows/ThermoViewer/ThermoViewer.exe"

        if not os.path.exists(ruta_thermoviewer):
            progress_callback.emit('\nNo se ha podido iniciar el proceso: NO SE HA ENCONTRADO INSTALACION DE PROGRAMA THERMOVIEWER - COMPROBAR URL {0} DEL ARCHIVO "ThermoViewer.exe"\n'.format(ruta_thermoviewer))
        else:
            if cfg.pre_extraction:
                self.organizer_logger_obj.logger.info("-------------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("Generación de la estructura de carpetas.")
                self.organizer_logger_obj.logger.info("-------------------------------------------------------------------")

                progress_callback.emit("\n-------------------------------------------------------------------\n")
                progress_callback.emit("\nOrganización de las imágenes en cada carpeta de vuelo.\n")
                progress_callback.emit("\n-------------------------------------------------------------------\n")

                self.gen_struct_folder_obj.gen_folder_struct(cfg.estad, "", cfg.output_folder,
                                                        False, 0.0, 0, 0, progress_callback, progress_bar)

                self.organizer_logger_obj.logger.info("-------------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: Pre-extracción. Ordenar los archivos TMC.")
                self.organizer_logger_obj.logger.info("-------------------------------------------------------------------")

                progress_callback.emit("\n-------------------------------------------------------------------\n")
                progress_callback.emit("\nProceso de Pre-Extracción. Ordenar los archivos TMC.\n")
                progress_callback.emit("\n-------------------------------------------------------------------\n")

                # Esto lo hago para detectar la carpeta "termica" sin importar si está con mayúsculas o con minúsculas, pues parece que le daba
                # problemas a los de AEROTOOLS.
                name_folder_termica = ""
                for it in os.scandir(os.path.join(cfg.folder)):
                    if it.is_dir():
                        if (os.path.basename(it).lower() == "termica"):
                            name_folder_termica = os.path.basename(it)

                self.extraction_obj.ordenar_TMCs(os.path.join(cfg.folder, name_folder_termica), cfg.estad, ruta_thermoviewer, cfg.output_folder, progress_callback, progress_bar)

                self.organizer_logger_obj.logger.info("Pre-extracción finalizada")
                progress_callback.emit("\nPre-Extracción finalizada.\n")

            if cfg.extraction:
                self.extraction_obj.reset_variables(main_process=False, progress_callback=progress_callback)
                self.extraction_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.output_folder, tmc=True)
                self.new_log_gui.enable_process()

                self.organizer_logger_obj.logger.info("-------------------------------------------------------------------")
                self.organizer_logger_obj.logger.info("SUBPROCESO: Extracción de las imágenes a partir de los archivos TMC.")
                self.organizer_logger_obj.logger.info("-------------------------------------------------------------------")

                progress_callback.emit("\n-------------------------------------------------------------------\n")
                progress_callback.emit("\nSUBPROCESO: Extracción de las imágenes a partir de los archivos TMC.\n")
                progress_callback.emit("\n-------------------------------------------------------------------\n")

                self.extraction_obj.iterate_folders(ruta_thermoviewer, cfg.output_folder, cfg.auto, cfg.temp_max, cfg.temp_min, progress_callback, progress_bar)

                self.organizer_logger_obj.logger.info("Extracción de todos los archivos TMC finalizada.")
                progress_callback.emit("\nExtracción de todos los archivos TMC finalizada.\n")

        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time
    
    def do_rgb_aerotools_processing(self, cfg: RgbAerotoolsConfig, progress_callback = None, progress_bar=None, progress_summarize=None):
        """Función que lleva a cabo el procesamiento de las imágenes RGB de AEROTOOLS.

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().rgb_aerotools`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: Procesado de las imágenes RGB de AEROTOOLS.")
        self.organizer_logger_obj.logger.info("###################################################################")
        # Esto lo hago para detectar la carpeta "rgb" sin importar si está con mayúsculas o con minúsculas, pues parece que le daba
        # problemas a los de AEROTOOLS.

        processing_time = self.utils_obj.logging_time()

        name_folder_rgb = ""
        for it in os.scandir(os.path.join(cfg.input_folder)):
            if it.is_dir():
                if (os.path.basename(it).lower() == "rgb"):
                    name_folder_rgb = os.path.basename(it)

        if cfg.organize_images:
            self.utils_obj.prepare_output_folder(cfg.output_folder, ["RGB"])
            # En la siguiente función movemos las imágenes RGB a la carpeta de salida, pero aún sin dividirlas por PB y V.
            self.rgb_processing_obj.iterate_folders(os.path.join(cfg.input_folder, name_folder_rgb), cfg.output_folder,
            cfg.compress_rgb, cfg.compress_level,
            progress_callback, cfg.rename_images, progress_bar)

            progress_callback.emit("\nOrganización de las imágenes en cada carpeta de vuelo.\n")
            self.organizer_logger_obj.logger.info("Start organizing images")

        if not self.rgb_processing_obj.stop and cfg.organize_images:
            self.gen_struct_folder_obj.reset_variables(main_process=False, progress_callback=progress_callback)
            self.gen_struct_folder_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(os.path.join(cfg.output_folder,"RGB"))
            self.new_log_gui.enable_process()

            # TODO: Si entramos a gen_folder_struct y le damos a parar el proceso, va a continuar, ya que el objeto es diferente de donde está el self.stop
            # En el resto de funciones, sí que son del objeto que tiene el self.stop (rgb_processing_obj)
            self.gen_struct_folder_obj.gen_folder_struct(cfg.estad, cfg.output_folder, cfg.output_folder,
                                                        True, cfg.seconds_range_image_estad, cfg.mismatch_hours, cfg.mismatch_minutes, progress_callback, progress_bar)

        # A partir de aquí gestionamos los logs
        if not self.rgb_processing_obj.stop and cfg.geotagging:
            self.rgb_processing_obj.reset_variables(main_process=False, progress_callback=progress_callback)
            self.rgb_processing_obj.error_flights_for_postprocessing.clear()  # Si lo geoetiquetamos automáticamente, borramos todos los errores encontrados en algún proceso anterior, por si posteriormente tratamos los errores igualmente en modo manual
            self.rgb_processing_obj.total_images_number = len(self.utils_obj.get_logs_from_dir(cfg.logs_folder))
            self.new_log_gui.enable_process()

            self.rgb_processing_obj.rename_and_move_logs(cfg.logs_folder,cfg.estad,cfg.output_folder, cfg.seconds_range_log_estad, progress_callback, progress_bar)

            self.rgb_processing_obj.reset_variables(main_process=False, progress_callback=progress_callback)
            self.rgb_processing_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(os.path.join(cfg.output_folder,"RGB"))
            self.new_log_gui.enable_process()

            self.rgb_processing_obj.geo_etiquetar(os.path.join(cfg.output_folder,"RGB"), cfg.logs_folder, progress_callback, progress_bar)

            if len(self.rgb_processing_obj.error_flights_for_postprocessing) > 0:
                self.organizer_logger_obj.logger.debug(self.rgb_processing_obj.error_flights_for_postprocessing)
                for clave, valor in self.rgb_processing_obj.error_flights_for_postprocessing.items():
                    print(clave, '->', valor)
                    pb_v = valor.split("_")

                    pb = int(pb_v[0].removeprefix("PB"))
                    v = int(pb_v[1].removeprefix("V"))
                    images_quantity = int(pb_v[2])
                    cam_msgs_quantity = int(pb_v[3])
                    self.organizer_logger_obj.logger.debug("*****************************************************************************")
                    self.organizer_logger_obj.logger.debug("El pb es el {0}".format(pb))
                    self.organizer_logger_obj.logger.debug("El vuelo es el {0}".format(v))
                    self.organizer_logger_obj.logger.debug("El número de imágenes es {0}".format(images_quantity))
                    self.organizer_logger_obj.logger.debug("El número de CAM messages es {0}".format(cam_msgs_quantity))

                    if images_quantity > 0 and cam_msgs_quantity > 0:
                        self.rgb_processing_obj.post_processing_rgb_errors(os.path.join(cfg.output_folder,"RGB"), cfg.logs_folder,
                                                                       cfg.estad, pb, v, images_quantity, cam_msgs_quantity, progress_callback, progress_bar)
                    else:
                        if images_quantity == 0:
                            progress_callback.emit("\nWARNING: El número de imágenes es 0 en el PB {0} y vuelo {1}\n".format(pb, v))
                            # self.new_log_gui.te_log.appendPlainText("\nWARNING: El número de imágenes es 0 en el PB {0} y vuelo {1}\n".format(pb, v))
                        if cam_msgs_quantity == 0:
                            progress_callback.emit("\nWARNING: El número de CAM messages es 0 en el PB {0} y vuelo {1}\n".format(pb, v))
                            # self.new_log_gui.te_log.appendPlainText("\nWARNING: El número de CAM messages es 0 en el PB {0} y vuelo {1}\n".format(pb, v))
      
        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time

    def do_manual_geotagging(self, cfg: ManualGeotaggingConfig, progress_callback = None, progress_bar=None, progress_summarize=None):
        """Función que lleva a cabo el geoetiquetado manual de las imágenes RGB de AEROTOOLS.
        Se usa cuando en el geoetiquetado automático ha quedado alguna carpeta PBX_VX sin geoetiquetar por cualquier razón.
        A partir de la GUI se elige la carpeta PBX_VX, el log específico, la carpeta de logs y el estadillo para poder geoetiquetar todas las imágenes,
        incluso cuando el número de CAM messages y de imágenes no coinciden.

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().manual_geotagging`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        processing_time = self.utils_obj.logging_time()

        self.rgb_processing_obj.error_flights_for_postprocessing.clear()  # Si lo geoetiquetamos manualmente, borramos todos los errores encontrados en algún proceso anterior, por si posteriormente tratamos los errores igualmente en modo auto
        self.rgb_processing_obj.geo_etiquetar(cfg.select_images_folder, cfg.rgb_aerotools_logs_folder, progress_callback, progress_bar, cfg.select_log, True)

        if len(self.rgb_processing_obj.error_flights_for_postprocessing) > 0:
            self.organizer_logger_obj.logger.debug(self.rgb_processing_obj.error_flights_for_postprocessing)
            for clave, valor in self.rgb_processing_obj.error_flights_for_postprocessing.items():
                print(clave, '->', valor)
                pb_v = valor.split("_")

                pb = int(pb_v[0].removeprefix("PB"))
                v = int(pb_v[1].removeprefix("V"))
                images_quantity = int(pb_v[2])
                cam_msgs_quantity = int(pb_v[3])

                self.organizer_logger_obj.logger.debug("*****************************************************************************")
                self.organizer_logger_obj.logger.debug("El pb es el {0}".format(pb))
                self.organizer_logger_obj.logger.debug("El vuelo es el {0}".format(v))
                self.organizer_logger_obj.logger.debug("El número de imágenes es {0}".format(images_quantity))
                self.organizer_logger_obj.logger.debug("El número de CAM messages es {0}".format(cam_msgs_quantity))

                if images_quantity > 0 and cam_msgs_quantity > 0:
                    pbx_folder = "PB" + str(pb)
                    pbx_vx_folder_name = pbx_folder + "_V" + str(v)
                    # Como la carpeta de entrada para el etiquetado manual es la propia de PBX_VX, en la siguiente línea me quedo con la carpeta *\RGB, para poder usarla
                    # dentro de la función post_processing_rgb_errors.
                    base_input_folder = cfg.select_images_folder.removesuffix(os.path.join(pbx_folder, pbx_vx_folder_name).replace("\\","/"))
                    self.rgb_processing_obj.post_processing_rgb_errors(base_input_folder, cfg.logs_folder,
                                                                    cfg.estad, pb, v, images_quantity, cam_msgs_quantity, progress_callback, progress_bar, cfg.select_log, True)
                else:
                    if images_quantity == 0:
                        progress_callback.emit("\nWARNING: El número de imágenes es 0 en el PB {0} y vuelo {1}\n".format(pb, v))
                        # self.new_log_gui.te_log.appendPlainText("\nWARNING: El número de imágenes es 0 en el PB {0} y vuelo {1}\n".format(pb, v))
                    if cam_msgs_quantity == 0:
                        progress_callback.emit("\nWARNING: El número de CAM messages es 0 en el PB {0} y vuelo {1}\n".format(pb, v))
                        # self.new_log_gui.te_log.appendPlainText("\nWARNING: El número de CAM messages es 0 en el PB {0} y vuelo {1}\n".format(pb, v))

        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time

    def _resolve_dron_selector(self, selector, termica_folder, progress_callback):
        """Comprueba que el DJI Thermal SDK está disponible antes de convertir a TIF, y
        deja constancia en el log de qué dron trae la muestra térmica.

        Hasta la 3.3.2 esta función ELEGÍA carpeta de binarios: autodetectaba el modelo
        por EXIF y lo mapeaba a programas_externos/M2EA o /M4T. Esas dos carpetas eran
        byte a byte idénticas (mismo md5 en los 19 ficheros), así que la elección no
        elegía nada: siempre acababa lanzando el mismo binario. Ahora hay una sola
        carpeta (programas_externos/DJI) y una sola ruta posible — una variable menos
        que descartar cuando el conversor falla en la máquina de un usuario.

        El modelo se sigue leyendo y logueando porque es dato de diagnóstico útil (el
        SDK resuelve el modelo por su cuenta desde el propio R-JPEG), pero YA NO decide
        nada ni aborta la corrida: un dron fuera de la lista conocida se avisa y se
        intenta igual. `selector` (combo Qt / webui, valores históricos M2EA/M4T) se
        acepta por compatibilidad con configuraciones guardadas y se ignora."""
        detected_model = None
        sample = None

        # Modelos conocidos, solo para el mensaje del log. Se compara NORMALIZADO
        # (mayúsculas y sin separadores) porque el mismo dron escribe el modelo de
        # formas distintas según firmware: 'M4T', 'Matrice 4T',
        # 'MAVIC2-ENTERPRISE-ADVANCED', 'Mavic2 Enterprise Advanced'…
        modelos_conocidos = {
            "M4T", "MATRICE4T", "DJIMATRICE4T",
            "MAVIC2ENTERPRISEADVANCED", "M2EA", "DJIMAVIC2ENTERPRISEADVANCED",
        }

        def _norm(s):
            return re.sub(r"[^A-Z0-9]", "", (s or "").upper())

        for root, _dirs, files in os.walk(termica_folder):
            for name in files:
                if os.path.splitext(name)[1].lower() in EXTS_IMAGEN:
                    sample = os.path.join(root, name)
                    break
            if sample:
                break
        if not sample:
            progress_callback.emit("AVISO: no se encontró imagen térmica de muestra para identificar el dron.\n")
        else:
            model = self.meta_location_obj.exif_management_obj.get_model(sample, progress_callback)
            model = (model or "").strip("\x00").strip()
            detected_model = model
            if model and _norm(model) in modelos_conocidos:
                progress_callback.emit(f"Dron detectado por EXIF: {model}.\n")
            elif model:
                progress_callback.emit(
                    f"AVISO: dron '{model}' fuera de los modelos probados (M2EA/M4T). "
                    "Se intenta la conversión igualmente: el SDK térmico es el mismo para todos.\n")
            else:
                progress_callback.emit(
                    f"AVISO: la imagen de muestra no trae modelo en el EXIF ({sample}).\n")

        # GUARD: el SDK DEBE traer el binario del SO (dji_irp.exe en Windows, libdirp.so
        # en Linux). Si no, abortar fuerte upfront con UN error claro, en vez de N fallos
        # por imagen aguas abajo. Ahora solo hay UN sitio donde puede faltar.
        if not config.has_dji_binaries():
            bin_name = config.dji_bin_name()
            msg = (f"Falta el conversor térmico DJI: no se encuentra {bin_name} en "
                   f"{config.dji_sdk_dir()}. La instalación está incompleta — "
                   "reinstala ATOM Organizer.")
            progress_callback.emit("ERROR: " + msg + "\n")
            raise ValueError(msg)

        return detected_model

    def do_convert_to_tif(self, cfg: ConvertToTifConfig, progress_callback = None, progress_bar=None, progress_summarize=None):
        """
        Función que convierte las imágenes JPG térmicas a TIFF para su uso en la aplicación de AEROTOOLS.

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().convert_to_tif`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: CONVERTIR IMÁGENES DJI A TIFF")
        self.organizer_logger_obj.logger.info("###################################################################")

        processing_time = self.utils_obj.logging_time()

        self.split_images_obj.reset_variables()
        self.split_images_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.input_folder, exclude_patterns=[utils.ROTATED_JPG_SUFFIX])  # idem: las `_ROT` heredadas de 3.4.5 no se reconvierten
        self.new_log_gui.enable_process()

        exiftool_exe = resource_path("programas_externos", "exiftool.exe")
        self._resolve_dron_selector(cfg.dron_selector, cfg.input_folder, progress_callback)
        dji_utility = config.dji_utility_path()

        self.split_images_obj.iterate_folders_for_DJI(cfg.input_folder, exiftool_exe, dji_utility,
                                                      progress_callback, progress_bar, cfg.emissivity,
                                                      cfg.humidity, cfg.temp_auto,
                                                      cfg.up_temperature, cfg.low_temperature,
                                                      cfg.rotate_90, cfg.rotate_minus_90,
                                                      cfg.rotate_auto, cfg.solo_seleccion_atom,
                                                      cfg.create_gray_scale_images)

        # TODO: En el caso de que la carpeta elegida no sea TERMICA, o no estén las carpetas PBX, dará error. Habrá que añadir un parámetro para que en este caso busque en el árbol de directorios o algo similar.
        self.split_images_obj.checking_convert_to_tif(cfg.input_folder, progress_callback, progress_summarize)

        # Giro in-place del JPG térmico junto a su TIFF (ver la fase equivalente de
        # split_images): después de convertir y verificar, porque girarlo destruye el
        # payload radiométrico que necesita el conversor.
        self.split_images_obj.rotate_thermal_jpgs_in_place(
            cfg.input_folder, progress_callback, progress_bar,
            cfg.rotate_90, cfg.rotate_minus_90, cfg.rotate_auto)

        summarize_dict = self.split_images_obj.get_summarize()
        self.show_summarize(summarize_dict, progress_summarize)

        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)
        return processing_time

    def do_rgb_cropping(self, cfg: RgbCroppingConfig, progress_callback = None, progress_bar=None, progress_summarize=None):
        """
        Función que itera la carpeta seleccionada para generar el recorte de las imágenes que se encuentran en su árbol de carpetas.

        Arguments:
        ---------
        - cfg - Configuración capturada en el hilo GUI (`_capture_run_config().rgb_cropping`) con todos los valores necesarios.
        - progress_callback - Callback (los signals) que envían, mediante un emit(), información de texto desde el hilo correspondiente.
        - progress_bar - Callback (los signals) que envían, mediante un emit(), el porcentaje actual a la barra de progreso desde el hilo correspondiente.
        """
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: RECORTE DE IMÁGENES RGB.")
        self.organizer_logger_obj.logger.info("###################################################################")

        processing_time = self.utils_obj.logging_time()

        self.rgb_cropping_obj.reset_variables()
        self.rgb_cropping_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.output_folder)
        self.new_log_gui.enable_process()

        self.rgb_cropping_obj.iterate_folders_for_rgb_cropping(cfg.output_folder, progress_callback, progress_bar, self.config_obj.percentage_by_models, cfg.choose_mode_auto
                                                               ,cfg.crop_percentage)
        # self.rgb_cropping_obj.crop_centered_image(cfg.output_folder,400,400)
        processing_time = self.utils_obj.logging_time(progress_callback, processing_time, False)

        self.rgb_cropping_obj.checking_results_rgb_cropping(cfg.output_folder, progress_callback, progress_summarize)

        summarize_dict = self.rgb_cropping_obj.get_summarize()
        self.show_summarize(summarize_dict, progress_summarize)
        return processing_time
    
    def do_tif_rotating(self, cfg: TifRotatingConfig, progress_callback = None, progress_bar=None, progress_summarize=None):
        self.organizer_logger_obj.logger.info("###################################################################")
        self.organizer_logger_obj.logger.info("PROCESO: GIRAR IMÁGENES TIFF.")
        self.organizer_logger_obj.logger.info("###################################################################")
        rotate_angle = 0
        processing_time = self.utils_obj.logging_time()

        self.gen_struct_folder_obj.reset_variables()
        self.gen_struct_folder_obj.total_images_number = self.utils_obj.contar_imagenes_or_tmc(cfg.input_folder_2)
        self.new_log_gui.enable_process()

        if cfg.rotate_90:
           rotate_angle = 90
        elif cfg.rotate_minus_90:
           rotate_angle = -90
        elif cfg.rotate_180:
           rotate_angle = 180

        self.gen_struct_folder_obj.iterate_folder_for_rotating_tiff(cfg.input_folder_2, rotate_angle, progress_callback, progress_bar, progress_summarize)
        
        summarize_dict = self.gen_struct_folder_obj.get_summarize()
        self.show_summarize(summarize_dict, progress_summarize)
        return processing_time

    def show_summarize(self, summarize_dict: dict, progress_summarize=None):
        try:
            for clave, valor in summarize_dict.items():
                if progress_summarize is not None:
                    progress_summarize.emit(f"{clave}: {str(valor)}")
                else:
                    self.new_log_gui.te_summarize_process.appendPlainText(f"{clave}: {str(valor)}")
        except Exception as e:
            self.organizer_logger_obj.logger.debug("Error en show_summarize.")
