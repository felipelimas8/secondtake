#!/usr/bin/env bash
# Publica uma versão do Second Take com as verificações que evitam os erros já vividos
# (beta saindo na main, tag repetida, versão/CHANGELOG esquecidos, testes quebrados).
#
#   scripts/release.sh          confere tudo, mostra o resumo e pede confirmação
#   scripts/release.sh --check  só confere (não publica nada)
#
# main -> versão estável X.Y.Z, tag vX.Y.Z
# beta -> versão X.Y.ZbN,       tag vX.Y.Z-beta.N (pré-release)
set -euo pipefail
cd "$(dirname "$0")/.."

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m✗ %s\033[0m\n' "$1"; exit 1; }

echo "Second Take · conferência de release"

BRANCH=$(git branch --show-current)
case "$BRANCH" in
  main|beta) ok "branch: $BRANCH" ;;
  *) fail "publique só a partir de main ou beta (branch atual: ${BRANCH:-nenhum})" ;;
esac

[ -z "$(git status --porcelain)" ] || { git status --short; fail "há mudanças sem commit (acima): faça o commit antes"; }
ok "nada pendente para commit"

VERSION=$(python3 -c "import re;print(re.search(r\"__version__\s*=\s*'([^']+)'\", open('src/secondtake/__init__.py').read()).group(1))")
CHANNEL=stable
if [ -f src/secondtake/channel.py ]; then
  CHANNEL=$(PYTHONPATH=src SECONDTAKE_CHANNEL= python3 -c "from secondtake import channel; print(channel.CHANNEL)")
fi

if [ "$BRANCH" = main ]; then
  [ "$CHANNEL" = stable ] || fail "a main está com channel.py em modo '$CHANNEL' (sairia o app Beta como versão oficial)"
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "versão '$VERSION' não é estável (X.Y.Z)"
  TAG="v$VERSION"
else
  [ "$CHANNEL" = beta ] || fail "o branch beta está com channel.py em modo '$CHANNEL'"
  [[ "$VERSION" =~ ^([0-9]+\.[0-9]+\.[0-9]+)b([0-9]+)$ ]] || fail "versão '$VERSION' não é beta (X.Y.ZbN)"
  TAG="v${BASH_REMATCH[1]}-beta.${BASH_REMATCH[2]}"
fi
ok "canal $CHANNEL · versão $VERSION · tag $TAG"

# só o branch: as tags móveis main-build/beta-build são recriadas a cada push e
# fariam o `git fetch --tags` falhar ("would clobber existing tag")
git fetch -q origin "$BRANCH" || fail "não consegui falar com o GitHub (git fetch origin $BRANCH)"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
  fail "a tag $TAG já existe: suba a versão em src/secondtake/__init__.py (nunca reutilize uma tag)"
fi
ok "tag $TAG ainda não existe"

grep -q "^## $VERSION" CHANGELOG.md || fail "CHANGELOG.md não tem a seção '## $VERSION'"
ok "CHANGELOG tem a $VERSION"

AHEAD=$(git rev-list --count "origin/$BRANCH..HEAD" 2>/dev/null || echo 0)
BEHIND=$(git rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo 0)
[ "$BEHIND" = 0 ] || fail "o $BRANCH local está $BEHIND commit(s) atrás do GitHub: rode git pull"
ok "em dia com o GitHub ($AHEAD commit(s) para enviar)"

echo "  … rodando os testes"
python3 -m pytest -q >/tmp/secondtake-pytest.log 2>&1 || { tail -20 /tmp/secondtake-pytest.log; fail "testes falharam"; }
ok "testes: $(tail -1 /tmp/secondtake-pytest.log)"

if [ "$CHECK_ONLY" = 1 ]; then
  echo "Tudo certo para publicar $TAG (modo --check: nada foi publicado)."
  exit 0
fi

echo
echo "Testou o app empacotado? (scripts/build_macos.sh --install e abrir o app)"
printf 'Publicar %s a partir do %s? Digite sim: ' "$TAG" "$BRANCH"
read -r ANSWER
[ "$ANSWER" = sim ] || { echo "Cancelado."; exit 1; }

git push origin "$BRANCH"
git tag "$TAG"
git push origin "$TAG"
echo
echo "Publicado. Acompanhe em https://github.com/felipelimas8/secondtake/actions"
