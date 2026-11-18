//! JPEG sobre libjpeg-turbo con los flags de Pillow (T3): decode ISLOW +
//! fancy upsampling, encode baseline sin optimize ni progresivo (RGB 4:2:0,
//! L gris, CMYK 4:4:4 con Adobe APP14), JFIF y un APP1 EXIF opcional. La
//! paridad con Pillow por plataforma la fija la sonda P (`jpeg_sonda`); aquí
//! solo se reutilizan sus piezas.
//!
//! Decode tolerante como Pillow: los avisos de libjpeg (datos extra, scan
//! corrupto, marcador inválido, fin prematuro con EOI) NO abortan; la imagen se
//! da por buena con los píxeles que libjpeg deja (Pillow ignora esos avisos).
//! Solo un error fatal es error. Por eso se usa la API cruda `tj3*` (el crate
//! `turbojpeg` convierte todo aviso en `Err`, aunque los píxeles estén enteros).

use std::cell::RefCell;

use turbojpeg_sys as raw;

use crate::error::AtomError;
use crate::termica::codificar_jpeg;

/// Modo de píxel de la imagen en memoria (= modo Pillow que se escribe como JPEG).
/// CMYK está en el dominio del JPEG (valores tal cual los guarda libjpeg; en un
/// JPEG Adobe son los invertidos): Pillow decodifica y codifica con `CMYK;I`, así
/// que decode->encode los deja idénticos. Fuera del recorte se rellena con
/// `relleno()`: el 0 de Pillow en CMYK normal es 255 en ese dominio.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Modo {
    L,
    Rgb,
    Cmyk,
}

impl Modo {
    /// Bytes por píxel.
    pub fn bpp(self) -> usize {
        match self {
            Modo::L => 1,
            Modo::Rgb => 3,
            Modo::Cmyk => 4,
        }
    }

    /// Byte de relleno de `Image.crop` fuera de la imagen (negro/0 de Pillow en el
    /// dominio de píxeles en memoria).
    pub fn relleno(self) -> u8 {
        match self {
            Modo::Cmyk => 255,
            _ => 0,
        }
    }

    /// Nombre de modo Pillow (para `verificar_salida_rust` y mensajes).
    pub fn nombre(self) -> &'static str {
        match self {
            Modo::L => "L",
            Modo::Rgb => "RGB",
            Modo::Cmyk => "CMYK",
        }
    }
}

/// Imagen decodificada: `px` compactos (`ancho * alto * modo.bpp()`).
#[derive(Debug)]
pub struct Imagen {
    pub ancho: usize,
    pub alto: usize,
    pub modo: Modo,
    pub px: Vec<u8>,
}

/// `MAX_BYTES_IN_MARKER` de Pillow (`JpegImagePlugin`): payload máximo de un marcador.
pub const MAX_BYTES_APP1: usize = 65533;

/// Mismo error que Pillow (`ValueError("EXIF data is too long")`) antes de escribir nada.
pub fn validar_exif(exif: &[u8]) -> Result<(), AtomError> {
    if exif.len() > MAX_BYTES_APP1 {
        return Err(AtomError::Entrada("EXIF data is too long".into()));
    }
    Ok(())
}

/// Posición justo tras SOI y el APP0/JFIF (RGB, L) o el APP14/Adobe (CMYK), si
/// existen: ahí Pillow escribe EXIF y COM (tras `jpeg_start_compress`).
fn pos_tras_app0(jpeg: &[u8]) -> Result<usize, AtomError> {
    if jpeg.len() < 4 || jpeg[0] != 0xFF || jpeg[1] != 0xD8 {
        return Err(AtomError::Entrada("salida sin SOI".into()));
    }
    let mut pos = 2;
    for marca in [0xE0u8, 0xEEu8] {
        if jpeg.len() >= pos + 4 && jpeg[pos] == 0xFF && jpeg[pos + 1] == marca {
            let l = u16::from_be_bytes([jpeg[pos + 2], jpeg[pos + 3]]) as usize;
            pos += 2 + l;
        }
    }
    if pos > jpeg.len() {
        return Err(AtomError::Entrada("APP0 de la salida truncado".into()));
    }
    Ok(pos)
}

/// Cabecera del APP1 XMP (idéntica a `_NS_XMP_APP1` de `apply.py`).
pub const NS_XMP_APP1: &[u8] = b"http://ns.adobe.com/xap/1.0/\0";

/// Inserta, tras SOI/APP0 y en el orden de Pillow 12.3.0, el APP1 EXIF (`exif`
/// ya trae `Exif\0\0`, como lo pasa Pillow), después el APP1 XMP (`xmp` = payload
/// sin namespace; misma colocación que `insertar_xmp_app1` de Python) y luego el segmento COM
/// (`comment` del origen: Pillow lo copia en `crop`/`transpose` y lo escribe
/// tras el EXIF; un comentario vacío no genera marcador).
pub fn insertar_segmentos(
    jpeg: &[u8],
    exif: Option<&[u8]>,
    xmp: Option<&[u8]>,
    comentario: Option<&[u8]>,
) -> Result<Vec<u8>, AtomError> {
    if let Some(e) = exif {
        validar_exif(e)?;
    }
    let xmp = xmp.filter(|x| !x.is_empty());
    if let Some(x) = xmp {
        if x.len() + NS_XMP_APP1.len() > MAX_BYTES_APP1 {
            return Err(AtomError::Entrada("XMP data is too long".into()));
        }
    }
    let comentario = comentario.filter(|c| !c.is_empty());
    if let Some(c) = comentario {
        if c.len() > MAX_BYTES_APP1 {
            return Err(AtomError::Entrada("comment is too long".into()));
        }
    }
    let pos = pos_tras_app0(jpeg)?;
    let mut out = Vec::with_capacity(
        jpeg.len() + exif.map_or(0, |e| e.len() + 4)
            + xmp.map_or(0, |x| x.len() + NS_XMP_APP1.len() + 4)
            + comentario.map_or(0, |c| c.len() + 4),
    );
    out.extend_from_slice(&jpeg[..pos]);
    for (marca, ns, datos) in [
        (0xE1u8, &[][..], exif),
        (0xE1u8, NS_XMP_APP1, xmp),
        (0xFEu8, &[][..], comentario),
    ] {
        if let Some(d) = datos {
            out.extend_from_slice(&[0xFF, marca]);
            out.extend_from_slice(&((ns.len() + d.len() + 2) as u16).to_be_bytes());
            out.extend_from_slice(ns);
            out.extend_from_slice(d);
        }
    }
    out.extend_from_slice(&jpeg[pos..]);
    Ok(out)
}

/// Solo el APP1 EXIF (sonda de paridad).
pub fn insertar_app1(jpeg: &[u8], exif: &[u8]) -> Result<Vec<u8>, AtomError> {
    insertar_segmentos(jpeg, Some(exif), None, None)
}

/// Como `Image.save(quality=q, exif=exif, comment=c)` de Pillow para RGB.
/// `calidad` 0 se trata como 1 (libjpeg recorta `jpeg_set_quality(0)` a 1).
pub fn codificar_rgb(
    rgb: &[u8],
    ancho: usize,
    alto: usize,
    calidad: u8,
    exif: Option<&[u8]>,
    xmp: Option<&[u8]>,
    comentario: Option<&[u8]>,
) -> Result<Vec<u8>, AtomError> {
    codificar(rgb, ancho, alto, Modo::Rgb, calidad, exif, xmp, comentario)
}

/// JPEG CMYK como Pillow (`CMYK;I`): `jpeg_set_defaults` con entrada CMYK deja el
/// espacio JCS_CMYK, 1x1 en los 4 componentes, sin JFIF y con Adobe APP14
/// (transform 0). `px` ya está en el dominio del JPEG (ver `Modo`).
fn codificar_cmyk(px: &[u8], ancho: usize, alto: usize, calidad: u8) -> Result<Vec<u8>, AtomError> {
    let err = |e: turbojpeg::Error| AtomError::Io(format!("libjpeg-turbo: {e}"));
    let mut c = turbojpeg::Compressor::new().map_err(err)?;
    c.set_quality(calidad as i32).map_err(err)?;
    c.set_subsamp(turbojpeg::Subsamp::None).map_err(err)?;
    c.set_colorspace(turbojpeg::Colorspace::CMYK).map_err(err)?;
    let img = turbojpeg::Image {
        pixels: px,
        width: ancho,
        pitch: ancho * 4,
        height: alto,
        format: turbojpeg::PixelFormat::CMYK,
    };
    c.compress_to_vec(img).map_err(err)
}

/// Como `Image.save(quality=q, exif=exif, comment=c)` de Pillow según el modo
/// (RGB 4:2:0, L gris, CMYK). `calidad` 0 se trata como 1.
pub fn codificar(
    px: &[u8],
    ancho: usize,
    alto: usize,
    modo: Modo,
    calidad: u8,
    exif: Option<&[u8]>,
    xmp: Option<&[u8]>,
    comentario: Option<&[u8]>,
) -> Result<Vec<u8>, AtomError> {
    if let Some(e) = exif {
        validar_exif(e)?;
    }
    if px.len() != ancho * alto * modo.bpp() {
        return Err(AtomError::Entrada(format!(
            "píxeles de {} bytes no son {ancho}x{alto} {}",
            px.len(),
            modo.nombre()
        )));
    }
    let q = calidad.max(1);
    let j = match modo {
        Modo::Rgb => codificar_jpeg(px, ancho, alto, false, q)?,
        Modo::L => codificar_jpeg(px, ancho, alto, true, q)?,
        Modo::Cmyk => codificar_cmyk(px, ancho, alto, q)?,
    };
    if exif.is_none()
        && xmp.map_or(true, |x| x.is_empty())
        && comentario.map_or(true, |c| c.is_empty())
    {
        return Ok(j);
    }
    insertar_segmentos(&j, exif, xmp, comentario)
}

/// Instancia `tj3` de decompresión (se destruye al terminar el hilo).
struct Manejador(raw::tjhandle);

impl Drop for Manejador {
    fn drop(&mut self) {
        unsafe { raw::tj3Destroy(self.0) }
    }
}

thread_local! {
    /// Decompresor reutilizado por hilo (evita `tj3Init`/destroy por imagen). Se
    /// descarta tras cualquier error (el estado de libjpeg puede quedar a medias).
    static DECOMPRESOR: RefCell<Option<Manejador>> = const { RefCell::new(None) };
    /// Buffer de píxeles persistente por hilo: solo crece (el resize pone a cero
    /// únicamente la parte nueva); un decode OK sobrescribe todos los bytes.
    static BUFFER_RGB: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
}

/// Devuelve al hilo actual el buffer de píxeles de un decode ya consumido,
/// para que la siguiente imagen no reserve ni ponga a cero ~144 MB. Opcional.
pub fn devolver_buffer_rgb(buf: Vec<u8>) {
    BUFFER_RGB.with(|b| {
        let mut b = b.borrow_mut();
        if buf.capacity() > b.capacity() {
            *b = buf;
        }
    });
}

fn mensaje_tj(h: raw::tjhandle) -> String {
    let p = unsafe { raw::tj3GetErrorStr(h) };
    if p.is_null() {
        return "error desconocido".into();
    }
    unsafe { std::ffi::CStr::from_ptr(p) }.to_string_lossy().into_owned()
}

fn err_tj(h: raw::tjhandle) -> AtomError {
    AtomError::Io(format!("libjpeg-turbo: TurboJPEG error: {}", mensaje_tj(h)))
}

/// `true` si el `-1` de la última llamada fue un aviso de libjpeg (los píxeles están
/// completos), no un error fatal.
fn es_aviso(h: raw::tjhandle) -> bool {
    unsafe { raw::tj3GetErrorCode(h) == raw::TJERR_TJERR_WARNING as i32 }
}

/// Decodifica un JPEG al modo que Pillow le daría (1 componente = L; 3 = RGB;
/// 4 = CMYK). Tolerante a avisos de libjpeg como Pillow (ver doc del módulo).
/// Reutiliza decompresor y buffer del hilo; el `Vec` devuelto sale del buffer
/// persistente (devolverlo con `devolver_buffer_rgb` para reciclarlo).
pub fn decodificar_jpeg(jpeg: &[u8]) -> Result<Imagen, AtomError> {
    if jpeg.is_empty() {
        return Err(AtomError::Io("libjpeg-turbo: JPEG vacío".into()));
    }
    let mut buf = BUFFER_RGB.with(|b| std::mem::take(&mut *b.borrow_mut()));
    let res: Result<(usize, usize, Modo), AtomError> = DECOMPRESOR.with(|d| {
        let mut d = d.borrow_mut();
        let r = decodificar_en(&mut d, jpeg, &mut buf);
        if r.is_err() {
            *d = None;
        }
        r
    });
    match res {
        Ok((ancho, alto, modo)) => Ok(Imagen { ancho, alto, modo, px: buf }),
        Err(e) => {
            devolver_buffer_rgb(buf);
            Err(e)
        }
    }
}

fn decodificar_en(
    d: &mut Option<Manejador>,
    jpeg: &[u8],
    buf: &mut Vec<u8>,
) -> Result<(usize, usize, Modo), AtomError> {
    if d.is_none() {
        let h = unsafe { raw::tj3Init(raw::TJINIT_TJINIT_DECOMPRESS as i32) };
        if h.is_null() {
            return Err(AtomError::Io("libjpeg-turbo: tj3Init falló".into()));
        }
        *d = Some(Manejador(h));
    }
    let h = d.as_ref().unwrap().0;
    if jpeg.len() > u32::MAX as usize {
        // `size_t` de la FFI puede ser u32 (Windows c_ulong): no truncar en silencio.
        return Err(AtomError::Entrada(format!(
            "JPEG de {} bytes: supera el máximo que acepta libjpeg-turbo ({} bytes)",
            jpeg.len(),
            u32::MAX
        )));
    }
    let n_jpeg = jpeg.len() as raw::size_t;
    // `tj3*` devuelve -1 también ante avisos (STOPONWARNING=0: se completan); un
    // aviso no es error. La cabecera con aviso deja igualmente sus parámetros.
    if unsafe { raw::tj3DecompressHeader(h, jpeg.as_ptr(), n_jpeg) } != 0 && !es_aviso(h) {
        return Err(err_tj(h));
    }
    let ancho = unsafe { raw::tj3Get(h, raw::TJPARAM_TJPARAM_JPEGWIDTH as i32) };
    let alto = unsafe { raw::tj3Get(h, raw::TJPARAM_TJPARAM_JPEGHEIGHT as i32) };
    let cs = unsafe { raw::tj3Get(h, raw::TJPARAM_TJPARAM_COLORSPACE as i32) };
    if ancho < 1 || alto < 1 {
        return Err(AtomError::Io(format!(
            "libjpeg-turbo: dimensiones inválidas tras la cabecera: {ancho}x{alto}"
        )));
    }
    let (ancho, alto) = (ancho as usize, alto as usize);
    let (modo, pf) = match cs as u32 {
        raw::TJCS_TJCS_GRAY => (Modo::L, raw::TJPF_TJPF_GRAY),
        raw::TJCS_TJCS_RGB | raw::TJCS_TJCS_YCbCr => (Modo::Rgb, raw::TJPF_TJPF_RGB),
        raw::TJCS_TJCS_CMYK | raw::TJCS_TJCS_YCCK => (Modo::Cmyk, raw::TJPF_TJPF_CMYK),
        otro => {
            return Err(AtomError::Io(format!("libjpeg-turbo: espacio de color desconocido {otro}")))
        }
    };
    let pitch = ancho
        .checked_mul(modo.bpp())
        .filter(|p| *p <= i32::MAX as usize)
        .ok_or_else(|| AtomError::Io(format!("libjpeg-turbo: ancho desbordado: {ancho}")))?;
    let n = alto
        .checked_mul(pitch)
        .ok_or_else(|| AtomError::Io(format!("libjpeg-turbo: tamaño desbordado: {ancho}x{alto}")))?;
    if buf.len() < n {
        buf.resize(n, 0);
    } else {
        buf.truncate(n);
    }
    let r = unsafe {
        raw::tj3Decompress8(h, jpeg.as_ptr(), n_jpeg, buf.as_mut_ptr(), pitch as i32, pf as i32)
    };
    if r != 0 && !es_aviso(h) {
        return Err(err_tj(h));
    }
    Ok((ancho, alto, modo))
}

/// Como `Image.open().convert("RGB")` de un JPEG RGB: `(ancho, alto, píxeles RGB compactos)`.
/// Error si el JPEG no es de 3 componentes.
pub fn decodificar_rgb(jpeg: &[u8]) -> Result<(usize, usize, Vec<u8>), AtomError> {
    let img = decodificar_jpeg(jpeg)?;
    if img.modo != Modo::Rgb {
        let m = img.modo;
        devolver_buffer_rgb(img.px);
        return Err(AtomError::Entrada(format!("JPEG de modo {}, se esperaba RGB", m.nombre())));
    }
    Ok((img.ancho, img.alto, img.px))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app1_va_tras_el_app0() {
        let j = codificar_jpeg(&[10u8; 16 * 16 * 3], 16, 16, false, 75).unwrap();
        let o = insertar_app1(&j, b"Exif\0\0xx").unwrap();
        assert_eq!(&o[2..4], &[0xFF, 0xE0]);
        let l0 = u16::from_be_bytes([o[4], o[5]]) as usize;
        assert_eq!(&o[4 + l0..6 + l0], &[0xFF, 0xE1]);
        assert_eq!(o.len(), j.len() + 4 + 8);
    }

    #[test]
    fn exif_demasiado_largo_como_pillow() {
        let e = vec![0u8; MAX_BYTES_APP1 + 1];
        let err = validar_exif(&e).unwrap_err();
        assert!(err.to_string().contains("EXIF data is too long"));
        assert!(validar_exif(&e[..MAX_BYTES_APP1]).is_ok());
    }

    #[test]
    fn roundtrip_dimensiones_no_multiplo_de_mcu() {
        let (w, h) = (37usize, 21usize);
        let px: Vec<u8> = (0..w * h * 3).map(|i| (i % 251) as u8).collect();
        let j = codificar_rgb(&px, w, h, 90, Some(b"Exif\0\0ab"), None, None).unwrap();
        let (dw, dh, d) = decodificar_rgb(&j).unwrap();
        assert_eq!((dw, dh, d.len()), (w, h, w * h * 3));
    }

    #[test]
    fn com_va_tras_el_exif_y_vacio_no_escribe() {
        let j = codificar_jpeg(&[10u8; 16 * 16 * 3], 16, 16, false, 75).unwrap();
        let o = insertar_segmentos(&j, Some(b"Exif\0\0xx"), None, Some(b"hola")).unwrap();
        let l0 = u16::from_be_bytes([o[4], o[5]]) as usize;
        assert_eq!(&o[4 + l0..6 + l0], &[0xFF, 0xE1]);
        let p = 4 + l0 + 2 + 2 + 8;
        assert_eq!(&o[p..p + 2], &[0xFF, 0xFE]);
        assert_eq!(&o[p + 4..p + 8], b"hola");
        let v = insertar_segmentos(&j, None, None, Some(b"")).unwrap();
        assert_eq!(v, j);
    }

    #[test]
    fn calidad_cero_como_uno() {
        let px = vec![77u8; 16 * 16 * 3];
        assert_eq!(
            codificar_rgb(&px, 16, 16, 0, None, None, None).unwrap(),
            codificar_rgb(&px, 16, 16, 1, None, None, None).unwrap()
        );
    }

    #[test]
    fn decode_de_basura_falla() {
        assert!(decodificar_rgb(&[1, 2, 3, 4]).is_err());
    }

    fn ruido(n: usize) -> Vec<u8> {
        (0..n).map(|i| ((i * 7 + i / 5) % 253) as u8).collect()
    }

    #[test]
    fn modo_l_roundtrip_y_jfif() {
        let (w, h) = (37usize, 21usize);
        let px = ruido(w * h);
        let j = codificar(&px, w, h, Modo::L, 90, Some(b"Exif\0\0ab"), None, None).unwrap();
        assert_eq!(&j[2..4], &[0xFF, 0xE0]); // JFIF como Pillow
        let img = decodificar_jpeg(&j).unwrap();
        assert_eq!((img.ancho, img.alto, img.modo, img.px.len()), (w, h, Modo::L, w * h));
        assert!(decodificar_rgb(&j).is_err());
    }

    #[test]
    fn modo_cmyk_roundtrip_adobe_y_exif_tras_app14() {
        let (w, h) = (37usize, 21usize);
        let px = ruido(w * h * 4);
        let j = codificar(&px, w, h, Modo::Cmyk, 90, Some(b"Exif\0\0ab"), None, None).unwrap();
        assert_eq!(&j[2..4], &[0xFF, 0xEE], "Adobe APP14 primero, sin JFIF");
        let l14 = u16::from_be_bytes([j[4], j[5]]) as usize;
        assert_eq!(&j[4 + l14..6 + l14], &[0xFF, 0xE1], "EXIF tras el APP14");
        let img = decodificar_jpeg(&j).unwrap();
        assert_eq!((img.ancho, img.alto, img.modo, img.px.len()), (w, h, Modo::Cmyk, w * h * 4));
        // Todos los componentes 1x1 (sin submuestreo): sin pérdida grande en un plano liso.
        let liso = vec![200u8; 16 * 16 * 4];
        let j = codificar(&liso, 16, 16, Modo::Cmyk, 95, None, None, None).unwrap();
        let img = decodificar_jpeg(&j).unwrap();
        assert!(img.px.iter().all(|&b| (b as i32 - 200).abs() <= 2));
    }

    #[test]
    fn decode_tolerante_a_avisos_como_pillow() {
        let (w, h) = (64usize, 48usize);
        let base = codificar_rgb(&ruido(w * h * 3), w, h, 90, None, None, None).unwrap();
        // bytes extra antes del primer DHT (aviso "extraneous bytes before marker").
        let i = base.windows(2).position(|p| p == [0xFF, 0xC4]).unwrap();
        let mut extra = base[..i].to_vec();
        extra.extend_from_slice(&[0, 1, 2]);
        extra.extend_from_slice(&base[i..]);
        let img = decodificar_jpeg(&extra).unwrap();
        assert_eq!((img.ancho, img.alto), (w, h));
        // truncado con EOI (aviso "insufficient data"): imagen completa, resto gris.
        let mut cortado = base[..base.len() * 6 / 10].to_vec();
        cortado.extend_from_slice(&[0xFF, 0xD9]);
        let img = decodificar_jpeg(&cortado).unwrap();
        assert_eq!((img.ancho, img.alto, img.px.len()), (w, h, w * h * 3));
    }
}
