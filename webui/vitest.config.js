import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Config aparte de vite.config.js: aquella lleva `base: './'` para que pywebview
// cargue dist/index.html por file://, y eso no pinta nada en los tests.
export default defineConfig({
  plugins: [react()],
  // El runtime automático de JSX, explícito: si no, el transform de los tests
  // cae en el clásico y pide un `React` en ámbito que el código no importa
  // (el build de producción sí usa el automático vía plugin).
  esbuild: { jsx: 'automatic' },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    include: ['src/**/*.test.jsx'],
  },
})
