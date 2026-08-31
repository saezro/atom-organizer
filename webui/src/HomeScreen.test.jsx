import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import HomeScreen from './HomeScreen.jsx'

describe('HomeScreen', () => {
  // El nombre accesible del botón concatena título + descripción (ambos son
  // texto dentro del <button>), así que se ancla al inicio con `^` para no
  // confundir "Organizar" con el resto de la frase de otra tarjeta.
  it('pinta las tres tarjetas con sus títulos', () => {
    render(<HomeScreen onElegir={vi.fn()} />)
    expect(screen.getByRole('button', { name: /^organizar\b/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^subir en crudo\b/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^herramientas extra\b/i })).toBeInTheDocument()
  })

  it('pulsar "Organizar" llama a onElegir con "organizar"', async () => {
    const onElegir = vi.fn()
    render(<HomeScreen onElegir={onElegir} />)
    await userEvent.click(screen.getByRole('button', { name: /^organizar\b/i }))
    expect(onElegir).toHaveBeenCalledWith('organizar')
  })

  it('pulsar "Subir en crudo" llama a onElegir con "subir"', async () => {
    const onElegir = vi.fn()
    render(<HomeScreen onElegir={onElegir} />)
    await userEvent.click(screen.getByRole('button', { name: /^subir en crudo\b/i }))
    expect(onElegir).toHaveBeenCalledWith('subir')
  })

  it('pulsar "Herramientas extra" llama a onElegir con "herramientas"', async () => {
    const onElegir = vi.fn()
    render(<HomeScreen onElegir={onElegir} />)
    await userEvent.click(screen.getByRole('button', { name: /^herramientas extra\b/i }))
    expect(onElegir).toHaveBeenCalledWith('herramientas')
  })
})
