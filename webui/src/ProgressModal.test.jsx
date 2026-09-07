import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'

import ProgressModal from './ProgressModal.jsx'

// Indicador de actividad indeterminado (Cambio A): la fase ACTIVA lleva la
// clase de animación continua y las demás no, y la barra de progreso pasa a
// modo indeterminado mientras `progress === 0`, para distinguir "trabajando"
// de "colgado" en los tramos mudos del backend.
describe('ProgressModal — indicador de actividad', () => {
  const phases = [
    { name: 'Separación RGB/Térmica', status: 'active' },
    { name: 'Estructura de carpetas', status: 'pending' },
  ]

  it('la fase activa lleva pm-ico-spin y la pendiente no', () => {
    const { container } = render(
      <ProgressModal
        plant="TEST"
        phases={phases}
        progress={0}
        stats={null}
        detail={[]}
        finished={null}
        onClose={() => {}}
      />
    )
    const items = container.querySelectorAll('.pm-phase')
    expect(items[0].querySelector('.pm-ico').className).toContain('pm-ico-spin')
    expect(items[1].querySelector('.pm-ico').className).not.toContain('pm-ico-spin')
  })

  it('con progress===0 en la fase activa pinta la barra indeterminada', () => {
    const { container } = render(
      <ProgressModal
        plant="TEST"
        phases={phases}
        progress={0}
        stats={null}
        detail={[]}
        finished={null}
        onClose={() => {}}
      />
    )
    const fill = container.querySelector('.pm-bar-fill')
    expect(fill.className).toContain('pm-bar-fill-indeterminate')
  })

  it('con progress>0 vuelve al comportamiento normal (sin indeterminada)', () => {
    const { container } = render(
      <ProgressModal
        plant="TEST"
        phases={phases}
        progress={42}
        stats={null}
        detail={[]}
        finished={null}
        onClose={() => {}}
      />
    )
    const fill = container.querySelector('.pm-bar-fill')
    expect(fill.className).not.toContain('pm-bar-fill-indeterminate')
    expect(fill.style.width).toBe('42%')
  })

  it('el <li> "Preparando…" (sin fases todavía) también lleva pm-ico-spin', () => {
    const { container } = render(
      <ProgressModal
        plant=""
        phases={[]}
        progress={0}
        stats={null}
        detail={[]}
        finished={null}
        onClose={() => {}}
      />
    )
    const ico = container.querySelector('.pm-phase .pm-ico')
    expect(ico.className).toContain('pm-ico-spin')
  })
})
