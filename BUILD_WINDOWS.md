# ATOM Organizer: compilar el instalador de Windows

Windows se distribuye como instalador **Inno Setup** (`ATOM-Organizer-Setup-vX.Y.Z.exe`) sobre un
empaquetado PyInstaller **onedir** (`atom_organizer_webview.spec`, con `COLLECT`). Ya no hay `.exe` portable.
El instalador lo define `packaging/windows/ATOM-Organizer.iss`.

## Camino normal: CI

`.github/workflows/release.yml` compila y publica en cada release:

- Windows: `ATOM-Organizer-Setup-vX.Y.Z.exe`.
- Linux: AppImage `ATOM_Organizer-<ver>-x86_64.AppImage` (`atom_organizer_webview_linux.spec`).

El CI usa Python 3.11 y Node 22, y **inyecta `google_client.json` desde secretos (`ATOM_GOOGLE_CLIENT_ID` y `ATOM_GOOGLE_CLIENT_SECRET`)** (el repo no
contiene ningún valor de cliente OAuth).

## Compilar a mano (solo Windows, PyInstaller no cross-compila)

Requisitos: Python 3.11 (con PATH), Node.js 22, Edge WebView2 Runtime, Inno Setup 6.

1. Ejecuta `build_windows.bat`: venv, dependencias Python, `npm ci && npm run build` y PyInstaller.
2. Resultado onedir: `dist\ATOM-Organizer\`.
3. Instalador: `ISCC.exe /DMyVersion=X.Y.Z /DMyTag=vX.Y.Z packaging\windows\ATOM-Organizer.iss`.
4. Para probar en local necesitas tu propio `google_client.json` (no se versiona).

## Notas

- `ThermoViewer.exe` no se incluye (licencia). Se instala aparte para vídeo térmico `.TMC`.
- Si la ventana no muestra nada, falta el WebView2 Runtime.
