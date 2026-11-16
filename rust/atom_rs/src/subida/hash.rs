//! Hashes locales (md5 + crc32c) y comparación con `x-goog-hash` del servidor.

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use md5::{Digest, Md5};
use std::fs::File;
use std::io::{self, Read};
use std::path::Path;

/// Hashes del servidor tal como vienen en base64 (sin decodificar).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct HashesServidor {
    pub md5: Option<String>,
    pub crc32c: Option<String>,
}

/// Hashes locales binarios.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct HashesLocales {
    pub md5: [u8; 16],
    pub crc32c: u32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Veredicto {
    Ok,
    Mismatch { campo: &'static str },
    SinHash,
}

/// md5 + crc32c de un fichero en una sola pasada, leyendo por bloques.
pub fn hashes_fichero(ruta: &Path, bloque: usize) -> io::Result<([u8; 16], u32)> {
    let mut f = File::open(ruta)?;
    let mut buf = vec![0u8; bloque.max(1)];
    let mut md = Md5::new();
    let mut crc: u32 = 0;
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        md.update(&buf[..n]);
        crc = crc32c::crc32c_append(crc, &buf[..n]);
    }
    let digest = md.finalize();
    let mut out = [0u8; 16];
    out.copy_from_slice(&digest);
    Ok((out, crc))
}

/// md5 + crc32c de un buffer en memoria.
pub fn hashes_bytes(datos: &[u8]) -> ([u8; 16], u32) {
    let digest = Md5::digest(datos);
    let mut out = [0u8; 16];
    out.copy_from_slice(&digest);
    (out, crc32c::crc32c(datos))
}

pub fn b64_md5(md5: &[u8; 16]) -> String {
    STANDARD.encode(md5)
}

/// crc32c en base64 de sus 4 bytes big-endian (formato de GCS).
pub fn b64_crc32c(crc: u32) -> String {
    STANDARD.encode(crc.to_be_bytes())
}

/// Parsea una o varias cabeceras `x-goog-hash` (unidas por coma o por salto de
/// línea). Clave insensible a mayúsculas; el valor base64 conserva los `=`.
pub fn parse_x_goog_hash(cabecera: &str) -> HashesServidor {
    let mut h = HashesServidor::default();
    for parte in cabecera.split([',', '\n', '\r']) {
        let parte = parte.trim();
        // split_once en el PRIMER '=': el valor puede acabar en '=='.
        if let Some((clave, valor)) = parte.split_once('=') {
            let clave = clave.trim().to_ascii_lowercase();
            let valor = valor.trim();
            if valor.is_empty() {
                continue;
            }
            match clave.as_str() {
                "md5" => h.md5 = Some(valor.to_string()),
                "crc32c" => h.crc32c = Some(valor.to_string()),
                _ => {}
            }
        }
    }
    h
}

/// Cualquier hash presente que difiera = Mismatch; ninguno presente = SinHash.
pub fn coincide(local: &HashesLocales, servidor: &HashesServidor) -> Veredicto {
    let mut alguno = false;
    if let Some(m) = &servidor.md5 {
        alguno = true;
        if *m != b64_md5(&local.md5) {
            return Veredicto::Mismatch { campo: "md5" };
        }
    }
    if let Some(c) = &servidor.crc32c {
        alguno = true;
        if *c != b64_crc32c(local.crc32c) {
            return Veredicto::Mismatch { campo: "crc32c" };
        }
    }
    if alguno {
        Veredicto::Ok
    } else {
        Veredicto::SinHash
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn md5_vacio_conocido() {
        let (m, _) = hashes_bytes(b"");
        assert_eq!(b64_md5(&m), "1B2M2Y8AsgTpgAmY7PhCfg==");
    }

    #[test]
    fn crc32c_vector_conocido() {
        let (_, c) = hashes_bytes(b"123456789");
        assert_eq!(c, 0xE306_9283);
        assert_eq!(b64_crc32c(c), STANDARD.encode([0xE3, 0x06, 0x92, 0x83]));
    }

    #[test]
    fn fichero_igual_que_bytes() {
        let ruta = std::env::temp_dir().join(format!("atom_rs_hash_{}.bin", std::process::id()));
        std::fs::write(&ruta, b"123456789").unwrap();
        let r = hashes_fichero(&ruta, 4).unwrap();
        let _ = std::fs::remove_file(&ruta);
        assert_eq!(r, hashes_bytes(b"123456789"));
    }

    #[test]
    fn parse_varios_valores_y_mayusculas() {
        let h = parse_x_goog_hash("CRC32C=AAAAAA==, MD5=1B2M2Y8AsgTpgAmY7PhCfg==");
        assert_eq!(h.crc32c.as_deref(), Some("AAAAAA=="));
        assert_eq!(h.md5.as_deref(), Some("1B2M2Y8AsgTpgAmY7PhCfg=="));
    }

    #[test]
    fn parse_clave_repetida_en_lineas() {
        let h = parse_x_goog_hash("crc32c=AAAAAA==\nmd5=1B2M2Y8AsgTpgAmY7PhCfg==\ncrc32c=BBBBBB==");
        assert_eq!(h.crc32c.as_deref(), Some("BBBBBB=="));
        assert!(h.md5.is_some());
    }

    #[test]
    fn parse_sin_hash() {
        assert_eq!(parse_x_goog_hash(""), HashesServidor::default());
        assert_eq!(parse_x_goog_hash("foo=bar"), HashesServidor::default());
    }

    fn locales_vacio() -> HashesLocales {
        let (m, c) = hashes_bytes(b"");
        HashesLocales { md5: m, crc32c: c }
    }

    #[test]
    fn veredicto_ok_sinhash_y_mismatch() {
        let l = locales_vacio();
        let ok = HashesServidor {
            md5: Some(b64_md5(&l.md5)),
            crc32c: Some(b64_crc32c(l.crc32c)),
        };
        assert_eq!(coincide(&l, &ok), Veredicto::Ok);
        assert_eq!(coincide(&l, &HashesServidor::default()), Veredicto::SinHash);
        let mal_md5 = HashesServidor { md5: Some("AAAA".into()), crc32c: ok.crc32c.clone() };
        assert_eq!(coincide(&l, &mal_md5), Veredicto::Mismatch { campo: "md5" });
        let mal_crc = HashesServidor { md5: ok.md5.clone(), crc32c: Some("AAAAAA==".into()) };
        assert_eq!(coincide(&l, &mal_crc), Veredicto::Mismatch { campo: "crc32c" });
    }
}
