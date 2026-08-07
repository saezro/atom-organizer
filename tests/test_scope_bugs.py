import glob
import os
import subprocess

from pipeline import Extraction, GenStructFolder


class _NoopCallback:
    def emit(self, *args, **kwargs):
        pass


def test_ordenar_tmcs_sin_fotos_preextraidas_no_crashea(organizer_logger_stub, tmp_path, monkeypatch):
    """
    Extraction.ordenar_TMCs (contiene el bug de scope descrito en Task 10 para
    `hora_foto_tmc`, ya que Extraction.extraccion en esta versión no llega a usar
    esa variable). Si el glob de fotos pre-extraídas devuelve 0 archivos, la variable
    `hora_foto_tmc` (asignada dentro de `for foto in glob.glob(ruta+'/*.jpg')`) queda
    sin definir, y se usa más abajo en la comprobación contra el estadillo -> NameError.
    """
    extractor = Extraction(organizer_logger_stub)
    extractor.current_image_number = 0
    extractor.total_images_number = 1
    extractor.stop = False

    carpeta_raiz = tmp_path / "raiz"
    ruta_vuelo = carpeta_raiz / "PB1_V1"
    output_folder = tmp_path / "salida"
    os.makedirs(ruta_vuelo, exist_ok=True)
    os.makedirs(output_folder, exist_ok=True)

    estadillo_path = tmp_path / "estadillo.csv"
    estadillo_path.write_text(
        "Fecha;Hora_de_inicio;Hora_final;PB;Vuelo\n"
        "2024:06:01;10:00:00;10:10:00;1;1\n",
        encoding="utf-8",
    )

    # Forzamos que check_number_of_videos devuelva un tmc, pero que no haya ningún
    # .jpg pre-extraído (glob de fotos pre-extraídas vacío).
    monkeypatch.setattr(
        extractor,
        "check_number_of_videos",
        lambda *a, **k: os.path.join(str(ruta_vuelo), "video.TMC"),
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)

    def fake_glob(pattern):
        if pattern.endswith("\\*"):
            # Simula el glob.glob(carpeta_raiz + "\*") que devuelve las rutas de vuelo.
            return [str(ruta_vuelo)]
        # Cualquier otro patrón (glob de *.jpg) no devuelve ninguna foto pre-extraída.
        return []

    monkeypatch.setattr(glob, "glob", fake_glob)

    # No debe lanzar NameError por hora_foto_tmc indefinida.
    extractor.ordenar_TMCs(
        str(carpeta_raiz),
        str(estadillo_path),
        "ruta_thermoviewer_falsa",
        str(output_folder),
        _NoopCallback(),
        _NoopCallback(),
    )


def test_rotate_tiff_image_open_falla_no_crashea(organizer_logger_stub, tmp_path):
    """Si Image.open falla (archivo no encontrado), rotate_tiff_image no debe lanzar UnboundLocalError ni AttributeError."""
    gsf = GenStructFolder(organizer_logger_stub)
    gsf.current_image_number = 0
    gsf.total_images_number = 1

    gsf.rotate_tiff_image(
        str(tmp_path), "no_existe.tiff", 2, _NoopCallback(), _NoopCallback(), _NoopCallback()
    )
    assert gsf.error_gen_struct_folder == 1
