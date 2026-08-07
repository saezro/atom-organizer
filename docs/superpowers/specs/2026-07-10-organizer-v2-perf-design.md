# ATOM Organizer v2 — Optimización de rendimiento (diseño)

**Fecha:** 2026-07-10
**Autor:** Rodrigo Saez
**Estado:** Aprobado (baseline medido, alcance H1+H2 confirmado por el user)

## Objetivo

Acelerar el procesado del ATOM Organizer sin cambiar el comportamiento observable
(salida idéntica o imperceptiblemente equivalente) y con los **107 tests pytest en verde**
tras cada optimización. Cada cambio lleva medición ANTES/DESPUÉS.

## Baseline medido (dataset real GOURNAY, banco de 4 cores)

Harness: `scratchpad/profile_baseline.py` sobre 60–80 imágenes reales
(`gs://plantas_pv_nl/GOURNAY/INSPECCIONES/TERMICA_MODULOS/2026/{TERMICA,RGB}/PB1/PB1_V2/`).

| Hotspot | Actual | Propuesto | Speedup | Comportamiento |
|---|---|---|---|---|
| H1 exiftool 1-proc/img → `-stay_open` batch | 927.9 ms/img | 170.3 ms/img | **5.4×** | Byte-idéntico (mismo tag-copy, solo batcheado) |
| H2 colormap `get_cmap`/img → LUT numpy | 37.6 ms/img | 11.5 ms/img | **3.3×** | Casi idéntico (ver fidelidad LUT-1024 abajo) |
| H3 `max_workers` None/2/4/8 | None≈4≈8 óptimo; 2 peor | — | ~1.0× | **Descartado** (sin ganancia medible en el hardware) |

## Alcance de esta fase: **H1 + H2** (H3 descartado)

H3 no entra: en 4 cores `max_workers=None` (=cpu_count) ya es óptimo y capar hace daño;
el único argumento sería seguridad de memoria en hosts de muchos cores, no demostrable en
este banco. Se documenta como posible guarda defensiva futura, fuera de esta fase.

---

## H1 — exiftool batch con `-stay_open`

### Situación actual
- `pipeline.py:1980-1981`, dentro de `convert_dji_image_to_tif` (def en `pipeline.py:1826`),
  llamada por-imagen en el bucle `for image in images:` (`pipeline.py:1816`) de
  `convert_dji_images_to_tif` (`pipeline.py:1779`).
- Hoy: **un proceso exiftool por foto**:
  `subprocess.run('"exiftool" -tagsfromfile SRC DST -overwrite_original_in_place')`.
- Es el único bucle masivo que NO usa `run_batch` → corre 100% secuencial. Es el mayor
  trozo serial del pipeline.

### Cambio propuesto
Diferir la copia de tags: en vez de llamar a exiftool inline por imagen, **acumular los
pares `(src_jpg, dst_tiff)`** durante el bucle y ejecutar **UN solo pase** exiftool
`-stay_open` (vía argfile `-@`) al terminar el bucle en `convert_dji_images_to_tif`.

**Seguridad del diferido:** el `.JPG` origen persiste durante todo el bucle (solo se borra
el `.raw` vía `_safe_remove`), así que todos los `src` siguen existiendo cuando corre el
pase batch al final. El orden de la copia de tags cambia (todas al final en vez de
intercaladas), pero el resultado por-archivo es idéntico.

### Detalle del argfile
Por cada par, escribir en el argfile:
```
-tagsfromfile
<src_jpg>
-overwrite_original_in_place
<dst_tiff>
-execute
```
y cerrar con `-stay_open\nFalse\n-execute\n` (sin el cierre, exiftool cuelga esperando).
Invocar: `exiftool -stay_open True -@ <argfile>`.

### Consideraciones de implementación
- Acumulador en `self` (p.ej. `self._pending_exif_pairs`), reseteado al inicio del bucle
  y drenado al final. Cuidado con reentrancia / estado entre tandas.
- Manejo de error: si el pase batch falla, no debe abortar todo silenciosamente — loggear
  como hoy. Considerar reportar por-archivo los que fallaron (exiftool devuelve resumen).
- El `exiftool_exe` sigue siendo el mismo parámetro (ruta al binario embebido en el .exe).

### Verificación
- Test: convertir un set pequeño, comprobar que los tiffs resultantes tienen **los mismos
  tags EXIF** que con el código actual (comparar con exiftool sobre salida de ambas ramas).
- Medición antes/después con el harness sobre GOURNAY.

---

## H2 — LUT precomputada para el colormap térmico

### Situación actual
- `apply_thermal_colormap` en `pipeline.py:1390-1405`, llamada en `pipeline.py:1966-1969`
  (por-imagen, solo si `generate_colormap_images=True`).
- Hoy: `cm.get_cmap(colormap_name)` se **recrea por imagen** (línea 1402), luego aplica
  el colormap sobre el array normalizado.

### Cambio propuesto
Precomputar una **LUT uint8 de 1024 entradas** por `colormap_name` una sola vez
(cache a nivel de módulo, dict `colormap_name -> lut`), e indexar con numpy:

```python
_LUT_CACHE: dict[str, np.ndarray] = {}
_LUT_SIZE = 1024

def _get_lut(colormap_name: str) -> np.ndarray:
    lut = _LUT_CACHE.get(colormap_name)
    if lut is None:
        lut = (cm.get_cmap(colormap_name)(np.linspace(0, 1, _LUT_SIZE))[:, :3] * 255).astype(np.uint8)
        _LUT_CACHE[colormap_name] = lut
    return lut
```

En `apply_thermal_colormap`, sustituir la recreación + aplicación por:
```python
clipped = np.clip(array, temp_min, temp_max)
if temp_max > temp_min:
    idx = ((clipped - temp_min) / (temp_max - temp_min) * (_LUT_SIZE - 1)).astype(np.uint16)
else:
    idx = np.zeros(clipped.shape, dtype=np.uint16)
return _get_lut(colormap_name)[idx]
```

### Fidelidad (decisión del user: LUT-1024)
- LUT-256 daba dif máx **5/255** por cuantización → se descarta.
- LUT-1024 baja la dif a **~1/255** (prácticamente byte-idéntico), manteniendo casi toda
  la velocidad. La spec fija **1024**.
- El colormap es un overlay visual para inspección humana (carpeta `Color_gradiente`),
  no se analiza numéricamente → 1/255 es irrelevante a ojo y respeta "sin cambio observable".

### Consideraciones de implementación
- La firma pública de `apply_thermal_colormap(array, temp_min, temp_max, colormap_name)`
  **no cambia** → los tests existentes siguen valiendo.
- `idx` debe ser `uint16` (no `uint8`) porque el rango es 0..1023.
- Mantener el manejo del caso `temp_max <= temp_min` (todo a idx 0), como hoy.

### Verificación
- Test de equivalencia: para un array de prueba, `abs(actual - nuevo).max() <= 1`.
- Medición antes/después con el harness.

---

## Plan de ejecución

1. Spec (este doc) → commit local (autor `saez_ro`, sin Co-Authored-By, **sin push**).
2. `writing-plans` → plan por tasks.
3. `subagent-driven-development`: un `implementer` (Sonnet) por optimización, TDD,
   **107 tests en verde** tras cada una + medición antes/después. Opus coordina/revisa.
4. Cierre: rebuild AppImage v2 (`build-appimage/.progress.md`), re-inyectar `ipaddress.pyc`
   tras el build PyInstaller (`inject_ipaddress.py`). Documentar (`documentar-sesion`).

## Fuera de alcance
- H3 (`max_workers`) — sin ganancia medible; posible guarda de memoria futura.
- Cualquier cambio de comportamiento observable (formato de salida, tags, nombres, orden
  de archivos en disco).
