// Esquema declarativo de las secciones AEROTOOLS y OTROS EQUIPOS.
// Cada bloque = un task del pipeline; cada field está keyeado por el nombre
// EXACTO del campo de su dataclass de config (lo que consume run_task en el
// backend). Tipos: 'folder' | 'file' | 'number' | 'bool' | 'select'.
// Un 'select' fija uno o varios params vía `options[].params`.

export const AEROTOOLS = [
  {
    task: 'do_extraction',
    title: 'Térmica · extracción',
    fields: [
      { name: 'folder', type: 'folder', label: 'Carpeta origen' },
      { name: 'output_folder', type: 'folder', label: 'Carpeta de salida' },
      { name: 'estad', type: 'file', label: 'Estadillo' },
      { name: 'auto', type: 'bool', label: 'Temperaturas TMC automáticas', default: true },
      { name: 'temp_min', type: 'number', label: 'Temperatura mínima', default: '0' },
      { name: 'temp_max', type: 'number', label: 'Temperatura máxima', default: '0' },
      { name: 'pre_extraction', type: 'bool', label: 'Pre-extracción', default: true },
      { name: 'extraction', type: 'bool', label: 'Extracción', default: true },
    ],
  },
  {
    task: 'rgb_aerotools',
    title: 'Procesamiento RGB',
    fields: [
      { name: 'input_folder', type: 'folder', label: 'Carpeta origen' },
      { name: 'output_folder', type: 'folder', label: 'Carpeta destino' },
      { name: 'estad', type: 'file', label: 'Estadillo' },
      { name: 'logs_folder', type: 'folder', label: 'Carpeta Logs' },
      { name: 'compress_rgb', type: 'bool', label: 'Comprimir RGB', default: true },
      { name: 'compress_level', type: 'number', label: 'Factor de calidad', default: '40' },
      { name: 'rename_images', type: 'bool', label: 'Renombrar imágenes', default: true },
      { name: 'geotagging', type: 'bool', label: 'Georreferencia y location', default: true },
      { name: 'organize_images', type: 'bool', label: 'Organizar imágenes', default: true },
      { name: 'seconds_range_image_estad', type: 'number', label: 'Margen s imagen–estadillo', default: '30' },
      { name: 'seconds_range_log_estad', type: 'number', label: 'Margen s log–estadillo', default: '30' },
      { name: 'mismatch_hours', type: 'number', label: 'Desfase horas estadillo→imagen', default: '0' },
      { name: 'mismatch_minutes', type: 'number', label: 'Desfase minutos estadillo→imagen', default: '0' },
    ],
  },
  {
    task: 'gen_thumbnails_aerotools',
    title: 'Rotación y miniaturas',
    fields: [
      { name: 'aerotools_input_folder', type: 'folder', label: 'Carpeta origen' },
      { name: 'aerotools_rgb', type: 'bool', label: 'RGB', default: true },
      { name: 'aerotools_termica', type: 'bool', label: 'Térmicas', default: true },
      { name: 'aerotools_max_error', type: 'number', label: 'Mín. imágenes que deben girar igual (%)', default: '50' },
      { name: 'aerotools_add_to_angle', type: 'number', label: 'Margen Yaw + (grados)', default: '80' },
      { name: 'aerotools_subs_to_angle', type: 'number', label: 'Margen Yaw − (grados)', default: '80' },
    ],
  },
  {
    task: 'manual_geotagging',
    title: 'Geoetiquetado manual',
    fields: [
      { name: 'select_images_folder', type: 'folder', label: 'Carpeta imágenes' },
      { name: 'select_log', type: 'file', label: 'Log' },
      { name: 'estad', type: 'file', label: 'Estadillo' },
      { name: 'logs_folder', type: 'folder', label: 'Carpeta Logs' },
    ],
  },
]

export const OTROS = [
  {
    task: 'compress_image',
    title: 'Comprimir imágenes',
    fields: [
      { name: 'input_folder', type: 'folder', label: 'Carpeta origen' },
      { name: 'output_folder', type: 'folder', label: 'Carpeta destino' },
      { name: 'include_subfolders', type: 'bool', label: 'Incluir subcarpetas', default: false },
      { name: 'compress_level', type: 'number', label: 'Factor de calidad', default: '40' },
    ],
  },
  {
    task: 'gen_meta_location',
    title: 'Generar meta y location',
    fields: [
      { name: 'input_folder', type: 'folder', label: 'Carpeta origen' },
      {
        name: '__mode', type: 'select', label: 'Modo',
        options: [
          { label: 'Ambos', params: { mode_location_and_meta: true } },
          { label: 'Meta', params: { mode_meta: true } },
          { label: 'Location', params: { mode_location: true } },
        ],
      },
      { name: 'flight_height', type: 'number', label: 'Altura de vuelo', default: '0' },
      { name: 'calculate_proyected_distance', type: 'bool', label: 'Calcular distancia proyectada', default: false },
    ],
  },
  {
    task: 'gen_struct',
    title: 'Estructura de carpetas · organizar',
    fields: [
      { name: 'output_folder', type: 'folder', label: 'Carpeta' },
      { name: 'estad', type: 'file', label: 'Estadillo' },
      { name: 'organize_images', type: 'bool', label: 'Organizar imágenes', default: true },
      { name: 'include_v', type: 'bool', label: 'Incluir V', default: true },
      { name: 'seconds_range', type: 'number', label: 'Margen segundos', default: '30' },
      { name: 'mismatch_hours', type: 'number', label: 'Desfase horas estadillo→imagen', default: '0' },
      { name: 'mismatch_minutes', type: 'number', label: 'Desfase minutos estadillo→imagen', default: '0' },
    ],
  },
  {
    task: 'gen_thumbnails',
    title: 'Rotación y miniaturas',
    fields: [
      { name: 'input_folder', type: 'folder', label: 'Carpeta origen' },
      { name: 'rgb', type: 'bool', label: 'RGB', default: true },
      { name: 'termica', type: 'bool', label: 'Térmicas', default: true },
      { name: 'choose_mode_auto', type: 'bool', label: 'Modo automático', default: true },
      { name: 'rotate_90', type: 'bool', label: 'Rotar 90°', default: false },
      { name: 'max_error', type: 'number', label: 'Mín. imágenes que deben girar igual (%)', default: '50' },
      { name: 'add_to_angle', type: 'number', label: 'Margen Yaw + (grados)', default: '80' },
      { name: 'subs_to_angle', type: 'number', label: 'Margen Yaw − (grados)', default: '80' },
    ],
  },
  {
    task: 'rename_images',
    title: 'Renombrado de imágenes',
    fields: [{ name: 'output_folder', type: 'folder', label: 'Carpeta' }],
  },
  {
    task: 'convert_to_tif',
    title: 'Convertir DJI → TIF',
    fields: [
      { name: 'input_folder', type: 'folder', label: 'Carpeta' },
      {
        name: 'dron_selector', type: 'select', label: 'Selector de dron',
        options: [
          { label: 'M2EA', params: { dron_selector: 'M2EA' } },
          { label: 'M4T', params: { dron_selector: 'M4T' } },
        ],
      },
      { name: 'emissivity', type: 'number', label: 'Emisividad', default: '0.95' },
      { name: 'humidity', type: 'number', label: 'Humedad', default: '70' },
      { name: 'temp_auto', type: 'bool', label: 'Auto Tª habilitado', default: false },
      { name: 'up_temperature', type: 'number', label: 'Umbral Tª máxima', default: '0' },
      { name: 'low_temperature', type: 'number', label: 'Umbral Tª mínima', default: '0' },
      {
        name: '__rot', type: 'select', label: 'Giro',
        options: [
          { label: 'Sin giro', params: {} },
          { label: 'Auto', params: { rotate_auto: true } },
          { label: '90º', params: { rotate_90: true } },
          { label: '-90º', params: { rotate_minus_90: true } },
        ],
      },
      { name: 'solo_seleccion_atom', type: 'bool', label: 'Solo Seleccion_ATOM', default: false },
      { name: 'create_gray_scale_images', type: 'bool', label: 'Generar escala de grises JPG', default: false },
    ],
  },
  {
    task: 'rgb_cropping',
    title: 'Recorte RGB',
    fields: [
      { name: 'output_folder', type: 'folder', label: 'Carpeta' },
      { name: 'choose_mode_auto', type: 'bool', label: 'Modo automático', default: true },
      { name: 'crop_percentage', type: 'number', label: 'Porcentaje de recorte', default: '0' },
    ],
  },
  {
    task: 'tif_rotating',
    title: 'Girar imágenes TIF',
    fields: [
      { name: 'input_folder_2', type: 'folder', label: 'Carpeta' },
      {
        name: '__rot', type: 'select', label: 'Giro',
        options: [
          { label: '90º', params: { rotate_90: true } },
          { label: '-90º', params: { rotate_minus_90: true } },
          { label: '180', params: { rotate_180: true } },
        ],
      },
    ],
  },
]

// Modo avanzado de "Organizar completo" (task split_images). Las 7 secciones
// espejan el _build_advanced_panel de la GUI Qt (ya validado por Cas), keyeadas
// por el nombre EXACTO del campo de SplitImagesConfig. Los defaults reproducen
// _default_split_config: abrir el panel y ejecutar == run por defecto. NO se
// incluyen los 4 controles de la card principal (input/output/estad/rename) ni
// organize_images (lo deriva el backend de la presencia de estadillo).
export const SPLIT_ADVANCED = [
  {
    title: 'Separación RGB / térmica',
    fields: [
      {
        name: '__split_mode', type: 'select', label: 'Modo de separación',
        options: [
          { label: 'Por sufijo', params: { choose_mode_size: false } },
          { label: 'Por tamaño', params: { choose_mode_size: true } },
        ],
      },
      { name: 'max_size', type: 'number', label: 'Tamaño máx. (separación)', default: '0' },
      { name: 'end_rgb_files', type: 'text', label: 'Sufijo RGB', default: '' },
      { name: 'end_rgb_extra_files', type: 'text', label: 'Sufijo RGB extra', default: '' },
      { name: 'end_thermo_files', type: 'text', label: 'Sufijo térmicas', default: '_T' },
    ],
  },
  {
    title: 'Compresión RGB',
    fields: [
      { name: 'compress_rgb', type: 'bool', label: 'Comprimir RGB', default: true },
      { name: 'compress_level', type: 'number', label: 'Factor de calidad', default: '40' },
    ],
  },
  {
    title: 'Organización por estadillo',
    fields: [
      { name: 'include_v', type: 'bool', label: 'Incluir V', default: true },
      { name: 'seconds_range', type: 'number', label: 'Margen segundos', default: '30' },
      { name: 'mismatch_hours', type: 'number', label: 'Desfase horas estadillo→imagen', default: '0' },
      { name: 'mismatch_minutes', type: 'number', label: 'Desfase minutos estadillo→imagen', default: '0' },
    ],
  },
  {
    title: 'Generar meta y GPS',
    fields: [
      { name: 'gen_meta_location', type: 'bool', label: 'Generar meta y location', default: true },
      { name: 'calculate_proyected_distance', type: 'bool', label: 'Calcular distancia proyectada', default: false },
      { name: 'flight_height', type: 'number', label: 'Altura de vuelo', default: '0' },
    ],
  },
  {
    title: 'Miniaturas y rotación',
    fields: [
      { name: 'gen_thumbnails', type: 'bool', label: 'Generar miniaturas', default: true },
      { name: 'gen_thumbnails_rgb', type: 'bool', label: 'RGB', default: true },
      { name: 'gen_thumbnails_termica', type: 'bool', label: 'Térmicas', default: true },
      { name: 'choose_mode_auto', type: 'bool', label: 'Modo automático', default: true },
      { name: 'gen_thumbnails_rotate_90', type: 'bool', label: 'Rotar 90° (modo manual)', default: false },
      { name: 'gen_thumbnails_max_error', type: 'number', label: 'Mín. imágenes que deben girar igual (%)', default: '50' },
      { name: 'gen_thumbnails_add_to_angle', type: 'number', label: 'Margen Yaw + (grados)', default: '80' },
      { name: 'gen_thumbnails_subs_to_angle', type: 'number', label: 'Margen Yaw − (grados)', default: '80' },
    ],
  },
  {
    title: 'Convertir DJI → TIF (térmica)',
    fields: [
      { name: 'convert_to_tif', type: 'bool', label: 'Convertir a TIF', default: true },
      {
        name: '__dron', type: 'select', label: 'Selector de dron',
        options: [
          { label: '(automático)', params: { convert_to_tif_dron_selector: '' } },
          { label: 'M2EA', params: { convert_to_tif_dron_selector: 'M2EA' } },
          { label: 'M4T', params: { convert_to_tif_dron_selector: 'M4T' } },
        ],
      },
      {
        name: '__tif_rot', type: 'select', label: 'Giro',
        // Cada opción fija los TRES bools (mutuamente excluyentes); el default
        // de _default_split_config es rotate_auto=True → índice 1.
        options: [
          { label: 'Sin giro', params: { convert_to_tiff_rotate_auto: false, convert_to_tiff_rotate_90: false, convert_to_tiff_rotate_minus_90: false } },
          { label: 'Auto', params: { convert_to_tiff_rotate_auto: true, convert_to_tiff_rotate_90: false, convert_to_tiff_rotate_minus_90: false } },
          { label: '90º', params: { convert_to_tiff_rotate_auto: false, convert_to_tiff_rotate_90: true, convert_to_tiff_rotate_minus_90: false } },
          { label: '-90º', params: { convert_to_tiff_rotate_auto: false, convert_to_tiff_rotate_90: false, convert_to_tiff_rotate_minus_90: true } },
        ],
        default: 1,
      },
      { name: 'convert_to_tif_emissivity', type: 'number', label: 'Emisividad', default: '0.95' },
      { name: 'convert_to_tif_humidity', type: 'number', label: 'Humedad', default: '70' },
      { name: 'convert_to_tif_up_temperature', type: 'number', label: 'Umbral Tª máxima', default: '0' },
      { name: 'convert_to_tif_low_temperature', type: 'number', label: 'Umbral Tª mínima', default: '0' },
      { name: 'convert_to_tif_temp_auto', type: 'bool', label: 'Auto Tª habilitado', default: false },
      { name: 'convert_to_tif_create_gray_scale_images', type: 'bool', label: 'Generar escala de grises JPG', default: false },
      { name: 'convert_to_tif_solo_seleccion_atom', type: 'bool', label: 'Solo Seleccion_ATOM', default: false },
    ],
  },
  {
    title: 'Recorte RGB',
    fields: [
      { name: 'cropping_rgb', type: 'bool', label: 'Recortar RGB', default: true },
      { name: 'cropping_mode_auto', type: 'bool', label: 'Modo automático', default: true },
      { name: 'crop_percentage', type: 'number', label: 'Porcentaje de recorte', default: '0' },
    ],
  },
]

export const SECTIONS = {
  aerotools: { label: 'AEROTOOLS', blocks: AEROTOOLS },
  otros: { label: 'OTROS EQUIPOS', blocks: OTROS },
}
