import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createElement as h } from 'react'

import MenuApps from './MenuApps.jsx'
import { APPS } from './apps/registry.js'

// Icono ficticio mínimo, solo para las pruebas de paginación (no depende del
// registry real, así se puede variar el número de apps a placer).
function IconoDummy() {
  return h('svg', { 'data-testid': 'icono-dummy' })
}

function appsFicticias(n) {
  return Array.from({ length: n }, (_, i) => ({
    id: `app-${i}`,
    nombre: `App ${i}`,
    Icono: IconoDummy,
  }))
}

describe('MenuApps', () => {
  it('pinta las apps del registry real y no pinta paginación (caben en una página)', () => {
    render(<MenuApps apps={APPS} tactil={false} disabled={false} onAbrir={vi.fn()} />)
    for (const app of APPS) {
      expect(screen.getByTestId(`kiosk-app-${app.id}`)).toBeInTheDocument()
    }
    expect(screen.queryByTestId('kiosk-apps-pagina')).not.toBeInTheDocument()
    expect(screen.queryByTestId('kiosk-apps-arriba')).not.toBeInTheDocument()
    expect(screen.queryByTestId('kiosk-apps-abajo')).not.toBeInTheDocument()
  })

  it('con 6 apps pagina de 4 en 4: pulsar ▼ muestra el resto y actualiza el indicador', async () => {
    const seis = appsFicticias(6)
    render(<MenuApps apps={seis} tactil={false} disabled={false} onAbrir={vi.fn()} />)

    expect(screen.getByTestId('kiosk-apps-pagina')).toHaveTextContent('1/2')
    for (let i = 0; i < 4; i++) expect(screen.getByTestId(`kiosk-app-app-${i}`)).toBeInTheDocument()
    for (let i = 4; i < 6; i++) expect(screen.queryByTestId(`kiosk-app-app-${i}`)).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('kiosk-apps-abajo'))

    expect(screen.getByTestId('kiosk-apps-pagina')).toHaveTextContent('2/2')
    for (let i = 0; i < 4; i++) expect(screen.queryByTestId(`kiosk-app-app-${i}`)).not.toBeInTheDocument()
    for (let i = 4; i < 6; i++) expect(screen.getByTestId(`kiosk-app-app-${i}`)).toBeInTheDocument()
  })

  it('pulsar una app llama a onAbrir con su id', async () => {
    const onAbrir = vi.fn()
    render(<MenuApps apps={APPS} tactil={false} disabled={false} onAbrir={onAbrir} />)
    await userEvent.click(screen.getByTestId('kiosk-app-organizer'))
    expect(onAbrir).toHaveBeenCalledWith('organizer')
  })

  it('con disabled los botones de app quedan deshabilitados', () => {
    render(<MenuApps apps={APPS} tactil={false} disabled onAbrir={vi.fn()} />)
    for (const app of APPS) {
      expect(screen.getByTestId(`kiosk-app-${app.id}`)).toBeDisabled()
    }
  })
})
