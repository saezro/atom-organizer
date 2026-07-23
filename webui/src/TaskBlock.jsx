import { useState } from 'react'
import { api } from './bridge'

export function initialState(fields) {
  const s = {}
  for (const f of fields) {
    if (f.type === 'bool') s[f.name] = !!f.default
    else if (f.type === 'select') s[f.name] = 0 // índice de opción
    else s[f.name] = f.default ?? ''
  }
  return s
}

export function buildParams(fields, state) {
  const params = {}
  for (const f of fields) {
    const v = state[f.name]
    if (f.type === 'bool') params[f.name] = !!v
    else if (f.type === 'select') Object.assign(params, f.options[v]?.params || {})
    else if (f.type === 'number') {
      if (String(v).trim() !== '') params[f.name] = v
    } else params[f.name] = v // folder / file
  }
  return params
}

export default function TaskBlock({ block, running, onRun }) {
  const [state, setState] = useState(() => initialState(block.fields))
  const set = (name, value) => setState((s) => ({ ...s, [name]: value }))

  async function pick(field) {
    const path = field.type === 'file' ? await api.pickFile() : await api.pickFolder()
    if (path) set(field.name, path)
  }

  return (
    <section className="block">
      <h3 className="block-title">{block.title}</h3>
      <div className="block-grid">
        {block.fields.map((f) => (
          <Field key={f.name} f={f} value={state[f.name]} onSet={set} onPick={pick} />
        ))}
      </div>
      <button
        className="btn-run"
        disabled={running}
        onClick={() => onRun(block.task, buildParams(block.fields, state))}
      >
        {running ? 'Procesando…' : 'Ejecutar'}
      </button>
    </section>
  )
}

export function Field({ f, value, onSet, onPick }) {
  if (f.type === 'bool') {
    return (
      <label className="check block-field">
        <input
          type="checkbox"
          checked={!!value}
          onChange={(e) => onSet(f.name, e.target.checked)}
        />
        <span>{f.label}</span>
      </label>
    )
  }
  if (f.type === 'select') {
    return (
      <label className="block-field">
        <span className="field-label">{f.label}</span>
        <select
          className="glass-input"
          value={value}
          onChange={(e) => onSet(f.name, Number(e.target.value))}
        >
          {f.options.map((o, i) => (
            <option key={i} value={i}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    )
  }
  if (f.type === 'number' || f.type === 'text') {
    return (
      <label className="block-field">
        <span className="field-label">{f.label}</span>
        <input
          className="glass-input"
          type={f.type === 'number' ? 'number' : 'text'}
          value={value}
          onChange={(e) => onSet(f.name, e.target.value)}
        />
      </label>
    )
  }
  // folder / file
  return (
    <label className="block-field wide">
      <span className="field-label">{f.label}</span>
      <div className="field-row">
        <input className="glass-input" type="text" value={value} placeholder="—" readOnly />
        <button type="button" className="btn-ghost" onClick={() => onPick(f)}>
          Elegir…
        </button>
      </div>
    </label>
  )
}
