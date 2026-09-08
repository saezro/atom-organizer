import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import ProgressModal from './ProgressModal.jsx'

// Props mínimas comunes: solo se sobreescribe lo que cada test necesita.
function renderModal(props = {}) {
  return render(
    <ProgressModal
      plant="ALPHA"
      phases={[]}
      progress={0}
      stats={null}
      detail={[]}
      finished={null}
      recursosTotales={null}
      onClose={() => {}}
      {...props}
    />
  )
}

describe('ProgressModal — métricas de disco/CPU por fase', () => {
  it('fase con recursos muestra MB/s y CPU', () => {
    renderModal({
      phases: [
        {
          name: 'Copiando',
          status: 'done',
          duration: 12.4,
          errors: 0,
          recursos: { mb_leidos: 100, mb_escritos: 50, mb_por_segundo: 48, cpu_pct: 21, nucleos: 8, veredicto: null },
        },
      ],
    })

    expect(screen.getByText('48 MB/s · CPU 21%')).toBeTruthy()
  })

  it('fase sin recursos no pinta la línea y no revienta', () => {
    renderModal({
      phases: [
        { name: 'Copiando', status: 'done', duration: 12.4, errors: 0 },
      ],
    })

    expect(screen.queryByText(/MB\/s/)).toBeNull()
    expect(screen.getByText('Copiando')).toBeTruthy()
  })
})

describe('ProgressModal — veredicto de cuello de botella del run', () => {
  it('veredicto "disco" muestra el aviso de cuello de botella', () => {
    renderModal({
      finished: { ok: true },
      recursosTotales: {
        mb_leidos: 1200,
        mb_escritos: 3400,
        mb_por_segundo: 48,
        cpu_pct: 21,
        nucleos: 8,
        veredicto: 'disco',
      },
    })

    expect(screen.getByText('⚠ Cuello de botella: disco')).toBeTruthy()
  })

  it('veredicto null no pinta ninguna línea de veredicto', () => {
    renderModal({
      finished: { ok: true },
      recursosTotales: {
        mb_leidos: 1200,
        mb_escritos: 3400,
        mb_por_segundo: 48,
        cpu_pct: 21,
        nucleos: 8,
        veredicto: null,
      },
    })

    expect(screen.queryByText(/Cuello de botella/)).toBeNull()
  })
})
