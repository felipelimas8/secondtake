#!/usr/bin/env bash
# Gera "Second Take.app", o instalador .dmg e um .zip em dist/.
# Uso: scripts/build_macos.sh [--install]   (--install copia o app para /Applications)
#
# Todo o trabalho (PyInstaller, Info.plist, assinatura, .zip e .dmg) acontece numa pasta
# temporária no disco interno (APFS). Assim o build funciona com o projeto num SSD exFAT,
# numa pasta do iCloud ou em qualquer volume que acrescente atributos/arquivos "._*" e
# quebre o codesign. Só os artefatos finais são copiados para dist/.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

# canal (estável/beta): APP_NAME, BUNDLE_ID, FILE_PREFIX e ícone vêm de src/secondtake/channel.py
eval "$(PYTHONPATH="$ROOT/src" python3 -m secondtake.channel --sh)"
ICON_SRC=assets/icon_1024.png
[ "$CHANNEL" = beta ] && ICON_SRC=assets/icon_beta_1024.png
SIGN_ID="-"   # assinatura ad-hoc

# dependências travadas (versões + hashes): o CustomTkinter/PyInstaller não mudam sozinhos
python3 -m pip install -q --require-hashes -r requirements-build.txt
python3 -m pip install -q --no-deps -e .
VERSION=$(python3 -c "import secondtake; print(secondtake.__version__)")
ARCH=$(uname -m)
BASENAME="$FILE_PREFIX-$VERSION-macOS-$ARCH"

# ---------------------------------------------------------------- pasta de trabalho no disco interno
WORK=$(mktemp -d /tmp/secondtake-build.XXXXXX)
WORK=$(cd "$WORK" && pwd -P)   # /tmp -> /private/tmp
trap 'rm -rf "$WORK"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
echo "Pasta de trabalho: $WORK"

# ---------------------------------------------------------------- ícone
ICNS="$WORK/SecondTake.icns"
if [ -f "$ICON_SRC" ]; then
  SET="$WORK/SecondTake.iconset"
  mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_SRC" --out "$SET/icon_${s}x${s}.png" >/dev/null
    sips -z $((s*2)) $((s*2)) "$ICON_SRC" --out "$SET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$SET" -o "$ICNS"
fi
ICON=()
[ -f "$ICNS" ] && ICON=(--icon "$ICNS")

# ---------------------------------------------------------------- PyInstaller (work, dist e .spec no disco interno)
python3 -m PyInstaller --windowed --noconfirm --clean \
  --name "$APP_NAME" --paths "$ROOT/src" --collect-submodules secondtake \
  --collect-data customtkinter --collect-data secondtake --collect-all tkinterdnd2 \
  --hidden-import PIL._tkinter_finder \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --workpath "$WORK/pyi-build" --distpath "$WORK/dist" --specpath "$WORK" \
  ${ICON[@]+"${ICON[@]}"} "$ROOT/scripts/secondtake_app.py"

APP="$WORK/dist/$APP_NAME.app"
PLIST="$APP/Contents/Info.plist"

# ---------------------------------------------------------------- Info.plist
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

# ---------------------------------------------------------------- limpeza antes de assinar
# Arquivos "._*" (AppleDouble) vêm de dados lidos de exFAT/NTFS/SMB; atributos estendidos
# (FinderInfo, quarentena, iCloud) fazem o codesign recusar o bundle.
find "$APP" -name '._*' -type f -delete
find "$APP" -name '.DS_Store' -type f -delete
xattr -cr "$APP"

# ---------------------------------------------------------------- assinatura de dentro para fora (sem --deep)
sign() { codesign --force --sign "$SIGN_ID" --timestamp=none "$@"; }

# lista caminhos do mais profundo para o mais raso (compatível com o Bash 3.2 do macOS)
deepest_first() { awk -F/ '{ print NF "\t" $0 }' | sort -t "$(printf '\t')" -k1,1nr -k2 | cut -f2-; }

MAIN_EXE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$PLIST")"

# 1) todo arquivo Mach-O (.dylib, .so, executáveis sem extensão), exceto o executável principal
N_BIN=0
while IFS= read -r f; do
  [ "$f" = "$MAIN_EXE" ] && continue
  case "$(file -b "$f")" in
    *Mach-O*) sign "$f"; N_BIN=$((N_BIN+1)) ;;
  esac
done < <(find "$APP/Contents/Frameworks" "$APP/Contents/MacOS" "$APP/Contents/Resources" -type f 2>/dev/null | deepest_first)

# 2) bundles aninhados (.framework, .bundle, .app…), dos mais internos para os externos
N_BUNDLE=0
while IFS= read -r b; do
  sign "$b"; N_BUNDLE=$((N_BUNDLE+1))
done < <(find "$APP/Contents" -type d \( -name '*.framework' -o -name '*.bundle' -o -name '*.app' -o -name '*.appex' -o -name '*.xpc' \) 2>/dev/null | deepest_first)

# 3) por último, o próprio .app (assina o executável principal e sela os recursos)
sign "$APP"
echo "Assinados: $N_BIN binários, $N_BUNDLE bundles internos e o app"

# --deep aqui é só na verificação (continua suportado); --strict pega symlinks e bundles inválidos
codesign --verify --deep --strict "$APP"

# ---------------------------------------------------------------- .zip e .dmg (gerados no disco interno)
OUT="$WORK/out"
mkdir -p "$OUT"
(cd "$WORK/dist" && ditto -c -k --keepParent "$APP_NAME.app" "$OUT/$BASENAME.zip")

DMG="$OUT/$BASENAME.dmg"
STAGE="$WORK/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/$APP_NAME.app"
cp "assets/Leia-me · Read me.txt" "$STAGE/"
xattr -c "$STAGE/Leia-me · Read me.txt" 2>/dev/null || true
# atalho que abre Ajustes do Sistema > Privacidade e Segurança (gerado aqui, no formato que o Finder lê)
LINK="$STAGE/Abrir Ajustes de Segurança.webloc"
/usr/libexec/PlistBuddy -c "Add :URL string x-apple.systempreferences:com.apple.preference.security?Security" "$LINK" >/dev/null
if command -v create-dmg >/dev/null 2>&1; then
  BG="$ROOT/assets/dmg_background.png"
  if [ -f assets/dmg_background@2x.png ]; then
    tiffutil -cathidpicheck assets/dmg_background.png assets/dmg_background@2x.png -out "$WORK/dmg_background.tiff" >/dev/null
    BG="$WORK/dmg_background.tiff"
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

# ---------------------------------------------------------------- instalar (a partir da cópia no disco interno)
if [ "${1:-}" = "--install" ]; then
  rm -rf "/Applications/$APP_NAME.app"
  ditto "$APP" "/Applications/$APP_NAME.app"
  echo "Instalado em /Applications"
fi

# ---------------------------------------------------------------- copiar os artefatos finais para dist/
mkdir -p dist
cp "$OUT/$BASENAME.dmg" "$OUT/$BASENAME.zip" dist/
rm -rf "dist/$APP_NAME.app"
# exFAT/FAT não guardam symlinks nem atributos; se a cópia do .app não sair íntegra,
# ela é descartada (o .dmg e o .zip continuam válidos: foram gerados no disco interno).
if ditto "$APP" "dist/$APP_NAME.app" 2>/dev/null \
   && codesign --verify --deep --strict "dist/$APP_NAME.app" 2>/dev/null; then
  echo "OK: dist/$APP_NAME.app"
else
  rm -rf "dist/$APP_NAME.app" 2>/dev/null || true
  echo "Aviso: este volume não guarda um .app assinado (exFAT/NTFS/iCloud); dist/ fica só com o .dmg e o .zip." >&2
fi
echo "    dist/$BASENAME.dmg"
echo "    dist/$BASENAME.zip"
