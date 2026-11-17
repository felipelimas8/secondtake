; Instalador do Second Take para Windows (Inno Setup 6).
; Compilado por scripts\build_windows.ps1:  ISCC /DAppVersion=0.8.0 installer\secondtake.iss
; Instala só para o usuário atual (não pede senha de administrador).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; canal (estável/beta), passado pelo build a partir de src\secondtake\channel.py
#ifndef AppName
  #define AppName "Second Take"
#endif
#ifndef FilePrefix
  #define FilePrefix "SecondTake"
#endif
#ifndef AppId
  #define AppId "{{6E3C2B7A-5D1F-4C8E-9A2B-3F7E1D4C5A60}"
#endif
#ifndef IconFile
  #define IconFile "icon.ico"
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Super8 Produtora
AppPublisherURL=https://github.com/felipelimas8/secondtake
AppSupportURL=https://github.com/felipelimas8/secondtake/issues
AppUpdatesURL=https://github.com/felipelimas8/secondtake/releases/latest
AppCopyright=Copyright (C) 2026 Super8 Produtora
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename={#FilePrefix}-{#AppVersion}-Windows-x64-Setup
SetupIconFile=..\assets\{#IconFile}
UninstallDisplayIcon={app}\{#AppName}.exe
UninstallDisplayName={#AppName}
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
pt.LaunchApp=Abrir o {#AppName} agora
en.LaunchApp=Open {#AppName} now
pt.Readme=Leia-me
en.Readme=Read me

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchApp}"; Flags: nowait postinstall skipifsilent
