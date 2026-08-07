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
;    SIN UAC. Imprescindible para que el updater pueda lanzar el instalador en
;    silencio (/VERYSILENT) sin que salte un diálogo de administrador que el
;    usuario no vería (la app se cierra durante el proceso).
;  - CloseApplications + RestartApplications: el Restart Manager cierra la
;    instancia en marcha para poder sustituir los ficheros, y la reabre al acabar.
;    OJO: en el updater in-app va con /FORCECLOSEAPPLICATIONS porque el cierre
;    limpio no funciona (QtWebEngineProcess lo ignora), y entonces el Restart
;    Manager ya no reabre nada → la reapertura la hace la entrada [Run] silenciosa.

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
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Cierra la app en marcha (y la reabre) al actualizar
CloseApplications=yes
RestartApplications=yes
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
; fuerza, ya NO los revive con /RESTARTAPPLICATIONS (verificado en la VM,
; 2026-08-04: instalaba bien pero la app no volvía). `WizardSilent` acota esta
; entrada a /SILENT y /VERYSILENT, para no abrirla dos veces en la interactiva.
Filename: "{app}\{#MyAppExe}"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallDelete]
; Restos del updater (instaladores descargados en %TEMP%)
Type: filesandordirs; Name: "{localappdata}\Temp\atom-organizer-update"
