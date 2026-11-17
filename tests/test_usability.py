"""Ajustes de usabilidade e da auditoria para a 1.0 (0.9.11):
B3 (relatório CSV que não grava não some em silêncio), mesma unidade de tamanho na tabela
e no Detalhes, diagnóstico completo no Detalhes e extração bruta selecionável à mão.

Usa clipes sintéticos (tests/synth.py); nada de material real.
"""
from __future__ import annotations

import logging
import os
import shutil

import pytest

from secondtake import core

import synth

FF = shutil.which('ffmpeg')
needs_ff = pytest.mark.skipif(FF is None, reason='ffmpeg não encontrado')


def by(clips, name):
    return next(c for c in clips if c.name == name)


def strip_moov(path, info):
    with open(path, 'r+b') as f:
        f.truncate(info['moov_pos'])


@pytest.fixture(scope='module')
def src(tmp_path_factory):
    d = tmp_path_factory.mktemp('usab')
    return {'ref': synth.make_xavc(str(d / 'C0001.MP4'), seconds=3, seed=1),
            'victim': synth.make_xavc(str(d / 'C0002.MP4'), seconds=4, seed=2)}


@pytest.fixture
def folder(tmp_path, src):
    d = tmp_path / 'in'
    d.mkdir()
    for k in ('ref', 'victim'):
        shutil.copy(src[k]['path'], d / os.path.basename(src[k]['path']))
    return {'dir': str(d), 'out': str(tmp_path / 'out'), 'info': src['victim'],
            'victim': str(d / 'C0002.MP4'), 'ref': str(d / 'C0001.MP4')}


# ---------------------------------------------------------------- tamanho

@pytest.mark.parametrize('n, txt', [
    (0, '0 B'), (999, '999 B'), (1000, '1.0 KB'), (806_028_083, '806.0 MB'),
    (999_949_999, '999.9 MB'), (1_500_000_000, '1.50 GB'), (2_810_000_000_000, '2.81 TB'),
])
def test_fmt_size_is_decimal(n, txt):
    assert core.fmt_size(n) == txt


def test_table_and_details_use_the_same_size():
    """O mesmo clipe não pode mostrar 768.7 MB na tabela e 806.0 MB no Detalhes."""
    import pathlib
    import re
    gui_src = (pathlib.Path(core.__file__).parent / 'gui.py').read_text(encoding='utf-8')
    assert re.search(r'^human = core\.fmt_size\b', gui_src, re.M)          # tabela e avisos
    assert 'def human(' not in gui_src
    c = core.Clip('/x/C0001.MP4', 806_028_083)
    c.extra['meta'] = {'file_size': 806_028_083}
    assert dict(core.meta_rows(c))['Tamanho'] == core.fmt_size(c.size) == '806.0 MB'


# ---------------------------------------------------------------- diagnóstico completo no Detalhes

def test_details_show_full_diagnosis():
    c = core.Clip('/x/C0001.MP4', 10, core.NO_MOOV)
    c.detail = core.L('diag.rebuild', secs='42.5', chunks=85, extra='', reason=core.L('reason.end'))
    c.note(core.L('diag.silent'))
    rows = dict(core.meta_rows(c))
    assert rows['Diagnóstico'] == core.diag_text(c) == str(c.detail)
    assert len(rows['Diagnóstico']) > 60                                    # inteiro, sem corte
    c.verify = 'OK: 1000/1000 quadros'
    assert core.diag_text(c).startswith('OK: 1000/1000 quadros · ')
    assert list(dict(core.meta_rows(c)))[0] == 'Diagnóstico'                # primeira linha


def test_intact_clip_has_no_diagnosis_row():
    c = core.Clip('/x/C0001.MP4', 10, core.OK)
    assert 'Diagnóstico' not in dict(core.meta_rows(c))


# ---------------------------------------------------------------- extração bruta selecionável

def test_header_without_reference_is_not_repairable():
    c = core.Clip('/x/C0001.MP4', 10, core.HEADER)
    assert not core.repairable(c) and not core.auto_selected(c)


@needs_ff
def test_last_resort_without_reference_can_be_selected_and_repaired(folder):
    os.remove(folder['ref'])                          # só o clipe danificado, sem referência
    strip_moov(folder['victim'], folder['info'])
    c = by(core.scan(folder['dir']), 'C0002.MP4')
    assert c.status == core.NO_MOOV
    assert c.extra['mode'] == 'extrair' and c.reference == ''
    assert core.repairable(c)                         # o botão Reparar habilita com a linha selecionada
    assert not core.auto_selected(c)                  # mas não entra sozinho no lote
    out = core.repair(c, folder['out'])
    assert out and os.path.basename(out).startswith('C0002_EXTRAIDO')
    assert os.path.getsize(out) > 0


@needs_ff
def test_auto_selection_still_skips_extraction_with_reference(folder):
    strip_moov(folder['victim'], folder['info'])
    c = by(core.scan(folder['dir']), 'C0002.MP4')
    assert c.extra['mode'] == 'reconstruir'
    assert core.repairable(c) and core.auto_selected(c)
    core.set_extract(c, [folder['ref']])
    assert core.repairable(c) and not core.auto_selected(c)


# ---------------------------------------------------------------- B3: relatório CSV

@needs_ff
def test_batch_report_written(folder):
    scramble_header(folder['victim'])
    clips = core.scan(folder['dir'])
    c = by(clips, 'C0002.MP4')
    core.repair(c, folder['out'])
    shown, failures = core.write_batch_reports(clips, [c], folder['out'], 'rel.csv')
    assert failures == [] and shown == folder['out']
    assert os.path.exists(os.path.join(folder['out'], 'rel.csv'))


@needs_ff
def test_batch_report_failure_is_logged_and_keeps_recovered_files(folder, monkeypatch, caplog):
    scramble_header(folder['victim'])
    clips = core.scan(folder['dir'])
    c = by(clips, 'C0002.MP4')
    out = core.repair(c, folder['out'])

    def boom(*a, **k):
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr(core, 'write_report', boom)
    with caplog.at_level(logging.ERROR, logger='secondtake'):
        shown, failures = core.write_batch_reports(clips, [c], folder['out'], 'rel.csv')
    assert len(failures) == 1
    path, err = failures[0]
    assert path == os.path.join(folder['out'], 'rel.csv') and isinstance(err, OSError)
    assert any('rel.csv' in r.getMessage() and r.exc_info for r in caplog.records)   # com traceback
    assert os.path.exists(out)                                                     # recuperado intacto
    assert core.short_err(err) == 'No space left on device'                         # status curto, sem caminho


@needs_ff
def test_beside_reports_one_failure_does_not_stop_the_others(tmp_path, src, monkeypatch):
    dirs = []
    for n in ('A', 'B'):
        d = tmp_path / n
        d.mkdir()
        shutil.copy(src['victim']['path'], d / 'C0002.MP4')
        dirs.append(str(d))
    clips = [core.diagnose(os.path.join(d, 'C0002.MP4')) for d in dirs]
    real = core.write_report

    def flaky(group, path):
        if os.sep + 'A' + os.sep in path:
            raise PermissionError(13, 'Permission denied')
        real(group, path)
    monkeypatch.setattr(core, 'write_report', flaky)
    shown, failures = core.write_batch_reports(clips, clips, None, 'rel.csv')
    assert shown is None                                       # várias pastas
    assert [os.path.basename(os.path.dirname(os.path.dirname(p))) for p, _ in failures] == ['A']
    assert os.path.exists(os.path.join(dirs[1], core.SAME_FOLDER_DIR, 'rel.csv'))


def scramble_header(path):
    with open(path, 'r+b') as f:
        f.write(os.urandom(core.HDR))
