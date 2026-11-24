#![allow(dead_code)]
//! Servidor HTTP/1.1 falso (solo tests): `std::net::TcpListener`, una petición
//! por conexión, sin crates extra.

use super::cliente::Entorno;
use std::io::{Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

#[derive(Debug, Clone)]
pub struct Peticion {
    pub metodo: String,
    /// Ruta con query.
    pub ruta: String,
    pub cabeceras: Vec<(String, String)>,
    pub cuerpo: Vec<u8>,
}

impl Peticion {
    pub fn cab(&self, k: &str) -> Option<&str> {
        self.cabeceras
            .iter()
            .find(|(n, _)| n.eq_ignore_ascii_case(k))
            .map(|(_, v)| v.as_str())
    }
}

pub enum Accion {
    Responder(u16, Vec<(String, String)>),
    /// Responde con un cuerpo de texto (p. ej. el XML de error de GCS).
    ResponderCuerpo(u16, String),
    /// Cierra la conexión sin responder.
    Cortar,
}

pub fn resp(estado: u16, cab: &[(&str, &str)]) -> Accion {
    Accion::Responder(estado, cab.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect())
}

/// Cabeceras `x-goog-hash` + generación para un contenido.
pub fn cab_hash(datos: &[u8]) -> Vec<(String, String)> {
    use super::hash::{b64_crc32c, b64_md5, hashes_bytes};
    let (md5, crc) = hashes_bytes(datos);
    vec![
        ("x-goog-hash".to_string(), format!("crc32c={},md5={}", b64_crc32c(crc), b64_md5(&md5))),
        ("x-goog-generation".to_string(), "5".to_string()),
    ]
}

pub struct Servidor {
    pub base: String,
    log: Arc<Mutex<Vec<Peticion>>>,
    fin: Arc<AtomicBool>,
    addr: SocketAddr,
}

type Manejador = dyn Fn(&Peticion, usize) -> Accion + Send + Sync;

impl Servidor {
    pub fn nuevo<F>(f: F) -> Servidor
    where
        F: Fn(&Peticion, usize) -> Accion + Send + Sync + 'static,
    {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        let fin = Arc::new(AtomicBool::new(false));
        let log: Arc<Mutex<Vec<Peticion>>> = Arc::new(Mutex::new(Vec::new()));
        let f: Arc<Manejador> = Arc::new(f);
        let contador = Arc::new(AtomicUsize::new(0));
        let (fin2, log2) = (fin.clone(), log.clone());
        thread::spawn(move || {
            for conn in listener.incoming() {
                if fin2.load(Ordering::SeqCst) {
                    break;
                }
                let Ok(s) = conn else { continue };
                let (f, log, n) = (f.clone(), log2.clone(), contador.clone());
                thread::spawn(move || atender(s, &*f, &log, &n));
            }
        });
        Servidor { base: format!("http://{addr}"), log, fin, addr }
    }

    pub fn peticiones(&self) -> Vec<Peticion> {
        self.log.lock().expect("log").clone()
    }
}

impl Drop for Servidor {
    fn drop(&mut self) {
        self.fin.store(true, Ordering::SeqCst);
        let _ = TcpStream::connect(self.addr);
    }
}

fn buscar(h: &[u8], aguja: &[u8]) -> Option<usize> {
    h.windows(aguja.len()).position(|w| w == aguja)
}

fn atender(mut s: TcpStream, f: &Manejador, log: &Mutex<Vec<Peticion>>, n: &AtomicUsize) {
    let mut buf: Vec<u8> = Vec::new();
    let mut tmp = [0u8; 8192];
    let fin_cab = loop {
        match s.read(&mut tmp) {
            Ok(0) | Err(_) => return,
            Ok(k) => {
                buf.extend_from_slice(&tmp[..k]);
                if let Some(p) = buscar(&buf, b"\r\n\r\n") {
                    break p + 4;
                }
            }
        }
    };
    let txt = String::from_utf8_lossy(&buf[..fin_cab]).to_string();
    let mut lineas = txt.split("\r\n");
    let primera = lineas.next().unwrap_or("");
    let mut partes = primera.split_whitespace();
    let metodo = partes.next().unwrap_or("").to_string();
    let ruta = partes.next().unwrap_or("").to_string();
    let mut cabeceras = Vec::new();
    let mut cl = 0usize;
    for l in lineas {
        if let Some((k, v)) = l.split_once(':') {
            let (k, v) = (k.trim().to_string(), v.trim().to_string());
            if k.eq_ignore_ascii_case("content-length") {
                cl = v.parse().unwrap_or(0);
            }
            cabeceras.push((k, v));
        }
    }
    while buf.len() < fin_cab + cl {
        match s.read(&mut tmp) {
            Ok(0) | Err(_) => break,
            Ok(k) => buf.extend_from_slice(&tmp[..k]),
        }
    }
    let fin_cuerpo = (fin_cab + cl).min(buf.len());
    let p = Peticion { metodo, ruta, cabeceras, cuerpo: buf[fin_cab..fin_cuerpo].to_vec() };
    let idx = n.fetch_add(1, Ordering::SeqCst);
    log.lock().expect("log").push(p.clone());
    match f(&p, idx) {
        Accion::Cortar => {
            let _ = s.shutdown(Shutdown::Both);
        }
        Accion::Responder(estado, cab) => {
            let mut out = format!("HTTP/1.1 {estado} X\r\n");
            for (k, v) in cab {
                out.push_str(&format!("{k}: {v}\r\n"));
            }
            out.push_str("Content-Length: 0\r\nConnection: close\r\n\r\n");
            let _ = s.write_all(out.as_bytes());
            let _ = s.flush();
        }
        Accion::ResponderCuerpo(estado, cuerpo) => {
            let out = format!(
                "HTTP/1.1 {estado} X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{cuerpo}",
                cuerpo.len()
            );
            let _ = s.write_all(out.as_bytes());
            let _ = s.flush();
        }
    }
}

/// Entorno de pruebas: URL = `{base}/{remote}` (+ `?ifGenerationMatch=0`).
pub struct EntornoFalso {
    pub base: String,
    pub token: Option<String>,
    pub recover_ok: bool,
    pub recovers: AtomicUsize,
}

impl EntornoFalso {
    pub fn nuevo(base: &str) -> Arc<EntornoFalso> {
        Arc::new(EntornoFalso {
            base: base.to_string(),
            token: Some("Bearer t".to_string()),
            recover_ok: true,
            recovers: AtomicUsize::new(0),
        })
    }

    /// Modo URL firmada: sin credencial OAuth (`token_fn=None`).
    pub fn sin_credencial(base: &str) -> Arc<EntornoFalso> {
        Arc::new(EntornoFalso {
            base: base.to_string(),
            token: None,
            recover_ok: false,
            recovers: AtomicUsize::new(0),
        })
    }
}

impl Entorno for EntornoFalso {
    fn url(&self, remote: &str, _size: u64, sobrescribir: bool) -> Result<String, String> {
        let q = if sobrescribir { "" } else { "?ifGenerationMatch=0" };
        Ok(format!("{}/{}{}", self.base, remote, q))
    }
    fn token(&self) -> Result<Option<String>, String> {
        Ok(self.token.clone())
    }
    fn recover(&self) -> bool {
        self.recovers.fetch_add(1, Ordering::SeqCst);
        self.recover_ok
    }
}

static N_TMP: AtomicUsize = AtomicUsize::new(0);

/// Escribe `datos` en un fichero temporal único y devuelve su ruta.
pub fn fichero_tmp(datos: &[u8]) -> String {
    let n = N_TMP.fetch_add(1, Ordering::SeqCst);
    let ruta = std::env::temp_dir().join(format!("atom_rs_subida_{}_{n}.bin", std::process::id()));
    std::fs::write(&ruta, datos).expect("escribir tmp");
    ruta.to_string_lossy().into_owned()
}
