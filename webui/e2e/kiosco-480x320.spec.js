// Regresión de layout del kiosco a 480x320 (pantalla real de la Raspberry
// Pi): nada se puede cortar durante la espera del estadillo. Monta el arnés
// de `e2e/harness` (KioskScreen directo, sin App.jsx) y comprueba que todo
// el contenido de `EsperaEstadillo` cabe SIN scroll (`.espera-cuerpo`
// tiene el mismo alto de scroll que de cliente) y que el pie con las
// acciones (Cancelar/Volver a esperar/Empezar) queda dentro del viewport.
//
// Motivo (pedido de Rodrigo, 2026-09-22): «se corta al esperar el
// estadillo» — la dirección `http://organizer.local` y el botón Cancelar
// quedaban fuera de los 320px reales de la pantalla.
import { expect, test } from '@playwright/test'

test.use({ viewport: { width: 480, height: 320 } })

const escenarios = ['espera-con-carpeta', 'espera-sin-carpeta']

for (const escenario of escenarios) {
  test(`espera del estadillo (${escenario}): todo visible sin scroll, sin scroll horizontal`, async ({ page }) => {
    await page.goto(`/e2e/harness/index.html?escenario=${escenario}`)

    const panel = page.getByTestId('espera-estadillo')
    await expect(panel).toBeVisible()
    await expect(panel).toHaveAttribute('data-fase', 'esperando')

    // Sin scroll horizontal en ningún punto de la pantalla.
    const overflowX = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
    expect(overflowX).toBe(false)

    // `.espera-cuerpo` es el único bloque con scroll propio del panel: si su
    // contenido cupiera de sobra no haría falta, pero tampoco puede
    // desbordar sin que el pie (acciones) siga alcanzable.
    const { cuerpoClient, cuerpoScroll, piePad } = await page.evaluate(() => {
      const cuerpo = document.querySelector('.espera-cuerpo')
      const pie = document.querySelector('.espera-pie')
      const viewport = window.innerHeight
      return {
        cuerpoClient: cuerpo.clientHeight,
        cuerpoScroll: cuerpo.scrollHeight,
        piePad: viewport - pie.getBoundingClientRect().bottom,
      }
    })
    expect(cuerpoScroll).toBeLessThanOrEqual(cuerpoClient)
    // El pie (acciones) siempre visible, con algo de aire hasta el borde.
    expect(piePad).toBeGreaterThan(0)

    // Botón "Cancelar" siempre alcanzable y clicable dentro del viewport.
    const cancelar = page.getByRole('button', { name: 'Cancelar' })
    await expect(cancelar).toBeVisible()
    const box = await cancelar.boundingBox()
    expect(box.y + box.height).toBeLessThanOrEqual(320)

    // Nunca un botón "Elegir carpeta…" duplicado dentro del panel de espera:
    // el selector de carpeta ya está justo encima, en `.kiosk-carpeta`.
    await expect(panel.getByRole('button', { name: /elegir carpeta/i })).toHaveCount(0)
  })
}

test('espera CON carpeta: la dirección de conexión es legible sin cortarse', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-con-carpeta')
  await expect(page.getByText('http://organizer.local')).toBeVisible()
})

test('espera SIN carpeta: solo un aviso en texto, sin botón redundante', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-sin-carpeta')
  await expect(page.getByTestId('espera-carpeta')).toHaveText('Elige carpeta arriba')
})

// Estado «estadillo recibido» (ejemplo REAL, ver harness/main.jsx): resumen
// completo + «OK, seguir» siempre visible y clicable, sin cortarse, con o sin
// avisos de validación. Pedido de Rodrigo: "necesito que al llegar en la
// raspi salga el resumen del estadillo y un OK para seguir".
//
// Desde 2026-09-22 (bug reportado por Rodrigo): el resumen sale como MODAL
// (portal a `document.body`, por encima de la pantalla del kiosco) y NUNCA
// avanza solo — sin auto-avance ni timeout, solo el botón «OK, seguir» avisa.
for (const escenario of ['espera-recibido', 'espera-recibido-avisos']) {
  test(`estadillo recibido (${escenario}): resumen + "OK, seguir" caben sin scroll de página ni recortes`, async ({ page }) => {
    await page.goto(`/e2e/harness/index.html?escenario=${escenario}`)

    const panel = page.getByTestId('espera-estadillo')
    await expect(panel).toHaveAttribute('data-fase', 'recibido_ok')
    await expect(page.getByText('KL05')).toBeVisible()
    await expect(page.getByText('2026-09-20')).toBeVisible()
    await expect(page.getByText('Rebeca')).toBeVisible()

    const overflowX = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
    expect(overflowX).toBe(false)

    const seguir = page.getByRole('button', { name: 'OK, seguir' })
    await expect(seguir).toBeVisible()
    await expect(seguir).toBeEnabled()
    const box = await seguir.boundingBox()
    expect(box.y + box.height).toBeLessThanOrEqual(320)

    // El panel entero (cabecera + cuerpo + pie) cabe dentro de los 480x320
    // reales de la pantalla, sin salirse por ningún borde.
    const panelBox = await panel.boundingBox()
    expect(panelBox.x).toBeGreaterThanOrEqual(0)
    expect(panelBox.y).toBeGreaterThanOrEqual(0)
    expect(panelBox.x + panelBox.width).toBeLessThanOrEqual(480)
    expect(panelBox.y + panelBox.height).toBeLessThanOrEqual(320)
  })
}

test('estadillo recibido: sale como MODAL (portal a document.body, role dialog) encima del kiosco', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-recibido')

  const panel = page.getByTestId('espera-estadillo')
  await expect(panel).toHaveAttribute('role', 'dialog')
  await expect(panel).toHaveAttribute('aria-modal', 'true')

  const { fueraDeKiosk, overlayFijoPantallaCompleta } = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="espera-estadillo"]')
    const overlay = el.closest('.espera-modal-overlay')
    const rect = overlay.getBoundingClientRect()
    return {
      fueraDeKiosk: el.closest('.kiosk-estadillo') === null && el.closest('.kiosk') === null,
      overlayFijoPantallaCompleta:
        getComputedStyle(overlay).position === 'fixed' &&
        rect.width === window.innerWidth &&
        rect.height === window.innerHeight,
    }
  })
  expect(fueraDeKiosk).toBe(true)
  expect(overlayFijoPantallaCompleta).toBe(true)
})

test('estadillo recibido: NO avanza solo aunque pasen varios ciclos de poll (sin auto-avance ni timeout)', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-recibido')

  const panel = page.getByTestId('espera-estadillo')
  await expect(panel).toHaveAttribute('data-fase', 'recibido_ok')

  // El poll normal es cada 2 s; se deja pasar más del triple (y muy por
  // encima del antiguo retraso de 2.5 s que avanzaba solo) sin tocar nada.
  await page.waitForTimeout(6500)

  await expect(panel).toBeVisible()
  await expect(panel).toHaveAttribute('data-fase', 'recibido_ok')
})

test('estadillo recibido: pulsar "OK, seguir" es lo único que avanza', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-recibido')

  await expect(page.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'recibido_ok')
  await page.getByRole('button', { name: 'OK, seguir' }).click()

  // El modal se cierra y el kiosco vuelve al paso normal de "Recibir
  // estadillo" (KioskScreen tras `onRecibido`).
  await expect(page.getByTestId('espera-estadillo')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /recibir estadillo/i })).toBeVisible()
})

test('estadillo recibido CON avisos: semáforo ámbar y avisos visibles, "OK, seguir" sigue habilitado', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-recibido-avisos')
  await expect(page.getByTestId('espera-semaforo')).toContainText('Revisar')
  await expect(page.getByText('3 fotos fuera de vuelo')).toBeVisible()
  await expect(page.getByRole('button', { name: 'OK, seguir' })).toBeEnabled()
})

test('estadillo recibido SIN avisos: semáforo verde "Todo encaja"', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-recibido')
  await expect(page.getByTestId('espera-semaforo')).toHaveText('Todo encaja')
})

test('rechazado con motivo: pinta el motivo, no "rechazado" a secas', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-rechazado-motivo')
  await expect(page.getByTestId('espera-motivo')).toHaveText('No hay carpeta seleccionada en el Organizer')
})

// Bug real (reportado por Rodrigo): tras «OK, seguir» del modal, la tarjeta
// «Estadillo recibido» de `EstadilloField` (ya dentro de la pantalla normal
// de "Organizar", NO el modal) salía cortada por debajo con 3 vuelos, y no
// se podía hacer scroll con el dedo — el panel resistivo de la Pi no genera
// gesto de arrastre (`ADS7846` como puntero, no como touch), así que un
// `overflow-y:auto` nunca podía dispararse. Se pagina en su lugar
// (`EstadilloField.jsx`, `.estad-nav`): comprueba que el total (última
// sección) queda alcanzable pulsando el paginador, dentro de los 480x320
// reales, sin scroll de página.
test('tarjeta "Estadillo recibido" con 3 vuelos: se pagina, nada se corta ni depende de scroll', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=tarjeta-recibida')

  const tarjeta = page.getByTestId('estadillo-recibido-card')
  await expect(tarjeta).toBeVisible()
  await expect(tarjeta).toContainText('PRUEBA_SIM')

  const overflowX = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth)
  expect(overflowX).toBe(false)
  const overflowY = await page.evaluate(() => document.documentElement.scrollHeight > document.documentElement.clientHeight)
  expect(overflowY).toBe(false)

  // Toda la tarjeta cabe dentro de los 480x320 reales.
  const box = await tarjeta.boundingBox()
  expect(box.y + box.height).toBeLessThanOrEqual(320)

  // El total (última sección de `secciones`, no cabía en la primera página)
  // no está aún, pero se llega con el paginador sin arrastrar nada —
  // «Quitar» sigue alcanzable en todo momento.
  await expect(page.getByTestId('estadillo-recibido-total')).toHaveCount(0)
  const pagina = page.getByTestId('estadillo-recibido-pagina')
  await expect(pagina).toBeVisible()

  const abajo = page.getByTestId('estadillo-recibido-pag-abajo')
  for (let i = 0; i < 20 && (await page.getByTestId('estadillo-recibido-total').count()) === 0; i++) {
    await expect(page.getByTestId('estad-recibido-quitar')).toBeVisible()
    await abajo.click()
  }
  await expect(page.getByTestId('estadillo-recibido-total')).toContainText('1h 35min')

  // El botón "Organizar" del pie del kiosco sigue alcanzable, sin que la
  // tarjeta lo tape.
  const organizarBox = await page.getByRole('button', { name: 'Organizar' }).boundingBox()
  expect(organizarBox.y + organizarBox.height).toBeLessThanOrEqual(320)
})

// Bug real (reportado por Rodrigo, 2026-09-23): "fatal como ya esta full
// cortado los datos del estadillo y se queda fijo lo de elegir carpeta no se
// ve nada nunca". Con la carpeta ya elegida (el caso normal: se elige antes
// de "Recibir estadillo"), el control de carpeta no debe pintarse ni en la
// espera ni en la tarjeta ya recibida — le robaba altura a lo que sí hay que
// leer (ver KioskScreen.jsx).
test('con carpeta ya elegida, el selector de carpeta no se pinta durante la espera del estadillo', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=espera-con-carpeta')
  await expect(page.getByTestId('espera-estadillo')).toHaveAttribute('data-fase', 'esperando')
  await expect(page.getByRole('button', { name: /elegir carpeta/i })).toHaveCount(0)
})

test('con carpeta ya elegida, el selector de carpeta no se pinta con la tarjeta "Estadillo recibido"', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=tarjeta-recibida')
  await expect(page.getByTestId('estadillo-recibido-card')).toBeVisible()
  await expect(page.getByRole('button', { name: /elegir carpeta/i })).toHaveCount(0)
})

// Cada fila de vuelo debe quedar contenida dentro de `.estad-recibido-cuerpo`
// (su contenedor con recorte): nada de una fila cortada a media altura, ni
// en la primera página (campos) ni en la que trae los vuelos.
test('tarjeta "Estadillo recibido": ninguna fila de vuelo sobresale de su contenedor', async ({ page }) => {
  await page.goto('/e2e/harness/index.html?escenario=tarjeta-recibida')

  const abajo = page.getByTestId('estadillo-recibido-pag-abajo')
  for (let i = 0; i < 20 && (await page.getByTestId('estadillo-recibido-total').count()) === 0; i++) {
    await abajo.click()
  }
  await expect(page.getByTestId('estadillo-recibido-total')).toBeVisible()

  const { cuerpoBox, filaBoxes } = await page.evaluate(() => {
    const cuerpo = document.querySelector('.estad-recibido-cuerpo')
    const filas = Array.from(document.querySelectorAll('.estad-recibido-vuelo'))
    const rect = (el) => {
      const r = el.getBoundingClientRect()
      return { top: r.top, bottom: r.bottom }
    }
    return { cuerpoBox: rect(cuerpo), filaBoxes: filas.map(rect) }
  })
  expect(filaBoxes.length).toBeGreaterThan(0)
  for (const fila of filaBoxes) {
    expect(fila.top).toBeGreaterThanOrEqual(cuerpoBox.top - 0.5)
    expect(fila.bottom).toBeLessThanOrEqual(cuerpoBox.bottom + 0.5)
  }
})
