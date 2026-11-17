# Gera o Second Take para Windows em dist\:
#   - SecondTake-<versão>-Windows-x64-Setup.exe   (instalador, Inno Setup)
#   - SecondTake-<versão>-Windows-x64-portatil.zip (sem instalar)
# Uso (PowerShell, na raiz do repositório):  .\scripts\build_windows.ps1
# Requisitos: Python 3.10+ e Inno Setup 6 (https://jrsoftware.org/isinfo.php) para o instalador.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -q -e ".[build]"
$Version = (python -c "import secondtake; print(secondtake.__version__)").Trim()
# canal (estável/beta): nome do app, prefixo dos arquivos, ícone e AppId do instalador
$Ch = @{}
python -m secondtake.channel | ForEach-Object { $k, $v = $_.Split('=', 2); $Ch[$k] = $v.Trim() }
$AppName = $Ch['APP_NAME']; $Prefix = $Ch['FILE_PREFIX']
$Ico = "assets\$($Ch['ICON_ICO'])"
Write-Host "$AppName $Version"

# versão do executável (aparece em Propriedades > Detalhes); '0.10.0b1' -> 0, 10, 0, 1
$nums = [regex]::Matches($Version, '\d+') | ForEach-Object { $_.Value }
$parts = (@($nums) + @('0', '0', '0', '0'))[0..3] -join ', '
New-Item -ItemType Directory -Force build | Out-Null
$vi = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts)),
  kids=[StringFileInfo([StringTable('041604B0', [
    StringStruct('CompanyName', 'Super8 Produtora'),
    StringStruct('FileDescription', '$AppName'),
    StringStruct('FileVersion', '$Version'),
    StringStruct('LegalCopyright', 'Copyright (C) 2026 Super8 Produtora - GPL v3'),
    StringStruct('ProductName', '$AppName'),
    StringStruct('ProductVersion', '$Version')])]),
  VarFileInfo([VarStruct('Translation', [1046, 1200])])]
)
"@
# UTF-8 sem BOM (o PyInstaller não aceita BOM; o Windows PowerShell 5 grava BOM com -Encoding UTF8)
[IO.File]::WriteAllText((Join-Path (Get-Location) 'build\version_info.txt'), $vi)

python -m PyInstaller --windowed --noconfirm --clean `
  --name "$AppName" --paths src --collect-submodules secondtake `
  --collect-data customtkinter --collect-data secondtake --collect-all tkinterdnd2 `
  --icon $Ico --version-file build\version_info.txt `
  scripts\secondtake_app.py

$App = "dist\$AppName"
Copy-Item "installer\LEIA-ME.txt", "installer\README.txt" $App

# portátil
$Zip = "dist\$Prefix-$Version-Windows-x64-portatil.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path $App -DestinationPath $Zip

# instalador
$Iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($Iscc) {
  & $Iscc /Qp "/DAppVersion=$Version" "/DAppName=$AppName" "/DFilePrefix=$Prefix" `
    "/DAppId=$($Ch['WIN_APP_ID'])" "/DIconFile=$($Ch['ICON_ICO'])" "installer\secondtake.iss"
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup falhou" }
} else {
  Write-Warning "Inno Setup 6 não encontrado: gerado só o .zip portátil. Instale com: winget install JRSoftware.InnoSetup"
}

Get-ChildItem "dist\$Prefix-*" | ForEach-Object { Write-Host "OK: $($_.FullName)" }
