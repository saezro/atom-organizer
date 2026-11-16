//! Orden de subida: General primero, luego PB natural; dentro del PB,
//! urgentes antes que resto.

use std::cmp::Ordering;

/// (local, remote, size, sobrescribir, urgente)
pub type Item = (String, String, u64, bool, bool);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Grupo {
    pub pb: Option<u32>,
    pub urgentes: Vec<Item>,
    pub resto: Vec<Item>,
}

/// PB de una ruta remota ya sin prefijo: primer componente `PB<n>` (sin
/// distinguir mayúsculas) o que empiece por `PB<n>` seguido de no-dígito
/// (`PB3_V1`). `None` = General.
pub fn pb_de(remote_sin_prefijo: &str) -> Option<u32> {
    // Solo componentes de directorio: el último (nombre de fichero) no cuenta.
    let mut comps: Vec<&str> = remote_sin_prefijo.split('/').collect();
    comps.pop();
    for comp in comps {
        let b = comp.as_bytes();
        if b.len() < 3 || !b[..2].eq_ignore_ascii_case(b"PB") {
            continue;
        }
        let digitos: &str = {
            let resto = &comp[2..];
            let fin = resto
                .find(|c: char| !c.is_ascii_digit())
                .unwrap_or(resto.len());
            &resto[..fin]
        };
        if digitos.is_empty() {
            continue;
        }
        if let Ok(n) = digitos.parse::<u32>() {
            return Some(n);
        }
    }
    None
}

/// Quita `prefijo` (y la `/` siguiente) si la ruta empieza por él.
fn sin_prefijo<'a>(remote: &'a str, prefijo: &str) -> &'a str {
    let p = prefijo.trim_matches('/');
    if p.is_empty() {
        return remote.trim_start_matches('/');
    }
    let r = remote.trim_start_matches('/');
    match r.strip_prefix(p) {
        Some(resto) if resto.is_empty() || resto.starts_with('/') => resto.trim_start_matches('/'),
        _ => r,
    }
}

/// Comparación natural: tramos de dígitos por valor, resto por texto
/// (sin distinguir mayúsculas).
pub fn cmp_natural(a: &str, b: &str) -> Ordering {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    let (mut i, mut j) = (0, 0);
    while i < a.len() && j < b.len() {
        if a[i].is_ascii_digit() && b[j].is_ascii_digit() {
            let si = i;
            while i < a.len() && a[i].is_ascii_digit() {
                i += 1;
            }
            let sj = j;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            let na = trim_ceros(&a[si..i]);
            let nb = trim_ceros(&b[sj..j]);
            let o = na.len().cmp(&nb.len()).then_with(|| na.cmp(nb));
            if o != Ordering::Equal {
                return o;
            }
        } else {
            let o = a[i].to_ascii_lowercase().cmp(&b[j].to_ascii_lowercase());
            if o != Ordering::Equal {
                return o;
            }
            i += 1;
            j += 1;
        }
    }
    (a.len() - i).cmp(&(b.len() - j))
}

fn trim_ceros(s: &[u8]) -> &[u8] {
    let n = s.iter().take_while(|c| **c == b'0').count();
    &s[n..]
}

fn ordenar_lista(v: &mut [Item]) {
    v.sort_by(|x, y| cmp_natural(&x.1, &y.1).then_with(|| x.1.cmp(&y.1)));
}

/// Agrupa por PB: General (None) primero y luego PB ascendente.
pub fn ordenar(items: Vec<Item>, prefijo: &str) -> Vec<Grupo> {
    let mut grupos: Vec<Grupo> = Vec::new();
    for it in items {
        let pb = pb_de(sin_prefijo(&it.1, prefijo));
        let idx = match grupos.iter().position(|g| g.pb == pb) {
            Some(i) => i,
            None => {
                grupos.push(Grupo { pb, urgentes: Vec::new(), resto: Vec::new() });
                grupos.len() - 1
            }
        };
        if it.4 {
            grupos[idx].urgentes.push(it);
        } else {
            grupos[idx].resto.push(it);
        }
    }
    // Option<u32>: None < Some(_), y Some ordena numéricamente.
    grupos.sort_by_key(|g| g.pb);
    for g in grupos.iter_mut() {
        ordenar_lista(&mut g.urgentes);
        ordenar_lista(&mut g.resto);
    }
    grupos
}

#[cfg(test)]
mod tests {
    use super::*;

    fn it(remote: &str, urgente: bool) -> Item {
        (format!("/l/{remote}"), remote.to_string(), 1, false, urgente)
    }

    #[test]
    fn pb_de_basico() {
        assert_eq!(pb_de("RGB/PB3_V1/x.jpg"), Some(3));
        assert_eq!(pb_de("TERMICA/PB3_V2/x.tiff"), Some(3));
        assert_eq!(pb_de("pb01/x"), Some(1));
        assert_eq!(pb_de("PB10/x"), Some(10));
    }

    #[test]
    fn general_y_no_pb() {
        assert_eq!(pb_de("ESTADILLOS/a.csv"), None);
        assert_eq!(pb_de("FOTOS_GENERALES/a.jpg"), None);
        assert_eq!(pb_de("INDICE_x.xlsx"), None);
        assert_eq!(pb_de("PBX/a.jpg"), None);
        assert_eq!(pb_de("PB/a.jpg"), None);
    }

    #[test]
    fn pb_en_nombre_de_fichero_no_cuenta() {
        assert_eq!(pb_de("pb12_notas.txt"), None);
        assert_eq!(pb_de("ESTADILLOS/pb12_notas.txt"), None);
        assert_eq!(pb_de("RGB/PB3_V1/pb12_notas.txt"), Some(3));
    }

    #[test]
    fn prefijo_con_pb_no_confunde() {
        let g = ordenar(vec![it("PB9_planta/ESTADILLOS/a.csv", false)], "PB9_planta");
        assert_eq!(g.len(), 1);
        assert_eq!(g[0].pb, None);
        let g = ordenar(vec![it("PB9_planta/RGB/PB2_V1/a.jpg", false)], "PB9_planta/");
        assert_eq!(g[0].pb, Some(2));
    }

    #[test]
    fn orden_general_pb_natural() {
        let items = vec![
            it("P/RGB/PB10_V1/a.jpg", false),
            it("P/RGB/PB2_V1/a.jpg", false),
            it("P/ESTADILLOS/e.csv", false),
            it("P/RGB/PB1_V1/a.jpg", false),
        ];
        let g = ordenar(items, "P");
        let pbs: Vec<Option<u32>> = g.iter().map(|x| x.pb).collect();
        assert_eq!(pbs, vec![None, Some(1), Some(2), Some(10)]);
    }

    #[test]
    fn urgentes_antes_que_resto_y_mismo_pb() {
        let items = vec![
            it("P/RGB/PB3_V1/b.jpg", false),
            it("P/TERMICA/PB3_V2/x.tiff", true),
            it("P/RGB/PB3_V1/a.jpg", true),
        ];
        let g = ordenar(items, "P");
        assert_eq!(g.len(), 1);
        assert_eq!(g[0].pb, Some(3));
        assert_eq!(g[0].urgentes.len(), 2);
        assert_eq!(g[0].resto.len(), 1);
        assert_eq!(g[0].urgentes[0].1, "P/RGB/PB3_V1/a.jpg");
    }

    #[test]
    fn orden_natural_dentro_de_lista() {
        let items = vec![
            it("P/RGB/PB1_V1/img10.jpg", false),
            it("P/RGB/PB1_V1/img2.jpg", false),
        ];
        let g = ordenar(items, "P");
        assert_eq!(g[0].resto[0].1, "P/RGB/PB1_V1/img2.jpg");
    }
}
