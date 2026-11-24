// El backend (`GenStructFolderConfig.estad` / `atom_core.organize.run_task`)
// sigue esperando el estadillo como UN string: para colar varios por el mismo
// hueco, `atom_core.estadillo.empaquetar_rutas` los junta con el carácter de
// control Unit Separator (\x1f, no puede aparecer en un path real). Aquí se
// replica esa misma codificación en JS. Con 0 o 1 rutas el resultado es
// idéntico a lo que se mandaba antes (string vacío o el path suelto).
export const ESTADILLO_PATH_SEP = '\x1f'
export function empaquetarRutas(paths) {
  return (paths || []).map((p) => String(p).trim()).filter(Boolean).join(ESTADILLO_PATH_SEP)
}
