; Primary module entrypoint: nlagent.ui.app
; =============================================================================
; Inno Setup Script: NL-OS-Agent (Sprint 4)
; Safety-Verified, AI-Native Operating System Shell Assistant
; =============================================================================

#define MyAppName "NL-OS-Agent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "NL-OS-Agent Autonomous Research Team"
#define MyAppURL "https://github.com/Siva-0317/nl-os-agent"
#define MyAppExeName "nlagent-gui.exe"
#define MyCliExeName "nlctl.exe"

[Setup]
AppId={{B8A1D392-4C78-4A59-8812-7A9B934F0C42}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=NL-OS-Agent-Setup-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=auto
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Launch NL-OS-Agent in System Tray on Windows startup"; GroupDescription: "Startup Options:"
Name: "addtopath"; Description: "Add NL-OS-Agent to user PATH environment variable"; GroupDescription: "System Integration:"

[Files]
; 1. Frozen PyInstaller onedir distribution
Source: "..\dist\nlagent\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; 2. Models Manifest & Documentation
Source: "..\models\manifest.json"; DestDir: "{app}\models"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\models\NOTICE"; DestDir: "{app}\models"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\README.md"; DestDir: "{app}"; Flags: isreadme skipifsourcedoesntexist

; 3. CLI Helper Scripts
Source: "..\nlctl.cmd"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\nlagent.cmd"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\First-Run Setup Wizard"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--wizard"; WorkingDir: "{app}"
Name: "{group}\Usability Study Mode"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--study"; WorkingDir: "{app}"
Name: "{group}\NL-OS-Agent Diagnostic Check"; Filename: "{cmd}"; Parameters: "/k ""{app}\{#MyCliExeName}"" doctor"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--tray-only"; Tasks: autostart

[Registry]
; Add {app} to user PATH environment variable
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

; Autostart registration
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"" --tray-only"; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--wizard"; Description: "Launch First-Run Setup Wizard"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\NLAgent\state"
Type: filesandordirs; Name: "{localappdata}\NLAgent\quarantine"
Type: filesandordirs; Name: "{app}\results\study_trials.jsonl"

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + UpperCase(Param) + ';', ';' + UpperCase(OrigPath) + ';') = 0;
end;
