"""Interface de linha de comando do Second Take."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, core


def _collect(targets: list[str], progress):
    if len(targets) == 1 and os.path.isdir(targets[0]):
        return core.scan(targets[0], progress)
    files = [t for t in targets if os.path.isfile(t)]
    missing = [t for t in targets if not os.path.exists(t)]
    if missing:
        raise SystemExit(f'não encontrado: {", ".join(missing)}')
    return core.scan_files(files, progress)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='secondtake',
                                 description='Diagnóstico e recuperação de clipes Sony XAVC S corrompidos.')
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scan', help='analisa uma pasta ou arquivos e lista os problemas')
    s.add_argument('targets', nargs='+', help='uma pasta, ou um ou mais arquivos')
    s.add_argument('--csv', help='salva o diagnóstico em CSV')
    r = sub.add_parser('repair', help='analisa e repara (gera cópias; originais intactos)')
    r.add_argument('targets', nargs='+', help='uma pasta, ou um ou mais arquivos')
    r.add_argument('-o', '--output', required=True, help='pasta de saída')
    r.add_argument('--verify', action='store_true', help='confere os reparados com ffprobe')
    r.add_argument('--mode', choices=['auto', 'cabecalho', 'reconstruir', 'extrair'], default='auto',
                   help='força um modo de reparo (padrão: automático)')
    r.add_argument('--ref', help='clipe de referência manual')
    r.add_argument('--data-start', type=int, help='offset manual do início dos dados (bytes)')
    a = ap.parse_args(argv)

    def prog(i, n, c):
        print(f'[{i}/{n}] {c.status:22} {c.name}  {c.detail}', flush=True)

    clips = _collect(a.targets, prog)
    bad = [c for c in clips if c.status != core.OK]
    print(f'\n{len(clips)} arquivos · {len(clips) - len(bad)} OK · {len(bad)} com problema')

    if a.cmd == 'scan':
        if a.csv:
            core.write_report(clips, a.csv)
            print(f'relatório: {a.csv}')
        return 0 if not bad else 2

    failures = 0
    for c in clips:
        if c.status not in core.REPAIRABLE:
            continue
        try:
            if a.mode != 'auto' or a.ref or a.data_start is not None:
                ref = [a.ref] if a.ref else ([c.reference] if c.reference else [])
                if not ref:
                    raise ValueError('sem referência; use --ref')
                if a.mode in ('auto', 'reconstruir'):
                    if a.mode == 'reconstruir' and c.status == core.HEADER:
                        c.status = core.NO_MOOV
                    if c.status != core.HEADER:
                        core.plan_rebuild(c, ref, a.data_start)
                    else:
                        c.reference = ref[0]
                elif a.mode == 'extrair':
                    try:
                        core.plan_rebuild(c, ref, a.data_start)
                    except Exception:
                        c._plan = None
                    core.set_extract(c, ref)
                else:
                    c.reference, c.extra['mode'] = ref[0], 'cabecalho'
            if not c.reference:
                print(f'pulando {c.name}: sem referência compatível')
                failures += 1
                continue
            print(f'reparando {c.name} ({c.extra.get("mode", "?")}, ref. {os.path.basename(c.reference)})', flush=True)
            core.repair(c, a.output)
            if a.verify:
                print('   ', core.verify(c), flush=True)
            elif c.verify:
                print('   ', c.verify, flush=True)
        except Exception as e:
            failures += 1
            c.verify = f'ERRO: {e}'
            print(f'   ERRO: {e}', file=sys.stderr)
    os.makedirs(a.output, exist_ok=True)
    rep = os.path.join(a.output, 'relatorio_reparo.csv')
    core.write_report(clips, rep)
    print(f'relatório: {rep}')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
