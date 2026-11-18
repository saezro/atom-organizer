//! Fase `rgb` (T3): recorte + giro + encode de una imagen RGB, modo `a`.
//!
//! Modo `a` = paridad byte-idéntica con Pillow (referencia de comportamiento): decode UNA vez (ISLOW + fancy
//! upsampling), `_CROP` = crop y luego giro (calidad_crop), original = giro
//! (calidad_original), y los dos encodes en `rayon::join` con los flags de
//! Pillow (`jpeg::codificar_rgb`). El EXIF llega ya serializado desde Python
//! (`getexif().tobytes()`) y se inserta como APP1 igual que `img.save(exif=)`.
//! El XMP NO lo toca Rust: Python lo inserta después (`insertar_xmp_app1`).
//! Rust escribe a las rutas `*.parcial` que le da Python (sin `fsync`: con XMP
//! Python reescribe el fichero, y Pillow tampoco lo hacía); el
//! `verificar_parcial` y el `os.replace` siguen en Python.
//!
//! Hilos: NUNCA el pool global de rayon. Todo uso de rayon va dentro de
//! `pool(n).install(..)`, un pool propio que recuerda el pid que lo creó: tras un
//! `fork` el hijo no hereda hilos, así que ve otro pid, olvida (sin drop) el pool
//! del padre y crea uno nuevo. Un pool global creado en el padre dejaría el
//! registro sin hilos en el hijo (deadlock).
//!
//! Modos y orígenes (solo Rust, sin retroceso a Pillow): JPEG L/RGB/CMYK (decode y
//! encode en el mismo modo), PNG y TIFF (crates `png`/`tiff`) -> encode con
//! libjpeg-turbo. RGBA/LA/P/I;16 no se pueden escribir como JPEG: error con el
//! mismo texto que Pillow (`cannot write mode X as JPEG`, `ErrorFila` tipo `io`).
//! El decode JPEG es tolerante a avisos de libjpeg como Pillow; un fallo fatal es
//! error de fila visible. El JPEG truncado sin EOI lo rechaza antes la lectura
//! verificada (`_leer_origen_verificado`).
//! La caja de recorte puede salirse de la imagen: se rellena como `Image.crop`.
//!
//! Modo `b` (transformación DCT sin pérdida) está APLAZADO: se rechaza.

use crate::error::AtomError;
use crate::error::DatosErrorFila;
use crate::jpeg::{codificar, decodificar_jpeg, devolver_buffer_rgb, validar_exif, Imagen, Modo};
use pyo3::prelude::*;
use rayon::prelude::*;
use std::sync::Mutex;
use std::time::Instant;

/// Error de `procesar`: `Atom` = ValueError/OSError; `Modo` = modo de píxel que
/// Pillow tampoco puede escribir como JPEG (texto `cannot write mode X as JPEG`).
#[derive(Debug)]
pub enum ErrRgb {
    Atom(AtomError),
    Modo(String),
}

impl From<AtomError> for ErrRgb {
    fn from(e: AtomError) -> Self {
        ErrRgb::Atom(e)
    }
}

fn sin_jpeg(modo_pillow: &str) -> ErrRgb {
    ErrRgb::Modo(format!("cannot write mode {modo_pillow} as JPEG"))
}

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
    /// Siempre `None` (campo conservado por compatibilidad; los fallos son excepción).
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
    /// `(left, top, right, bottom)` como `Image.crop`; puede salirse de la imagen.
    pub caja: Option<(i64, i64, i64, i64)>,
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

/// Giro de píxeles RGB compactos (ver `rotar_px`).
pub fn rotar_rgb(src: &[u8], ancho: usize, alto: usize, grados: i32) -> Vec<u8> {
    rotar_px(src, ancho, alto, grados, 3)
}

/// Giro de píxeles de `bpp` bytes con el criterio de PIL `transpose` según
/// `termica::rotar`: 90 = horario (`ROTATE_270`), 270 = antihorario
/// (`ROTATE_90`). El resultado tiene `ancho` filas de `alto` píxeles. Se
/// reparte por bandas de filas de salida (rayon); dentro de la banda se recorre
/// la fuente por filas para leer contiguo.
pub fn rotar_px(src: &[u8], ancho: usize, alto: usize, grados: i32, bpp: usize) -> Vec<u8> {
    const BANDA: usize = 16;
    if !matches!(grados, 90 | 270) {
        return src.to_vec();
    }
    let mut out = vec![0u8; src.len()];
    let Ok(pool) = pool_bandas() else {
        return rotar_serie(src, ancho, alto, grados, bpp);
    };
    pool.install(|| {
        out.par_chunks_mut(BANDA * alto * bpp)
            .enumerate()
            .for_each(|(b, trozo)| {
                let i0 = b * BANDA;
                let n = trozo.len() / (alto * bpp); // filas de salida de esta banda
                for j in 0..alto {
                    // Fila de la fuente que alimenta la columna `j` de la salida.
                    let fila = if grados == 90 { alto - 1 - j } else { j };
                    let base = fila * ancho * bpp;
                    for k in 0..n {
                        let i = i0 + k;
                        // 90: out[i][j] = a[alto-1-j][i]; 270: out[i][j] = a[j][ancho-1-i]
                        let col = if grados == 90 { i } else { ancho - 1 - i };
                        let s = base + col * bpp;
                        let d = (k * alto + j) * bpp;
                        trozo[d..d + bpp].copy_from_slice(&src[s..s + bpp]);
                    }
                }
            });
    });
    out
}

/// Giro en serie (solo si no se pudo crear el pool de bandas).
fn rotar_serie(src: &[u8], ancho: usize, alto: usize, grados: i32, bpp: usize) -> Vec<u8> {
    let mut out = vec![0u8; src.len()];
    for i in 0..ancho {
        for j in 0..alto {
            let (f, c) = if grados == 90 { (alto - 1 - j, i) } else { (j, ancho - 1 - i) };
            let s = (f * ancho + c) * bpp;
            let d = (i * alto + j) * bpp;
            out[d..d + bpp].copy_from_slice(&src[s..s + bpp]);
        }
    }
    out
}

/// Tamaño máximo del buffer de un recorte (2 GiB).
const MAX_RECORTE_BYTES: usize = 2 << 30;

/// Recorte (`left, top, right, bottom`, como `Image.crop`) a píxeles compactos. Lo
/// que cae fuera de la imagen se rellena con `modo.relleno()` (el negro de Pillow).
fn recortar_px(
    src: &[u8],
    ancho: usize,
    alto: usize,
    caja: (i64, i64, i64, i64),
    modo: Modo,
) -> Result<(Vec<u8>, usize, usize), AtomError> {
    let (l, t, r, b) = caja;
    if l >= r || t >= b {
        return Err(AtomError::Entrada(format!("caja de recorte {caja:?} vacía o invertida")));
    }
    let bpp = modo.bpp();
    let desbordado = || AtomError::Entrada(format!("caja de recorte {caja:?} desbordada"));
    let cw = usize::try_from(r - l).map_err(|_| desbordado())?;
    let ch = usize::try_from(b - t).map_err(|_| desbordado())?;
    let n = cw
        .checked_mul(ch)
        .and_then(|v| v.checked_mul(bpp))
        .ok_or_else(desbordado)?;
    if n > MAX_RECORTE_BYTES {
        return Err(AtomError::Entrada(format!(
            "caja de recorte {caja:?} pide {n} bytes: supera el máximo de {MAX_RECORTE_BYTES} (2 GiB)"
        )));
    }
    let mut out = vec![modo.relleno(); n];
    // Columnas de la caja que caen dentro de la imagen: [x0, x1) en coords de origen.
    let x0 = l.max(0);
    let x1 = r.min(ancho as i64);
    if x0 < x1 {
        let (x0, x1) = (x0 as usize, x1 as usize);
        let dx = (x0 as i64 - l) as usize; // desplazamiento en la fila de salida
        pool_bandas()?.install(|| {
            out.par_chunks_mut(cw * bpp).enumerate().for_each(|(y, fila)| {
                let sy = t + y as i64;
                if sy < 0 || sy >= alto as i64 {
                    return;
                }
                let s = (sy as usize * ancho + x0) * bpp;
                let d = dx * bpp;
                fila[d..d + (x1 - x0) * bpp].copy_from_slice(&src[s..s + (x1 - x0) * bpp]);
            });
        });
    }
    Ok((out, cw, ch))
}

/// Decodifica el origen según su magic: JPEG (L/RGB/CMYK), PNG o TIFF.
fn decodificar_origen(datos: &[u8]) -> Result<Imagen, ErrRgb> {
    if datos.starts_with(&[0xFF, 0xD8]) {
        return Ok(decodificar_jpeg(datos)?);
    }
    if datos.starts_with(b"\x89PNG\r\n\x1a\n") {
        return decodificar_png(datos);
    }
    if [&b"II*\0"[..], b"MM\0*", b"II+\0", b"MM\0+"].iter().any(|m| datos.starts_with(m)) {
        return decodificar_tiff(datos);
    }
    Err(ErrRgb::Atom(AtomError::Entrada(
        "formato de origen no reconocido (se esperaba JPEG, PNG o TIFF)".into(),
    )))
}

/// PNG como Pillow: RGB 8/16 bits -> RGB (16 -> byte alto), gris -> L (2/4 bits
/// escalados a 0..255). RGBA/LA/P/gris 1 y 16 bits no se escriben como JPEG.
fn decodificar_png(datos: &[u8]) -> Result<Imagen, ErrRgb> {
    use png::{BitDepth, ColorType};
    let io = |e: png::DecodingError| ErrRgb::Atom(AtomError::Io(format!("PNG: {e}")));
    let mut dec = png::Decoder::new_with_limits(
        std::io::Cursor::new(datos),
        png::Limits { bytes: usize::MAX },
    );
    dec.set_transformations(png::Transformations::STRIP_16);
    let mut lector = dec.read_info().map_err(io)?;
    let (color, prof) = {
        let i = lector.info();
        (i.color_type, i.bit_depth)
    };
    let modo = match (color, prof) {
        (ColorType::Grayscale, BitDepth::Sixteen) => return Err(sin_jpeg("I;16")),
        (ColorType::Grayscale, BitDepth::One) => return Err(sin_jpeg("1")),
        (ColorType::Grayscale, _) => Modo::L,
        (ColorType::Rgb, _) => Modo::Rgb,
        (ColorType::Rgba, _) => return Err(sin_jpeg("RGBA")),
        (ColorType::GrayscaleAlpha, _) => return Err(sin_jpeg("LA")),
        (ColorType::Indexed, _) => return Err(sin_jpeg("P")),
    };
    let n = lector
        .output_buffer_size()
        .ok_or_else(|| ErrRgb::Atom(AtomError::Io("PNG: tamaño de imagen desbordado".into())))?;
    let mut buf = vec![0u8; n];
    let inf = lector.next_frame(&mut buf).map_err(io)?;
    let (ancho, alto) = (inf.width as usize, inf.height as usize);
    let bits = prof as usize;
    let px = if modo == Modo::L && bits < 8 {
        let max = (1u16 << bits) - 1;
        let esc = 255 / max;
        let mut px = Vec::with_capacity(ancho * alto);
        for f in 0..alto {
            let fila = &buf[f * inf.line_size..(f + 1) * inf.line_size];
            for x in 0..ancho {
                let pos = x * bits;
                let v = ((fila[pos / 8] >> (8 - bits - pos % 8)) as u16) & max;
                px.push((v * esc) as u8);
            }
        }
        px
    } else {
        let esperado = ancho * alto * modo.bpp();
        if buf.len() < esperado {
            return Err(ErrRgb::Atom(AtomError::Io("PNG: datos de píxel incompletos".into())));
        }
        buf.truncate(esperado);
        buf
    };
    Ok(Imagen { ancho, alto, modo, px })
}

/// TIFF como Pillow: RGB 8/16 -> RGB, gris 8 -> L, CMYK 8 -> CMYK (en el dominio del
/// JPEG, invertido: ver `Modo`). RGBA/LA/P/gris 1 y 16 no se escriben como JPEG; el resto
/// (YCbCr, Lab, compresiones que el crate no trae) es error visible.
fn decodificar_tiff(datos: &[u8]) -> Result<Imagen, ErrRgb> {
    use tiff::decoder::{Decoder, DecodingResult, Limits};
    use tiff::ColorType;
    let io = |e: tiff::TiffError| ErrRgb::Atom(AtomError::Io(format!("TIFF: {e}")));
    let no = |m: String| ErrRgb::Atom(AtomError::Entrada(format!("TIFF no soportado: {m}")));
    let mut dec = Decoder::new(std::io::Cursor::new(datos))
        .map_err(io)?
        .with_limits(Limits::unlimited());
    let (w, h) = dec.dimensions().map_err(io)?;
    let ct = dec.colortype().map_err(io)?;
    let modo = match ct {
        ColorType::Gray(8) => Modo::L,
        ColorType::Gray(1) => return Err(sin_jpeg("1")),
        ColorType::Gray(16) => return Err(sin_jpeg("I;16")),
        ColorType::RGB(8) | ColorType::RGB(16) => Modo::Rgb,
        ColorType::CMYK(8) => Modo::Cmyk,
        ColorType::RGBA(_) => return Err(sin_jpeg("RGBA")),
        ColorType::GrayA(_) => return Err(sin_jpeg("LA")),
        ColorType::Palette(_) => return Err(sin_jpeg("P")),
        otro => return Err(no(format!("{otro:?}"))),
    };
    let mut px: Vec<u8> = match dec.read_image().map_err(io)? {
        DecodingResult::U8(v) => v,
        DecodingResult::U16(v) => v.iter().map(|x| (x >> 8) as u8).collect(),
        _ => return Err(no("tipo de muestra".into())),
    };
    let (ancho, alto) = (w as usize, h as usize);
    if px.len() != ancho * alto * modo.bpp() {
        return Err(no(format!("{} bytes para {ancho}x{alto} {}", px.len(), modo.nombre())));
    }
    if modo == Modo::Cmyk {
        // Pillow guarda el CMYK del TIFF tal cual y `CMYK;I` lo invierte al escribir.
        for b in px.iter_mut() {
            *b = 255 - *b;
        }
    }
    Ok(Imagen { ancho, alto, modo, px })
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

type EstadoPool = Mutex<Option<(u32, usize, &'static rayon::ThreadPool)>>;

/// Pool de encode (`hilos()`) y pool de bandas (nproc). Cada uno guarda (pid, n).
static POOL_ENC: EstadoPool = Mutex::new(None);
static POOL_BANDAS: EstadoPool = Mutex::new(None);

/// Pool pid-aware de `n` hilos. Si no existe, cambió `n` o el pid difiere (hijo de
/// `fork`), crea uno nuevo y lo filtra con `Box::leak`: el viejo NUNCA se droppea
/// (en el hijo sus hilos no existen; soltarlo intentaría joinearlos).
fn pool_en(estado: &'static EstadoPool, n: usize) -> Result<&'static rayon::ThreadPool, AtomError> {
    let pid = std::process::id();
    let mut g = match estado.lock() {
        Ok(g) => g,
        Err(e) => e.into_inner(),
    };
    if let Some((p, m, pool)) = *g {
        if p == pid && m == n {
            return Ok(pool);
        }
    }
    let nuevo = rayon::ThreadPoolBuilder::new()
        .num_threads(n)
        .build()
        .map_err(|e| AtomError::Io(format!("pool de hilos: {e}")))?;
    let nuevo: &'static rayon::ThreadPool = Box::leak(Box::new(nuevo));
    *g = Some((pid, n, nuevo));
    Ok(nuevo)
}

fn pool(n: usize) -> Result<&'static rayon::ThreadPool, AtomError> {
    pool_en(&POOL_ENC, n)
}

/// Pool para rotar/recortar por bandas (nproc hilos).
pub(crate) fn pool_bandas() -> Result<&'static rayon::ThreadPool, AtomError> {
    let n = std::thread::available_parallelism().map_or(1, |n| n.get());
    pool_en(&POOL_BANDAS, n)
}

/// Codifica `px` y lo escribe en `ruta`; devuelve los ms de encode (sin escritura).
fn codificar_y_escribir(
    px: &[u8], w: usize, h: usize, modo: Modo, calidad: u8, exif: Option<&[u8]>, xmp: Option<&[u8]>, com: Option<&[u8]>, ruta: &str,
) -> Result<f64, AtomError> {
    let t0 = Instant::now();
    let jpeg = codificar(px, w, h, modo, calidad, exif, xmp, com)?;
    let ms = t0.elapsed().as_secs_f64() * 1000.0;
    escribir(ruta, &jpeg)?;
    Ok(ms)
}

/// Núcleo sin Python (testeable con `cargo test`).
pub fn procesar(p: &Parametros<'_>) -> Result<RgbStats, ErrRgb> {
    match p.modo {
        "a" => {}
        "b" => {
            return Err(AtomError::Entrada(
                "modo b (transformación DCT sin pérdida) aplazado: no implementado".into(),
            )
            .into())
        }
        otro => return Err(AtomError::Entrada(format!("modo debe ser 'a' o 'b': {otro:?}")).into()),
    }
    if !matches!(p.angulo, 0 | 90 | 270) {
        return Err(AtomError::Entrada(format!("angulo debe ser 0, 90 o 270: {}", p.angulo)).into());
    }
    validar_calidad(p.calidad_original, "original")?;
    if p.salida_crop.is_some() {
        validar_calidad(p.calidad_crop, "crop")?;
    }
    if p.caja.is_some() && p.salida_crop.is_none() {
        return Err(AtomError::Entrada("caja de recorte sin salida_crop".into()).into());
    }
    if let Some(e) = p.exif_bytes {
        validar_exif(e)?;
    }
    if let Some(x) = p.xmp.filter(|x| !x.is_empty()) {
        if x.len() + crate::jpeg::NS_XMP_APP1.len() > crate::jpeg::MAX_BYTES_APP1 {
            return Err(AtomError::Entrada("XMP data is too long".into()).into());
        }
    }

    let t0 = Instant::now();
    // Sin retroceso a Pillow: un decode fatal o un modo no escribible es error de fila.
    let Imagen { ancho: w, alto: h, modo, px } = decodificar_origen(p.datos_origen)?;
    let ms_decode = t0.elapsed().as_secs_f64() * 1000.0;

    let n = hilos();
    let r = if n <= 1 {
        transformar_y_escribir(p, w, h, modo, &px, ms_decode, false)
    } else {
        match pool(n) {
            Ok(pool) => pool.install(|| transformar_y_escribir(p, w, h, modo, &px, ms_decode, true)),
            Err(e) => Err(e),
        }
    };
    devolver_buffer_rgb(px);
    r.map_err(ErrRgb::Atom)
}

/// Crop + giro + encode x2 + escritura. Con `paralelo` se ejecuta DENTRO del pool.
fn transformar_y_escribir(
    p: &Parametros<'_>,
    w: usize,
    h: usize,
    modo: Modo,
    px: &[u8],
    ms_decode: f64,
    paralelo: bool,
) -> Result<RgbStats, AtomError> {
    // _CROP: recorte en coordenadas del origen SIN girar y luego giro.
    let crop: Option<(Vec<u8>, usize, usize)> = match (p.salida_crop, p.caja) {
        (None, _) => None,
        (Some(_), Some(caja)) => {
            let (c, cw, ch) = recortar_px(px, w, h, caja, modo)?;
            let c = rotar_px(&c, cw, ch, p.angulo, modo.bpp());
            let (cw, ch) = if p.angulo == 0 { (cw, ch) } else { (ch, cw) };
            Some((c, cw, ch))
        }
        // Sin caja (`pct_recorte` None): copia íntegra, con el giro si toca.
        (Some(_), None) => {
            let c = rotar_px(px, w, h, p.angulo, modo.bpp());
            let (cw, ch) = if p.angulo == 0 { (w, h) } else { (h, w) };
            Some((c, cw, ch))
        }
    };
    let girada;
    let (orig, wo, ho): (&[u8], usize, usize) = if p.angulo == 0 {
        (px, w, h)
    } else {
        girada = rotar_px(px, w, h, p.angulo, modo.bpp());
        (&girada, h, w)
    };

    let exif = p.exif_bytes;
    let com = p.comentario;
    let xmp = p.xmp;
    let enc_crop = || match (&crop, p.salida_crop) {
        (Some((c, cw, ch)), Some(ruta)) => {
            codificar_y_escribir(c, *cw, *ch, modo, p.calidad_crop, exif, xmp, com, ruta).map(Some)
        }
        _ => Ok(None),
    };
    let enc_orig =
        || codificar_y_escribir(orig, wo, ho, modo, p.calidad_original, exif, xmp, com, p.salida_original);
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
    caja: Option<(i64, i64, i64, i64)>,
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
    match py.allow_threads(|| procesar(&p)) {
        Ok(st) => Ok(st),
        Err(ErrRgb::Atom(e)) => Err(e.into()),
        // Mismo texto que el OSError de Pillow; `traducir_error` lo da como OSError(msg).
        Err(ErrRgb::Modo(m)) => Err(DatosErrorFila::nuevo("io", m).a_pyerr(py)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jpeg::{codificar_rgb, decodificar_rgb};
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
        let (c, cw, ch) = recortar_px(&src, 10, 8, (2, 1, 6, 5), Modo::Rgb).unwrap();
        assert_eq!((cw, ch), (4, 4));
        assert_eq!(&c[..3], &src[(10 + 2) * 3..(10 + 2) * 3 + 3]);
        assert!(recortar_px(&src, 10, 8, (6, 1, 6, 5), Modo::Rgb).is_err());
    }

    #[test]
    fn recortar_fuera_rellena_como_pillow() {
        let src = imagen(10, 8);
        // Sale por los cuatro lados: (-2,-1)..(12,9) = 14x10.
        let (c, cw, ch) = recortar_px(&src, 10, 8, (-2, -1, 12, 9), Modo::Rgb).unwrap();
        assert_eq!((cw, ch), (14, 10));
        assert!(c[..14 * 3].iter().all(|&b| b == 0), "fila superior fuera");
        assert!(c[..0].is_empty());
        let fila1 = &c[(14 + 0) * 3..(14 + 14) * 3];
        assert!(fila1[..6].iter().all(|&b| b == 0), "columnas izquierda fuera");
        assert_eq!(&fila1[6..6 + 30], &src[..30]);
        assert!(fila1[36..].iter().all(|&b| b == 0), "columnas derecha fuera");
        // Totalmente fuera: todo relleno. CMYK rellena 255 (dominio JPEG).
        let (c, _, _) = recortar_px(&src, 10, 8, (20, 20, 22, 22), Modo::Rgb).unwrap();
        assert!(c.iter().all(|&b| b == 0));
        let cm = vec![7u8; 10 * 8 * 4];
        let (c, _, _) = recortar_px(&cm, 10, 8, (-1, 0, 1, 1), Modo::Cmyk).unwrap();
        assert_eq!(&c[..4], &[255; 4]);
        assert_eq!(&c[4..8], &[7; 4]);
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
        let origen = codificar_rgb(&imagen(w, h), w, h, 95, None, None, None).unwrap();
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
        let origen = codificar_rgb(&imagen(16, 16), 16, 16, 90, None, None, None).unwrap();
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
    fn png_rgba_y_p_no_se_escriben_como_jpeg() {
        fn png(color: png::ColorType, n: usize) -> Vec<u8> {
            let mut v = Vec::new();
            let mut e = png::Encoder::new(&mut v, 4, 4);
            e.set_color(color);
            e.set_depth(png::BitDepth::Eight);
            if color == png::ColorType::Indexed {
                e.set_palette(vec![0u8; 3]);
            }
            let mut w = e.write_header().unwrap();
            w.write_image_data(&vec![0u8; 16 * n]).unwrap();
            w.finish().unwrap();
            v
        }
        match decodificar_origen(&png(png::ColorType::Rgba, 4)) {
            Err(ErrRgb::Modo(m)) => assert_eq!(m, "cannot write mode RGBA as JPEG"),
            o => panic!("{o:?}"),
        }
        match decodificar_origen(&png(png::ColorType::Indexed, 1)) {
            Err(ErrRgb::Modo(m)) => assert_eq!(m, "cannot write mode P as JPEG"),
            o => panic!("{o:?}"),
        }
        let img = decodificar_origen(&png(png::ColorType::Rgb, 3)).unwrap();
        assert_eq!((img.ancho, img.alto, img.modo, img.px.len()), (4, 4, Modo::Rgb, 48));
    }

    #[test]
    fn procesa_l_con_caja_fuera() {
        let (w, h) = (40usize, 30usize);
        let px: Vec<u8> = (0..w * h).map(|i| ((i * 5) % 251) as u8).collect();
        let origen = crate::jpeg::codificar(&px, w, h, Modo::L, 95, None, None, None).unwrap();
        let dir = dir_tmp("l");
        let (o, c) = (dir.join("o.parcial.jpg"), dir.join("c.parcial.jpg"));
        let mut p = params(&origen, o.to_str().unwrap(), Some(c.to_str().unwrap()), 90);
        p.caja = Some((-5, -3, 45, 33));
        let st = procesar(&p).unwrap();
        assert_eq!((st.ancho_crop, st.alto_crop), (36, 50)); // 50x36 girada
        let img = crate::jpeg::decodificar_jpeg(&std::fs::read(&c).unwrap()).unwrap();
        assert_eq!((img.modo, img.ancho, img.alto), (Modo::L, 36, 50));
        std::fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn jpeg_roto_falla_sin_dejar_ficheros() {
        let dir = dir_tmp("roto");
        let o = dir.join("o.jpg");
        let r = procesar(&params(&[0xFF, 0xD8, 1, 2, 3], o.to_str().unwrap(), None, 0));
        assert!(matches!(r, Err(ErrRgb::Atom(_))), "decode fatal = error de fila");
        assert!(!o.exists());
        std::fs::remove_dir_all(dir).unwrap();
    }
}
