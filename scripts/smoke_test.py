"""Teste de fumaça do app empacotado: abre o executável do PyInstaller com SECONDTAKE_SMOKE=1,
espera ele montar a janela e fechar sozinho, e confere o relatório.

Uso:
  python scripts/smoke_test.py "dist/Second Take.app/Contents/MacOS/Second Take"   (macOS)
  python scripts/smoke_test.py "dist/Second Take/Second Take.exe"                  (Windows)

Sai com 0 se o app abriu sem erro. Funciona igual em macOS e Windows (o .exe "windowed" não
tem stdout, por isso o relatório vem por arquivo).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

TIMEOUT = 180   # 1ª abertura de um binário recém-assinado no runner pode demorar


def main(argv: list[str]) -> int:
    if len(argv) != 1 or not os.path.isfile(argv[0]):
        print(__doc__, file=sys.stderr)
        return 2
    exe = os.path.abspath(argv[0])
    with tempfile.TemporaryDirectory() as td:
        report = os.path.join(td, 'smoke.json')
        env = dict(os.environ, SECONDTAKE_SMOKE='1', SECONDTAKE_SMOKE_OUT=report)
        try:
            r = subprocess.run([exe], env=env, timeout=TIMEOUT, capture_output=True, text=True)
        except subprocess.TimeoutExpired:
            print(f'FALHOU: o app não fechou em {TIMEOUT} s', file=sys.stderr)
            return 1
        data = None
        if os.path.exists(report):
            with open(report, encoding='utf-8') as f:
                data = json.loads(f.read() or 'null')
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)
    if data is None:
        print(f'FALHOU: sem relatório (código {r.returncode}); veja o log do app', file=sys.stderr)
        return 1
    for err in data.get('errors') or []:
        print(err, file=sys.stderr)
    shown = {k: v for k, v in data.items() if k != 'errors'}
    print(json.dumps(shown, ensure_ascii=False, indent=2))
    if r.returncode != 0 or not data.get('ok'):
        print(f'FALHOU (código {r.returncode})', file=sys.stderr)
        return 1
    if not data.get('frozen'):
        print('Aviso: não é o app empacotado (frozen=false)', file=sys.stderr)
    print('OK: o app empacotado abriu e fechou sem erro')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
