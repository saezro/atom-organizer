@echo off
REM ============================================================================
REM  build_windows.bat  -  Compila ATOM Organizer (UI React/pywebview) a un
REM  EXE portable un-click para Windows:  dist\ATOM-Organizer.exe
REM
REM  Requisitos en ESTA maquina Windows (una sola vez):
REM    - Python 3.11 (marca "Add to PATH" al instalar)   ->  python --version
REM    - Node.js 18+ (incluye npm)                        ->  node --version
REM    - Edge WebView2 Runtime (ya viene en Win10/11 actualizados; si no,
REM      instalar el "Evergreen Standalone" de Microsoft, gratis).
REM
REM  Uso:  doble-clic, o en una consola:  build_windows.bat
REM  PyInstaller NO cross-compila -> esto DEBE correr en Windows, no en Linux.
REM ============================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo(
echo === [1/5] Comprobando Python y Node ===
where python >nul 2>&1 || (echo ERROR: Python no esta en PATH. Instala Python 3.11. & pause & exit /b 1)
where node   >nul 2>&1 || (echo ERROR: Node no esta en PATH. Instala Node.js 18+.  & pause & exit /b 1)
python --version
node --version

echo(
echo === [2/5] Entorno virtual + dependencias Python ===
if not exist ".venv\Scripts\python.exe" python -m venv .venv || (echo ERROR creando venv & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
REM requirements.txt = pinned base (mismo que Windows); + capa webview; + PyInstaller.
pip install -r requirements.txt            || (echo ERROR: pip requirements.txt & pause & exit /b 1)
pip install -r requirements-webview.txt    || (echo ERROR: pip requirements-webview & pause & exit /b 1)
pip install pyinstaller==6.6.0             || (echo ERROR: pip pyinstaller & pause & exit /b 1)
REM En Windows pywebview arrastra pythonnet (WebView2) por marker de plataforma.

echo(
echo === [3/5] Build del front React (Vite) ===
pushd webui
call npm ci        || (echo ERROR: npm ci & popd & pause & exit /b 1)
call npm run build || (echo ERROR: npm run build & popd & pause & exit /b 1)
popd
if not exist "webui\dist\index.html" (echo ERROR: no se genero webui\dist & pause & exit /b 1)

echo(
echo === [4/5] Empaquetado onefile con PyInstaller ===
pyinstaller --clean --noconfirm atom_organizer_webview.spec || (echo ERROR: PyInstaller & pause & exit /b 1)

echo(
echo === [5/5] Resultado ===
if exist "dist\ATOM-Organizer.exe" (
    echo OK -^> dist\ATOM-Organizer.exe
    for %%F in ("dist\ATOM-Organizer.exe") do echo Tamano: %%~zF bytes
    echo(
    echo Prueba: doble-clic en dist\ATOM-Organizer.exe . Debe abrir la ventana ATOM Organizer.
    echo Para repartir al compañero: copia SOLO ese .exe ^(es portable^).
    echo Nota: ThermoViewer.exe NO va dentro; se instala aparte para el video termico .TMC.
) else (
    echo ERROR: no se genero dist\ATOM-Organizer.exe . Revisa el log de arriba.
)
echo(
pause
endlocal
