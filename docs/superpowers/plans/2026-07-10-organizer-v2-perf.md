# ATOM Organizer v2-perf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acelerar el procesado del ATOM Organizer (H1 exiftool batch, H2 colormap LUT) sin cambiar el comportamiento observable, con los 107 tests pytest en verde tras cada tarea.

**Architecture:** Dos optimizaciones aisladas en `pipeline.py`. H1 difiere la copia de tags exiftool del bucle caliente a un único pase `-stay_open` (vía flag opcional `defer_exif`, el default queda byte-idéntico). H2 precomputa una LUT uint8 de 1024 entradas por colormap, cacheada a nivel de módulo, e indexa con numpy en vez de recrear `get_cmap` por imagen.

**Tech Stack:** Python 3.11, numpy, matplotlib, PIL, exiftool (binario del sistema/embebido), pytest 9.1.1.

## Global Constraints

- Intérprete de tests: `.venv-test/bin/python` (desde el root `/home/rodrigo_saez/atom-organizer-src/src-v2.1.5`).
- Suite completa: `.venv-test/bin/python -m pytest tests/ -q` → **107 passed** obligatorio tras cada tarea.
- Import de tests: `import pipeline as <alias>` (módulo top-level, no paquete `src`).
- Autor de commits: `git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit`, **SIN** `Co-Authored-By`. **NO push.** `git add` explícito por archivo, nunca `-A`.
- Sin cambio de comportamiento observable: salida en disco (tiffs, tags EXIF, imágenes colormap, nombres, orden de archivos) idéntica o imperceptiblemente equivalente (H2 ≤ 1/255 por píxel).
- El binario exiftool se resuelve por el parámetro `exiftool_exe` (no hardcodear ruta).

---

### Task 1: H2 — LUT precomputada para el colormap térmico

**Files:**
- Modify: `pipeline.py:1390-1405` (`apply_thermal_colormap`) + añadir cache LUT a nivel de módulo cerca del import `from matplotlib import cm` (`pipeline.py:38`).
- Test: `tests/test_split_images_colormap.py` (añadir tests; los 2 existentes deben seguir pasando).

**Interfaces:**
- Consumes: nada de tareas previas.
- Produces: `apply_thermal_colormap(array, temp_min, temp_max, colormap_name="inferno") -> np.ndarray` (firma **sin cambios**), devuelve uint8 `(H, W, 3)`. Cache interna `_LUT_CACHE: dict[str, np.ndarray]`, helper `_get_thermal_lut(colormap_name) -> np.ndarray` (shape `(1024, 3)`, uint8).

- [ ] **Step 1: Escribir el test que falla (equivalencia LUT ≤ 1/255 + rango idx)**

Añadir a `tests/test_split_images_colormap.py`:

```python
import numpy as np
from pipeline import apply_thermal_colormap
from matplotlib import cm


def _reference_colormap(array, temp_min, temp_max, name="inferno"):
    """Implementación de referencia (la vieja, get_cmap continuo) para comparar fidelidad."""
    clipped = np.clip(array, temp_min, temp_max)
    if temp_max > temp_min:
        normalized = (clipped - temp_min) / (temp_max - temp_min)
    else:
        normalized = np.zeros_like(clipped)
    rgba = cm.get_cmap(name)(normalized)
    return (rgba[..., :3] * 255).astype(np.uint8)


def test_lut_equivalente_a_referencia_dentro_de_1_255():
    temp_min, temp_max = 0.0, 60.0
    rng = np.linspace(-10.0, 70.0, 64 * 64).reshape(64, 64)  # incluye fuera de rango
    new = apply_thermal_colormap(rng, temp_min, temp_max)
    ref = _reference_colormap(rng, temp_min, temp_max)
    assert new.dtype == np.uint8 and new.shape == (64, 64, 3)
    diff = np.abs(new.astype(int) - ref.astype(int)).max()
    assert diff <= 1, f"LUT-1024 debe diferir ≤1/255 de la referencia continua, dio {diff}"


def test_lut_rango_completo_sin_indexerror():
    # valores muy por debajo y por encima no deben desbordar la LUT (idx 0..1023)
    arr = np.array([[-1000.0, 0.0], [30.0, 1e6]])
    out = apply_thermal_colormap(arr, 0.0, 60.0)
    assert out.shape == (2, 2, 3)
```

- [ ] **Step 2: Correr los tests nuevos y verificar que fallan**

Run: `.venv-test/bin/python -m pytest tests/test_split_images_colormap.py -q`
Expected: los 2 nuevos fallan por diff > 1 (la implementación actual usa get_cmap continuo, coincide exacto → en realidad `test_lut_equivalente` PASA con la impl vieja; usa este step para confirmar que arranca y que el NUEVO comportamiento LUT lo mantiene ≤1). Si `test_lut_equivalente` ya pasa con el código viejo, es correcto — el objetivo es que SIGA pasando tras el cambio a LUT. El test que discrimina es que tras el cambio la diff siga ≤1.

> Nota TDD: aquí el test es de **no-regresión de fidelidad**, no de "función inexistente". El fallo real a evitar es que la LUT introduzca diff > 1. Procede al Step 3 y confirma en Step 4.

- [ ] **Step 3: Implementar la LUT cacheada**

En `pipeline.py`, cerca de la línea 38 (tras `from matplotlib import cm`), añadir:

```python
_LUT_SIZE = 1024
_LUT_CACHE: dict = {}


def _get_thermal_lut(colormap_name: str) -> np.ndarray:
    """LUT uint8 (1024, 3) precomputada una sola vez por nombre de colormap."""
    lut = _LUT_CACHE.get(colormap_name)
    if lut is None:
        lut = (cm.get_cmap(colormap_name)(np.linspace(0, 1, _LUT_SIZE))[:, :3] * 255).astype(np.uint8)
        _LUT_CACHE[colormap_name] = lut
    return lut
```

Reemplazar el cuerpo de `apply_thermal_colormap` (líneas 1397-1404) por:

```python
    clipped = np.clip(array, temp_min, temp_max)
    if temp_max > temp_min:
        idx = ((clipped - temp_min) / (temp_max - temp_min) * (_LUT_SIZE - 1)).astype(np.uint16)
    else:
        idx = np.zeros(clipped.shape, dtype=np.uint16)
    return _get_thermal_lut(colormap_name)[idx]
```

(El docstring y la firma se mantienen. `idx` es `uint16` porque el rango es 0..1023.)

- [ ] **Step 4: Correr los tests del colormap y verificar que pasan**

Run: `.venv-test/bin/python -m pytest tests/test_split_images_colormap.py -q`
Expected: PASS todos (los 2 existentes + los 2 nuevos).

- [ ] **Step 5: Medición antes/después (evidencia)**

Run: `.venv-test/bin/python /tmp/claude-1000/-home-rodrigo-saez/c4dbdd6c-5004-4090-a24d-1a05be07f7cc/scratchpad/profile_baseline.py` (sección H2) o un micro-bench inline sobre 60 arrays.
Expected: ~3× speedup, diff máx ≤ 1/255. Anotar los números en el commit.

- [ ] **Step 6: Suite completa**

Run: `.venv-test/bin/python -m pytest tests/ -q`
Expected: **107 passed**.

- [ ] **Step 7: Commit**

```bash
git add pipeline.py tests/test_split_images_colormap.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "perf(colormap): LUT-1024 precomputada en apply_thermal_colormap (~3x, dif <=1/255)"
```

---

### Task 2: H1 — exiftool batch con `-stay_open` (flag `defer_exif`)

**Files:**
- Modify: `pipeline.py:1826` (`convert_dji_image_to_tif`: nuevo param `defer_exif=False`; cuando True no llama exiftool y devuelve el par `(src, dst)`).
- Modify: `pipeline.py:1779` (`convert_dji_images_to_tif`: acumular pares en el bucle con `defer_exif=True` y drenarlos con un pase `-stay_open` tras el bucle).
- Modify: `pipeline.py:1980-1981` (la llamada exiftool inline pasa a estar bajo `if not defer_exif:`).
- Test: `tests/test_task25_reverify.py` (el test existente `test_tiff_rotation_preserves_radiometry_and_copies_exif` NO se toca — usa el default `defer_exif=False`; añadir un test nuevo para el pase batch).

**Interfaces:**
- Consumes: nada de Task 1.
- Produces:
  - `convert_dji_image_to_tif(..., defer_exif: bool = False)`:
    - `defer_exif=False` (default): comportamiento **idéntico al actual** (exiftool inline por imagen). Devuelve `None`.
    - `defer_exif=True`: NO ejecuta exiftool; tras guardar el tiff devuelve la tupla `(src_jpg_path, dst_tiff_path)` en éxito, o `None` en error/early-return.
  - `convert_dji_images_to_tif(...)`: recoge las tuplas del bucle y ejecuta UN pase `exiftool -stay_open True -@ <argfile>` al final; sin cambio de firma.

- [ ] **Step 0: Verificar callers de `convert_dji_image_to_tif`**

Run: `grep -rn "convert_dji_image_to_tif" pipeline.py tests/`
Expected: confirmar que el único caller de producción es el bucle en `convert_dji_images_to_tif:1816` (+ los tests). Si hubiese otro caller directo en producción, sigue siendo seguro porque el default `defer_exif=False` no cambia su comportamiento. Anotar los callers encontrados.

- [ ] **Step 1: Escribir el test que falla (pase batch produce tiffs con tags)**

Añadir a `tests/test_task25_reverify.py`:

```python
def test_batch_exiftool_stay_open_una_sola_invocacion(tmp_path, logger, make_dji_jpeg, monkeypatch):
    """convert_dji_images_to_tif debe copiar los tags de TODAS las imágenes con UN solo
    pase exiftool -stay_open (no un proceso por imagen), preservando la salida."""
    import pipeline as split_images

    input_folder = tmp_path / "TERMICA"
    input_folder.mkdir()
    for i in range(3):
        make_dji_jpeg(str(input_folder / f"DJI_000{i}_T.JPG"))

    exif_calls = []

    def fake_run(cmd, *args, **kwargs):
        exif_calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    # convert_dji_image_to_tif genera el tiff pero difiere exiftool; mockeamos su cuerpo
    # para aislar el batch: devuelve el par (src, dst) como hará la versión defer_exif.
    obj = split_images.SplitImages(logger)
    obj.total_images_number = 3
    obj.current_image_number = 0

    pairs = []
    def fake_convert(inp, outp, image_name, *a, defer_exif=False, **k):
        src = os.path.join(inp, image_name)
        dst = os.path.join(outp, image_name.removesuffix(".JPG") + ".tiff")
        # simula tiff en disco
        from PIL import Image as _I
        _I.new("I;16", (4, 4)).save(dst, format="TIFF")
        assert defer_exif is True, "el bucle debe pasar defer_exif=True"
        return (src, dst)
    obj.convert_dji_image_to_tif = fake_convert

    monkeypatch.setattr(split_images.subprocess, "run", fake_run)
    progress = _noop_progress()
    obj.convert_dji_images_to_tif(str(input_folder), "exiftool", "dji_utility", progress, progress)

    assert len(exif_calls) == 1, "Se esperaba UNA sola invocación batch de exiftool para las 3 imágenes."
    batch_cmd = exif_calls[0]
    joined = " ".join(batch_cmd) if isinstance(batch_cmd, (list, tuple)) else str(batch_cmd)
    assert "-stay_open" in joined
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `.venv-test/bin/python -m pytest tests/test_task25_reverify.py::test_batch_exiftool_stay_open_una_sola_invocacion -q`
Expected: FAIL (hoy `convert_dji_image_to_tif` no acepta `defer_exif` ni devuelve el par, y no hay pase batch → `len(exif_calls) == 0` o AssertionError del `defer_exif is True`).

- [ ] **Step 3: Implementar `defer_exif` en `convert_dji_image_to_tif`**

En `pipeline.py`, cambiar la firma (línea 1826) añadiendo `defer_exif: bool = False` al final de los parámetros.

Reemplazar las líneas 1980-1981 (la llamada exiftool inline):

```python
        src_exif = os.path.join(input_folder, image_name)
        dst_exif = os.path.join(output_folder, image_name.removesuffix(".JPG") + ".tiff")
        if defer_exif:
            return (src_exif, dst_exif)
        subproceso_exiftool = '"{0}" -tagsfromfile "{1}" "{2}" -overwrite_original_in_place'.format(exiftool_exe, src_exif, dst_exif)
        subprocess.run(subproceso_exiftool)
```

(Con `defer_exif=False` el comando es idéntico al actual → cero cambio de comportamiento.)

- [ ] **Step 4: Implementar el drenaje batch en `convert_dji_images_to_tif`**

En el bucle (línea 1816-1824), recoger los pares y drenar tras el bucle. Reemplazar el cuerpo del `if not just_atom_selection ...:` por:

```python
        if not just_atom_selection or (just_atom_selection and os.path.basename(input_folder)== "Seleccion_ATOM"):
            pending_exif = []
            for image in images:
                if not self.stop:
                    self.current_image_number += 1
                    p = utils.safe_pct(self.current_image_number, self.total_images_number)
                    progress_callback.emit(".")
                    progress_bar.emit(p)
                    pair = self.convert_dji_image_to_tif(input_folder, input_folder, image, exiftool_exe, dji_utility, progress_callback, progress_bar, emissivity, humidity, auto_temp, up_threshold_temperature, low_threshold_temperature, rotate_90, rotate_minus_90, auto_rotate, just_atom_selection, generate_gray_scale_images, generate_colormap_images, defer_exif=True)
                    if pair:
                        pending_exif.append(pair)
            self._run_exif_batch(pending_exif, exiftool_exe, progress_callback)
```

Añadir el método helper en la clase `SplitImages`:

```python
    def _run_exif_batch(self, pairs, exiftool_exe, progress_callback=None):
        """Copia los tags EXIF de todos los (src_jpg, dst_tiff) con UN solo proceso
        exiftool -stay_open. Equivale a los N subprocess.run inline pero sin re-arrancar
        el intérprete Perl por imagen (~5x)."""
        if not pairs:
            return
        import tempfile
        argfile = None
        try:
            fd, argfile = tempfile.mkstemp(suffix="_exifargs.txt", text=True)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for src, dst in pairs:
                    f.write("-tagsfromfile\n{0}\n-overwrite_original_in_place\n{1}\n-execute\n".format(src, dst))
                f.write("-stay_open\nFalse\n-execute\n")  # cierre: sin esto exiftool cuelga
            cmd = '"{0}" -stay_open True -@ "{1}"'.format(exiftool_exe, argfile)
            result = subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if result.returncode != 0 and progress_callback is not None:
                progress_callback.emit("\nAviso: exiftool batch devolvió código {0}.\n".format(result.returncode))
        finally:
            if argfile and os.path.exists(argfile):
                try:
                    os.remove(argfile)
                except OSError:
                    pass
```

> Seguridad del diferido: el `.JPG` origen (`src`) persiste durante todo el bucle (solo se borra el `.raw` vía `_safe_remove`), así que todos los `src` existen cuando corre el pase batch al final. El resultado por-archivo es idéntico al inline.

- [ ] **Step 5: Correr el test nuevo + el existente de rotación/exif**

Run: `.venv-test/bin/python -m pytest tests/test_task25_reverify.py -q`
Expected: PASS todos. El existente `test_tiff_rotation_preserves_radiometry_and_copies_exif` sigue verde porque llama a `convert_dji_image_to_tif` con `defer_exif` por defecto (False) → 2 subprocess.run (DJI + exiftool inline), sin cambios.

- [ ] **Step 6: Medición antes/después (evidencia)**

Run: `.venv-test/bin/python /tmp/claude-1000/-home-rodrigo-saez/c4dbdd6c-5004-4090-a24d-1a05be07f7cc/scratchpad/profile_baseline.py` (sección H1).
Expected: ~5× speedup (ms/img). Anotar en el commit.

- [ ] **Step 7: Suite completa**

Run: `.venv-test/bin/python -m pytest tests/ -q`
Expected: **107 passed** (+ el test nuevo = 108). Confirmar 0 fallos.

- [ ] **Step 8: Commit**

```bash
git add pipeline.py tests/test_task25_reverify.py
git -c user.name=saez_ro -c user.email=ro.saezescobar@outlook.com commit -m "perf(exiftool): batch -stay_open en convert_dji_images_to_tif (~5x), default inline sin cambios"
```

---

## Self-Review

- **Cobertura de spec:** H1 → Task 2; H2 → Task 1; H3 descartado (documentado fuera de alcance en la spec). Ambos hotspots cubiertos.
- **Sin cambio observable:** H2 test de fidelidad ≤1/255; H1 default `defer_exif=False` byte-idéntico + test existente intacto + pase batch produce mismos tiffs.
- **Consistencia de tipos:** `_get_thermal_lut` devuelve `(1024,3)` uint8; `idx` uint16; `apply_thermal_colormap` firma sin cambios. `convert_dji_image_to_tif` devuelve `(src,dst)` solo con `defer_exif=True`, `None` en el resto; `_run_exif_batch(pairs, exiftool_exe, progress_callback)`.
- **Orden:** Task 1 (H2, aislada, menor riesgo) antes de Task 2 (H1, integración) — permite validar la mecánica TDD/medición con el cambio más simple primero.

## Notas de cierre (post-tasks)
- Tras ambas tareas: rebuild AppImage v2 (`build-appimage/.progress.md`), re-inyectar `ipaddress.pyc` (`inject_ipaddress.py`) tras PyInstaller. **No** en este plan (fase de build aparte).
- Documentar con `documentar-sesion` (Diario + nota `[[ATOM Organizer]]`).
- **NO push** sin OK expreso del user.
