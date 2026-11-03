; Headwaters per-user installer (HDW-405).
;
; Build: scripts/build_windows.py --version X.Y.Z, which stages the payload and passes
;   /DAppVersion, /DSourceDir and /DOutputDir to ISCC.
;
; Rules this script must keep (tests/test_packaging_artifacts.py checks them):
;   * PrivilegesRequired=lowest and no HKLM: a standard user installs and uninstalls.
;   * {app} is %LOCALAPPDATA%\Headwaters, which is also the state dir, so the uninstaller
;     removes {app}\runtime and leaves state and keys unless the user says otherwise.
;   * No SignTool directive: code signing happens in the build script with the owner's
;     certificate from HEADWATERS_CERT_PATH. No certificate and no password lives here.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#ifndef SourceDir
  #define SourceDir "..\build\windows\payload"
#endif

#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{B5C2F0A4-3D7E-4A61-9C2B-7E5D4F1A2B10}
AppName=Headwaters
AppVersion={#AppVersion}
AppVerName=Headwaters {#AppVersion}
AppPublisher=Howitzer-10
AppPublisherURL=https://headwaters.local/
DefaultDirName={localappdata}\Headwaters
DefaultGroupName=Headwaters
DisableProgramGroupPage=yes
DisableDirPage=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=HeadwatersSetup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Headwaters
UninstallDisplayIcon={app}\runtime\uv.exe
MinVersion=10.0
AllowNoIcons=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start Headwaters when I log on (recommended)"; GroupDescription: "Startup:"; Flags: checkedonce
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
; uv.exe, uv.lock, pyproject.toml, headwaters\ and catalog\ - the venv is created at install.
Source: "{#SourceDir}\*"; DestDir: "{app}\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Open status page"; Filename: "{app}\runtime\.venv\Scripts\headwaters.exe"; Parameters: "status --open"; WorkingDir: "{app}"; Comment: "Start the daemon if needed and open the dashboard"
Name: "{group}\Doctor"; Filename: "{app}\runtime\.venv\Scripts\headwaters.exe"; Parameters: "doctor"; WorkingDir: "{app}"; Comment: "Check the install"
Name: "{group}\Uninstall Headwaters"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Headwaters"; Filename: "{app}\runtime\.venv\Scripts\headwaters.exe"; Parameters: "status --open"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; The private runtime: uv sync --frozen from the bundled uv and lockfile. No Python is shipped.
Filename: "{app}\runtime\uv.exe"; Parameters: "sync --frozen --no-dev --project ""{app}\runtime"" --python 3.11"; WorkingDir: "{app}\runtime"; StatusMsg: "Creating the private Python runtime (this takes a few minutes)..."; Flags: runhidden waituntilterminated
; The at-logon task, registered only when the user left the box ticked.
Filename: "{app}\runtime\.venv\Scripts\python.exe"; Parameters: "-m headwaters.packaging.autostart --enable"; WorkingDir: "{app}"; StatusMsg: "Registering the start-at-logon task..."; Flags: runhidden waituntilterminated; Tasks: autostart
Filename: "{app}\runtime\.venv\Scripts\headwaters.exe"; Parameters: "status --open"; Description: "Open the Headwaters status page"; Flags: postinstall nowait skipifsilent

[UninstallRun]
; The task goes first: a task left behind would restart a program that is gone.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""Headwaters"" /F"; Flags: runhidden; RunOnceId: "HeadwatersTask"

[UninstallDelete]
; runtime only. {app} is also the state dir, so it is never removed wholesale.
Type: filesandordirs; Name: "{app}\runtime"
Type: dirifempty; Name: "{app}"

[Code]
function AskRemoveUserData(): Boolean;
var
  Answer: Integer;
begin
  Answer := MsgBox('Also delete your Headwaters settings, history and saved API keys?' + #13#10 +
    'Choose No to keep them for a future reinstall.', mbConfirmation, MB_YESNO);
  Result := Answer = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Python, Params, Root: String;
  Code: Integer;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  Python := ExpandConstant('{app}\runtime\.venv\Scripts\python.exe');
  if not FileExists(Python) then
    Exit;  { an incomplete install: nothing can read the keys anyway }
  if not AskRemoveUserData() then
    Exit;
  Params := '-m headwaters.packaging.uninstall --yes --remove-state --remove-keys';
  Root := ExpandConstant('{app}');
  if (not Exec(Python, Params, Root, SW_HIDE, ewWaitUntilTerminated, Code)) or (Code <> 0) then
    MsgBox('Headwaters could not remove your settings. They are in ' + Root + ' and ' +
      ExpandConstant('{appdata}\Headwaters') + '.', mbInformation, MB_OK);
end;
