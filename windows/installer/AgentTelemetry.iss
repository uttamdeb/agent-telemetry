#ifndef Payload
  #error Payload must name the verified staging directory
#endif
#ifndef Output
  #error Output must name the artifact directory
#endif
#ifndef AppVersion
  #error AppVersion must name the released version
#endif

[Setup]
AppId={{29DCF84F-2765-4E99-AFD3-721785895350}
AppName=AgentTelemetry
AppVersion={#AppVersion}
AppPublisher=Uttam Deb
AppPublisherURL=https://github.com/uttamdeb/agent-telemetry
AppSupportURL=https://github.com/uttamdeb/agent-telemetry/issues
DefaultDirName={localappdata}\Programs\AgentTelemetry
DefaultGroupName=AgentTelemetry
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=AgentTelemetry-{#AppVersion}-windows-x64-setup
SetupIconFile={#Payload}\windows\assets\AgentTelemetry.ico
UninstallDisplayIcon={app}\windows\assets\AgentTelemetry.ico
LicenseFile={#Payload}\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=yes
CloseApplications=no
RestartApplications=no
Uninstallable=yes
; Signing: build.py defines SignToolName (and passes the signtool command with /S) only when
; AGENT_TELEMETRY_SIGNTOOL is set, so an unsigned build compiles exactly as before.
#ifdef SignToolName
SignTool={#SignToolName}
SignedUninstaller=yes
#endif

[Files]
; An isolated, bundled-Python guard runs BEFORE any installed files are changed.
Source: "{#Payload}\runtime\*"; DestDir: "{tmp}\Guard\runtime"; Flags: dontcopy
Source: "{#Payload}\native.py"; DestDir: "{tmp}\Guard"; Flags: dontcopy
Source: "{#Payload}\windows\register_install.py"; DestDir: "{tmp}\Guard\windows"; Flags: dontcopy
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\AgentTelemetry"; Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows\tray.py"""; WorkingDir: "{app}"; IconFilename: "{app}\windows\assets\AgentTelemetry.ico"

[Run]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\windows\register_install.py"" launch"; Description: "Open AgentTelemetry"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var ExitCode: Integer;
begin
  ExtractTemporaryFiles('{tmp}\Guard\runtime\*');
  ExtractTemporaryFiles('{tmp}\Guard\native.py');
  ExtractTemporaryFiles('{tmp}\Guard\windows\register_install.py');
  if not Exec(ExpandConstant('{tmp}\Guard\runtime\python.exe'),
      '"' + ExpandConstant('{tmp}\Guard\windows\register_install.py') + '" check',
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
    Result := 'Quit AgentTelemetry and wait for its ledger save before installing. No application files have been replaced.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var ExitCode: Integer;
begin
  if CurStep = ssPostInstall then begin
    if not Exec(ExpandConstant('{app}\runtime\python.exe'),
        '"' + ExpandConstant('{app}\windows\register_install.py') + '" register',
        '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      RaiseException('AgentTelemetry could not register its app controls. Run the installer again.');
  end;
end;

function InitializeUninstall(): Boolean;
var ExitCode: Integer;
begin
  Result := Exec(ExpandConstant('{app}\runtime\python.exe'),
      '"' + ExpandConstant('{app}\windows\register_install.py') + '" check',
      '', SW_HIDE, ewWaitUntilTerminated, ExitCode) and (ExitCode = 0);
  if not Result then
    MsgBox('Quit AgentTelemetry and wait for its ledger save before uninstalling.', mbError, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var ExitCode: Integer;
begin
  if CurUninstallStep = usUninstall then begin
    if not Exec(ExpandConstant('{app}\runtime\python.exe'),
        '"' + ExpandConstant('{app}\windows\register_install.py') + '" unregister',
        '', SW_HIDE, ewWaitUntilTerminated, ExitCode) or (ExitCode <> 0) then
      RaiseException('AgentTelemetry could not unregister. No telemetry files will be removed.');
  end;
end;
