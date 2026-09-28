import { defineConfig, devices } from '@playwright/test'

// Config MÍNIMA solo para el test de regresión del kiosco (480x320): monta
// el arnés de `e2e/harness` (KioskScreen directo, sin App.jsx) contra el
// propio servidor de desarrollo de Vite — sirve cualquier ruta del proyecto
// por su grafo de módulos, así que no hace falta declarar el harness como
// entrada de build. Puerto propio (5183) para no chocar con un `vite dev`
// que ya pueda estar corriendo en el 5173.
const PORT = 5183

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'off',
    screenshot: 'off',
  },
  webServer: {
    // `--host 127.0.0.1`: sin esto Vite a veces solo escucha en IPv6
    // (`[::1]`) y `page.goto('http://127.0.0.1:...')` da ERR_CONNECTION_REFUSED
    // aunque el server esté arriba.
    command: `npx vite --port ${PORT} --strictPort --host 127.0.0.1`,
    port: PORT,
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
