#!/usr/bin/env bash
# Gera "Second Take.app", o instalador .dmg e um .zip em dist/.
# Uso: scripts/build_macos.sh [--install]   (--install copia o app para /Applications)
set -euo pipefail
cd "$(dirname "$0")/.."

case "$PWD" in
  "$HOME/Documents"*|"$HOME/Desktop"*|*"Mobile Documents"*)
    echo "Aviso: pastas sincronizadas com o iCloud quebram a assinatura do app." >&2
    echo "       Clone o repositório fora delas (ex.: ~/Dev/second-take)." >&2 ;;
esac

python3 -m pip install -q -e ".[build]"
VERSION=$(python3 -c "import secondtake; print(secondtake.__version__)")
ARCH=$(uname -m)

# ícone: assets/icon_1024.png -> build/SecondTake.icns
mkdir -p build
ICNS=build/SecondTake.icns
if [ -f assets/icon_1024.png ]; then
  SET=build/SecondTake.iconset
  rm -rf "$SET" && mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s assets/icon_1024.png --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) assets/icon_1024.png --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$ICNS"
fi
ICON=()
[ -f "$ICNS" ] && ICON=(--icon "$ICNS")

python3 -m PyInstaller --windowed --noconfirm --clean \
  --name "Second Take" --paths src --collect-submodules secondtake \
  --collect-data customtkinter --collect-data secondtake --collect-all tkinterdnd2 \
  --osx-bundle-identifier br.com.super8.secondtake \
  ${ICON[@]+"${ICON[@]}"} scripts/secondtake_app.py

APP="dist/Second Take.app"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string 'Copyright © 2026 Super8 Produtora · GPL v3'" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSHumanReadableCopyright 'Copyright © 2026 Super8 Produtora · GPL v3'" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.video" "$PLIST" 2>/dev/null || true
# avisos de privacidade do macOS (TCC): o texto que aparece quando o app lê um cartão/SSD externo,
# um volume de rede ou as pastas Mesa/Documentos/Transferências. Base em inglês + traduções.
PRIV_KEYS="NSRemovableVolumesUsageDescription NSNetworkVolumesUsageDescription NSDesktopFolderUsageDescription NSDocumentsFolderUsageDescription NSDownloadsFolderUsageDescription"
PRIV_EN="Second Take reads camera clips in this location to diagnose them and saves the recovered copies. Originals are never changed."
PRIV_PT="O Second Take lê os clipes de câmera neste local para diagnosticá-los e grava as cópias recuperadas. Os originais nunca são alterados."
RES="$APP/Contents/Resources"
mkdir -p "$RES/en.lproj" "$RES/pt-BR.lproj"
: > "$RES/en.lproj/InfoPlist.strings"
: > "$RES/pt-BR.lproj/InfoPlist.strings"
for KEY in $PRIV_KEYS; do
  /usr/libexec/PlistBuddy -c "Delete :$KEY" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Add :$KEY string '$PRIV_EN'" "$PLIST"
  printf '"%s" = "%s";\n' "$KEY" "$PRIV_EN" >> "$RES/en.lproj/InfoPlist.strings"
  printf '"%s" = "%s";\n' "$KEY" "$PRIV_PT" >> "$RES/pt-BR.lproj/InfoPlist.strings"
done
/usr/libexec/PlistBuddy -c "Delete :CFBundleLocalizations" "$PLIST" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleLocalizations array" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleLocalizations:0 string en" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleLocalizations:1 string pt-BR" "$PLIST"
plutil -lint "$PLIST" "$RES/en.lproj/InfoPlist.strings" "$RES/pt-BR.lproj/InfoPlist.strings" >/dev/null

xattr -cr "$APP"
codesign -s - --force --deep "$APP"

# .zip (para quem prefere)
(cd dist && ditto -c -k --keepParent "Second Take.app" "SecondTake-$VERSION-macOS-$ARCH.zip")

# instalador .dmg: janela com fundo, ícone do app, atalho para Aplicativos e "Como abrir.txt"
DMG="dist/SecondTake-$VERSION-macOS-$ARCH.dmg"
rm -f "$DMG"
STAGE=build/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
cp "assets/Leia-me · Read me.txt" "$STAGE/"
# atalho que abre Ajustes do Sistema > Privacidade e Segurança (gerado aqui, no formato que o Finder lê)
LINK="$STAGE/Abrir Ajustes de Segurança.webloc"
/usr/libexec/PlistBuddy -c "Add :URL string x-apple.systempreferences:com.apple.preference.security?Security" "$LINK" >/dev/null
if command -v create-dmg >/dev/null 2>&1; then
  BG=assets/dmg_background.png
  if [ -f assets/dmg_background@2x.png ]; then
    tiffutil -cathidpicheck assets/dmg_background.png assets/dmg_background@2x.png -out build/dmg_background.tiff >/dev/null
    BG=build/dmg_background.tiff
  fi
  VOLICON=()
  [ -f "$ICNS" ] && VOLICON=(--volicon "$ICNS")
  dmg() {
    create-dmg \
      --volname "Second Take $VERSION" ${VOLICON[@]+"${VOLICON[@]}"} \
      --background "$BG" \
      --window-pos 200 120 --window-size 680 620 \
      --icon-size 104 --text-size 13 \
      --icon "Second Take.app" 170 200 --hide-extension "Second Take.app" \
      --app-drop-link 510 200 \
      --icon "Leia-me · Read me.txt" 600 52 \
      --icon "Abrir Ajustes de Segurança.webloc" 340 520 --hide-extension "Abrir Ajustes de Segurança.webloc" \
      --no-internet-enable "$@" \
      "$DMG" "$STAGE" >/dev/null
  }
  # o layout da janela usa o Finder via AppleScript; em máquinas sem sessão gráfica, cai para --skip-jenkins
  dmg || { rm -f "$DMG"; dmg --skip-jenkins; }
else
  echo "create-dmg não encontrado (brew install create-dmg): gerando DMG simples, sem layout." >&2
  ln -s /Applications "$STAGE/Aplicativos"
  hdiutil create -volname "Second Take $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
fi
echo "OK: $APP"
echo "    $DMG"
echo "    dist/SecondTake-$VERSION-macOS-$ARCH.zip"

if [ "${1:-}" = "--install" ]; then
  rm -rf "/Applications/Second Take.app"
  cp -R "$APP" /Applications/
  echo "Instalado em /Applications"
fi
