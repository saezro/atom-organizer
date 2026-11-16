//! Control de concurrencia AIMD (puro, sin reloj propio: recibe `Instant`).

use std::time::{Duration, Instant};

pub const VENTANA: Duration = Duration::from_secs(5);
pub const COOLDOWN: Duration = Duration::from_secs(10);
const INCREMENTO: usize = 4;

#[derive(Debug, Clone)]
pub struct Aimd {
    actual: usize,
    min: usize,
    max: usize,
    ultima_reduccion: Option<Instant>,
}

impl Aimd {
    /// Arranca en `min`. Si `max < min`, `max` se sube a `min`.
    pub fn nuevo(min: usize, max: usize) -> Self {
        let min = min.max(1);
        let max = max.max(min);
        Aimd { actual: min, min, max, ultima_reduccion: None }
    }

    /// Cierra una ventana de 5 s y devuelve el nuevo límite.
    /// Congestión: x0.5 (suelo `min`) con cooldown de 10 s entre reducciones.
    /// Sin congestión y saturado: +4 hasta `max`.
    pub fn ventana(&mut self, ahora: Instant, hubo_congestion: bool, saturado: bool) -> usize {
        if hubo_congestion {
            let en_cooldown = match self.ultima_reduccion {
                Some(t) => ahora.saturating_duration_since(t) < COOLDOWN,
                None => false,
            };
            if !en_cooldown {
                self.actual = (self.actual / 2).max(self.min);
                self.ultima_reduccion = Some(ahora);
            }
        } else if saturado {
            self.actual = (self.actual + INCREMENTO).min(self.max);
        }
        self.actual
    }

    pub fn limite(&self) -> usize {
        self.actual
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sube_4_por_ventana_y_satura() {
        let t0 = Instant::now();
        let mut a = Aimd::nuevo(16, 64);
        assert_eq!(a.limite(), 16);
        assert_eq!(a.ventana(t0, false, true), 20);
        assert_eq!(a.ventana(t0 + VENTANA, false, true), 24);
        let mut ult = 0;
        for i in 0..30u32 {
            ult = a.ventana(t0 + VENTANA * (i + 2), false, true);
        }
        assert_eq!(ult, 64);
    }

    #[test]
    fn baja_la_mitad_con_suelo() {
        let t0 = Instant::now();
        let mut a = Aimd::nuevo(16, 64);
        for i in 0..12u32 {
            a.ventana(t0 + VENTANA * i, false, true);
        }
        assert_eq!(a.limite(), 64);
        assert_eq!(a.ventana(t0 + Duration::from_secs(100), true, true), 32);
        assert_eq!(a.ventana(t0 + Duration::from_secs(200), true, true), 16);
        assert_eq!(a.ventana(t0 + Duration::from_secs(300), true, true), 16);
    }

    #[test]
    fn cooldown_dos_congestiones_bajan_una_vez() {
        let t0 = Instant::now();
        let mut a = Aimd::nuevo(16, 64);
        for i in 0..12u32 {
            a.ventana(t0 + VENTANA * i, false, true);
        }
        let t = t0 + Duration::from_secs(100);
        assert_eq!(a.ventana(t, true, true), 32);
        assert_eq!(a.ventana(t + Duration::from_secs(5), true, true), 32);
        assert_eq!(a.ventana(t + Duration::from_secs(10), true, true), 16);
    }

    #[test]
    fn sin_saturar_no_sube() {
        let t0 = Instant::now();
        let mut a = Aimd::nuevo(16, 64);
        assert_eq!(a.ventana(t0, false, false), 16);
    }

    #[test]
    fn min_igual_max_constante() {
        let t0 = Instant::now();
        let mut a = Aimd::nuevo(16, 16);
        assert_eq!(a.ventana(t0, false, true), 16);
        assert_eq!(a.ventana(t0 + Duration::from_secs(50), true, true), 16);
    }
}
