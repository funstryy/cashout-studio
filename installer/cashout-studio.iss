; Cashout Studio setup wizard. Built by build_installer.py, which stages the payload
; and passes PayloadDir/OutputDir/AppVersion in with /D - nothing here depends
; on where the repo happens to live.
;
; Installs per-user into {localappdata}\Programs\Cashout Studio rather than Program
; Files, and deliberately so: the app keeps its database, generated audio and
; imported voices next to its own executable, which a non-elevated process
; cannot write inside Program Files.

#define AppName "Cashout Studio"
#define AppPublisher "Cashout Studio"
#define AppURL "https://github.com/remiqora/remiqora"
#define AppExe "CashoutStudio.exe"

[Setup]
AppId={{8F4C2A31-6E7D-4B29-9A15-7C3E8D6F1B04}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=CashoutStudio-Setup-{#AppVersion}
; No SetupIconFile: the app has no .ico yet, and SetupIconFile only accepts
; one (pointing it at the .exe fails with "resource update error"). The
; uninstall entry can take the exe directly and pull the icon out of it.
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#LicenseFile}
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\engines\*"; DestDir: "{app}\engines"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  ModelsPage: TInputDirWizardPage;

function ExistingEngineGuess(): String;
var
  Candidate: String;
begin
  Result := '';
  // {%USERPROFILE} reads the environment variable; {userprofile} is not an
  // Inno constant and raises "Unknown constant" at startup.
  Candidate := ExpandConstant('{%USERPROFILE}\remiqora\external\audio.cpp');
  if DirExists(Candidate + '\models') then
    Result := Candidate;
end;

procedure InitializeWizard;
begin
  // The model weights are ~13 GB and are not in this installer. Anyone who
  // already has them (a previous install, or a source checkout) should be
  // able to point at them instead of downloading them a second time.
  ModelsPage := CreateInputDirPage(
    wpSelectDir,
    'Engine models',
    'Where should Cashout Studio look for its AI models?',
    'The models are about 13 GB and are downloaded separately, not bundled here.' + #13#10 + #13#10 +
    'If you already have an audio.cpp folder with a "models" subfolder, point Setup at it and this install will use those files.' + #13#10 + #13#10 +
    'Leave it as-is to install a fresh, empty engine folder - you can fetch the models afterwards.',
    False, '');
  ModelsPage.Add('');
  ModelsPage.Values[0] := ExistingEngineGuess();
end;

function EngineDir(): String;
begin
  Result := Trim(ModelsPage.Values[0]);
  if (Result = '') or (not DirExists(Result)) then
    Result := ExpandConstant('{app}\engines\audio.cpp');
end;

procedure WriteEnvFile();
var
  EnvPath: String;
  Lines: TArrayOfString;
begin
  EnvPath := ExpandConstant('{app}\.env');
  // Never overwrite on an upgrade: this file is where someone puts the paths
  // they tuned for their own machine.
  if FileExists(EnvPath) then
    exit;
  SetArrayLength(Lines, 3);
  Lines[0] := '# Written by the Cashout Studio installer. Safe to edit.';
  Lines[1] := 'YUE2_DIR=' + EngineDir();
  Lines[2] := 'FFMPEG_BIN_DIR=' + ExpandConstant('{app}\ffmpeg\bin');
  SaveStringsToFile(EnvPath, Lines, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteEnvFile();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  DataDir := ExpandConstant('{app}\data');
  if not DirExists(DataDir) then
    exit;
  // A silent uninstall has nobody to answer a prompt - /SUPPRESSMSGBOXES does
  // not cover MsgBox from [Code], so asking here hangs the uninstaller on an
  // invisible dialog forever. Unattended means keep the data.
  if UninstallSilent then
    exit;
  // Generated tracks, imported voices and the library database live here.
  // Deleting someone's work silently because they uninstalled the app that
  // made it is not a decision for the uninstaller to take on its own.
  if MsgBox('Also delete your Cashout Studio library?' + #13#10 + #13#10 +
            'This removes generated tracks, imported voices and the track database in:' + #13#10 +
            DataDir + #13#10 + #13#10 +
            'Choose No to keep it for a future install.',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(DataDir, True, True, True);
end;
