//! Fase `rgb` (T3): recorte + giro + encode de una imagen RGB, modo `a`.
//!
//! Modo `a` = equivalente a Pillow bit a bit: decode UNA vez (ISLOW + fancy
//! upsampling), `_CROP` = crop y luego giro (calidad_crop), original = giro
//! (calidad_original), y los dos encodes en `rayon::join` con los flags de
//! Pillow (`jpeg::codificar_rgb`). El EXIF llega ya serializado desde Python
//! (`getexif().tobytes()`) y se inserta como APP1 igual que `img.save(exif=)`.
//! El XMP NO lo toca Rust: Python lo inserta después (`insertar_xmp_app1`).
//! Rust escribe a las rutas `*.parcial` que le da Python (sin `fsync`: con XMP
//! Python reescribe el fichero, y Pillow tampoco lo hacía); el
//! `verificar_parcial` y el `os.replace` siguen en Python.
//!
//! Hilos: NUNCA el pool global de rayon. Cada llamada construye un pool LOCAL
//! (`ATOM_RS_HILOS`, defecto 2) y lo destruye al acabar. Un pool global creado en
//! el padre deja el registro sin hilos en un hijo `fork` (deadlock), y con un
//! `ProcessPoolExecutor` serían ncpu hilos por proceso (sobresuscripción).
//!
//! Decode no limpio: si libjpeg-turbo avisa o falla al decodificar (datos
//! corruptos, truncado), Rust NO da la imagen por buena ni escribe nada: devuelve
//! `motivo` y Python decide con Pillow (acepta o lanza, como siempre).
//!
//! Modo `b` (transformación DCT sin pérdida) está APLAZADO: se rechaza.

use crate::error::AtomError;
use crate::jpeg::{codificar_rgb, decodificar_rgb, devolver_buffer_rgb, validar_exif};
use pyo3::prelude::*;
use rayon::prelude::*;
use std::sync::OnceLock;
use std::time::Instant;

/// Resultado de `rgb_procesar`. Tiempos en ms (`encode_crop` = 0 sin `_CROP`).
#[pyclass(frozen, get_all, module = "atom_rs")]
#[derive(Debug, Clone, PartialEq)]
pub struct RgbStats {
    pub ancho_orig: u32,
    pub alto_orig: u32,
    pub ancho_crop: u32,
    pub alto_crop: u32,
    pub ms_decode: f64,
    pub ms_encode_orig: f64,
    pub ms_encode_crop: f64,
    /// `Some(..)`: Rust no ha procesado la imagen (decode no limpio) y no ha escrito
    /// nada; Python debe tomar el camino Pillow y contarlo.
    pub motivo: Option<String>,
}

#[pymethods]
impl RgbStats {
    fn __repr__(&self) -> String {
        format!(
            "RgbStats(orig={}x{}, crop={}x{}, ms_decode={:.1}, ms_encode_orig={:.1}, ms_encode_crop={:.1}, motivo={:?})",
            self.ancho_orig, self.alto_orig, self.ancho_crop, self.alto_crop,
            self.ms_decode, self.ms_encode_orig, self.ms_encode_crop, self.motivo
        )
    }
}

pub struct Parametros<'a> {
    pub datos_origen: &'a [u8],
    pub exif_bytes: Option<&'a [u8]>,
    pub caja: Option<(u32, u32, u32, u32)>,
    pub angulo: i32,
    pub calidad_original: u8,
    pub calidad_crop: u8,
    pub salida_original: &'a str,
    pub salida_crop: Option<&'a str>,
    pub modo: &'a str,
    pub comentario: Option<&'a [u8]>,
    /// Payload XMP (sin namespace) como APP1 tras el EXIF; `None` = sin XMP.
    pub xmp: Option<&'a [u8]>,
}

fn validar_calidad(q: u8, que: &str) -> Result<(), AtomError> {
    if q <= 100 {
        Ok(())
    } else {
        Err(AtomError::Entrada(format!("calidad JPEG de {que} fuera de 0..=100: {q}")))
    }
}

/// Giro de píxeles RGB compactos con el criterio de PIL `transpose` según
/// `termica::rotar`: 90 = horario (`ROTATE_270`), 270 = antihorario
/// (`ROTATE_90`). El resultado tiene `ancho` filas de `alto` píxeles. Se
/// reparte por bandas de filas de salida (rayon); dentro de la banda se recorre
/// la fuente por filas para leer contiguo.
pub fn rotar_rgb(src: &[u8], ancho: usize, alto: usize, grados: i32) -> Vec<u8> {
    const BANDA: usize = 16;
    if !matches!(grados, 90 | 270) {
        return src.to_vec();
    }
    let mut out = vec![0u8; src.len()];
    out.par_chunks_mut(BANDA * alto * 3)
        .enumerate()
        .for_each(|(b, trozo)| {
            let i0 = b * BANDA;
            let n = trozo.len() / (alto * 3); // filas de salida de esta banda
            for j in 0..alto {
                // Fila de la fuente que alimenta la columna `j` de la salida.
                let fila = if grados == 90 { alto - 1 - j } else { j };
                let base = fila * ancho * 3;
                for k in 0..n {
                    let i = i0 + k;
                    // 90: out[i][j] = a[alto-1-j][i]; 270: out[i][j] = a[j][ancho-1-i]
                    let col = if grados == 90 { i } else { ancho - 1 - i };
                    let s = base + col * 3;
                    let d = (k * alto + j) * 3;
                    trozo[d..d + 3].copy_from_slice(&src[s..s + 3]);
                }
            }
        });
    out
}

/// Recorte (`left, top, right, bottom`, como `Image.crop`) a píxeles compactos.
fn recortar_rgb(src: &[u8], ancho: usize, alto: usize, caja: (u32, u32, u32, u32)) -> Result<(Vec<u8>, usize, usize), AtomError> {
    let (l, t, r, b) = (caja.0 as usize, caja.1 as usize, caja.2 as usize, caja.3 as usize);
    if l >= r || t >= b || r > ancho || b > alto {
        return Err(AtomError::Entrada(format!(
            "caja de recorte {caja:?} fuera de la imagen {ancho}x{alto}"
        )));
    }
    let (cw, ch) = (r - l, b - t);
    let mut out = vec![0u8; cw * ch * 3];
    out.par_chunks_mut(cw * 3).enumerate().for_each(|(y, fila)| {
        let s = ((t + y) * ancho + l) * 3;
        fila.copy_from_slice(&src[s..s + cw * 3]);
    });
    Ok((out, cw, ch))
}

/// Escribe `datos` en `ruta`. Si algo falla, no deja el fichero.
fn escribir(ruta: &str, datos: &[u8]) -> Result<(), AtomError> {
    if let Err(e) = std::fs::write(ruta, datos) {
        let _ = std::fs::remove_file(ruta);
        return Err(AtomError::Io(format!("{ruta}: {e}")));
    }
    Ok(())
}

/// Hilos del pool: `ATOM_RS_HILOS` (>=1) o 1 (encode en serie, sin pool; con N
/// procesos no se sobresuscribe).
fn hilos() -> usize {
    std::env::var("ATOM_RS_HILOS")
        .ok()
        .and_then(|v| v.trim().parse::<usize>().ok())
        .filter(|&n| n >= 1)
        .unwrap_or(1)
}

/// Pool rayon compartido (solo con `ATOM_RS_HILOS` > 1), creado una vez.
static POOL: OnceLock<Result<rayon::ThreadPool, String>> = OnceLock::new();

fn pool(n: usize) -> Result<&'static rayon::ThreadPool, AtomError> {
    POOL.get_or_init(|| {
        rayon::ThreadPoolBuilder::new().num_threads(n).build().map_err(|e| e.to_string())
    })
    .as_ref()
    .map_err(|e| AtomError::Io(format!("pool de hilos: {e}")))
}

/// Codifica `px` y lo escribe en `ruta`; devuelve los ms de encode (sin escritura).
fn codificar_y_escribir(
    px: &[u8], w: usize, h: usize, calidad: u8, exif: Option<&[u8]>, xmp: Option<&[u8]>, com: Option<&[u8]>, ruta: &str,
) -> Result<f64, AtomError> {
    let t0 = Instant::now();
    let jpeg = codificar_rgb(px, w, h, calidad, exif, xmp, com)?;
    let ms = t0.elapsed().as_secs_f64() * 1000.0;
    escribir(ruta, &jpeg)?;
    Ok(ms)
}

/// Núcleo sin Python (testeable con `cargo test`).
pub fn procesar(p: &Parametros<'_>) -> Result<RgbStats, AtomError> {
    match p.modo {
        "a" => {}
        "b" => {
            return Err(AtomError::Entrada(
                "modo b (transformación DCT sin pérdida) aplazado: no implementado".into(),
            ))
        }
        otro => return Err(AtomError::Entrada(format!("modo debe ser 'a' o 'b': {otro:?}"))),
    }
    if !matches!(p.angulo, 0 | 90 | 270) {
        return Err(AtomError::Entrada(format!("angulo debe ser 0, 90 o 270: {}", p.angulo)));
    }
    validar_calidad(p.calidad_original, "original")?;
    if p.salida_crop.is_some() {
        validar_calidad(p.calidad_crop, "crop")?;
    }
    if p.caja.is_some() && p.salida_crop.is_none() {
        return Err(AtomError::Entrada("caja de recorte sin salida_crop".into()));
    }
    if let Some(e) = p.exif_bytes {
        validar_exif(e)?;
    }
    if let Some(x) = p.xmp.filter(|x| !x.is_empty()) {
        if x.len() + crate::jpeg::NS_XMP_APP1.len() > crate::jpeg::MAX_BYTES_APP1 {
            return Err(AtomError::Entrada("XMP data is too long".into()));
        }
    }

    let t0 = Instant::now();
    let (w, h, px) = match decodificar_rgb(p.datos_origen) {
        Ok(d) => d,
        // Decode no limpio: no se acepta ni se escribe nada; decide Pillow.
        Err(e) => {
            return Ok(RgbStats {
                ancho_orig: 0,
                alto_orig: 0,
                ancho_crop: 0,
                alto_crop: 0,
                ms_decode: t0.elapsed().as_secs_f64() * 1000.0,
                ms_encode_orig: 0.0,
                ms_encode_crop: 0.0,
                motivo: Some(e.to_string()),
            })
        }
    };
    let ms_decode = t0.elapsed().as_secs_f64() * 1000.0;

    let n = hilos();
    let r = if n <= 1 {
        transformar_y_escribir(p, w, h, &px, ms_decode, false)
    } else {
        match pool(n) {
            Ok(pool) => pool.install(|| transformar_y_escribir(p, w, h, &px, ms_decode, true)),
            Err(e) => Err(e),
        }
    };
    devolver_buffer_rgb(px);
    r
}

/// Crop + giro + encode x2 + escritura. Con `paralelo` se ejecuta DENTRO del pool.
fn transformar_y_escribir(
    p: &Parametros<'_>,
    w: usize,
    h: usize,
    px: &[u8],
    ms_decode: f64,
    paralelo: bool,
) -> Result<RgbStats, AtomError> {
    // _CROP: recorte en coordenadas del origen SIN girar y luego giro.
    let crop: Option<(Vec<u8>, usize, usize)> = match (p.salida_crop, p.caja) {
        (None, _) => None,
        (Some(_), Some(caja)) => {
            let (c, cw, ch) = recortar_rgb(px, w, h, caja)?;
            let c = rotar_rgb(&c, cw, ch, p.angulo);
            let (cw, ch) = if p.angulo == 0 { (cw, ch) } else { (ch, cw) };
            Some((c, cw, ch))
        }
        // Sin caja (`pct_recorte` None): copia íntegra, con el giro si toca.
        (Some(_), None) => {
            let c = rotar_rgb(px, w, h, p.angulo);
            let (cw, ch) = if p.angulo == 0 { (w, h) } else { (h, w) };
            Some((c, cw, ch))
        }
    };
    let girada;
    let (orig, wo, ho): (&[u8], usize, usize) = if p.angulo == 0 {
        (px, w, h)
    } else {
        girada = rotar_rgb(px, w, h, p.angulo);
        (&girada, h, w)
    };

    let exif = p.exif_bytes;
    let com = p.comentario;
    let xmp = p.xmp;
    let enc_crop = || match (&crop, p.salida_crop) {
        (Some((c, cw, ch)), Some(ruta)) => {
            codificar_y_escribir(c, *cw, *ch, p.calidad_crop, exif, xmp, com, ruta).map(Some)
        }
        _ => Ok(None),
    };
    let enc_orig =
        || codificar_y_escribir(orig, wo, ho, p.calidad_original, exif, xmp, com, p.salida_original);
    let (r_crop, r_orig) = if paralelo {
        rayon::join(enc_crop, enc_orig)
    } else {
        (enc_crop(), enc_orig())
    };
    match (r_crop, r_orig) {
        (Ok(ms_crop), Ok(ms_orig)) => {
            let (ancho_crop, alto_crop) = crop.as_ref().map_or((0, 0), |(_, cw, ch)| (*cw as u32, *ch as u32));
            Ok(RgbStats {
                ancho_orig: wo as u32,
                alto_orig: ho as u32,
                ancho_crop,
                alto_crop,
                ms_decode,
                ms_encode_orig: ms_orig,
                ms_encode_crop: ms_crop.unwrap_or(0.0),
                motivo: None,
            })
        }
        (r_crop, r_orig) => {
            // Todo o nada dentro de esta llamada: no queda ningún `.parcial`.
            if let Some(ruta) = p.salida_crop {
                let _ = std::fs::remove_file(ruta);
            }
            let _ = std::fs::remove_file(p.salida_original);
            Err(r_crop.err().or(r_orig.err()).expect("al menos un error"))
        }
    }
}

/// Puente Python. Libera el GIL. `ValueError` si la entrada no es válida,
/// `OSError` si falla el decode o la E/S. Ante cualquier error no deja ninguno
/// de los dos `.parcial`.
#[pyfunction]
#[pyo3(signature = (datos_origen, exif_bytes, caja, angulo, calidad_original, calidad_crop, salida_original, salida_crop, modo, comentario=None, xmp=None))]
#[allow(clippy::too_many_arguments)]
pub fn rgb_procesar(
    py: Python<'_>,
    datos_origen: &[u8],
    exif_bytes: Option<&[u8]>,
    caja: Option<(u32, u32, u32, u32)>,
    angulo: i32,
    calidad_original: u8,
    calidad_crop: u8,
    salida_original: &str,
    salida_crop: Option<&str>,
    modo: &str,
    comentario: Option<&[u8]>,
    xmp: Option<&[u8]>,
) -> PyResult<RgbStats> {
    let p = Parametros {
        datos_origen,
        exif_bytes,
        caja,
        angulo,
        calidad_original,
        calidad_crop,
        salida_original,
        salida_crop,
        modo,
        comentario,
        xmp,
    };
    Ok(py.allow_threads(|| procesar(&p))?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::termica::rotar;

    fn imagen(w: usize, h: usize) -> Vec<u8> {
        (0..w * h * 3).map(|i| ((i * 7 + i / 5) % 253) as u8).collect()
    }

    fn dir_tmp(nombre: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("atom_rs_rgb_{nombre}_{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn rotar_rgb_como_rotar_generico() {
        // 37x21 no es múltiplo de la banda (16) ni de 8.
        for &(w, h) in &[(37usize, 21usize), (16, 16), (5, 40), (1, 3)] {
            let src = imagen(w, h);
            let px: Vec<[u8; 3]> = src.chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect();
            for g in [90, 270] {
                let esperado: Vec<u8> = rotar(&px, w, h, g).iter().flatten().copied().collect();
                assert_eq!(rotar_rgb(&src, w, h, g), esperado, "{w}x{h} giro {g}");
            }
            assert_eq!(rotar_rgb(&src, w, h, 0), src);
        }
    }

    #[test]
    fn recortar_extrae_la_caja() {
        let src = imagen(10, 8);
        let (c, cw, ch) = recortar_rgb(&src, 10, 8, (2, 1, 6, 5)).unwrap();
        assert_eq!((cw, ch), (4, 4));
        assert_eq!(&c[..3], &src[(10 + 2) * 3..(10 + 2) * 3 + 3]);
        assert!(recortar_rgb(&src, 10, 8, (2, 1, 11, 5)).is_err());
        assert!(recortar_rgb(&src, 10, 8, (6, 1, 6, 5)).is_err());
    }

    fn params<'a>(datos: &'a [u8], d: &'a str, c: Option<&'a str>, angulo: i32) -> Parametros<'a> {
        Parametros {
            datos_origen: datos,
            exif_bytes: Some(b"Exif\0\0ab"),
            caja: if c.is_some() { Some((8, 4, 40, 36)) } else { None },
            angulo,
            calidad_original: 40,
            calidad_crop: 75,
            salida_original: d,
            salida_crop: c,
            modo: "a",
            comentario: None,
            xmp: None,
        }
    }

    #[test]
    fn procesa_con_crop_y_giro_y_dimensiones() {
        let (w, h) = (61usize, 45usize);
        let origen = crate::jpeg::codificar_rgb(&imagen(w, h), w, h, 95, None, None, None).unwrap();
        let dir = dir_tmp("giro");
        let (o, c) = (dir.join("o.parcial.jpg"), dir.join("c.parcial.jpg"));
        let (os_, cs) = (o.to_str().unwrap(), c.to_str().unwrap());
        let st = procesar(&params(&origen, os_, Some(cs), 90)).unwrap();
        assert_eq!((st.ancho_orig, st.alto_orig), (h as u32, w as u32));
        assert_eq!((st.ancho_crop, st.alto_crop), (32, 32)); // caja (8,4,40,36) = 32x32, girada sigue 32x32
        let (dw, dh, _) = decodificar_rgb(&std::fs::read(&o).unwrap()).unwrap();
        assert_eq!((dw, dh), (h, w));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn modo_b_y_angulo_invalido_fallan_sin_dejar_ficheros() {
        let origen = crate::jpeg::codificar_rgb(&imagen(16, 16), 16, 16, 90, None, None, None).unwrap();
        let dir = dir_tmp("err");
        let o = dir.join("o.jpg");
        let os_ = o.to_str().unwrap();
        let mut p = params(&origen, os_, None, 0);
        p.modo = "b";
        assert!(procesar(&p).is_err());
        let p = params(&origen, os_, None, 45);
        assert!(procesar(&p).is_err());
        assert!(!o.exists());
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn jpeg_roto_falla_sin_dejar_ficheros() {
        let dir = dir_tmp("roto");
        let o = dir.join("o.jpg");
        let st = procesar(&params(&[0xFF, 0xD8, 1, 2, 3], o.to_str().unwrap(), None, 0)).unwrap();
        assert!(st.motivo.is_some(), "decode no limpio: decide Pillow");
        assert!(!o.exists());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
