//! Fase `termica`: del `.raw` float32 del SDK de DJI al TIFF float32 + previews
//! (escala de grises y colormap), bit a bit iguales a la rama Python de
//! `SplitImages.convert_dji_image_to_tif` (`src/pipeline.py`).
//!
//! Orden (idéntico al de Python):
//! 1. `arr = np.frombuffer(raw, "<f4").astype(np.float64)`; aquí el TIFF se
//!    queda en f32 (f32 -> f64 -> f32 es exacto) y la normalización va en f64.
//! 2. `array_to_normalize`: si `!auto_temp`, `a[a > up] = up` y DESPUÉS
//!    `a[a < low] = low` (secuencial, no `clamp`: con `low > up` gana `low`).
//! 3. Giro de ambos con `rotar_arrays_termicos` (`np.rot90`): 90 = horario
//!    `rot90(a, 1, (1, 0))`, 270 = antihorario `rot90(a, 1, (0, 1))`.
//! 4. Gris: `((a - min) / (max - min)) * 255` en f64 -> `astype(np.uint8)`.
//! 5. Colormap: `apply_thermal_colormap(a, low, up)` con la LUT 1024x3 que
//!    pasa Python (`_get_thermal_lut`).
//! 6. TIFF float32 (`tiff_f32`).
//!
//! Escritura: TIFF, gris y color se codifican en paralelo (`rayon::join`) a
//! `.parcial`; solo si TODO sale bien se publican, el TIFF el ÚLTIMO (es el
//! marcador de "imagen ya convertida" que mira la reejecución). Si algo falla
//! no queda ninguna salida ni ningún `.parcial`.

use crate::error::AtomError;
use crate::tiff_f32;
use pyo3::prelude::*;

/// Filas de la LUT de colormap (`pipeline._LUT_SIZE`).
pub const LUT_SIZE: usize = 1024;

/// Versión de libjpeg-turbo enlazada. Por defecto, la que vendoriza
/// `turbojpeg-sys` =1.2.0 (3.1.0, compilada con cmake). Si la wheel se enlaza
/// contra otra (`TURBOJPEG_SOURCE=explicit`), quien construye debe exportar
/// `ATOM_RS_LIBJPEG_TURBO_VERSION` con la versión exacta: el A/B la compara con
/// `PIL.features.version("libjpeg_turbo")` para exigir sha256 idéntico.
pub const LIBJPEG_TURBO_VERSION: &str = match option_env!("ATOM_RS_LIBJPEG_TURBO_VERSION") {
    Some(v) => v,
    None => "3.1.0",
};

/// Resultado de `termica_procesar`. `min`/`max`: temperaturas del `.raw` (sin
/// umbrales); `ancho_final`/`alto_final`: tamaño del TIFF ya girado.
#[pyclass(frozen, get_all, module = "atom_rs")]
#[derive(Debug, Clone, PartialEq)]
pub struct TermicaStats {
    pub min: f64,
    pub max: f64,
    pub ancho_final: u32,
    pub alto_final: u32,
}

#[pymethods]
impl TermicaStats {
    fn __repr__(&self) -> String {
        format!(
            "TermicaStats(min={}, max={}, ancho_final={}, alto_final={})",
            self.min, self.max, self.ancho_final, self.alto_final
        )
    }
}

/// Entrada de `procesar` (misma semántica que los argumentos de `termica_procesar`).
pub struct Parametros<'a> {
    pub raw: &'a [u8],
    pub ancho: u32,
    pub alto: u32,
    pub auto_temp: bool,
    pub t_min: f64,
    pub t_max: f64,
    pub grados: i32,
    pub tiff_out: &'a str,
    pub gris_out: Option<&'a str>,
    pub color_out: Option<&'a str>,
    pub lut: Option<&'a [u8]>,
    pub jpeg_quality_gris: u8,
    pub jpeg_quality_color: u8,
}

/// `np.float64 -> np.uint8` tal como lo hace NumPy 2.4.6 en x86_64 (medido):
/// NaN, ±inf y fuera de rango de i32 -> 0; el resto se trunca hacia cero a i32
/// y se queda el byte bajo (-1 -> 255, 256 -> 0, 300.7 -> 44, -255.5 -> 1).
/// NO es `x as u8` de Rust (satura: 300.7 -> 255). En el uso real (gris) el
/// valor está en [0, 255] o es NaN (frame constante, 0/0), y ahí ambos coinciden.
#[inline]
pub fn numpy_f64_a_u8(x: f64) -> u8 {
    if x.is_finite() && (-2_147_483_648.0..2_147_483_648.0).contains(&x) {
        (x as i32) as u8
    } else {
        0
    }
}

/// `np.float64 -> np.uint16`, mismo criterio que `numpy_f64_a_u8`.
#[inline]
pub fn numpy_f64_a_u16(x: f64) -> u16 {
    if x.is_finite() && (-2_147_483_648.0..2_147_483_648.0).contains(&x) {
        (x as i32) as u16
    } else {
        0
    }
}

/// `np.rot90` con el criterio de `rotar_arrays_termicos`. `src` es `alto` filas
/// de `ancho`; con 90/270 el resultado es `ancho` filas de `alto`.
pub fn rotar<T: Copy>(src: &[T], ancho: usize, alto: usize, grados: i32) -> Vec<T> {
    match grados {
        // rot90(a, 1, (1, 0)) = horario: out[i][j] = a[alto-1-j][i]
        90 => {
            let mut out = Vec::with_capacity(src.len());
            for i in 0..ancho {
                for j in 0..alto {
                    out.push(src[(alto - 1 - j) * ancho + i]);
                }
            }
            out
        }
        // rot90(a, 1, (0, 1)) = antihorario: out[i][j] = a[j][ancho-1-i]
        270 => {
            let mut out = Vec::with_capacity(src.len());
            for i in 0..ancho {
                let col = ancho - 1 - i;
                for j in 0..alto {
                    out.push(src[j * ancho + col]);
                }
            }
            out
        }
        _ => src.to_vec(),
    }
}

/// Umbrales de la rama Python, en su orden: primero `> up`, luego `< low`.
#[inline]
fn umbral(x: f64, low: f64, up: f64) -> f64 {
    let y = if x > up { up } else { x };
    if y < low {
        low
    } else {
        y
    }
}

/// `np.min`/`np.max` (sin NaN: el `.raw` con NaN se rechaza antes).
fn min_max(a: &[f64]) -> (f64, f64) {
    let mut mn = f64::INFINITY;
    let mut mx = f64::NEG_INFINITY;
    for &x in a {
        if x < mn {
            mn = x;
        }
        if x > mx {
            mx = x;
        }
    }
    (mn, mx)
}

/// Escala de grises: `((a - min) / (max - min)) * 255` -> `astype(np.uint8)`.
/// Frame constante: 0/0 = NaN -> 0, igual que NumPy (sin pánico).
pub fn gris_u8(a: &[f64]) -> Vec<u8> {
    let (mn, mx) = min_max(a);
    let d = mx - mn;
    a.iter().map(|&x| numpy_f64_a_u8((x - mn) / d * 255.0)).collect()
}

/// `apply_thermal_colormap(a, t_min, t_max)` con la LUT ya calculada en Python.
pub fn colormap_rgb(a: &[f64], t_min: f64, t_max: f64, lut: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(a.len() * 3);
    let rango = t_max - t_min;
    for &x in a {
        let idx = if t_max > t_min {
            // np.clip(a, t_min, t_max) = minimum(maximum(a, t_min), t_max)
            let c = if x < t_min { t_min } else { x };
            let c = if c > t_max { t_max } else { c };
            let nrm = (c - t_min) / rango;
            (numpy_f64_a_u16(nrm * LUT_SIZE as f64) as usize).min(LUT_SIZE - 1)
        } else {
            0
        };
        out.extend_from_slice(&lut[idx * 3..idx * 3 + 3]);
    }
    out
}

fn err_jpeg(e: turbojpeg::Error) -> AtomError {
    AtomError::Io(format!("libjpeg-turbo: {e}"))
}

/// JPEG como `Image.fromarray(...).save(path, format='JPEG')` de Pillow: baseline,
/// DCT ISLOW, sin optimize/progresivo, JFIF por defecto; RGB en 4:2:0.
///
/// OJO con la DCT: la API TurboJPEG 2.x (`tjCompress2`) usa IFAST salvo
/// `TJFLAG_ACCURATEDCT` y da bytes Y píxeles distintos de Pillow (medido con
/// libjpeg-turbo 2.1.5 de Debian contra Pillow 9.4.0 enlazado a la misma). La
/// API 3.x que usa el crate `turbojpeg` (`tj3Init` pone `fastDCT = FALSE`)
/// usa ISLOW, que es lo que hace Pillow (`jpeg_set_defaults`). El crate no
/// expone TJPARAM_FASTDCT; si algún día cambia ese defecto, el A/B lo detecta.
pub fn codificar_jpeg(
    pixeles: &[u8],
    ancho: usize,
    alto: usize,
    gris: bool,
    calidad: u8,
) -> Result<Vec<u8>, AtomError> {
    let (formato, subsamp) = if gris {
        (turbojpeg::PixelFormat::GRAY, turbojpeg::Subsamp::Gray)
    } else {
        (turbojpeg::PixelFormat::RGB, turbojpeg::Subsamp::Sub2x2)
    };
    let mut c = turbojpeg::Compressor::new().map_err(err_jpeg)?;
    c.set_quality(calidad as i32).map_err(err_jpeg)?;
    c.set_subsamp(subsamp).map_err(err_jpeg)?;
    let img = turbojpeg::Image {
        pixels: pixeles,
        width: ancho,
        pitch: ancho * formato.size(),
        height: alto,
        format: formato,
    };
    c.compress_to_vec(img).map_err(err_jpeg)
}

fn validar_calidad(q: u8, que: &str) -> Result<(), AtomError> {
    if (1..=100).contains(&q) {
        Ok(())
    } else {
        Err(AtomError::Entrada(format!("calidad JPEG de {que} fuera de 1..=100: {q}")))
    }
}

/// Núcleo sin Python (testeable con `cargo test`).
pub fn procesar(p: &Parametros<'_>) -> Result<TermicaStats, AtomError> {
    let (w, h) = (p.ancho as usize, p.alto as usize);
    if w == 0 || h == 0 {
        return Err(AtomError::Entrada(format!("resolución nula: {w}x{h}")));
    }
    let n = w
        .checked_mul(h)
        .ok_or_else(|| AtomError::Entrada(format!("resolución desbordada: {w}x{h}")))?;
    let esperado = n
        .checked_mul(4)
        .ok_or_else(|| AtomError::Entrada(format!("resolución desbordada: {w}x{h}")))?;
    if p.raw.len() != esperado {
        return Err(AtomError::Entrada(format!(
            "tamaño de raw incoherente: {} bytes, se esperaban {esperado} ({w}x{h} float32)",
            p.raw.len()
        )));
    }
    if !matches!(p.grados, 0 | 90 | 270) {
        return Err(AtomError::Entrada(format!("grados debe ser 0, 90 o 270: {}", p.grados)));
    }
    let lut = match (p.color_out, p.lut) {
        (_, Some(l)) if l.len() != LUT_SIZE * 3 => {
            return Err(AtomError::Entrada(format!(
                "LUT de {} bytes, se esperaban {} ({LUT_SIZE}x3 uint8)",
                l.len(),
                LUT_SIZE * 3
            )))
        }
        (Some(_), None) => {
            return Err(AtomError::Entrada("color_out sin LUT".to_string()));
        }
        (_, l) => l,
    };
    if p.gris_out.is_some() {
        validar_calidad(p.jpeg_quality_gris, "gris")?;
    }
    if p.color_out.is_some() {
        validar_calidad(p.jpeg_quality_color, "color")?;
    }

    // 1. Decodificar y rechazar NaN/±inf ANTES de escribir nada (Python solo
    //    rechaza NaN; aquí también ±inf: dato no finito = error visible).
    let mut temps: Vec<f32> = Vec::with_capacity(n);
    // Tamaño ya validado (`n * 4`): el resto de `as_chunks` está vacío.
    let (pixeles, _resto) = p.raw.as_chunks::<4>();
    for (i, c) in pixeles.iter().enumerate() {
        let v = f32::from_le_bytes(*c);
        if !v.is_finite() {
            return Err(AtomError::Entrada(format!(
                "raw con valor no finito ({v}) en el píxel {i} (fila {}, columna {}); no se escribe el TIFF",
                i / w,
                i % w
            )));
        }
        temps.push(v);
    }
    let arr64: Vec<f64> = temps.iter().map(|&v| v as f64).collect();
    let (min, max) = min_max(&arr64);

    let (wf, hf) = if p.grados == 0 { (w, h) } else { (h, w) };
    let necesita_norm = p.gris_out.is_some() || p.color_out.is_some();

    // 2-3. Umbrales (solo con auto_temp = false) y giro de ambos arrays.
    let norm: Option<Vec<f64>> = if necesita_norm {
        let base: Vec<f64> = if p.auto_temp {
            arr64
        } else {
            arr64.iter().map(|&x| umbral(x, p.t_min, p.t_max)).collect()
        };
        Some(rotar(&base, w, h, p.grados))
    } else {
        drop(arr64);
        None
    };

    // 4-6. Codificar en paralelo a `.parcial`.
    let tarea_tiff = || -> Result<String, AtomError> {
        let girado = rotar(&temps, w, h, p.grados);
        let bytes = tiff_f32::codificar(wf as u32, hf as u32, &girado)?;
        tiff_f32::escribir_parcial(p.tiff_out, &bytes)
    };
    let tarea_gris = || -> Result<Option<String>, AtomError> {
        match (p.gris_out, norm.as_deref()) {
            (Some(out), Some(a)) => {
                let g = gris_u8(a);
                let jpg = codificar_jpeg(&g, wf, hf, true, p.jpeg_quality_gris)?;
                tiff_f32::escribir_parcial(out, &jpg).map(Some)
            }
            _ => Ok(None),
        }
    };
    let tarea_color = || -> Result<Option<String>, AtomError> {
        match (p.color_out, norm.as_deref(), lut) {
            (Some(out), Some(a), Some(l)) => {
                let rgb = colormap_rgb(a, p.t_min, p.t_max, l);
                let jpg = codificar_jpeg(&rgb, wf, hf, false, p.jpeg_quality_color)?;
                tiff_f32::escribir_parcial(out, &jpg).map(Some)
            }
            _ => Ok(None),
        }
    };
    let (r_tiff, (r_gris, r_color)) =
        rayon::join(tarea_tiff, || rayon::join(tarea_gris, tarea_color));

    // Publicar solo si TODO salió bien; si no, borrar los `.parcial` que haya.
    let limpiar = |rs: &[Option<&String>]| {
        for parcial in rs.iter().flatten() {
            let _ = std::fs::remove_file(parcial);
        }
    };
    let (parcial_tiff, parcial_gris, parcial_color) = match (r_tiff, r_gris, r_color) {
        (Ok(t), Ok(g), Ok(c)) => (t, g, c),
        (t, g, c) => {
            let t_ok = t.as_ref().ok();
            let g_ok = g.as_ref().ok().and_then(|x| x.as_ref());
            let c_ok = c.as_ref().ok().and_then(|x| x.as_ref());
            limpiar(&[t_ok, g_ok, c_ok]);
            // Primer error en el orden de Python: gris, color, TIFF.
            g?;
            c?;
            t?;
            unreachable!("algún resultado era Err");
        }
    };
    let publicar_todo = || -> Result<(), AtomError> {
        if let (Some(parcial), Some(out)) = (&parcial_gris, p.gris_out) {
            tiff_f32::publicar(parcial, out)?;
        }
        if let (Some(parcial), Some(out)) = (&parcial_color, p.color_out) {
            tiff_f32::publicar(parcial, out)?;
        }
        tiff_f32::publicar(&parcial_tiff, p.tiff_out)
    };
    if let Err(e) = publicar_todo() {
        limpiar(&[Some(&parcial_tiff), parcial_gris.as_ref(), parcial_color.as_ref()]);
        return Err(e);
    }

    Ok(TermicaStats {
        min,
        max,
        ancho_final: wf as u32,
        alto_final: hf as u32,
    })
}

/// Puente Python. Libera el GIL mientras trabaja. `ValueError` si la entrada no
/// es válida (NaN, tamaño incoherente, grados, LUT); `OSError` si falla la E/S.
#[pyfunction]
#[pyo3(signature = (raw, ancho, alto, auto_temp, t_min, t_max, grados, tiff_out, gris_out, color_out, lut, jpeg_quality_gris, jpeg_quality_color))]
#[allow(clippy::too_many_arguments)]
pub fn termica_procesar(
    py: Python<'_>,
    raw: &[u8],
    ancho: u32,
    alto: u32,
    auto_temp: bool,
    t_min: f64,
    t_max: f64,
    grados: i32,
    tiff_out: &str,
    gris_out: Option<&str>,
    color_out: Option<&str>,
    lut: Option<&[u8]>,
    jpeg_quality_gris: u8,
    jpeg_quality_color: u8,
) -> PyResult<TermicaStats> {
    let p = Parametros {
        raw,
        ancho,
        alto,
        auto_temp,
        t_min,
        t_max,
        grados,
        tiff_out,
        gris_out,
        color_out,
        lut,
        jpeg_quality_gris,
        jpeg_quality_color,
    };
    let stats = py.allow_threads(|| procesar(&p))?;
    Ok(stats)
}

/// Versión de libjpeg-turbo con la que se codifican gris y colormap.
#[pyfunction]
pub fn libjpeg_turbo_version() -> &'static str {
    LIBJPEG_TURBO_VERSION
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cast_como_numpy_x86_64() {
        // Medido con numpy 2.4.6 en x86_64 (atom-dev-nl).
        assert_eq!(numpy_f64_a_u8(f64::NAN), 0);
        assert_eq!(numpy_f64_a_u8(f64::INFINITY), 0);
        assert_eq!(numpy_f64_a_u8(f64::NEG_INFINITY), 0);
        assert_eq!(numpy_f64_a_u8(-1.0), 255);
        assert_eq!(numpy_f64_a_u8(-255.5), 1);
        assert_eq!(numpy_f64_a_u8(256.0), 0);
        assert_eq!(numpy_f64_a_u8(300.7), 44);
        assert_eq!(numpy_f64_a_u8(2147483653.0), 0);
        assert_eq!(numpy_f64_a_u8(2147483647.0), 255);
        assert_eq!(numpy_f64_a_u8(254.99999), 254);
        assert_eq!(numpy_f64_a_u8(255.0), 255);
    }

    #[test]
    fn rotar_como_np_rot90() {
        // s = [[0,1,2],[3,4,5]] (alto 2, ancho 3)
        let s = [0, 1, 2, 3, 4, 5];
        assert_eq!(rotar(&s, 3, 2, 90), vec![3, 0, 4, 1, 5, 2]);
        assert_eq!(rotar(&s, 3, 2, 270), vec![2, 5, 1, 4, 0, 3]);
        assert_eq!(rotar(&s, 3, 2, 0), s.to_vec());
    }

    #[test]
    fn umbral_secuencial_como_python() {
        assert_eq!(umbral(50.0, 0.0, 40.0), 40.0);
        assert_eq!(umbral(-5.0, 0.0, 40.0), 0.0);
        // low > up: primero baja a up y luego sube a low.
        assert_eq!(umbral(50.0, 30.0, 20.0), 30.0);
    }

    #[test]
    fn gris_frame_constante_sin_panico() {
        assert_eq!(gris_u8(&[25.0; 6]), vec![0u8; 6]);
        assert_eq!(gris_u8(&[0.0, 10.0, 5.0]), vec![0, 255, 127]);
    }

    #[test]
    fn colormap_indices() {
        let mut lut = vec![0u8; LUT_SIZE * 3];
        for i in 0..LUT_SIZE {
            lut[i * 3] = (i % 256) as u8;
            lut[i * 3 + 1] = (i / 256) as u8;
        }
        let rgb = colormap_rgb(&[-10.0, 0.0, 35.0, 70.0, 99.0], 0.0, 70.0, &lut);
        let idx: Vec<usize> = rgb
            .chunks(3)
            .map(|c| c[0] as usize + 256 * c[1] as usize)
            .collect();
        assert_eq!(idx, vec![0, 0, 512, 1023, 1023]);
        // t_max <= t_min: todo al índice 0.
        let rgb = colormap_rgb(&[5.0], 10.0, 10.0, &lut);
        assert_eq!(rgb, vec![0u8, 0, 0]);
    }

    fn params<'a>(raw: &'a [u8], tiff: &'a str) -> Parametros<'a> {
        Parametros {
            raw,
            ancho: 3,
            alto: 2,
            auto_temp: true,
            t_min: 0.0,
            t_max: 70.0,
            grados: 0,
            tiff_out: tiff,
            gris_out: None,
            color_out: None,
            lut: None,
            jpeg_quality_gris: 75,
            jpeg_quality_color: 75,
        }
    }

    fn raw_de(v: &[f32]) -> Vec<u8> {
        v.iter().flat_map(|x| x.to_le_bytes()).collect()
    }

    fn dir_tmp(nombre: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("atom_rs_{nombre}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn nan_no_escribe_nada() {
        let d = dir_tmp("nan");
        let tiff = d.join("x.tiff");
        let raw = raw_de(&[1.0, 2.0, f32::NAN, 4.0, 5.0, 6.0]);
        let t = tiff.to_string_lossy().into_owned();
        let e = procesar(&params(&raw, &t)).unwrap_err();
        assert!(matches!(e, AtomError::Entrada(_)));
        assert!(std::fs::read_dir(&d).unwrap().next().is_none());
    }

    #[test]
    fn inf_no_escribe_nada() {
        for (k, malo) in [f32::INFINITY, f32::NEG_INFINITY].into_iter().enumerate() {
            let d = dir_tmp(&format!("inf{k}"));
            let tiff = d.join("x.tiff");
            let raw = raw_de(&[1.0, 2.0, 3.0, 4.0, malo, 6.0]);
            let t = tiff.to_string_lossy().into_owned();
            let e = procesar(&params(&raw, &t)).unwrap_err();
            match e {
                AtomError::Entrada(m) => assert!(m.contains("inf"), "{m}"),
                otro => panic!("se esperaba Entrada, no {otro:?}"),
            }
            assert!(std::fs::read_dir(&d).unwrap().next().is_none());
        }
    }

    #[test]
    fn tamano_incoherente() {
        let raw = raw_de(&[1.0; 5]);
        let e = procesar(&params(&raw, "/nunca/x.tiff")).unwrap_err();
        assert!(matches!(e, AtomError::Entrada(_)));
    }

    #[test]
    fn tiff_girado_y_stats() {
        let d = dir_tmp("giro");
        let tiff = d.join("x.tiff");
        let t = tiff.to_string_lossy().into_owned();
        let raw = raw_de(&[0.0, 1.0, 2.0, 3.0, 4.0, 5.0]);
        let mut p = params(&raw, &t);
        p.grados = 90;
        let s = procesar(&p).unwrap();
        assert_eq!((s.ancho_final, s.alto_final, s.min, s.max), (2, 3, 0.0, 5.0));
        let b = std::fs::read(&tiff).unwrap();
        let esperado = tiff_f32::codificar(2, 3, &[3.0, 0.0, 4.0, 1.0, 5.0, 2.0]).unwrap();
        assert_eq!(b, esperado);
        let nombres: Vec<_> = std::fs::read_dir(&d).unwrap().map(|e| e.unwrap().file_name()).collect();
        assert_eq!(nombres.len(), 1);
    }
}
