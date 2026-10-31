; SMS Gateway - Windows installer (Inno Setup 6). Built by build_windows_installer.ps1, which passes /DAppVersion=...
; Saved as UTF-8 with BOM (French text).

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "SMS Gateway"
#define AppExeName "SMS Gateway.exe"

[Setup]
AppId={{6E4C2B1A-8F3D-4C7E-9B21-5D0A7F3E9C42}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Globalsoft
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=SMS_Gateway_Setup_{#AppVersion}
SetupIconFile=build_assets\sms_gateway.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
MinVersion=10.0

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[CustomMessages]
french.DriverStatus=Préparation de la clé USB (pilote du dongle)...
french.DriverFailed=L'application est installée, mais la préparation de la clé USB n'a pas abouti.%n%nRelancez l'installation. Si le problème continue, envoyez le fichier suivant à votre contact :%n%1
french.PlugDongleHint=Branchez maintenant la clé USB (dongle) : SMS Gateway la prépare toute seule en une vingtaine de secondes.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "build\pyinstaller_dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "install_dongle_driver.ps1"; DestDir: "{app}\driver_setup"; Flags: ignoreversion
Source: "driver\sms_gateway_winusb.inf"; DestDir: "{app}\driver_setup\driver"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\driver_setup\install_dongle_driver.ps1"" -Uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveDongleDriver"

[UninstallDelete]
Type: files; Name: "{app}\driver_setup\driver\*.cat"

[Code]
procedure InstallDongleDriver;
var
  ResultCode: Integer;
  Parameters: String;
begin
  WizardForm.StatusLabel.Caption := CustomMessage('DriverStatus');
  Parameters := '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\driver_setup\install_dongle_driver.ps1') +
    '" -DriverFolder "' + ExpandConstant('{app}\driver_setup\driver') + '"';
  if (not Exec('powershell.exe', Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)) or (ResultCode <> 0) then
    MsgBox(FmtMessage(CustomMessage('DriverFailed'), [ExpandConstant('{commonappdata}\SMS Gateway\driver_install.log')]),
      mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    InstallDongleDriver;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + CustomMessage('PlugDongleHint');
end;
