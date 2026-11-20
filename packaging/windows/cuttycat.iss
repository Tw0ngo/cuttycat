; Windows installer, built by packaging\build-windows.ps1 from dist\Cuttycat\.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; Never change AppId: Windows uses it to recognise updates of the installed program.
AppId={{F8388349-D93D-46F4-A75E-BB0FDE07ABB0}
AppName=Cuttycat
AppVersion={#AppVersion}
AppVerName=Cuttycat {#AppVersion}
AppPublisher=Twongo
AppPublisherURL=https://github.com/Tw0ngo/cuttycat
AppSupportURL=https://github.com/Tw0ngo/cuttycat/issues
DefaultDirName={commonpf64}\Cuttycat
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=cuttycat_install
SetupIconFile=..\cuttycat.ico
UninstallDisplayIcon={app}\Cuttycat.exe
; The uninstaller itself (unins000.exe + .dat) goes into a subfolder; {app}\Uninstall starts it.
UninstallFilesDir={app}\uninstall
UninstallDisplayName=Cuttycat
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Remove the files of an older version before installing the new one.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\..\dist\Cuttycat\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Cuttycat"; Filename: "{app}\Cuttycat.exe"
Name: "{autodesktop}\Cuttycat"; Filename: "{app}\Cuttycat.exe"; Tasks: desktopicon
Name: "{app}\Uninstall"; Filename: "{uninstallexe}"; Comment: "Remove Cuttycat from this computer"

[Run]
Filename: "{app}\Cuttycat.exe"; Description: "{cm:LaunchProgram,Cuttycat}"; Flags: nowait postinstall skipifsilent
