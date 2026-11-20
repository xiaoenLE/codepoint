[Setup]
AppId=CodePoint
AppName=CodePoint
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\CodePoint
DefaultGroupName=CodePoint
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\CodePoint.exe
OutputDir=..\dist
OutputBaseFilename=CodePoint-{#AppVersion}-Setup-x64
Compression=lzma2/ultra64
SolidCompression=yes
RestartApplications=no

[Files]
Source: "..\dist\fast\CodePoint\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\CodePoint"; Filename: "{app}\CodePoint.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\CodePoint.exe"; Description: "Launch CodePoint"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command: String;
begin
  if CurUninstallStep = usPostUninstall then
    if RegQueryStringValue(HKCU, RunKey, 'CodePoint', Command) then
      if CompareText(Command, ExpandConstant('"{app}\CodePoint.exe" --minimized')) = 0 then
        RegDeleteValue(HKCU, RunKey, 'CodePoint');
end;
