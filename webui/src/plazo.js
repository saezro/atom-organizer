// Plazo para promesas del bridge.
//
// Toda llamada al backend puede no volver JAMÁS: el hilo del bridge Qt es uno
// solo, y basta con que algo haga red sin plazo para que la promesa se quede
// colgada y la UI espere en silencio para siempre (ver el modal de progreso
// atascado en "Preparando…" que reportó la 3.4.72). Cuando la respuesta manda
// una pantalla, la espera va acotada y el vencimiento se trata como error
// legible, no como "sigue cargando".
export function conPlazo(promesa, ms, mensaje = 'El programa tardó demasiado en responder.') {
  let temporizador = null
  return Promise.race([
    Promise.resolve(promesa).finally(() => clearTimeout(temporizador)),
    new Promise((_, rechazar) => {
      temporizador = setTimeout(() => rechazar(new Error(mensaje)), ms)
    }),
  ])
}
