import { api } from '../bridge'

// `cloudUpload` puede devolver `requiere_confirmacion` cuando la carpeta ya
// tiene un lote COMPLETO (su manifest.json ya se escribió, la Suite ya lo
// procesó): resubirla en silencio duplicaría el trabajo, así que en vez de
// eso hay que confirmar dos veces, dejando claro que es una subida EXTRA con
// OTRO estadillo, no un reintento. Se reusa `window.confirm`, igual que el
// resto de confirmaciones de esta pantalla (ver el checkbox "Subir sin
// estadillo" en `BucketScreen`) en vez de montar un diálogo nuevo. Un lote
// INCOMPLETO no pasa por aquí: el backend lo reanuda directo, sin preguntar.
// Módulo, no closure de componente: la usan tanto `App` (kiosco) como
// `PanelSubida` (escritorio), y ambas importan el mismo `api` singleton.
export default async function cloudUploadConfirmando(carpeta, prefijo, inspeccionId) {
  let r = await api.cloudUpload(carpeta, false, prefijo, inspeccionId)
  if (r && r.requiere_confirmacion) {
    const primera = window.confirm(
      `Esta carpeta ya se subió por completo (lote ${r.lote_anterior}). ` +
        '¿Seguro que quieres subirla otra vez?'
    )
    if (!primera) return { started: false, reason: 'Subida cancelada por el operador.' }
    const segunda = window.confirm(
      'Confirma que esta subida es EXTRA, con OTRO estadillo distinto del ya subido. ' +
        'Si es el mismo estadillo, cancela: ya está subido.'
    )
    if (!segunda) return { started: false, reason: 'Subida cancelada por el operador.' }
    r = await api.cloudUpload(carpeta, false, prefijo, inspeccionId, true)
  }
  return r
}
