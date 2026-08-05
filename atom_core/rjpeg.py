"""Inspección de R-JPEG (JPEG radiométrico de DJI) previa a la conversión a TIFF.

Por qué existe: cuando el conversor del DJI Thermal SDK rechaza una imagen lo hace
con `create R-JPEG dirp handle failed` y código -16, sin decir QUÉ le pasa al
fichero. El 2026-08-05, con las térmicas del 28/07, ese -16 dejó el diagnóstico en
punto muerto: la misma imagen convertía bien en otra máquina, así que la pregunta
era si el fichero que llegaba al conversor seguía siendo el original — y no había
en el log ni un byte para responderla.

Con el tamaño, el hash y los segmentos APP delante del -16, esa pregunta se
responde leyendo el log, que es el único canal de diagnóstico disponible (el
usuario final no ejecuta nada por terminal).

El payload radiométrico de DJI viaja en segmentos APPn del JPEG (APP3/APP4/APP5,
según modelo y firmware). Un JPEG que perdió esos segmentos sigue abriéndose como
imagen normal — se ve igual — pero ya no tiene datos de temperatura y el SDK lo
rechaza. Ese es exactamente el fallo que no se distinguía de un problema del SDK.

Módulo puro: sin Qt, sin PIL, sin dependencias del pipeline. No lanza nunca: ante
cualquier problema devuelve el motivo en el propio resultado. Es diagnóstico, así
que NO debe alterar el flujo de conversión.
"""
import hashlib
import os

# Marcadores APPn donde DJI mete el payload radiométrico. Puede usar varios a la
# vez y repetir el mismo (el M2EA de la muestra trae 11 APP3 de ~64 KB, que es el
# tamaño máximo de un segmento JPEG, más un APP4 y un APP5).
MARCADORES_PAYLOAD = (0xE3, 0xE4, 0xE5)

# Suelo de payload por debajo del cual no hay datos radiométricos que valgan: una
# imagen 640x512 float32 son 1.310.720 bytes de raw, y el payload comprimido de la
# muestra real ronda los 745 KB. 64 KB es holgadamente conservador — está para
# cazar el caso "no queda nada", no para validar la integridad del contenido.
MIN_PAYLOAD_BYTES = 64 * 1024

# Marcadores sin longitud (no llevan los 2 bytes de tamaño detrás).
_SIN_LONGITUD = frozenset({0xD8, 0xD9, 0x01} | set(range(0xD0, 0xD8)))


def _iter_segmentos(data):
    """Recorre los marcadores de un JPEG y va emitiendo (marcador, longitud).

    Se detiene en SOS (0xDA): a partir de ahí vienen los datos comprimidos de la
    imagen y ya no hay marcadores que interpretar sin descodificar. Los segmentos
    de DJI van todos en la cabecera, así que no se pierde nada.
    """
    if len(data) < 2 or data[0] != 0xFF or data[1] != 0xD8:
        return
    i = 2
    n = len(data)
    while i + 1 < n:
        if data[i] != 0xFF:
            # Relleno o desincronización: avanzar hasta el siguiente 0xFF.
            i += 1
            continue
        marcador = data[i + 1]
        if marcador == 0xFF:  # bytes de relleno antes del marcador real
            i += 1
            continue
        if marcador == 0xDA:  # SOS: empieza el scan
            return
        if marcador in _SIN_LONGITUD:
            i += 2
            continue
        if i + 3 >= n:
            return
        longitud = (data[i + 2] << 8) | data[i + 3]
        if longitud < 2:
            return
        yield marcador, longitud
        i += 2 + longitud


def inspect_rjpeg(path):
    """Devuelve un diccionario con el estado radiométrico del fichero.

    Claves: `existe`, `size`, `sha256` (16 hex, suficiente para comparar dos
    copias del mismo fichero), `segmentos` ({marcador: (nº, bytes)}), `payload`
    (bytes sumados) y `motivo` ("" si todo está en orden). `ok` es False cuando el
    fichero no puede llegar bien al conversor.
    """
    info = {
        "existe": False,
        "size": 0,
        "sha256": "",
        "segmentos": {},
        "payload": 0,
        "ok": False,
        "motivo": "",
    }
    try:
        info["size"] = os.path.getsize(path)
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as err:
        info["motivo"] = "no se puede leer el fichero ({0})".format(err)
        return info

    info["existe"] = True
    info["sha256"] = hashlib.sha256(data).hexdigest()[:16]

    if info["size"] == 0:
        info["motivo"] = "el fichero está vacío (0 bytes)"
        return info
    if not data.startswith(b"\xff\xd8"):
        info["motivo"] = "no es un JPEG (no empieza por SOI 0xFFD8)"
        return info

    for marcador, longitud in _iter_segmentos(data):
        if marcador in MARCADORES_PAYLOAD:
            n, total = info["segmentos"].get(marcador, (0, 0))
            info["segmentos"][marcador] = (n + 1, total + longitud)
    info["payload"] = sum(total for _, total in info["segmentos"].values())

    if not info["segmentos"]:
        info["motivo"] = (
            "no tiene payload radiométrico: faltan los segmentos APP3/APP4/APP5 de DJI. "
            "Es un JPEG normal, no un R-JPEG — se ve igual pero ya no lleva temperaturas, "
            "y el conversor lo rechazará"
        )
    elif info["payload"] < MIN_PAYLOAD_BYTES:
        info["motivo"] = (
            "el payload radiométrico es de solo {0} bytes (se esperan cientos de KB): "
            "los segmentos APP están truncados".format(info["payload"])
        )
    else:
        info["ok"] = True
    return info


def describe(info, nombre=""):
    """Línea única para el log de corrida. Compacta cuando todo está bien."""
    if not info.get("existe"):
        return "[rjpeg] {0} ERROR: {1}".format(nombre, info.get("motivo") or "no existe")

    apps = " ".join(
        "APP{0}x{1}={2}B".format(m - 0xE0, n, total)
        for m, (n, total) in sorted(info["segmentos"].items())
    ) or "sin APP3/4/5"

    linea = "[rjpeg] {0} {1}B sha256:{2} {3} payload={4}B".format(
        nombre, info["size"], info["sha256"], apps, info["payload"])
    if info.get("ok"):
        return linea + " OK"
    return linea + " ✗ " + info.get("motivo", "")
