# LEDGER — Optimización a fondo de la fase RGB (ATOM Organizer)

Adopta el trabajo parado de `.workflow/LEDGER-rgb-lossless.md` (sesión muerta desde 2026-09-09
15:18, 2/13 items). Ese fichero NO se toca; este es el vigente.

## Clarified
- ¿Quién lleva esto? → Rodrigo (2026-09-10): lo lleva ESTA sesión. La otra está muerta.
- ¿OK al desplazamiento de ±16 px del `_CROP` a cambio de crop lossless? → **MEDIR PRIMERO**.
  Decide con el número delante, no antes.
- Alcance → "optimizar todo a fondo": no solo el crop lossless; también encodes redundantes,
  paralelismo y solape de fases.
- Baseline real: run KL19 (METLEN, 1012 img) con v3.4.91 → 13m46s total; RGB 6m49s (86% CPU),
  Térmicas 4m23s, Índice 52s, Cierre 1m41s. 8 núcleos.
- Config efectiva de RGB hoy (hardcode `organize.py:276-279`): compress_rgb=True,
  compress_level=40, cropping_rgb=True (auto por modelo), gen_thumbnails=True.

## Requisitos
- [x] Mapear el trabajo REAL por imagen en la fase RGB (nº de decodes y encodes, dónde) — archivo:línea
- [x] Conseguir una RGB real 8000x6000 de KL19 en la VM + comprobar si hay `jpegtran` disponible
- [x] Benchmark A: pipeline actual (baseline por imagen)
- [x] Benchmark B: un solo encode (escribir original a q40 y derivar el `_CROP` de él)
- [x] Benchmark C: `jpegtran -crop` lossless (mide el coste del ±16 px)
- [x] Benchmark D: `jpegtran -rotate -copy all` vs. giro con Pillow
- [x] Presentar a Rodrigo los números → **crop lossless DESCARTADO** (ver abajo), no hay decisión de ±16 px que tomar
- [ ] Implementar lo aprobado + tests (incl. XMP conservado y píxeles intactos donde toque)
- [ ] Gap ya existente: las RGB giradas pierden hoy el XMP (`af9d781` solo parcheó térmicas)
- [ ] Sonda de máquina falla en silencio (`organize.py:768-771` try/except: pass)
- [ ] Reevaluar el solape RGB+Térmicas con los tiempos NUEVOS (tarea /tareas 3876)
- [ ] Suite verde (1473+) antes de cualquier release

## Resultados del benchmark (2026-09-10, 3 RGB reales KL19 8000x6000, VM 4 núcleos cargada; min de 7)

| Variante | ms/foto | vs hoy |
|---|---|---|
| A hoy: 1 decode + 2 encodes | 1377 | — |
| E1 sin giro: original copiado + `_CROP` con Pillow (pixel-exacto) | 855 | −38 % |
| E2 con giro: `jpegtran -rotate` + `_CROP` con Pillow | 1305 | −5 % |
| C1 sin giro: original copiado + `_CROP` `jpegtran -crop` (±16 px) | 390 | −72 % |
| C2 con giro: `jpegtran -rotate` + `jpegtran -crop` (±16 px) | 1040 | −25 % |

Referencias: solo decode 400 ms · decode+1 encode 666 ms · `jpegtran -crop` puro 370 ms ·
`jpegtran -rotate` lossless 650 ms.

**Hallazgo clave e independiente del ±16 px: `compress_level=40` NO comprime.** El original
recomprimido a q40 sale MÁS grande que el de la cámara (3,67 → 3,86 MB; +5 % en las 3 fotos).
Recomprimir el original cuesta CPU, pierde calidad y engorda el disco. Copiarlo (o girarlo
lossless) es mejor en las tres dimensiones.

- [ ] Confirmar con Rodrigo si el original puede dejar de recomprimirse (independiente del ±16 px)

## Corrección importante (2026-09-10, tarde)

Las 3 fotos del primer benchmark se bajaron de `.../RGB/PB1/PB1_V1/` del bucket, que **ya es
salida del Organizer**. Medían una recompresión sobre algo ya recomprimido → de ahí la falsa
conclusión "q40 no comprime". Con el ORIGINAL de cámara real (Drive
`1hlxhGjDvRaygX2teRRvtsip5mQqN9twR`, q97, 15,7 MB, MAVIC2-ENTERPRISE-ADVANCED):

| | recta | girada | salida |
|---|---|---|---|
| A hoy (1 decode + 2 encodes) | **974 ms** | **1172 ms** | 3,54 + 1,85 MB |
| B (1 encode + `jpegtran -crop`) | 1063 ms | 1311 ms | 3,54 + 1,84 MB |
| ref: solo decode | 485 ms | 494 ms | — |

- **`compress_level=40` SÍ comprime: 15,7 → 3,54 MB (4,4×).** El original NO puede copiarse tal cual.
- **Crop lossless DESCARTADO**: `jpegtran` es más lento que encodear el 49 % del área ya
  decodificada en memoria, y el `_CROP` pesa lo mismo. No hay nada que ganar.
- La desviación del ±16 px además era menor de lo temido: con pct=0,70 sobre 8000x6000 los
  offsets son left=1200 (múltiplo exacto de 16, desviación **0**) y top=900→896 (**4 px**).
  Irrelevante: la variante no se implementa igualmente.

## Dónde está el tiempo de verdad

KL19 = **999 RGB** (999 `_CROP` + 999 originales, 5,96 GB en el bucket).
Trabajo de CPU medido ≈ 1,0-1,2 s/img → ~1.100 CPU-s → **~2m15s ideales en 8 núcleos**.
La fase tardó **6m49s**. Sobra un factor ~3.

Paralelismo mapeado (`atom_core/apply.py:536`): `ProcessPoolExecutor`, una imagen por `submit`,
sin chunksize. Workers = `utils.workers_para_lote(600 MB)` (`utils.py:1324`) = min(núcleos−1,
RAM_disp/600), techo `arranque*2`, ajuste en caliente cada 5 s por `ControladorAdaptativo`
(`paralelismo.py:267`) vía semáforo, no redimensionando el pool.
sqlite del manifiesto **no serializa**: los commits los hace solo el proceso padre en el callback
(`apply.py:469-488`), los workers no lo tocan. `_guardar_atomico` usa `.parcial` + `os.replace`,
sin fsync (`apply.py:194-225`).

`TOPE_WORKERS_HDD = 3` (`paralelismo.py:82`) se aplica **solo a RGB** (`phases.py:1122-1123`) si la
sonda detecta disco de origen HDD. Fallback seguro: sonda rota → `None` → no capa
(`paralelismo.py:330-338`). **Descartado como causa del run KL19**: con 3 workers la CPU no
habría marcado 86 % de 8 núcleos.

**Hipótesis viva: la fase está limitada por el disco de ORIGEN, no por CPU.** 15,7 GB leídos
(999 × 15,7 MB) + 5,3 GB escritos en 409 s ≈ 51 MB/s agregados — perfil de HDD, USB o tarjeta SD,
no de SSD. El benchmark local no lo ve porque relee el mismo fichero desde caché de página.
- [x] Confirmar con Rodrigo desde qué soporte leyó el run KL19 → **DISCO EXTERNO USB/HDD**
      (2026-09-10). El log de ese run ya no existe.
- [ ] Reabrir `TOPE_WORKERS_HDD=3` como causa: con origen USB/HDD la capa PUDO aplicarse.
      El "86 % CPU" que la descartaba hay que revalidarlo (¿es %-de-un-núcleo o %-de-8?).
- [x] Instrumentar la fase RGB (HECHO, 1475 tests verdes): por imagen, tiempo de lectura / decode / encode / escritura,
      y workers en vuelo a lo largo del run. Sin eso no se puede re-medir con criterio.
- [ ] Re-medir baseline: Rodrigo limpió ese PC → 13m46s / 6m49s ya no es comparable.


## Estado 2026-09-10 (mediodía) — instrumentación y bancos de prueba

**La hipótesis del disco CAE.** El `86 % CPU` del run KL19 es CPU **de sistema ya normalizada**
(`psutil.cpu_percent(percpu=False)`, `atom_core/medicion_recursos.py:288`), o sea ~6,9 de 8
núcleos ocupados → la fase NO esperaba al disco. Y la capa `TOPE_WORKERS_HDD=3` **no se aplicó**:
el origen era USB externo, y en Windows `Get-PhysicalDisk` devuelve `MediaType` vacío para USB →
`"desconocido"` → sin capa y **sin línea de log** (`paralelismo.py:324-353` retorna en silencio).
Queda el hueco real: 0,86 × 8 × 409 s ≈ **2.800 CPU-s** consumidos frente a ~1.100 que predice
el benchmark por imagen. Sobra CPU sin explicar, no I/O.

Pista para el siguiente: los **thumbnails son una fase aparte**
(`pipeline.gen_thumbnails_and_rotate`), NO salen del worker RGB — comprobar si su tiempo se está
contabilizando dentro de los 6m49s de "RGB".

### Instrumentación (hecha, apagada por defecto)
- `atom_core/perfil_rgb.py` (nuevo): opt-in con `ORGANIZER_PERFIL_RGB=<ruta.csv>`, cacheado al
  importar. Con la env var sin definir es no-op de coste cero.
- `atom_core/apply.py`: worker RGB = `_trabajo_fila` → `_escribir_salidas_de_fila` (:228).
  Instrumentado `lectura` (`Image.open`), `decode` (`img.load()`), `encode_original`,
  `encode_crop` y `escritura` (`os.replace` en `_guardar_atomico`, :194). Cabecera del CSV la
  escribe el padre en `aplicar_rgb` (~:439); resumen al terminar.
- `tests/test_perfil_rgb.py` (nuevo). Suite: **1475 passed**.
- `t_thumbnail` sale siempre `0.0` a propósito (fase aparte).
- ⏳ `scripts/bench_rgb.py` — lo estaba escribiendo un subagente al cerrar la sesión.
  **VERIFICAR SI EXISTE Y SI LA SUITE SIGUE VERDE ANTES DE SEGUIR.**

### Camino headless (verificado)
No hay CLI con argparse en el repo, pero `app_webview.py` **sí acepta flags**: la Pi corre
`python app_webview.py --server --host 0.0.0.0 --port 8765`. Alto nivel:
`atom_core/organize.py:1171 run_organize(params, emit, advanced)`; fase RGB sola:
`atom_core/phases.py:1281 do_rgb_aerotools_processing(cfg, progress_callback, ...)` sobre
`HeadlessHost` (`organize.py:123`). Plantillas: `tests/test_pipeline_sin_qt.py`.
Config efectiva de prod hardcodeada en `organize.py:276-279`.

### Bancos de prueba disponibles (túneles reverse desde el portátil de Rodrigo)
| Banco | Acceso | Perfil |
|---|---|---|
| Raspberry Pi (kiosco) | `ssh -p 2223 pi@127.0.0.1` | aarch64, 4 núcleos, 4 GB, todo en SD. Túnel permanente (`tunel-atom-dev.service`) |
| Portátil de Rodrigo | `ssh -p 2224 saez@127.0.0.1` | `saez-aerotools`, CachyOS, 16 núcleos, 32 GB, NVMe |
| PC oficina .121 | `ssh -p 2226 aerotools@127.0.0.1` | ⏳ túnel abierto pero **falta autorizar la clave** en la .121 |

Clave pública a autorizar:
`ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIQOMEfcQ9Rm+pHAl6L08TBsgq9Xjxe+i89MJgaEJES9 atom-server`
Los túneles los abre Rodrigo desde el portátil con
`ssh -o ControlPath=none -R <puerto>:<ip>:22 rodrigo_saez@atom-dev-nl -N` (el `ControlPath=none`
es imprescindible: su ControlMaster se come el `-R` y falla con "remote port forwarding failed").

### Incidencias abiertas fuera del scope RGB
- **PIN del kiosco de la Pi da "incorrecto" sin haberlo cambiado.** Hash scrypt en
  `meta.pin_kiosco` de `/home/pi/.config/atom-organizer/session.db`; ese fichero **se modificó
  hoy 11:31** y el `organizer-server.service` lleva corriendo desde el 25-ago (código viejo en
  memoria). No hay lockout persistente (`ControlIntentos` es en RAM). Propuesto a Rodrigo:
  refijar el PIN y reiniciar el servicio — **sin respuesta suya todavía**.
- Tarea /tareas **#3878** "Kiosco Pi: que reciba updates del Organizer" (proyecto Atom Organizer).
- Rodrigo estaba metiendo la .121 en la VPN al cerrar la sesión.

## Estado 2026-09-10 (tarde, sesión de relevo)

- [x] Suite verde revalidada: **1475 passed** (`--ignore=tests/test_dark_theme.py`).
- [x] `scripts/bench_rgb.py` NO existía (el subagente anterior murió antes de escribirlo). Relanzado.
- [x] **Pista de los thumbnails DESCARTADA.** `gen_thumbnails_and_rotate` (`pipeline.py:2019`) es del
      motor legacy y NO lo llama `organizar_plan_apply`. La ventana cronometrada como "RGB" es
      EXACTAMENTE la llamada a `aplicar_rgb(...)` (`phases.py:1120-1125`). Los thumbnails se
      cronometran aparte, en el subproceso "ROTACIÓN" (`phases.py:780-830`).
      → El hueco de ~1.700 CPU-s sigue SIN EXPLICAR.
- Trabajo adicional que SÍ cae dentro de la ventana "RGB" (candidatos al hueco, por orden de
  sospecha): giro `transpose` inline (`apply.py:279-293`, antes vivía en la fase de rotación),
  escrituras SQLite del manifiesto por fila (`apply.py:525,529,545`), `controlador.registrar/revisar`
  cada imagen (`apply.py:540-541`), telemetría de aforo (`apply.py:362-370`), emisión de stats
  por lote (`apply.py:154-170`). **El giro inline es la hipótesis nº1 nueva**: `jpegtran -rotate`
  medía 650 ms/foto y el benchmark A/E lo trataba como caso aparte.
- Bancos: Pi (2223) y portátil (2224) responden. **.121 (2226) sigue con `Permission denied`** —
  falta autorizar la clave `ssh-ed25519 AAAA...EJES9 atom-server`.
- Rodrigo dio OK a refijar el PIN del kiosco de la Pi; falta que diga qué PIN.

### PIN del kiosco Pi — RESUELTO (2026-09-10)
**Causa raíz**: la tabla `meta` de `/home/pi/.config/atom-organizer/session.db` NO tenía la clave
`pin_kiosco` (solo `esquema=1`). Sin valor guardado, `pin_kiosco.verificar()` sale por
`if not guardado: return False` (`atom_core/pin_kiosco.py:84`) → cualquier PIN da "incorrecto".
No fue un cambio de PIN ni un lockout. El `mtime` de las 11:31 era la **sesión de Google**
(tabla `sesion`, `rodrigo.saez@aerotools.es`, creada ese mismo instante), no el PIN.
Por qué nunca se persistió: sin evidencia — el journal del user no es persistente en la Pi
(`journalctl --user -u organizer-server` → "No journal files were found").
**Solución**: `pin_kiosco.fijar(store, "1379")` con el venv de la Pi + `systemctl --user restart
organizer-server`. Verificado: `verificar("1379")=True`, `verificar("0000")=False`, servicio
`active`, HTTP 200. Backup previo en la Pi: `/tmp/session.db.bak-<hhmm>`.
**Nota**: los units de la Pi son de USUARIO → `systemctl --user ...`, no hace falta sudo.

### Cierre de esta sesión (2026-09-10, handoff por tokens)
- [x] `scripts/bench_rgb.py` + `tests/test_bench_rgb.py` creados. Suite **1491 passed**.
      Lanza `organize.run_organize` (params: `origen`, `destino`, `estadillo`), fija
      `ORGANIZER_PERFIL_RGB` antes de importar `atom_core`, parsea la línea `[tiempos]`,
      mide CPU-s con `resource` (SELF+CHILDREN) y agrega suma/media/p95 por sub-etapa.
      Flags: `--origen --destino --estadillo --perfil-csv --repeticiones --json`.
- [x] **Dataset REAL localizado**: portátil (`ssh -p 2224 saez@127.0.0.1`),
      `/home/saez/Descargas/2026_03_16_ZARATAN` — 9.264 JPG, 86 GB, mitad `_W` (RGB) y mitad
      `_T` (térmicas), estructura de tarjeta (`1/100MEDIA`…`2/104MEDIA`) + estadillo real
      `2026_03_16_estadillo.csv` (Ingeteam/ZARATAN, Mavic 2EA). Ahí hay que medir: 16 núcleos,
      NVMe, 517 GB libres. **La VM NO sirve como banco: al 95 % de disco, 5,6 GB libres.**
- [x] Original de cámara suelto en el scratchpad de la sesión: `rgb_src/orig.JPG` (15,7 MB).
- [ ] ⏳ Primera corrida del bench: falla con `ValueError: combinar_estadillos: no se ha pasado
      ninguna ruta de estadillo` → hay que pasar `--estadillo`. Con ZARATAN ya hay uno real.
- [ ] ⏳ Repo + venv en el portátil (Python del sistema es **3.14.6**, puede no tener ruedas;
      PySide6 NO hace falta para el camino headless). `exiftool` sin comprobar allí.
- [ ] ⏳ .121 sigue sin la clave autorizada (`Permission denied` en el puerto 2226).

## Baseline REAL en el portátil (2026-09-10, ZARATAN completo)

Banco: portátil `saez-aerotools`, 16 núcleos, NVMe. Origen crudo de tarjeta
(`/home/saez/Descargas/2026_03_16_ZARATAN`, 4.632 RGB de 17,7 MB + 4.632 térmicas),
estadillo real. Comando en `/home/saez/bench-out/` (bench.log, bench.json, perfil_rgb.csv).

Etapas (s): Índice 51,6 · **RGB 467,0** · Térmicas 13,7 · Cierre 4,8.

| sub-etapa RGB | suma CPU-s | media/img | % |
|---|---|---|---|
| decode | 2333,0 | 0,504 s | 53 % |
| encode_original | 1222,5 | 0,264 s | 28 % |
| encode_crop | 802,8 | 0,173 s | 18 % |
| lectura | 10,2 | 0,0022 s | 0,2 % |
| escritura | 1,4 | 0,0003 s | 0,03 % |
| **total útil** | **4370,0** | **0,944 s** | 100 % |

**El hueco de ~1.700 CPU-s NO existe aquí.** Ocupación media 10,9 workers en vuelo
(pico 26, máximo 30) × 467 s ≈ 5.090 CPU-s frente a 4.370 útiles → overhead real ~16 %.
El giro inline, SQLite, controlador y telemetría caben todos en ese 16 %: NINGUNO es
el cuello de botella. La hipótesis nº1 (giro `transpose`) queda DESCARTADA como causa mayor.

**El limitador es el paralelismo, no el trabajo por imagen.** Techo ideal 4.370/16 = 273 s;
real 467 s (**factor 1,7**). El `ControladorAdaptativo` mantiene 10,9 procesos de media sobre
16 núcleos (36 % del máximo de 30) y oscila 7↔26 sin estabilizarse; se ve bajar a 7-9 workers
con `cpu_ociosa=30-46 %`, es decir recorta con la CPU libre. Ahí está el 100 % de la ganancia
alcanzable sin tocar la calidad de imagen.

- [x] Re-medir baseline en banco real (ZARATAN, 16 núcleos) — HECHO
- [ ] **Nueva prioridad nº1: arreglar el `ControladorAdaptativo`** (`paralelismo.py:267`) —
      no debe bajar workers con CPU ociosa; objetivo: mantener ≈núcleos en vuelo. Potencial −40 %.
- [ ] Prioridad nº2: `encode_original` (1.222 CPU-s, 28 %) — único encode recortable sin
      perder el `_CROP`. Medir q40 vs. calidad/ tamaño antes de tocar.
- [ ] `bench_rgb.py`: `cpu_segundos_mejor_repeticion` (236 s) NO captura los workers del
      `ProcessPoolExecutor` → el campo `sin_explicar` sale negativo (−4.134) y es basura.
      Arreglar leyendo CPU por PID de los hijos, o borrar ambos campos del informe.

## Fix del ControladorAdaptativo (2026-09-10, sin commitear)

**Causa raíz**: `decidir_trabajadores` (`atom_core/paralelismo.py`) comparaba el throughput de la
última ventana contra la anterior y bajaba workers si caía >5 %, **sin comprobar si el número de
workers había cambiado**. El throughput fluctúa más del 5 % por ruido (contenido de las fotos) →
bajaba por ruido; y `_subir` tras cualquier bajada previa sube solo +1 → nunca recuperaba.
Deriva medida: arranque 15, pico 26, estabiliza en 7-11 sobre 16 núcleos.

**Cambios (solo `paralelismo.py`, mínimos):**
1. Regla 4 con atribución causal: baja **solo si `ultima.trabajadores > anterior.trabajadores`**
   (la caída siguió a una subida = thrashing real). Sin cambio de workers = ruido → no baja.
   Preserva el comportamiento HDD: allí el thrashing aparece justo tras subir.
2. `_CPU_OCIOSA_PARA_SUBIR` 40,0 → **15,0** (con 11/16 workers la ociosa medida era 13-18 %, así
   que la regla 5 no disparaba nunca y no había forma de recuperar).
3. Histéresis nueva `_VENTANAS_ESPERA_TRAS_BAJADA = 3`: la regla 5 no sube si hubo una bajada en
   las 3 últimas mediciones (evita oscilar en discos lentos con el umbral más bajo).

Intactos: regla 1 (RAM), `_subir`, `_bajar`, `TOPE_WORKERS_HDD`, `apply.py`, `phases.py`.

Suite: **1497 passed** (1491 + 6 tests nuevos; ningún test existente modificado).
Tests añadidos en `tests/test_paralelismo_adaptativo.py`: `test_caida_sin_cambio_de_workers_no_baja`,
`test_caida_tras_subir_workers_sigue_bajando`, `test_caida_justo_despues_de_una_bajada_no_vuelve_a_bajar`,
`test_zona_muerta_con_poca_cpu_ociosa_ahora_sube`,
`test_zona_muerta_con_bajada_reciente_no_sube_pese_a_cpu_ociosa`,
`test_ram_manda_incluso_en_zona_muerta_con_cpu_ociosa_y_sin_bajada_previa`.

- [ ] **Bench de validación EN CURSO al cerrar la sesión** (portátil, PID 444890, ZARATAN completo).
      Salida: `/home/saez/bench-out/bench2.{log,json}` + `perfil_rgb2.csv`.
      Baseline a batir: **RGB 467,0 s**, media 10,9 workers en vuelo. Techo ideal 273 s.
      `paralelismo.py` YA sincronizado al portátil por scp (allí hay copia sin git).
- [ ] Si el bench confirma la mejora: commit (NO push a main sin OK de Rodrigo) y decidir si se
      ataca `encode_original` (1.222 CPU-s, 28 %).
- [ ] Si NO mejora: sospechar el cambio 2 (umbral 15 %) antes que el 1; revertir solo ese.

## Pi del kiosco — PIN vuelve a dar "incorrecto" (2026-09-10, sin resolver)

Rodrigo reporta que el PIN sigue fallando pese al fix de esta mañana (`pin_kiosco.fijar(store,"1379")`
verificado en su momento). **No se pudo diagnosticar: la Pi es inalcanzable.**
- Túnel `ssh -p 2223 pi@127.0.0.1` → `Connection refused` (lo abre el portátil de Rodrigo).
- Desde el portátil, `atom-pi.local` / `raspberrypi.local` no resuelven; la Pi no aparece en su ARP.
- **La mesh tampoco vale desde atom-dev-nl**: el servicio `netbird` está INACTIVO, solo queda la
  interfaz `wt0` (100.91.47.17/16) residual, sin peers ni DNS de mesh (`/opt/netbird/state.json`
  no lista peers). Levantar netbird en la VM está sin hacer y no se tocó.
- Pendiente de Rodrigo: IP de la Pi en la mesh, o reabrir el túnel 2223.
- Hipótesis a comprobar cuando haya acceso: que el `organizer-server` lea OTRA `session.db`
  (¿otro `$HOME`/otro usuario?) distinta de `/home/pi/.config/atom-organizer/session.db`, lo que
  explicaría que `verificar("1379")=True` en disco y el kiosco siga rechazándolo.

### Resultado bench2 (validación del fix) — el fix FUNCIONA pero el resultado EMPEORA

| | bench1 (antes) | bench2 (con fix) |
|---|---|---|
| RGB pared | **467 s** | **506 s** (8 min 26 s) |
| workers medios en vuelo | 10,9 (36 % del máx) | **29,6 (99 % del máx)** |
| throughput | 9,9 img/s | 9,2 img/s |

**Diagnóstico**: el controlador ya NO deriva a la baja — hace exactamente lo que se le pidió y
satura el máximo. El problema es que ese máximo está mal: `maximo = arranque*2` = **30 procesos
sobre 16 núcleos** (`paralelismo.py:309-316`, RGB no pasa `maximo` en `phases.py:1120-1123`).
Con trabajo CPU-bound (decode+encode, 0,944 s/img) la sobre-suscripción 2× solo añade cambios de
contexto y presión de memoria: −7 % de throughput.

**Siguiente paso (prioritario, NO hecho)**: capar el máximo de la fase RGB a ≈núcleos utilizables
en vez de `arranque*2`, y re-medir. El óptimo esperado está entre 15 y 16 workers → cerca del
techo ideal de 273 s. Ojo: `arranque*2` puede tener sentido para fases I/O-bound; capar solo RGB
(pasando `maximo` explícito desde `phases.py`) es más seguro que cambiar el default global.
NO revertir el fix del controlador: sin él no se puede sostener ningún número de workers.

## Bench3 (cap de workers a núcleos) — EL PARALELISMO NO ERA LA PALANCA

Cambio: `paralelismo.maximo_cpu_bound()` (nuevo, = `utils.workers_para_lote(mb_por_worker=0)`
→ núcleos utilizables, sin límite por RAM) y RGB lo pasa explícito (`phases.py:1123`).
Default global `arranque*2` intacto (las térmicas siguen a 32 hilos, 98 % de aprovechamiento).
Suite **1500 passed** (+3 tests en `tests/test_paralelismo_adaptativo.py`). SIN COMMITEAR.

| run | workers medios | RGB pared | throughput | decode medio |
|---|---|---|---|---|
| bench1 (original) | 10,9 | **467 s** | 9,9 img/s | 0,504 s |
| bench2 (fix controlador) | 29,6 | 506 s | 9,2 img/s | — |
| bench3 (cap a 15) | 15,0 (100 % saturado) | **521 s** | 8,9 img/s | **0,734 s** |

**Conclusión: la fase RGB YA estaba en el techo del hardware; el "techo ideal de 273 s" era
falso.** El total de CPU útil NO es constante: sube de 4.370 CPU-s (bench1) a **6.343 CPU-s**
(bench3) con el mismo trabajo. El decode por imagen se encarece un 45 % al saturar.
Causa: el banco es un **i7-13620H = 6 núcleos P + 4 E (16 hilos)**, no 16 núcleos iguales.
Pasados ~10 workers los nuevos caen en E-cores y en hermanos SMT, que rinden una fracción,
y además bajan la frecuencia de todos. Más workers = misma tarta repartida peor.

→ Ni el fix del controlador ni el cap mejoran el tiempo de pared. **La palanca real es
reducir trabajo por imagen** (6.343 CPU-s: decode 54 %, encode_original 27 %, encode_crop 19 %),
no repartirlo mejor.

- [ ] ⏳ Barrido pendiente para fijar el óptimo real de workers (6/8/10/12/15) sobre un
      subconjunto (`/home/saez/Descargas/2026_03_16_ZARATAN/1/100MEDIA`, ~1-2 min por punto).
      Hipótesis: el óptimo está en 8-11, no en núcleos-1. Si se confirma, el cap debe ser
      "núcleos de rendimiento", no "núcleos utilizables".
- [ ] Decidir qué se commitea: el fix del controlador + el cap son defendibles como
      *estabilidad* (ya no oscila 7↔26), pero NO como mejora de tiempo. Sin barrido no hay
      número que justifique tocar producción.

### Banco Windows real (PC de oficina, .121) — YA ACCESIBLE
`ssh aerotools@100.91.107.47` por la **mesh Netbird** (también por el túnel `-p 2226`).
`DESKTOP-A9EAEIO`, Windows 10 Home, **8 núcleos**, 16 GB, Python 3.12 en
`C:\Program Files\Python312`, `exiftool` en `C:\Windows\exiftool.exe`.
Discos: C: 126 GB libres · D: 1,24 TB · E: 4,56 TB (todos fijos, ningún USB conectado ahora).
Lotes crudos reales en `E:\DATOS_PARA_ORGANIZAR\` (KL19, KL20, KL21, KL86, GRIJOTA_III…).
**NO tiene el fuente**, solo el .exe (`ATOM-Organizer-Setup-v3.4.55.exe` en el Escritorio).
Montar banco ahí = clonar repo + venv con `requirements.txt`. Pendiente de OK de Rodrigo.

### Mesh Netbird (verificada 2026-09-10)
`atom-dev-nl` es peer (`wt0` = 100.91.47.17). Ping y SSH OK a `pc-ofi-aerotools`
(100.91.107.47, user `aerotools`, Windows/cmd) y `pi-ofi-aerotools` (100.91.2.163, user `pi`).
La Pi está viva, `organizer-server` (user unit) **active** → ya se puede diagnosticar el PIN.
No activar la policy `Default`; el demonio corre en el contenedor Docker `netbird`.

## Sesión 2026-09-10 (noche) — bancos Pi + PC oficina montados, barrido EN CURSO

Orden de Rodrigo: **el portátil queda FUERA como banco.** Solo PC oficina y Pi.

- [x] **PC oficina montado** (`ssh aerotools@100.91.107.47`, Windows/cmd, 8 núcleos homogéneos).
      Repo en `C:\Users\Aerotools\bench` (tar+scp desde la VM, sin git), venv con
      **`requirements-server.txt`** (ligero y suficiente; `requirements.txt` arrastra
      PySide6/pyinstaller/jupyter y no hace falta para el camino headless). `DEPS OK`.
      OJO: `mkdir C:\bench` falla por permisos → todo bajo el perfil del usuario.
- [x] Humo verde ahí: 114 img a 8 workers fijos, 97 % del potencial. Las térmicas fallan
      (falta `programas_externos\DJI\dji_irp.exe`), **irrelevante para RGB** (quita ruido).
      `resource` no existe en Windows → no hay CPU-s en el informe (campo ya era basura).
- [x] **`scripts/bench_rgb.py --workers N`** (nuevo): monkeypatch LOCAL del script que fuerza
      `arranque=minimo=maximo=N` y `tope_hdd=None` → controlador inmóvil. NO toca
      `paralelismo.py` ni `phases.py`. Campo `workers_fijados` en el JSON.
      Tests nuevos en `tests/test_bench_rgb.py`; `62 passed` (bench_rgb + paralelismo).
- [x] **Pi montada a medias** (`ssh pi@100.91.2.163`): repo en `~/bench`, venv creado,
      `pip install -r requirements-server.txt` lanzado en background (`/home/pi/pip.log`).
      **Falta dataset**: no hay RGB crudas en la Pi. 14 GB libres en la SD.
- [ ] ⏳ **BARRIDO EN CURSO en el PC de oficina**: `C:\Users\Aerotools\bench\barrido.bat`,
      workers 4/6/8/10/12/16 sobre `E:\DATOS_PARA_ORGANIZAR\2026_08_19_KL19\FOTOS\106MEDIA`
      (~500 RGB), estadillo `...\2026_08_19_estadillo_KL19.csv`.
      Salidas: `E:\bench-out\barrido_w<N>.json` + `perfil_w<N>.csv`.
      Comprobar fin con `dir /b E:\bench-out\barrido_w16.json`.
      ⚠️ El .bat se editó EN CALIENTE para añadirle el cartel de "ocupado" → cmd puede releer
      desde un offset desplazado al acabar el bucle `for` y repetir parte del barrido.
      No es destructivo (mismos nombres de JSON), pero si se ve repitiendo, matar y relanzar.
- [ ] Con los números: decidir el cap real de la fase RGB y qué se commitea.

### Lanzar en el PC de oficina EN VENTANA VISIBLE (aprobado por Rodrigo)
SSH en Windows es sesión no interactiva: `start /b` **ni se ve ni sobrevive** al cierre de la
conexión (el primer intento de barrido murió así, sin dejar ni log). La vía buena:
```
schtasks /create /tn <Nombre> /tr "cmd /k C:\ruta\script.bat" /sc once /st 23:59 /it /f
schtasks /run /tn <Nombre>
```
`/it` = sesión interactiva del usuario logueado (tiene que estar la sesión iniciada). Verificado.
**Motivo de Rodrigo**: que quien esté físicamente en ese PC vea que está ocupado y no lance el
Organizer encima. Por eso `barrido.bat` abre en rojo con el cartel
"OCUPADO: BENCH ATOM ORGANIZER — NO LANZAR OTRO" y ese título de ventana.
Decidido NO instalar la GUI real (pywebview) ahí: la ventana de consola ya cumple ese fin.
Tarea actual creada: `BarridoRGB` (borrarla al terminar: `schtasks /delete /tn BarridoRGB /f`).

Estos bancos quedan documentados también en `~/.claude/CLAUDE.md` (sección "Bancos de prueba").
