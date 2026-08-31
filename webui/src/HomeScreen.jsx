import NavIcon from './NavIcon'

// Home de opciones, delante de la pantalla de trabajo: tres puertas de
// entrada (organizar aquí, subir en crudo, herramientas extra). Solo pinta;
// `onElegir(id)` decide qué pantalla mostrar después, la cablea `App.jsx`.
const OPCIONES = [
  {
    id: 'organizar',
    icono: 'organizar',
    titulo: 'Organizar',
    detalle: 'Ordena las fotos de un vuelo en este ordenador.',
  },
  {
    id: 'subir',
    icono: 'bucket',
    titulo: 'Subir en crudo',
    detalle: 'Sube la carpeta del vuelo al bucket tal cual.',
  },
  {
    id: 'herramientas',
    icono: 'herramientas',
    titulo: 'Herramientas extra',
    detalle: 'Accesos y utilidades de Aerotools.',
  },
]

export default function HomeScreen({ onElegir }) {
  return (
    <div className="home-grid">
      {OPCIONES.map((o) => (
        <button
          key={o.id}
          type="button"
          className="home-card"
          onClick={() => onElegir(o.id)}
        >
          <span className="home-card-icono">
            <NavIcon id={o.icono} />
          </span>
          <span className="home-card-titulo">{o.titulo}</span>{' '}
          <span className="home-card-detalle">{o.detalle}</span>
        </button>
      ))}
    </div>
  )
}
