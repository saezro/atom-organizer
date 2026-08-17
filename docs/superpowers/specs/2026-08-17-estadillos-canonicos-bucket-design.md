# Estadillos en ubicación canónica del bucket

Fecha: 2026-08-17
Estado: diseño aprobado en decisiones, pendiente de revisión del spec
Tarea: #3783 (proyecto Atom Organizer)

## Problema

El estadillo es hoy un fichero local que el operario adjunta en la app de
escritorio (`webui/src/EstadilloField.jsx:13,47`). Su identidad depende de que
el operario lo nombre bien y lo coloque en el sitio correcto, y eso no siempre
pasa. Consecuencias actuales:

- La Suite tiene que **adivinar** cuál es el estadillo de una operación:
  `descubrirEstadillo(origen)` → `elegirEstadillo` (`lib/organizer-estadillo.js`),
  cuyo resultado se guarda en `organizer_operaciones.estadillo`
  (`scripts/organizer/esquema-fase2.sql:55`, poblada en `server.js:2855`).
- El fichero crudo no se conserva en ningún sitio estable, así que una jornada
  no se puede reprocesar sin volver a pedirle el fichero al operario.
- El aviso a la Suite cuelga del *organizado*, no de la *subida*
  (`app_webview.py:691-693` → `_notificar_estadillo`, `:697-724`), y es
  fail-open total: sin estadillo (`:709-711`) o sin login (`:712-714`) retorna
  en silencio y el operario no ve error.

Esto último bloquea el rumbo cloud-first ([[Organizer cloud-first]]): si el
organizado se muda al Cloud Run Job, el camino que notifica el estadillo deja de
dispararse.

## Objetivo

El estadillo acaba **siempre** en una ruta determinista del bucket, calculada
por la app, nunca por el operario. Nadie tiene que adivinar dónde está ni cómo
se llama.

No objetivos (fuera de alcance de este spec):

- Portar el parser de estadillos de Python a la Suite.
- Resolver el fail-open sin reintentos ni cola de `RunReporter`
  (`atom_core/run_reporter.py:82-101`, `:298-320`). Se menciona porque este
  diseño lo agrava menos, no porque se arregle aquí.
- Cambiar el reparto en shards del Job.

## Decisiones tomadas

| Decisión | Elegido |
|---|---|
| Layout de la ruta | Histórico por subida + puntero `actual/` |
| Acoplamiento | Subida de estadillo es acción propia, independiente de organizar |
| Notificación a la Suite | Solo la subida del estadillo, igual en los dos modos |
| Identificador de carpeta | `<YYYY-MM-DD>T<HHMMSS>Z`, generado en local |
| Hash de contenido | MD5 base64, no sha256 |
| Parseo | Sigue en el escritorio, pero como **validador previo a la subida** |
| Formato | Se guarda el crudo **y** un normalizado de esquema fijo |

## Dos modos de operación, una sola ubicación

Los dos modos van a coexistir; esto no es una migración de uno a otro:

- **Modo local (hoy)**: el operario organiza en su ordenador, sube a Drive, y de
  Drive al bucket llega por el sync.
- **Modo RAW (cloud-first)**: se sube todo crudo y el organizado ocurre en el
  Cloud Run Job.

El estadillo **no viaja por ninguno de los dos caminos**. Al ser una acción
propia, la app lo sube directamente a `ESTADILLOS/`, con lo que la ubicación
canónica es **invariante al modo**. En particular, en modo local el estadillo no
va a Drive esperando que el sync lo coloque en su sitio: eso reintroduciría la
dependencia del nombre y de la carpeta que este diseño elimina.

Consecuencia de diseño: nada en el flujo del estadillo puede asumir que la
jornada está subida, ni que el organizado ha ocurrido, ni en qué máquina ocurre.

## Ruta canónica

```
gs://plantas_pv_nl/<PLANTA>/PREPARACION/ESTADILLOS/
    2026-08-14T091233Z/
        01__3f9c2e11.xlsx
        02__a71b0d54.csv
        manifest.json
    2026-08-17T034501Z/          <- vigente = max() del prefijo
        01__9f3c2e11.xlsx        <- crudo, tal cual lo entregó el operario
        estadillo.json           <- normalizado, esquema fijo
        manifest.json
    actual/                      <- reescrita en cada subida correcta
        01__9f3c2e11.xlsx
        estadillo.json
        manifest.json
```

### Formato canónico

Además del crudo se emite `estadillo.json`, resultado de la validación del paso
1, con esquema fijo y versionado:

```json
{
  "version": 1,
  "vuelos": [
    {
      "fecha": "2026-08-17",
      "piloto": "...",
      "equipo_vuelo": "...",
      "pb": "...",
      "num_vuelo": "...",
      "hora_inicio": "09:12:33",
      "hora_fin": "09:41:02",
      "origen": "..."
    }
  ]
}
```

El esquema es el mismo que ya acepta `POST /api/organizer/estadillo`
(`lib/estadillo-ingest.js:65-66`), para no inventar un tercer formato. Esto es lo
que hace que los estadillos sean legibles a futuro con independencia de que el
crudo sea xlsx hoy y csv mañana, o de que el cliente mueva las columnas: el crudo
se conserva como evidencia, el normalizado es el que se consume.

Cuando hay varios ficheros, `estadillo.json` es el resultado **fusionado** en el
orden de prioridad del manifest.

- `<PLANTA>` se compone con la función que ya existe,
  `prefijo_desde_carpeta()` (`atom_core/cloud_config.py:121-140`). No se añade
  una segunda forma de normalizar nombres de planta.
- `NN` es el orden de prioridad de la UI. Hoy ese orden vive solo en memoria,
  serializado con separador `\x1f` (`webui/src/App.jsx:276-277`); a partir de
  aquí queda **persistido** en el nombre y en el manifest.
- `<md5-8>` son los 8 primeros caracteres hex del MD5 del contenido. Da dedupe
  por contenido y trazabilidad: el nombre original del operario se conserva como
  metadato, nunca como identidad.
- El identificador de carpeta es fecha+hora UTC porque **no puede depender de la
  Suite**: el `run_id` lo genera el servidor en `POST /api/organizer/runs`
  (`atom_core/run_reporter.py:113-121`), y si el operario no tiene login el
  reporter es fail-open, así que no habría id con el que componer la ruta.
  Ordenable alfabéticamente ⇒ el vigente es `max()` del prefijo, sin consultar
  nada.
- `actual/` es una copia, no un symlink (GCS no tiene symlinks). Se reescribe
  entera en cada subida correcta.

### Manifest

`manifest.json`, un objeto por subida:

```json
{
  "version": 1,
  "planta": "MARISOLES_LOS_MANGOS",
  "subido_en": "2026-08-17T03:45:01Z",
  "subido_por": "daniel@...",
  "ficheros": [
    {
      "orden": 1,
      "objeto": "01__9f3c2e11.xlsx",
      "nombre_original": "Estadillo VUELOS 17 agosto (2).xlsx",
      "md5_b64": "nzwuEQ...",
      "bytes": 48213
    }
  ],
  "validacion": {
    "vuelos_detectados": 34,
    "filas_con_problemas": 0
  }
}
```

El manifest es la **fuente de verdad del orden de prioridad**. El consumidor
nunca infiere prioridad del nombre del fichero ni del listado del bucket.

## Flujo

### 1. Validar antes de subir

El operario elige N ficheros en el file-picker que ya existe
(`EstadilloField.jsx`, soporta N y reordenación desde `b0c3c77`). La app los
parsea en local con el parser actual y **muestra lo que ha entendido**: número
de vuelos, pilotos y PBs detectados, y las filas problemáticas.

- Si no parsea, **error visible y no se sube**. Esto es lo contrario del
  fail-open de `app_webview.py:709-714`, y es el requisito explícito: al subirlo
  tiene que confirmar que carga bien.
- El parseo local deja de ser el productor de datos y pasa a ser el validador.
  El crudo persistido en el bucket es lo que permitirá, más adelante y sin
  urgencia, mover el parser a la Suite y re-ingestar el histórico.

### 2. Subir

Nueva acción en el escritorio, hermana de las que ya existen y por tanto
desacoplada de organizar: hoy ya hay `cloud_prepare` (`app_webview.py:529`) y
`cloud_upload` (`app_webview.py:573`) fuera del camino de `split_images`.

La subida reutiliza `upload_file()` / `upload_plan()`
(`atom_core/cloud_upload.py:710`, `:834`) y el MD5 se calcula con
`_file_md5_b64()` (`atom_core/cloud_upload.py:677-687`), que ya está elegido
para casar con el `md5Hash` que expone la API de GCS — el manifest queda
verificable contra el bucket sin descargar nada.

Orden de escritura, importante para no dejar estado a medias:

1. Ficheros crudos de la carpeta con timestamp.
2. `estadillo.json` normalizado.
3. `manifest.json` de esa carpeta — **último**, así su presencia significa
   "subida completa".
4. `actual/` (ficheros, normalizado, y manifest al final).

Si la subida se corta, la carpeta queda sin manifest y ningún consumidor la
considera válida.

### 3. Notificar

Un único camino: la subida del estadillo, idéntico en modo local y en modo RAW.
Se **elimina** `_notificar_estadillo` del final del organizado
(`app_webview.py:691-693`) y el Job no notifica. Es imprescindible que muera ese
camino precisamente porque el modo local sigue vivo: si se dejaran los dos, cada
jornada organizada en local produciría doble ingesta del mismo estadillo.

La notificación sigue usando el endpoint actual, `POST /api/organizer/estadillo`
(`server.js:2308-2333`, guard `requireIngestOrganizer`), con el body de hoy
—`{vuelos: [...], planta_id, inspeccion_id}`— más un campo nuevo con la ruta del
manifest. La ingesta (`lib/estadillo-ingest.js:68-226`) no cambia: sigue
escribiendo `misiones`, `mision_plantas` y `vuelos` con su dedupe aplicativo por
`(mision_id, pb, num_vuelo, hora_inicio)`.

### 4. Consumir

`organizer_operaciones.estadillo` ya existe y ya guarda una ruta del bucket
(`esquema-fase2.sql:55`). No hace falta columna nueva: cambia **lo que se pone
dentro**. En vez del resultado de adivinar, la ruta del manifest canónico.

- La Suite lanza el Job (`server/organizer/organizerJob.js:35-48`) pasando la
  ruta del manifest, no un CSV suelto.
- `organize_cli.py` acepta un flag nuevo para el manifest y conserva
  `--estadillo` con rutas locales por compatibilidad.
- **El Job no descubre por prefijo.** Recibe la ruta. El descubrimiento parece
  cómodo y luego falla en silencio: dos jornadas el mismo día, o un residuo de
  un run abortado, y coge el estadillo equivocado sin avisar.
- `elegirEstadillo` (`lib/organizer-estadillo.js`) queda como camino de
  compatibilidad para operaciones antiguas, y deja de usarse para las nuevas.

## Errores y casos límite

| Caso | Comportamiento |
|---|---|
| El estadillo no parsea | Error visible en la UI, no se sube nada |
| Subida cortada a medias | Carpeta sin `manifest.json` ⇒ inválida para todo consumidor |
| Mismo fichero subido dos veces | Mismo `<md5-8>` ⇒ mismo objeto; el manifest nuevo lo referencia igual |
| Dos subidas en el mismo segundo | Colisión de carpeta. Se resuelve sufijando; poco probable con un operario por planta |
| Sin login en la Suite | La subida al bucket **sí ocurre** (la ruta no depende de la Suite); la notificación se pierde como hoy. El crudo queda en el bucket, así que es re-ingestable — hoy se perdía del todo |
| `actual/` desincronizada | Se reconstruye desde `max()` del prefijo; es caché, no fuente de verdad |

## Verificación

- Parseo/validación y composición de ruta: unitarios, sin red.
- Manifest: test de que el orden de la UI sobrevive al ida y vuelta.
- Ruta canónica: test de que `prefijo_desde_carpeta()` se usa como única fuente
  del `<PLANTA>`.
- Subida real: contra una planta de prueba, verificando el `md5Hash` que
  devuelve GCS contra el `md5_b64` del manifest.
- ⚠️ `gs://plantas_pv_nl` es **solo para plantas**. Las pruebas van bajo una
  planta real de prueba, y se limpian. Nunca a un prefijo inventado.
- La ingesta escribe en `aerotoolsDB`, que es **la misma BD en dev y prod**. Las
  pruebas de notificación no se hacen contra ids reales de plantas en
  producción.

## Riesgos

- El punto de mayor riesgo no es este diseño, es el que sigue abierto en el
  rumbo cloud-first: el **coste y tiempo de subir RAW antes de organizar**. Este
  spec es deliberadamente independiente de esa decisión —los dos modos coexisten
  y el estadillo se comporta igual en ambos—, así que puede entrar antes de
  resolverla.
- El modo local sigue vivo, y en él la única notificación es la de la subida del
  estadillo. Si un operario organiza sin haber subido estadillo, no hay ingesta
  — que es correcto, pero es un cambio de comportamiento observable.
- El normalizado se genera con el parser actual. Si ese parser tiene un fallo, el
  `estadillo.json` lo hereda; el crudo conservado al lado es lo que permite
  regenerarlo, y por eso guardar ambos no es redundancia.
