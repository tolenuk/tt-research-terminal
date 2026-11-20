; TT Research Terminal installer for testers (Inno Setup 6). Built by .github/workflows/windows-installer.yml:
;   iscc /DAppVersion=0.1.0 /DWheelName=obb_terminal-0.1.0-py3-none-any.whl /DStageDir=<dir> obb-terminal.iss
; StageDir holds uv.exe, requirements.txt (hash-pinned, from uv.lock) and the app wheel.
; Per user, no administrator rights. The app's Python lives inside the install folder.

#ifndef AppVersion
  #error Pass /DAppVersion=<version>
#endif
#ifndef WheelName
  #error Pass /DWheelName=<wheel file name>
#endif
#ifndef StageDir
  #error Pass /DStageDir=<folder with uv.exe, requirements.txt and the wheel>
#endif

#define AppName "TT Research Terminal"

[Setup]
AppId={{FBB58902-7105-4B3B-8CBB-A3D486E2ADA2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={localappdata}\Programs\obb-terminal
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
InfoBeforeFile=TESTERS.txt
OutputBaseFilename=TT-Research-Terminal-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
UninstallDisplayName={#AppName}
SetupLogging=yes
SetupIconFile=..\..\..\src\obb_terminal\ui\assets\tt-research.ico
UninstallDisplayIcon={app}\tt-research.ico

[Tasks]
Name: "claude"; Description: "Install Claude Code for the AI analyst (also installs Git for Windows if missing; you log in with your own Claude account)"; Check: not ClaudeInstalled
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#StageDir}\uv.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageDir}\{#WheelName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "setup.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "install-claude.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "TESTERS.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\..\src\obb_terminal\ui\assets\tt-research.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; wheels from earlier versions
Type: files; Name: "{app}\obb_terminal-*.whl"
; shortcuts from before the rename
Type: files; Name: "{userprograms}\OBB Terminal.lnk"
Type: files; Name: "{userdesktop}\OBB Terminal.lnk"

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m obb_terminal"; WorkingDir: "{%USERPROFILE}"; Comment: "Research terminal powered by OpenBB"; IconFilename: "{app}\tt-research.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m obb_terminal"; WorkingDir: "{%USERPROFILE}"; Comment: "Research terminal powered by OpenBB"; IconFilename: "{app}\tt-research.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\venv\Scripts\pythonw.exe"; Parameters: "-m obb_terminal"; WorkingDir: "{%USERPROFILE}"; Description: "Open {#AppName}"; Flags: postinstall nowait skipifsilent; Check: SetupSucceeded

[UninstallRun]
Filename: "{app}\venv\Scripts\obb-terminal-alerts.exe"; Parameters: "--uninstall"; Flags: runhidden skipifdoesntexist; RunOnceId: "RemoveAlertsTask"

[UninstallDelete]
; built by setup.cmd, not by the installer. The watchlist and journal in
; %LOCALAPPDATA%\obb-terminal are kept.
Type: filesandordirs; Name: "{app}\venv"
Type: filesandordirs; Name: "{app}\python"
Type: files; Name: "{app}\*.log"
Type: dirifempty; Name: "{app}"

[Code]
var
  Failed: Boolean;

function ClaudeInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{%USERPROFILE}\.local\bin\claude.exe')) or
    (FileSearch('claude.exe', GetEnv('PATH')) <> '');
end;

function SetupSucceeded: Boolean;
begin
  Result := not Failed;
end;

{ Runs a command hidden with its output in a log file; True when it exits with 0. }
function RunLogged(const Command, LogName: String; var Code: Integer): Boolean;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/c "' + Command + ' > "' + ExpandConstant('{app}\' + LogName) + '" 2>&1"',
    ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

procedure SetStatus(const Text: String);
begin
  WizardForm.StatusLabel.Caption := Text;
  WizardForm.FilenameLabel.Caption := '';
  WizardForm.ProgressGauge.Style := npbstMarquee;
end;

procedure BuildPython;
var
  Code: Integer;
begin
  SetStatus('Installing Python 3.12 and the app''s libraries. This takes a few minutes the first time...');
  if not RunLogged('"' + ExpandConstant('{app}\setup.cmd') + '" "{#WheelName}"', 'setup.log', Code) then
  begin
    Failed := True;
    SuppressibleMsgBox('TT Research Terminal could not finish installing (step ' + IntToStr(Code) + ').' + #13#10#13#10 +
      'Check your internet connection, close TT Research Terminal if it is open, and run this installer again.' + #13#10#13#10 +
      'Details are in ' + ExpandConstant('{app}\setup.log') + '. Please send that file if it keeps failing.',
      mbError, MB_OK, IDOK);
  end;
end;

procedure InstallClaude;
var
  Code: Integer;
begin
  SetStatus('Installing Claude Code...');
  if not RunLogged('powershell.exe -NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\install-claude.ps1') + '"', 'claude-install.log', Code) then
    SuppressibleMsgBox('Claude Code could not be installed, so the AI analyst will be unavailable. ' +
      'The rest of TT Research Terminal works.' + #13#10#13#10 +
      'To try yourself, run this in PowerShell:  irm https://claude.ai/install.ps1 | iex' + #13#10 +
      'Details are in ' + ExpandConstant('{app}\claude-install.log') + '.',
      mbInformation, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then
    exit;
  BuildPython;
  if (not Failed) and WizardIsTaskSelected('claude') then
    InstallClaude;
  WizardForm.ProgressGauge.Style := npbstNormal;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpFinished) and WizardIsTaskSelected('claude') and not Failed then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 +
      'Before using the AI analyst, open PowerShell and run  claude  once to log in with your Claude account.';
end;
