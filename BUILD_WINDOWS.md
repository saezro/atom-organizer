# ATOM Organizer — compilar el EXE portable de Windows

La nueva UI (React + pywebview) se reparte como **un solo `.exe` portable** (un click, sin instalación).
**PyInstaller no cross-compila** → el `.exe` hay que generarlo **en una máquina Windows**, no en Linux.

## Requisitos (una sola vez, en el Windows donde compiles)

- **Python 3.11** — al instalar marca *"Add Python to PATH"*.
- **Node.js 18+** (trae `npm`).
- **Edge WebView2 Runtime** — ya viene en Windows 10/11 actualizados. Si el `.exe` no abre,
  instala el *"WebView2 Evergreen Standalone Installer"* de Microsoft (gratis).

## Compilar

1. Copia **toda** la carpeta del proyecto (`atom-organizer/`) a la máquina Windows.
2. Doble-clic en **`build_windows.bat`** (o ejecútalo en una consola).
   Hace: venv → deps Python → `npm ci && npm run build` → PyInstaller onefile.
3. Sale **`dist\ATOM-Organizer.exe`**.

## Repartir al compañero

- Copia **solo** `dist\ATOM-Organizer.exe` — es portable (doble-clic y abre).
- **ThermoViewer.exe NO va dentro** (licencia). Para procesar vídeo térmico `.TMC` el compañero
  debe instalarlo aparte; la app lo busca en `C:\Program Files (x86)\ThermoViewer\`.
  El resto del pipeline (RGB/térmica, estadillo, GPS, TIF) funciona sin él.

## Ojo / no validado todavía

- **Nunca se ha ejecutado en Windows real.** La primera compilación es también la primera prueba:
  ábrelo tú antes de repartirlo y confirma que arranca y hace una corrida.
- Tamaño esperado ~150–250 MB (onefile mete Python + PySide6 core + front). Arranque algo lento
  (autoextrae a temp) — normal en onefile.
- Si al abrir no se ve nada: falta el **WebView2 Runtime** (ver arriba).
- Icono del `.exe`: hoy no hay `assets\atom-icon.ico` → sale sin icono. Para ponerlo, convierte
  `assets\atom-icon.svg` a `.ico` y recompila (el spec lo detecta solo).
