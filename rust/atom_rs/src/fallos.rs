//! Puntos de inyección de fallos para los tests de paridad (feature
//! `inyeccion-fallos`). Solo se compila en la wheel de TEST: la de release no
//! trae este módulo ni sus símbolos. El estado es global del proceso y los tests
//! lo reinician con `_reset()`.
//!
//! - `_guion_lecturas(ruta, tamanos)`: la n-ésima lectura ENTERA de `ruta` devuelve
//!   `tamanos[n]` bytes (`-1` = completa; sin entrada = completa).
//! - `_tam_stat_falso(ruta, n)`: `stat` de `ruta` declara `n` bytes.
//! - `_sin_dormir(True)`: las esperas no duermen, solo se registran.
//! - `_esperas_log()`: esperas calculadas (segundos) desde el último `_reset()`.
//! - `_eventos()`: `leer:<ruta>` / `hidratar:<ruta>` en orden de ocurrencia.

use pyo3::prelude::*;
use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard};

struct Estado {
    guiones: BTreeMap<String, (Vec<i64>, usize)>,
    stat: BTreeMap<String, u64>,
    sin_dormir: bool,
    esperas: Vec<f64>,
    eventos: Vec<String>,
}

static ESTADO: Mutex<Estado> = Mutex::new(Estado {
    guiones: BTreeMap::new(),
    stat: BTreeMap::new(),
    sin_dormir: false,
    esperas: Vec::new(),
    eventos: Vec::new(),
});

fn estado() -> MutexGuard<'static, Estado> {
    ESTADO.lock().unwrap_or_else(|e| e.into_inner())
}

/// Tamaño de `stat` inyectado para `ruta`, si lo hay.
pub fn tam_stat(ruta: &str) -> Option<u64> {
    estado().stat.get(ruta).copied()
}

/// Aplica el guion a una lectura entera ya hecha de `ruta` (la recorta si toca) y
/// registra el evento `leer`.
pub fn aplicar_guion(ruta: &str, datos: &mut Vec<u8>) {
    let mut e = estado();
    e.eventos.push(format!("leer:{ruta}"));
    if let Some((tam, cont)) = e.guiones.get_mut(ruta) {
        let idx = *cont;
        *cont += 1;
        if let Some(&t) = tam.get(idx) {
            if t >= 0 {
                datos.truncate(t as usize);
            }
        }
    }
}

pub fn evento_hidratar(ruta: &str) {
    estado().eventos.push(format!("hidratar:{ruta}"));
}

/// Registra la espera calculada; devuelve `true` si NO hay que dormir de verdad.
pub fn registrar_espera(segundos: f64) -> bool {
    let mut e = estado();
    e.esperas.push(segundos);
    e.sin_dormir
}

#[pyfunction]
#[pyo3(name = "_guion_lecturas")]
fn guion_lecturas(ruta: &str, tamanos: Vec<i64>) {
    estado().guiones.insert(ruta.to_string(), (tamanos, 0));
}

#[pyfunction]
#[pyo3(name = "_tam_stat_falso")]
fn tam_stat_falso(ruta: &str, n: u64) {
    estado().stat.insert(ruta.to_string(), n);
}

#[pyfunction]
#[pyo3(name = "_sin_dormir")]
fn sin_dormir(activo: bool) {
    estado().sin_dormir = activo;
}

#[pyfunction]
#[pyo3(name = "_esperas_log")]
fn esperas_log() -> Vec<f64> {
    estado().esperas.clone()
}

#[pyfunction]
#[pyo3(name = "_eventos")]
fn eventos() -> Vec<String> {
    estado().eventos.clone()
}

#[pyfunction]
#[pyo3(name = "_reset")]
fn reset() {
    let mut e = estado();
    e.guiones.clear();
    e.stat.clear();
    e.sin_dormir = false;
    e.esperas.clear();
    e.eventos.clear();
}

pub fn registrar(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(guion_lecturas, m)?)?;
    m.add_function(wrap_pyfunction!(tam_stat_falso, m)?)?;
    m.add_function(wrap_pyfunction!(sin_dormir, m)?)?;
    m.add_function(wrap_pyfunction!(esperas_log, m)?)?;
    m.add_function(wrap_pyfunction!(eventos, m)?)?;
    m.add_function(wrap_pyfunction!(reset, m)?)?;
    Ok(())
}
