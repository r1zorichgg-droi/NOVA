; NOVA — per-user Inno Setup installer.
; Keep AppId unchanged between all releases: Inno Setup uses it to detect the existing installation.
#include "version.iss"
#define MyAppName "NOVA 1.06 TEST"
#define MyAppPublisher "NOVA Project"
#define MyAppExeName "NOVA.exe"

[Setup]
AppId={{6A6A2A06-8D35-4D88-9B6C-106000000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://github.com/r1zorichgg-droi/NOVA
AppSupportURL=https://github.com/r1zorichgg-droi/NOVA/issues
AppUpdatesURL=https://github.com/r1zorichgg-droi/NOVA/releases
DefaultDirName={localappdata}\Programs\NOVA
DefaultGroupName=NOVA 1.06 TEST
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
UninstallDisplayName=NOVA 1.06 TEST
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
Name: "startup"; Description: "Запускать NOVA вместе с Windows для автоматической проверки обязательных обновлений"; GroupDescription: "Дополнительные задачи:"

[Files]
Source: "..\dist\NOVA.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\NOVA"; Filename: "{app}\NOVA.exe"
Name: "{autodesktop}\NOVA"; Filename: "{app}\NOVA.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NOVA 1.06 TEST"; ValueData: """""{app}\NOVA.exe"""" --minimized"; Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\NOVA.exe"; Description: "Запустить NOVA"; Flags: postinstall nowait skipifsilent
