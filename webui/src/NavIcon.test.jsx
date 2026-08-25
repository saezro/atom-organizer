import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import NavIcon from './NavIcon'
import { NAV } from './App'

// Regresión: la nav de App.jsx pasó a usar los ids `trabajo`/`herramientas` y
// `TRAZOS` en NavIcon.jsx se quedó desincronizado (path sin `d` → icono
// vacío). Este test falla en cuanto NAV traiga un id sin trazo definido.
describe('NavIcon', () => {
  it('tiene un trazo no vacío para cada id de NAV', () => {
    for (const { id } of NAV) {
      const { container, unmount } = render(<NavIcon id={id} />)
      const path = container.querySelector('path')
      expect(path, `sin <path> para el id "${id}"`).toBeTruthy()
      expect(path.getAttribute('d'), `trazo vacío para el id "${id}"`).toBeTruthy()
      unmount()
    }
  })
})
