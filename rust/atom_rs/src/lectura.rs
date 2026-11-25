//! Fase `io` (4a, ABI 7): lectura verificada de un fichero local e integridad de
//! cola JPEG. Réplica de `atom_core/lectura_segura.py` (fuente de verdad):
//! mismos bytes, mismos errores (con el mismo `str` tras `traducir_error`), mismas
//! esperas (0.5, 1, 2, 4, 8 s con tope `backoff_max`), misma regla de EOF estable.
//! Nunca se devuelven bytes parciales ni se rellena nada.
//!
//! `gs://` NO se lee aquí: el wrapper Python sigue llamando a `_leer_completo_gcs`;
//! una ruta `gs://` es un error visible. Sin pool rayon.

use crate::error::DatosErrorFila;
use pyo3::exceptions::{PyOverflowError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::io::Read;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::time::Duration;

/// `lectura_segura._BLOQUE`.
const BLOQUE: usize = 1024 * 1024;
/// `lectura_segura.VENTANA_COLA_JPEG`.
pub const VENTANA_COLA_JPEG: usize = 64 * 1024;

/// Motivo de fallo de `leer_core`. `esperas` = esperas efectivamente calculadas
/// antes del fallo (las que Python habría pasado a `time.sleep`).
#[derive(Debug)]
pub enum ErrorLectura {
    Incompleta { ruta: String, leidos: u64, esperados: u64, intentos: i64, esperas: Vec<f64> },
    Cambiado { ruta: String, actual: u64, indexado: u64, esperas: Vec<f64> },
    Io { ruta: String, errno: Option<i32>, winerror: Option<i32>, mensaje: String, esperas: Vec<f64> },
    /// `time.sleep` de Python lanza `ValueError` (negativa/NaN) u `OverflowError`.
    EsperaInvalida { negativa_o_nan: bool, mensaje: String },
}

impl ErrorLectura {
    fn a_datos(self) -> Result<DatosErrorFila, PyErr> {
        Ok(match self {
            ErrorLectura::Incompleta { ruta, leidos, esperados, intentos, esperas } => {
                let mut d = DatosErrorFila::nuevo(
                    "lectura_incompleta",
                    format!(
                        "lectura incompleta: leídos {leidos} de {esperados} bytes tras {intentos} intentos ({ruta})"
                    ),
                );
                d.ruta = Some(ruta);
                d.leidos = Some(leidos);
                d.esperados = Some(esperados);
                d.intentos = Some(intentos);
                d.esperas = esperas;
                d
            }
            ErrorLectura::Cambiado { ruta, actual, indexado, esperas } => {
                let mut d = DatosErrorFila::nuevo(
                    "fichero_cambiado",
                    format!(
                        "el fichero cambió desde el índice: {indexado} vs {actual} bytes ({ruta})"
                    ),
                );
                d.ruta = Some(ruta);
                d.leidos = Some(0);
                d.esperados = Some(actual);
                d.intentos = Some(0);
                d.actual = Some(actual);
                d.indexado = Some(indexado);
                d.esperas = esperas;
                d
            }
            ErrorLectura::Io { ruta, errno, mensaje, esperas, .. } => {
                let mut d = DatosErrorFila::nuevo("io", mensaje);
                d.ruta = Some(ruta);
                d.errno = errno;
                d.esperas = esperas;
                d
            }
            ErrorLectura::EsperaInvalida { negativa_o_nan, mensaje } => {
                return Err(if negativa_o_nan {
                    PyValueError::new_err(mensaje)
                } else {
                    PyOverflowError::new_err(mensaje)
                });
            }
        })
    }
}

/// errno POSIX. En unix, el raw. En Windows `raw_os_error()` es un código Win32
/// (3 = PATH_NOT_FOUND, 5 = ACCESS_DENIED), no errno: se deriva de `ErrorKind`.
#[cfg(not(windows))]
fn errno_posix(e: &std::io::Error) -> Option<i32> {
    e.raw_os_error()
}
#[cfg(windows)]
fn errno_posix(e: &std::io::Error) -> Option<i32> {
    use std::io::ErrorKind::*;
    match e.kind() {
        NotFound => Some(2),          // ENOENT
        PermissionDenied => Some(13), // EACCES
        AlreadyExists => Some(17),    // EEXIST
        _ => None,
    }
}

/// Código Win32 crudo (solo Windows); en otras plataformas no aplica.
#[cfg(windows)]
fn winerror(e: &std::io::Error) -> Option<i32> {
    e.raw_os_error()
}
#[cfg(not(windows))]
fn winerror(_e: &std::io::Error) -> Option<i32> {
    None
}

fn io_err(ruta: &str, e: &std::io::Error, esperas: &[f64]) -> ErrorLectura {
    ErrorLectura::Io {
        ruta: ruta.to_string(),
        errno: errno_posix(e),
        winerror: winerror(e),
        mensaje: e.to_string(),
        esperas: esperas.to_vec(),
    }
}

#[cfg(feature = "inyeccion-fallos")]
fn tam_stat(ruta: &str, real: u64) -> u64 {
    crate::fallos::tam_stat(ruta).unwrap_or(real)
}
#[cfg(not(feature = "inyeccion-fallos"))]
fn tam_stat(_ruta: &str, real: u64) -> u64 {
    real
}

#[cfg(feature = "inyeccion-fallos")]
fn tras_lectura(ruta: &str, datos: &mut Vec<u8>) {
    crate::fallos::aplicar_guion(ruta, datos);
}
#[cfg(not(feature = "inyeccion-fallos"))]
fn tras_lectura(_ruta: &str, _datos: &mut Vec<u8>) {}

#[cfg(feature = "inyeccion-fallos")]
fn evento_hidratar(ruta: &str) {
    crate::fallos::evento_hidratar(ruta);
}
#[cfg(not(feature = "inyeccion-fallos"))]
fn evento_hidratar(_ruta: &str) {}

/// Duerme `segundos` como `time.sleep`; con inyección registra la espera y, si
/// `_sin_dormir(True)`, no duerme. Valida igual que Python (negativa/NaN/enorme).
fn dormir(segundos: f64) -> Result<(), ErrorLectura> {
    let d = match Duration::try_from_secs_f64(segundos) {
        Ok(d) => d,
        Err(_) => {
            return Err(if segundos.is_nan() {
                ErrorLectura::EsperaInvalida {
                    negativa_o_nan: true,
                    mensaje: "Invalid value NaN (not a number)".into(),
                }
            } else if segundos < 0.0 {
                ErrorLectura::EsperaInvalida {
                    negativa_o_nan: true,
                    mensaje: "sleep length must be non-negative".into(),
                }
            } else {
                ErrorLectura::EsperaInvalida {
                    negativa_o_nan: false,
                    mensaje: "timestamp too large to convert to C _PyTime_t".into(),
                }
            });
        }
    };
    #[cfg(feature = "inyeccion-fallos")]
    {
        if crate::fallos::registrar_espera(segundos) {
            return Ok(());
        }
    }
    std::thread::sleep(d);
    Ok(())
}

/// `lectura_segura._hidratar`: lee el fichero entero en secuencial por bloques de
/// 1 MiB hasta EOF; los errores de E/S se ignoran (como en Python).
fn hidratar(ruta: &str) {
    evento_hidratar(ruta);
    if let Ok(mut f) = std::fs::File::open(ruta) {
        let mut buf = vec![0u8; BLOQUE];
        loop {
            match f.read(&mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
        }
    }
}

/// `lectura_segura._esperados`: tamaño por `metadata` del MISMO handle ya abierto
/// (sin `stat` por ruta aparte: en DriveFS cada acceso por ruta es otra ida al
/// montaje); `bytes_origen` (distinto de 0) que difiera = `Cambiado` al momento.
fn esperados(
    ruta: &str,
    f: &std::fs::File,
    bytes_origen: Option<u64>,
    esperas: &[f64],
) -> Result<u64, ErrorLectura> {
    let real = f.metadata().map_err(|e| io_err(ruta, &e, esperas))?.len();
    let real = tam_stat(ruta, real);
    if let Some(ind) = bytes_origen {
        if ind != 0 && ind != real {
            return Err(ErrorLectura::Cambiado {
                ruta: ruta.to_string(),
                actual: real,
                indexado: ind,
                esperas: esperas.to_vec(),
            });
        }
    }
    Ok(real)
}

/// Núcleo de `leer_completo` (mismo orden de operaciones que Python).
pub fn leer_core(
    ruta: &str,
    intentos: i64,
    backoff: f64,
    backoff_max: f64,
    bytes_origen: Option<u64>,
) -> Result<Vec<u8>, ErrorLectura> {
    let mut espera = backoff;
    let mut esperas: Vec<f64> = Vec::new();
    let mut leidos: u64 = 0;
    let mut esp: u64 = 0;
    let mut previo: Option<u64> = None;
    let total = std::cmp::max(1, intentos);
    for n in 1..=total {
        // Un solo `open` por intento: tamaño por el handle y buffer reservado con
        // ese tamaño. `read_to_end` (no `read_exact`) para que un fichero más corto
        // siga siendo `Incompleta` y no un error de EOF. `io_err` conserva el código
        // OS (`errno`/`winerror`, p. ej. 1450) para clasificarlo desde Python.
        let mut f = std::fs::File::open(ruta).map_err(|e| io_err(ruta, &e, &esperas))?;
        esp = esperados(ruta, &f, bytes_origen, &esperas)?;
        let mut datos: Vec<u8> = Vec::new();
        datos
            .try_reserve_exact((esp as usize).saturating_add(1))
            .map_err(|e| ErrorLectura::Io {
                ruta: ruta.to_string(),
                errno: None,
                winerror: None,
                mensaje: format!("sin memoria para {esp} bytes: {e}"),
                esperas: esperas.clone(),
            })?;
        f.read_to_end(&mut datos).map_err(|e| io_err(ruta, &e, &esperas))?;
        drop(f);
        tras_lectura(ruta, &mut datos);
        leidos = datos.len() as u64;
        if leidos >= esp {
            return Ok(datos);
        }
        if n > 1 && previo == Some(leidos) {
            // EOF estable: dos lecturas seguidas devuelven los mismos bytes.
            return Err(ErrorLectura::Incompleta {
                ruta: ruta.to_string(),
                leidos,
                esperados: esp,
                intentos: n,
                esperas,
            });
        }
        previo = Some(leidos);
        if n < intentos {
            esperas.push(espera);
            dormir(espera)?;
            espera = (espera * 2.0).min(backoff_max);
            hidratar(ruta);
        }
    }
    Err(ErrorLectura::Incompleta {
        ruta: ruta.to_string(),
        leidos,
        esperados: esp,
        intentos,
        esperas,
    })
}

fn es_gs(ruta: &str) -> bool {
    ruta.get(..5).is_some_and(|p| p.eq_ignore_ascii_case("gs://"))
}

fn mensaje_panic(p: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = p.downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = p.downcast_ref::<String>() {
        s.clone()
    } else {
        "panic sin mensaje".to_string()
    }
}

/// Devuelve TODOS los bytes de `ruta` o lanza `atom_rs.ErrorFila`
/// (`lectura_incompleta` / `fichero_cambiado` / `io`). `ruta` es `str` (el
/// wrapper pasa `os.fspath`); `gs://` es un `ValueError`.
#[pyfunction]
#[pyo3(signature = (ruta, intentos=5, backoff=0.5, backoff_max=8.0, bytes_origen=None))]
pub fn leer_completo(
    py: Python<'_>,
    ruta: &str,
    intentos: i64,
    backoff: f64,
    backoff_max: f64,
    bytes_origen: Option<u64>,
) -> PyResult<Py<PyBytes>> {
    if es_gs(ruta) {
        return Err(PyValueError::new_err(format!(
            "atom_rs.leer_completo no lee rutas gs:// ({ruta}): usa el camino Python (_leer_completo_gcs)"
        )));
    }
    let r = py.allow_threads(|| {
        catch_unwind(AssertUnwindSafe(|| {
            leer_core(ruta, intentos, backoff, backoff_max, bytes_origen)
        }))
    });
    match r {
        Ok(Ok(datos)) => Ok(PyBytes::new(py, &datos).unbind()),
        Ok(Err(e)) => {
            let win = match &e {
                ErrorLectura::Io { winerror, .. } => *winerror,
                _ => None,
            };
            Err(match e.a_datos() {
                Ok(d) => {
                    let err = d.a_pyerr(py);
                    if win.is_some() {
                        // `winerror` aparte del errno (DatosErrorFila no lo lleva).
                        let _ = err.value(py).setattr("winerror", win);
                    }
                    err
                }
                Err(pe) => pe,
            })
        }
        Err(p) => {
            let mut d = DatosErrorFila::nuevo(
                "panic",
                format!("panic en atom_rs.leer_completo: {}", mensaje_panic(p.as_ref())),
            );
            d.ruta = Some(ruta.to_string());
            Err(d.a_pyerr(py))
        }
    }
}

// ---------------------------------------------------------------------------
// Integridad de cola JPEG (`jpeg_cola_valida` || `jpeg_eoi_en_cola_util` ||
// `jpeg_eoi_por_segmentos`), la condición de truncado de `apply.py`.

fn contiene_eoi(d: &[u8]) -> bool {
    d.windows(2).any(|w| w == [0xFF, 0xD9])
}

/// `jpeg_cola_valida(bytes_cola)`: no vacía, no toda a ceros y con FFD9.
fn cola_valida(cola: &[u8]) -> bool {
    !cola.is_empty() && cola.iter().any(|&b| b != 0) && contiene_eoi(cola)
}

/// `jpeg_eoi_en_cola_util(datos)`: FFD9 en los últimos 64 KiB sin ceros finales.
fn eoi_en_cola_util(datos: &[u8]) -> bool {
    let fin = datos.iter().rposition(|&b| b != 0).map_or(0, |i| i + 1);
    let s = &datos[..fin];
    let ini = s.len().saturating_sub(VENTANA_COLA_JPEG);
    contiene_eoi(&s[ini..])
}

/// Byte válido tras FF+ dentro del scan: `[\x01-\xcf\xd8-\xfe]`.
fn marcador_en_scan(b: u8) -> bool {
    (0x01..=0xCF).contains(&b) || (0xD8..=0xFE).contains(&b)
}

/// `_RE_MARCADOR_EN_SCAN.search(datos, pos)`; devuelve `m.end()`.
fn buscar_marcador_scan(datos: &[u8], desde: usize) -> Option<usize> {
    let n = datos.len();
    let mut i = desde;
    while i < n {
        if datos[i] != 0xFF {
            i += 1;
            continue;
        }
        let mut j = i;
        while j < n && datos[j] == 0xFF {
            j += 1;
        }
        if j < n && marcador_en_scan(datos[j]) {
            return Some(j + 1);
        }
        i = j;
    }
    None
}

/// `jpeg_eoi_por_segmentos(datos)`.
fn eoi_por_segmentos(datos: &[u8]) -> bool {
    let n = datos.len();
    if n < 4 || datos[0] != 0xFF || datos[1] != 0xD8 {
        return false;
    }
    let mut pos = 2usize;
    while pos < n {
        if datos[pos] != 0xFF {
            return false;
        }
        while pos < n && datos[pos] == 0xFF {
            pos += 1;
        }
        if pos >= n {
            return false;
        }
        let marcador = datos[pos];
        pos += 1;
        if marcador == 0xD9 {
            return true;
        }
        if marcador == 0x00 {
            return false;
        }
        if marcador == 0x01 || (0xD0..=0xD8).contains(&marcador) {
            continue;
        }
        if pos + 2 > n {
            return false;
        }
        let longitud = ((datos[pos] as usize) << 8) | datos[pos + 1] as usize;
        if longitud < 2 || pos + longitud > n {
            return false;
        }
        pos += longitud;
        if marcador == 0xDA {
            match buscar_marcador_scan(datos, pos) {
                None => return false,
                // `pos = m.end() - 1; pos -= 1`: la última FF del run, justo antes del marcador.
                Some(fin) => pos = fin - 2,
            }
        }
    }
    false
}

/// Lógica pura de `jpeg_integro_por_cola` (testeable sin intérprete).
pub fn integro_por_cola(datos: &[u8]) -> bool {
    let ini = datos.len().saturating_sub(VENTANA_COLA_JPEG);
    cola_valida(&datos[ini..]) || eoi_en_cola_util(datos) || eoi_por_segmentos(datos)
}

/// `True` si el JPEG tiene EOI útil (condición contraria al truncado de `apply.py`).
#[pyfunction]
pub fn jpeg_integro_por_cola(py: Python<'_>, datos: &[u8]) -> bool {
    py.allow_threads(|| integro_por_cola(datos))
}

/// `True` solo si la wheel se construyó con la feature `inyeccion-fallos`.
#[pyfunction]
pub fn tiene_inyeccion() -> bool {
    cfg!(feature = "inyeccion-fallos")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn tmp(nombre: &str, datos: &[u8]) -> String {
        let p = std::env::temp_dir().join(format!("atom_rs_lectura_{}_{nombre}", std::process::id()));
        std::fs::File::create(&p).unwrap().write_all(datos).unwrap();
        p.to_string_lossy().into_owned()
    }

    #[test]
    fn lee_completo_sin_esperas() {
        let r = tmp("ok", &[7u8; 1000]);
        assert_eq!(leer_core(&r, 5, 0.5, 8.0, None).unwrap().len(), 1000);
        assert_eq!(leer_core(&r, 5, 0.5, 8.0, Some(1000)).unwrap().len(), 1000);
        let _ = std::fs::remove_file(&r);
    }

    #[test]
    fn bytes_origen_distinto_es_cambiado() {
        let r = tmp("cambia", &[7u8; 100]);
        match leer_core(&r, 5, 0.5, 8.0, Some(200)) {
            Err(ErrorLectura::Cambiado { actual: 100, indexado: 200, esperas, .. }) => assert!(esperas.is_empty()),
            o => panic!("{o:?}"),
        }
        let _ = std::fs::remove_file(&r);
    }

    #[test]
    fn intentos_cero_no_entra_en_panic() {
        let r = tmp("cero", &[]);
        assert!(leer_core(&r, 0, 0.5, 8.0, None).unwrap().is_empty());
        let _ = std::fs::remove_file(&r);
    }

    #[test]
    fn ruta_inexistente_es_io() {
        assert!(matches!(
            leer_core("/no/existe/atom_rs", 5, 0.5, 8.0, None),
            Err(ErrorLectura::Io { .. })
        ));
    }

    #[cfg(unix)]
    #[test]
    fn error_io_conserva_codigo_os() {
        match leer_core("/no/existe/atom_rs", 5, 0.5, 8.0, None) {
            Err(ErrorLectura::Io { errno: Some(2), .. }) => {}
            o => panic!("{o:?}"),
        }
    }

    #[test]
    fn un_solo_open_lee_exacto_y_vacio() {
        let r = tmp("exacto", &[3u8; 4096]);
        let d = leer_core(&r, 1, 0.5, 8.0, Some(4096)).unwrap();
        assert_eq!(d.len(), 4096);
        assert!(d.iter().all(|&b| b == 3));
        let _ = std::fs::remove_file(&r);
    }

    #[test]
    fn cola_jpeg_casos() {
        assert!(integro_por_cola(&[0xFF, 0xD8, 0xFF, 0xD9]));
        assert!(!integro_por_cola(&[0xFF, 0xD8, 0, 0, 0, 0]));
        assert!(!integro_por_cola(&[]));
        // FFD9 dentro de un APP1 (miniatura) y scan truncado sin EOI en cola: solo
        // el recorrido por segmentos puede decidir; aquí la cola SÍ lo contiene.
        let mut d = vec![0xFF, 0xD8, 0xFF, 0xE1, 0x00, 0x06, 0xFF, 0xD9, 0x00, 0x00];
        d.extend_from_slice(&[0xFF, 0xDA, 0x00, 0x02, 0x11, 0x22]);
        assert!(integro_por_cola(&d)); // cola útil contiene FFD9 (vía rápida, como Python)
    }

    #[test]
    fn marcador_en_scan_busqueda() {
        // 00 stuffing, RST, luego FF D9 final.
        let d = [0x11, 0xFF, 0x00, 0xFF, 0xD0, 0xFF, 0xFF, 0xD9];
        assert_eq!(buscar_marcador_scan(&d, 0), Some(8));
    }
}
