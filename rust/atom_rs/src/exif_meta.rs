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

    /// Entero SHORT/LONG de recuento 1 (p. ej. ImageWidth/ImageLength de un TIFF).
    fn entero_uno(&self, e: &Entrada) -> Option<u32> {
        if e.count != 1 {
            return None;
        }
        match e.tipo {
            3 => self.u16_en(e.pos).map(u32::from),
            4 => self.u32_en(e.pos),
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

/// `(modelo, make, modelo_ausente, make_ausente, make_ilegible)`. `*_ausente` solo es
/// true si el IFD0 se leyó entero y NO trae el tag (ausencia cierta). `make_ilegible` =
/// ExifRead LANZARÍA al procesar el fichero (Python: `get_make` falla y la fila queda
/// con `make_ilegible=True`, modelo y make `None`).
type ModeloMake = (Option<String>, Option<String>, bool, bool, bool);

/// `str(tags["Image Model"])` / `str(tags["Image Make"])` de ExifRead sobre un JPEG
/// (`find_jpeg_exif` portado, incluido su comportamiento con ficheros truncados).
fn modelo_make_exifread(buf: &[u8]) -> ModeloMake {
    match exif_exifread_jpeg(buf) {
        ExifJpeg::Tiff(datos) => modelo_make_tiff(datos),
        ExifJpeg::Vacio => (None, None, false, false, false),
        ExifJpeg::Lanza => (None, None, false, false, true),
    }
}

/// Resultado de localizar el EXIF como `exifread.core.find_exif.determine_type`.
enum ExifJpeg<'a> {
    /// TIFF desde `base + 12` hasta el final del buffer (ExifRead lee el fichero, no el
    /// segmento APP1).
    Tiff(&'a [u8]),
    /// `ExifNotFound`/`InvalidExif`: ExifRead devuelve `{}` (sin error, sin tags).
    Vacio,
    /// Excepción que ExifRead NO captura (IndexError sobre un fichero corto/truncado).
    Lanza,
}

/// `data[a:b]` de Python (recorta en vez de fallar).
fn corte(d: &[u8], a: usize, b: usize) -> &[u8] {
    let a = a.min(d.len());
    let b = b.min(d.len());
    &d[a..b]
}

/// `exifread.core.jpeg.find_jpeg_exif` (+ `get_endian_str`) sobre un buffer que empieza
/// por SOI. Incluye los `logger.debug(...)` que evalúan `data[i]` y por eso lanzan
/// IndexError con ficheros cortos.
fn exif_exifread_jpeg(buf: &[u8]) -> ExifJpeg<'_> {
    if buf.get(..2) != Some(&[0xFF, 0xD8][..]) {
        return ExifJpeg::Vacio;
    }
    let n = buf.len();
    // `determine_type`: `data = fh.read(12)`.
    let mut data: Vec<u8> = buf[..n.min(12)].to_vec();
    let mut fpos = data.len();
    let mut base: usize = 2;
    // `_get_initial_base`: `ord_(data[2])`, `ord_(data[3])` en el debug inicial.
    if data.len() < 4 {
        return ExifJpeg::Lanza;
    }
    loop {
        if data.len() < 3 {
            return ExifJpeg::Lanza; // `ord_(data[2])` del `while` tras `b"\xff\x00" + read(10)`
        }
        let jfif = data[2] == 0xFF
            && matches!(data.get(6..10), Some(b"JFIF" | b"JFXX" | b"OLYM" | b"Phot"));
        if !jfif {
            break;
        }
        let length = usize::from(data[4]) * 256 + usize::from(data[5]);
        // `fh.read(length - 8)`; negativo = leer hasta el final.
        fpos = if length >= 8 { (fpos + length - 8).min(n) } else { n };
        let fin = (fpos + 10).min(n);
        let mut nuevo = vec![0xFF, 0x00];
        nuevo.extend_from_slice(&buf[fpos..fin]);
        fpos = fin;
        data = nuevo;
        base = if base > 2 { base + length + 2 } else { length + 4 };
    }
    // `fh.seek(0); data = fh.read(base + 4000)`.
    let data = &buf[..n.min(base.saturating_add(4000))];
    // `_get_base`.
    loop {
        let seg = corte(data, base, base + 2);
        if matches!(seg, [0xFF, 0xDB]) {
            break;
        }
        if data.len() <= base + 3 {
            // APPn conocidos: el `logger.debug` lanza IndexError; el resto: `InvalidExif`.
            let conocido = matches!(
                seg,
                [0xFF, 0xE0] | [0xFF, 0xE1] | [0xFF, 0xE2] | [0xFF, 0xEE] | [0xFF, 0xD8]
                    | [0xFF, 0xEC]
            );
            return if conocido { ExifJpeg::Lanza } else { ExifJpeg::Vacio };
        }
        if matches!(seg, [0xFF, 0xE1]) && corte(data, base + 4, base + 8) == &b"Exif"[..] {
            base -= 2;
            break;
        }
        base += usize::from(data[base + 2]) * 256 + usize::from(data[base + 3]) + 2;
    }
    // `ord_(data[2 + base])` sin protección.
    if data.len() <= base + 2 {
        return ExifJpeg::Lanza;
    }
    if data[base + 2] != 0xFF {
        return ExifJpeg::Vacio;
    }
    let ident5 = corte(data, base + 6, base + 11);
    let hay_cabecera = corte(data, base + 6, base + 10) == &b"Exif"[..]
        || ident5 == &b"Ducky"[..]
        || ident5 == &b"Adobe"[..];
    if !hay_cabecera {
        return ExifJpeg::Vacio;
    }
    // `endian = fh.read(1)` -> `get_endian_str(b"")` lanza IndexError.
    match buf.get(base + 12..) {
        Some(t) if !t.is_empty() => ExifJpeg::Tiff(t),
        _ => ExifJpeg::Lanza,
    }
}

/// `ExifHeader.s2n`: entero de `len` bytes; 0 si la lectura es corta o vacía.
fn s2n(d: &[u8], le: bool, p: usize, len: usize) -> u32 {
    let Some(b) = p.checked_add(len).and_then(|fin| d.get(p..fin)) else {
        return 0;
    };
    let mut v: u32 = 0;
    if le {
        for &x in b.iter().rev() {
            v = (v << 8) | u32::from(x);
        }
    } else {
        for &x in b {
            v = (v << 8) | u32::from(x);
        }
    }
    v
}

fn s2n16(d: &[u8], le: bool, p: usize) -> u16 {
    u16::try_from(s2n(d, le, p, 2)).unwrap_or(0)
}

/// ¿`values` queda vacío para la entrada en `e` (IndexError en `values[0]`)?
fn valores_vacios(d: &[u8], le: bool, e: usize, tipo: u16, count: u32) -> bool {
    if tipo == 2 {
        if count == 0 {
            return true;
        }
        let pos = if count > 4 { s2n(d, le, e + 8, 4) as usize } else { e + 8 };
        // ASCII: `read(count)` y corte en el primer NUL.
        return d.get(pos).is_none_or(|&b| b == 0);
    }
    count == 0 || count >= 1000
}

/// `modelo_make_exifread` sobre un TIFF ya localizado (`datos` empieza en el byte de
/// endian; los offsets son relativos a él). Réplica de `process_file` de ExifRead con
/// `s2n` tolerante: una lectura corta da 0, nunca falla.
fn modelo_make_tiff(datos: &[u8]) -> ModeloMake {
    let nada: ModeloMake = (None, None, false, false, false);
    let Some(&e0) = datos.first() else {
        return nada;
    };
    let le = e0 == b'I'; // ExifRead: "<" solo si el byte es 'I'; cualquier otro, ">".
    let off = s2n(datos, le, 4, 4) as usize;
    if off == 0 {
        return nada; // `list_ifd` vacío
    }
    let n = usize::from(s2n16(datos, le, off));
    let fin_entradas = off + 2 + 12 * n;
    // `None` = sin tag; `Some(None)` = tag que no se modela; `Some(Some(v))` = valor.
    let mut modelo: Option<Option<String>> = None;
    let mut make: Option<Option<String>> = None;
    let mut ilegible = false;
    for i in 0..n {
        let e = off + 2 + 12 * i;
        let tag = s2n16(datos, le, e);
        let tipo = s2n16(datos, le, e + 2);
        let count = s2n(datos, le, e + 4, 4);
        // Tipo desconocido/PROPRIETARY (también entradas más allá del final): salta.
        let Some(unidad) = unidad_exifread(tipo) else {
            continue;
        };
        // `process_file`: `exif_off.values[0]` lanza IndexError si `values` está vacío.
        if tag == 0x8769 && valores_vacios(datos, le, e, tipo, count) {
            ilegible = true;
        }
        if tag != 0x010F && tag != 0x0110 {
            continue;
        }
        let valor = if tipo != 2 {
            None
        } else if count == 0 {
            Some(String::new())
        } else {
            let pos = if u64::from(count) * unidad > 4 {
                s2n(datos, le, e + 8, 4) as usize
            } else {
                e + 8
            };
            let resto = datos.get(pos..).unwrap_or(&[]);
            let b = &resto[..resto.len().min(count as usize)];
            let corte_nul = b.iter().position(|&c| c == 0).unwrap_or(b.len());
            String::from_utf8(b[..corte_nul].to_vec()).ok()
        };
        if tag == 0x0110 {
            modelo = Some(valor);
        } else {
            make = Some(valor);
        }
    }
    // IFD0 entero (entradas + puntero al siguiente) dentro de los datos: ausencia cierta.
    let ifd0_entero = fin_entradas + 4 <= datos.len();
    let sig = s2n(datos, le, fin_entradas, 4) as usize;
    if sig != 0 && sig != off {
        // IFD1 = "Thumbnail": `extract_tiff_thumbnail` / `extract_jpeg_thumbnail`.
        let n1 = usize::from(s2n16(datos, le, sig));
        let (mut t_off, mut t_len): (Option<(u16, u32)>, Option<(u16, u32)>) = (None, None);
        for i in 0..n1 {
            let e = sig + 2 + 12 * i;
            let tag = s2n16(datos, le, e);
            let tipo = s2n16(datos, le, e + 2);
            let count = s2n(datos, le, e + 4, 4);
            if unidad_exifread(tipo).is_none() {
                continue;
            }
            if tag == 0x0103 && (tipo != 3 || s2n16(datos, le, e + 8) == 1) {
                // Miniatura TIFF sin comprimir: puede lanzar o no; no se modela.
                return (None, None, false, false, ilegible);
            }
            match tag {
                0x0201 => t_off = Some((tipo, count)),
                0x0202 => t_len = Some((tipo, count)),
                _ => {}
            }
        }
        // Con ambos tags: `seek(offset + values[0])` / `read(values[0])` lanzan si
        // `values` está vacío o `values[0]` no es un entero (ASCII, RATIONAL, FLOAT).
        if let (Some(a), Some(b)) = (t_off, t_len) {
            let invalido = |(tipo, count): (u16, u32)| {
                matches!(tipo, 2 | 5 | 10 | 11 | 12) || count == 0 || count >= 1000
            };
            if invalido(a) || invalido(b) {
                ilegible = true;
            }
        }
    }
    if ilegible {
        return (None, None, false, false, true);
    }
    let modelo_ausente = ifd0_entero && modelo.is_none();
    let make_ausente = ifd0_entero && make.is_none();
    (modelo.flatten(), make.flatten(), modelo_ausente, make_ausente, false)
}

/// Resultado de `parsear_cabecera` (getters de solo lectura). Un campo `None` es un
/// dato que no existe (o que Pillow/ExifRead no darían): Python NO lo recalcula. Con
/// `necesita_completo` el buffer no basta y Python debe repetir la llamada con el
/// fichero ENTERO (`completo=True`).
#[pyclass(frozen, get_all, module = "atom_rs")]
#[derive(Debug, Clone, PartialEq)]
pub struct CabeceraParseada {
    /// Tag 36867 tal cual lo da Pillow (`"%Y:%m:%d %H:%M:%S"` en un DJI); el
    /// `split(' ')` + `strptime` se hacen en Python. Siempre `None` en TIFF (Pillow
    /// `TiffImageFile` no tiene `_getexif`).
    pub timestamp_iso: Option<String>,
    /// `str(tags["Image Model"])` de ExifRead.
    pub modelo: Option<String>,
    /// `str(tags["Image Make"])` de ExifRead, SIN el `strip` (lo hace Python).
    pub make: Option<String>,
    /// ExifRead lanzaría al leer Make (EXIF raro/truncado): Python deja `make_ilegible`.
    pub make_ilegible: bool,
    pub gps: Option<(f64, f64)>,
    pub ancho: Option<u32>,
    pub alto: Option<u32>,
    /// `"abre"` | `"corto"` | `"incierto"`: `Image.open` sobre ESTE buffer (informativo).
    pub pil_estado: String,
    pub gimbal_yaw: Option<String>,
    pub gimbal_pitch: Option<String>,
    /// AbsoluteAltitude, RelativeAltitude, GimbalRollDegree, FlightRollDegree,
    /// FlightYawDegree, FlightPitchDegree, CamReverse, GimbalReverse, RtkFlag.
    pub xmp: Option<Vec<String>>,
    /// El bloque XMP sale del buffer. Con `completo` siempre es true (sin XMP, todos
    /// los valores son "0", como `exif.get_xmp_data`).
    pub xmp_presente: bool,
    pub flight_yaw_en_xmp: bool,
    /// Campos (`"timestamp"`, `"gps"`, `"modelo"`, `"make"`) que son `None` porque el
    /// tag NO existe con certeza. Informativo.
    pub ausentes: Vec<String>,
    /// `"jpeg"` | `"tiff"` | `"png"` | `"desconocido"` (por la firma del buffer).
    pub formato: String,
    /// El buffer es solo una cabecera y falta información (JPEG que no abre o sin XMP
    /// cerrado; TIFF/PNG grandes): Python relee el fichero entero y llama de nuevo con
    /// `completo=True`. Siempre false con `completo` (o con un buffer < 256 KiB).
    pub necesita_completo: bool,
}

#[pymethods]
impl CabeceraParseada {
    fn __repr__(&self) -> String {
        format!("{self:?}")
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Formato {
    Jpeg,
    Tiff,
    Png,
    Otro,
}

impl Formato {
    fn nombre(&self) -> &'static str {
        match self {
            Formato::Jpeg => "jpeg",
            Formato::Tiff => "tiff",
            Formato::Png => "png",
            Formato::Otro => "desconocido",
        }
    }
}

const FIRMA_PNG: &[u8] = b"\x89PNG\r\n\x1a\n";

fn formato_de(buf: &[u8]) -> Formato {
    if buf.get(..2) == Some(&[0xFF, 0xD8][..]) {
        Formato::Jpeg
    } else if Tiff::nuevo(buf).is_some() {
        Formato::Tiff
    } else if buf.get(..8) == Some(FIRMA_PNG) {
        Formato::Png
    } else {
        Formato::Otro
    }
}

/// Campos que dependen del formato (todo salvo el XMP, que es búsqueda de texto).
struct Base {
    timestamp_iso: Option<String>,
    gps: Option<(f64, f64)>,
    dims: Option<(u32, u32)>,
    modelo: Option<String>,
    make: Option<String>,
    make_ilegible: bool,
    estado: &'static str,
    ausentes: Vec<String>,
    necesita_completo: bool,
}

impl Base {
    fn vacia(estado: &'static str) -> Self {
        Base {
            timestamp_iso: None,
            gps: None,
            dims: None,
            modelo: None,
            make: None,
            make_ilegible: false,
            estado,
            ausentes: Vec::new(),
            necesita_completo: false,
        }
    }
}

/// JPEG: `Image.open` simulado (`caminar`) + EXIF. Con el buffer completo, un fichero
/// que Pillow no abriría (`Corto`/`Incierto`) deja fecha/GPS/dimensiones en `None`.
fn base_jpeg(buf: &[u8], completo: bool) -> Base {
    let mut r = Recorrido::default();
    let paso = caminar(buf, &mut r);
    let estado = estado_pil(paso, &r);
    let mut b = Base::vacia(estado.nombre());
    if let (EstadoPil::Abre { ancho, alto }, Some(t)) =
        (estado, r.exif.and_then(|s| Tiff::nuevo(quitar_prefijo_exif(s))))
    {
        let (ts, g) = (timestamp_pil(&t), gps_pil(&t));
        if ts.is_none() && timestamp_ausente(&t) {
            b.ausentes.push("timestamp".to_string());
        }
        if g.is_none() && gps_ausente(&t) {
            b.ausentes.push("gps".to_string());
        }
        b.timestamp_iso = ts;
        b.gps = g;
        b.dims = Some((u32::from(ancho), u32::from(alto)));
    } else if let EstadoPil::Abre { ancho, alto } = estado {
        b.dims = Some((u32::from(ancho), u32::from(alto)));
    }
    b.necesita_completo = !completo && !matches!(estado, EstadoPil::Abre { .. });
    poner_modelo_make(&mut b, modelo_make_exifread(buf));
    b
}

fn poner_modelo_make(b: &mut Base, v: ModeloMake) {
    let (modelo, make, modelo_ausente, make_ausente, make_ilegible) = v;
    b.make_ilegible = make_ilegible;
    if modelo_ausente {
        b.ausentes.push("modelo".to_string());
    }
    if make_ausente {
        b.ausentes.push("make".to_string());
    }
    b.modelo = modelo;
    b.make = make;
}

/// `Image.open(...).size` en un PNG/TIFF: por encima de 2x `MAX_IMAGE_PIXELS` Pillow
/// lanza `DecompressionBombError` (y `_dimensiones_desde_buffer` da `None`).
fn dims_validas(ancho: u32, alto: u32) -> Option<(u32, u32)> {
    if ancho == 0 || alto == 0 || u64::from(ancho) * u64::from(alto) > 2 * MAX_IMAGE_PIXELS {
        None
    } else {
        Some((ancho, alto))
    }
}

/// TIFF clásico: IFD0 (ImageWidth 256 / ImageLength 257), Make/Model (ExifRead) y GPS
/// (IFD 34853, como `getexif().get_ifd`). Fecha siempre `None` (golden).
fn base_tiff(buf: &[u8], completo: bool) -> Base {
    let mut b = Base::vacia("incierto");
    let Some(t) = Tiff::nuevo(buf) else {
        return b;
    };
    if !completo {
        // Python leía el fichero entero (Pillow sobre un TIFF recortado no es fiable).
        b.necesita_completo = true;
        return b;
    }
    if let Some(ifd0) = t.primer_ifd().and_then(|o| t.ifd_pil(o)) {
        let ancho = ifd0.get(&256).and_then(|e| t.entero_uno(e));
        let alto = ifd0.get(&257).and_then(|e| t.entero_uno(e));
        if let (Some(a), Some(h)) = (ancho, alto) {
            b.dims = dims_validas(a, h);
        }
    }
    if b.dims.is_some() {
        b.estado = "abre";
    }
    b.gps = gps_pil(&t);
    if b.gps.is_none() && gps_ausente(&t) {
        b.ausentes.push("gps".to_string());
    }
    poner_modelo_make(&mut b, modelo_make_tiff(buf));
    b
}

fn be32(b: &[u8], p: usize) -> Option<u32> {
    let a: [u8; 4] = b.get(p..p.checked_add(4)?)?.try_into().ok()?;
    Some(u32::from_be_bytes(a))
}

/// PNG: IHDR (tamaño) y chunk `eXIf` (EXIF). Pillow solo ve un `eXIf` posterior al
/// primer IDAT si carga la imagen entera (`load`), lo que exige llegar a IEND; uno
/// anterior a IDAT ya está en `info` tras `open`, y entonces manda sobre el posterior.
/// El XMP (iTXt/tEXt) NO se parsea por chunks: `indice.py` lo busca como texto en el
/// fichero y aquí se reproduce igual.
fn base_png(buf: &[u8], completo: bool) -> Base {
    let mut b = Base::vacia("incierto");
    if be32(buf, 8) >= Some(13) && buf.get(12..16) == Some(&b"IHDR"[..]) {
        if let (Some(a), Some(h)) = (be32(buf, 16), be32(buf, 20)) {
            b.dims = dims_validas(a, h);
        }
    }
    if b.dims.is_some() {
        b.estado = "abre";
    }
    if !completo {
        b.necesita_completo = true;
        return b;
    }
    let mut exif_antes: Option<&[u8]> = None;
    let mut exif_despues: Option<&[u8]> = None;
    let mut idat = false;
    let mut hay_iend = false;
    let mut pos: usize = 8;
    while let (Some(len), Some(tipo)) = (be32(buf, pos), buf.get(pos.saturating_add(4)..pos.saturating_add(8))) {
        let Some(ini) = pos.checked_add(8) else {
            break;
        };
        let Some(datos) = ini.checked_add(len as usize).and_then(|fin| buf.get(ini..fin)) else {
            break; // chunk truncado
        };
        match tipo {
            b"IDAT" => idat = true,
            b"eXIf" => {
                if idat {
                    exif_despues = Some(datos);
                } else {
                    exif_antes = Some(datos);
                }
            }
            b"IEND" => {
                hay_iend = true;
                break;
            }
            _ => {}
        }
        pos = ini + datos.len() + 4; // + CRC (no se verifica)
    }
    let exif = exif_antes.or(if hay_iend { exif_despues } else { None });
    if let Some(t) = exif.and_then(|e| Tiff::nuevo(quitar_prefijo_exif(e))) {
        b.timestamp_iso = timestamp_pil(&t);
        b.gps = gps_pil(&t);
        if b.timestamp_iso.is_none() && timestamp_ausente(&t) {
            b.ausentes.push("timestamp".to_string());
        }
        if b.gps.is_none() && gps_ausente(&t) {
            b.ausentes.push("gps".to_string());
        }
        poner_modelo_make(&mut b, modelo_make_tiff(t.d));
    }
    b
}

/// Bloque donde buscar el XMP, como `indice._bloque_xmp_desde_buffer`: el de la
/// cabecera si trae el cierre (o el atajo del tag 700 de un TIFF grande); si no, con el
/// fichero entero (`completo`) el texto completo, que es lo que devuelve
/// `exif.leer_bloque_xmp` al releer; sin él, `None` (Python debe pasar el fichero).
fn bloque_para(buf: &[u8], completo: bool) -> Option<Vec<u8>> {
    let cabecera = buf.get(..xmp::BYTES_CABECERA).unwrap_or(buf);
    if let Some(b) = xmp::bloque_xmp(cabecera) {
        return Some(b);
    }
    if completo {
        Some(xmp::normalizar_saltos(buf))
    } else {
        None
    }
}

fn datos_xmp(bloque: &[u8]) -> xmp::DatosXmp {
    let mut gimbal = xmp::extraer_campos(bloque, &xmp::CAMPOS_GIMBAL).into_iter();
    let gimbal_yaw = gimbal.next().unwrap_or_else(|| "0".to_string());
    let gimbal_pitch = gimbal.next().unwrap_or_else(|| "0".to_string());
    xmp::DatosXmp {
        gimbal_yaw,
        gimbal_pitch,
        xmp: xmp::extraer_campos(bloque, &xmp::CAMPOS_XMP),
        flight_yaw_en_xmp: xmp::buscar(bloque, b"FlightYawDegree").is_some(),
    }
}

/// Buffer de cabecera (sin saber si es el fichero entero): `analizar_con(buf, false)`.
pub fn analizar(buf: &[u8]) -> CabeceraParseada {
    analizar_con(buf, false)
}

/// Núcleo puro (sin Python): también lo usan los tests de `cargo test`. `completo` =
/// `buf` es el fichero entero (también se infiere si `buf` < 256 KiB).
pub fn analizar_con(buf: &[u8], completo: bool) -> CabeceraParseada {
    let completo = completo || buf.len() < xmp::BYTES_CABECERA;
    let formato = formato_de(buf);
    let mut base = match formato {
        Formato::Jpeg => base_jpeg(buf, completo),
        Formato::Tiff => base_tiff(buf, completo),
        Formato::Png => base_png(buf, completo),
        Formato::Otro => Base::vacia("incierto"),
    };
    let x = bloque_para(buf, completo).map(|b| datos_xmp(&b));
    if x.is_none() {
        base.necesita_completo = true;
    }
    CabeceraParseada {
        timestamp_iso: base.timestamp_iso,
        modelo: base.modelo,
        make: base.make,
        make_ilegible: base.make_ilegible,
        gps: base.gps,
        ancho: base.dims.map(|d| d.0),
        alto: base.dims.map(|d| d.1),
        pil_estado: base.estado.to_string(),
        xmp_presente: x.is_some(),
        flight_yaw_en_xmp: x.as_ref().is_some_and(|d| d.flight_yaw_en_xmp),
        ausentes: base.ausentes,
        formato: formato.nombre().to_string(),
        necesita_completo: base.necesita_completo,
        gimbal_yaw: x.as_ref().map(|d| d.gimbal_yaw.clone()),
        gimbal_pitch: x.as_ref().map(|d| d.gimbal_pitch.clone()),
        xmp: x.map(|d| d.xmp),
    }
}

/// Parsea la cabecera (los primeros `_BYTES_CABECERA` bytes) o, con `completo=True`, el
/// fichero entero, sin el GIL. Nunca lanza por datos de entrada: lo que no entiende sale
/// `None`; `necesita_completo` pide a Python que repita la llamada con el fichero entero.
#[pyfunction]
#[pyo3(signature = (buf, completo=false))]
pub fn parsear_cabecera(py: Python<'_>, buf: &[u8], completo: bool) -> PyResult<CabeceraParseada> {
    Ok(py.allow_threads(|| analizar_con(buf, completo)))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// JPEG DJI mínimo: SOI, APP1 Exif (IFD0 Make/Model + punteros Exif/GPS), DQT,
    /// SOF0 64x48, SOS.
    fn jpeg_minimo() -> Vec<u8> {
        let t = tiff_dji();
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

    /// TIFF EXIF de un DJI: IFD0 Make/Model + punteros Exif (fecha) y GPS.
    fn tiff_dji() -> Vec<u8> {
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
        t
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
        // F2: un buffer < 256 KiB es el fichero entero (`completo` inferido), así que el
        // bloque XMP "resuelto" es el texto completo y `xmp_presente` es true aunque no
        // haya XMP (valores por defecto "0", como `get_xmp_data` sobre `leer_bloque_xmp`).
        // `xmp_presente=false` solo ocurre con cabecera de 256 KiB sin cierre XMP.
        assert!(c.xmp_presente && !c.flight_yaw_en_xmp && !c.necesita_completo);
        assert_eq!(c.xmp.as_ref().map(|v| v.len()), Some(9));
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
        assert!(c.make_ilegible);
    }

    /// TIFF LE: IFD0 con Make "DJI" inline y un IFD1 con JPEGInterchangeFormat de tipo
    /// `tipo_off` y JPEGInterchangeFormatLength LONG.
    fn tiff_con_ifd1(tipo_off: u16) -> Vec<u8> {
        let mut t: Vec<u8> = b"II*\0\x08\0\0\0".to_vec();
        let ent = |t: &mut Vec<u8>, tag: u16, tipo: u16, count: u32, val: [u8; 4]| {
            t.extend_from_slice(&tag.to_le_bytes());
            t.extend_from_slice(&tipo.to_le_bytes());
            t.extend_from_slice(&count.to_le_bytes());
            t.extend_from_slice(&val);
        };
        t.extend_from_slice(&1u16.to_le_bytes());
        ent(&mut t, 0x010F, 2, 4, *b"DJI\0");
        t.extend_from_slice(&26u32.to_le_bytes()); // IFD1 justo detrás
        t.extend_from_slice(&2u16.to_le_bytes());
        ent(&mut t, 0x0201, tipo_off, 1, [0; 4]);
        ent(&mut t, 0x0202, 4, 1, [0; 4]);
        t.extend_from_slice(&0u32.to_le_bytes());
        t
    }

    #[test]
    fn miniatura_jpeg_tipo_no_entero_es_make_ilegible() {
        // Control: tipo LONG -> ExifRead no lanza y el Make sale.
        let (modelo, make, _, _, ilegible) = modelo_make_tiff(&tiff_con_ifd1(4));
        assert!(modelo.is_none() && make.as_deref() == Some("DJI") && !ilegible);
        // ASCII: `offset + values[0]` lanza TypeError.
        let (modelo, make, ma, mo, ilegible) = modelo_make_tiff(&tiff_con_ifd1(2));
        assert!(modelo.is_none() && make.is_none() && !ma && !mo && ilegible);
    }

    #[test]
    fn jpeg_truncado_como_exifread() {
        let j = jpeg_minimo();
        // `data[2]`/`data[3]`/`data[base+3]` de ExifRead lanzan IndexError (n = 2..=5) y,
        // con la cabecera `Exif` completa pero sin endian, `get_endian_str(b"")` (n = 11).
        for n in [2usize, 3, 4, 5, 11] {
            assert!(analizar(&j[..n]).make_ilegible, "n={n}");
        }
        // 0/1 bytes: formato desconocido, ExifRead devuelve `{}`; 20: IFD0 fuera de datos.
        for n in [0usize, 1, 6, 20] {
            let c = analizar(&j[..n]);
            assert!(!c.make_ilegible && c.make.is_none(), "n={n}");
        }
        // Make inline sí sale con el IFD0 cortado tras la primera entrada; Model queda "".
        let c = analizar(&j[..40]);
        assert_eq!(c.make.as_deref(), Some("DJI"));
        assert_eq!(c.modelo.as_deref(), Some(""));
        assert!(!c.make_ilegible);
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

    const XMP_DJI: &[u8] = b"<x:xmpmeta drone-dji:GimbalYawDegree=\"-87.40\" \
        drone-dji:GimbalPitchDegree=\"-45.20\" drone-dji:FlightYawDegree=\"-86.90\"></x:xmpmeta>";

    /// TIFF mínimo (LE): IFD0 con ancho 64, alto 48, Make "DJI", Model "FC6310" y,
    /// tras los valores, el XMP como texto.
    fn tiff_minimo(con_xmp: bool) -> Vec<u8> {
        let mut t: Vec<u8> = b"II*\0\x08\0\0\0".to_vec();
        let n = 4u16;
        let fin_ifd = 8 + 2 + 12 * n as usize + 4;
        let ent = |t: &mut Vec<u8>, tag: u16, tipo: u16, count: u32, val: u32| {
            t.extend_from_slice(&tag.to_le_bytes());
            t.extend_from_slice(&tipo.to_le_bytes());
            t.extend_from_slice(&count.to_le_bytes());
            t.extend_from_slice(&val.to_le_bytes());
        };
        t.extend_from_slice(&n.to_le_bytes());
        ent(&mut t, 256, 3, 1, 64);
        ent(&mut t, 257, 3, 1, 48);
        ent(&mut t, 0x010F, 2, 4, u32::from_le_bytes(*b"DJI\0"));
        ent(&mut t, 0x0110, 2, 7, fin_ifd as u32);
        t.extend_from_slice(&0u32.to_le_bytes());
        t.extend_from_slice(b"FC6310\0");
        if con_xmp {
            t.extend_from_slice(XMP_DJI);
        }
        t
    }

    fn chunk_png(out: &mut Vec<u8>, tipo: &[u8; 4], datos: &[u8]) {
        out.extend_from_slice(&(datos.len() as u32).to_be_bytes());
        out.extend_from_slice(tipo);
        out.extend_from_slice(datos);
        out.extend_from_slice(&[0u8; 4]); // CRC: no se verifica
    }

    /// PNG 64x48 con eXIf (antes o después de IDAT), iTXt con XMP e IEND.
    fn png_minimo(exif_antes_de_idat: bool, con_iend: bool) -> Vec<u8> {
        let mut p = FIRMA_PNG.to_vec();
        let mut ihdr = Vec::new();
        ihdr.extend_from_slice(&64u32.to_be_bytes());
        ihdr.extend_from_slice(&48u32.to_be_bytes());
        ihdr.extend_from_slice(&[8, 2, 0, 0, 0]);
        chunk_png(&mut p, b"IHDR", &ihdr);
        if exif_antes_de_idat {
            chunk_png(&mut p, b"eXIf", &tiff_dji());
        }
        let mut itxt = b"XML:com.adobe.xmp\0\0\0\0\0".to_vec();
        itxt.extend_from_slice(XMP_DJI);
        chunk_png(&mut p, b"iTXt", &itxt);
        chunk_png(&mut p, b"IDAT", &[0u8; 4]);
        if !exif_antes_de_idat {
            chunk_png(&mut p, b"eXIf", &tiff_dji());
        }
        if con_iend {
            chunk_png(&mut p, b"IEND", &[]);
        }
        p
    }

    #[test]
    fn tiff_minimo_campos() {
        let c = analizar(&tiff_minimo(true));
        assert_eq!(c.formato, "tiff");
        assert_eq!(c.pil_estado, "abre");
        assert_eq!((c.ancho, c.alto), (Some(64), Some(48)));
        assert_eq!(c.timestamp_iso, None); // Pillow TiffImageFile no tiene `_getexif`
        assert_eq!(c.modelo.as_deref(), Some("FC6310"));
        assert_eq!(c.make.as_deref(), Some("DJI"));
        assert!(c.xmp_presente && c.flight_yaw_en_xmp && !c.necesita_completo);
        assert_eq!(c.gimbal_yaw.as_deref(), Some("-87.40"));
        assert_eq!(c.gimbal_pitch.as_deref(), Some("-45.20"));
        assert_eq!(c.xmp.as_ref().map(|v| v[4].as_str()), Some("-86.90"));
    }

    #[test]
    fn tiff_sin_xmp_da_ceros_y_grande_pide_completo() {
        let c = analizar(&tiff_minimo(false));
        assert!(c.xmp_presente && !c.flight_yaw_en_xmp);
        assert_eq!(c.gimbal_yaw.as_deref(), Some("0"));
        assert_eq!(c.xmp.as_ref().map(|v| v.len()), Some(9));
        // Buffer de exactamente una cabecera: puede ser un recorte -> Python pasa el entero.
        let mut grande = tiff_minimo(false);
        grande.resize(xmp::BYTES_CABECERA, 0);
        assert!(analizar_con(&grande, false).necesita_completo);
        let c = analizar_con(&grande, true);
        assert!(!c.necesita_completo && c.xmp_presente);
        assert_eq!((c.ancho, c.alto), (Some(64), Some(48)));
    }

    #[test]
    fn png_minimo_campos() {
        let c = analizar(&png_minimo(true, true));
        assert_eq!(c.formato, "png");
        assert_eq!((c.ancho, c.alto), (Some(64), Some(48)));
        assert_eq!(c.timestamp_iso.as_deref(), Some("2026:08:20 10:11:12"));
        assert_eq!(c.modelo.as_deref(), Some("FC6310"));
        assert_eq!(c.make.as_deref(), Some("DJI"));
        let (lat, lon) = c.gps.expect("gps");
        assert_eq!(lat, 40.0 + 25.0 / 60.0 + 12.34 / 3600.0);
        assert_eq!(lon, (3.0 + 41.0 / 60.0 + 5.5 / 3600.0) * -1.0);
        assert!(c.xmp_presente && c.flight_yaw_en_xmp);
        assert_eq!(c.gimbal_yaw.as_deref(), Some("-87.40"));
    }

    #[test]
    fn png_exif_tras_idat_exige_iend() {
        let c = analizar(&png_minimo(false, true));
        assert_eq!(c.timestamp_iso.as_deref(), Some("2026:08:20 10:11:12"));
        // Sin IEND (truncado): Pillow no puede `load()` -> sin EXIF; el tamaño sí sale.
        let c = analizar(&png_minimo(false, false));
        assert!(c.timestamp_iso.is_none() && c.gps.is_none() && c.modelo.is_none());
        assert_eq!((c.ancho, c.alto), (Some(64), Some(48)));
    }

    #[test]
    fn png_y_tiff_truncados_no_panican() {
        for base in [png_minimo(true, true), tiff_minimo(true)] {
            for n in 0..=base.len() {
                let c = analizar(&base[..n]);
                assert!(!c.necesita_completo);
            }
        }
    }

    #[test]
    fn jpeg_roto_con_fichero_entero_no_inventa_fecha_ni_tamano() {
        // Marcador 0xFF01 antes del DQT ("no marker found" en Pillow): no abre.
        let mut j = jpeg_minimo();
        let p = j.windows(2).position(|w| w == [0xFF, 0xDB]).expect("DQT");
        j.insert(p, 0x01);
        j.insert(p, 0xFF);
        let c = analizar_con(&j, true);
        assert_ne!(c.pil_estado, "abre");
        assert!(c.timestamp_iso.is_none() && c.gps.is_none());
        assert!(c.ancho.is_none() && c.alto.is_none());
        assert_eq!(c.modelo.as_deref(), Some("FC6310"));
        assert_eq!(c.make.as_deref(), Some("DJI"));
        assert!(!c.necesita_completo);
    }

    #[test]
    fn jpeg_con_jfif_antes_del_exif_lee_modelo() {
        let j = jpeg_minimo();
        let mut k = vec![0xFF, 0xD8, 0xFF, 0xE0, 0, 16];
        k.extend_from_slice(b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0");
        k.extend_from_slice(&j[2..]);
        let c = analizar(&k);
        assert_eq!(c.modelo.as_deref(), Some("FC6310"));
        assert_eq!(c.make.as_deref(), Some("DJI"));
    }

    #[test]
    fn formato_desconocido_no_panica() {
        let c = analizar(b"BM\0\0\0\0\0\0\0\0");
        assert_eq!(c.formato, "desconocido");
        assert!(c.ancho.is_none() && c.modelo.is_none() && !c.necesita_completo);
    }
}
