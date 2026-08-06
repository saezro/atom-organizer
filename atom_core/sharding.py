"""Reparto del trabajo de un vuelo entre N tareas paralelas (sharding).

Motivo: una corrida completa de ANTOLIN (2.516 imágenes) tarda **33m 06s** en un
Cloud Run Job de 8 vCPU, y el 90 % de ese tiempo son fases que trabajan carpeta a
carpeta sin ninguna dependencia entre ellas. Repartirlas entre N tareas del mismo
Job no encarece apenas (son las mismas vCPU·segundo, gastadas en paralelo en vez
de en serie) y divide el reloj de pared por ~N.

El pipeline tiene **tres etapas con unidades de partición distintas**, y por eso
el reparto no es un simple «trocea la lista de imágenes»:

  · `split`  — separación RGB/térmica. Lee el ORIGEN y escribe plano en
               `destino/RGB` y `destino/TERMICA`. Unidad = **carpeta del origen**
               que contiene imágenes (una carpeta de vuelo `DJI_*`, normalmente).
  · `struct` — estructura de carpetas. Lee el ESTADILLO y MUEVE lo que hay en el
               destino a `PBx/PBx_Vy/`. No es particionable: una fila del
               estadillo puede reclamar imágenes que dejó cualquier tarea de
               `split`, así que necesita el destino entero y ya quieto. Es barata
               (~16 s medidos) y va en una sola tarea.
  · `post`   — recorte RGB, meta/location, rotación y TIF. Trabajan sobre el
               destino YA estructurado, y todas deciden **por carpeta hoja**
               (`PBx_Vy`): el criterio de rotación, por ejemplo, se calcula con
               las imágenes de esa carpeta y de ninguna otra. Unidad = **PBx**.

Las funciones de aquí solo reparten rutas: no tocan ficheros ni saben nada del
pipeline. Viven en su propio módulo para poder testearlas sin arrastrar numpy,
PIL ni el SDK térmico.
"""
from __future__ import annotations

import os

ETAPAS = ("todo", "split", "struct", "post")


def normalizar_shard(shard_index, shard_count) -> tuple[int, int]:
    """Devuelve `(index, count)` saneados. Cualquier valor ilegible o fuera de
    rango cae al par neutro `(0, 1)` = «no hay reparto, hazlo todo».

    Es la única puerta de entrada de estos dos números, que llegan de la línea de
    comandos o de las variables de entorno de Cloud Run: un `count` a 0 dividiría
    por cero y un `index >= count` dejaría a esa tarea sin trabajo en silencio,
    que es peor que no repartir (el vuelo saldría incompleto y en verde).
    """
    try:
        count = int(shard_count)
    except (TypeError, ValueError):
        count = 1
    try:
        index = int(shard_index)
    except (TypeError, ValueError):
        index = 0
    if count < 1:
        count = 1
    if index < 0 or index >= count:
        index = 0
        count = 1
    return index, count


def shard_desde_entorno(env=None) -> tuple[int, int]:
    """Lee `CLOUD_RUN_TASK_INDEX` / `CLOUD_RUN_TASK_COUNT`, que Cloud Run Jobs
    inyecta en cada tarea. Fuera de Cloud Run no existen y sale `(0, 1)`, con lo
    que el comportamiento por defecto es el de siempre."""
    env = os.environ if env is None else env
    return normalizar_shard(env.get("CLOUD_RUN_TASK_INDEX"),
                            env.get("CLOUD_RUN_TASK_COUNT"))


def repartir(items: list, shard_index: int, shard_count: int, peso=None) -> list:
    """Devuelve los elementos de `items` que le tocan a esta tarea.

    Con `peso` reparte por CARGA (LPT: se ordena de más pesado a menos y cada
    elemento va al shard que menos lleva acumulado), no por número de elementos.
    Importa: las carpetas de vuelo son muy desiguales (un `DJI_*` puede tener 40
    imágenes y el de al lado 600), y un round-robin ciego dejaría a una tarea
    corriendo el doble que el resto — con lo que el reloj de pared del Job, que
    es el máximo de las N tareas, apenas bajaría.

    El reparto es **determinista**: las N tareas ejecutan este mismo cálculo por
    separado, sin hablar entre ellas, y tienen que llegar a una partición que no
    solape y no deje nada fuera. Por eso se ordena por (-peso, str(item)): sin el
    desempate por nombre, dos carpetas del mismo tamaño podrían salir en orden
    distinto en cada tarea y una imagen acabaría procesada dos veces o ninguna.

    Arguments:
    ---------
    - items - elementos a repartir (rutas, normalmente).
    - shard_index / shard_count - ya saneados por `normalizar_shard`.
    - peso - callable item -> número (coste estimado). Si es None, todos pesan 1.
    """
    if shard_count <= 1:
        return list(items)
    if peso is None:
        ordenados = sorted(items, key=str)
        return [it for i, it in enumerate(ordenados) if i % shard_count == shard_index]

    ordenados = sorted(items, key=lambda it: (-float(peso(it)), str(it)))
    carga = [0.0] * shard_count
    mios = []
    for item in ordenados:
        destino = min(range(shard_count), key=lambda s: (carga[s], s))
        carga[destino] += float(peso(item))
        if destino == shard_index:
            mios.append(item)
    return mios


def carpetas_con_imagenes(root: str, get_images) -> list[str]:
    """Rutas de todas las carpetas del árbol de `root` que contienen imágenes.

    Es la unidad de reparto de la etapa `split`: `SplitImages.iterate_folders`
    recorre el origen llamando a `split_images` una vez por carpeta, así que
    trocear por carpeta reproduce exactamente el mismo trabajo, solo que dividido.

    Se listan las que TIENEN imágenes y no las carpetas de primer nivel a secas:
    en los orígenes reales los `DJI_*` cuelgan a profundidades distintas según
    cómo haya volcado la tarjeta el piloto, y repartir por el primer nivel dejaría
    a una tarea con el árbol entero.

    Arguments:
    ---------
    - root - carpeta de origen.
    - get_images - callable ruta -> lista de imágenes (típicamente
      `Utils.get_images_from_dir`), para no duplicar aquí qué extensión es imagen.
    """
    encontradas = []
    for dirpath, _dirnames, _filenames in os.walk(root):
        try:
            if get_images(dirpath):
                encontradas.append(dirpath)
        except OSError:
            continue
    return sorted(encontradas)


def contar_imagenes(carpeta: str, get_images) -> int:
    try:
        return len(get_images(carpeta))
    except OSError:
        return 0


def repartir_imagenes(carpetas: list[str], get_images, shard_index: int,
                      shard_count: int) -> dict:
    """`{carpeta: [imágenes que le tocan a esta tarea]}` para la etapa `split`.

    La unidad es la IMAGEN y no la carpeta, y esto no es un refinamiento: ANTOLIN
    —el vuelo de referencia— son 2.516 fotos repartidas en **dos** carpetas
    `DJI_*`. Repartiendo por carpeta, un Job de 8 tareas dejaría seis sin nada
    que hacer y la fase se quedaría en 6m17s / 2 en vez de / 8.

    Se puede repartir tan fino porque la separación es imagen a imagen y sin
    estado: cada foto se lee del origen y se copia a `destino/RGB` o
    `destino/TERMICA`, que son PLANAS en esta fase. Dos fotos de la misma carpeta
    procesadas por tareas distintas no se pisan.

    El corte se hace sobre el índice GLOBAL (acumulado entre carpetas) y no sobre
    el de cada carpeta: si no, con muchas carpetas pequeñas las primeras tareas
    se llevarían siempre la primera imagen de cada una y el reparto se torcería.
    """
    reparto = {}
    global_i = 0
    for carpeta in sorted(carpetas):
        try:
            imagenes = list(get_images(carpeta))
        except OSError:
            continue
        mias = []
        for imagen in imagenes:
            if shard_count <= 1 or global_i % shard_count == shard_index:
                mias.append(imagen)
            global_i += 1
        if mias:
            reparto[carpeta] = mias
    return reparto


def pbs_del_destino(destino: str, subcarpetas=("RGB", "TERMICA", "RGB_Extra")) -> list[str]:
    """Nombres de las carpetas `PB*` que existen en el destino ya estructurado.

    Es la unidad de reparto de la etapa `post`. Se mira en RGB, TERMICA y
    RGB_Extra y se devuelve la UNIÓN: un PB puede tener térmica y no RGB (o al
    revés), y si cada fase repartiera por su cuenta un mismo PB podría caer en
    tareas distintas para el recorte y para el TIF — que funciona, pero rompe la
    correspondencia PB→tarea de la que dependen los `checking_*` acotados.
    """
    nombres = set()
    for sub in subcarpetas:
        ruta = os.path.join(destino, sub)
        if not os.path.isdir(ruta):
            continue
        try:
            entradas = os.listdir(ruta)
        except OSError:
            continue
        for d in entradas:
            if d.startswith("PB") and os.path.isdir(os.path.join(ruta, d)):
                nombres.add(d)
    return sorted(nombres)


def peso_de_pb(destino: str, pb: str, contar,
               subcarpetas=("RGB", "TERMICA", "RGB_Extra")) -> int:
    """Imágenes totales de un PB sumando RGB + TERMICA (+ RGB_Extra). Es el coste
    que se usa para equilibrar la etapa `post`, donde las fases caras (TIF sobre
    todo) escalan con el número de imágenes térmicas de cada PB."""
    total = 0
    for sub in subcarpetas:
        ruta = os.path.join(destino, sub, pb)
        if os.path.isdir(ruta):
            total += contar(ruta)
    return total
