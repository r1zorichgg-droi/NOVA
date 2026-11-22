; NOVA — per-user Inno Setup installer.
; Keep AppId unchanged between all releases: Inno Setup uses it to detect the existing installation.
#include "version.iss"
#define MyAppName "NOVA"
#define MyAppPublisher "NOVA Project"
#define MyAppExeName "NOVA.exe"

[Setup]
AppId={{C9B39B3E-7E31-4B83-95B8-3E12C1F10555}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/r1zorichgg-droi/NOVA
AppSupportURL=https://github.com/r1zorichgg-droi/NOVA/issues
AppUpdatesURL=https://github.com/r1zorichgg-droi/NOVA/releases
DefaultDirName={localappdata}\Programs\NOVA
DefaultGroupName=NOVA
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UninstallDisplayName=NOVA
UninstallDisplayIcon={app}\NOVA.exe
SetupIconFile=..\assets\icons\nova.ico
OutputDir=..\release
OutputBaseFilename=NOVA_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0.17763
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
Uninstallable=yes

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Дополнительные задачи:"; Flags: unchecked
Name: "startup"; Description: "Запускать NOVA вместе с Windows (Dynamic Island будет появляться при входе)"; GroupDescription: "Дополнительные задачи:"; Flags: unchecked

[Files]
Source: "..\dist\NOVA.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NOVA"; Filename: "{app}\NOVA.exe"
Name: "{autodesktop}\NOVA"; Filename: "{app}\NOVA.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NOVA"; ValueData: """""{app}\NOVA.exe"""" --minimized"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\NOVA.exe"; Description: "Запустить NOVA"; Flags: postinstall nowait skipifsilent
