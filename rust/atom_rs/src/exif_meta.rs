//! EXIF de la cabecera para el índice (fase `indice`, T2): `parsear_cabecera`.
//!
//! Parser propio, solo lectura, de JPEG (marcadores) + TIFF embebido en APP1 `Exif`
//! (IFD0, Exif IFD 0x8769, GPS IFD 0x8825). Las referencias de VALORES son las
//! librerías que usa hoy `indice.py` (versiones fijadas en packaging/requirements):
//!
//! - Pillow 12.3.0 (`_timestamp_desde_buffer` = `_getexif()[36867]`,
//!   `_gps_desde_buffer` = `getexif().get_ifd(34853)`, `_dimensiones_desde_buffer` =
//!   `Image.open(buf).size`): `ImageFileDirectory_v2.load` (última entrada válida gana,
//!   tipos sin cargador se ignoran, recuento 0 se ignora, un valor fuera de datos corta
//!   la lectura del IFD), ASCII = quitar UN NUL final + latin-1, racional `n/d` con
//!   `d == 0` -> NaN, y el recorrido de marcadores de `JpegImageFile._open`.
//! - ExifRead 3.5.1 (`_modelo_desde_buffer`/`_make_desde_buffer` = `str(tags["Image
//!   Model"|"Image Make"])`): ASCII = corte en el primer NUL + UTF-8 estricto.
//!
//! Regla de oro: ante cualquier duda (estructura rara, tipo inesperado, valor fuera
//! del segmento, error que Pillow/ExifRead lanzarían) el campo sale `None` o
//! `pil_estado = "incierto"`, y Python ejecuta su réplica original para ese campo
//! (contador `rust_parse_fallbacks`). Nunca se inventa un valor. Sin panics: todo
//! acceso a bytes de entrada va por `get`.

use std::collections::HashMap;

use pyo3::prelude::*;

use crate::xmp;

/// `PIL.Image.MAX_IMAGE_PIXELS` por defecto: por encima Pillow avisa (y a 2x lanza).
const MAX_IMAGE_PIXELS: u64 = 89_478_485;

/// Unidad (bytes) de los tipos con cargador en `ImageFileDirectory_v2._load_dispatch`
/// de Pillow 12.3.0; `None` = tipo ignorado por Pillow.
fn unidad_pil(tipo: u16) -> Option<u64> {
    match tipo {
        1 | 2 | 6 | 7 => Some(1),
        3 | 8 => Some(2),
        4 | 9 | 11 | 13 => Some(4),
        5 | 10 | 12 | 16 => Some(8),
        _ => None,
    }
}

/// Unidad de `exifread.tags.fields.FIELD_DEFINITIONS` (tipos 1..=13); `None` =
/// tipo desconocido o `PROPRIETARY` (ExifRead salta la entrada).
fn unidad_exifread(tipo: u16) -> Option<u64> {
    match tipo {
        1 | 2 | 6 | 7 => Some(1),
        3 | 8 => Some(2),
        4 | 9 | 11 | 13 => Some(4),
        5 | 10 | 12 => Some(8),
        _ => None,
    }
}

fn be16(b: &[u8], p: usize) -> Option<u16> {
    let a: [u8; 2] = b.get(p..p.checked_add(2)?)?.try_into().ok()?;
    Some(u16::from_be_bytes(a))
}

#[derive(Clone, Copy, Debug)]
struct Entrada {
    tipo: u16,
    count: u32,
    /// Posición del valor dentro de los datos TIFF.
    pos: usize,
    /// Bytes del valor (`count * unidad`).
    tam: usize,
}

/// TIFF clásico (no BigTIFF) con cabecera `II*\0` / `MM\0*`.
struct Tiff<'a> {
    d: &'a [u8],
    le: bool,
}

impl<'a> Tiff<'a> {
    fn nuevo(d: &'a [u8]) -> Option<Self> {
        let le = match d.get(..4)? {
            b"II*\0" => true,
            b"MM\0*" => false,
            _ => return None,
        };
        if d.len() < 8 {
            return None;
        }
        Some(Tiff { d, le })
    }

    fn u16_en(&self, p: usize) -> Option<u16> {
        let a: [u8; 2] = self.d.get(p..p.checked_add(2)?)?.try_into().ok()?;
        Some(if self.le { u16::from_le_bytes(a) } else { u16::from_be_bytes(a) })
    }

    fn u32_en(&self, p: usize) -> Option<u32> {
        let a: [u8; 4] = self.d.get(p..p.checked_add(4)?)?.try_into().ok()?;
        Some(if self.le { u32::from_le_bytes(a) } else { u32::from_be_bytes(a) })
    }

    fn primer_ifd(&self) -> Option<u32> {
        self.u32_en(4)
    }

    /// Un IFD tal como lo deja `ImageFileDirectory_v2.load` de Pillow. `None` si
    /// Pillow cortaría la lectura a medias (entradas o valores fuera de los datos):
    /// no se modela la lectura parcial, el campo cae a Python.
    fn ifd_pil(&self, off: u32) -> Option<HashMap<u16, Entrada>> {
        let off = off as usize;
        let n = self.u16_en(off)? as usize;
        let mut m = HashMap::new();
        for i in 0..n {
            let e = off.checked_add(2 + 12 * i)?;
            if e.checked_add(12)? > self.d.len() {
                return None;
            }
            let tag = self.u16_en(e)?;
            let tipo = self.u16_en(e + 2)?;
            let count = self.u32_en(e + 4)?;
            let Some(unidad) = unidad_pil(tipo) else {
                continue; // tipo sin cargador: Pillow lo ignora
            };
            let tam = u64::from(count) * unidad;
            let pos = if tam > 4 {
                let o = u64::from(self.u32_en(e + 8)?);
                if o + tam > self.d.len() as u64 {
                    return None; // Pillow: "Truncated File Read" -> IFD a medias
                }
                o as usize
            } else {
                e + 8
            };
            if tam == 0 {
                continue; // `if not data: continue`
            }
            m.insert(tag, Entrada { tipo, count, pos, tam: tam as usize });
        }
        Some(m)
    }

    fn datos(&self, e: &Entrada) -> &'a [u8] {
        e.pos
            .checked_add(e.tam)
            .and_then(|fin| self.d.get(e.pos..fin))
            .unwrap_or(&[])
    }

    /// `load_string` de Pillow: quita UN NUL final y decodifica latin-1.
    fn ascii_pil(&self, e: &Entrada) -> Option<String> {
        if e.tipo != 2 {
            return None;
        }
        let mut b = self.datos(e);
        if let Some((&0, resto)) = b.split_last() {
            b = resto;
        }
        Some(xmp::latin1(b))
    }

    /// Puntero a sub-IFD que Pillow puede usar como `fp.seek(entero)`.
    fn puntero_ifd(&self, e: &Entrada) -> Option<u32> {
        if e.count != 1 {
            return None;
        }
        match e.tipo {
            3 => self.u16_en(e.pos).map(u32::from),
            4 | 13 => self.u32_en(e.pos),
            _ => None,
        }
    }

    /// Tres `RATIONAL` como `float(IFDRational)`: `n / d` (correctamente redondeado,
    /// igual que la división entera exacta de Python) y NaN si `d == 0`.
    fn racionales3(&self, e: &Entrada) -> Option<[f64; 3]> {
        if e.tipo != 5 || e.count != 3 {
            return None;
        }
        let mut v = [0f64; 3];
        for (k, x) in v.iter_mut().enumerate() {
            let n = self.u32_en(e.pos + 8 * k)?;
            let d = self.u32_en(e.pos + 8 * k + 4)?;
            *x = if d == 0 { f64::NAN } else { f64::from(n) / f64::from(d) };
        }
        Some(v)
    }
}

/// Datos de `info["exif"]` tras `while data.startswith(b"Exif\0\0"): data = data[6:]`.
fn quitar_prefijo_exif(mut s: &[u8]) -> &[u8] {
    while let Some(resto) = s.strip_prefix(b"Exif\0\0") {
        s = resto;
    }
    s
}

/// `_getexif()[36867]` (Exif IFD manda sobre IFD0, como `_get_merged_dict`).
fn timestamp_pil(t: &Tiff<'_>) -> Option<String> {
    let ifd0 = t.ifd_pil(t.primer_ifd()?)?;
    let mut ganador = ifd0.get(&36867).copied();
    if let Some(p) = ifd0.get(&0x8769) {
        let exif_ifd = t.ifd_pil(t.puntero_ifd(p)?)?;
        if let Some(e) = exif_ifd.get(&36867) {
            ganador = Some(*e);
        }
    }
    t.ascii_pil(&ganador?)
}

/// Ausencia CIERTA de fecha: IFD0 (y el Exif IFD si hay puntero válido) leídos enteros
/// y sin el tag 36867. Cualquier duda (IFD a medias, puntero raro) -> false.
fn timestamp_ausente(t: &Tiff<'_>) -> bool {
    let Some(ifd0) = t.primer_ifd().and_then(|o| t.ifd_pil(o)) else {
        return false;
    };
    if ifd0.contains_key(&36867) {
        return false;
    }
    match ifd0.get(&0x8769) {
        None => true,
        Some(p) => match t.puntero_ifd(p).and_then(|o| t.ifd_pil(o)) {
            Some(exif_ifd) => !exif_ifd.contains_key(&36867),
            None => false,
        },
    }
}

/// Ausencia CIERTA de GPS: sin IFD0 con entradas, sin puntero GPS, o GPS IFD leído
/// entero sin latitud/longitud. Cualquier duda -> false.
fn gps_ausente(t: &Tiff<'_>) -> bool {
    let Some(ifd0) = t.primer_ifd().and_then(|o| t.ifd_pil(o)) else {
        return false;
    };
    if ifd0.is_empty() {
        return true;
    }
    match ifd0.get(&0x8825) {
        None => true,
        Some(p) => match t.puntero_ifd(p).and_then(|o| t.ifd_pil(o)) {
            Some(g) => !g.contains_key(&2) || !g.contains_key(&4),
            None => false,
        },
    }
}

/// `_gps_desde_buffer`: misma aritmética y mismo orden de operaciones.
fn gps_pil(t: &Tiff<'_>) -> Option<(f64, f64)> {
    let ifd0 = t.ifd_pil(t.primer_ifd()?)?;
    if ifd0.is_empty() {
        return None;
    }
    let g = t.ifd_pil(t.puntero_ifd(ifd0.get(&0x8825)?)?)?;
    let lat_ref = t.ascii_pil(g.get(&1)?)?;
    let lon_ref = t.ascii_pil(g.get(&3)?)?;
    let la = t.racionales3(g.get(&2)?)?;
    let lo = t.racionales3(g.get(&4)?)?;
    let lat = match lat_ref.as_str() {
        "N" => la[0] + la[1] / 60.0 + la[2] / 3600.0,
        "S" => (la[0] + la[1] / 60.0 + la[2] / 3600.0) * -1.0,
        _ => return None,
    };
    let lon = match lon_ref.as_str() {
        "E" => lo[0] + lo[1] / 60.0 + lo[2] / 3600.0,
        "W" => (lo[0] + lo[1] / 60.0 + lo[2] / 3600.0) * -1.0,
        _ => return None,
    };
    Some((lat, lon))
}

/// Resultado de simular `PIL.Image.open(io.BytesIO(buf))` sobre el buffer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EstadoPil {
    /// Abriría: tamaño `(ancho, alto)` del último SOF antes del SOS.
    Abre { ancho: u16, alto: u16 },
    /// Marcadores bien formados pero el buffer se acaba antes del SOS: Pillow falla
    /// sobre ESTE buffer. Fecha/GPS/dimensiones salen `None` (decide Python).
    Corto,
    /// Algo que no se modela (o que Pillow rechazaría): decide Python.
    Incierto,
}

impl EstadoPil {
    fn nombre(&self) -> &'static str {
        match self {
            EstadoPil::Abre { .. } => "abre",
            EstadoPil::Corto => "corto",
            EstadoPil::Incierto => "incierto",
        }
    }
}

enum Paso {
    Sos,
    Corto,
    Incierto,
}

#[derive(Default)]
struct Recorrido<'a> {
    tam: Option<(u16, u16)>,
    exif: Option<&'a [u8]>,
    mpf: Option<&'a [u8]>,
    dpi_jfif: bool,
    icc_n: usize,
    icc_corto: bool,
}

/// Bucle de `JpegImageFile._open` (sin tolerar basura entre marcadores).
fn caminar<'a>(buf: &'a [u8], r: &mut Recorrido<'a>) -> Paso {
    if buf.get(..3) != Some(&[0xFF, 0xD8, 0xFF][..]) {
        return Paso::Incierto;
    }
    let mut pos: usize = 2;
    loop {
        let Some(&ff) = buf.get(pos) else {
            return Paso::Corto;
        };
        if ff != 0xFF {
            return Paso::Incierto; // Pillow salta basura; no se modela
        }
        let Some(&m) = buf.get(pos + 1) else {
            return Paso::Corto;
        };
        match m {
            // Marcadores sin manejador en `MARKER` (SOI, EOI, RSTn, JPG, JPGn).
            0xD0..=0xD9 | 0xC8 | 0xF0..=0xFD => {
                pos += 2;
                continue;
            }
            0xC0..=0xC7 | 0xC9..=0xCF | 0xDA..=0xDF | 0xE0..=0xEF | 0xFE => {}
            _ => return Paso::Incierto, // relleno 0xFF/0x00 o "no marker found"
        }
        let Some(l) = be16(buf, pos + 2) else {
            return Paso::Corto;
        };
        let l = usize::from(l);
        if l < 2 {
            return Paso::Incierto;
        }
        let (ini, fin) = (pos + 4, pos + 2 + l);
        let Some(s) = buf.get(ini..fin) else {
            return Paso::Corto; // `_safe_read` corto
        };
        match m {
            // SOF (y DHP, que Pillow trata igual).
            0xC0..=0xC3 | 0xC5..=0xC7 | 0xC9..=0xCB | 0xCD..=0xCF | 0xDE => {
                if s.len() < 6 || !(s.len() - 6).is_multiple_of(3) {
                    return Paso::Incierto;
                }
                let (Some(alto), Some(ancho)) = (be16(s, 1), be16(s, 3)) else {
                    return Paso::Incierto;
                };
                if s.first() != Some(&8) || !matches!(s.get(5), Some(1 | 3 | 4)) {
                    return Paso::Incierto; // SyntaxError en Pillow
                }
                if r.icc_n > 0 {
                    if r.icc_corto {
                        return Paso::Incierto;
                    }
                    r.icc_n = 0;
                }
                r.tam = Some((ancho, alto));
            }
            0xDB => {
                let mut q = s;
                while let Some(&v) = q.first() {
                    let precision = if v / 16 == 0 { 1 } else { 2 };
                    let ql = 1 + precision * 64;
                    if q.len() < ql {
                        return Paso::Incierto; // "bad quantization table marker"
                    }
                    q = q.get(ql..).unwrap_or(&[]);
                }
            }
            0xE0 => {
                if s.starts_with(b"JFIF") {
                    if s.len() < 7 {
                        return Paso::Incierto;
                    }
                    if s.len() >= 12 && matches!(s.get(7), Some(1 | 2)) {
                        r.dpi_jfif = true;
                    }
                }
            }
            0xE1 => {
                if s.starts_with(b"Exif\0\0") && r.exif.is_none() {
                    r.exif = Some(s);
                }
            }
            0xE2 => {
                if s.starts_with(b"ICC_PROFILE\0") {
                    r.icc_n += 1;
                    if s.len() < 14 {
                        r.icc_corto = true;
                    }
                } else if s.starts_with(b"MPF\0") {
                    r.mpf = s.get(4..);
                }
            }
            0xED => {
                if s.starts_with(b"Photoshop 3.0\0") {
                    return Paso::Incierto;
                }
            }
            0xEE => {
                if s.starts_with(b"Adobe") && s.len() < 7 {
                    return Paso::Incierto;
                }
            }
            0xDA => return Paso::Sos,
            _ => {}
        }
        pos = fin;
    }
}

/// Prefijos que `TiffImagePlugin._accept` admite como TIFF "inválido" (`MM*\0`,
/// `II\0*`) con 8+ bytes: Pillow sigue leyendo con un orden de bytes que aquí no se
/// modela. Las cabeceras no TIFF, cortas o BigTIFF (8 bytes de cabecera ->
/// `struct.error`) acaban en excepciones que Pillow captura.
fn cabecera_tiff_rara(d: &[u8]) -> bool {
    d.len() >= 8 && matches!(d.get(..4), Some(b"MM*\0") | Some(b"II\0*"))
}

#[derive(PartialEq, Eq)]
enum Mpf {
    NoFatal,
    Fatal,
    Incierto,
}

/// ¿`jpeg_factory` reventaría por el índice MPF? Solo el `struct.error` de
/// `_getmp` (entradas MP más cortas que `NumberOfImages * 16`) se escapa de
/// `jpeg_factory` y tumba `Image.open`; el resto de errores dejan un JPEG normal.
fn mpf_estado(d: &[u8]) -> Mpf {
    let Some(t) = Tiff::nuevo(d) else {
        // Cabecera no TIFF, corta o BigTIFF: SyntaxError/struct.error capturados.
        return if cabecera_tiff_rara(d) { Mpf::Incierto } else { Mpf::NoFatal };
    };
    let Some(ifd) = t.primer_ifd().and_then(|o| t.ifd_pil(o)) else {
        return Mpf::Incierto;
    };
    let Some(q) = ifd.get(&0xB001) else {
        return Mpf::NoFatal;
    };
    let quant: i128 = match q.tipo {
        3 => t.u16_en(q.pos).map(i128::from),
        4 | 13 => t.u32_en(q.pos).map(i128::from),
        6 => t.d.get(q.pos).map(|&b| i128::from(b as i8)),
        8 => t.u16_en(q.pos).map(|v| i128::from(v as i16)),
        9 => t.u32_en(q.pos).map(|v| i128::from(v as i32)),
        16 => t
            .d
            .get(q.pos..q.pos + 8)
            .and_then(|b| <[u8; 8]>::try_from(b).ok())
            .map(|a| i128::from(if t.le { u64::from_le_bytes(a) } else { u64::from_be_bytes(a) })),
        _ => return Mpf::NoFatal, // range(no-int) -> TypeError, capturado
    }
    .unwrap_or(-1);
    let raw = match ifd.get(&0xB002) {
        None => return Mpf::NoFatal,
        Some(e) if e.tipo == 1 || e.tipo == 7 => t.datos(e),
        Some(_) => return Mpf::NoFatal, // unpack_from(no-bytes) -> TypeError
    };
    let le = !d.starts_with(b"MM\0*");
    let mut k: i128 = 0;
    while k < quant {
        let o = (k as usize).saturating_mul(16);
        let Some(ent) = o.checked_add(16).and_then(|f| raw.get(o..f)) else {
            return Mpf::Fatal; // struct.error
        };
        let Some(a) = ent.get(..4).and_then(|b| <[u8; 4]>::try_from(b).ok()) else {
            return Mpf::Fatal;
        };
        let attr = if le { u32::from_le_bytes(a) } else { u32::from_be_bytes(a) };
        if (attr >> 24) & 7 != 0 {
            return Mpf::NoFatal; // "unsupported picture format": SyntaxError
        }
        k += 1;
    }
    Mpf::NoFatal
}

/// ¿`_read_dpi_from_exif` terminaría sin excepción no capturada?
fn dpi_seguro(r: &Recorrido<'_>) -> bool {
    if r.dpi_jfif {
        return true;
    }
    let Some(seg) = r.exif else {
        return true;
    };
    let datos = quitar_prefijo_exif(seg);
    if datos.is_empty() {
        return true;
    }
    let Some(t) = Tiff::nuevo(datos) else {
        // Cabecera no TIFF, corta o BigTIFF: SyntaxError/struct.error, capturados.
        return !cabecera_tiff_rara(datos);
    };
    let Some(ifd0) = t.primer_ifd().and_then(|o| t.ifd_pil(o)) else {
        return false;
    };
    if ifd0.contains_key(&0x0128) {
        if let Some(x) = ifd0.get(&0x011A) {
            // BYTE/ASCII/UNDEFINED de 1 elemento -> `x_resolution[1]` IndexError.
            if matches!(x.tipo, 1 | 2 | 7) {
                return false;
            }
        }
    }
    true
}

fn estado_pil(paso: Paso, r: &Recorrido<'_>) -> EstadoPil {
    let comunes_ok = || {
        !r.icc_corto
            && r.mpf.is_none_or(|d| mpf_estado(d) == Mpf::NoFatal)
            && dpi_seguro(r)
    };
    match paso {
        Paso::Incierto => EstadoPil::Incierto,
        Paso::Corto => {
            if comunes_ok() {
                EstadoPil::Corto
            } else {
                EstadoPil::Incierto
            }
        }
        Paso::Sos => {
            let Some((ancho, alto)) = r.tam else {
                return EstadoPil::Incierto;
            };
            if ancho == 0
                || alto == 0
                || u64::from(ancho) * u64::from(alto) > MAX_IMAGE_PIXELS
                || !comunes_ok()
            {
                return EstadoPil::Incierto;
            }
            EstadoPil::Abre { ancho, alto }
        }
    }
}

/// `str(tags["Image Model"])` / `str(tags["Image Make"])` de ExifRead, solo en la
/// disposición DJI (APP1 `Exif` justo tras el SOI: ExifRead y Pillow leen el mismo
/// TIFF en `buf[12..]`). `None` = no se fía (Python ejecuta su réplica).
///
/// Devuelve `(modelo, make, modelo_ausente, make_ausente)`: `*_ausente` solo es true si
/// el IFD0 se leyó entero y NO trae el tag (ausencia cierta, no "no se modela").
fn modelo_make_exifread(buf: &[u8]) -> (Option<String>, Option<String>, bool, bool) {
    let nada = (None, None, false, false);
    if buf.get(..4) != Some(&[0xFF, 0xD8, 0xFF, 0xE1][..]) || buf.get(6..12) != Some(&b"Exif\0\0"[..]) {
        return nada;
    }
    let Some(l) = be16(buf, 4) else {
        return nada;
    };
    let Some(datos) = buf.get(12..4 + usize::from(l)) else {
        return nada;
    };
    let Some(t) = Tiff::nuevo(datos) else {
        return nada;
    };
    let Some(off) = t.primer_ifd() else {
        return nada;
    };
    let off = off as usize;
    let Some(n) = t.u16_en(off) else {
        return nada;
    };
    let n = usize::from(n);
    let fin_entradas = off + 2 + 12 * n;
    if fin_entradas > datos.len() {
        return nada;
    }
    // `None` = sin tag; `Some(None)` = tag que no se modela; `Some(Some(v))` = valor.
    let mut modelo: Option<Option<String>> = None;
    let mut make: Option<Option<String>> = None;
    for i in 0..n {
        let e = off + 2 + 12 * i;
        let (Some(tag), Some(tipo), Some(count)) = (t.u16_en(e), t.u16_en(e + 2), t.u32_en(e + 4))
        else {
            return nada;
        };
        // `exifread/__init__.py`: `exif_off.values[0]` lanza IndexError / TypeError si
        // ExifOffset no es un SHORT/LONG de recuento 1. No se modela: decide Python.
        if tag == 0x8769 && !(matches!(tipo, 3 | 4) && count == 1) {
            return nada;
        }
        if tag != 0x010F && tag != 0x0110 {
            continue;
        }
        let Some(unidad) = unidad_exifread(tipo) else {
            continue; // ExifRead salta tipos desconocidos (no pisa el anterior)
        };
        let valor = if tipo != 2 {
            None
        } else if count == 0 {
            Some(String::new())
        } else {
            let tam = u64::from(count) * unidad;
            let pos = if tam > 4 { t.u32_en(e + 8).map(|p| p as usize) } else { Some(e + 8) };
            pos.and_then(|p| datos.get(p..p.checked_add(count as usize)?))
                .and_then(|b| {
                    let corte = b.iter().position(|&c| c == 0).unwrap_or(b.len());
                    String::from_utf8(b.get(..corte)?.to_vec()).ok()
                })
        };
        if tag == 0x0110 {
            modelo = Some(valor);
        } else {
            make = Some(valor);
        }
    }
    // Miniatura TIFF sin comprimir en IFD1: `extract_tiff_thumbnail` puede lanzar.
    let Some(sig) = t.u32_en(fin_entradas) else {
        return nada;
    };
    if sig != 0 && sig as usize != off {
        let ifd1 = sig as usize;
        let Some(n1) = t.u16_en(ifd1) else {
            return nada;
        };
        if ifd1 + 2 + 12 * usize::from(n1) > datos.len() {
            return nada;
        }
        for i in 0..usize::from(n1) {
            let e = ifd1 + 2 + 12 * i;
            let tag = t.u16_en(e);
            if tag == Some(0x0103) && (t.u16_en(e + 2) != Some(3) || t.u16_en(e + 8) == Some(1)) {
                return nada;
            }
            // `extract_jpeg_thumbnail` hace `values[0]`: lista vacía (recuento 0 o
            // >= 1000, que `_process_field` no lee) -> IndexError.
            // `self.offset + values[0]` lanza TypeError si el tipo no es entero
            // (ASCII, RATIONAL, FLOAT...): solo SHORT/LONG son seguros.
            if matches!(tag, Some(0x0201) | Some(0x0202))
                && (!matches!(t.u16_en(e + 2), Some(3) | Some(4))
                    || t.u32_en(e + 4).is_none_or(|c| c == 0 || c >= 1000))
            {
                return nada;
            }
        }
    }
    let (modelo_ausente, make_ausente) = (modelo.is_none(), make.is_none());
    (modelo.flatten(), make.flatten(), modelo_ausente, make_ausente)
}

/// Resultado de `parsear_cabecera` (getters de solo lectura). `None` en un campo =
/// Rust no lo da: Python ejecuta su réplica original para ESE campo.
#[pyclass(frozen, get_all, module = "atom_rs")]
#[derive(Debug, Clone, PartialEq)]
pub struct CabeceraParseada {
    /// Tag 36867 tal cual lo da Pillow (`"%Y:%m:%d %H:%M:%S"` en un DJI); el
    /// `split(' ')` + `strptime` se hacen en Python.
    pub timestamp_iso: Option<String>,
    /// `str(tags["Image Model"])` de ExifRead.
    pub modelo: Option<String>,
    /// `str(tags["Image Make"])` de ExifRead, SIN el `strip` (lo hace Python).
    pub make: Option<String>,
    pub gps: Option<(f64, f64)>,
    pub ancho: Option<u32>,
    pub alto: Option<u32>,
    /// `"abre"` | `"corto"` | `"incierto"`: `Image.open` sobre ESTE buffer.
    pub pil_estado: String,
    pub gimbal_yaw: Option<String>,
    pub gimbal_pitch: Option<String>,
    /// AbsoluteAltitude, RelativeAltitude, GimbalRollDegree, FlightRollDegree,
    /// FlightYawDegree, FlightPitchDegree, CamReverse, GimbalReverse, RtkFlag.
    pub xmp: Option<Vec<String>>,
    /// El bloque XMP sale del buffer (si no, Python relee con `leer_bloque_xmp`).
    pub xmp_presente: bool,
    pub flight_yaw_en_xmp: bool,
    /// Campos (`"timestamp"`, `"gps"`, `"modelo"`, `"make"`) que Rust da como `None`
    /// porque el tag NO existe con certeza (no es un fallback: Python reproduce el
    /// mismo `None`). Un `None` que no está aquí = "no se modela" -> cuenta como fallback.
    pub ausentes: Vec<String>,
}

#[pymethods]
impl CabeceraParseada {
    fn __repr__(&self) -> String {
        format!("{self:?}")
    }
}

/// Núcleo puro (sin Python): también lo usan los tests de `cargo test`.
pub fn analizar(buf: &[u8]) -> CabeceraParseada {
    let mut r = Recorrido::default();
    let paso = caminar(buf, &mut r);
    let estado = estado_pil(paso, &r);
    // Fecha/GPS solo si Pillow abriría ESTE buffer: en `corto` Python relee el
    // fichero entero y puede fallar (marcadores rotos tras 256 KiB) -> no se adivina.
    let mut ausentes: Vec<String> = Vec::new();
    let (timestamp_iso, gps) = match (estado, r.exif.and_then(|s| Tiff::nuevo(quitar_prefijo_exif(s)))) {
        (EstadoPil::Abre { .. }, Some(t)) => {
            let (ts, g) = (timestamp_pil(&t), gps_pil(&t));
            if ts.is_none() && timestamp_ausente(&t) {
                ausentes.push("timestamp".to_string());
            }
            if g.is_none() && gps_ausente(&t) {
                ausentes.push("gps".to_string());
            }
            (ts, g)
        }
        _ => (None, None),
    };
    let (ancho, alto) = match estado {
        EstadoPil::Abre { ancho, alto } => (Some(u32::from(ancho)), Some(u32::from(alto))),
        _ => (None, None),
    };
    let (modelo, make, modelo_ausente, make_ausente) = modelo_make_exifread(buf);
    if modelo_ausente {
        ausentes.push("modelo".to_string());
    }
    if make_ausente {
        ausentes.push("make".to_string());
    }
    let x = xmp::analizar(buf);
    CabeceraParseada {
        timestamp_iso,
        modelo,
        make,
        gps,
        ancho,
        alto,
        pil_estado: estado.nombre().to_string(),
        xmp_presente: x.is_some(),
        flight_yaw_en_xmp: x.as_ref().is_some_and(|d| d.flight_yaw_en_xmp),
        ausentes,
        gimbal_yaw: x.as_ref().map(|d| d.gimbal_yaw.clone()),
        gimbal_pitch: x.as_ref().map(|d| d.gimbal_pitch.clone()),
        xmp: x.map(|d| d.xmp),
    }
}

/// Parsea la cabecera (los primeros `_BYTES_CABECERA` bytes, o el fichero entero si
/// es más corto) sin el GIL. Nunca lanza por datos de entrada: lo que no entiende
/// sale `None`.
#[pyfunction]
pub fn parsear_cabecera(py: Python<'_>, buf: &[u8]) -> PyResult<CabeceraParseada> {
    Ok(py.allow_threads(|| analizar(buf)))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// JPEG DJI mínimo: SOI, APP1 Exif (IFD0 Make/Model + punteros Exif/GPS), DQT,
    /// SOF0 64x48, SOS.
    fn jpeg_minimo() -> Vec<u8> {
        let mut t: Vec<u8> = b"II*\0\x08\0\0\0".to_vec();
        // IFD0 en 8: 4 entradas.
        let ifd0_n = 4u16;
        let ifd0_fin = 8 + 2 + 12 * ifd0_n as usize + 4;
        let make_off = ifd0_fin;
        let model_off = make_off + 4;
        let exif_off = model_off + 8;
        let exif_fin = exif_off + 2 + 12 + 4;
        let fecha_off = exif_fin;
        let gps_off = fecha_off + 20;
        let gps_fin = gps_off + 2 + 12 * 4 + 4;
        let lat_off = gps_fin;
        let lon_off = lat_off + 24;
        let ent = |t: &mut Vec<u8>, tag: u16, tipo: u16, count: u32, val: u32| {
            t.extend_from_slice(&tag.to_le_bytes());
            t.extend_from_slice(&tipo.to_le_bytes());
            t.extend_from_slice(&count.to_le_bytes());
            t.extend_from_slice(&val.to_le_bytes());
        };
        t.extend_from_slice(&ifd0_n.to_le_bytes());
        ent(&mut t, 0x010F, 2, 4, u32::from_le_bytes(*b"DJI\0"));
        ent(&mut t, 0x0110, 2, 7, model_off as u32);
        ent(&mut t, 0x8769, 4, 1, exif_off as u32);
        ent(&mut t, 0x8825, 4, 1, gps_off as u32);
        t.extend_from_slice(&0u32.to_le_bytes());
        t.extend_from_slice(b"\0\0\0\0"); // relleno make_off (no usado)
        t.extend_from_slice(b"FC6310\0\0");
        t.extend_from_slice(&1u16.to_le_bytes());
        ent(&mut t, 36867, 2, 20, fecha_off as u32);
        t.extend_from_slice(&0u32.to_le_bytes());
        t.extend_from_slice(b"2026:08:20 10:11:12\0");
        t.extend_from_slice(&4u16.to_le_bytes());
        ent(&mut t, 1, 2, 2, u32::from_le_bytes(*b"N\0\0\0"));
        ent(&mut t, 2, 5, 3, lat_off as u32);
        ent(&mut t, 3, 2, 2, u32::from_le_bytes(*b"W\0\0\0"));
        ent(&mut t, 4, 5, 3, lon_off as u32);
        t.extend_from_slice(&0u32.to_le_bytes());
        for (n, d) in [(40u32, 1u32), (25, 1), (1234, 100), (3, 1), (41, 1), (55, 10)] {
            t.extend_from_slice(&n.to_le_bytes());
            t.extend_from_slice(&d.to_le_bytes());
        }
        let mut app1 = b"Exif\0\0".to_vec();
        app1.extend_from_slice(&t);
        let mut j = vec![0xFF, 0xD8, 0xFF, 0xE1];
        j.extend_from_slice(&((app1.len() + 2) as u16).to_be_bytes());
        j.extend_from_slice(&app1);
        let mut dqt = vec![0u8];
        dqt.extend_from_slice(&[1u8; 64]);
        j.extend_from_slice(&[0xFF, 0xDB]);
        j.extend_from_slice(&((dqt.len() + 2) as u16).to_be_bytes());
        j.extend_from_slice(&dqt);
        j.extend_from_slice(&[0xFF, 0xC0, 0, 17, 8, 0, 48, 0, 64, 3, 1, 0x11, 0, 2, 0x11, 0, 3, 0x11, 0]);
        j.extend_from_slice(&[0xFF, 0xDA, 0, 2]);
        j.extend_from_slice(&[0xFF, 0xD9]);
        j
    }

    #[test]
    fn jpeg_minimo_campos() {
        let c = analizar(&jpeg_minimo());
        assert_eq!(c.pil_estado, "abre");
        assert_eq!((c.ancho, c.alto), (Some(64), Some(48)));
        assert_eq!(c.timestamp_iso.as_deref(), Some("2026:08:20 10:11:12"));
        assert_eq!(c.modelo.as_deref(), Some("FC6310"));
        assert_eq!(c.make.as_deref(), Some("DJI"));
        let (lat, lon) = c.gps.expect("gps");
        assert_eq!(lat, 40.0 + 25.0 / 60.0 + 12.34 / 3600.0);
        assert_eq!(lon, (3.0 + 41.0 / 60.0 + 5.5 / 3600.0) * -1.0);
        assert!(!c.xmp_presente);
    }

    #[test]
    fn exif_offset_malformado_decide_python() {
        // ExifOffset (0x8769, LONG) con recuento 0: ExifRead lanzaría IndexError.
        let mut j = jpeg_minimo();
        let pat = [0x69u8, 0x87, 4, 0, 1, 0, 0, 0];
        let p = j.windows(pat.len()).position(|w| w == pat).expect("entrada 0x8769");
        j[p + 4] = 0;
        let c = analizar(&j);
        assert!(c.modelo.is_none() && c.make.is_none());
        assert!(!c.ausentes.iter().any(|a| a == "modelo" || a == "make"));
    }

    #[test]
    fn ausencias_ciertas_no_son_fallback() {
        let c = analizar(&jpeg_minimo());
        assert!(c.ausentes.is_empty());
        // Sin Model (0x0110 pasa a un tag desconocido 0x0111) y sin GPS (0x8825 -> 0x8826).
        let mut j = jpeg_minimo();
        let p = j.windows(2).position(|w| w == [0x10, 0x01]).expect("0x0110");
        j[p] = 0x11;
        let q = j.windows(2).position(|w| w == [0x25, 0x88]).expect("0x8825");
        j[q] = 0x26;
        let c = analizar(&j);
        assert!(c.modelo.is_none() && c.gps.is_none());
        assert!(c.ausentes.iter().any(|a| a == "modelo"));
        assert!(c.ausentes.iter().any(|a| a == "gps"));
        assert!(!c.ausentes.iter().any(|a| a == "make" || a == "timestamp"));
    }

    #[test]
    fn truncados_no_panican_ni_inventan() {
        let j = jpeg_minimo();
        for n in 0..=j.len() {
            let c = analizar(&j[..n]);
            if n < j.len() - 2 {
                assert_ne!(c.pil_estado, "abre", "n={n}");
                assert!(c.ancho.is_none());
            }
        }
    }

    #[test]
    fn mpf_corto_es_fatal() {
        // TIFF LE con B001 = 2 imágenes y B002 de solo 16 bytes.
        let mut d: Vec<u8> = b"II*\0\x08\0\0\0".to_vec();
        d.extend_from_slice(&2u16.to_le_bytes());
        d.extend_from_slice(&0xB001u16.to_le_bytes());
        d.extend_from_slice(&4u16.to_le_bytes());
        d.extend_from_slice(&1u32.to_le_bytes());
        d.extend_from_slice(&2u32.to_le_bytes());
        d.extend_from_slice(&0xB002u16.to_le_bytes());
        d.extend_from_slice(&7u16.to_le_bytes());
        d.extend_from_slice(&16u32.to_le_bytes());
        d.extend_from_slice(&38u32.to_le_bytes());
        d.extend_from_slice(&0u32.to_le_bytes());
        d.extend_from_slice(&[0u8; 16]);
        assert!(mpf_estado(&d) == Mpf::Fatal);
        let mut ok = d.clone();
        let b002_count = 8 + 2 + 12 + 4;
        ok[b002_count..b002_count + 4].copy_from_slice(&32u32.to_le_bytes());
        ok.extend_from_slice(&[0u8; 16]);
        assert!(mpf_estado(&ok) == Mpf::NoFatal);
    }
}
