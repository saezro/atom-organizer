//! JPEG RGB sobre libjpeg-turbo con los flags de Pillow (T3): decode ISLOW +
//! fancy upsampling, encode baseline 4:2:0 sin optimize ni progresivo, JFIF y
//! un APP1 EXIF opcional. La paridad con Pillow por plataforma la fija la
//! sonda P (`jpeg_sonda`); aquí solo se reutilizan sus piezas.

use std::cell::RefCell;

use crate::error::AtomError;
use crate::termica::codificar_jpeg;

/// `MAX_BYTES_IN_MARKER` de Pillow (`JpegImagePlugin`): payload máximo de un marcador.
pub const MAX_BYTES_APP1: usize = 65533;

/// Mismo error que Pillow (`ValueError("EXIF data is too long")`) antes de escribir nada.
pub fn validar_exif(exif: &[u8]) -> Result<(), AtomError> {
    if exif.len() > MAX_BYTES_APP1 {
        return Err(AtomError::Entrada("EXIF data is too long".into()));
    }
    Ok(())
}

/// Posición justo tras SOI y el APP0/JFIF (si existe): ahí Pillow escribe EXIF y COM.
fn pos_tras_app0(jpeg: &[u8]) -> Result<usize, AtomError> {
    if jpeg.len() < 4 || jpeg[0] != 0xFF || jpeg[1] != 0xD8 {
        return Err(AtomError::Entrada("salida sin SOI".into()));
    }
    let mut pos = 2;
    if jpeg.len() >= 6 && jpeg[2] == 0xFF && jpeg[3] == 0xE0 {
        let l = u16::from_be_bytes([jpeg[4], jpeg[5]]) as usize;
        pos = 4 + l;
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
    if let Some(e) = exif {
        validar_exif(e)?;
    }
    let j = codificar_jpeg(rgb, ancho, alto, false, calidad.max(1))?;
    if exif.is_none()
        && xmp.map_or(true, |x| x.is_empty())
        && comentario.map_or(true, |c| c.is_empty())
    {
        return Ok(j);
    }
    insertar_segmentos(&j, exif, xmp, comentario)
}

thread_local! {
    /// Decompresor reutilizado por hilo (evita `tj3Init`/destroy por imagen).
    static DECOMPRESOR: RefCell<Option<turbojpeg::Decompressor>> = const { RefCell::new(None) };
    /// Buffer de píxeles persistente por hilo: solo crece (el resize pone a cero
    /// únicamente la parte nueva); un decode OK sobrescribe todos los bytes.
    static BUFFER_RGB: RefCell<Vec<u8>> = const { RefCell::new(Vec::new()) };
}

/// Devuelve al hilo actual el buffer de píxeles de un `decodificar_rgb` ya consumido,
/// para que la siguiente imagen no reserve ni ponga a cero ~144 MB. Opcional.
pub fn devolver_buffer_rgb(buf: Vec<u8>) {
    BUFFER_RGB.with(|b| {
        let mut b = b.borrow_mut();
        if buf.capacity() > b.capacity() {
            *b = buf;
        }
    });
}

/// Como `Image.open().convert("RGB")` de un JPEG RGB: `(ancho, alto, píxeles RGB compactos)`.
/// Reutiliza decompresor y buffer del hilo; el `Vec` devuelto sale del buffer
/// persistente (devolverlo con `devolver_buffer_rgb` para reciclarlo).
pub fn decodificar_rgb(jpeg: &[u8]) -> Result<(usize, usize, Vec<u8>), AtomError> {
    let err = |e: turbojpeg::Error| AtomError::Io(format!("libjpeg-turbo: {e}"));
    let mut buf = BUFFER_RGB.with(|b| std::mem::take(&mut *b.borrow_mut()));
    let res: Result<(usize, usize), AtomError> = DECOMPRESOR.with(|d| {
        let mut d = d.borrow_mut();
        if d.is_none() {
            *d = Some(turbojpeg::Decompressor::new().map_err(err)?);
        }
        let dec = d.as_mut().unwrap();
        let cab = dec.read_header(jpeg).map_err(err)?;
        let pitch = cab.width * 3;
        let n = cab.height * pitch;
        if buf.len() < n {
            buf.resize(n, 0);
        } else {
            buf.truncate(n);
        }
        let img = turbojpeg::Image {
            pixels: &mut buf[..],
            width: cab.width,
            pitch,
            height: cab.height,
            format: turbojpeg::PixelFormat::RGB,
        };
        dec.decompress(jpeg, img).map_err(err)?;
        Ok((cab.width, cab.height))
    });
    match res {
        Ok((w, h)) => Ok((w, h, buf)),
        Err(e) => {
            devolver_buffer_rgb(buf);
            Err(e)
        }
    }
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
}
