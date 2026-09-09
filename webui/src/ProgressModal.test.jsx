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
      maquina={null}
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

describe('ProgressModal — sonda inicial de la máquina', () => {
  it('pinta el texto de la sonda', () => {
    renderModal({
      maquina: {
        disco_origen: { tipo: 'HDD', modelo: 'X', unidad: 'C:' },
        disco_destino: { tipo: 'SSD', modelo: 'Y', unidad: 'D:' },
        mismo_disco: false,
        nucleos: 8,
        ram_total_gb: 16,
        ram_libre_gb: 4,
        cpu_ocupada_pct: 30,
        maquina_ocupada: false,
        texto: 'Origen HDD · Destino SSD · 8 núcleos',
      },
    })

    expect(screen.getByText('Origen HDD · Destino SSD · 8 núcleos')).toBeTruthy()
  })
})

describe('ProgressModal — veredicto EN VIVO', () => {
  it('veredicto "disco" en vivo muestra el aviso con cifras mientras el run no ha terminado', () => {
    renderModal({
      finished: null,
      stats: {
        recursos_vivo: {
          mb_por_segundo: 36.4,
          cpu_pct: 47,
          nucleos: 8,
          tipo_disco: 'HDD',
          veredicto: 'disco',
        },
      },
    })

    expect(
      screen.getByText('⚠ El disco es el cuello de botella — 36,4 MB/s (HDD)')
    ).toBeTruthy()
  })

  it('no aparece el aviso en vivo si recursos_vivo es null', () => {
    renderModal({
      finished: null,
      stats: { recursos_vivo: null },
    })

    expect(screen.queryByText(/cuello de botella/i)).toBeNull()
  })

  it('el resumen final sigue funcionando aunque haya recursos_vivo (run ya terminado)', () => {
    renderModal({
      finished: { ok: true },
      stats: {
        recursos_vivo: {
          mb_por_segundo: 36.4,
          cpu_pct: 47,
          nucleos: 8,
          tipo_disco: 'HDD',
          veredicto: 'disco',
        },
      },
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
    expect(screen.queryByText(/El disco es el cuello de botella/)).toBeNull()
  })
})
