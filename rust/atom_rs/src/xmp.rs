//! XMP DJI de la cabecera (fase `indice`, T2).
//!
//! Réplica byte a byte de las manías de Python, sin "arreglarlas":
//! - `indice._bloque_xmp_desde_buffer`: TIFF >= 256 KiB con IFD válidos -> tag 700
//!   (`_tiff_exif_en_buffer` + `_tiff_xmp_desde_buffer`); si no, el buffer entero en
//!   latin-1 con `\r\n`/`\r` -> `\n`, válido solo si contiene `</x:xmpmeta`. Si no lo
//!   contiene, `None`: Python relee el fichero (`exif.leer_bloque_xmp`) como hoy.
//! - `exif.get_gimbal_yaw_pitch` / `exif.get_xmp_data`: `d[find('<x:xmpmeta'):
//!   find('</x:xmpmeta')+12]` con semántica de slice de Python (índices -1 incluidos),
//!   `split("drone-dji:")`, `rfind(nombre) != -1`, `Utils.extract_number_from_string`
//!   (`str.isdigit()` en latin-1 incluye ¹²³), regla `found` (primera pieza cuyo número
//!   pasa `float()`; si no pasa, el valor se queda con lo extraído y se sigue buscando)
//!   y `"0"` si la etiqueta no aparece.
//!
//! Todo opera sobre bytes: latin-1 es 1 byte = 1 carácter, así que `find`/`split`/
//! slices sobre bytes dan los mismos índices que sobre el `str` de Python.

use std::collections::HashSet;

/// Debe coincidir con `exif._XMP_HEADER_BYTES` (= `indice._BYTES_CABECERA`); solo
/// decide si un TIFF entra por el tag 700 (`len(buf) >= _BYTES_CABECERA`).
/// `test_tiff_r_jpeg` lo ejercita con un buffer de exactamente ese tamaño.
pub const BYTES_CABECERA: usize = 262_144;

/// Orden de `exif.get_xmp_data`.
pub const CAMPOS_XMP: [&[u8]; 9] = [
    b"AbsoluteAltitude",
    b"RelativeAltitude",
    b"GimbalRollDegree",
    b"FlightRollDegree",
    b"FlightYawDegree",
    b"FlightPitchDegree",
    b"CamReverse",
    b"GimbalReverse",
    b"RtkFlag",
];

/// Orden de `exif.get_gimbal_yaw_pitch`.
pub const CAMPOS_GIMBAL: [&[u8]; 2] = [b"GimbalYawDegree", b"GimbalPitchDegree"];

/// `str.find`: primera aparición de `aguja` (vacía -> 0).
pub fn buscar(pajar: &[u8], aguja: &[u8]) -> Option<usize> {
    if aguja.is_empty() {
        return Some(0);
    }
    if aguja.len() > pajar.len() {
        return None;
    }
    pajar.windows(aguja.len()).position(|w| w == aguja)
}

/// `texto.replace('\r\n', '\n').replace('\r', '\n')`.
pub fn normalizar_saltos(b: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0usize;
    while let Some(&c) = b.get(i) {
        if c == b'\r' {
            out.push(b'\n');
            if b.get(i + 1) == Some(&b'\n') {
                i += 2;
                continue;
            }
        } else {
            out.push(c);
        }
        i += 1;
    }
    out
}

/// `str.split(sep)` con `sep` no vacío (cortes sin solape, de izquierda a derecha).
pub fn dividir<'a>(pajar: &'a [u8], sep: &[u8]) -> Vec<&'a [u8]> {
    let mut piezas = Vec::new();
    if sep.is_empty() {
        piezas.push(pajar);
        return piezas;
    }
    let mut resto = pajar;
    while let Some(p) = buscar(resto, sep) {
        piezas.push(resto.get(..p).unwrap_or(&[]));
        resto = resto.get(p + sep.len()..).unwrap_or(&[]);
    }
    piezas.push(resto);
    piezas
}

/// Índices `[a, b)` de `s[inicio:fin]` en Python (paso 1) sobre una cadena de `len`.
pub fn rebanada_py(len: usize, inicio: i64, fin: i64) -> (usize, usize) {
    let n = len as i64;
    let norm = |i: i64| -> i64 {
        if i < 0 {
            (i + n).max(0)
        } else {
            i.min(n)
        }
    };
    let a = norm(inicio);
    let b = norm(fin);
    if b < a {
        (a as usize, a as usize)
    } else {
        (a as usize, b as usize)
    }
}

/// `str.isdigit()` para un carácter latin-1: ASCII 0-9 y los superíndices ² ³ ¹.
fn es_digito_py(c: u8) -> bool {
    c.is_ascii_digit() || c == 0xB2 || c == 0xB3 || c == 0xB9
}

/// `Utils.extract_number_from_string`.
pub fn extraer_numero(pieza: &[u8]) -> Vec<u8> {
    pieza
        .iter()
        .copied()
        .filter(|&c| es_digito_py(c) || c == b'-' || c == b'.')
        .collect()
}

/// ¿`float(s)` de Python acepta `s`? Solo para el alfabeto de `extraer_numero`
/// (dígitos, ¹²³, `-`, `.`): signo opcional, dígitos ASCII con a lo sumo un punto y
/// al menos un dígito. Los superíndices no son `Nd` y `float()` los rechaza.
pub fn float_py_valido(s: &[u8]) -> bool {
    let cuerpo = match s.split_first() {
        Some((b'-', resto)) => resto,
        _ => s,
    };
    let mut digitos = 0usize;
    let mut puntos = 0usize;
    for &c in cuerpo {
        if c.is_ascii_digit() {
            digitos += 1;
        } else if c == b'.' {
            puntos += 1;
        } else {
            return false;
        }
    }
    digitos > 0 && puntos <= 1
}

/// bytes latin-1 -> `String` (cada byte es el carácter U+00XX).
pub fn latin1(b: &[u8]) -> String {
    b.iter().map(|&c| char::from(c)).collect()
}

/// Valores de `nombres` como los devuelven `get_gimbal_yaw_pitch`/`get_xmp_data`
/// a partir de `bloque` (el `d` de Python).
pub fn extraer_campos(bloque: &[u8], nombres: &[&[u8]]) -> Vec<String> {
    let ini = buscar(bloque, b"<x:xmpmeta").map_or(-1, |p| p as i64);
    let fin = buscar(bloque, b"</x:xmpmeta").map_or(-1, |p| p as i64);
    let (a, b) = rebanada_py(bloque.len(), ini, fin + 12);
    let xmp_str = bloque.get(a..b).unwrap_or(&[]);
    let piezas = dividir(xmp_str, b"drone-dji:");
    nombres
        .iter()
        .map(|nombre| {
            let mut valor: Vec<u8> = b"0".to_vec();
            let mut hallado = false;
            for pieza in &piezas {
                if !hallado && buscar(pieza, nombre).is_some() {
                    valor = extraer_numero(pieza);
                    hallado = float_py_valido(&valor);
                }
            }
            latin1(&valor)
        })
        .collect()
}

/// `_TIFF_TAMANO_TIPO.get(tipo, 1)` de `indice.py`.
fn tamano_tipo_indice(tipo: u64) -> u64 {
    match tipo {
        1 | 2 | 6 | 7 => 1,
        3 | 8 => 2,
        4 | 9 | 11 | 13 => 4,
        5 | 10 | 12 | 16 | 17 | 18 => 8,
        _ => 1,
    }
}

/// `int.from_bytes(buf[pos:pos + tam], orden)` con el recorte de slice de Python
/// (fuera del buffer -> menos bytes, o 0). `tam` <= 4.
fn u_py(buf: &[u8], pos: u64, tam: u64, le: bool) -> u64 {
    let n = buf.len() as u64;
    if pos >= n {
        return 0;
    }
    let fin = pos.saturating_add(tam).min(n);
    let s = buf.get(pos as usize..fin as usize).unwrap_or(&[]);
    let mut v: u64 = 0;
    if le {
        for &c in s.iter().rev() {
            v = (v << 8) | u64::from(c);
        }
    } else {
        for &c in s {
            v = (v << 8) | u64::from(c);
        }
    }
    v
}

fn orden_le(buf: &[u8]) -> bool {
    buf.get(..2) == Some(&b"II"[..])
}

/// `indice._tiff_exif_en_buffer`.
pub fn tiff_exif_en_buffer(buf: &[u8]) -> bool {
    let le = orden_le(buf);
    let n = buf.len() as u64;
    let u = |pos: u64, tam: u64| u_py(buf, pos, tam, le);
    let mut pendientes: Vec<u64> = vec![u(4, 4)];
    let mut vistos: HashSet<u64> = HashSet::new();
    let mut es_ifd0 = true;
    while let Some(off) = pendientes.pop() {
        if vistos.contains(&off) || off < 8 || off + 2 > n {
            return false;
        }
        vistos.insert(off);
        let cuenta = u(off, 2);
        if off + 2 + 12 * cuenta > n {
            return false;
        }
        for i in 0..cuenta {
            let e = off + 2 + 12 * i;
            let tag = u(e, 2);
            let tipo = u(e + 2, 2);
            let count = u(e + 4, 4);
            let tam = tamano_tipo_indice(tipo) * count;
            if tam > 4 {
                let voff = u(e + 8, 4);
                if voff + tam > n {
                    return false;
                }
            }
            if es_ifd0 && (tag == 0x8769 || tag == 0x8825) {
                pendientes.push(u(e + 8, 4));
            }
        }
        es_ifd0 = false;
    }
    true
}

/// `indice._tiff_xmp_desde_buffer`: `Some(vacío)` = sin tag 700; `Some(texto)` = su
/// valor normalizado; `None` = valor fuera del buffer (Python relee).
pub fn tiff_xmp_desde_buffer(buf: &[u8]) -> Option<Vec<u8>> {
    let le = orden_le(buf);
    let u = |pos: u64, tam: u64| u_py(buf, pos, tam, le);
    let off = u(4, 4);
    let cuenta = u(off, 2);
    for i in 0..cuenta {
        let e = off + 2 + 12 * i;
        if u(e, 2) != 700 {
            continue;
        }
        let tipo = u(e + 2, 2);
        let tam = tamano_tipo_indice(tipo) * u(e + 4, 4);
        let voff = if tam <= 4 { e + 8 } else { u(e + 8, 4) };
        if voff + tam > buf.len() as u64 {
            return None;
        }
        let valor = buf.get(voff as usize..(voff + tam) as usize)?;
        return Some(normalizar_saltos(valor));
    }
    Some(Vec::new())
}

/// `indice._bloque_xmp_desde_buffer` sin el fallback por ruta: `None` cuando Python
/// tendría que releer el fichero (`exif.leer_bloque_xmp`).
pub fn bloque_xmp(buf: &[u8]) -> Option<Vec<u8>> {
    let es_tiff = matches!(buf.get(..4), Some(b"II*\0") | Some(b"MM\0*"));
    if buf.len() >= BYTES_CABECERA && es_tiff && tiff_exif_en_buffer(buf) {
        if let Some(x) = tiff_xmp_desde_buffer(buf) {
            return Some(x);
        }
    }
    let texto = normalizar_saltos(buf);
    if buscar(&texto, b"</x:xmpmeta").is_some() {
        Some(texto)
    } else {
        None
    }
}

/// Campos XMP de una cabecera, como los usa `indice._leer_metadatos`.
pub struct DatosXmp {
    pub gimbal_yaw: String,
    pub gimbal_pitch: String,
    pub xmp: Vec<String>,
    /// `'FlightYawDegree' in bloque_xmp` (sobre el bloque entero).
    pub flight_yaw_en_xmp: bool,
}

/// `None` = el bloque no sale del buffer (Python relee, `xmp_presente=False`).
pub fn analizar(buf: &[u8]) -> Option<DatosXmp> {
    let bloque = bloque_xmp(buf)?;
    let mut gimbal = extraer_campos(&bloque, &CAMPOS_GIMBAL).into_iter();
    let gimbal_yaw = gimbal.next().unwrap_or_else(|| "0".to_string());
    let gimbal_pitch = gimbal.next().unwrap_or_else(|| "0".to_string());
    Some(DatosXmp {
        gimbal_yaw,
        gimbal_pitch,
        xmp: extraer_campos(&bloque, &CAMPOS_XMP),
        flight_yaw_en_xmp: buscar(&bloque, b"FlightYawDegree").is_some(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_como_python() {
        for ok in ["1", "1.", ".5", "-.5", "-0.0", "00.5", "12.50"] {
            assert!(float_py_valido(ok.as_bytes()), "{ok}");
        }
        for mal in ["", "-", ".", "-.", "1-2", "--1", "1.2.3", "1e-3"] {
            assert!(!float_py_valido(mal.as_bytes()), "{mal}");
        }
        assert!(!float_py_valido(&[0xB2]));
        assert!(!float_py_valido(&[b'5', 0xB2]));
    }

    #[test]
    fn extraer_incluye_superindices_y_quita_signo_mas() {
        assert_eq!(extraer_numero(b"GimbalYawDegree=\"+12.5\" "), b"12.5".to_vec());
        assert_eq!(extraer_numero(&[b'a', 0xB2, b'3']), vec![0xB2, b'3']);
        assert_eq!(extraer_numero(b"x=\"1e-3\""), b"1-3".to_vec());
    }

    #[test]
    fn split_como_python() {
        assert_eq!(dividir(b"a::b::", b"::"), vec![&b"a"[..], &b"b"[..], &b""[..]]);
        assert_eq!(dividir(b"", b"::"), vec![&b""[..]]);
    }

    #[test]
    fn rebanada_con_negativos() {
        assert_eq!(rebanada_py(10, -1, 11), (9, 10));
        assert_eq!(rebanada_py(100, -1, 11), (99, 99));
        assert_eq!(rebanada_py(10, 2, 5), (2, 5));
        assert_eq!(rebanada_py(3, 2, 50), (2, 3));
    }

    #[test]
    fn normaliza_crlf_y_cr() {
        assert_eq!(normalizar_saltos(b"a\r\nb\rc\r\r\n"), b"a\nb\nc\n\n".to_vec());
    }

    #[test]
    fn primera_valida_gana_y_ausente_es_cero() {
        let d = b"<x:xmpmeta> drone-dji:GimbalYawDegree=\"abc\" drone-dji:GimbalYawDegree=\"-3.5\" \
                  drone-dji:GimbalYawDegree=\"7\" </x:xmpmeta>";
        let v = extraer_campos(d, &CAMPOS_GIMBAL);
        assert_eq!(v, vec!["-3.5".to_string(), "0".to_string()]);
    }

    #[test]
    fn sin_cierre_no_hay_bloque() {
        assert!(bloque_xmp(b"<x:xmpmeta> drone-dji:GimbalYawDegree=\"1\"").is_none());
        let tiff_corto = [b'I', b'I', b'*', 0, 8, 0, 0, 0];
        assert!(bloque_xmp(&tiff_corto).is_none());
    }

    #[test]
    fn tiff_truncado_no_panica() {
        let mut b = vec![b'I', b'I', b'*', 0, 8, 0, 0, 0, 0xFF, 0xFF];
        b.resize(BYTES_CABECERA, 0);
        for n in [0usize, 1, 4, 9, 10, BYTES_CABECERA] {
            let _ = bloque_xmp(&b[..n]);
            let _ = tiff_exif_en_buffer(&b[..n]);
            let _ = tiff_xmp_desde_buffer(&b[..n]);
        }
    }
}
