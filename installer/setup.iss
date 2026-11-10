; =============================================================================
; Inno Setup Script: NL-OS-Agent (Task T35)
; Safety-Verified, AI-Native Operating System Shell Assistant
; =============================================================================

#define MyAppName "NL-OS-Agent"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "NL-OS-Agent Autonomous Research Team"
#define MyAppURL "https://github.com/Siva-0317/nl-os-agent"
#define MyAppExeName "nlagent-gui.exe"
#define MyBatchName "nlctl.cmd"

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
OutputDir=dist
OutputBaseFilename=NL-OS-Agent-Setup-v{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableProgramGroupPage=auto
ChangesEnvironment=yes
LicenseFile=LICENSE
InfoBeforeFile=docs\Claude_Context_Handover_and_Project_Status.md

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Launch NL-OS-Agent in System Tray on Windows startup"; GroupDescription: "Startup Options:"
Name: "addtopath"; Description: "Add NL-OS-Agent to user PATH environment variable"; GroupDescription: "System Integration:"

[Files]
; Core Python Source & Packages
Source: "src\*"; DestDir: "{app}\src"; Flags: recursesubdirs createallsubdirs
Source: "scripts\*"; DestDir: "{app}\scripts"; Flags: recursesubdirs createallsubdirs
Source: "pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: isreadme

; CLI Batch wrappers
Source: "nlctl.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "nlagent.cmd"; DestDir: "{app}"; Flags: ignoreversion

; Configuration and directories
Source: "models\*"; DestDir: "{app}\models"; Flags: recursesubdirs createallsubdirs external; Flags: skipifsourcedoesntexist
Source: "results\*"; DestDir: "{app}\results"; Flags: recursesubdirs createallsubdirs external; Flags: skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName} Tray Assistant"; Filename: "{app}\nlctl.cmd"; Parameters: "service start"; IconFilename: "{app}\src\nlagent\ui\tray.py"
Name: "{group}\First-Run Setup Wizard"; Filename: "pythonw.exe"; Parameters: "-m nlagent.ui.app --wizard"; WorkingDir: "{app}"
Name: "{group}\Usability Study Mode (Task N7)"; Filename: "pythonw.exe"; Parameters: "-m nlagent.ui.app --study"; WorkingDir: "{app}"
Name: "{group}\NL-OS-Agent Terminal Hook"; Filename: "{cmd}"; Parameters: "/k ""{app}\{#MyBatchName}"""; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyBatchName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "pythonw.exe"; Parameters: "-m nlagent.ui.app"; Tasks: autostart

[Registry]
; User PATH environment variable registration
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

; Autostart registration
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """pythonw.exe"" -m nlagent.ui.app"; Tasks: autostart

[Run]
Filename: "pythonw.exe"; Parameters: "-m nlagent.ui.app --wizard"; Description: "Launch First-Run Setup Wizard"; Flags: nowait postinstall skipifsilent

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

