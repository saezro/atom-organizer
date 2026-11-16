//! atom_rs: aceleradores Rust de ATOM Organizer.
//!
//! El contrato con Python vive en `src/atom_core/rust_bridge.py`:
//! `ABI_ESPERADA` debe coincidir con `ABI` (se sube a la vez en ambos lados
//! cuando cambia la firma de cualquier función exportada).

use pyo3::prelude::*;

pub mod error;
pub mod exif_meta;
#[cfg(feature = "inyeccion-fallos")]
pub mod fallos;
pub mod jpeg;
pub mod jpeg_sonda;
pub mod lectura;
pub mod rgb;
pub mod subida;
pub mod termica;
pub mod tiff_f32;
pub mod xmp;

/// Versión del contrato Python<->Rust. Subir al romper firmas o al añadir una
/// fase. 2 = fase `termica` (T1). 3 = sonda
/// libjpeg-turbo (P). 4 = fase `subida` (T6, `subir_lote`). 5 = fase `indice`
/// (T2, `parsear_cabecera`). 6 = fase `rgb` (T3, `rgb_procesar`). 7 = fase `io` (4a, `leer_completo`,
/// `jpeg_integro_por_cola`, excepción `ErrorFila`).
pub const ABI: u32 = 7;

/// Versión del crate (Cargo.toml).
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Versión de ABI del puente; `rust_bridge` la compara con `ABI_ESPERADA`.
#[pyfunction]
fn version_abi() -> u32 {
    ABI
}

#[pymodule]
fn atom_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(version_abi, m)?)?;
    // Fase `termica` (T1, ABI 2). Además `rust_bridge.modulo()` valida al
    // arrancar que existan los símbolos de cada fase activa (RustNoDisponible).
    m.add_class::<termica::TermicaStats>()?;
    m.add_function(wrap_pyfunction!(termica::termica_procesar, m)?)?;
    m.add_function(wrap_pyfunction!(termica::libjpeg_turbo_version, m)?)?;
    // Sonda de paridad libjpeg-turbo (P, ABI 3).
    m.add_function(wrap_pyfunction!(jpeg_sonda::jpeg_sonda_codificar, m)?)?;
    m.add_function(wrap_pyfunction!(jpeg_sonda::jpeg_sonda_decodificar, m)?)?;
    m.add_function(wrap_pyfunction!(jpeg_sonda::jpeg_sonda_rotar, m)?)?;
    // Fase `subida` (T6, ABI 4).
    m.add_function(wrap_pyfunction!(subida::subir_lote, m)?)?;
    // Fase `indice` (T2, ABI 5).
    m.add_class::<exif_meta::CabeceraParseada>()?;
    m.add_function(wrap_pyfunction!(exif_meta::parsear_cabecera, m)?)?;
    // Fase `rgb` (T3, ABI 6).
    m.add_class::<rgb::RgbStats>()?;
    m.add_function(wrap_pyfunction!(rgb::rgb_procesar, m)?)?;
    // Fase `io` (4a, ABI 7).
    m.add("ErrorFila", m.py().get_type::<error::ErrorFila>())?;
    m.add_function(wrap_pyfunction!(lectura::leer_completo, m)?)?;
    m.add_function(wrap_pyfunction!(lectura::jpeg_integro_por_cola, m)?)?;
    m.add_function(wrap_pyfunction!(lectura::tiene_inyeccion, m)?)?;
    #[cfg(feature = "inyeccion-fallos")]
    fallos::registrar(m)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_es_siete() {
        assert_eq!(version_abi(), 7);
    }

    #[test]
    fn version_no_vacia() {
        assert!(!version().is_empty());
    }
}
