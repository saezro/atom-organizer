# LEDGER — Velocidad fase RGB (jpegtran lossless + copia directa)

## Clarified
- ¿Las RGB de KL19 se giran? → El ángulo es POR VUELO (yaw, `indice.py:266-286`). KL19 tiene
  vuelos a 0 y vuelos a 270 (run v3.4.83: 1094/930). PB1_V1 es de los de 0 → normal.
- Aviso de rotación: si no se gira ninguna, debe decir "sin giro" explícito. → HECHO.
- ±16 px de desviación en el `_CROP` a cambio de crop lossless → Rodrigo pregunta impacto;
  respondido (0,2 % del ancho). PENDIENTE su OK final.
- ¿compress_rgb activo? → SÍ, hardcodeado True con compress_level=40 (`organize.py:276`), sin
  toggle en el webui. => la copia de bytes NO aplica; la palanca es reducir de 2 encodes a 1.

## Requisitos
- [x] `rotLine` dice "sin giro · N imágenes tal cual" cuando rot270+rot90 == 0
      (`webui/src/ProgressModal.jsx`) + test `webui/src/test/rotacionLinea.test.jsx`
- [x] Confirmar config efectiva de la fase RGB (compress_rgb=True, compress_level=40,
      cropping_rgb=True auto por modelo, gen_thumbnails=True — todo hardcode en organize.py:276-279)
- [ ] `jpegtran` disponible: helper en `external_tools.py` + `jpegtran.exe` en
      `programas_externos/` + `libjpeg-turbo-progs` en `.github/workflows/release.yml`
- [~] Ruta de copia de bytes: DESCARTADA, siempre se comprime a q40
- [ ] Un solo encode por imagen: escribir el ORIGINAL a q40 y derivar el `_CROP` de él con
      `jpegtran -crop` (hoy son 2 encodes del mismo decode)
- [ ] Benchmark que cuantifique el ahorro (en curso)
- [ ] Giro RGB con `jpegtran -rotate -copy all` + fallback Pillow (sin binario, dims no
      múltiplo de 16, o error de jpegtran)
- [ ] `_CROP` con `jpegtran -crop` lossless (pendiente OK del ±16 px)
- [ ] Test: el giro lossless NO altera píxeles y CONSERVA el XMP (GimbalYawDegree != 0)
- [ ] Gap ya existente: las RGB giradas pierden hoy el XMP (`af9d781` solo parcheó térmicas)
- [ ] Sonda de máquina falla en voz alta (`organize.py:768-771` try/except: pass)
- [ ] Quitar el `0` suelto del log crudo (comprobando que no oculta un error)
- [ ] Benchmark antes/después sobre una RGB 8000x6000 real
- [ ] `.venv/bin/python -m pytest -q` (baseline 1443) y `cd webui && npm test -- --run` (315→319)
