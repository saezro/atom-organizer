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

// Excepción de fila (fase `io` y siguientes): lleva el motivo estructurado en
// atributos para que `rust_bridge.traducir_error` reconstruya la excepción
// ORIGINAL de Python (mismo tipo, mismo `str`). Es una excepción normal
// (`Exception`), picklable por `__dict__`.
pyo3::create_exception!(
    atom_rs,
    ErrorFila,
    pyo3::exceptions::PyException,
    "Fallo de una fila en Rust; `.tipo` dice cuál (ver rust_bridge.traducir_error)."
);

/// Datos de un `ErrorFila`. `tipo` en {"lectura_incompleta","fichero_cambiado",
/// "jpeg_truncado_origen","xmp_grande","verificacion","io","panic"}.
#[derive(Debug, Clone)]
pub struct DatosErrorFila {
    pub tipo: &'static str,
    pub mensaje: String,
    pub ruta: Option<String>,
    pub leidos: Option<u64>,
    pub esperados: Option<u64>,
    pub intentos: Option<i64>,
    pub actual: Option<u64>,
    pub indexado: Option<u64>,
    pub errno: Option<i32>,
    pub esperas: Vec<f64>,
}

impl DatosErrorFila {
    pub fn nuevo(tipo: &'static str, mensaje: String) -> Self {
        DatosErrorFila {
            tipo,
            mensaje,
            ruta: None,
            leidos: None,
            esperados: None,
            intentos: None,
            actual: None,
            indexado: None,
            errno: None,
            esperas: Vec::new(),
        }
    }

    /// Construye la excepción con todos los atributos. Si fijar un atributo
    /// fallara, devuelve ESE error (nunca una excepción a medias).
    pub fn a_pyerr(self, py: pyo3::Python<'_>) -> PyErr {
        use pyo3::types::PyAnyMethods;
        let err = ErrorFila::new_err(self.mensaje.clone());
        let fijar = || -> pyo3::PyResult<()> {
            let v = err.value(py);
            v.setattr("tipo", self.tipo)?;
            v.setattr("ruta", self.ruta.clone())?;
            v.setattr("leidos", self.leidos)?;
            v.setattr("esperados", self.esperados)?;
            v.setattr("intentos", self.intentos)?;
            v.setattr("actual", self.actual)?;
            v.setattr("indexado", self.indexado)?;
            v.setattr("errno", self.errno)?;
            v.setattr("esperas", self.esperas.clone())?;
            Ok(())
        };
        match fijar() {
            Ok(()) => err,
            Err(e) => e,
        }
    }
}
