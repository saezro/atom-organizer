export default function FileField({ label, value, onPick, onType, placeholder }) {
  // Si se pasa `onType`, el campo es editable → se puede escribir o PEGAR la
  // ruta a mano (fallback cuando el diálogo nativo no abre, p.ej. en Windows).
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <div className="field-row">
        <input
          className="glass-input"
          type="text"
          value={value}
          placeholder={placeholder || 'Elige, o escribe/pega la ruta aquí…'}
          onChange={onType ? (e) => onType(e.target.value) : undefined}
          readOnly={!onType}
          spellCheck={false}
        />
        <button type="button" className="btn-ghost" onClick={onPick}>
          Elegir…
        </button>
      </div>
    </label>
  )
}
