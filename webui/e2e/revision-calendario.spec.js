// Capturas del arnés del calendario de revisión del índice (Task 0 del plan de revisión tras índice).
// Guarda un PNG por escenario en test-results/ y comprueba lo mínimo visible de cada uno.
import { expect, test } from '@playwright/test'

test.use({ viewport: { width: 1280, height: 900 } })

const escenarios = [
  ['esperando', /se continuará solo en 9:4\d si no tocas nada/i],
  ['desatendida', /Sin respuesta en 30 min: aplicada 1 recomendación de confianza alta; 2 quedan en SIN_ORDENAR/],
  ['sin_pendientes', /No quedan fotos sin colocar ni recomendaciones/],
  ['muchas', /2\d{3} fotos sin colocar/],
]

for (const [escenario, texto] of escenarios) {
  test(`revisión del índice (${escenario})`, async ({ page }) => {
    await page.goto(`/e2e/harness/revision.html?escenario=${escenario}`)
    await expect(page.getByRole('dialog', { name: 'Revisión del índice' })).toBeVisible()
    await expect(page.getByRole('dialog')).toContainText(texto)
    if (escenario !== 'sin_pendientes') await expect(page.getByTestId('ri-calendario')).toBeVisible()
    // El modal hace scroll propio: se agranda el viewport al alto del contenido para capturarlo entero.
    const alto = await page.evaluate(() => document.querySelector('.ri-card').scrollHeight)
    await page.setViewportSize({ width: 1280, height: Math.ceil(alto / 0.94) + 20 })
    await page.screenshot({ path: `test-results/revision-${escenario}.png`, fullPage: true })
  })
}
