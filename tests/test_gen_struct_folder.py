import os
from utils import OrganizerLogger as ol
from pipeline import GenStructFolder


class FakeSignal:
    def emit(self, *args, **kwargs):
        pass


def _make_gsf(tmp_path):
    logger = ol("test_gen_struct", log_dir=str(tmp_path / "Logs"), create_file_handler=False)
    return GenStructFolder(logger)


def test_moverListaImagenes_por_defecto_sobrescribe_en_colision(tmp_path):
    # Task 2 (modo_destino): `moverListaImagenes` -> `_mover_pares` ahora pasa
    # `modo=self.modo_destino`, y el default de `GenStructFolder` es
    # `MODO_SOBRESCRIBIR` (utils.MODO_SOBRESCRIBIR), no el `unico` implícito
    # de antes. Ese `unico` era precisamente el que causaba que reorganizar
    # PRUEBA dejara 5.049 objetos a partir de 2.516 (ver utils.SplitImagesConfig
    # / utils.GenStructFolderConfig). La segunda pasada ahora manda y pisa lo
    # que hubiera en la misma ruta; para conservar ambas hace falta pedir
    # `modo_destino=utils.MODO_UNICO` explícitamente (ver test_utils.py de la
    # Task 1 para la cobertura de esa rama).
    origen1 = tmp_path / "origen1"
    origen2 = tmp_path / "origen2"
    destino = tmp_path / "destino"
    origen1.mkdir()
    origen2.mkdir()
    destino.mkdir()
    (origen1 / "img.jpg").write_bytes(b"CONTENIDO_A")
    (origen2 / "img.jpg").write_bytes(b"CONTENIDO_B")

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 2
    cb = FakeSignal()

    gsf.moverListaImagenes(str(origen1), str(destino), ["img.jpg"], cb, cb)
    gsf.moverListaImagenes(str(origen2), str(destino), ["img.jpg"], cb, cb)

    destinos = sorted(os.listdir(destino))
    assert destinos == ["img.jpg"], f"Con modo_destino sobrescribir se esperaba 1 solo archivo, hay {destinos}"
    assert (destino / "img.jpg").read_bytes() == b"CONTENIDO_B", \
        "La segunda pasada debe mandar (sobrescribir), y ha ganado la primera"


def test_rename_images_no_sobreescribe_en_colision(tmp_path):
    carpeta = tmp_path / "vuelo"
    carpeta.mkdir()
    (carpeta / "DJI_0001.jpg").write_bytes(b"FOTO_1")
    (carpeta / "DJI_0002.jpg").write_bytes(b"FOTO_2")

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 2
    gsf.utils_obj.get_images_from_dir = lambda folder, *a, **kw: ["DJI_0001.jpg", "DJI_0002.jpg"]
    # Forzamos que ambas fotos calculen el MISMO nuevo nombre (mismo timestamp EXIF) para provocar la colisión.
    gsf.exif_management_obj.fechaHora_DJI = lambda path, progress_callback: "20240101_120000"
    cb = FakeSignal()

    gsf.rename_images(str(carpeta), cb, cb)

    resultado = sorted(os.listdir(carpeta))
    assert len(resultado) == 2, f"Se esperaban 2 archivos tras renombrar, hay {resultado} (colisión perdió uno)"


def test_gen_thumbnails_and_rotate_manual_no_reprocesa_miniaturas(tmp_path, make_dji_jpeg):
    root = tmp_path / "PLANTA"
    vuelo = root / "PB1_V1"
    vuelo.mkdir(parents=True)
    (root / "TERMICA").mkdir()
    # Una imagen de verdad fuera de MINIATURAS: sin ella no se rota nada y el test
    # se cumpliría solo, sin llegar a comprobar que MINIATURAS queda fuera.
    make_dji_jpeg(str(vuelo / "DJI_0001_D.JPG"))
    miniaturas_existentes = root / "MINIATURAS" / "PB1_V1_miniaturas"
    miniaturas_existentes.mkdir(parents=True)
    (miniaturas_existentes / "PB1_V1_0001.JPG").write_bytes(b"YA_GENERADA")

    gsf = _make_gsf(tmp_path)
    gsf.root_folder = str(root)
    gsf.miniaturas_root_folder = str(root / "MINIATURAS")
    gsf.total_images_number = 1

    # Se espía `_rotate_images_batch` y no `rotate_and_save`: desde que la rotación RGB
    # va en ProcessPool, `rotate_and_save` se ejecuta en un proceso hijo y este espía
    # nunca se llamaría — el test pasaría sin comprobar nada.
    llamadas = []
    gsf._rotate_images_batch = lambda images, input_folder, *args, **kwargs: llamadas.append(input_folder)

    cb = FakeSignal()
    gsf.gen_thumbnails_and_rotate_manual(str(root), rgb_processing=True, rotation_value_90=True, progress_callback=cb, progress_bar=cb)

    assert llamadas, "No se rotó nada: el test no está probando la exclusión de MINIATURAS."
    reprocesadas = [c for c in llamadas if "MINIATURAS" in c]
    assert reprocesadas == [], f"Se ha reprocesado contenido dentro de MINIATURAS: {reprocesadas}"


def _estadillo(path, filas):
    """Estadillo mínimo con las columnas que mira `get_nombres_columnas` (ES)."""
    lineas = ["PB;Vuelo;Fecha;Hora_de_inicio;Hora_final"]
    lineas += [";".join(str(c) for c in fila) for fila in filas]
    path.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return str(path)


def _destino_plano(root, imagenes):
    """Destino tal y como lo deja `split`: RGB/ y TERMICA/ planas."""
    for sub in ("RGB", "TERMICA"):
        (root / sub).mkdir(parents=True)
        for nombre in imagenes:
            (root / sub / nombre).write_bytes(b"X")


def _con_timestamps(gsf, horas):
    """Fija el EXIF de cada imagen por nombre: {'DJI_0001.jpg': '10:00:30'}."""
    import datetime as _dt

    def _fake(ruta):
        hora = horas[os.path.basename(ruta)]
        return _dt.datetime.strptime("2026:03:17_" + hora, "%Y:%m:%d_%H:%M:%S")

    gsf.exif_management_obj.get_timestamp_from_image = _fake


def test_gen_folder_struct_reparte_por_ventana_horaria(tmp_path):
    """Cada imagen acaba en el vuelo cuya franja la contiene, y la que no cae en
    ninguna se queda en la raíz (de ahí se la lleva luego `checking_results_*` a
    SIN_ORDENAR). Es el contrato que tenía el bucle «por vuelo» y que debe seguir
    cumpliendo el bucle «por imagen»."""
    root = tmp_path / "destino"
    _destino_plano(root, ["a.jpg", "b.jpg", "fuera.jpg"])
    estadillo = _estadillo(tmp_path / "est.csv",
                           [("1", "1", "2026:03:17", "10:00:00", "10:05:00"),
                            ("2", "1", "2026:03:17", "11:00:00", "11:05:00")])

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 6
    _con_timestamps(gsf, {"a.jpg": "10:02:00", "b.jpg": "11:03:00", "fuera.jpg": "23:00:00"})
    cb = FakeSignal()

    gsf.gen_folder_struct(estadillo, str(root), str(root), True, 0, 0, 0, cb, cb)

    for sub in ("RGB", "TERMICA"):
        assert os.listdir(root / sub / "PB1" / "PB1_V1") == ["a.jpg"]
        assert os.listdir(root / sub / "PB2" / "PB2_V1") == ["b.jpg"]
        sueltas = [f for f in os.listdir(root / sub) if f.endswith(".jpg")]
        assert sueltas == ["fuera.jpg"], f"{sub}: quedaron sueltas {sueltas}"


def test_gen_folder_struct_solape_lo_gana_el_primer_vuelo_del_estadillo(tmp_path):
    """Con dos vuelos que solapan, la imagen es del que aparece antes en el CSV.
    Antes lo garantizaba el orden secuencial del bucle (el primero se llevaba la
    imagen y el segundo ya no la veía); ahora lo garantiza el orden en que se
    recorren las ventanas. Si eso se rompe, la foto cambia de carpeta sin avisar."""
    root = tmp_path / "destino"
    _destino_plano(root, ["solapada.jpg"])
    estadillo = _estadillo(tmp_path / "est.csv",
                           [("9", "2", "2026:03:17", "10:00:00", "10:30:00"),
                            ("3", "1", "2026:03:17", "10:10:00", "10:40:00")])

    gsf = _make_gsf(tmp_path)
    gsf.total_images_number = 2
    _con_timestamps(gsf, {"solapada.jpg": "10:20:00"})
    cb = FakeSignal()

    gsf.gen_folder_struct(estadillo, str(root), str(root), True, 0, 0, 0, cb, cb)

    assert os.listdir(root / "RGB" / "PB9" / "PB9_V2") == ["solapada.jpg"]
    assert os.listdir(root / "RGB" / "PB3" / "PB3_V1") == []
