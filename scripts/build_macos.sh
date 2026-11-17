#!/usr/bin/env bash
# Gera o app ("Second Take.app" ou "Second Take Beta.app", conforme src/secondtake/channel.py),
# o instalador .dmg e um .zip em dist/.
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
# canal (estável/beta): nome do app, bundle id, prefixo dos arquivos e ícone
eval "$(python3 -m secondtake.channel --sh)"
ICON_SRC=assets/icon_1024.png
[ "$CHANNEL" = beta ] && ICON_SRC=assets/icon_beta_1024.png

# ícone: $ICON_SRC -> build/SecondTake.icns
mkdir -p build
ICNS=build/SecondTake.icns
if [ -f "$ICON_SRC" ]; then
  SET=build/SecondTake.iconset
  rm -rf "$SET" && mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_SRC" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$ICON_SRC" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$ICNS"
fi
ICON=()
[ -f "$ICNS" ] && ICON=(--icon "$ICNS")

python3 -m PyInstaller --windowed --noconfirm --clean \
  --name "$APP_NAME" --paths src --collect-submodules secondtake \
  --collect-data customtkinter --collect-data secondtake --collect-all tkinterdnd2 \
  --osx-bundle-identifier "$BUNDLE_ID" \
  ${ICON[@]+"${ICON[@]}"} scripts/secondtake_app.py

APP="dist/$APP_NAME.app"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string 'Copyright © 2026 Super8 Produtora · GPL v3'" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSHumanReadableCopyright 'Copyright © 2026 Super8 Produtora · GPL v3'" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.video" "$PLIST" 2>/dev/null || true
xattr -cr "$APP"
codesign -s - --force --deep "$APP"

# .zip (para quem prefere)
(cd dist && ditto -c -k --keepParent "$APP_NAME.app" "$FILE_PREFIX-$VERSION-macOS-$ARCH.zip")

# instalador .dmg: janela com fundo, ícone do app, atalho para Aplicativos e "Como abrir.txt"
DMG="dist/$FILE_PREFIX-$VERSION-macOS-$ARCH.dmg"
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
      --volname "$APP_NAME $VERSION" ${VOLICON[@]+"${VOLICON[@]}"} \
      --background "$BG" \
      --window-pos 200 120 --window-size 680 620 \
      --icon-size 104 --text-size 13 \
      --icon "$APP_NAME.app" 170 200 --hide-extension "$APP_NAME.app" \
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
  hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
fi
echo "OK: $APP"
echo "    $DMG"
echo "    dist/$FILE_PREFIX-$VERSION-macOS-$ARCH.zip"

if [ "${1:-}" = "--install" ]; then
  rm -rf "/Applications/$APP_NAME.app"
  cp -R "$APP" /Applications/
  echo "Instalado em /Applications"
fi
