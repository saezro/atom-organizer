"""Troceado del pipeline en etapas (`--etapa split|struct|post|todo`).

Repartir la corrida entre varias tareas de Cloud Run exige poder ejecutar solo un
trozo del pipeline: `split` y `post` se abren en N tareas paralelas y `struct`
(que lee el estadillo y mueve el destino entero) va en una sola en medio.

Lo que estos tests sujetan:

1. Que `--etapa todo` — el default, y lo que usan la app de escritorio y la GUI
   Qt — siga llamando a las MISMAS fases que antes de que existieran las etapas.
   El troceado no puede cambiar el producto que Daniel prueba en Windows.
2. Que cada etapa ejecute lo suyo y NADA más. Si `post` arrastrase la separación,
   las 8 tareas volverían a copiar el vuelo entero 8 veces.
3. Que el checklist de fases que ve el usuario cuente sobre las fases de SU etapa
   (si no, una tarea de `post` diría «fase 1/6» corriendo la cuarta).
"""
import os
import sys
import types

import pytest

from atom_core.organize import _active_split_phases
from atom_core.phases import PipelinePhasesMixin
from utils import SplitImagesConfig


class _SignalFalsa:
    def __init__(self):
        self.mensajes = []

    def emit(self, valor):
        self.mensajes.append(valor)


class _ObjetoQueApunta:
    """Sustituto de un objeto de negocio del pipeline: acepta cualquier llamada y
    la apunta, para poder afirmar QUÉ fases se ejecutaron sin tocar imágenes."""

    def __init__(self, registro, nombre, detalle=None):
        self._registro = registro
        self._nombre = nombre
        # `detalle` guarda además CON QUÉ se llamó: hace falta para afirmar qué
        # carpetas y qué PB le tocaron a cada tarea, no solo qué fases corrieron.
        self._detalle = detalle if detalle is not None else []
        self.total_images_number = 0
        # Numérico y no apuntador: las etapas repartidas le suman los sobrantes
        # («las que no encajan en ningún vuelo») antes de pedir el resumen.
        self.current_image_number = 0
        self.stop = False

    def __getattr__(self, metodo):
        if metodo.startswith("_"):
            raise AttributeError(metodo)

        def _apuntar(*args, **kwargs):
            self._registro.append(f"{self._nombre}.{metodo}")
            self._detalle.append((metodo, args, kwargs))
            if metodo == "get_summarize":
                return {}
            if metodo.startswith("checking_results_gen_struct"):
                return (0, 0)
            return None
        return _apuntar


class _UtilsFalso(_ObjetoQueApunta):
    """`utils_obj` necesita devolver DATOS y no None: los contadores de imágenes
    alimentan sumas y el apuntador genérico las reventaría con un TypeError."""

    filtros_recibidos: list = []
    recursivos_recibidos: list = []

    def contar_imagenes_or_tmc(self, folder, tmc=False, exclude_patterns=None,
                               exclude_folders=None, filtro_nombre=None,
                               recursivo=True):
        self._registro.append("utils_obj.contar_imagenes_or_tmc")
        self.filtros_recibidos.append((folder, filtro_nombre))
        self.recursivos_recibidos.append((folder, recursivo))
        return 0

    imagenes_por_carpeta: dict = {}

    def get_images_from_dir(self, input_folder, exclude_patterns=None):
        self._registro.append("utils_obj.get_images_from_dir")
        return list(self.imagenes_por_carpeta.get(input_folder, []))

    def logging_time(self, *args, **kwargs):
        return 0.0


class _HostDePrueba(PipelinePhasesMixin):
    """Host con todos los objetos de negocio sustituidos por apuntadores."""

    def __init__(self, etapa="todo", shard_index=0, shard_count=1):
        self.llamadas = []
        self.detalle = []
        self.etapa = etapa
        self.shard_index = shard_index
        self.shard_count = shard_count
        for nombre in ("organizer_logger_obj", "split_images_obj",
                       "gen_struct_folder_obj", "meta_location_obj",
                       "rgb_cropping_obj", "new_log_gui", "config_obj"):
            setattr(self, nombre, _ObjetoQueApunta(self.llamadas, nombre, self.detalle))
        self.organizer_logger_obj.logger = _ObjetoQueApunta(self.llamadas, "logger")
        self.config_obj.percentage_by_models = {}
        self.utils_obj = _UtilsFalso(self.llamadas, "utils_obj")

    def show_summarize(self, summarize_dict, progress_summarize=None):
        self.llamadas.append("show_summarize")

    def _resolve_dron_selector(self, selector, termica_folder, progress_callback):
        self.llamadas.append("_resolve_dron_selector")


def _cfg(**overrides):
    base = dict(
        input_folder="/origen", output_folder="/destino",
        end_rgb_extra_files="", end_thermo_files="_T", end_rgb_files="",
        estad="/estadillo.csv", choose_mode_size=False, max_size="0",
        compress_rgb=True, compress_level=40, rename_images=True,
        mismatch_hours=0, mismatch_minutes=0, organize_images=True,
        cropping_rgb=True, cropping_mode_auto=True, crop_percentage="0",
        gen_meta_location=True, gen_thumbnails=True, seconds_range=30.0,
        include_v=True, calculate_proyected_distance=False, flight_height=0.0,
        gen_thumbnails_rotate_90=False, gen_thumbnails_add_to_angle=5.0,
        gen_thumbnails_max_error=80, gen_thumbnails_subs_to_angle=5.0,
        choose_mode_auto=True, gen_thumbnails_rgb=True, gen_thumbnails_termica=True,
        convert_to_tif=True, convert_to_tif_dron_selector="",
        convert_to_tif_emissivity=0.95, convert_to_tif_humidity=70.0,
        convert_to_tif_temp_auto=1, convert_to_tif_up_temperature=0.0,
        convert_to_tif_low_temperature=0.0, convert_to_tiff_rotate_90=False,
        convert_to_tiff_rotate_minus_90=False, convert_to_tiff_rotate_auto=True,
        convert_to_tif_solo_seleccion_atom=False,
        convert_to_tif_create_gray_scale_images=False,
    )
    base.update(overrides)
    return SplitImagesConfig(**base)


def _correr(etapa, monkeypatch, cfg=None, **kwargs):
    host = _HostDePrueba(etapa=etapa, **kwargs)
    # `os.path.isdir` decide qué rutas del destino existen; en un host de prueba
    # no hay disco, así que se afirma que todo existe para que ninguna fase se
    # salte por "la carpeta no está".
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(cfg or _cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
    return host.llamadas


# --- el default no cambia ----------------------------------------------------

def test_etapa_todo_ejecuta_las_cuatro_familias_de_fases(monkeypatch):
    """Es el comportamiento de siempre: es lo que corre la app de escritorio."""
    llamadas = _correr("todo", monkeypatch)
    assert "split_images_obj.iterate_folders" in llamadas          # separación
    assert "gen_struct_folder_obj.gen_folder_struct" in llamadas   # estructura
    assert "rgb_cropping_obj.iterate_folders_for_rgb_cropping" in llamadas
    assert "meta_location_obj.check_input_folder_and_iterate" in llamadas
    assert "gen_struct_folder_obj.check_input_folder_and_iterate" in llamadas
    assert "split_images_obj.iterate_folders_for_DJI" in llamadas  # TIF


def test_el_orden_de_las_fases_no_cambia(monkeypatch):
    """El TIF va después de la rotación y la rotación después de la estructura:
    el conversor necesita el R-JPEG intacto y la rotación necesita los PB ya
    formados. Un reordenado silencioso rompe el vuelo, no los tests."""
    llamadas = _correr("todo", monkeypatch)
    orden = [c for c in llamadas if c in (
        "split_images_obj.iterate_folders",
        "gen_struct_folder_obj.gen_folder_struct",
        "rgb_cropping_obj.iterate_folders_for_rgb_cropping",
        "meta_location_obj.check_input_folder_and_iterate",
        "gen_struct_folder_obj.check_input_folder_and_iterate",
        "split_images_obj.iterate_folders_for_DJI",
    )]
    assert orden == [
        "split_images_obj.iterate_folders",
        "gen_struct_folder_obj.gen_folder_struct",
        "rgb_cropping_obj.iterate_folders_for_rgb_cropping",
        "meta_location_obj.check_input_folder_and_iterate",
        "gen_struct_folder_obj.check_input_folder_and_iterate",
        "split_images_obj.iterate_folders_for_DJI",
    ]


# --- cada etapa hace lo suyo y nada más --------------------------------------

def test_etapa_split_solo_separa(monkeypatch):
    llamadas = _correr("split", monkeypatch)
    assert "split_images_obj.iterate_folders" in llamadas
    assert "gen_struct_folder_obj.gen_folder_struct" not in llamadas
    assert "split_images_obj.iterate_folders_for_DJI" not in llamadas
    assert "rgb_cropping_obj.iterate_folders_for_rgb_cropping" not in llamadas


def test_etapa_struct_solo_estructura(monkeypatch):
    llamadas = _correr("struct", monkeypatch)
    assert "gen_struct_folder_obj.gen_folder_struct" in llamadas
    assert "split_images_obj.iterate_folders" not in llamadas
    assert "split_images_obj.iterate_folders_for_DJI" not in llamadas


def test_etapa_post_no_vuelve_a_separar_ni_a_estructurar(monkeypatch):
    """Es el error que multiplicaría por 8 el trabajo: si `post` arrastrase la
    separación, las 8 tareas copiarían el vuelo entero cada una."""
    llamadas = _correr("post", monkeypatch)
    assert "split_images_obj.iterate_folders" not in llamadas
    assert "gen_struct_folder_obj.gen_folder_struct" not in llamadas
    assert "rgb_cropping_obj.iterate_folders_for_rgb_cropping" in llamadas
    assert "meta_location_obj.check_input_folder_and_iterate" in llamadas
    assert "split_images_obj.iterate_folders_for_DJI" in llamadas


def test_el_selector_de_dron_se_resuelve_tambien_en_etapa_post(monkeypatch):
    """Deduce el modelo buscando una térmica de muestra. Sin él no hay conversión
    posible, y la etapa `post` es justo donde vive el TIF."""
    assert "_resolve_dron_selector" in _correr("post", monkeypatch)


# --- el checklist que ve el usuario cuenta sobre su etapa --------------------

def test_el_plan_de_fases_se_filtra_por_etapa():
    cfg = _cfg()
    assert _active_split_phases(cfg, "todo") == [
        "Separación RGB / térmica", "Estructura de carpetas", "Recorte RGB",
        "Meta y geolocalización", "Rotación", "Convertir a TIF"]
    assert _active_split_phases(cfg, "split") == ["Separación RGB / térmica"]
    assert _active_split_phases(cfg, "struct") == ["Estructura de carpetas"]
    assert _active_split_phases(cfg, "post") == [
        "Recorte RGB", "Meta y geolocalización", "Rotación", "Convertir a TIF"]


def test_el_plan_por_defecto_es_el_de_siempre():
    """`_active_split_phases(cfg)` sin etapa tiene que seguir dando el plan
    completo: lo llama la ruta webview y no sabe de etapas."""
    assert _active_split_phases(_cfg()) == _active_split_phases(_cfg(), "todo")


def test_el_plan_respeta_los_flags_apagados():
    cfg = _cfg(convert_to_tif=False, cropping_rgb=False)
    assert _active_split_phases(cfg, "post") == ["Meta y geolocalización", "Rotación"]


# --- el reparto llega hasta las llamadas del pipeline ------------------------

def _correr_repartido(etapa, monkeypatch, shard_index, shard_count,
                      origen=None, vuelos=()):
    """`origen` es {carpeta: [imágenes]}, el árbol de origen que se simula."""
    origen = origen or {}
    host = _HostDePrueba(etapa=etapa, shard_index=shard_index,
                         shard_count=shard_count)
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    monkeypatch.setattr("atom_core.sharding.carpetas_con_imagenes",
                        lambda _root, _get: sorted(origen))
    monkeypatch.setattr("atom_core.sharding.vuelos_del_destino",
                        lambda _d: list(vuelos))
    monkeypatch.setattr("atom_core.sharding.peso_de_ruta",
                        lambda _d, _rel, _contar: 1)
    host.utils_obj.imagenes_por_carpeta = origen
    host.split_images(_cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
    return host.llamadas, host.detalle


def test_la_separacion_repartida_no_recursa_por_el_arbol(monkeypatch):
    """Con reparto se llama a `split_images` (una carpeta, con SUS imágenes) y NO
    a `iterate_folders`, que bajaría por el árbol y volvería a procesar lo de las
    demás tareas."""
    llamadas, detalle = _correr_repartido(
        "split", monkeypatch, shard_index=0, shard_count=2,
        origen={"/origen/A": [f"a{i}.JPG" for i in range(4)]})

    assert "split_images_obj.iterate_folders" not in llamadas
    procesadas = [kw["solo_imagenes"] for metodo, _args, kw in detalle
                  if metodo == "split_images"]
    assert procesadas == [["a0.JPG", "a2.JPG"]]


def test_las_tareas_de_separacion_cubren_todas_las_imagenes(monkeypatch):
    """El invariante de verdad: entre las N tareas se procesa cada IMAGEN del
    origen una vez y solo una. Se reparte por imagen y no por carpeta porque un
    vuelo real tiene 2.500 fotos en dos carpetas."""
    origen = {"/origen/A": [f"a{i}.JPG" for i in range(11)],
              "/origen/B": [f"b{i}.JPG" for i in range(7)]}
    procesadas = []
    for i in range(3):
        _llamadas, detalle = _correr_repartido(
            "split", monkeypatch, shard_index=i, shard_count=3, origen=origen)
        for metodo, args, kw in detalle:
            if metodo == "split_images":
                procesadas.extend(f"{args[0]}/{img}" for img in kw["solo_imagenes"])

    todas = [f"{c}/{img}" for c, imgs in origen.items() for img in imgs]
    assert sorted(procesadas) == sorted(todas)
    assert len(procesadas) == len(set(procesadas))


def test_el_post_repartido_pasa_sus_vuelos_al_verificador(monkeypatch):
    """El `checking_*` de una tarea NO puede mirar los vuelos de las demás: los
    están escribiendo ahora mismo y verlos a medias daría un falso 'no coinciden'."""
    todos = ["PB1/PB1_V1", "PB1/PB1_V2", "PB2/PB2_V1", "PB2/PB2_V2"]
    vistos = []
    for i in range(2):
        _llamadas, detalle = _correr_repartido(
            "post", monkeypatch, shard_index=i, shard_count=2, vuelos=todos)
        checks = [kw.get("only_pb") for metodo, _args, kw in detalle
                  if metodo == "checking_convert_to_tif"]
        assert checks, "no se verificó la conversión a TIF"
        assert checks[0] is not None, "el verificador miró el destino entero"
        assert len(checks[0]) == 2
        vistos.extend(checks[0])
    # Entre las dos tareas se verifican los cuatro vuelos, cada uno una sola vez.
    assert sorted(vistos) == todos


def test_el_post_repartido_solo_convierte_sus_vuelos(monkeypatch):
    """Y arranca en la carpeta del VUELO, no en la del PB: si arrancara en el PB
    convertiría también los vuelos hermanos, que son de otra tarea."""
    _llamadas, detalle = _correr_repartido(
        "post", monkeypatch, shard_index=0, shard_count=2,
        vuelos=["PB1/PB1_V1", "PB1/PB1_V2", "PB2/PB2_V1", "PB2/PB2_V2"])
    rutas = [args[0] for metodo, args, _kw in detalle
             if metodo == "iterate_folders_for_DJI"]
    assert len(rutas) == 2, "la tarea debería convertir 2 de los 4 vuelos"
    assert all(r.startswith(os.path.join("/destino", "TERMICA", "PB")) for r in rutas)
    assert all(os.path.basename(r).startswith("PB") and "_V" in os.path.basename(r)
               for r in rutas), f"no se arrancó en la carpeta del vuelo: {rutas}"


# --- el total esperado de la estructura, con reparto --------------------------

def _filtro_del_destino(host, cfg):
    """El `filtro_nombre` con el que la fase Estructura contó el total esperado.

    Desde la corrección del conteo (solo TERMICA+RGB, no el destino entero),
    el total se calcula sumando dos llamadas -una por TERMICA y otra por RGB-,
    ambas con el mismo filtro. Basta con mirar cualquiera de las dos."""
    esperadas = {os.path.join(cfg.output_folder, "TERMICA"),
                 os.path.join(cfg.output_folder, "RGB")}
    return next((f for carpeta, f in host.utils_obj.filtros_recibidos
                 if carpeta in esperadas), None)


def test_struct_repartido_cuenta_solo_las_imagenes_de_su_tarea(monkeypatch):
    """Las OCHO tareas de v3.4.31 salieron con exit 1 en la primera corrida real:
    cada una comparaba las ~300 que movía contra las 2.516 del destino entero.
    El reparto estaba sano; el que mentía era el contador."""
    cfg = _cfg()
    nombres = [f"DJI_{i:04d}.JPG" for i in range(1, 200)]
    duenas = {}

    for index in range(8):
        host = _HostDePrueba(etapa="struct", shard_index=index, shard_count=8)
        host.utils_obj.filtros_recibidos = []
        monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
        host.split_images(cfg, _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

        filtro = _filtro_del_destino(host, cfg)
        assert filtro is not None, "sin filtro, la tarea espera el destino entero"
        for nombre in nombres:
            if filtro(nombre):
                duenas.setdefault(nombre, []).append(index)

    assert len(duenas) == len(nombres), "hay imágenes que no espera ninguna tarea"
    assert all(len(d) == 1 for d in duenas.values()), "hay imágenes esperadas por dos tareas"


def test_sin_reparto_el_total_sigue_siendo_el_del_destino_entero(monkeypatch):
    """La app de escritorio corre `todo` con una sola tarea: ahí el total es el
    global y el filtro sobra."""
    cfg = _cfg()
    host = _HostDePrueba(etapa="todo")
    host.utils_obj.filtros_recibidos = []
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(cfg, _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
    assert _filtro_del_destino(host, cfg) is None


def _con_sobrantes(host, nombres):
    """Deja `nombres` sueltos en la raíz de RGB/TERMICA del destino: son las
    imágenes que no encajan en la franja horaria de ningún vuelo."""
    host.utils_obj.imagenes_por_carpeta = {
        os.path.join("/destino", "TERMICA"): list(nombres),
        os.path.join("/destino", "RGB"): list(nombres),
    }


def test_struct_cuenta_como_procesadas_las_que_no_encajan_en_ningun_vuelo(monkeypatch):
    """Con `--etapa struct` el barrido a SIN_ORDENAR ya no corre aquí, sino en
    `post`. Las imágenes fuera del estadillo se quedan en la raíz sin pasar por
    `_mover_pares`: si no se cuentan, el cuadre de `get_summarize` sale en ROJO
    con un reparto perfectamente sano."""
    from atom_core import sharding

    nombres = [f"DJI_{i:04d}.JPG" for i in range(1, 100)]
    host = _HostDePrueba(etapa="struct", shard_index=0, shard_count=8)
    _con_sobrantes(host, nombres)
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(_cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    mias = sum(1 for n in nombres if sharding.toca_imagen(n, 0, 8))
    assert host.gen_struct_folder_obj.current_image_number == mias * 2, (
        "la tarea tiene que contar sus sobrantes de TERMICA y de RGB")


def test_en_todo_los_sobrantes_los_cuenta_el_barrido_y_no_se_suman_dos_veces(monkeypatch):
    """En `todo` sí corre `checking_results_gen_struct_folder`, que ya los suma
    al apartarlos. Contarlos otra vez aquí los duplicaría y volvería a
    descuadrar, esta vez por exceso."""
    host = _HostDePrueba(etapa="todo")
    _con_sobrantes(host, [f"DJI_{i:04d}.JPG" for i in range(1, 100)])
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: True)
    host.split_images(_cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())

    assert host.gen_struct_folder_obj.current_image_number == 0
    assert "gen_struct_folder_obj.checking_results_gen_struct_folder" in host.llamadas


# --- `post` sobre un destino sin estructura -----------------------------------

def _correr_sin_destino(etapa, monkeypatch, **kwargs):
    """Como `_correr`, pero afirmando que NINGUNA carpeta del destino existe."""
    host = _HostDePrueba(etapa=etapa, **kwargs)
    monkeypatch.setattr("atom_core.phases.os.path.isdir", lambda _p: False)
    host.split_images(_cfg(), _SignalFalsa(), _SignalFalsa(), _SignalFalsa())
    return host.llamadas


@pytest.mark.parametrize("shard_index", [0, 3])
def test_post_aborta_si_el_destino_no_esta_estructurado(monkeypatch, shard_index):
    """Ocurrio de verdad (2026-08-09, ejecucion `atom-organizer-pipeline-zgmxb`):
    se lanzo `post` contra un destino recien estrenado y el modo de fallo fue el
    peor de todos — la tarea 0 murio con un `FileNotFoundError: .../TERMICA`
    crudo desde las tripas del barrido, y las otras SIETE dieron VERDE sin haber
    procesado una sola imagen, porque no habia vuelos que repartirles.

    Se parametriza el shard para sujetar justo eso: el guard salta en TODAS las
    tareas, no solo en la 0, para que el run entero salga rojo.
    """
    with pytest.raises(FileNotFoundError) as exc:
        _correr_sin_destino("post", monkeypatch,
                            shard_index=shard_index, shard_count=8)
    mensaje = str(exc.value)
    assert "post" in mensaje and "/destino" in mensaje
    # El mensaje tiene que decir QUE hacer, no solo que falta un directorio.
    assert "struct" in mensaje


def test_el_guard_de_destino_no_afecta_a_las_demas_etapas(monkeypatch):
    """`split` crea el destino: exigirselo hecho seria un bloqueo circular."""
    assert "split_images_obj.iterate_folders" in _correr_sin_destino("split", monkeypatch)
