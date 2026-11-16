; Instalador do Second Take para Windows (Inno Setup 6).
; Compilado por scripts\build_windows.ps1:  ISCC /DAppVersion=0.8.0 installer\secondtake.iss
; Instala só para o usuário atual (não pede senha de administrador).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E3C2B7A-5D1F-4C8E-9A2B-3F7E1D4C5A60}
AppName=Second Take
AppVersion={#AppVersion}
AppVerName=Second Take {#AppVersion}
AppPublisher=Super8 Produtora
AppPublisherURL=https://github.com/felipelimas8/secondtake
AppSupportURL=https://github.com/felipelimas8/secondtake/issues
AppUpdatesURL=https://github.com/felipelimas8/secondtake/releases/latest
AppCopyright=Copyright (C) 2026 Super8 Produtora
DefaultDirName={autopf}\Second Take
DefaultGroupName=Second Take
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=SecondTake-{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\Second Take.exe
UninstallDisplayName=Second Take
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
ShowLanguageDialog=auto

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
pt.LaunchApp=Abrir o Second Take agora
en.LaunchApp=Open Second Take now
pt.Readme=Leia-me
en.Readme=Read me

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Second Take\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Second Take"; Filename: "{app}\Second Take.exe"
Name: "{autodesktop}\Second Take"; Filename: "{app}\Second Take.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Second Take.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent
