import { useState } from 'react'
import { api } from '../bridge'
import FileField from '../FileField'

export default function PasoCarpeta({ label, value, onChange, disabled, avisoNoVacia }) {
  const [noVacia, setNoVacia] = useState(null)

  async function elegir() {
    const path = await api.pickFolder()
    if (!path) return
    onChange(path)
    if (!avisoNoVacia) return
    try {
      const r = await api.folderIsEmpty(path)
      setNoVacia(r?.empty ? null : { count: r?.count ?? 0 })
    } catch {
      setNoVacia(null)
    }
  }

  return (
    <>
      <FileField
        label={label}
        value={value}
        onPick={disabled ? () => {} : elegir}
        onType={disabled ? undefined : onChange}
      />
      {noVacia && (
        <span className="field-hint hint-warn">
          La carpeta ya tiene {noVacia.count} ficheros. Elige una vacía para no mezclar vuelos.
        </span>
      )}
    </>
  )
}
