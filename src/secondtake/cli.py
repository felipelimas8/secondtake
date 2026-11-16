"""Interface de linha de comando do Second Take."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, core, i18n
from .i18n import msg, t


def _collect(targets: list[str], progress):
    if len(targets) == 1 and os.path.isdir(targets[0]):
        return core.scan(targets[0], progress)
    files = [t for t in targets if os.path.isfile(t)]
    missing = [t for t in targets if not os.path.exists(t)]
    if missing:
        raise SystemExit(t('cli.not_found', paths=', '.join(missing)))
    return core.scan_files(files, progress)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # idioma antes de montar a ajuda: --lang xx em qualquer posição
    lang = None
    for i, a_ in enumerate(argv):
        if a_ == '--lang' and i + 1 < len(argv):
            lang = argv[i + 1]
        elif a_.startswith('--lang='):
            lang = a_.split('=', 1)[1]
    i18n.init(lang)

    ap = argparse.ArgumentParser(prog='secondtake', description=t('cli.desc'))
    ap.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    ap.add_argument('--lang', choices=i18n.LANGS, help=t('cli.lang'))
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('scan', help=t('cli.scan'))
    s.add_argument('targets', nargs='+', help=t('cli.targets'))
    s.add_argument('--csv', help=t('cli.csv'))
    r = sub.add_parser('repair', help=t('cli.repair'))
    r.add_argument('targets', nargs='+', help=t('cli.targets'))
    r.add_argument('-o', '--output', required=True, help=t('cli.output'))
    r.add_argument('--verify', action='store_true', help=t('cli.verify'))
    r.add_argument('--mode', choices=['auto', 'cabecalho', 'reconstruir', 'extrair'], default='auto',
                   help=t('cli.mode'))
    r.add_argument('--ref', help=t('cli.ref'))
    r.add_argument('--data-start', type=int, help=t('cli.data_start'))
    for p_ in (s, r):
        p_.add_argument('--lang', choices=i18n.LANGS, help=t('cli.lang'))
    a = ap.parse_args(argv)

    def prog(i, n, c):
        print(f'[{i}/{n}] {t("status." + c.status):14} {c.name}  {c.detail}', flush=True)

    clips = _collect(a.targets, prog)
    bad = [c for c in clips if c.status != core.OK]
    print('\n' + t('cli.summary', n=len(clips), ok=len(clips) - len(bad), bad=len(bad)))

    if a.cmd == 'scan':
        if a.csv:
            core.write_report(clips, a.csv)
            print(t('cli.report', path=a.csv))
        return 0 if not bad else 2

    failures = 0
    for c in clips:
        if c.status not in core.REPAIRABLE:
            continue
        try:
            if a.mode != 'auto' or a.ref or a.data_start is not None:
                ref = [a.ref] if a.ref else ([c.reference] if c.reference else [])
                if not ref:
                    raise ValueError(t('err.no_ref_cli'))
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
                print(t('cli.skip', name=c.name))
                failures += 1
                continue
            mode = c.extra.get('mode')
            print(t('cli.repairing', name=c.name, mode=t(f'mode.{mode}') if mode else '?',
                    ref=os.path.basename(c.reference)), flush=True)
            core.repair(c, a.output)
            if a.verify:
                print('   ', core.verify(c), flush=True)
            elif c.verify:
                print('   ', c.verify, flush=True)
        except Exception as e:
            failures += 1
            c.verify = core.L('verify.error', err=msg(e))
            print('   ' + str(c.verify), file=sys.stderr)
    os.makedirs(a.output, exist_ok=True)
    rep = os.path.join(a.output, t('csv.report_name'))
    core.write_report(clips, rep)
    print(t('cli.report', path=rep))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
