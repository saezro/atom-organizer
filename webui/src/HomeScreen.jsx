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

// `acceso` = `{organizer, estadillos}` del login usuario+contraseña (`null` =
// todo visible, como en Google). Sin `organizer` se ocultan organizar/subir en
// crudo; con solo `estadillos` aparece una puerta propia a la subida de estadillos.
const OPCION_ESTADILLOS = {
  id: 'estadillos',
  icono: 'bucket',
  titulo: 'Estadillos',
  detalle: 'Sube o baja los estadillos de una inspección.',
}

export default function HomeScreen({ onElegir, acceso = null }) {
  const sinOrganizer = acceso?.organizer === false
  const opciones = sinOrganizer
    ? [
        ...(acceso?.estadillos === false ? [] : [OPCION_ESTADILLOS]),
        ...OPCIONES.filter((o) => o.id === 'herramientas'),
      ]
    : OPCIONES
  return (
    <div className="home-grid">
      {opciones.map((o) => (
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
