//! Sonda de paridad libjpeg-turbo (Task P): codifica/decodifica RGB con los
//! mismos flags que Pillow para comparar bytes por plataforma. No es una fase.

use crate::error::AtomError;
use crate::termica::{codificar_jpeg, rotar};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// Inserta un APP1 (`exif` ya trae `Exif\0\0`, como lo pasa Pillow) justo tras
/// el APP0/JFIF: Pillow lo escribe con `jpeg_write_marker` tras `start_compress`.
fn insertar_app1(jpeg: &[u8], exif: &[u8]) -> Result<Vec<u8>, AtomError> {
    if exif.len() > 65533 {
        return Err(AtomError::Entrada(format!("exif de {} bytes no cabe en un APP1", exif.len())));
    }
    if jpeg.len() < 4 || jpeg[0] != 0xFF || jpeg[1] != 0xD8 {
        return Err(AtomError::Entrada("salida sin SOI".into()));
    }
    // Tras SOI: APP0 (FF E0, len BE) si existe; el APP1 va a continuación.
    let mut pos = 2;
    if jpeg[2] == 0xFF && jpeg[3] == 0xE0 {
        let l = u16::from_be_bytes([jpeg[4], jpeg[5]]) as usize;
        pos = 4 + l;
    }
    let mut out = Vec::with_capacity(jpeg.len() + exif.len() + 4);
    out.extend_from_slice(&jpeg[..pos]);
    out.extend_from_slice(&[0xFF, 0xE1]);
    out.extend_from_slice(&((exif.len() + 2) as u16).to_be_bytes());
    out.extend_from_slice(exif);
    out.extend_from_slice(&jpeg[pos..]);
    Ok(out)
}

fn comprobar(rgb: &[u8], ancho: u32, alto: u32) -> Result<(), AtomError> {
    if ancho == 0 || alto == 0 || rgb.len() != ancho as usize * alto as usize * 3 {
        return Err(AtomError::Entrada(format!(
            "rgb de {} bytes no es {ancho}x{alto}x3",
            rgb.len()
        )));
    }
    Ok(())
}

/// Como `Image.save(quality=q, exif=exif)` de Pillow, RGB 4:2:0.
#[pyfunction]
#[pyo3(signature = (rgb, ancho, alto, calidad, exif=None))]
pub fn jpeg_sonda_codificar(
    py: Python<'_>,
    rgb: &[u8],
    ancho: u32,
    alto: u32,
    calidad: u8,
    exif: Option<&[u8]>,
) -> PyResult<Py<PyBytes>> {
    comprobar(rgb, ancho, alto)?;
    if !(1..=100).contains(&calidad) {
        return Err(AtomError::Entrada(format!("calidad fuera de 1..=100: {calidad}")).into());
    }
    let out = py.allow_threads(|| -> Result<Vec<u8>, AtomError> {
        let j = codificar_jpeg(rgb, ancho as usize, alto as usize, false, calidad)?;
        match exif {
            Some(e) => insertar_app1(&j, e),
            None => Ok(j),
        }
    })?;
    Ok(PyBytes::new(py, &out).into())
}

/// Como `Image.open().convert("RGB").tobytes()` (ISLOW + fancy upsampling).
#[pyfunction]
pub fn jpeg_sonda_decodificar(py: Python<'_>, jpeg: &[u8]) -> PyResult<(u32, u32, Py<PyBytes>)> {
    let img = py
        .allow_threads(|| turbojpeg::decompress(jpeg, turbojpeg::PixelFormat::RGB))
        .map_err(|e| AtomError::Io(format!("libjpeg-turbo: {e}")))?;
    // pitch == ancho*3 en una Image<Vec<u8>> recién creada.
    Ok((img.width as u32, img.height as u32, PyBytes::new(py, &img.pixels).into()))
}

/// `termica::rotar` aplicado por píxel RGB. 90 = horario (PIL ROTATE_270),
/// 270 = antihorario (PIL ROTATE_90).
#[pyfunction]
pub fn jpeg_sonda_rotar(py: Python<'_>, rgb: &[u8], ancho: u32, alto: u32, grados: i32) -> PyResult<Py<PyBytes>> {
    comprobar(rgb, ancho, alto)?;
    let px: Vec<[u8; 3]> = rgb.chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect();
    let r = rotar(&px, ancho as usize, alto as usize, grados);
    let plano: Vec<u8> = r.iter().flatten().copied().collect();
    Ok(PyBytes::new(py, &plano).into())
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
    fn tamano_incoherente_falla() {
        assert!(comprobar(&[0u8; 5], 1, 1).is_err());
    }
}
