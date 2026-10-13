; Instalador Windows de ATOM Organizer (Inno Setup 6).
;
; Se compila desde la raíz del repo, con la versión inyectada desde version.py:
;   iscc /DMyVersion=3.2.0 packaging\windows\ATOM-Organizer.iss
; Espera el onedir de PyInstaller en dist\ATOM-Organizer\ y escribe el instalador
; en dist\installer\ATOM-Organizer-Setup-v<version>.exe
;
; Decisiones (equivalentes a las del atom-migrador con electron-builder/NSIS):
;  - AppId FIJO: es la clave por la que Windows reconoce una instalación previa y
;    hace upgrade in-place. NO CAMBIAR NUNCA: cambiarlo rompe las actualizaciones
;    (dejaría dos apps instaladas en paralelo).
;  - PrivilegesRequired=lowest → instalación por usuario en %LOCALAPPDATA%\Programs,
;    SIN UAC. Imprescindible para que el updater pueda lanzar el instalador sin
;    que salte un diálogo de administrador que el usuario no vería.
;  - El updater in-app usa /SILENT (barra de progreso visible, WizardSilent true),
;    no /VERYSILENT: así el usuario ve que se está actualizando y no reabre la app.
;  - SetupMutex (NO AppMutex: abortaría un silent install con la app viva): la app
;    lo detecta al arrancar y se niega a abrirse mientras se instala.
;  - CloseApplications: el Restart Manager cierra la instancia en marcha; en el
;    updater va con /FORCECLOSEAPPLICATIONS (QtWebEngineProcess ignora el cierre
;    limpio). NO hay RestartApplications: la reapertura la hace solo la entrada
;    [Run] con Check: WizardSilent (con --tras-actualizar).
;  - Bloque Code: PrepareToInstall espera a que mueran los procesos y comprueba que
;    QtCore.pyd se abre en exclusiva ANTES de copiar nada: si no, aborta con la
;    versión anterior intacta (evita la mezcla exe nuevo + _internal viejo).

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif
; MyTag = versión tal cual va en el tag (puede llevar sufijo: 3.2.0-rc1). Sólo
; se usa para el nombre del fichero; VersionInfoVersion exige números limpios.
#ifndef MyTag
  #define MyTag MyVersion
#endif

#define MyAppName "ATOM Organizer"
#define MyAppExe "ATOM-Organizer.exe"
#define MyPublisher "Aerotools UAV"

[Setup]
AppId={{1731D1F4-F803-4BF8-9F73-60048BB607F4}
AppName={#MyAppName}
AppVersion={#MyVersion}
AppVerName={#MyAppName} {#MyVersion}
AppPublisher={#MyPublisher}
AppPublisherURL=https://github.com/saezro/atom-organizer
DefaultDirName={autopf}\ATOM Organizer
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Instalación per-user: sin UAC, y así el updater puede correr en silencio.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist\installer
OutputBaseFilename=ATOM-Organizer-Setup-v{#MyTag}
; lzma2/fast + sin solid: compilar el instalador bajaba de ~3 min a ~1 min a cambio de un .exe algo mayor.
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
; Cierra la app en marcha al actualizar. La reapertura la hace [Run].
CloseApplications=yes
SetupMutex=ATOM-Organizer-Setup
CloseApplicationsFilter=*.exe
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExe}
VersionInfoVersion={#MyVersion}
VersionInfoCompany={#MyPublisher}
VersionInfoProductName={#MyAppName}
#if FileExists("..\..\assets\atom-icon.ico")
SetupIconFile=..\..\assets\atom-icon.ico
#endif

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[InstallDelete]
; Solo assets\, que es donde Vite nombra por hash y el instalador, que sobrescribe
; por nombre, dejaría huérfanos. El resto de webui\dist (index.html y compañía)
; tiene nombre estable: se sobrescribe solo, y borrarlo obligaba a reescribirlo
; entero en cada actualización.
Type: filesandordirs; Name: "{app}\webui\dist\assets"
Type: filesandordirs; Name: "{app}\_internal\webui\dist\assets"

[Files]
; Todo el onedir de PyInstaller (incluye _internal\ con Qt, pyexiv2, programas_externos…)
Source: "..\..\dist\ATOM-Organizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
; Tras una instalación interactiva, ofrecer abrir (casilla al final del asistente).
Filename: "{app}\{#MyAppExe}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent
; En modo silencioso (updater in-app) hay que relanzarla a mano: el updater usa
; /FORCECLOSEAPPLICATIONS y el Restart Manager, tras matar los procesos a la
; fuerza, ya NO los revive (verificado en la VM, 2026-08-04: instalaba bien pero
; la app no volvía). `WizardSilent` acota esta
; entrada a /SILENT y /VERYSILENT, para no abrirla dos veces en la interactiva.
Filename: "{app}\{#MyAppExe}"; Parameters: "--tras-actualizar"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallDelete]
; Restos del updater (instaladores descargados en %TEMP%)
Type: filesandordirs; Name: "{localappdata}\Temp\atom-organizer-update"

[Code]
// Devuelve True si queda (o no se puede descartar que quede) algún proceso de la
// app vivo. Usa tasklist (por nombre de imagen); si no se puede saber -> True.
function ProcesoVivo(Nombre: String): Boolean;
var
  Salida: String;
  Contenido: AnsiString;
  Code: Integer;
begin
  Result := True;
  Salida := ExpandConstant('{tmp}\atom_tasklist.txt');
  DeleteFile(Salida);
  if not Exec(ExpandConstant('{cmd}'),
              '/S /C "tasklist /FI "IMAGENAME eq ' + Nombre + '" /NH > "' + Salida + '" 2>&1"',
              '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Exit;
  if not FileExists(Salida) then
    Exit;
  if not LoadStringFromFile(Salida, Contenido) then
    Exit;
  Result := Pos(Lowercase(Nombre), Lowercase(String(Contenido))) > 0;
end;

function QuedanProcesos(): Boolean;
begin
  Result := ProcesoVivo('ATOM-Organizer.exe') or ProcesoVivo('QtWebEngineProcess.exe');
end;

// True si QtCore.pyd se puede abrir en exclusiva (nadie lo tiene cargado).
// Renombrar NO sirve: Windows deja renombrar una DLL cargada.
function PydLibre(Pyd: String): Boolean;
var
  S: TFileStream;
begin
  Result := True;
  if not FileExists(Pyd) then
    Exit;
  try
    S := TFileStream.Create(Pyd, fmOpenReadWrite or fmShareExclusive);
    S.Free;
  except
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Inicio, Ahora: DWORD;
  Code: Integer;
  Matado: Boolean;
begin
  Result := '';
  // Instalación nueva: nada que proteger.
  if not DirExists(ExpandConstant('{app}')) then
    Exit;

  // Espera hasta 20 s reales; a los 10 s, taskkill de los dos nombres (una vez).
  Inicio := GetTickCount;
  Matado := False;
  while QuedanProcesos() do
  begin
    Ahora := GetTickCount - Inicio;
    if Ahora >= 20000 then
      Break;
    if (Ahora >= 10000) and (not Matado) then
    begin
      Matado := True;
      Exec('taskkill.exe', '/F /IM ATOM-Organizer.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
      Exec('taskkill.exe', '/F /IM QtWebEngineProcess.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
    end;
    Sleep(500);
  end;

  // Comprobación final: apertura exclusiva de QtCore.pyd.
  if not PydLibre(ExpandConstant('{app}\_internal\PySide6\QtCore.pyd')) then
    Result := 'ATOM Organizer sigue abierto. Ciérralo y vuelve a intentar la actualización.';
end;
