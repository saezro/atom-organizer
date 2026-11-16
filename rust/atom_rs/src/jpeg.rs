//! JPEG RGB sobre libjpeg-turbo con los flags de Pillow (T3): decode ISLOW +
//! fancy upsampling, encode baseline 4:2:0 sin optimize ni progresivo, JFIF y
//! un APP1 EXIF opcional. La paridad con Pillow por plataforma la fija la
//! sonda P (`jpeg_sonda`); aquí solo se reutilizan sus piezas.

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

/// Inserta, tras SOI/APP0 y en el orden de Pillow 12.3.0, el APP1 EXIF (`exif`
/// ya trae `Exif\0\0`, como lo pasa Pillow) y después el segmento COM
/// (`comment` del origen: Pillow lo copia en `crop`/`transpose` y lo escribe
/// tras el EXIF; un comentario vacío no genera marcador).
pub fn insertar_segmentos(
    jpeg: &[u8],
    exif: Option<&[u8]>,
    comentario: Option<&[u8]>,
) -> Result<Vec<u8>, AtomError> {
    if let Some(e) = exif {
        validar_exif(e)?;
    }
    let comentario = comentario.filter(|c| !c.is_empty());
    if let Some(c) = comentario {
        if c.len() > MAX_BYTES_APP1 {
            return Err(AtomError::Entrada("comment is too long".into()));
        }
    }
    let pos = pos_tras_app0(jpeg)?;
    let mut out = Vec::with_capacity(
        jpeg.len() + exif.map_or(0, |e| e.len() + 4) + comentario.map_or(0, |c| c.len() + 4),
    );
    out.extend_from_slice(&jpeg[..pos]);
    for (marca, datos) in [(0xE1u8, exif), (0xFEu8, comentario)] {
        if let Some(d) = datos {
            out.extend_from_slice(&[0xFF, marca]);
            out.extend_from_slice(&((d.len() + 2) as u16).to_be_bytes());
            out.extend_from_slice(d);
        }
    }
    out.extend_from_slice(&jpeg[pos..]);
    Ok(out)
}

/// Solo el APP1 EXIF (sonda de paridad).
pub fn insertar_app1(jpeg: &[u8], exif: &[u8]) -> Result<Vec<u8>, AtomError> {
    insertar_segmentos(jpeg, Some(exif), None)
}

/// Como `Image.save(quality=q, exif=exif, comment=c)` de Pillow para RGB.
/// `calidad` 0 se trata como 1 (libjpeg recorta `jpeg_set_quality(0)` a 1).
pub fn codificar_rgb(
    rgb: &[u8],
    ancho: usize,
    alto: usize,
    calidad: u8,
    exif: Option<&[u8]>,
    comentario: Option<&[u8]>,
) -> Result<Vec<u8>, AtomError> {
    if let Some(e) = exif {
        validar_exif(e)?;
    }
    let j = codificar_jpeg(rgb, ancho, alto, false, calidad.max(1))?;
    if exif.is_none() && comentario.map_or(true, |c| c.is_empty()) {
        return Ok(j);
    }
    insertar_segmentos(&j, exif, comentario)
}

/// Como `Image.open().convert("RGB")` de un JPEG RGB: `(ancho, alto, píxeles RGB compactos)`.
pub fn decodificar_rgb(jpeg: &[u8]) -> Result<(usize, usize, Vec<u8>), AtomError> {
    let img = turbojpeg::decompress(jpeg, turbojpeg::PixelFormat::RGB)
        .map_err(|e| AtomError::Io(format!("libjpeg-turbo: {e}")))?;
    if img.pitch != img.width * 3 || img.pixels.len() != img.width * img.height * 3 {
        return Err(AtomError::Io(format!(
            "libjpeg-turbo devolvió un buffer con pitch {} para {}x{}",
            img.pitch, img.width, img.height
        )));
    }
    Ok((img.width, img.height, img.pixels))
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
        let j = codificar_rgb(&px, w, h, 90, Some(b"Exif\0\0ab"), None).unwrap();
        let (dw, dh, d) = decodificar_rgb(&j).unwrap();
        assert_eq!((dw, dh, d.len()), (w, h, w * h * 3));
    }

    #[test]
    fn com_va_tras_el_exif_y_vacio_no_escribe() {
        let j = codificar_jpeg(&[10u8; 16 * 16 * 3], 16, 16, false, 75).unwrap();
        let o = insertar_segmentos(&j, Some(b"Exif\0\0xx"), Some(b"hola")).unwrap();
        let l0 = u16::from_be_bytes([o[4], o[5]]) as usize;
        assert_eq!(&o[4 + l0..6 + l0], &[0xFF, 0xE1]);
        let p = 4 + l0 + 2 + 2 + 8;
        assert_eq!(&o[p..p + 2], &[0xFF, 0xFE]);
        assert_eq!(&o[p + 4..p + 8], b"hola");
        let v = insertar_segmentos(&j, None, Some(b"")).unwrap();
        assert_eq!(v, j);
    }

    #[test]
    fn calidad_cero_como_uno() {
        let px = vec![77u8; 16 * 16 * 3];
        assert_eq!(
            codificar_rgb(&px, 16, 16, 0, None, None).unwrap(),
            codificar_rgb(&px, 16, 16, 1, None, None).unwrap()
        );
    }

    #[test]
    fn decode_de_basura_falla() {
        assert!(decodificar_rgb(&[1, 2, 3, 4]).is_err());
    }
}
