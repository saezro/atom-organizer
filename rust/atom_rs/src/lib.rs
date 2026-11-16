//! atom_rs: aceleradores Rust de ATOM Organizer.
//!
//! El contrato con Python vive en `src/atom_core/rust_bridge.py`:
//! `ABI_ESPERADA` debe coincidir con `ABI` (se sube a la vez en ambos lados
//! cuando cambia la firma de cualquier función exportada).

use pyo3::prelude::*;

pub mod error;

/// Versión del contrato Python<->Rust. Subir al romper firmas.
pub const ABI: u32 = 1;

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
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn abi_es_uno() {
        assert_eq!(version_abi(), 1);
    }

    #[test]
    fn version_no_vacia() {
        assert!(!version().is_empty());
    }
}
