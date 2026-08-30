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
               destino a `PBx/PBx_Vy/`. Unidad = **imagen** (ver `toca_imagen`).
               Durante mucho tiempo se dio por no particionable porque una fila
               del estadillo puede reclamar imágenes que dejó cualquier tarea de
               `split`; eso solo descarta repartir por VUELO. Repartiendo por
               imagen el problema no existe: cada tarea lee el estadillo ENTERO
               —las ventanas horarias son de solo lectura— y mueve únicamente las
               fotos que le tocan, sin mirar las de nadie más.
               No era barata: medida en el vuelo de referencia son **377 s** de
               los 763 del pipeline, casi todo latencia de GCS moviendo ficheros
               de uno en uno. Eso es I/O, y el I/O se paraleliza.
               Dos cosas NO se reparten y las hace la tarea 0: copiar el estadillo
               a `ESTADILLOS/` (ocho tareas copiándolo generan `_1`…`_7`) y el
               barrido de sobrantes a `SIN_ORDENAR`, que se mudó al arranque de
               `post` — ver `checking_results_gen_struct_folder`.
  · `post`   — recorte RGB, meta/location, rotación y TIF. Trabajan sobre el
               destino YA estructurado, y todas deciden **por carpeta hoja**
               (`PBx_Vy`): el criterio de rotación, por ejemplo, se calcula con
               las imágenes de esa carpeta y de ninguna otra. Unidad = **vuelo
               hoja** (`PBx/PBx_Vy`), no el PB entero: en ANTOLIN hay 7 PB para
               8 tareas, y con esa granularidad ningún algoritmo puede
               equilibrar — el PB más grande marca el suelo del reloj de pared.

Las funciones de aquí solo reparten rutas: no tocan ficheros ni saben nada del
pipeline. Viven en su propio módulo para poder testearlas sin arrastrar numpy,
PIL ni el SDK térmico.
"""
from __future__ import annotations

import os
import zlib

from atom_core.almacen import es_uri_gcs, listar_subcarpetas, unir

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
    if not es_uri_gcs(root):
        # Camino de siempre, intacto: `os.walk` real, sin pasar por la capa
        # URI-aware (que en local sería equivalente, pero no hay motivo para
        # arriesgar una diferencia de comportamiento en el caso que ya funciona).
        encontradas = []
        for dirpath, _dirnames, _filenames in os.walk(root):
            try:
                if get_images(dirpath):
                    encontradas.append(dirpath)
            except OSError:
                continue
        return sorted(encontradas)

    # `gs://…`: no hay `os.walk`, así que se recorre a mano con
    # `listar_subcarpetas` (un nivel por llamada), igual que hace
    # `SplitImages.iterate_folders` en pipeline.py para el mismo árbol.
    encontradas = []

    def _recorrer(carpeta: str) -> None:
        try:
            if get_images(carpeta):
                encontradas.append(carpeta)
        except OSError:
            pass  # Solo se salta ESTA carpeta; las subcarpetas se siguen mirando,
            # igual que `os.walk` no se detiene si una llamada suelta falla.
        for sub in listar_subcarpetas(carpeta):
            _recorrer(unir(carpeta, sub))

    _recorrer(root)
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


def toca_imagen(nombre: str, shard_index: int, shard_count: int) -> bool:
    """True si la imagen `nombre` le toca a esta tarea, en la etapa `struct`.

    El reparto se decide con un hash del NOMBRE y no con la posición en el
    listado de la carpeta, y esa es la diferencia entre que esto funcione o
    corrompa el vuelo. En `split` la posición vale porque el ORIGEN no se toca:
    las N tareas listan lo mismo de principio a fin. En `struct` la carpeta que
    se reparte es también la que se está vaciando —cada tarea MUEVE sus imágenes
    a `PBx/PBx_Vy` mientras las demás siguen listando—, así que el índice de una
    misma foto es distinto según cuándo mire cada tarea. Con `i % count` eso
    significa que una imagen puede tocarle a dos tareas (se mueve dos veces: la
    segunda falla o duplica) o a ninguna (se queda en la raíz y acaba en
    SIN_ORDENAR, dando un vuelo incompleto en ámbar, no en rojo).

    El hash no depende del estado de la carpeta: cada tarea, vea lo que vea y
    cuando lo vea, llega siempre a la misma respuesta para la misma foto. Se usa
    `zlib.crc32` y no el `hash()` de Python a propósito: el de Python está
    aleatorizado por proceso (PYTHONHASHSEED) para las cadenas, que es justo lo
    que no se puede tener aquí — cada tarea de Cloud Run es un proceso distinto.

    Reparte parejo porque los nombres de imagen de un vuelo son correlativos
    (`DJI_0001.JPG`…) y el CRC de una serie así se distribuye uniforme entre los
    N restos; no hace falta pesar nada, todas las fotos cuestan lo mismo (leer su
    EXIF de la caché y un `move`).
    """
    if shard_count <= 1:
        return True
    return zlib.crc32(str(nombre).encode("utf-8")) % shard_count == shard_index


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


def vuelos_del_destino(destino: str, subcarpetas=("RGB", "TERMICA", "RGB_Extra")) -> list[str]:
    """Unidad de reparto de la etapa `post`: rutas RELATIVAS `PBx/PBx_Vy`.

    Se reparte por vuelo hoja y no por PB porque el PB es demasiado grueso: en
    ANTOLIN hay 7 PB y el Job lanza 8 tareas, así que una se queda vacía y la que
    coge el PB mayor marca el reloj de pared por mucho que se pesen bien. El vuelo
    hoja es además la unidad real de trabajo de todas las fases de `post`.

    Se devuelve la UNIÓN de las tres subcarpetas por el mismo motivo que en
    `pbs_del_destino`: un vuelo puede tener térmica y no RGB (o al revés), y hace
    falta que caiga en la MISMA tarea en las tres — la rotación de la térmica lee
    el `location.csv` que escribe el RGB hermano.

    Un PB sin subcarpetas (destino a medio hacer) se devuelve tal cual, para no
    perderlo del reparto.
    """
    rutas = set()
    for pb in pbs_del_destino(destino, subcarpetas):
        for sub in subcarpetas:
            pb_path = os.path.join(destino, sub, pb)
            if not os.path.isdir(pb_path):
                continue
            try:
                hijos = [d for d in os.listdir(pb_path)
                         if os.path.isdir(os.path.join(pb_path, d))]
            except OSError:
                continue
            if hijos:
                rutas.update(f"{pb}/{d}" for d in hijos)
            else:
                rutas.add(pb)
    return sorted(rutas)


def ruta_de_relativo(base: str, relativo: str) -> str:
    """`base` + una ruta relativa `PBx/PBx_Vy` con los separadores del sistema.

    Las relativas se manejan siempre con `/` (son claves, no rutas del disco);
    esto es lo único que las convierte en una ruta de verdad."""
    return os.path.join(base, *str(relativo).replace("\\", "/").strip("/").split("/"))


def peso_de_ruta(destino: str, relativo: str, contar,
                 subcarpetas=("RGB", "TERMICA", "RGB_Extra")) -> int:
    """Imágenes totales de una unidad de reparto (`PBx` o `PBx/PBx_Vy`) sumando
    RGB + TERMICA (+ RGB_Extra). Es el coste que se usa para equilibrar la etapa
    `post`, donde las fases caras (TIF sobre todo) escalan con el número de
    imágenes térmicas."""
    total = 0
    for sub in subcarpetas:
        ruta = ruta_de_relativo(os.path.join(destino, sub), relativo)
        if os.path.isdir(ruta):
            total += contar(ruta)
    return total


def indice_seleccion(seleccion) -> dict:
    """`{PBx: {vuelos} | None}` a partir de una lista de unidades de reparto.

    `None` como valor significa «el PB entero» (la unidad era el PB, sin vuelo).
    Sirve para que las verificaciones del pipeline, que recorren PB y luego sus
    subcarpetas, sepan si un vuelo concreto es suyo sin tener que parsear rutas
    en cada punto."""
    indice: dict = {}
    for item in seleccion:
        partes = str(item).replace("\\", "/").strip("/").split("/")
        pb = partes[0]
        if len(partes) == 1:
            indice[pb] = None
        elif indice.get(pb, "sin-valor") is not None:
            indice.setdefault(pb, set()).add(partes[1])
    return indice


def seleccion_incluye(indice: dict, pb: str, subcarpeta: str | None = None) -> bool:
    """True si a esta tarea le toca `pb` (o el vuelo `pb/subcarpeta`)."""
    if pb not in indice:
        return False
    vuelos = indice[pb]
    return vuelos is None or subcarpeta is None or subcarpeta in vuelos
