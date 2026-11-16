//! TIFF float32 (modo "F" de Pillow) reproducido BYTE A BYTE, sin crate `tiff`
//! (que no garantiza el layout).
//!
//! Layout observado volcando `Image.fromarray(arr_float64).save(..., format='TIFF')`
//! con Pillow 12.3.0 (la de `packaging/requirements/requirements*.txt`), para
//! 640x512, 512x640, 1280x1024, 1x1 y 5x3 (mismo patrón en todos):
//!
//! ```text
//! 0x00  49 49 2A 00         "II", 42: little-endian
//! 0x04  08 00 00 00         offset del IFD0 = 8
//! 0x08  0A 00               10 entradas, en orden ascendente de tag:
//!       tag  tipo        n  valor
//!       256  LONG(4)     1  ancho               ImageWidth
//!       257  LONG(4)     1  alto                ImageLength
//!       258  SHORT(3)    1  32                  BitsPerSample
//!       259  SHORT(3)    1  1                   Compression = none
//!       262  SHORT(3)    1  1                   Photometric = BlackIsZero
//!       273  LONG(4)     1  134                 StripOffsets (UN solo strip)
//!       278  LONG(4)     1  alto                RowsPerStrip = alto
//!       279  LONG(4)     1  ancho*alto*4        StripByteCounts
//!       284  SHORT(3)    1  1                   PlanarConfig = contig
//!       339  SHORT(3)    1  3                   SampleFormat = IEEE float
//!       (SHORT: valor en los 2 primeros bytes del campo de 4, resto a 0)
//! 0x82  00 00 00 00         sin IFD siguiente
//! 0x86  píxeles float32 LE, fila a fila, sin relleno final (134 = 8+2+10*12+4)
//! ```
//!
//! Sin tags de resolución ni de software: `fromarray` no lleva `info`. Si una
//! versión futura de Pillow cambia este layout, el A/B `test_tiff_bit_identico_a_pil`
//! lo detecta (sha256) y hay que volver a volcar la referencia.

use crate::error::AtomError;
use std::fs;
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const TIPO_SHORT: u16 = 3;
const TIPO_LONG: u16 = 4;
const N_ENTRADAS: u16 = 10;
/// Offset de los píxeles: cabecera (8) + nº entradas (2) + 10*12 + IFD siguiente (4).
pub const OFFSET_DATOS: u32 = 8 + 2 + (N_ENTRADAS as u32) * 12 + 4;

fn entrada(buf: &mut Vec<u8>, tag: u16, tipo: u16, valor: u32) {
    buf.extend_from_slice(&tag.to_le_bytes());
    buf.extend_from_slice(&tipo.to_le_bytes());
    buf.extend_from_slice(&1u32.to_le_bytes());
    match tipo {
        TIPO_SHORT => {
            buf.extend_from_slice(&(valor as u16).to_le_bytes());
            buf.extend_from_slice(&[0, 0]);
        }
        _ => buf.extend_from_slice(&valor.to_le_bytes()),
    }
}

/// Codifica el TIFF completo en memoria (cabecera + IFD + píxeles).
pub fn codificar(ancho: u32, alto: u32, datos: &[f32]) -> Result<Vec<u8>, AtomError> {
    if ancho == 0 || alto == 0 {
        return Err(AtomError::Entrada(format!("TIFF de tamaño nulo: {ancho}x{alto}")));
    }
    let n = (ancho as u64) * (alto as u64);
    if datos.len() as u64 != n {
        return Err(AtomError::Entrada(format!(
            "TIFF {ancho}x{alto}: {} valores, se esperaban {n}",
            datos.len()
        )));
    }
    let bytes_pixeles = n * 4;
    if bytes_pixeles + OFFSET_DATOS as u64 > u32::MAX as u64 {
        return Err(AtomError::Entrada(format!(
            "TIFF {ancho}x{alto} demasiado grande para un TIFF clásico"
        )));
    }
    let mut buf = Vec::with_capacity(OFFSET_DATOS as usize + bytes_pixeles as usize);
    buf.extend_from_slice(b"II");
    buf.extend_from_slice(&42u16.to_le_bytes());
    buf.extend_from_slice(&8u32.to_le_bytes());
    buf.extend_from_slice(&N_ENTRADAS.to_le_bytes());
    entrada(&mut buf, 256, TIPO_LONG, ancho);
    entrada(&mut buf, 257, TIPO_LONG, alto);
    entrada(&mut buf, 258, TIPO_SHORT, 32);
    entrada(&mut buf, 259, TIPO_SHORT, 1);
    entrada(&mut buf, 262, TIPO_SHORT, 1);
    entrada(&mut buf, 273, TIPO_LONG, OFFSET_DATOS);
    entrada(&mut buf, 278, TIPO_LONG, alto);
    entrada(&mut buf, 279, TIPO_LONG, bytes_pixeles as u32);
    entrada(&mut buf, 284, TIPO_SHORT, 1);
    entrada(&mut buf, 339, TIPO_SHORT, 3);
    buf.extend_from_slice(&0u32.to_le_bytes());
    debug_assert_eq!(buf.len(), OFFSET_DATOS as usize);
    for v in datos {
        buf.extend_from_slice(&v.to_le_bytes());
    }
    Ok(buf)
}

/// Escribe `datos` (fila a fila, `ancho*alto` valores) como TIFF float32 idéntico
/// al de Pillow, de forma atómica (`.parcial` + rename).
pub fn escribir_f32(path: &str, ancho: u32, alto: u32, datos: &[f32]) -> Result<(), AtomError> {
    let bytes = codificar(ancho, alto, datos)?;
    let parcial = escribir_parcial(path, &bytes)?;
    publicar(&parcial, path)
}

static CONTADOR: AtomicU32 = AtomicU32::new(0);

/// Nombre del temporal atómico, mismo formato que `apply.ruta_parcial`:
/// `<raíz>.parcial.<pid>-<8 hex><ext>` (lo reconoce `apply.es_nombre_parcial`).
pub fn ruta_parcial(destino: &str) -> String {
    let p = Path::new(destino);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.subsec_nanos() ^ (d.as_secs() as u32))
        .unwrap_or(0);
    let n = CONTADOR.fetch_add(1, Ordering::Relaxed);
    let sufijo = nanos ^ n.wrapping_mul(0x9E37_79B9);
    let pid = std::process::id();
    let nombre = match (p.file_stem(), p.extension()) {
        (Some(raiz), Some(ext)) => format!(
            "{}.parcial.{pid}-{sufijo:08x}.{}",
            raiz.to_string_lossy(),
            ext.to_string_lossy()
        ),
        (Some(raiz), None) => format!("{}.parcial.{pid}-{sufijo:08x}", raiz.to_string_lossy()),
        _ => format!("salida.parcial.{pid}-{sufijo:08x}"),
    };
    match p.parent() {
        Some(dir) if !dir.as_os_str().is_empty() => dir.join(nombre).to_string_lossy().into_owned(),
        _ => nombre,
    }
}

/// Escribe `bytes` en un `.parcial` junto a `destino` y devuelve su ruta. Si falla,
/// no deja el `.parcial`.
pub fn escribir_parcial(destino: &str, bytes: &[u8]) -> Result<String, AtomError> {
    let parcial = ruta_parcial(destino);
    let res = (|| -> std::io::Result<()> {
        let mut f = fs::File::create(&parcial)?;
        f.write_all(bytes)?;
        f.flush()
    })();
    if let Err(e) = res {
        let _ = fs::remove_file(&parcial);
        return Err(AtomError::Io(format!("{destino}: {e}")));
    }
    Ok(parcial)
}

/// `os.replace(parcial, destino)`; si falla, borra el `.parcial`.
pub fn publicar(parcial: &str, destino: &str) -> Result<(), AtomError> {
    if let Err(e) = fs::rename(parcial, destino) {
        let _ = fs::remove_file(parcial);
        return Err(AtomError::Io(format!("{destino}: {e}")));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cabecera_como_pillow_640x512() {
        let datos = vec![0f32; 640 * 512];
        let b = codificar(640, 512, &datos).unwrap();
        assert_eq!(b.len(), 134 + 640 * 512 * 4);
        // Volcado de Pillow 12.3.0 (primeros 134 bytes), ver cabecera del módulo.
        let esperado: [u8; 134] = [
            0x49, 0x49, 0x2a, 0x00, 0x08, 0x00, 0x00, 0x00, 0x0a, 0x00, //
            0x00, 0x01, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x80, 0x02, 0x00, 0x00, //
            0x01, 0x01, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, //
            0x02, 0x01, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00, //
            0x03, 0x01, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, //
            0x06, 0x01, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, //
            0x11, 0x01, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x86, 0x00, 0x00, 0x00, //
            0x16, 0x01, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, //
            0x17, 0x01, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x14, 0x00, //
            0x1c, 0x01, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, //
            0x53, 0x01, 0x03, 0x00, 0x01, 0x00, 0x00, 0x00, 0x03, 0x00, 0x00, 0x00, //
            0x00, 0x00, 0x00, 0x00,
        ];
        assert_eq!(&b[..134], &esperado[..]);
    }

    #[test]
    fn pixeles_float32_le_tras_la_cabecera() {
        let b = codificar(2, 1, &[1.5f32, -0.25]).unwrap();
        assert_eq!(&b[134..138], &1.5f32.to_le_bytes());
        assert_eq!(&b[138..142], &(-0.25f32).to_le_bytes());
    }

    #[test]
    fn tamano_incoherente_es_error() {
        assert!(codificar(2, 2, &[0.0; 3]).is_err());
        assert!(codificar(0, 2, &[]).is_err());
    }

    #[test]
    fn ruta_parcial_formato_de_apply() {
        let r = ruta_parcial("/a/b/DJI_0001_T.tiff");
        assert!(r.starts_with("/a/b/DJI_0001_T.parcial."));
        assert!(r.ends_with(".tiff"));
        let medio = &r["/a/b/DJI_0001_T.parcial.".len()..r.len() - ".tiff".len()];
        let (pid, hex) = medio.split_once('-').unwrap();
        assert!(pid.chars().all(|c| c.is_ascii_digit()));
        assert_eq!(hex.len(), 8);
        assert!(hex.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    }
}
