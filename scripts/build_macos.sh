#!/usr/bin/env bash
# Gera "Second Take.app" (e um .zip para distribuição) em dist/.
# Uso: scripts/build_macos.sh [--install]   (--install copia para /Applications)
set -euo pipefail
cd "$(dirname "$0")/.."

case "$PWD" in
  "$HOME/Documents"*|"$HOME/Desktop"*|*"Mobile Documents"*)
    echo "Aviso: pastas sincronizadas com o iCloud quebram a assinatura do app." >&2
    echo "       Clone o repositório fora delas (ex.: ~/Dev/second-take)." >&2 ;;
esac

python3 -m pip install -q -e ".[build]"
VERSION=$(python3 -c "import secondtake; print(secondtake.__version__)")
ICON=()
[ -f assets/SecondTake.icns ] && ICON=(--icon assets/SecondTake.icns)

python3 -m PyInstaller --windowed --noconfirm --clean \
  --name "Second Take" --paths src --collect-submodules secondtake \
  --collect-data customtkinter \
  --osx-bundle-identifier br.com.super8.secondtake \
  ${ICON[@]+"${ICON[@]}"} scripts/secondtake_app.py

APP="dist/Second Take.app"
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST" || true
/usr/libexec/PlistBuddy -c "Add :NSHumanReadableCopyright string 'Copyright © 2026 Super8 Produtora · GPL v3'" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSHumanReadableCopyright 'Copyright © 2026 Super8 Produtora · GPL v3'" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.video" "$PLIST" 2>/dev/null || true
xattr -cr "$APP"
codesign -s - --force --deep "$APP"
(cd dist && ditto -c -k --keepParent "Second Take.app" "SecondTake-$VERSION-macOS-$(uname -m).zip")
echo "OK: $APP  ·  dist/SecondTake-$VERSION-macOS-$(uname -m).zip"

if [ "${1:-}" = "--install" ]; then
  rm -rf "/Applications/Second Take.app"
  cp -R "$APP" /Applications/
  echo "Instalado em /Applications"
fi
