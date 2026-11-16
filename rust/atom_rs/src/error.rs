//! Errores del crate. Se exponen a Python como excepciones concretas:
//! nunca se devuelve un valor "por defecto" cuando algo falla.

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::PyErr;
use std::fmt;

#[derive(Debug)]
pub enum AtomError {
    /// Argumento o dato de entrada inválido.
    Entrada(String),
    /// Fallo de E/S (ruta, lectura, escritura).
    Io(String),
}

impl fmt::Display for AtomError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AtomError::Entrada(m) => write!(f, "entrada inválida: {m}"),
            AtomError::Io(m) => write!(f, "error de E/S: {m}"),
        }
    }
}

impl std::error::Error for AtomError {}

impl From<AtomError> for PyErr {
    fn from(e: AtomError) -> PyErr {
        match e {
            AtomError::Entrada(_) => PyValueError::new_err(e.to_string()),
            AtomError::Io(_) => PyOSError::new_err(e.to_string()),
        }
    }
}

impl From<std::io::Error> for AtomError {
    fn from(e: std::io::Error) -> Self {
        AtomError::Io(e.to_string())
    }
}
