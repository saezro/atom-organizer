"""Documentacion HTTP de la API LAN del Organizer (`GET /api/docs`).

Documenta dos grupos de rutas, los unicos con contrato HTTP estable para
clientes ajenos al kiosco (el resto de la API, `/api/<metodo>`, `pin_*`,
`cloud_*`, etc., es interna y NO sale aqui a proposito):

- Las 3 rutas que puede usar la app "Estadillo Digital" de Christian desde un
  portatil de la LAN (ver `_RUTAS_LAN_ABIERTAS` en `webserver.py`): ping,
  `GET /api/estadillo/espera` y `POST /api/estadillo`. Sin token, CORS abierto.
- Las rutas `_RUTAS_CONTROL` del panel de control remoto (movil/tablet en la
  LAN operando el kiosco sin tocar la pantalla de la Pi): discos, carpetas,
  fijar carpeta, organizar, estado y cancelar. Con PIN del kiosco por
  cabecera (`X-Atom-Pin`), CORS abierto igual que las de estadillo.

Las rutas de los ejemplos se leen de las constantes de `webserver.py` (no se
repiten a mano) para que esta documentacion no se desincronice si algun dia
cambian de sitio. El host de los ejemplos es SIEMPRE `organizer.atom` con el
alias amistoso `organizer.local` que usa mDNS en la LAN -nunca una IP-: ver
`especificacion()`.
"""

from __future__ import annotations

HOST_EJEMPLO = "http://organizer.local"
PUERTO_ALTERNATIVO = 8765

TITULO = "API del Organizer para el Estadillo Digital"


def _columnas_vuelo() -> tuple[list[str], dict[str, str]]:
    """Columnas reconocidas de un `vuelo` del POST y sus alias de JSON.

    Import perezoso (aqui, no arriba del modulo): igual que el resto de
    `webserver.py`, para no arrastrar pandas (via `atom_core.estadillo`) solo
    por construir la documentacion. Fuente unica: `atom_core.estadillo`, asi
    esta lista no se desincroniza si cambian las columnas reconocidas.
    """
    from atom_core import estadillo

    columnas = sorted(estadillo._CSV_COLUMNAS_CONOCIDAS)
    alias = dict(estadillo._CSV_ALIAS_CAMPO_JSON)
    return columnas, alias


def _endpoint_ping() -> dict:
    from atom_core.webserver import _RUTA_ESTADILLO_PING

    return {
        "id": "ping",
        "metodo": "GET",
        "ruta": _RUTA_ESTADILLO_PING,
        "resumen": (
            "Localiza al Organizer en la LAN. Responde SIEMPRE 200 -incluso "
            "sin modo espera activo-: sirve para escanear IP a IP el puerto "
            "80/8765 y reconocer cual maquina es la Pi."
        ),
        "auth": "Ninguna (sin token, con CORS abierto).",
        "request": None,
        "respuestas": [
            {
                "codigo": 200,
                "cuando": "Siempre.",
                "ejemplo": {"organizer": True, "version": "1.4.2", "esperando": False},
            },
        ],
        "campos_respuesta": [
            {"campo": "organizer", "tipo": "bool", "nota": "Siempre `true`: identifica que es el Organizer."},
            {"campo": "version", "tipo": "string", "nota": "Version de la app, o \"?\" si no se pudo leer."},
            {"campo": "esperando", "tipo": "bool", "nota": "Si el kiosco tiene el modo espera de estadillo activo ahora mismo."},
        ],
        "curl": f"curl {HOST_EJEMPLO}{_RUTA_ESTADILLO_PING}",
    }


def _endpoint_espera() -> dict:
    from atom_core.webserver import _RUTA_ESTADILLO_ESPERA

    return {
        "id": "espera",
        "metodo": "GET",
        "ruta": _RUTA_ESTADILLO_ESPERA,
        "resumen": (
            "Consulta si el kiosco esta esperando un estadillo y si ya hay "
            "carpeta de vuelo elegida. Se sondea en bucle antes de mandar el "
            "POST."
        ),
        "auth": "Ninguna (sin token, con CORS abierto).",
        "request": None,
        "respuestas": [
            {
                "codigo": 409,
                "cuando": (
                    "No hay modo espera activo: nunca se inicio, o caduco "
                    "(`caducado` distingue los dos casos; `codigo` vale "
                    "`sin_espera_activa` o `espera_caducada`)."
                ),
                "ejemplo": {
                    "ok": False, "codigo": "sin_espera_activa",
                    "motivo": "El kiosco no tiene ninguna espera de estadillo activa. Inícala desde el kiosco antes de enviar.",
                    "esperando": False, "caducado": False,
                    "carpeta_seleccionada": False, "carpeta": None,
                    "estadillo_en_carpeta": {"encontrado": False, "nombre": None, "buscando": False},
                },
            },
            {
                "codigo": 200,
                "cuando": (
                    "Modo espera activo (con o sin carpeta elegida en el "
                    "kiosco todavia; y aunque ya se haya recibido un "
                    "estadillo antes, hasta que el kiosco reinicie la "
                    "espera)."
                ),
                "ejemplo": {
                    "esperando": True, "caducado": False,
                    "inspeccion": {"planta": "KL05"},
                    "fotos": {"total": 42, "primera": "2026-09-20T08:00:00",
                              "ultima": "2026-09-20T09:30:00", "calculando": False},
                    "recibido": False,
                    "caduca_en": "2026-09-20T10:00:00+02:00",
                    "segundos_restantes": 480,
                    "red": {"hostname": "raspi-kl05", "puerto": 80, "ips": [], "url": "http://raspi-kl05"},
                    "carpeta_seleccionada": True,
                    "carpeta": "/home/pi/vuelo/KL05",
                    "estadillo_en_carpeta": {"encontrado": False, "nombre": None, "buscando": False},
                },
            },
        ],
        "campos_respuesta": [
            {"campo": "esperando", "tipo": "bool", "nota": "Si el kiosco tiene el modo espera abierto."},
            {"campo": "caducado", "tipo": "bool", "nota": "Solo relevante cuando `esperando` es `false`: distingue \"nunca se inicio\" de \"se agoto el tiempo\"."},
            {"campo": "inspeccion", "tipo": "object", "nota": "Solo si `esperando` es `true`. Datos de la inspeccion elegida en el kiosco."},
            {"campo": "fotos", "tipo": "object", "nota": "Solo si `esperando` es `true`. Resumen de fotos detectadas en la carpeta."},
            {"campo": "recibido", "tipo": "bool", "nota": "Solo si `esperando` es `true`. `true` si ya se acepto un estadillo en esta espera (un POST mas dara 409)."},
            {"campo": "caduca_en", "tipo": "string (ISO 8601)", "nota": "Solo si `esperando` es `true`."},
            {"campo": "segundos_restantes", "tipo": "int", "nota": "Solo si `esperando` es `true`."},
            {"campo": "red", "tipo": "object", "nota": "Solo si `esperando` es `true`. `{hostname, puerto, ips, url}` de la Pi."},
            {"campo": "carpeta_seleccionada", "tipo": "bool", "nota": "Si ademas de la espera hay una carpeta de vuelo elegida y existente en disco."},
            {"campo": "carpeta", "tipo": "string|null", "nota": "Ruta de esa carpeta, o `null` si no hay ninguna."},
            {"campo": "aviso", "tipo": "string", "nota": "Solo presente si `esperando` es `true` y `carpeta_seleccionada` es `false`."},
            {"campo": "estadillo_en_carpeta", "tipo": "object", "nota": "Siempre presente (aunque no haya carpeta). `{encontrado, nombre, buscando}`: si `carpeta` no tiene fichero de estadillo detectable (`detectar_estadillos`), sin carpeta o mientras el escaneo en hilo sigue en marcha (`buscando: true`), `encontrado` es `false` y `nombre` es `null`."},
            {"campo": "resumen", "tipo": "object", "nota": "Solo si `recibido` es `true`. `{planta, fecha, pilotos[], drones[], n_vuelos}`."},
            {"campo": "validacion", "tipo": "object", "nota": "Solo si `recibido` es `true` y ya se calculo. Ver `POST /api/estadillo`."},
            {"campo": "ok", "tipo": "bool", "nota": "Solo presente cuando la respuesta es un 409: siempre `false`."},
            {"campo": "codigo", "tipo": "string", "nota": "Solo en 409: `sin_espera_activa` o `espera_caducada`."},
            {"campo": "motivo", "tipo": "string", "nota": "Solo en 409: frase en español lista para mostrar."},
        ],
        "curl": f"curl {HOST_EJEMPLO}{_RUTA_ESTADILLO_ESPERA}",
    }


def _endpoint_recibir() -> dict:
    from atom_core.webserver import _MAX_BODY_ESTADILLO, _RUTA_ESTADILLO_RECIBIR

    columnas, alias = _columnas_vuelo()

    return {
        "id": "recibir",
        "metodo": "POST",
        "ruta": _RUTA_ESTADILLO_RECIBIR,
        "resumen": "Entrega el estadillo de la jornada al Organizer para esta espera.",
        "auth": "Ninguna (sin token, con CORS abierto). Content-Type obligatorio `application/json`.",
        "request": {
            "content_type": "application/json",
            "limite_bytes": _MAX_BODY_ESTADILLO,
            "campos": [
                {"campo": "vuelos", "tipo": "list[object]", "obligatorio": True,
                 "nota": "No puede ir vacia. Cada elemento es un vuelo (FlightRecord de la app de Christian, 31 campos); los campos que no se reconocen se ignoran en silencio. `sync_uid` es opcional -si no se manda, ese vuelo simplemente no tiene id de sincronizacion aguas abajo- y `Hora_de_inicio`/`Hora_final` aceptan `HH:MM` o `HH:MM:SS`."},
            ],
            "columnas_vuelo_reconocidas": columnas,
            "alias_de_columna": alias,
            "ejemplo": {
                "vuelos": [
                    {"Fecha": "2026-09-20", "Piloto": "Rebeca", "dron": "M300",
                     "PB": "1", "Vuelo": "1", "Hora_de_inicio": "08:00:00", "Hora_final": "08:25:00",
                     "sync_uid": "2026-09-20_REBECA_V1_H080000_PB1"},
                    {"Fecha": "2026-09-20", "Piloto": "Rebeca", "dron": "M300",
                     "PB": "1", "Vuelo": "2", "Hora_de_inicio": "08:30", "Hora_final": "08:55",
                     "sync_uid": "2026-09-20_REBECA_V2_H0830_PB1"},
                ],
            },
        },
        "respuestas": [
            {
                "codigo": 200,
                "cuando": "El estadillo se acepto y valido correctamente. Si el kiosco todavia no tenia carpeta de vuelo elegida, se acepta igual -se guarda y se asocia a la espera- y la respuesta añade `pendiente_carpeta: true`. Antes de validar, el Organizer FILTRA los vuelos recibidos a los que corresponden a la tarjeta elegida (`vuelos_recibidos`/`vuelos_en_tarjeta`/`vuelos_descartados` en `resumen`): la app de Christian manda toda la campaña, no solo la SD que se esta organizando.",
                "ejemplo": {"ok": True, "resumen": {
                    "planta": "KL05", "fecha": "2026-09-20",
                    "pilotos": ["Rebeca"], "drones": ["M300"], "n_vuelos": 1,
                    "vuelos_recibidos": 2, "vuelos_en_tarjeta": 1,
                    "vuelos_descartados": [
                        {"pb": "1", "vuelo": "2", "hora_inicio": "08:30", "hora_fin": "08:55"},
                    ],
                    "avisos": [
                        "1 vuelo del estadillo descartado(s) por tarjeta: su horario no coincide "
                        "con ninguna foto de la carpeta elegida.",
                    ],
                }, "validacion": {
                    "ok": False, "pendiente": False,
                    "vuelos": [
                        {"id": "1-1", "nombre": "PB1 · Vuelo 1",
                         "inicio": "2026-09-20T07:58:00", "fin": "2026-09-20T08:27:00",
                         "fotos": 40, "esperado": None, "estado": "ok"},
                    ],
                    "fotos_fuera": 2,
                    "avisos": ["2 fotos fuera del horario de cualquier vuelo."],
                }},
            },
            {
                "codigo": 409,
                "cuando": "No hay modo espera activo en el kiosco (nunca se inicio -`sin_espera_activa`-, o caduco -`espera_caducada`-).",
                "ejemplo": {"ok": False, "codigo": "sin_espera_activa",
                            "motivo": "El kiosco no tiene ninguna espera de estadillo activa. Inícala desde el kiosco antes de enviar.",
                            "esperando": False, "caducado": False},
            },
            {
                "codigo": 409,
                "cuando": "Ya se habia recibido un estadillo valido en esta espera; el kiosco tiene que reiniciarla antes de aceptar otro POST.",
                "ejemplo": {"ok": False, "codigo": "estadillo_ya_recibido",
                            "motivo": "Ya se recibió el estadillo para esta espera; reinicia la espera desde el kiosco.",
                            "esperando": True, "recibido": True,
                            "errores": ["Ya se recibió el estadillo; reinicia la espera desde el kiosco."]},
            },
            {
                "codigo": 415,
                "cuando": "La cabecera `Content-Type` no es exactamente `application/json` (`codigo: content_type_invalido`).",
                "ejemplo": {"ok": False, "codigo": "content_type_invalido",
                            "motivo": "El Content-Type debe ser application/json.",
                            "errores": ["Content-Type debe ser application/json"]},
            },
            {
                "codigo": 400,
                "cuando": "`Content-Length` invalido, no numerico o negativo (`codigo: content_length_invalido`).",
                "ejemplo": {"ok": False, "codigo": "content_length_invalido",
                            "motivo": "El encabezado Content-Length no es válido.",
                            "errores": ["Content-Length invalido"]},
            },
            {
                "codigo": 400,
                "cuando": "El cuerpo no es JSON valido (`codigo: json_invalido`).",
                "ejemplo": {"ok": False, "codigo": "json_invalido",
                            "motivo": "El cuerpo enviado no es JSON válido.",
                            "errores": ["JSON invalido"]},
            },
            {
                "codigo": 413,
                "cuando": f"El cuerpo supera {_MAX_BODY_ESTADILLO // (1024 * 1024)} MB (`codigo: cuerpo_demasiado_grande`).",
                "ejemplo": {"ok": False, "codigo": "cuerpo_demasiado_grande",
                            "motivo": "El cuerpo del estadillo supera el tamaño máximo permitido.",
                            "errores": ["cuerpo demasiado grande"]},
            },
            {
                "codigo": 422,
                "cuando": "Falta `vuelos` o es una lista vacia (`codigo: faltan_vuelos`).",
                "ejemplo": {"ok": False, "codigo": "faltan_vuelos",
                            "motivo": "Falta la lista 'vuelos' (no puede estar vacía).",
                            "errores": ["falta 'vuelos' (lista no vacia)"]},
            },
            {
                "codigo": 422,
                "cuando": (
                    "El propio validador del estadillo rechaza el contenido "
                    "(por ejemplo, ninguna columna reconocida, o faltan PB/"
                    "Vuelo/fecha/hora en todas las filas) (`codigo: "
                    "validacion_estadillo`; `motivo` trae el detalle del "
                    "validador)."
                ),
                "ejemplo": {"ok": False, "codigo": "validacion_estadillo",
                            "motivo": "cabecera de vuelo desconocida",
                            "errores": ["cabecera de vuelo desconocida"]},
            },
            {
                "codigo": 500,
                "cuando": "Error interno al procesar el estadillo (no se filtra el detalle al cliente; `codigo: error_interno`).",
                "ejemplo": {"ok": False, "codigo": "error_interno",
                            "motivo": "Error interno del Organizer al procesar el estadillo.",
                            "errores": ["error interno al procesar el estadillo"]},
            },
        ],
        "campos_respuesta": [
            {"campo": "ok", "tipo": "bool", "nota": "Presente en toda respuesta."},
            {"campo": "codigo", "tipo": "string", "nota": "Presente cuando `ok` es `false`. Slug estable para tratar el error por codigo (ver lista de respuestas)."},
            {"campo": "motivo", "tipo": "string", "nota": "Presente cuando `ok` es `false`. Frase en español, lista para mostrar al usuario."},
            {"campo": "resumen", "tipo": "object", "nota": "Solo si `ok` es `true`. `{planta, fecha, fechas[], pilotos[], drones[], n_vuelos, vuelos[], vuelos_recibidos, vuelos_en_tarjeta, vuelos_descartados[], avisos[]}`. `n_vuelos`/`vuelos` son YA los de la tarjeta (tras filtrar); `vuelos_recibidos` es el total que mando la app ANTES de filtrar. Si no se pudo leer el EXIF de la carpeta (o no hay carpeta elegida todavia) no se filtra nada -`vuelos_en_tarjeta == vuelos_recibidos`- y `avisos` lo indica."},
            {"campo": "validacion", "tipo": "object", "nota": "Solo si `ok` es `true`. Compara los vuelos del estadillo con las fotos EXIF de la carpeta elegida: `{ok, pendiente, vuelos:[{id,nombre,inicio,fin,fotos,esperado,estado}], fotos_fuera, avisos}` (`estado` en `ok|sin_fotos|pocas_fotos|solapado`). `pendiente: true` si el escaneo de fotos no ha terminado en 5s (reintentar con `GET /api/estadillo/espera`). Informativo: nunca hace fallar el POST."},
            {"campo": "errores", "tipo": "list[string]", "nota": "Presente cuando `ok` es `false` y el motivo es de validacion (409 sin carpeta es la excepcion: usa `aviso`)."},
            {"campo": "esperando", "tipo": "bool", "nota": "Presente en los 409 de modo espera."},
            {"campo": "recibido", "tipo": "bool", "nota": "Presente cuando el 409 es \"ya se habia recibido\"."},
            {"campo": "carpeta_seleccionada", "tipo": "bool", "nota": "Presente cuando el 409 es \"sin carpeta seleccionada\"."},
            {"campo": "aviso", "tipo": "string", "nota": "Presente cuando el 409 es \"sin carpeta seleccionada\"."},
        ],
        "curl": (
            f"curl -X POST {HOST_EJEMPLO}{_RUTA_ESTADILLO_RECIBIR} "
            "-H 'Content-Type: application/json' "
            "-d '{\"vuelos\":[{\"Fecha\":\"2026-09-20\",\"Piloto\":\"Rebeca\","
            "\"PB\":\"1\",\"Vuelo\":\"1\"}]}'"
        ),
    }


_AUTH_CONTROL = (
    "PIN del kiosco por cabecera `X-Atom-Pin` (mismo PIN de la pantalla "
    "tactil, verificado con la misma logica de verificacion de PIN). Sin PIN "
    "configurado en el kiosco -> 403 `pin_no_configurado`. Cabecera ausente "
    "o PIN incorrecto -> 401 `pin_invalido`. 5 fallos seguidos desde la "
    "misma IP -> esa IP queda bloqueada 60 s (429 `pin_bloqueado`), aparte "
    "del bloqueo del teclado tactil del kiosco."
)

_RESPUESTAS_PIN_CONTROL = [
    {"codigo": 401, "cuando": "Sin cabecera `X-Atom-Pin`, o con un PIN incorrecto.",
     "ejemplo": {"ok": False, "codigo": "pin_invalido"}},
    {"codigo": 403, "cuando": "El kiosco todavia no tiene PIN configurado.",
     "ejemplo": {"ok": False, "codigo": "pin_no_configurado"}},
    {"codigo": 429, "cuando": "5 fallos seguidos desde esta IP: bloqueada 60 s.",
     "ejemplo": {"ok": False, "codigo": "pin_bloqueado"}},
]


def _endpoint_control_discos() -> dict:
    from atom_core.webserver import _RUTA_CONTROL_DISCOS

    return {
        "id": "control_discos",
        "metodo": "GET",
        "ruta": _RUTA_CONTROL_DISCOS,
        "resumen": "Discos externos montados en la Pi: nivel superior del selector de carpeta del kiosco.",
        "auth": _AUTH_CONTROL,
        "request": None,
        "respuestas": [
            {"codigo": 200, "cuando": "Siempre, con PIN valido.",
             "ejemplo": {"discos": [{"name": "USB1", "path": "/media/usb1", "libre_gb": 12.3, "total_gb": 64.0}]}},
            *_RESPUESTAS_PIN_CONTROL,
        ],
        "campos_respuesta": [
            {"campo": "discos", "tipo": "list[object]", "nota": "`{name, path, libre_gb, total_gb}` de cada disco externo montado ahora mismo."},
        ],
        "curl": f"curl -H 'X-Atom-Pin: 1234' {HOST_EJEMPLO}{_RUTA_CONTROL_DISCOS}",
    }


def _endpoint_control_carpetas() -> dict:
    from atom_core.webserver import _RUTA_CONTROL_CARPETAS

    return {
        "id": "control_carpetas",
        "metodo": "GET",
        "ruta": _RUTA_CONTROL_CARPETAS,
        "resumen": "Subcarpetas de `path` (confinado a los discos externos montados; sin `path`, la lista de discos).",
        "auth": _AUTH_CONTROL,
        "request": None,
        "respuestas": [
            {"codigo": 200, "cuando": "`path` cae dentro de un disco externo montado.",
             "ejemplo": {"ok": True, "path": "/media/usb1/vuelo", "parent": "/media/usb1",
                         "dirs": [{"name": "sub", "path": "/media/usb1/vuelo/sub"}], "files": [],
                         "is_root": False, "disk_name": "USB1", "rel_parts": ["vuelo"]}},
            {"codigo": 400, "cuando": "`path` fuera de los discos montados, invalido, o no es una carpeta.",
             "ejemplo": {"ok": False, "codigo": "path_no_permitido", "error": "Fuera de los discos externos montados."}},
            *_RESPUESTAS_PIN_CONTROL,
        ],
        "campos_respuesta": [
            {"campo": "ok", "tipo": "bool", "nota": "Presente en toda respuesta."},
            {"campo": "dirs", "tipo": "list[object]", "nota": "Solo si `ok`. Subcarpetas directas de `path`."},
            {"campo": "files", "tipo": "list[object]", "nota": "Solo si `ok`. Ficheros directos de `path`."},
            {"campo": "codigo", "tipo": "string", "nota": "Presente cuando `ok` es `false`: `path_no_permitido`."},
        ],
        "curl": f"curl -H 'X-Atom-Pin: 1234' '{HOST_EJEMPLO}{_RUTA_CONTROL_CARPETAS}?path=/media/usb1/vuelo'",
    }


def _endpoint_control_carpeta() -> dict:
    from atom_core.webserver import _RUTA_CONTROL_CARPETA

    return {
        "id": "control_carpeta",
        "metodo": "POST",
        "ruta": _RUTA_CONTROL_CARPETA,
        "resumen": (
            "Fija la carpeta de trabajo, como si se eligiera desde el kiosco. "
            "Si hay una espera de estadillo (o un estadillo pendiente de "
            "carpeta) en curso, se entera sola, sin reiniciar su caducidad. "
            "La UI del kiosco se entera por SSE con el evento "
            "`atom:control_carpeta` (`{path}`)."
        ),
        "auth": _AUTH_CONTROL,
        "request": {
            "content_type": "application/json",
            "limite_bytes": 10 * 1024 * 1024,
            "campos": [
                {"campo": "path", "tipo": "string", "obligatorio": True, "nota": "Ruta dentro de un disco externo montado."},
            ],
            "ejemplo": {"path": "/media/usb1/vuelo/KL05"},
        },
        "respuestas": [
            {"codigo": 200, "cuando": "`path` valido.", "ejemplo": {"ok": True, "path": "/media/usb1/vuelo/KL05"}},
            {"codigo": 400, "cuando": "Falta `path`, o cae fuera de los discos montados.",
             "ejemplo": {"ok": False, "codigo": "path_no_permitido", "error": "Fuera de los discos externos montados."}},
            *_RESPUESTAS_PIN_CONTROL,
        ],
        "campos_respuesta": [
            {"campo": "ok", "tipo": "bool", "nota": "Presente en toda respuesta."},
            {"campo": "path", "tipo": "string", "nota": "Solo si `ok`: la carpeta fijada."},
            {"campo": "codigo", "tipo": "string", "nota": "Presente cuando `ok` es `false`: `path_no_permitido`."},
        ],
        "curl": (
            f"curl -X POST {HOST_EJEMPLO}{_RUTA_CONTROL_CARPETA} "
            "-H 'X-Atom-Pin: 1234' -H 'Content-Type: application/json' "
            "-d '{\"path\":\"/media/usb1/vuelo/KL05\"}'"
        ),
    }


def _endpoint_control_organizar() -> dict:
    from atom_core.webserver import _RUTA_CONTROL_ORGANIZAR

    return {
        "id": "control_organizar",
        "metodo": "POST",
        "ruta": _RUTA_CONTROL_ORGANIZAR,
        "resumen": (
            "Arranca \"Organizar\" sobre la carpeta fijada con `POST /api/control/carpeta`, "
            "con los mismos parametros por defecto que el boton del kiosco "
            "(destino `<carpeta>_ORGANIZADO`, sin estadillo, con renombrado)."
        ),
        "auth": _AUTH_CONTROL,
        "request": None,
        "respuestas": [
            {"codigo": 200, "cuando": "Arranca el proceso (async; progreso por SSE `atom:progress`).",
             "ejemplo": {"ok": True, "started": True}},
            {"codigo": 409, "cuando": "No se ha fijado ninguna carpeta todavia.",
             "ejemplo": {"ok": False, "codigo": "sin_carpeta"}},
            {"codigo": 409, "cuando": "Ya hay un proceso en curso.",
             "ejemplo": {"ok": False, "codigo": "en_curso"}},
            *_RESPUESTAS_PIN_CONTROL,
        ],
        "campos_respuesta": [
            {"campo": "ok", "tipo": "bool", "nota": "Presente en toda respuesta."},
            {"campo": "started", "tipo": "bool", "nota": "Solo si `ok`."},
            {"campo": "codigo", "tipo": "string", "nota": "Presente cuando `ok` es `false`: `sin_carpeta` o `en_curso`."},
        ],
        "curl": f"curl -X POST {HOST_EJEMPLO}{_RUTA_CONTROL_ORGANIZAR} -H 'X-Atom-Pin: 1234'",
    }


def _endpoint_control_estado() -> dict:
    from atom_core.webserver import _RUTA_CONTROL_ESTADO

    return {
        "id": "control_estado",
        "metodo": "GET",
        "ruta": _RUTA_CONTROL_ESTADO,
        "resumen": "Foto del estado actual del control remoto: carpeta fijada, estadillo, proceso en curso.",
        "auth": _AUTH_CONTROL,
        "request": None,
        "respuestas": [
            {"codigo": 200, "cuando": "Siempre, con PIN valido.",
             "ejemplo": {"carpeta": "/media/usb1/vuelo/KL05",
                         "estadillo": {"encontrado": True, "nombre": "estadillo.csv", "buscando": False},
                         "en_curso": False, "fase": None, "progreso": None, "ultimo_error": None}},
            *_RESPUESTAS_PIN_CONTROL,
        ],
        "campos_respuesta": [
            {"campo": "carpeta", "tipo": "string|null", "nota": "Ultima carpeta fijada por `POST /api/control/carpeta`, o `null`."},
            {"campo": "estadillo", "tipo": "object|null", "nota": "`{encontrado, nombre, buscando}` de la carpeta fijada, o `null` sin carpeta."},
            {"campo": "en_curso", "tipo": "bool", "nota": "Si hay un proceso de organizar/analizar en marcha."},
            {"campo": "fase", "tipo": "string|null", "nota": "Sin estado retenido server-side (el progreso viaja solo por SSE): siempre `null`."},
            {"campo": "progreso", "tipo": "int|null", "nota": "Igual que `fase`: siempre `null`."},
            {"campo": "ultimo_error", "tipo": "string|null", "nota": "Igual que `fase`: siempre `null`."},
        ],
        "curl": f"curl -H 'X-Atom-Pin: 1234' {HOST_EJEMPLO}{_RUTA_CONTROL_ESTADO}",
    }


def _endpoint_control_cancelar() -> dict:
    from atom_core.webserver import _RUTA_CONTROL_CANCELAR

    return {
        "id": "control_cancelar",
        "metodo": "POST",
        "ruta": _RUTA_CONTROL_CANCELAR,
        "resumen": "Pide cancelar el proceso/analisis en curso.",
        "auth": _AUTH_CONTROL,
        "request": None,
        "respuestas": [
            {"codigo": 200, "cuando": "Siempre, con PIN valido.", "ejemplo": {"ok": True}},
            *_RESPUESTAS_PIN_CONTROL,
        ],
        "campos_respuesta": [
            {"campo": "ok", "tipo": "bool", "nota": "Siempre `true`: pide la cancelacion, no confirma que ya haya parado."},
        ],
        "curl": f"curl -X POST {HOST_EJEMPLO}{_RUTA_CONTROL_CANCELAR} -H 'X-Atom-Pin: 1234'",
    }


def _endpoint_control_login() -> dict:
    from atom_core.webserver import _RUTA_CONTROL_LOGIN

    return {
        "id": "control_login",
        "metodo": "POST",
        "ruta": _RUTA_CONTROL_LOGIN,
        "resumen": (
            "Valida el PIN del kiosco sin mutar nada mas: es el paso de "
            "\"entrar\" al panel de control remoto, primer paso del flujo "
            "recomendado. Si el kiosco estaba bloqueado, la pantalla anima "
            "en la pantalla los 4 puntos del PIN uno a uno (cada ~250 ms), "
            "sin revelar nunca los digitos reales, y se desbloquea al "
            "terminar; si ya estaba desbloqueada no se ve nada en pantalla, "
            "solo confirma el PIN. En cualquier caso deja visible el marco "
            "azul de \"Control remoto\" (ver notas generales)."
        ),
        "auth": _AUTH_CONTROL,
        "request": None,
        "respuestas": [
            {"codigo": 200, "cuando": "PIN correcto.", "ejemplo": {"ok": True}},
            *_RESPUESTAS_PIN_CONTROL,
        ],
        "campos_respuesta": [
            {"campo": "ok", "tipo": "bool", "nota": "Presente en toda respuesta."},
            {"campo": "codigo", "tipo": "string", "nota": "Presente cuando `ok` es `false`: ver auth."},
        ],
        "curl": f"curl -X POST {HOST_EJEMPLO}{_RUTA_CONTROL_LOGIN} -H 'X-Atom-Pin: 1234'",
    }


def especificacion() -> dict:
    """Estructura completa que sirve tanto `GET /api/docs` (HTML) como
    `GET /api/docs?format=json`. Unica fuente para ambos formatos."""
    return {
        "titulo": TITULO,
        "host_ejemplo": HOST_EJEMPLO,
        "puerto_alternativo": PUERTO_ALTERNATIVO,
        "notas": [
            "Las 3 rutas `/api/estadillo/*` no piden token ni comprueban el "
            "origen: son el contrato publico de la LAN para la app "
            "\"Estadillo Digital\". Las rutas `/api/control/*` si piden el "
            "PIN del kiosco (cabecera `X-Atom-Pin`, NUNCA en la URL): son el "
            "panel de control remoto (movil/tablet operando el kiosco sin "
            "tocar la pantalla). El resto de la API del Organizer es "
            "interna y no se documenta aqui.",
            "Todas las rutas `/api/control/*` que mutan algo (login, "
            "carpeta, organizar, cancelar) emiten ademas el evento SSE "
            "unificado `atom:control_ui` (`{accion, path?}`, `accion` en "
            "`login|carpeta|organizar|cancelar`) tras la mutacion real. La "
            "pantalla del kiosco lo reproduce como si lo hiciera una "
            "persona delante: navega a Organizer, va resaltando cada "
            "carpeta del recorrido hasta la elegida, y resalta el boton "
            "Organizar o Cancelar segun la accion. Si el kiosco esta "
            "bloqueado con PIN, las acciones se encolan y se reproducen en "
            "orden en cuanto se desbloquea (con el propio `login` remoto, o "
            "a mano desde la pantalla).",
            "Mientras el kiosco reproduce o recibe estas acciones se ve un "
            "marco azul \"Control remoto\" a pantalla completa, tambien "
            "encima del bloqueo de PIN: pleno mientras llegan eventos; tras "
            "15 s sin eventos se atenua en ~5 s a un estado tenue que se "
            "queda asi (ya NO se desmonta solo); un evento nuevo lo vuelve "
            "a poner pleno; un toque o tecla local es la unica forma de "
            "quitarlo, al instante, sin esperar la atenuacion.",
            "El login remoto (accion `login`) NUNCA pinta el teclado: solo "
            "se ven los 4 puntos del PIN rellenandose uno a uno, sin "
            "resaltar ni animar ninguna tecla, para que quien mire la "
            "pantalla no pueda leer que se esta tecleando.",
            "El POST a `/api/estadillo` puede llegar antes de que el "
            "kiosco tenga carpeta elegida: se acepta igual (devuelve "
            "`pendiente_carpeta: true`) y se aplica solo en cuanto se fija "
            "la carpeta, a mano o con `POST /api/control/carpeta`.",
            "Seguridad del PIN de control (misma verificacion que la "
            "pantalla tactil): sin cabecera `X-Atom-Pin` o PIN incorrecto -> "
            "401 `pin_invalido`; kiosco sin PIN configurado todavia -> 403 "
            "`pin_no_configurado`; 5 fallos seguidos desde la misma IP -> "
            "esa IP bloqueada 60 s con 429 `pin_bloqueado` (aparte del "
            "bloqueo del teclado tactil del kiosco).",
            f"Usa siempre el host `organizer.atom` (alias `organizer.local` "
            f"por mDNS), nunca una IP: la Pi puede cambiar de IP entre "
            f"jornadas. El puerto 80 redirige al servidor real; tambien vale "
            f"poniendo el puerto explicito (`:{PUERTO_ALTERNATIVO}`).",
        ],
        "flujo_recomendado": [
            "1. GET /api/estadillo/ping repetido (escaneo IP a IP si hace "
            "falta) hasta encontrar una respuesta con `organizer: true`: esa "
            "es la Pi. "
            "`curl http://organizer.local/api/estadillo/ping`",
            "2. GET /api/estadillo/espera en bucle hasta ver `esperando: "
            "true` Y `carpeta_seleccionada: true`. Un 409 \"sin carpeta "
            "seleccionada\" NO consume nada: se reintenta sin mas. "
            "`curl http://organizer.local/api/estadillo/espera`",
            "3. POST /api/estadillo con `{\"vuelos\": [...]}` una sola vez. "
            "`curl -X POST http://organizer.local/api/estadillo "
            "-H 'Content-Type: application/json' "
            "-d '{\"vuelos\":[{\"Fecha\":\"2026-09-20\",\"Piloto\":\"Rebeca\","
            "\"PB\":\"1\",\"Vuelo\":\"1\"}]}'`",
            "4. Interpretar la respuesta: 200 -> aceptado (mostrar "
            "`resumen`; si trae `pendiente_carpeta: true`, avisar que se "
            "aplicara al fijar carpeta); 409 \"ya se habia recibido\" -> no "
            "reintentar, pedir al kiosco que reinicie la espera; 422 -> "
            "corregir el estadillo y reintentar el POST; 415/400/413 -> "
            "error del propio cliente.",
            "5. Panel de control remoto (opcional, requiere PIN por "
            "cabecera): `POST /api/control/login` para desbloquear el "
            "kiosco si hace falta -- "
            "`curl -X POST http://organizer.local/api/control/login "
            "-H 'X-Atom-Pin: 1234'` -- luego `GET /api/control/discos` -- "
            "`curl -H 'X-Atom-Pin: 1234' http://organizer.local/api/control/discos` "
            "-- y `GET /api/control/carpetas?path=...` para navegar hasta "
            "la carpeta del vuelo -- "
            "`curl -H 'X-Atom-Pin: 1234' "
            "'http://organizer.local/api/control/carpetas?path=/media/usb1'`.",
            "6. Fijar la carpeta con `POST /api/control/carpeta` -- "
            "`curl -X POST http://organizer.local/api/control/carpeta "
            "-H 'X-Atom-Pin: 1234' -H 'Content-Type: application/json' "
            "-d '{\"path\":\"/media/usb1/vuelo/KL05\"}'` -- despues "
            "`GET /api/control/estado` para confirmar carpeta y estadillo "
            "detectados -- "
            "`curl -H 'X-Atom-Pin: 1234' http://organizer.local/api/control/estado` "
            "-- y por ultimo `POST /api/control/organizar` para arrancar "
            "(o `POST /api/control/cancelar` para pararlo) -- "
            "`curl -X POST http://organizer.local/api/control/organizar "
            "-H 'X-Atom-Pin: 1234'`.",
        ],
        "endpoints": [
            _endpoint_ping(), _endpoint_espera(), _endpoint_recibir(),
            _endpoint_control_discos(), _endpoint_control_carpetas(), _endpoint_control_carpeta(),
            _endpoint_control_organizar(), _endpoint_control_estado(), _endpoint_control_cancelar(),
            _endpoint_control_login(),
        ],
    }


def _escape(valor) -> str:
    from html import escape

    return escape(str(valor), quote=True)


def _json_bloque(dato) -> str:
    import json

    return _escape(json.dumps(dato, ensure_ascii=False, indent=2))


def _render_campos(campos: list[dict], columnas_extra: tuple[str, ...] = ("campo", "tipo", "nota")) -> str:
    filas = "".join(
        "<tr>" + "".join(f"<td>{_escape(c.get(col, ''))}</td>" for col in columnas_extra) + "</tr>"
        for c in campos
    )
    cabecera = "".join(f"<th>{_escape(col)}</th>" for col in columnas_extra)
    return f"<table><thead><tr>{cabecera}</tr></thead><tbody>{filas}</tbody></table>"


def _render_probador(ep: dict) -> str:
    """Bloque interactivo del endpoint: ejecutar GET, o editar+enviar POST,
    mas boton de copiar curl. Todo JS inline vanilla (sin CDNs, la Pi puede
    estar sin internet): ver `_JS` para la logica compartida."""
    eid = _escape(ep["id"])
    metodo = ep["metodo"]
    ruta = _escape(ep["ruta"])
    partes = ['<div class="probador">']

    if metodo == "GET":
        partes.append(
            f'<button type="button" class="btn-ejecutar" '
            f'data-metodo="GET" data-ruta="{ruta}" data-target="{eid}">Ejecutar</button>'
        )
    else:
        ejemplo = ""
        request = ep.get("request")
        if request and request.get("ejemplo") is not None:
            import json

            ejemplo = json.dumps(request["ejemplo"], ensure_ascii=False, indent=2)
        partes.append(f'<textarea class="body-editable" id="body-{eid}" rows="6" spellcheck="false">{_escape(ejemplo)}</textarea>')
        partes.append(
            f'<button type="button" class="btn-ejecutar btn-enviar" '
            f'data-metodo="{_escape(metodo)}" data-ruta="{ruta}" data-target="{eid}" '
            f'data-body-id="body-{eid}">Enviar</button>'
        )

    partes.append(
        f'<button type="button" class="btn-curl" data-curl="{_escape(ep["curl"])}">Copiar curl</button>'
    )
    partes.append(f'<div class="resultado" id="resultado-{eid}" hidden></div>')
    partes.append("</div>")
    return "\n".join(partes)


def _render_endpoint(ep: dict) -> str:
    partes = [
        f'<section class="endpoint" id="{_escape(ep["id"])}">',
        f'<h2><span class="metodo metodo-{_escape(ep["metodo"].lower())}">{_escape(ep["metodo"])}</span> '
        f'<code>{_escape(ep["ruta"])}</code></h2>',
        f'<p class="resumen">{_escape(ep["resumen"])}</p>',
        f'<p class="auth"><strong>Autenticacion:</strong> {_escape(ep["auth"])}</p>',
        _render_probador(ep),
    ]

    request = ep.get("request")
    if request:
        partes.append("<h3>Request</h3>")
        partes.append(f'<p><strong>Content-Type:</strong> <code>{_escape(request["content_type"])}</code> '
                       f'&middot; <strong>limite:</strong> {request["limite_bytes"] // (1024 * 1024)} MB</p>')
        partes.append(_render_campos(request["campos"], ("campo", "tipo", "obligatorio", "nota")))
        if request.get("columnas_vuelo_reconocidas"):
            cols = ", ".join(f"<code>{_escape(c)}</code>" for c in request["columnas_vuelo_reconocidas"])
            partes.append(f"<p><strong>Columnas reconocidas de cada vuelo</strong> (el resto se ignora en silencio): {cols}</p>")
        if request.get("alias_de_columna"):
            alias = ", ".join(f"<code>{_escape(k)}</code> &rarr; <code>{_escape(v)}</code>"
                               for k, v in request["alias_de_columna"].items())
            partes.append(f"<p><strong>Alias aceptados:</strong> {alias}</p>")
        partes.append("<p><strong>Ejemplo de request:</strong></p>")
        partes.append(f'<pre>{_json_bloque(request["ejemplo"])}</pre>')
    else:
        partes.append("<h3>Request</h3><p>Sin cuerpo.</p>")

    partes.append("<h3>Respuestas</h3>")
    for r in ep["respuestas"]:
        partes.append(f'<div class="respuesta"><p><span class="codigo codigo-{r["codigo"] // 100}">{r["codigo"]}</span> {_escape(r["cuando"])}</p>')
        partes.append(f'<pre>{_json_bloque(r["ejemplo"])}</pre></div>')

    partes.append("<h3>Campos de la respuesta</h3>")
    partes.append(_render_campos(ep["campos_respuesta"]))

    partes.append("<h3>Ejemplo curl</h3>")
    partes.append(f'<pre>{_escape(ep["curl"])}</pre>')
    partes.append("</section>")
    return "\n".join(partes)


_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; background: #0a0a0a; color: #f5f5f5;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.5; padding: 2rem 1.5rem 4rem;
}
main { max-width: 56rem; margin: 0 auto; }
h1 { font-size: 1.6rem; margin-bottom: 0.25rem; }
h1 .marca { color: #EE763C; }
h2 { font-size: 1.15rem; margin-top: 0; }
h3 { font-size: 0.95rem; color: #d4d4d4; margin-bottom: 0.5rem; }
p, li { color: #d4d4d4; font-size: 0.92rem; }
code { background: rgba(255,255,255,0.08); padding: 0.1rem 0.35rem; border-radius: 0.25rem; font-size: 0.85em; }
pre {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1);
  border-radius: 0.5rem; padding: 0.9rem 1rem; overflow-x: auto; font-size: 0.82rem;
}
section.endpoint {
  border: 1px solid rgba(255,255,255,0.12); border-radius: 0.75rem;
  background: rgba(255,255,255,0.03); padding: 1.25rem 1.5rem; margin: 1.5rem 0;
}
.metodo { font-weight: 700; padding: 0.1rem 0.5rem; border-radius: 0.35rem; margin-right: 0.5rem; }
.metodo-get { background: rgba(96,165,250,0.2); color: #93c5fd; }
.metodo-post { background: rgba(238,118,60,0.2); color: #EE763C; }
.codigo { font-weight: 700; padding: 0.05rem 0.4rem; border-radius: 0.3rem; }
.codigo-2 { background: rgba(74,222,128,0.2); color: #86efac; }
.codigo-4 { background: rgba(250,204,21,0.2); color: #fde047; }
.codigo-5 { background: rgba(248,113,113,0.2); color: #fca5a5; }
table { width: 100%; border-collapse: collapse; margin: 0.75rem 0 1rem; font-size: 0.85rem; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid rgba(255,255,255,0.08); }
th { color: #a3a3a3; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.03rem; }
.flujo li { margin-bottom: 0.5rem; }
.notas li { margin-bottom: 0.4rem; }
.top-nav a { color: #EE763C; text-decoration: none; margin-right: 1rem; font-size: 0.85rem; }
.probador { margin: 1rem 0 1.25rem; padding: 0.9rem 1rem; border-radius: 0.5rem; background: rgba(255,255,255,0.02); border: 1px dashed rgba(255,255,255,0.14); }
.probador textarea.body-editable {
  width: 100%; min-height: 8rem; background: #0f0f0f; color: #f5f5f5;
  border: 1px solid rgba(255,255,255,0.15); border-radius: 0.4rem; padding: 0.6rem 0.7rem;
  font-family: ui-monospace, "Cascadia Code", Menlo, monospace; font-size: 0.82rem; margin-bottom: 0.6rem;
}
button.btn-ejecutar, button.btn-curl {
  font: inherit; font-size: 0.85rem; font-weight: 600; cursor: pointer; margin: 0 0.5rem 0.5rem 0;
  border: 1px solid rgba(238,118,60,0.5); border-radius: 0.4rem; padding: 0.4rem 0.9rem;
  background: rgba(238,118,60,0.15); color: #EE763C;
}
button.btn-ejecutar:hover, button.btn-curl:hover { background: rgba(238,118,60,0.3); }
button.btn-ejecutar:disabled { opacity: 0.5; cursor: wait; }
button.btn-curl { border-color: rgba(255,255,255,0.2); background: rgba(255,255,255,0.06); color: #d4d4d4; }
button.btn-curl:hover { background: rgba(255,255,255,0.12); }
.resultado { margin-top: 0.75rem; }
.resultado .meta { font-size: 0.8rem; color: #a3a3a3; margin-bottom: 0.4rem; }
.resultado .meta .ok { color: #86efac; }
.resultado .meta .error { color: #fca5a5; }
.resultado pre { white-space: pre-wrap; word-break: break-word; }
"""


_JS = """
(function () {
  function fmtMs(ms) { return Math.round(ms) + ' ms'; }

  async function ejecutar(boton) {
    var ruta = boton.getAttribute('data-ruta');
    var metodo = boton.getAttribute('data-metodo');
    var target = boton.getAttribute('data-target');
    var contenedor = document.getElementById('resultado-' + target);
    var opciones = { method: metodo, headers: {} };

    if (metodo !== 'GET') {
      if (!window.confirm('Esto escribe en el Organizer real. \\u00bfContinuar?')) {
        return;
      }
      var bodyId = boton.getAttribute('data-body-id');
      var textarea = bodyId ? document.getElementById(bodyId) : null;
      var texto = textarea ? textarea.value : '';
      opciones.headers['Content-Type'] = 'application/json';
      opciones.body = texto;
    }

    boton.disabled = true;
    contenedor.hidden = false;
    contenedor.innerHTML = '';
    var meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = 'Ejecutando\\u2026';
    contenedor.appendChild(meta);

    var inicio = performance.now();
    try {
      var resp = await fetch(ruta, opciones);
      var ms = performance.now() - inicio;
      var texto2 = await resp.text();
      var cuerpo = texto2;
      try { cuerpo = JSON.stringify(JSON.parse(texto2), null, 2); } catch (e) { /* no era JSON */ }
      meta.innerHTML = '';
      var spanCodigo = document.createElement('span');
      spanCodigo.className = resp.ok ? 'ok' : 'error';
      spanCodigo.textContent = resp.status + ' ' + resp.statusText;
      meta.appendChild(spanCodigo);
      meta.appendChild(document.createTextNode(' \\u00b7 ' + fmtMs(ms)));
      var pre = document.createElement('pre');
      pre.textContent = cuerpo;
      contenedor.appendChild(pre);
    } catch (e) {
      meta.innerHTML = '';
      var spanErr = document.createElement('span');
      spanErr.className = 'error';
      spanErr.textContent = 'Error de red: ' + e;
      meta.appendChild(spanErr);
    } finally {
      boton.disabled = false;
    }
  }

  function copiarCurl(boton) {
    var texto = boton.getAttribute('data-curl');
    var host = window.location.origin;
    texto = texto.replace(/https?:\\/\\/organizer\\.local(:\\d+)?/, host);
    var listo = function () { boton.textContent = 'Copiado'; setTimeout(function () { boton.textContent = 'Copiar curl'; }, 1500); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(listo, function () { window.prompt('Copia el comando:', texto); });
    } else {
      window.prompt('Copia el comando:', texto);
    }
  }

  document.addEventListener('click', function (ev) {
    var boton = ev.target.closest ? ev.target.closest('button') : null;
    if (!boton) return;
    if (boton.classList.contains('btn-ejecutar')) {
      ejecutar(boton);
    } else if (boton.classList.contains('btn-curl')) {
      copiarCurl(boton);
    }
  });
})();
"""


def render_html(spec: dict) -> str:
    endpoints_html = "\n".join(_render_endpoint(ep) for ep in spec["endpoints"])
    notas_html = "".join(f"<li>{_escape(n)}</li>" for n in spec["notas"])
    flujo_html = "".join(f"<li>{_escape(f)}</li>" for f in spec["flujo_recomendado"])
    nav_html = "".join(
        f'<a href="#{_escape(ep["id"])}">{_escape(ep["metodo"])} {_escape(ep["ruta"])}</a>'
        for ep in spec["endpoints"]
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_escape(spec["titulo"])}</title>
<style>{_CSS}</style>
</head>
<body>
<main>
  <h1><span class="marca">ATOM</span> Organizer &middot; {_escape(spec["titulo"])}</h1>
  <p>Host de ejemplo: <code>{_escape(spec["host_ejemplo"])}</code>
  (alternativa con puerto explicito: <code>:{spec["puerto_alternativo"]}</code>).
  Tambien disponible en <a href="/api/docs?format=json"><code>?format=json</code></a>.</p>
  <nav class="top-nav">{nav_html}</nav>
  <h3>Notas</h3>
  <ul class="notas">{notas_html}</ul>
  <h3>Flujo recomendado</h3>
  <ol class="flujo">{flujo_html}</ol>
  {endpoints_html}
</main>
<script>{_JS}</script>
</body>
</html>
"""
