; Inno Setup script: wraps dist\TheThirdComing into TheThirdComing-Setup.exe.
;   Built by packaging\build.ps1 (needs Inno Setup 6: winget install JRSoftware.InnoSetup)
;
; Per-user install (no admin prompt) into %LOCALAPPDATA%\Programs\The Third Coming.
; The installer stays small: on first launch the app's setup window offers to install Ollama (from ollama.com)
; and download the AI models, and the voice models download by themselves.

#define AppName "The Third Coming"
#define AppExe "TheThirdComing.exe"
#define AppVersion "1.0.0"

[Setup]
AppId={{6E7C3B2A-3D1F-4C5B-9E2A-7A1B3C4D5E6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Andrew
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=TheThirdComing-Setup
SetupIconFile=third_coming.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start {#AppName} when I sign in to Windows"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\TheThirdComing\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Same value the app's own "Start with Windows" setting uses, so the two stay in sync.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "StickFigure"; \
    ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Quit a running copy before its files are removed.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "StopApp"

; Chat memory, settings and downloaded voice models live in %LOCALAPPDATA%\StickFigure and are KEPT on
; uninstall (reinstalling brings your buddy back). Ollama and its models are separate apps, also kept.
