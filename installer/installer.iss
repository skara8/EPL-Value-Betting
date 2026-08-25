#define MyAppName "EPL Value Betting"
#define MyAppPublisher "skara8"
#define MyAppExeName "EPLValueBetting.exe"
#define MyAppVersion GetEnv("APP_VERSION")

[Setup]
AppId={{8B050A4B-6F31-4A29-8B4E-7A4A60FA11AF}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\EPL Value Betting
DefaultGroupName=EPL Value Betting
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=EPL-Value-Betting-v{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Files]
Source: "..\dist\EPLValueBetting.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\EPL Value Betting"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\EPL Value Betting"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch EPL Value Betting"; Flags: nowait postinstall skipifsilent
