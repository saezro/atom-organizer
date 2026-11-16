//! Subida GCS en Rust (Task 6): hash, orden PB a PB, AIMD, cliente reqwest y
//! el puente pyo3 `subir_lote`.
//!
//! `correr` es el coordinador puro (sin pyo3, testeable con el servidor falso);
//! `subir_lote` solo conecta los callbacks Python (GIL liberado durante toda la
//! red, adquirido solo para cada callback).

pub mod aimd;
pub mod cliente;
pub mod hash;
pub mod orden;

#[cfg(test)]
pub(crate) mod falso;

use aimd::Aimd;
use cliente::{crear_cliente, subir_fichero, Config, Ctx, Entorno, Resultado, Stats};
use orden::{ordenar, Item};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

/// Refresco del token cacheado en Rust.
const TOKEN_TTL: Duration = Duration::from_secs(300);
const SONDEO_STOP: Duration = Duration::from_millis(250);
const PERIODO_PROGRESO: Duration = Duration::from_secs(1);
const BUCLE: Duration = Duration::from_millis(100);

fn bloquear<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

// ---------------------------------------------------------------------------
// Eventos
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Default)]
pub struct Progreso {
    pub bytes_done: u64,
    pub bytes_total: u64,
    pub files_done: u64,
    pub files_total: u64,
    pub elapsed: f64,
    pub eta: Option<f64>,
    pub mbps: f64,
    pub files_per_s: f64,
    pub reintentos: u64,
    pub hilos_activos: usize,
    pub hilos_max: usize,
    pub fichero_actual: String,
    pub fallidas: u64,
    pub pb_actual: String,
    /// disco, red, sesión, espera, token (s).
    pub seg: [f64; 5],
    /// 429, 5xx, auth, http, red.
    pub causas: [u64; 5],
}

#[derive(Debug, Clone, Default)]
pub struct Hecho {
    pub subidos: u64,
    pub saltados_412: u64,
    pub bytes_enviados: u64,
    pub elapsed: f64,
    pub reintentos: u64,
    pub cancelado: bool,
    pub abortado: Option<String>,
    pub fallidos: Vec<(String, String)>,
    pub md5: Vec<(String, String)>,
}

#[derive(Debug, Clone)]
pub enum Evento {
    Progreso(Box<Progreso>),
    PbListo { pb: String, ficheros: u64, bytes: u64 },
    Error { remote: String, tipo: String, motivo: String },
    Hecho(Box<Hecho>),
}

// ---------------------------------------------------------------------------
// Coordinador
// ---------------------------------------------------------------------------

pub struct Params {
    pub items: Vec<Item>,
    pub prefijo: String,
    pub oauth: bool,
    pub hilos_min: usize,
    pub hilos_max: usize,
    pub chunk_bytes: u64,
    pub simple_max_bytes: u64,
    pub reintentos_hash: u32,
    /// Divisor del backoff (1 en producción).
    pub escala_espera: u32,
}

#[derive(Default)]
struct Acum {
    fallidos: Vec<(String, String)>,
    md5: Vec<(String, String)>,
}

#[derive(Default)]
struct Comp {
    subidos: AtomicU64,
    saltados: AtomicU64,
    fallidas: AtomicU64,
    activos: AtomicUsize,
    pico: AtomicUsize,
    deuda: AtomicUsize,
    rechazos_403: AtomicU32,
    cancelado: AtomicBool,
    acum: Mutex<Acum>,
    actual: Mutex<String>,
    abortado: Mutex<Option<String>>,
}

/// Datos de un PB en curso compartidos por sus trabajadores.
struct Pb {
    nombre: String,
    lista: Vec<Item>,
    siguiente: AtomicUsize,
    /// Falló un urgente: no se intenta el resto del PB.
    cortado: AtomicBool,
    /// Ficheros del PB que no quedaron verificados (fallidos o no intentados).
    sin_verificar: AtomicU64,
}

struct Trab {
    ctx: Ctx,
    comp: Arc<Comp>,
    sem: Arc<Semaphore>,
    pb: Arc<Pb>,
    tx: Sender<Evento>,
}

fn liberar(permiso: OwnedSemaphorePermit, deuda: &AtomicUsize) {
    let debe = deuda
        .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |d| if d > 0 { Some(d - 1) } else { None })
        .is_ok();
    if debe {
        permiso.forget();
    } else {
        drop(permiso);
    }
}

async fn trabajador(t: Trab) {
    loop {
        let permiso = match t.sem.clone().acquire_owned().await {
            Ok(p) => p,
            Err(_) => break,
        };
        if t.ctx.stop.load(Ordering::Relaxed) {
            liberar(permiso, &t.comp.deuda);
            break;
        }
        let i = t.pb.siguiente.fetch_add(1, Ordering::SeqCst);
        let Some(item) = t.pb.lista.get(i) else {
            liberar(permiso, &t.comp.deuda);
            break;
        };
        if t.pb.cortado.load(Ordering::SeqCst) {
            t.pb.sin_verificar.fetch_add(1, Ordering::SeqCst);
            t.comp.fallidas.fetch_add(1, Ordering::Relaxed);
            bloquear(&t.comp.acum).fallidos.push((
                item.1.clone(),
                format!("no intentado: falló un fichero urgente de {}", t.pb.nombre),
            ));
            liberar(permiso, &t.comp.deuda);
            continue;
        }
        let ahora = t.comp.activos.fetch_add(1, Ordering::SeqCst) + 1;
        t.comp.pico.fetch_max(ahora, Ordering::SeqCst);
        *bloquear(&t.comp.actual) = item.1.clone();
        let (ctx, it) = (t.ctx.clone(), item.clone());
        let res = match tokio::spawn(async move { subir_fichero(&ctx, &it).await }).await {
            Ok(r) => r,
            Err(e) => Resultado::Fallo { tipo: "panic", motivo: format!("pánico en la subida: {e}") },
        };
        t.comp.activos.fetch_sub(1, Ordering::SeqCst);
        tratar(&t, item, res);
        liberar(permiso, &t.comp.deuda);
    }
}

fn tratar(t: &Trab, item: &Item, res: Resultado) {
    match res {
        Resultado::Hecho { md5_b64 } => {
            t.comp.subidos.fetch_add(1, Ordering::Relaxed);
            t.comp.rechazos_403.store(0, Ordering::SeqCst);
            bloquear(&t.comp.acum).md5.push((item.1.clone(), md5_b64));
        }
        Resultado::YaExiste => {
            t.comp.saltados.fetch_add(1, Ordering::Relaxed);
            t.comp.rechazos_403.store(0, Ordering::SeqCst);
        }
        Resultado::Cancelado => {
            t.pb.sin_verificar.fetch_add(1, Ordering::SeqCst);
        }
        Resultado::Fallo { tipo, motivo } => {
            t.comp.fallidas.fetch_add(1, Ordering::Relaxed);
            t.pb.sin_verificar.fetch_add(1, Ordering::SeqCst);
            bloquear(&t.comp.acum).fallidos.push((item.1.clone(), motivo.clone()));
            let _ = t.tx.send(Evento::Error { remote: item.1.clone(), tipo: tipo.to_string(), motivo });
            if item.4 {
                t.pb.cortado.store(true, Ordering::SeqCst);
            }
            if tipo == "rechazado_403" {
                if t.comp.rechazos_403.fetch_add(1, Ordering::SeqCst) + 1 >= 3 {
                    *bloquear(&t.comp.abortado) =
                        Some("3 ficheros seguidos rechazados por el servidor (403): se aborta la subida".to_string());
                    t.ctx.stop.store(true, Ordering::SeqCst);
                }
            } else {
                t.comp.rechazos_403.store(0, Ordering::SeqCst);
            }
        }
    }
}

struct Coord<'a> {
    ctx: Ctx,
    comp: Arc<Comp>,
    sem: Arc<Semaphore>,
    aimd: Aimd,
    limite_sem: usize,
    rx: Receiver<Evento>,
    parar_py: &'a mut dyn FnMut() -> bool,
    emitir: &'a mut dyn FnMut(Evento) -> bool,
    inicio: Instant,
    ult_progreso: Instant,
    ult_ventana: Instant,
    ult_sondeo: Instant,
    bytes_total: u64,
    files_total: u64,
    pb_actual: String,
}

impl Coord<'_> {
    fn emitir(&mut self, ev: Evento) {
        if !(self.emitir)(ev) {
            // El callback Python lanzó una excepción: se para y se propaga.
            self.ctx.stop.store(true, Ordering::SeqCst);
        }
    }

    fn drenar(&mut self) {
        while let Ok(ev) = self.rx.try_recv() {
            self.emitir(ev);
        }
    }

    fn ajustar_semaforo(&mut self, nuevo: usize) {
        if nuevo > self.limite_sem {
            let diff = nuevo - self.limite_sem;
            let pagado = self
                .comp
                .deuda
                .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |d| Some(d.saturating_sub(diff)))
                .unwrap_or(0)
                .min(diff);
            self.sem.add_permits(diff - pagado);
        } else if nuevo < self.limite_sem {
            for _ in 0..(self.limite_sem - nuevo) {
                match self.sem.try_acquire() {
                    Ok(p) => p.forget(),
                    Err(_) => {
                        self.comp.deuda.fetch_add(1, Ordering::SeqCst);
                    }
                }
            }
        }
        self.limite_sem = nuevo;
    }

    fn progreso(&self) -> Progreso {
        let st: &Stats = &self.ctx.stats;
        let elapsed = self.inicio.elapsed().as_secs_f64().max(1e-6);
        let bytes = st.bytes.load(Ordering::Relaxed);
        let files = self.comp.subidos.load(Ordering::Relaxed) + self.comp.saltados.load(Ordering::Relaxed);
        let bps = bytes as f64 / elapsed;
        let restantes = self.bytes_total.saturating_sub(bytes) as f64;
        let mut seg = [0.0; 5];
        let mut causas = [0u64; 5];
        for (i, s) in seg.iter_mut().enumerate() {
            *s = st.segundos(i);
        }
        for (i, c) in causas.iter_mut().enumerate() {
            *c = st.causas[i].load(Ordering::Relaxed);
        }
        Progreso {
            bytes_done: bytes,
            bytes_total: self.bytes_total,
            files_done: files,
            files_total: self.files_total,
            elapsed,
            eta: if bps > 0.0 && elapsed > 3.0 { Some(restantes / bps) } else { None },
            mbps: bps * 8.0 / 1_000_000.0,
            files_per_s: files as f64 / elapsed,
            reintentos: st.reintentos.load(Ordering::Relaxed),
            hilos_activos: self.comp.activos.load(Ordering::Relaxed),
            hilos_max: self.aimd.limite(),
            fichero_actual: bloquear(&self.comp.actual).clone(),
            fallidas: self.comp.fallidas.load(Ordering::Relaxed),
            pb_actual: self.pb_actual.clone(),
            seg,
            causas,
        }
    }

    /// Un paso del coordinador: eventos pendientes, sondeo de parada,
    /// ventana AIMD y progreso (<= 1/s).
    fn tic(&mut self) {
        self.drenar();
        let ahora = Instant::now();
        if ahora.duration_since(self.ult_sondeo) >= SONDEO_STOP {
            self.ult_sondeo = ahora;
            if (self.parar_py)() {
                self.comp.cancelado.store(true, Ordering::SeqCst);
                self.ctx.stop.store(true, Ordering::SeqCst);
            }
        }
        if ahora.duration_since(self.ult_ventana) >= aimd::VENTANA {
            self.ult_ventana = ahora;
            let congestion = self.ctx.stats.congestion.swap(0, Ordering::SeqCst) > 0;
            let pico = self.comp.pico.swap(self.comp.activos.load(Ordering::SeqCst), Ordering::SeqCst);
            let nuevo = self.aimd.ventana(ahora, congestion, pico >= self.aimd.limite());
            self.ajustar_semaforo(nuevo);
        }
        if ahora.duration_since(self.ult_progreso) >= PERIODO_PROGRESO {
            self.ult_progreso = ahora;
            let p = self.progreso();
            self.emitir(Evento::Progreso(Box::new(p)));
        }
    }
}

/// Sube el lote PB a PB. `parar_py` se sondea cada <= 250 ms; `emitir`
/// devuelve false si el callback falló (se corta la subida).
pub fn correr(
    p: Params,
    env: Arc<dyn Entorno>,
    parar_py: &mut dyn FnMut() -> bool,
    emitir: &mut dyn FnMut(Evento) -> bool,
) -> Result<Hecho, String> {
    let hilos = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4).clamp(2, 8);
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(hilos)
        .enable_all()
        .build()
        .map_err(|e| format!("no se pudo crear el runtime tokio: {e}"))?;
    rt.block_on(correr_async(p, env, parar_py, emitir))
}

async fn correr_async(
    p: Params,
    env: Arc<dyn Entorno>,
    parar_py: &mut dyn FnMut() -> bool,
    emitir: &mut dyn FnMut(Evento) -> bool,
) -> Result<Hecho, String> {
    let mut cfg = Config::nueva(p.oauth, p.chunk_bytes, p.simple_max_bytes, p.reintentos_hash);
    cfg.escala_espera = p.escala_espera.max(1);
    let ctx = Ctx {
        http: crear_cliente()?,
        env,
        stop: Arc::new(AtomicBool::new(false)),
        cfg,
        stats: Arc::new(Stats::default()),
    };
    let grupos = ordenar(p.items, &p.prefijo);
    let (files_total, bytes_total) = grupos
        .iter()
        .flat_map(|g| g.urgentes.iter().chain(g.resto.iter()))
        .fold((0u64, 0u64), |(f, b), it| (f + 1, b + it.2));
    let aimd = Aimd::nuevo(p.hilos_min, p.hilos_max);
    let limite = aimd.limite();
    let (tx, rx) = channel::<Evento>();
    let comp = Arc::new(Comp::default());
    let sem = Arc::new(Semaphore::new(limite));
    let ahora = Instant::now();
    let mut c = Coord {
        ctx: ctx.clone(),
        comp: comp.clone(),
        sem: sem.clone(),
        aimd,
        limite_sem: limite,
        rx,
        parar_py,
        emitir,
        inicio: ahora,
        ult_progreso: ahora,
        ult_ventana: ahora,
        ult_sondeo: ahora.checked_sub(SONDEO_STOP).unwrap_or(ahora),
        bytes_total,
        files_total,
        pb_actual: String::new(),
    };
    let n_trab = p.hilos_max.max(p.hilos_min).max(1);

    for g in grupos {
        c.tic();
        if ctx.stop.load(Ordering::SeqCst) {
            break;
        }
        let nombre = match g.pb {
            None => "General".to_string(),
            Some(n) => format!("PB{n}"),
        };
        c.pb_actual = nombre.clone();
        let mut lista = g.urgentes;
        lista.extend(g.resto);
        let n_items = lista.len() as u64;
        let bytes_pb: u64 = lista.iter().map(|i| i.2).sum();
        let pb = Arc::new(Pb {
            nombre: nombre.clone(),
            lista,
            siguiente: AtomicUsize::new(0),
            cortado: AtomicBool::new(false),
            sin_verificar: AtomicU64::new(0),
        });
        let mut hs = Vec::new();
        for _ in 0..n_trab.min(n_items as usize) {
            hs.push(tokio::spawn(trabajador(Trab {
                ctx: ctx.clone(),
                comp: comp.clone(),
                sem: sem.clone(),
                pb: pb.clone(),
                tx: tx.clone(),
            })));
        }
        while !hs.iter().all(|h| h.is_finished()) {
            c.tic();
            tokio::time::sleep(BUCLE).await;
        }
        for h in hs {
            if let Err(e) = h.await {
                // Un trabajador murió fuera de una subida: nunca en silencio.
                let motivo = format!("pánico en un trabajador de {nombre}: {e}");
                bloquear(&comp.acum).fallidos.push((nombre.clone(), motivo.clone()));
                comp.fallidas.fetch_add(1, Ordering::Relaxed);
                pb.sin_verificar.fetch_add(1, Ordering::SeqCst);
                let _ = tx.send(Evento::Error { remote: nombre.clone(), tipo: "panic".into(), motivo });
            }
        }
        c.drenar();
        let sin_verificar = pb.sin_verificar.load(Ordering::SeqCst);
        let hechos = pb.siguiente.load(Ordering::SeqCst) >= pb.lista.len();
        if sin_verificar == 0 && hechos && !ctx.stop.load(Ordering::SeqCst) {
            c.emitir(Evento::PbListo { pb: nombre, ficheros: n_items, bytes: bytes_pb });
        }
    }
    c.drenar();

    let (fallidos, md5) = {
        let a = bloquear(&comp.acum);
        (a.fallidos.clone(), a.md5.clone())
    };
    let hecho = Hecho {
        subidos: comp.subidos.load(Ordering::Relaxed),
        saltados_412: comp.saltados.load(Ordering::Relaxed),
        bytes_enviados: ctx.stats.bytes.load(Ordering::Relaxed),
        elapsed: c.inicio.elapsed().as_secs_f64(),
        reintentos: ctx.stats.reintentos.load(Ordering::Relaxed),
        cancelado: comp.cancelado.load(Ordering::SeqCst),
        abortado: bloquear(&comp.abortado).clone(),
        fallidos,
        md5,
    };
    c.emitir(Evento::Hecho(Box::new(hecho.clone())));
    Ok(hecho)
}

// ---------------------------------------------------------------------------
// Puente pyo3
// ---------------------------------------------------------------------------

struct PyEntorno {
    url_fn: PyObject,
    token_fn: Option<PyObject>,
    recover_fn: PyObject,
    cache: Mutex<Option<(Instant, String)>>,
}

impl Entorno for PyEntorno {
    fn url(&self, remote: &str, size: u64, sobrescribir: bool) -> Result<String, String> {
        Python::with_gil(|py| -> PyResult<String> {
            self.url_fn.bind(py).call1((remote, size, sobrescribir))?.extract::<String>()
        })
        .map_err(|e| e.to_string())
    }

    fn token(&self) -> Result<Option<String>, String> {
        let Some(f) = &self.token_fn else { return Ok(None) };
        if let Some((t, v)) = bloquear(&self.cache).as_ref() {
            if t.elapsed() < TOKEN_TTL {
                return Ok(Some(v.clone()));
            }
        }
        let v = Python::with_gil(|py| -> PyResult<String> { f.bind(py).call1(())?.extract::<String>() })
            .map_err(|e| e.to_string())?;
        *bloquear(&self.cache) = Some((Instant::now(), v.clone()));
        Ok(Some(v))
    }

    fn recover(&self) -> bool {
        let ok = Python::with_gil(|py| -> PyResult<bool> {
            self.recover_fn.bind(py).call0()?.extract::<bool>()
        })
        .unwrap_or(false);
        *bloquear(&self.cache) = None;
        ok
    }
}

fn a_dict<'py>(py: Python<'py>, ev: &Evento) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    match ev {
        Evento::Progreso(p) => {
            d.set_item("kind", "progreso")?;
            d.set_item("bytes_done", p.bytes_done)?;
            d.set_item("bytes_total", p.bytes_total)?;
            d.set_item("files_done", p.files_done)?;
            d.set_item("files_total", p.files_total)?;
            d.set_item("elapsed", p.elapsed)?;
            d.set_item("eta", p.eta)?;
            d.set_item("mbps", p.mbps)?;
            d.set_item("files_per_s", p.files_per_s)?;
            d.set_item("reintentos", p.reintentos)?;
            d.set_item("hilos_activos", p.hilos_activos)?;
            d.set_item("hilos_max", p.hilos_max)?;
            d.set_item("fichero_actual", p.fichero_actual.as_str())?;
            d.set_item("fallidas", p.fallidas)?;
            d.set_item("pb_actual", p.pb_actual.as_str())?;
            d.set_item("final", false)?;
            let m = PyDict::new(py);
            for (i, k) in ["disco_s", "red_s", "sesion_s", "espera_s", "token_s"].iter().enumerate() {
                m.set_item(*k, p.seg[i])?;
            }
            let r = PyDict::new(py);
            for (i, k) in ["429", "5xx", "auth", "http", "red"].iter().enumerate() {
                r.set_item(*k, p.causas[i])?;
            }
            m.set_item("reintentos", r)?;
            d.set_item("medidas", m)?;
        }
        Evento::PbListo { pb, ficheros, bytes } => {
            d.set_item("kind", "pb_listo")?;
            d.set_item("pb", pb.as_str())?;
            d.set_item("ficheros", *ficheros)?;
            d.set_item("bytes", *bytes)?;
        }
        Evento::Error { remote, tipo, motivo } => {
            d.set_item("kind", "error")?;
            d.set_item("remote", remote.as_str())?;
            d.set_item("tipo", tipo.as_str())?;
            d.set_item("motivo", motivo.as_str())?;
        }
        Evento::Hecho(h) => {
            d.set_item("kind", "hecho")?;
            d.set_item("subidos", h.subidos)?;
            d.set_item("saltados_412", h.saltados_412)?;
            d.set_item("bytes_enviados", h.bytes_enviados)?;
            d.set_item("elapsed", h.elapsed)?;
            d.set_item("reintentos", h.reintentos)?;
            d.set_item("cancelado", h.cancelado)?;
            d.set_item("abortado", h.abortado.as_deref())?;
            let f = PyList::empty(py);
            for (r, m) in &h.fallidos {
                f.append((r.as_str(), m.as_str()))?;
            }
            d.set_item("fallidos", f)?;
            let md5 = PyDict::new(py);
            for (r, m) in &h.md5 {
                md5.set_item(r.as_str(), m.as_str())?;
            }
            d.set_item("md5", md5)?;
        }
    }
    Ok(d)
}

fn texto_panico(p: &(dyn std::any::Any + Send)) -> String {
    if let Some(s) = p.downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = p.downcast_ref::<String>() {
        s.clone()
    } else {
        "pánico sin mensaje".to_string()
    }
}

/// Sube un lote al bucket (ver el plan, Task 6). Devuelve el dict `hecho`.
#[pyfunction]
#[pyo3(signature = (items, prefijo, modo, url_fn, token_fn, recover_fn, callback, should_stop,
                    hilos_min=16, hilos_max=64, chunk_bytes=16_777_216,
                    simple_max_bytes=16_777_216, reintentos_hash=3))]
#[allow(clippy::too_many_arguments)]
pub fn subir_lote(
    py: Python<'_>,
    items: Vec<(String, String, u64, bool, bool)>,
    prefijo: &str,
    modo: &str,
    url_fn: PyObject,
    token_fn: Option<PyObject>,
    recover_fn: PyObject,
    callback: PyObject,
    should_stop: PyObject,
    hilos_min: usize,
    hilos_max: usize,
    chunk_bytes: u64,
    simple_max_bytes: u64,
    reintentos_hash: u32,
) -> PyResult<PyObject> {
    let oauth = match modo {
        "oauth" => true,
        "firmada" => false,
        otro => return Err(PyValueError::new_err(format!("modo inválido {otro:?}: use 'oauth' o 'firmada'"))),
    };
    if oauth && token_fn.is_none() {
        return Err(PyValueError::new_err("modo 'oauth' requiere token_fn"));
    }
    if hilos_min == 0 || hilos_max == 0 || chunk_bytes == 0 {
        return Err(PyValueError::new_err("hilos_min, hilos_max y chunk_bytes deben ser > 0"));
    }
    let params = Params {
        items,
        prefijo: prefijo.to_string(),
        oauth,
        hilos_min,
        hilos_max,
        chunk_bytes,
        simple_max_bytes,
        reintentos_hash,
        escala_espera: 1,
    };
    let env: Arc<dyn Entorno> = Arc::new(PyEntorno {
        url_fn,
        token_fn,
        recover_fn,
        cache: Mutex::new(None),
    });

    let (res, err_cb) = py.allow_threads(move || {
        let mut err_cb: Option<PyErr> = None;
        let res = catch_unwind(AssertUnwindSafe(|| {
            let mut parar = || {
                Python::with_gil(|py| should_stop.bind(py).call0().and_then(|v| v.is_truthy()).unwrap_or(false))
            };
            let mut emitir = |ev: Evento| -> bool {
                Python::with_gil(|py| {
                    let r = a_dict(py, &ev).and_then(|d| callback.bind(py).call1((d,)));
                    match r {
                        Ok(_) => true,
                        Err(e) => {
                            if err_cb.is_none() {
                                err_cb = Some(e);
                            }
                            false
                        }
                    }
                })
            };
            correr(params, env, &mut parar, &mut emitir)
        }));
        (res, err_cb)
    });
    if let Some(e) = err_cb {
        return Err(e);
    }
    match res {
        Err(p) => Err(PyRuntimeError::new_err(format!("pánico en la subida Rust: {}", texto_panico(&*p)))),
        Ok(Err(m)) => Err(PyRuntimeError::new_err(m)),
        Ok(Ok(h)) => Ok(a_dict(py, &Evento::Hecho(Box::new(h)))?.into_any().unbind()),
    }
}

#[cfg(test)]
mod tests {
    use super::falso::*;
    use super::*;

    fn params(items: Vec<Item>) -> Params {
        Params {
            items,
            prefijo: String::new(),
            oauth: true,
            hilos_min: 2,
            hilos_max: 4,
            chunk_bytes: 16,
            simple_max_bytes: 1 << 20,
            reintentos_hash: 1,
            escala_espera: 1000,
        }
    }

    fn item(remote: &str, urgente: bool) -> Item {
        let ruta = fichero_tmp(remote.as_bytes());
        (ruta, remote.to_string(), remote.len() as u64, false, urgente)
    }

    /// PUT que acepta todo salvo rutas con `malo` (hash que no coincide).
    fn servidor() -> Servidor {
        Servidor::nuevo(|p, _| {
            if p.ruta.contains("malo") {
                Accion::Responder(200, cab_hash(b"distinto"))
            } else {
                Accion::Responder(200, cab_hash(&p.cuerpo))
            }
        })
    }

    fn lanzar(srv: &Servidor, items: Vec<Item>) -> (Hecho, Vec<Evento>) {
        let env: Arc<dyn Entorno> = EntornoFalso::nuevo(&srv.base);
        let mut evs: Vec<Evento> = Vec::new();
        let h = correr(params(items), env, &mut || false, &mut |e| {
            evs.push(e);
            true
        })
        .expect("correr");
        (h, evs)
    }

    fn pbs_listos(evs: &[Evento]) -> Vec<String> {
        evs.iter()
            .filter_map(|e| match e {
                Evento::PbListo { pb, .. } => Some(pb.clone()),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn pb_listo_sigue_el_orden_de_ordenar() {
        let srv = servidor();
        let items = vec![
            item("PB10/a.jpg", false),
            item("PB2/b.jpg", false),
            item("ESTADILLOS/e.csv", false),
            item("PB1/c.jpg", true),
            item("PB1/d.jpg", false),
        ];
        let (h, evs) = lanzar(&srv, items);
        assert_eq!(pbs_listos(&evs), vec!["General", "PB1", "PB2", "PB10"]);
        assert_eq!(h.subidos, 5);
        assert!(h.fallidos.is_empty());
        assert!(!h.cancelado && h.abortado.is_none());
        assert!(matches!(evs.last(), Some(Evento::Hecho(_))));
    }

    #[test]
    fn md5_solo_de_verificados_y_pb_con_fallo_no_esta_listo() {
        let srv = servidor();
        let items = vec![item("PB1/ok.jpg", false), item("PB2/malo.jpg", false), item("PB3/ok.jpg", false)];
        let (h, evs) = lanzar(&srv, items);
        assert_eq!(pbs_listos(&evs), vec!["PB1", "PB3"]);
        let claves: Vec<&str> = h.md5.iter().map(|(r, _)| r.as_str()).collect();
        assert!(claves.contains(&"PB1/ok.jpg") && claves.contains(&"PB3/ok.jpg"));
        assert!(!claves.contains(&"PB2/malo.jpg"));
        assert_eq!(h.fallidos.len(), 1);
        assert_eq!(h.fallidos[0].0, "PB2/malo.jpg");
        assert!(evs.iter().any(|e| matches!(e, Evento::Error { tipo, .. } if tipo == "hash")));
    }

    #[test]
    fn fallo_de_urgente_corta_el_resto_del_pb_pero_sigue_con_otros() {
        let srv = servidor();
        let items = vec![item("PB1/malo.jpg", true), item("PB1/resto.jpg", false), item("PB2/ok.jpg", false)];
        // Un solo hilo: el orden es determinista (con varios, el resto podría
        // haber arrancado antes de que falle el urgente).
        let env: Arc<dyn Entorno> = EntornoFalso::nuevo(&srv.base);
        let mut evs: Vec<Evento> = Vec::new();
        let mut p = params(items);
        p.hilos_min = 1;
        p.hilos_max = 1;
        let h = correr(p, env, &mut || false, &mut |e| {
            evs.push(e);
            true
        })
        .expect("correr");
        assert_eq!(pbs_listos(&evs), vec!["PB2"]);
        assert_eq!(h.subidos, 1);
        assert_eq!(h.fallidos.len(), 2, "el urgente y el no intentado: {:?}", h.fallidos);
        assert!(h.fallidos.iter().any(|(r, m)| r == "PB1/resto.jpg" && m.contains("no intentado")));
    }

    #[test]
    fn cancelar_no_marca_nada_ni_emite_pb_listo() {
        let srv = servidor();
        let env: Arc<dyn Entorno> = EntornoFalso::nuevo(&srv.base);
        let mut evs: Vec<Evento> = Vec::new();
        let h = correr(params(vec![item("PB1/a.jpg", false)]), env, &mut || true, &mut |e| {
            evs.push(e);
            true
        })
        .expect("correr");
        assert!(h.cancelado);
        assert!(pbs_listos(&evs).is_empty());
        assert!(h.md5.is_empty());
    }
}
