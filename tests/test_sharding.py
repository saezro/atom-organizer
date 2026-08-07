"""Reparto del trabajo de un vuelo entre N tareas paralelas.

Lo que estos tests protegen es un invariante muy concreto: las N tareas calculan
su parte POR SEPARADO, sin hablar entre ellas, así que la unión de lo que cada
una cree suyo tiene que ser exactamente el total, sin repetir nada. Si el reparto
solapa, dos tareas procesan la misma imagen (y `unique_dest` la duplica con
`_1`); si deja huecos, el vuelo sale incompleto y en VERDE, que es peor.

Contexto: la corrida completa de ANTOLIN medida el 2026-08-07 son 33m 06s en una
sola tarea de 8 vCPU. El reparto existe para bajar eso a ~9m con 8 tareas.
"""
import os

import pytest

from atom_core import sharding


# --- normalización de los dos números que llegan de fuera --------------------

@pytest.mark.parametrize("index, count, esperado", [
    (0, 1, (0, 1)),
    (3, 8, (3, 8)),
    ("2", "4", (2, 4)),          # llegan como string desde argparse / env
    (None, None, (0, 1)),
    ("", "", (0, 1)),
    ("hola", "que tal", (0, 1)),  # ilegible -> neutro, no excepción
    (0, 0, (0, 1)),              # count 0 dividiría por cero
    (0, -5, (0, 1)),
    (-1, 4, (0, 1)),             # index negativo: se descarta el reparto entero
    (4, 4, (0, 1)),              # index == count: esa tarea se quedaría sin nada
    (9, 4, (0, 1)),
])
def test_normalizar_shard(index, count, esperado):
    assert sharding.normalizar_shard(index, count) == esperado


def test_shard_desde_entorno_lee_las_variables_de_cloud_run():
    env = {"CLOUD_RUN_TASK_INDEX": "5", "CLOUD_RUN_TASK_COUNT": "8"}
    assert sharding.shard_desde_entorno(env) == (5, 8)


def test_shard_desde_entorno_fuera_de_cloud_run_no_reparte():
    """Sin las variables, el comportamiento tiene que ser el de siempre: una sola
    tarea que lo hace todo. Es lo que corre la app de escritorio."""
    assert sharding.shard_desde_entorno({}) == (0, 1)


# --- el invariante: partición completa y sin solapes -------------------------

@pytest.mark.parametrize("n_items", [0, 1, 3, 7, 8, 9, 40])
@pytest.mark.parametrize("shard_count", [1, 2, 3, 8])
def test_los_shards_particionan_el_total(n_items, shard_count):
    items = [f"vuelo_{i:03d}" for i in range(n_items)]
    repartido = []
    for i in range(shard_count):
        repartido.extend(sharding.repartir(items, i, shard_count))
    assert sorted(repartido) == sorted(items)
    assert len(repartido) == len(set(repartido))


@pytest.mark.parametrize("shard_count", [2, 3, 8])
def test_los_shards_particionan_el_total_tambien_con_peso(shard_count):
    pesos = {f"v{i}": (i * 37) % 91 for i in range(25)}
    items = list(pesos)
    repartido = []
    for i in range(shard_count):
        repartido.extend(sharding.repartir(items, i, shard_count, peso=pesos.get))
    assert sorted(repartido) == sorted(items)
    assert len(repartido) == len(set(repartido))


def test_pesos_empatados_no_rompen_la_particion():
    """Todos los elementos con el mismo peso es el caso en el que un orden no
    determinista pasaría desapercibido en local y duplicaría imágenes en Cloud
    Run: cada tarea ordenaría la lista a su manera."""
    items = [f"carpeta_{i}" for i in range(12)]
    repartido = []
    for i in range(4):
        repartido.extend(sharding.repartir(items, i, 4, peso=lambda _c: 10))
    assert sorted(repartido) == sorted(items)


def test_repartir_es_determinista_entre_llamadas():
    items = ["b", "a", "c", "d", "e"]
    pesos = {"a": 5, "b": 5, "c": 1, "d": 9, "e": 9}
    primera = sharding.repartir(items, 1, 3, peso=pesos.get)
    segunda = sharding.repartir(list(reversed(items)), 1, 3, peso=pesos.get)
    assert primera == segunda


def test_sin_reparto_devuelve_todo():
    items = ["a", "b", "c"]
    assert sharding.repartir(items, 0, 1) == items


# --- equilibrado por carga ---------------------------------------------------

def test_reparte_por_carga_y_no_por_numero_de_carpetas():
    """El reloj del Job es el MÁXIMO de las N tareas, así que lo que hay que
    igualar son las imágenes, no las carpetas. Una carpeta de 600 y seis de 40
    tienen que quedar en tareas distintas."""
    pesos = {"grande": 600, **{f"peq{i}": 40 for i in range(6)}}
    cargas = []
    for i in range(2):
        mios = sharding.repartir(list(pesos), i, 2, peso=pesos.get)
        cargas.append(sum(pesos[m] for m in mios))
    # Con round-robin ciego saldría 600+40+40 vs 40+40+40 (680 vs 120).
    assert max(cargas) == 600
    assert min(cargas) == 240


def test_mas_tareas_que_carpetas_deja_tareas_vacias_pero_no_pierde_nada():
    items = ["a", "b"]
    todos = [sharding.repartir(items, i, 5, peso=lambda _c: 1) for i in range(5)]
    assert sorted(x for lote in todos for x in lote) == ["a", "b"]
    assert sum(1 for lote in todos if not lote) == 3


# --- descubrimiento de las unidades de reparto sobre disco -------------------

def _get_images(carpeta):
    return sorted(f for f in os.listdir(carpeta) if f.endswith(".JPG"))


def test_carpetas_con_imagenes_encuentra_las_hojas_a_cualquier_profundidad(tmp_path):
    """En los orígenes reales los `DJI_*` cuelgan a profundidades distintas según
    cómo haya volcado la tarjeta el piloto: repartir por el primer nivel dejaría
    a una tarea con el árbol entero."""
    (tmp_path / "DCIM" / "DJI_202603171200_001").mkdir(parents=True)
    (tmp_path / "DCIM" / "DJI_202603171200_001" / "A_T.JPG").write_bytes(b"x")
    (tmp_path / "otro" / "nivel" / "hondo").mkdir(parents=True)
    (tmp_path / "otro" / "nivel" / "hondo" / "B.JPG").write_bytes(b"x")
    (tmp_path / "vacia").mkdir()

    encontradas = sharding.carpetas_con_imagenes(str(tmp_path), _get_images)

    assert [os.path.basename(c) for c in encontradas] == ["DJI_202603171200_001", "hondo"]


def test_carpetas_con_imagenes_ignora_las_que_no_tienen(tmp_path):
    (tmp_path / "solo_csv").mkdir()
    (tmp_path / "solo_csv" / "meta.csv").write_text("x")
    assert sharding.carpetas_con_imagenes(str(tmp_path), _get_images) == []


def test_pbs_del_destino_une_rgb_y_termica(tmp_path):
    """Un PB puede tener térmica y no RGB. Si cada fase repartiera por su cuenta,
    el mismo PB podría caer en tareas distintas para el recorte y para el TIF."""
    for sub, pbs in (("RGB", ["PB1", "PB2"]), ("TERMICA", ["PB2", "PB3"])):
        for pb in pbs:
            (tmp_path / sub / pb).mkdir(parents=True)
    (tmp_path / "RGB" / "no_es_un_pb").mkdir()
    (tmp_path / "CSVs").mkdir()

    assert sharding.pbs_del_destino(str(tmp_path)) == ["PB1", "PB2", "PB3"]


def test_pbs_del_destino_sin_estructura_devuelve_vacio(tmp_path):
    assert sharding.pbs_del_destino(str(tmp_path)) == []


# --- reparto de la separación: la unidad es la IMAGEN -----------------------

def _origen_falso(tmp_path, por_carpeta):
    for nombre, n in por_carpeta.items():
        d = tmp_path / nombre
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"{nombre}_{i:04d}.JPG").write_bytes(b"x")
    return sorted(str(tmp_path / n) for n in por_carpeta)


def test_reparte_por_imagen_y_no_por_carpeta(tmp_path):
    """El caso real que obligó a esto: ANTOLIN son 2.516 fotos en DOS carpetas.
    Repartiendo por carpeta, seis de las ocho tareas se quedarían paradas."""
    carpetas = _origen_falso(tmp_path, {"DJI_001": 1302, "DJI_002": 1214})

    cargas = []
    for i in range(8):
        reparto = sharding.repartir_imagenes(carpetas, _get_images, i, 8)
        cargas.append(sum(len(v) for v in reparto.values()))

    assert all(c > 0 for c in cargas), "alguna tarea se quedó sin trabajo"
    assert max(cargas) - min(cargas) <= 1, "el reparto quedó desequilibrado"
    assert sum(cargas) == 2516


def test_el_reparto_de_imagenes_es_una_particion(tmp_path):
    """Ninguna imagen puede quedarse sin procesar (vuelo incompleto en verde) ni
    procesarse dos veces (duplicados `_1` de `unique_dest`)."""
    carpetas = _origen_falso(tmp_path, {"A": 17, "B": 5, "C": 40})

    vistas = []
    for i in range(5):
        reparto = sharding.repartir_imagenes(carpetas, _get_images, i, 5)
        for carpeta, imagenes in reparto.items():
            vistas.extend(os.path.join(carpeta, img) for img in imagenes)

    todas = [os.path.join(c, img) for c in carpetas for img in _get_images(c)]
    assert sorted(vistas) == sorted(todas)
    assert len(vistas) == len(set(vistas))


def test_sin_reparto_la_separacion_coge_todas_las_imagenes(tmp_path):
    carpetas = _origen_falso(tmp_path, {"A": 6})
    reparto = sharding.repartir_imagenes(carpetas, _get_images, 0, 1)
    assert len(reparto[carpetas[0]]) == 6


def test_las_carpetas_sin_imagenes_propias_no_aparecen(tmp_path):
    """Una tarea a la que no le toca nada de una carpeta no debe recibir esa
    carpeta con lista vacía: acabaría emitiendo «Analizando directorio» y
    «Procesando 0 imágenes» por carpetas que no le corresponden."""
    carpetas = _origen_falso(tmp_path, {"A": 1})
    reparto = sharding.repartir_imagenes(carpetas, _get_images, 3, 4)
    assert reparto == {}


def test_peso_de_ruta_suma_rgb_y_termica(tmp_path):
    for sub, n in (("RGB", 3), ("TERMICA", 5)):
        d = tmp_path / sub / "PB1"
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"{i}.JPG").write_bytes(b"x")

    def contar(ruta):
        return len(_get_images(ruta))

    assert sharding.peso_de_ruta(str(tmp_path), "PB1", contar) == 8


# --- reparto del post-proceso: la unidad es el VUELO HOJA -------------------

def _destino_falso(tmp_path, por_sub):
    """`{"RGB": {"PBA/PBA_V1": 3, ...}, ...}` -> árbol con esas imágenes."""
    for sub, vuelos in por_sub.items():
        for rel, n in vuelos.items():
            d = tmp_path / sub
            for parte in rel.split("/"):
                d = d / parte
            d.mkdir(parents=True, exist_ok=True)
            for i in range(n):
                (d / f"{rel.replace('/', '_')}_{i}.JPG").write_bytes(b"x")


def test_vuelos_del_destino_une_las_subcarpetas(tmp_path):
    """Un vuelo puede tener térmica y no RGB. Tiene que caer en la MISMA tarea en
    las tres: la rotación de la térmica lee el `location.csv` del RGB hermano."""
    _destino_falso(tmp_path, {
        "RGB": {"PBA/PBA_V1": 2, "PBA/PBA_V2": 2},
        "TERMICA": {"PBA/PBA_V2": 2, "PBB/PBB_V1": 2},
    })

    assert sharding.vuelos_del_destino(str(tmp_path)) == [
        "PBA/PBA_V1", "PBA/PBA_V2", "PBB/PBB_V1"]


def test_un_pb_sin_vuelos_no_se_pierde_del_reparto(tmp_path):
    """Destino a medio hacer: mejor repartir el PB entero que dejarlo fuera."""
    (tmp_path / "RGB" / "PBA").mkdir(parents=True)
    assert sharding.vuelos_del_destino(str(tmp_path)) == ["PBA"]


def test_el_vuelo_equilibra_donde_el_pb_no_puede(tmp_path):
    """El caso real de ANTOLIN: 7 PB para 8 tareas. Repartiendo por PB una tarea
    se queda vacía y el PB mayor marca el suelo; por vuelo, no."""
    vuelos = {f"PB{p}/PB{p}_V{v}": 60 for p in "ABCDEFG" for v in range(1, 5)}
    _destino_falso(tmp_path, {"TERMICA": vuelos})

    def contar(ruta):
        return len(_get_images(ruta))

    destino = str(tmp_path)
    todos = sharding.vuelos_del_destino(destino)
    cargas = []
    for i in range(8):
        mios = sharding.repartir(todos, i, 8,
                                 peso=lambda r: sharding.peso_de_ruta(destino, r, contar))
        cargas.append(sum(contar(sharding.ruta_de_relativo(
            os.path.join(destino, "TERMICA"), r)) for r in mios))

    assert all(c > 0 for c in cargas), "alguna tarea se quedó sin trabajo"
    assert max(cargas) - min(cargas) <= 60, f"reparto desequilibrado: {cargas}"

    # Y el contraste: con la unidad vieja (el PB) no hay reparto posible — son 7
    # unidades para 8 tareas, así que una se queda a cero pese a pesarlas bien.
    pbs = sharding.pbs_del_destino(destino)
    cargas_pb = [sum(sharding.peso_de_ruta(destino, pb, contar)
                     for pb in sharding.repartir(
                         pbs, i, 8,
                         peso=lambda pb: sharding.peso_de_ruta(destino, pb, contar)))
                 for i in range(8)]
    assert min(cargas_pb) == 0


def test_el_reparto_de_vuelos_es_una_particion(tmp_path):
    _destino_falso(tmp_path, {"RGB": {f"PB{p}/PB{p}_V{v}": 1
                                      for p in "ABC" for v in range(1, 6)}})
    destino = str(tmp_path)
    todos = sharding.vuelos_del_destino(destino)

    vistos = [r for i in range(4) for r in sharding.repartir(todos, i, 4)]
    assert sorted(vistos) == todos
    assert len(vistos) == len(set(vistos))


# --- filtro de las verificaciones (`only_pb`) ------------------------------

def test_indice_seleccion_agrupa_por_pb():
    indice = sharding.indice_seleccion(["PBA/PBA_V1", "PBA/PBA_V3", "PBB/PBB_V1"])
    assert indice == {"PBA": {"PBA_V1", "PBA_V3"}, "PBB": {"PBB_V1"}}


def test_un_pb_a_secas_selecciona_el_pb_entero():
    """Compatibilidad con el destino a medio hacer: si la unidad fue el PB, la
    verificación tiene que mirar TODOS sus vuelos, no ninguno."""
    indice = sharding.indice_seleccion(["PBA"])
    assert sharding.seleccion_incluye(indice, "PBA", "PBA_V9") is True


def test_seleccion_deja_fuera_los_vuelos_de_otras_tareas():
    """El motivo de existir del filtro: verificar un vuelo que otra tarea está
    procesando ahora mismo da un "jpg y tiff no coinciden" que no es un fallo."""
    indice = sharding.indice_seleccion(["PBA/PBA_V1"])
    assert sharding.seleccion_incluye(indice, "PBA", "PBA_V1") is True
    assert sharding.seleccion_incluye(indice, "PBA", "PBA_V2") is False
    assert sharding.seleccion_incluye(indice, "PBB", "PBB_V1") is False
