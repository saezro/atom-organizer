import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FileField from './FileField'

describe('FileField', () => {
  it('muestra la etiqueta y el valor actual', () => {
    render(<FileField label="Carpeta origen" value="/datos/vuelo" onPick={() => {}} />)
    expect(screen.getByText('Carpeta origen')).toBeTruthy()
    expect(screen.getByDisplayValue('/datos/vuelo')).toBeTruthy()
  })

  it('llama a onPick al pulsar Elegir', () => {
    const onPick = vi.fn()
    render(<FileField label="Carpeta origen" value="" onPick={onPick} />)
    fireEvent.click(screen.getByRole('button'))
    expect(onPick).toHaveBeenCalledTimes(1)
  })

  it('llama a onType al teclear cuando se le pasa onType', () => {
    const onType = vi.fn()
    render(<FileField label="Carpeta origen" value="" onPick={() => {}} onType={onType} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '/otra' } })
    expect(onType).toHaveBeenCalledWith('/otra')
  })
})
