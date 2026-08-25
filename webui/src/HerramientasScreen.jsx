import { SECTIONS } from './schema'
import TaskBlock from './TaskBlock'

// Agrupa las dos pantallas que antes vivían en pestañas propias (AEROTOOLS /
// OTROS EQUIPOS) bajo el nuevo tab «Herramientas». No cambia su lógica ni sus
// props: solo las envuelve, cada una con su cabecera (antes la distinción
// entre las dos venía dada por la pestaña activa en el nav; al fundirlas en
// una sola pantalla hace falta un título por grupo para no perder esa señal).
export default function HerramientasScreen({ running, onRun }) {
  return (
    <>
      <h2 className="card-title">{SECTIONS.aerotools.label}</h2>
      <div className="section-blocks">
        {SECTIONS.aerotools.blocks.map((b) => (
          <TaskBlock key={b.task} block={b} running={running} onRun={onRun} />
        ))}
      </div>
      <h2 className="card-title">{SECTIONS.otros.label}</h2>
      <div className="section-blocks">
        {SECTIONS.otros.blocks.map((b) => (
          <TaskBlock key={b.task} block={b} running={running} onRun={onRun} />
        ))}
      </div>
    </>
  )
}
