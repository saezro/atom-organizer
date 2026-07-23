import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base: './' → el index.html buildeado referencia los assets con rutas
// relativas, imprescindible para que pywebview cargue dist/index.html
// desde el sistema de ficheros (file://) en el exe empaquetado.
export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
})
