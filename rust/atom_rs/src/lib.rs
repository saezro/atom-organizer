//! atom_rs: aceleradores Rust de ATOM Organizer.
//!
//! El contrato con Python vive en `src/atom_core/rust_bridge.py`:
//! `ABI_ESPERADA` debe coincidir con `ABI` (se sube a la vez en ambos lados
//! cuando cambia la firma de cualquier función exportada).

use pyo3::prelude::*;

pub mod error;
pub mod termica;
pub mod tiff_f32;

/// Versión del contrato Python<->Rust. Subir al romper firmas o al añadir una
/// fase. 2 = fase `termica` (T1).
pub const ABI: u32 = 2;

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
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_es_dos() {
        assert_eq!(version_abi(), 2);
    }

    #[test]
    fn version_no_vacia() {
        assert!(!version().is_empty());
    }
}
