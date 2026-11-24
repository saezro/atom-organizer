//! Cliente de subida: backoff y clasificación (puros, 6e) y parte de red (6f,
//! reqwest + tokio): resumable con consulta de offset, PUT simple con OAuth,
//! reintentos sin tope de tiempo y verificación del hash del servidor.

use super::hash::{self, HashesLocales, Veredicto};
use super::orden::Item;
use bytes::Bytes;
use std::future::Future;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const BASE_MS: u64 = 1_000;
const TOPE_MS: u64 = 30_000;

/// Qué hacer ante una respuesta/fallo.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Clase {
    Reintentar,
    Auth,
    YaExiste,
    SesionMuerta,
    Fatal,
}

fn xorshift64(estado: &mut u64) -> u64 {
    // xorshift64 no admite estado 0.
    let mut x = if *estado == 0 { 0x9E37_79B9_7F4A_7C15 } else { *estado };
    x ^= x << 13;
    x ^= x >> 7;
    x ^= x << 17;
    *estado = x;
    x
}

/// Espera antes del reintento `intento` (0 = primer reintento): 1 s x2, tope
/// 30 s, con jitter entre el 50 % y el 100 % del valor.
pub fn espera(intento: u32, semilla: &mut u64) -> Duration {
    let exp = intento.min(16);
    let bruto = BASE_MS.saturating_mul(1u64 << exp).min(TOPE_MS);
    let mitad = bruto / 2;
    let jitter = xorshift64(semilla) % (bruto - mitad + 1);
    Duration::from_millis(mitad + jitter)
}

/// Clasifica una respuesta. `estado=None` = no hubo respuesta HTTP.
pub fn clasificar(estado: Option<u16>, es_timeout_o_conexion: bool) -> Clase {
    if es_timeout_o_conexion {
        return Clase::Reintentar;
    }
    match estado {
        None => Clase::Reintentar,
        Some(408) | Some(429) => Clase::Reintentar,
        Some(s) if (500..=599).contains(&s) => Clase::Reintentar,
        Some(401) | Some(403) => Clase::Auth,
        Some(412) => Clase::YaExiste,
        Some(404) | Some(410) => Clase::SesionMuerta,
        Some(_) => Clase::Fatal,
    }
}


/// 403 de GCS por falta de `storage.objects.delete`: el objeto ya existe y la
/// credencial (solo crear) no puede reemplazarlo. Equivale a un 412.
pub fn es_403_por_sobrescribir(cuerpo: &str) -> bool {
    cuerpo.contains("storage.objects.delete")
}


// ---------------------------------------------------------------------------
// Parte de red (6f): reqwest + tokio.
// ---------------------------------------------------------------------------

/// Lo que el cliente necesita de Python (o de un mock en los tests). Todas las
/// llamadas son bloqueantes y se ejecutan con `spawn_blocking`, nunca en el
/// reactor.
pub trait Entorno: Send + Sync + 'static {
    /// URL de subida del objeto (`provider.upload_url`).
    fn url(&self, remote: &str, size: u64, sobrescribir: bool) -> Result<String, String>;
    /// Valor COMPLETO de `Authorization` (cacheado por la implementación);
    /// `None` con URL firmada.
    fn token(&self) -> Result<Option<String>, String>;
    /// `provider.recover_auth`: true = la sesión sigue válida.
    fn recover(&self) -> bool;
}

#[derive(Debug, Clone)]
pub struct Config {
    /// true = proveedor OAuth (permite PUT simple, HEAD y reintento con generación).
    pub oauth: bool,
    pub chunk_bytes: u64,
    pub simple_max_bytes: u64,
    pub reintentos_hash: u32,
    /// Divisor de las esperas de backoff (1 en producción; los tests lo suben).
    pub escala_espera: u32,
}

impl Config {
    pub fn nueva(oauth: bool, chunk_bytes: u64, simple_max_bytes: u64, reintentos_hash: u32) -> Self {
        Config { oauth, chunk_bytes: chunk_bytes.max(1), simple_max_bytes, reintentos_hash, escala_espera: 1 }
    }
}

pub const M_DISCO: usize = 0;
pub const M_RED: usize = 1;
pub const M_SESION: usize = 2;
pub const M_ESPERA: usize = 3;
pub const M_TOKEN: usize = 4;
pub const C_429: usize = 0;
pub const C_5XX: usize = 1;
pub const C_AUTH: usize = 2;
pub const C_HTTP: usize = 3;
pub const C_RED: usize = 4;

/// Contadores compartidos entre todas las transferencias.
#[derive(Debug, Default)]
pub struct Stats {
    /// Bytes confirmados por el servidor (no se descuentan al reintentar).
    pub bytes: AtomicU64,
    pub reintentos: AtomicU64,
    /// Eventos de congestión desde la última ventana AIMD (429/5xx/red).
    pub congestion: AtomicU64,
    /// Reintentos por causa: 429, 5xx, auth, http, red.
    pub causas: [AtomicU64; 5],
    /// Microsegundos acumulados: disco, red, sesión, espera, token.
    pub micros: [AtomicU64; 5],
}

impl Stats {
    pub fn sumar(&self, idx: usize, desde: Instant) {
        self.micros[idx].fetch_add(desde.elapsed().as_micros() as u64, Ordering::Relaxed);
    }
    pub fn segundos(&self, idx: usize) -> f64 {
        self.micros[idx].load(Ordering::Relaxed) as f64 / 1_000_000.0
    }
}

#[derive(Clone)]
pub struct Ctx {
    pub http: reqwest::Client,
    pub env: Arc<dyn Entorno>,
    pub stop: Arc<AtomicBool>,
    pub cfg: Config,
    pub stats: Arc<Stats>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Resultado {
    /// Subido y verificado contra el hash del servidor.
    Hecho { md5_b64: String },
    /// 412: el objeto ya existía (no es error).
    YaExiste,
    Cancelado,
    /// Fallo definitivo. `tipo` ∈ {"hash","rechazado_403","sesion","io","panic"}.
    Fallo { tipo: &'static str, motivo: String },
}

/// Cliente HTTP único: rustls con raíces nativas, sin redirecciones (308 es
/// parte del protocolo resumable), `User-Agent` fijo.
pub fn crear_cliente() -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .use_rustls_tls()
        .user_agent("ATOM-Organizer-Uploader")
        .connect_timeout(Duration::from_secs(20))
        .redirect(reqwest::redirect::Policy::none())
        .build()
        .map_err(|e| format!("no se pudo crear el cliente HTTP: {}", e.without_url()))
}

/// Salida interna de las operaciones.
#[derive(Debug)]
enum Corte {
    Cancelado,
    /// Fallo transitorio de UNA petición que el llamador gestiona (`una_vez`).
    Transitorio(String),
    YaExiste,
    SesionMuerta(String),
    /// Fallo definitivo con su tipo.
    Fin(&'static str, String),
}

enum Interrupcion {
    Cancelado,
    Panico(String),
}

struct Resp {
    estado: u16,
    goog_hash: String,
    location: Option<String>,
    range: Option<String>,
    generacion: Option<String>,
    cuerpo: String,
}

struct ErrEnvio {
    fatal: bool,
    msg: String,
}

fn cab_texto(h: &reqwest::header::HeaderMap, k: &str) -> Option<String> {
    h.get(k).and_then(|v| v.to_str().ok()).map(|s| s.to_string())
}

async fn enviar(rb: reqwest::RequestBuilder) -> Result<Resp, ErrEnvio> {
    let r = rb
        .send()
        .await
        .map_err(|e| ErrEnvio { fatal: e.is_builder(), msg: e.without_url().to_string() })?;
    let estado = r.status().as_u16();
    let (goog_hash, location, range, generacion) = {
        let h = r.headers();
        let goog = h
            .get_all("x-goog-hash")
            .iter()
            .filter_map(|v| v.to_str().ok())
            .collect::<Vec<_>>()
            .join(",");
        (goog, cab_texto(h, "location"), cab_texto(h, "range"), cab_texto(h, "x-goog-generation"))
    };
    let cuerpo = match r.bytes().await {
        Ok(b) => String::from_utf8_lossy(&b[..b.len().min(300)]).into_owned(),
        Err(_) => String::new(),
    };
    Ok(Resp { estado, goog_hash, location, range, generacion, cuerpo })
}

/// Ejecuta `fut` en una tarea y la aborta (<= 100 ms) si se pide parar.
async fn cancelable<T: Send + 'static>(
    stop: &AtomicBool,
    fut: impl Future<Output = T> + Send + 'static,
) -> Result<T, Interrupcion> {
    if stop.load(Ordering::Relaxed) {
        return Err(Interrupcion::Cancelado);
    }
    let mut h = tokio::spawn(fut);
    loop {
        match tokio::time::timeout(Duration::from_millis(100), &mut h).await {
            Ok(Ok(v)) => return Ok(v),
            Ok(Err(e)) => return Err(Interrupcion::Panico(e.to_string())),
            Err(_) => {
                if stop.load(Ordering::Relaxed) {
                    h.abort();
                    return Err(Interrupcion::Cancelado);
                }
            }
        }
    }
}

/// Función bloqueante (Python, disco) fuera del reactor y cancelable.
async fn bloqueante<T: Send + 'static>(
    ctx: &Ctx,
    f: impl FnOnce() -> T + Send + 'static,
) -> Result<T, Corte> {
    match cancelable(&ctx.stop, async move { tokio::task::spawn_blocking(f).await }).await {
        Ok(Ok(v)) => Ok(v),
        Ok(Err(e)) => Err(Corte::Fin("panic", e.to_string())),
        Err(Interrupcion::Cancelado) => Err(Corte::Cancelado),
        Err(Interrupcion::Panico(m)) => Err(Corte::Fin("panic", m)),
    }
}

/// Duerme en rodajas de <= 100 ms. false = se pidió parar.
async fn dormir(stop: &AtomicBool, d: Duration) -> bool {
    let fin = Instant::now() + d;
    loop {
        if stop.load(Ordering::Relaxed) {
            return false;
        }
        let ahora = Instant::now();
        if ahora >= fin {
            return true;
        }
        tokio::time::sleep((fin - ahora).min(Duration::from_millis(100))).await;
    }
}

/// Estado de reintentos de UN fichero.
struct Ctl {
    intento: u32,
    semilla: u64,
    auth_seguidos: u32,
    contados: u64,
}

impl Ctl {
    fn nuevo(remote: &str) -> Self {
        let mut s: u64 = 0xcbf2_9ce4_8422_2325;
        for b in remote.bytes() {
            s = (s ^ u64::from(b)).wrapping_mul(0x0000_0100_0000_01b3);
        }
        let ns = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(1);
        Ctl { intento: 0, semilla: s ^ ns, auth_seguidos: 0, contados: 0 }
    }
}

async fn pausa(ctx: &Ctx, ctl: &mut Ctl) -> Result<(), Corte> {
    let d = espera(ctl.intento, &mut ctl.semilla) / ctx.cfg.escala_espera.max(1);
    ctl.intento = ctl.intento.saturating_add(1);
    ctx.stats.reintentos.fetch_add(1, Ordering::Relaxed);
    let t = Instant::now();
    let seguir = dormir(&ctx.stop, d).await;
    ctx.stats.sumar(M_ESPERA, t);
    if seguir {
        Ok(())
    } else {
        Err(Corte::Cancelado)
    }
}

fn acreditar(ctx: &Ctx, ctl: &mut Ctl, hasta: u64) {
    if hasta > ctl.contados {
        ctx.stats.bytes.fetch_add(hasta - ctl.contados, Ordering::Relaxed);
        ctl.contados = hasta;
    }
}

struct Spec<'a> {
    metodo: reqwest::Method,
    url: &'a str,
    cab: Vec<(&'static str, String)>,
    /// `Bytes`: reintentar clona por refcount, sin copiar el trozo.
    cuerpo: Bytes,
    /// 308 es respuesta válida (resumable).
    acepta_308: bool,
    /// Un fallo transitorio se devuelve al llamador en vez de reintentar aquí.
    una_vez: bool,
}

fn timeout_para(bytes: usize) -> Duration {
    // 60 s base + 1 s por cada 64 KiB: un trozo de 16 MiB en un enlace lento
    // no puede quedarse siempre sin tiempo.
    Duration::from_secs(60 + (bytes as u64) / 65_536)
}

async fn token(ctx: &Ctx) -> Result<Option<String>, Corte> {
    let env = ctx.env.clone();
    let t = Instant::now();
    let r = bloqueante(ctx, move || env.token()).await;
    ctx.stats.sumar(M_TOKEN, t);
    match r? {
        Ok(v) => Ok(v),
        Err(m) => Err(Corte::Fin("sesion", format!("token: {m}"))),
    }
}

async fn recuperar(ctx: &Ctx) -> Result<bool, Corte> {
    let env = ctx.env.clone();
    let t = Instant::now();
    let r = bloqueante(ctx, move || env.recover()).await;
    ctx.stats.sumar(M_TOKEN, t);
    r
}

fn contar_causa(ctx: &Ctx, causa: usize, congestion: bool) {
    ctx.stats.causas[causa].fetch_add(1, Ordering::Relaxed);
    if congestion {
        ctx.stats.congestion.fetch_add(1, Ordering::Relaxed);
    }
}

/// Una petición con su política: reintentos sin tope (backoff + jitter) ante
/// red/5xx/429/408, un refresco de credenciales ante 401/403.
async fn ejecutar(ctx: &Ctx, ctl: &mut Ctl, sp: &Spec<'_>) -> Result<Resp, Corte> {
    loop {
        if ctx.stop.load(Ordering::Relaxed) {
            return Err(Corte::Cancelado);
        }
        let tok = token(ctx).await?;
        let mut rb = ctx
            .http
            .request(sp.metodo.clone(), sp.url)
            .timeout(timeout_para(sp.cuerpo.len()));
        for (k, v) in &sp.cab {
            rb = rb.header(*k, v.as_str());
        }
        if let Some(t) = tok {
            rb = rb.header("Authorization", t);
        }
        if !sp.cuerpo.is_empty() {
            rb = rb.body(reqwest::Body::from(sp.cuerpo.clone()));
        }
        let t0 = Instant::now();
        let r = cancelable(&ctx.stop, enviar(rb)).await;
        ctx.stats.sumar(M_RED, t0);
        let r = match r {
            Ok(r) => r,
            Err(Interrupcion::Cancelado) => return Err(Corte::Cancelado),
            Err(Interrupcion::Panico(m)) => return Err(Corte::Fin("panic", m)),
        };
        let resp = match r {
            Err(ErrEnvio { fatal: true, msg }) => return Err(Corte::Fin("io", msg)),
            Err(ErrEnvio { msg, .. }) => {
                contar_causa(ctx, C_RED, true);
                if sp.una_vez {
                    return Err(Corte::Transitorio(msg));
                }
                pausa(ctx, ctl).await?;
                continue;
            }
            Ok(resp) => resp,
        };
        let e = resp.estado;
        if e == 403 && es_403_por_sobrescribir(&resp.cuerpo) {
            // Credencial solo-crear (sin `storage.objects.delete`) contra un
            // objeto que YA existe: GCS comprueba el permiso de sobrescribir
            // antes que `ifGenerationMatch=0` y da 403 en vez de 412. Pasa al
            // reintentar un PUT cuya respuesta se perdió pero que sí llegó.
            // No es credencial caducada: refrescar no lo arregla.
            return Err(Corte::YaExiste);
        }
        if matches!(e, 200 | 201) || (sp.acepta_308 && e == 308) {
            ctl.intento = 0;
            ctl.auth_seguidos = 0;
            return Ok(resp);
        }
        match clasificar(Some(e), false) {
            Clase::Reintentar => {
                let (causa, cong) = if e == 429 {
                    (C_429, true)
                } else if e >= 500 {
                    (C_5XX, true)
                } else {
                    (C_HTTP, false)
                };
                contar_causa(ctx, causa, cong);
                if sp.una_vez {
                    return Err(Corte::Transitorio(format!("HTTP {e}")));
                }
                pausa(ctx, ctl).await?;
            }
            Clase::Auth => {
                contar_causa(ctx, C_AUTH, false);
                if ctl.auth_seguidos >= 1 {
                    return Err(Corte::Fin(
                        "rechazado_403",
                        format!("HTTP {e} tras refrescar credenciales: {}", resp.cuerpo),
                    ));
                }
                ctl.auth_seguidos += 1;
                if !recuperar(ctx).await? {
                    return Err(Corte::Fin(
                        "rechazado_403",
                        format!("HTTP {e} y la sesión ya no es válida: {}", resp.cuerpo),
                    ));
                }
            }
            Clase::YaExiste => return Err(Corte::YaExiste),
            Clase::SesionMuerta => return Err(Corte::SesionMuerta(format!("HTTP {e}"))),
            Clase::Fatal => return Err(Corte::Fin("io", format!("HTTP {e}: {}", resp.cuerpo))),
        }
    }
}

struct Local {
    hashes: HashesLocales,
    md5_b64: String,
    /// Contenido completo (solo PUT simple).
    datos: Bytes,
    total: u64,
}

fn preparar_local(ruta: &str, declarado: u64, en_memoria: bool) -> Result<Local, String> {
    let meta = std::fs::metadata(ruta).map_err(|e| format!("no se puede leer {ruta}: {e}"))?;
    if meta.len() != declarado {
        return Err(format!(
            "el tamaño de {ruta} en disco ({}) no coincide con el declarado ({declarado})",
            meta.len()
        ));
    }
    if en_memoria {
        let datos = std::fs::read(ruta).map_err(|e| format!("no se puede leer {ruta}: {e}"))?;
        if datos.len() as u64 != declarado {
            return Err(format!("{ruta} cambió de tamaño durante la lectura"));
        }
        let (md5, crc) = hash::hashes_bytes(&datos);
        Ok(Local {
            hashes: HashesLocales { md5, crc32c: crc },
            md5_b64: hash::b64_md5(&md5),
            datos: Bytes::from(datos),
            total: declarado,
        })
    } else {
        let (md5, crc) = hash::hashes_fichero(Path::new(ruta), 1 << 20)
            .map_err(|e| format!("no se puede leer {ruta}: {e}"))?;
        Ok(Local {
            hashes: HashesLocales { md5, crc32c: crc },
            md5_b64: hash::b64_md5(&md5),
            datos: Bytes::new(),
            total: declarado,
        })
    }
}

fn leer_trozo(ruta: &str, ini: u64, len: usize) -> std::io::Result<Vec<u8>> {
    use std::io::{Read, Seek, SeekFrom};
    let mut f = std::fs::File::open(ruta)?;
    f.seek(SeekFrom::Start(ini))?;
    let mut b = vec![0u8; len];
    f.read_exact(&mut b)?;
    Ok(b)
}

/// Sustituye/añade `ifGenerationMatch=<g>` en la query de la URL.
pub fn con_generacion(url: &str, g: &str) -> String {
    let (base, query) = url.split_once('?').unwrap_or((url, ""));
    let mut partes: Vec<String> = query
        .split('&')
        .filter(|p| !p.is_empty() && !p.starts_with("ifGenerationMatch="))
        .map(String::from)
        .collect();
    partes.push(format!("ifGenerationMatch={g}"));
    format!("{base}?{}", partes.join("&"))
}

/// `Range: bytes=0-N` -> N+1; sin cabecera (o ilegible) -> 0.
pub fn offset_de_range(r: Option<&str>) -> u64 {
    r.and_then(|s| s.rsplit_once('-'))
        .and_then(|(_, n)| n.trim().parse::<u64>().ok())
        .map(|n| n + 1)
        .unwrap_or(0)
}

fn es_digest_malo(msg: &str) -> bool {
    let m = msg.to_ascii_lowercase();
    msg.starts_with("HTTP 400") && (m.contains("digest") || m.contains("crc32c") || m.contains("checksum"))
}

fn sesion_a_io(e: Corte) -> Corte {
    match e {
        Corte::SesionMuerta(m) => Corte::Fin("io", m),
        o => o,
    }
}

async fn subida_simple(ctx: &Ctx, ctl: &mut Ctl, url: &str, loc: &Local) -> Result<Resp, Corte> {
    let sp = Spec {
        metodo: reqwest::Method::PUT,
        url,
        cab: vec![
            ("Content-Type", "application/octet-stream".to_string()),
            ("Content-MD5", loc.md5_b64.clone()),
            ("x-goog-hash", format!("crc32c={}", hash::b64_crc32c(loc.hashes.crc32c))),
        ],
        cuerpo: loc.datos.clone(),
        acepta_308: false,
        una_vez: false,
    };
    let r = ejecutar(ctx, ctl, &sp).await.map_err(sesion_a_io)?;
    acreditar(ctx, ctl, loc.total);
    Ok(r)
}

enum Offset {
    Completo(Resp),
    En(u64),
}

async fn consultar_offset(ctx: &Ctx, ctl: &mut Ctl, sesion: &str, total: u64) -> Result<Offset, Corte> {
    let sp = Spec {
        metodo: reqwest::Method::PUT,
        url: sesion,
        cab: vec![("Content-Range", format!("bytes */{total}"))],
        cuerpo: Bytes::new(),
        acepta_308: true,
        una_vez: false,
    };
    let r = ejecutar(ctx, ctl, &sp).await?;
    if r.estado == 308 {
        Ok(Offset::En(offset_de_range(r.range.as_deref())))
    } else {
        Ok(Offset::Completo(r))
    }
}

async fn abrir_sesion(ctx: &Ctx, ctl: &mut Ctl, url: &str) -> Result<String, Corte> {
    let t = Instant::now();
    let sp = Spec {
        metodo: reqwest::Method::POST,
        url,
        cab: vec![
            ("x-goog-resumable", "start".to_string()),
            ("Content-Type", "application/octet-stream".to_string()),
        ],
        cuerpo: Bytes::new(),
        acepta_308: false,
        una_vez: false,
    };
    let r = ejecutar(ctx, ctl, &sp).await;
    ctx.stats.sumar(M_SESION, t);
    match r {
        Ok(resp) => resp
            .location
            .ok_or_else(|| Corte::Fin("sesion", "GCS no devolvió Location al abrir la sesión resumable".into())),
        Err(Corte::SesionMuerta(m)) => Err(Corte::Fin("sesion", format!("no se pudo abrir la sesión: {m}"))),
        Err(e) => Err(e),
    }
}

/// Subida resumable: reanuda por offset tras cada fallo y rehace la sesión si
/// muere (404/410).
async fn subida_resumable(
    ctx: &Ctx,
    ctl: &mut Ctl,
    url: &str,
    ruta: &str,
    loc: &Local,
) -> Result<Resp, Corte> {
    let total = loc.total;
    let mut sesiones = 0u32;
    'sesion: loop {
        sesiones += 1;
        if sesiones > 6 {
            return Err(Corte::Fin("sesion", "la sesión resumable murió 6 veces seguidas".into()));
        }
        let sesion = abrir_sesion(ctx, ctl, url).await?;
        let mut offset = 0u64;
        loop {
            let (rango, cuerpo) = if total == 0 {
                ("bytes */0".to_string(), Bytes::new())
            } else {
                let len = (total - offset).min(ctx.cfg.chunk_bytes);
                let t = Instant::now();
                let r = ruta.to_string();
                let ini = offset;
                let leido = bloqueante(ctx, move || leer_trozo(&r, ini, len as usize)).await?;
                ctx.stats.sumar(M_DISCO, t);
                let datos = leido.map_err(|e| Corte::Fin("io", format!("no se puede leer {ruta}: {e}")))?;
                (format!("bytes {offset}-{}/{total}", offset + len - 1), Bytes::from(datos))
            };
            let sp = Spec {
                metodo: reqwest::Method::PUT,
                url: &sesion,
                cab: vec![("Content-Range", rango)],
                cuerpo,
                acepta_308: true,
                una_vez: true,
            };
            let confirmar = match ejecutar(ctx, ctl, &sp).await {
                Ok(resp) if resp.estado == 308 => {
                    if total == 0 {
                        return Err(Corte::Fin("sesion", "GCS pide más datos de un objeto vacío".into()));
                    }
                    offset = offset_de_range(resp.range.as_deref());
                    acreditar(ctx, ctl, offset);
                    offset >= total
                }
                Ok(resp) => {
                    acreditar(ctx, ctl, total);
                    return Ok(resp);
                }
                Err(Corte::Transitorio(_)) => {
                    pausa(ctx, ctl).await?;
                    true
                }
                Err(Corte::SesionMuerta(_)) => continue 'sesion,
                Err(e) => return Err(e),
            };
            if confirmar {
                match consultar_offset(ctx, ctl, &sesion, total).await {
                    Ok(Offset::Completo(resp)) => {
                        acreditar(ctx, ctl, total);
                        return Ok(resp);
                    }
                    Ok(Offset::En(n)) => {
                        if total > 0 && n >= total {
                            return Err(Corte::Fin(
                                "sesion",
                                "el servidor tiene todos los bytes pero no cierra el objeto".into(),
                            ));
                        }
                        offset = n;
                        acreditar(ctx, ctl, offset);
                    }
                    Err(Corte::SesionMuerta(_)) => continue 'sesion,
                    Err(e) => return Err(e),
                }
            }
        }
    }
}

/// HEAD del objeto (sin query): hashes y generación que declara el servidor.
/// Solo con OAuth (`Authorization: Bearer` la pone `ejecutar` con `token_fn`).
/// Con URL firmada no hay credencial de lectura (la firma del backend vale solo
/// para el método que firmó, PUT/POST) y no se hace ninguna petición: error
/// `hash` explícito, nunca se da por bueno.
async fn hashes_por_head(
    ctx: &Ctx,
    ctl: &mut Ctl,
    url: &str,
) -> Result<(hash::HashesServidor, Option<String>), Corte> {
    if !ctx.cfg.oauth {
        return Err(Corte::Fin(
            "hash",
            "no se puede verificar el objeto: con URL firmada no hay credencial de lectura \
             (HEAD/GET) y la respuesta no trajo x-goog-hash; el backend debería firmar también \
             una URL de lectura"
                .into(),
        ));
    }
    let base = url.split('?').next().unwrap_or(url);
    let sp = Spec {
        metodo: reqwest::Method::HEAD,
        url: base,
        cab: Vec::new(),
        cuerpo: Bytes::new(),
        acepta_308: false,
        una_vez: false,
    };
    match ejecutar(ctx, ctl, &sp).await {
        Ok(head) => Ok((hash::parse_x_goog_hash(&head.goog_hash), head.generacion)),
        Err(Corte::Cancelado) => Err(Corte::Cancelado),
        Err(Corte::YaExiste) => Err(Corte::Fin("hash", "HEAD del objeto devolvió 412".into())),
        Err(e) => Err(sesion_a_io(e)),
    }
}

/// Compara el hash del servidor con el local. `Ok(None)` = verificado;
/// `Ok(Some((campo, generacion)))` = mismatch. Si la respuesta no trae
/// `x-goog-hash`, se consulta el objeto con HEAD (solo OAuth; con URL firmada es error `hash`).
async fn verificar(
    ctx: &Ctx,
    ctl: &mut Ctl,
    url: &str,
    loc: &Local,
    resp: &Resp,
) -> Result<Option<(&'static str, Option<String>)>, Corte> {
    let srv = hash::parse_x_goog_hash(&resp.goog_hash);
    let mut gener = resp.generacion.clone();
    let mut v = hash::coincide(&loc.hashes, &srv);
    if v == Veredicto::SinHash {
        let (s, g) = hashes_por_head(ctx, ctl, url).await?;
        gener = g.or(gener);
        v = hash::coincide(&loc.hashes, &s);
    }
    match v {
        Veredicto::Ok => Ok(None),
        Veredicto::Mismatch { campo } => Ok(Some((campo, gener))),
        Veredicto::SinHash => Err(Corte::Fin(
            "hash",
            "el servidor no devolvió x-goog-hash ni en el HEAD: no se puede verificar el objeto".into(),
        )),
    }
}

async fn obtener_url(ctx: &Ctx, remote: &str, size: u64, sobre: bool) -> Result<String, Corte> {
    let env = ctx.env.clone();
    let r = remote.to_string();
    let t = Instant::now();
    let v = bloqueante(ctx, move || env.url(&r, size, sobre)).await;
    ctx.stats.sumar(M_SESION, t);
    v?.map_err(|m| Corte::Fin("sesion", format!("url_fn: {m}")))
}

async fn subir_inner(ctx: &Ctx, ctl: &mut Ctl, item: &Item) -> Result<Resultado, Corte> {
    let (ruta, remote, size, sobre, _urgente) = item;
    let simple = ctx.cfg.oauth && *size < ctx.cfg.simple_max_bytes;
    let t = Instant::now();
    let (r, sz) = (ruta.clone(), *size);
    let loc = bloqueante(ctx, move || preparar_local(&r, sz, simple)).await?;
    ctx.stats.sumar(M_DISCO, t);
    let loc = loc.map_err(|m| Corte::Fin("io", m))?;

    let mut sobrescribir = *sobre;
    let mut generacion: Option<String> = None;
    let mut tras_mismatch = false;
    let mut ultimo = String::new();
    // 1 subida inicial + `reintentos_hash` subidas completas si el hash no coincide.
    for _ in 0..=ctx.cfg.reintentos_hash {
        let url = obtener_url(ctx, remote, *size, sobrescribir).await?;
        let url = match &generacion {
            Some(g) => con_generacion(&url, g),
            None => url,
        };
        let res = if simple {
            subida_simple(ctx, ctl, &url, &loc).await
        } else {
            subida_resumable(ctx, ctl, &url, ruta, &loc).await
        };
        let resp = match res {
            Ok(r) => r,
            Err(Corte::YaExiste) => {
                if tras_mismatch {
                    return Err(Corte::Fin(
                        "hash",
                        format!("objeto corrupto en el bucket ({ultimo}) y no se pudo sobrescribir (412/403 sin permiso de borrado)"),
                    ));
                }
                // 412 (o 403 sin permiso de borrado): el objeto ya existe. Solo vale como verificado si su hash
                // coincide con el local (regla de hash); si no, fallo del fichero.
                let (srv, _) = hashes_por_head(ctx, ctl, &url).await?;
                return match hash::coincide(&loc.hashes, &srv) {
                    Veredicto::Ok => Ok(Resultado::YaExiste),
                    Veredicto::Mismatch { campo } => Err(Corte::Fin(
                        "hash",
                        format!("el objeto ya existía en el bucket (412) con {campo} distinto al local"),
                    )),
                    Veredicto::SinHash => Err(Corte::Fin(
                        "hash",
                        "el objeto ya existía (412) y el servidor no devolvió x-goog-hash: no se puede verificar".into(),
                    )),
                };
            }
            Err(Corte::Fin("rechazado_403", m)) if tras_mismatch => {
                return Err(Corte::Fin(
                    "hash",
                    format!("objeto corrupto en el bucket ({ultimo}) y estas credenciales no pueden sobrescribirlo: {m}"),
                ));
            }
            Err(Corte::Fin("io", m)) if es_digest_malo(&m) => {
                // El servidor rechazó el cuerpo por integridad: no creó nada.
                ultimo = format!("rechazo de integridad del servidor: {m}");
                continue;
            }
            Err(e) => return Err(e),
        };
        match verificar(ctx, ctl, &url, &loc, &resp).await? {
            None => return Ok(Resultado::Hecho { md5_b64: loc.md5_b64.clone() }),
            Some((campo, gener)) => {
                ultimo = format!("{campo} distinto");
                if !ctx.cfg.oauth {
                    return Err(Corte::Fin(
                        "hash",
                        format!("el {campo} del servidor no coincide con el local (URL firmada: no se puede sobrescribir)"),
                    ));
                }
                match gener {
                    Some(g) => {
                        generacion = Some(g);
                        sobrescribir = true;
                        tras_mismatch = true;
                    }
                    None => {
                        return Err(Corte::Fin(
                            "hash",
                            format!("el {campo} del servidor no coincide y no devolvió x-goog-generation"),
                        ))
                    }
                }
            }
        }
    }
    Err(Corte::Fin(
        "hash",
        format!("hash distinto tras {} reintentos de subida completa ({ultimo})", ctx.cfg.reintentos_hash),
    ))
}

/// Sube un fichero con todas las garantías (ver `Resultado`). Nunca devuelve
/// `Hecho` sin que el hash del servidor coincida con el local.
pub async fn subir_fichero(ctx: &Ctx, item: &Item) -> Resultado {
    let mut ctl = Ctl::nuevo(&item.1);
    match subir_inner(ctx, &mut ctl, item).await {
        Ok(r) => r,
        Err(Corte::Cancelado) => Resultado::Cancelado,
        Err(Corte::Fin(tipo, motivo)) => Resultado::Fallo { tipo, motivo },
        Err(Corte::YaExiste) => Resultado::YaExiste,
        Err(Corte::SesionMuerta(m)) => Resultado::Fallo { tipo: "sesion", motivo: m },
        Err(Corte::Transitorio(m)) => Resultado::Fallo { tipo: "io", motivo: m },
    }
}
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn espera_tope_30s() {
        let mut s = 12345u64;
        for i in [5u32, 6, 10, 100, u32::MAX] {
            let d = espera(i, &mut s);
            assert!(d <= Duration::from_secs(30), "intento {i}: {d:?}");
            assert!(d >= Duration::from_secs(15), "intento {i}: {d:?}");
        }
    }

    #[test]
    fn jitter_entre_50_y_100() {
        let mut s = 42u64;
        for intento in 0..6u32 {
            let bruto = (1_000u64 << intento).min(30_000);
            for _ in 0..200 {
                let ms = espera(intento, &mut s).as_millis() as u64;
                assert!((bruto / 2..=bruto).contains(&ms), "intento {intento}: {ms}");
            }
        }
    }

    #[test]
    fn determinista_con_semilla() {
        let (mut a, mut b) = (7u64, 7u64);
        for i in 0..10u32 {
            assert_eq!(espera(i, &mut a), espera(i, &mut b));
        }
        let mut cero = 0u64;
        let _ = espera(0, &mut cero);
        assert_ne!(cero, 0);
    }

    #[test]
    fn tabla_de_codigos() {
        assert_eq!(clasificar(None, true), Clase::Reintentar);
        assert_eq!(clasificar(None, false), Clase::Reintentar);
        assert_eq!(clasificar(Some(200), true), Clase::Reintentar);
        for c in [408u16, 429, 500, 502, 503, 504, 599] {
            assert_eq!(clasificar(Some(c), false), Clase::Reintentar, "{c}");
        }
        assert_eq!(clasificar(Some(401), false), Clase::Auth);
        assert_eq!(clasificar(Some(403), false), Clase::Auth);
        assert_eq!(clasificar(Some(412), false), Clase::YaExiste);
        assert_eq!(clasificar(Some(404), false), Clase::SesionMuerta);
        assert_eq!(clasificar(Some(410), false), Clase::SesionMuerta);
        for c in [400u16, 405, 409, 413, 416, 418] {
            assert_eq!(clasificar(Some(c), false), Clase::Fatal, "{c}");
        }
    }
}

#[cfg(test)]
mod tests_red {
    use super::*;
    use crate::subida::falso::*;
    use std::sync::Mutex;

    fn rt() -> tokio::runtime::Runtime {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .build()
            .expect("runtime")
    }

    fn ctx_con(env: Arc<EntornoFalso>, cfg: Config) -> Ctx {
        Ctx {
            http: crear_cliente().expect("cliente"),
            env,
            stop: Arc::new(AtomicBool::new(false)),
            cfg,
            stats: Arc::new(Stats::default()),
        }
    }

    /// PUT simple (oauth, < 1 MiB), esperas /1000.
    fn cfg_simple() -> Config {
        let mut c = Config::nueva(true, 16, 1 << 20, 3);
        c.escala_espera = 1000;
        c
    }

    /// Siempre resumable, trozos de 16 bytes.
    fn cfg_resumable(oauth: bool) -> Config {
        let mut c = Config::nueva(oauth, 16, 0, 3);
        c.escala_espera = 1000;
        c
    }

    fn item(ruta: &str, remote: &str, size: usize) -> Item {
        (ruta.to_string(), remote.to_string(), size as u64, false, false)
    }

    fn md5_de(d: &[u8]) -> String {
        hash::b64_md5(&hash::hashes_bytes(d).0)
    }

    #[test]
    fn put_simple_con_hash_correcto_y_cabeceras() {
        let datos = b"hola mundo".to_vec();
        let ruta = fichero_tmp(&datos);
        let srv = Servidor::nuevo(|p, _| Accion::Responder(200, cab_hash(&p.cuerpo)));
        let env = EntornoFalso::nuevo(&srv.base);
        let ctx = ctx_con(env, cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "a/b.txt", datos.len())));
        assert_eq!(r, Resultado::Hecho { md5_b64: md5_de(&datos) });
        let ps = srv.peticiones();
        assert_eq!(ps.len(), 1);
        assert_eq!(ps[0].metodo, "PUT");
        assert!(ps[0].ruta.contains("ifGenerationMatch=0"), "{}", ps[0].ruta);
        assert_eq!(ps[0].cab("content-md5"), Some(md5_de(&datos).as_str()));
        assert!(ps[0].cab("x-goog-hash").unwrap_or("").starts_with("crc32c="));
        assert_eq!(ps[0].cab("authorization"), Some("Bearer t"));
        assert_eq!(ps[0].cuerpo, datos);
    }

    #[test]
    fn reintenta_5xx_sin_tope_y_luego_ok() {
        let datos = b"0123456789".to_vec();
        let ruta = fichero_tmp(&datos);
        let srv = Servidor::nuevo(|p, n| {
            if n < 3 {
                resp(503, &[])
            } else {
                Accion::Responder(200, cab_hash(&p.cuerpo))
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", datos.len())));
        assert!(matches!(r, Resultado::Hecho { .. }), "{r:?}");
        assert!(ctx.stats.causas[C_5XX].load(Ordering::Relaxed) >= 3);
        assert!(ctx.stats.congestion.load(Ordering::Relaxed) >= 3);
        assert!(ctx.stats.reintentos.load(Ordering::Relaxed) >= 3);
    }

    #[test]
    fn reintenta_429_y_cuenta_congestion() {
        let datos = b"abc".to_vec();
        let ruta = fichero_tmp(&datos);
        let srv = Servidor::nuevo(|p, n| {
            if n < 2 {
                resp(429, &[])
            } else {
                Accion::Responder(200, cab_hash(&p.cuerpo))
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", datos.len())));
        assert!(matches!(r, Resultado::Hecho { .. }), "{r:?}");
        assert!(ctx.stats.causas[C_429].load(Ordering::Relaxed) >= 2);
        assert!(ctx.stats.congestion.load(Ordering::Relaxed) >= 2);
    }

    #[test]
    fn http_412_con_hash_igual_es_ya_existe() {
        let ruta = fichero_tmp(b"zz");
        let srv = Servidor::nuevo(|p, _| {
            if p.metodo == "HEAD" {
                Accion::Responder(200, cab_hash(b"zz"))
            } else {
                resp(412, &[])
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert_eq!(r, Resultado::YaExiste);
        assert!(srv.peticiones().iter().any(|p| p.metodo == "HEAD"));
    }

    const XML_403_DELETE: &str = "<?xml version='1.0' encoding='UTF-8'?><Error><Code>AccessDenied</Code><Message>Access denied.</Message><Details>sa@p.iam.gserviceaccount.com does not have storage.objects.delete access to the Google Cloud Storage object.</Details></Error>";

    #[test]
    fn http_403_sin_delete_con_hash_igual_es_ya_existe_sin_refrescar() {
        let ruta = fichero_tmp(b"zz");
        let srv = Servidor::nuevo(|p, _| {
            if p.metodo == "HEAD" {
                Accion::Responder(200, cab_hash(b"zz"))
            } else {
                Accion::ResponderCuerpo(403, XML_403_DELETE.into())
            }
        });
        let env = EntornoFalso::nuevo(&srv.base);
        let ctx = ctx_con(env.clone(), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert_eq!(r, Resultado::YaExiste);
        assert_eq!(env.recovers.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn http_403_sin_delete_con_hash_distinto_es_fallo_hash() {
        let ruta = fichero_tmp(b"zz");
        let srv = Servidor::nuevo(|p, _| {
            if p.metodo == "HEAD" {
                Accion::Responder(200, cab_hash(b"otro"))
            } else {
                Accion::ResponderCuerpo(403, XML_403_DELETE.into())
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert!(matches!(r, Resultado::Fallo { tipo: "hash", .. }), "{r:?}");
    }

    #[test]
    fn http_412_con_hash_distinto_es_fallo_hash() {
        let ruta = fichero_tmp(b"zz");
        let srv = Servidor::nuevo(|p, _| {
            if p.metodo == "HEAD" {
                Accion::Responder(200, cab_hash(b"otro contenido"))
            } else {
                resp(412, &[])
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert!(matches!(r, Resultado::Fallo { tipo: "hash", .. }), "{r:?}");
    }

    #[test]
    fn http_412_sin_hash_en_head_es_fallo_hash() {
        let ruta = fichero_tmp(b"zz");
        let srv = Servidor::nuevo(|_, _| resp(412, &[]));
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert!(matches!(r, Resultado::Fallo { tipo: "hash", .. }), "{r:?}");
    }

    #[test]
    fn http_403_refresca_una_vez_y_continua() {
        let datos = b"qq".to_vec();
        let ruta = fichero_tmp(&datos);
        let srv = Servidor::nuevo(|p, n| {
            if n == 0 {
                resp(403, &[])
            } else {
                Accion::Responder(200, cab_hash(&p.cuerpo))
            }
        });
        let env = EntornoFalso::nuevo(&srv.base);
        let ctx = ctx_con(env.clone(), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert!(matches!(r, Resultado::Hecho { .. }), "{r:?}");
        assert_eq!(env.recovers.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn http_403_doble_es_rechazado() {
        let ruta = fichero_tmp(b"qq");
        let srv = Servidor::nuevo(|_, _| resp(403, &[]));
        let env = EntornoFalso::nuevo(&srv.base);
        let ctx = ctx_con(env.clone(), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert!(matches!(r, Resultado::Fallo { tipo: "rechazado_403", .. }), "{r:?}");
        assert_eq!(env.recovers.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn hash_incorrecto_reintenta_3_veces_y_da_error_hash() {
        let datos = b"contenido".to_vec();
        let ruta = fichero_tmp(&datos);
        let srv = Servidor::nuevo(|_, _| Accion::Responder(200, cab_hash(b"otra cosa")));
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", datos.len())));
        assert!(matches!(r, Resultado::Fallo { tipo: "hash", .. }), "{r:?}");
        let ps = srv.peticiones();
        assert_eq!(ps.len(), 4, "1 subida + 3 reintentos completos");
        assert!(ps[0].ruta.contains("ifGenerationMatch=0"));
        assert!(ps[1].ruta.contains("ifGenerationMatch=5"), "{}", ps[1].ruta);
        assert!(!ps[1].ruta.contains("ifGenerationMatch=0"));
    }

    #[test]
    fn sin_x_goog_hash_oauth_usa_head() {
        let datos = b"datos".to_vec();
        let ruta = fichero_tmp(&datos);
        let d2 = datos.clone();
        let srv = Servidor::nuevo(move |p, _| {
            if p.metodo == "HEAD" {
                Accion::Responder(200, cab_hash(&d2))
            } else {
                resp(200, &[])
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", datos.len())));
        assert_eq!(r, Resultado::Hecho { md5_b64: md5_de(&datos) });
        let ps = srv.peticiones();
        let head = ps.iter().find(|p| p.metodo == "HEAD").expect("HEAD enviado");
        assert_eq!(head.cab("authorization"), Some("Bearer t"));
    }

    #[test]
    fn sin_x_goog_hash_firmada_no_hace_head_y_es_error_hash() {
        // Sin credencial de lectura no se intenta ningún HEAD: error `hash` explícito.
        let datos = b"datos".to_vec();
        let ruta = fichero_tmp(&datos);
        let srv = Servidor::nuevo(|p, _| {
            if p.metodo == "POST" {
                let loc = format!("http://{}/sesion", p.cab("host").unwrap_or(""));
                resp(200, &[("Location", loc.as_str())])
            } else {
                resp(200, &[])
            }
        });
        let ctx = ctx_con(EntornoFalso::sin_credencial(&srv.base), cfg_resumable(false));
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", datos.len())));
        match r {
            Resultado::Fallo { tipo: "hash", motivo } => {
                assert!(motivo.contains("URL firmada"), "{motivo}")
            }
            o => panic!("{o:?}"),
        }
        assert!(srv.peticiones().iter().all(|p| p.metodo != "HEAD"));
    }

    #[test]
    fn http_412_firmada_sin_credencial_es_fallo_hash_sin_head() {
        let ruta = fichero_tmp(b"zz");
        let srv = Servidor::nuevo(|p, _| {
            if p.metodo == "POST" {
                resp(412, &[])
            } else {
                resp(200, &[])
            }
        });
        let ctx = ctx_con(EntornoFalso::sin_credencial(&srv.base), cfg_resumable(false));
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 2)));
        assert!(matches!(r, Resultado::Fallo { tipo: "hash", .. }), "{r:?}");
        assert!(srv.peticiones().iter().all(|p| p.metodo != "HEAD"));
    }

    #[test]
    fn reanuda_por_offset_tras_cortar_la_conexion() {
        let datos: Vec<u8> = (0..40u8).collect();
        let ruta = fichero_tmp(&datos);
        let guardado = Arc::new(Mutex::new(Vec::<u8>::new()));
        let cortado = Arc::new(AtomicBool::new(false));
        let (g, c) = (guardado.clone(), cortado.clone());
        let srv = Servidor::nuevo(move |p, _| {
            let mut st = g.lock().expect("estado");
            if p.metodo == "POST" {
                let loc = format!("http://{}/sesion", p.cab("host").unwrap_or(""));
                return resp(200, &[("Location", loc.as_str())]);
            }
            let cr = p.cab("content-range").unwrap_or("").to_string();
            if cr.starts_with("bytes */") {
                if st.len() == 40 {
                    return Accion::Responder(200, cab_hash(&st));
                }
                if st.is_empty() {
                    return resp(308, &[]);
                }
                let rango = format!("bytes=0-{}", st.len() - 1);
                return resp(308, &[("Range", rango.as_str())]);
            }
            let ini: usize = cr
                .trim_start_matches("bytes ")
                .split('-')
                .next()
                .and_then(|s| s.parse().ok())
                .unwrap_or(999);
            if ini != st.len() {
                return resp(400, &[]);
            }
            if ini == 16 && !c.swap(true, Ordering::SeqCst) {
                st.extend_from_slice(&p.cuerpo[..8]);
                return Accion::Cortar;
            }
            st.extend_from_slice(&p.cuerpo);
            if st.len() == 40 {
                Accion::Responder(200, cab_hash(&st))
            } else {
                let rango = format!("bytes=0-{}", st.len() - 1);
                resp(308, &[("Range", rango.as_str())])
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_resumable(true));
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 40)));
        assert_eq!(r, Resultado::Hecho { md5_b64: md5_de(&datos) });
        assert_eq!(*guardado.lock().expect("estado"), datos);
        let rangos: Vec<String> = srv
            .peticiones()
            .iter()
            .filter_map(|p| p.cab("content-range").map(String::from))
            .collect();
        assert!(rangos.contains(&"bytes 24-39/40".to_string()), "{rangos:?}");
        assert_eq!(ctx.stats.bytes.load(Ordering::Relaxed), 40);
    }

    #[test]
    fn fichero_vacio_cierra_con_bytes_asterisco_0() {
        let ruta = fichero_tmp(b"");
        let srv = Servidor::nuevo(|p, _| {
            if p.metodo == "POST" {
                let loc = format!("http://{}/sesion", p.cab("host").unwrap_or(""));
                resp(200, &[("Location", loc.as_str())])
            } else {
                Accion::Responder(200, cab_hash(b""))
            }
        });
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_resumable(true));
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 0)));
        assert_eq!(r, Resultado::Hecho { md5_b64: "1B2M2Y8AsgTpgAmY7PhCfg==".to_string() });
        let ps = srv.peticiones();
        let put = ps.iter().find(|p| p.metodo == "PUT").expect("hubo PUT");
        assert_eq!(put.cab("content-range"), Some("bytes */0"));
    }

    #[test]
    fn cancelar_durante_el_backoff_devuelve_en_menos_de_500ms() {
        let ruta = fichero_tmp(b"abc");
        let srv = Servidor::nuevo(|_, _| resp(503, &[]));
        // Backoff real (sin escalar): la primera espera dura 0,5-1 s.
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), Config::nueva(true, 16, 1 << 20, 3));
        let stop = ctx.stop.clone();
        let parado = Arc::new(Mutex::new(None::<Instant>));
        let p2 = parado.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(200));
            *p2.lock().expect("parado") = Some(Instant::now());
            stop.store(true, Ordering::SeqCst);
        });
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 3)));
        let fin = Instant::now();
        assert_eq!(r, Resultado::Cancelado);
        let t_stop = parado.lock().expect("parado").expect("se pidió parar");
        assert!(fin.duration_since(t_stop) < Duration::from_millis(500), "tardó {:?}", fin.duration_since(t_stop));
    }

    #[test]
    fn tamano_distinto_al_declarado_es_error_visible() {
        let ruta = fichero_tmp(b"12345");
        let srv = Servidor::nuevo(|_, _| resp(200, &[]));
        let ctx = ctx_con(EntornoFalso::nuevo(&srv.base), cfg_simple());
        let r = rt().block_on(subir_fichero(&ctx, &item(&ruta, "x", 9)));
        assert!(matches!(r, Resultado::Fallo { tipo: "io", .. }), "{r:?}");
        assert!(srv.peticiones().is_empty());
    }

    #[test]
    fn con_generacion_sustituye_la_precondicion() {
        assert_eq!(con_generacion("http://h/o?ifGenerationMatch=0", "7"), "http://h/o?ifGenerationMatch=7");
        assert_eq!(con_generacion("http://h/o", "7"), "http://h/o?ifGenerationMatch=7");
        assert_eq!(con_generacion("http://h/o?a=1&ifGenerationMatch=0", "7"), "http://h/o?a=1&ifGenerationMatch=7");
    }

    #[test]
    fn offset_de_range_tabla() {
        assert_eq!(offset_de_range(None), 0);
        assert_eq!(offset_de_range(Some("bytes=0-23")), 24);
        assert_eq!(offset_de_range(Some("bytes=0-0")), 1);
        assert_eq!(offset_de_range(Some("basura")), 0);
    }
}
