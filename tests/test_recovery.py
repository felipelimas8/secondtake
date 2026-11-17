"""Testes de recuperação com dano simulado em clipes sintéticos XAVC S.

Cada teste corrompe uma cópia de um clipe íntegro e confere, quadro a quadro
(framemd5) e amostra a amostra (PCM), que o que o Second Take recupera é
idêntico ao original — e que nada do que foi recuperado falha ao decodificar.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import subprocess

import pytest

from secondtake import cli, core, rebuild as rb

import synth

FF = shutil.which('ffmpeg')
pytestmark = pytest.mark.skipif(FF is None, reason='ffmpeg não encontrado')


# ---------------------------------------------------------------- utilitários

def frame_md5s(path: str) -> tuple[list[str], int]:
    """(hash de cada quadro decodificado, linhas de erro do decoder)."""
    r = subprocess.run([FF, '-v', 'error', '-i', path, '-map', '0:v:0', '-f', 'framemd5', '-'],
                       capture_output=True, text=True)
    hashes = [l.split(',')[-1].strip() for l in r.stdout.splitlines() if l and not l.startswith('#')]
    errors = len([l for l in r.stderr.splitlines() if l.strip()])
    return hashes, errors


def pcm(path: str, stream: str = '0:a:0') -> bytes:
    r = subprocess.run([FF, '-v', 'error', '-i', path, '-map', stream, '-f', 's16le', '-'],
                       capture_output=True)
    return r.stdout


def strip_moov(path: str, info: dict):
    with open(path, 'r+b') as f:
        f.truncate(info['moov_pos'])


def scramble(path: str, start: int, length: int, seed: int = 0):
    rnd = random.Random(seed)
    with open(path, 'r+b') as f:
        f.seek(start)
        f.write(bytes(rnd.getrandbits(8) for _ in range(length)))


@pytest.fixture(scope='session')
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp('src')
    ref = synth.make_xavc(str(d / 'C0001.MP4'), seconds=3, seed=1)
    good = synth.make_xavc(str(d / 'C0002.MP4'), seconds=6, seed=2)
    golden, err = frame_md5s(good['path'])
    assert err == 0 and len(golden) == good['n_frames']
    return {'ref': ref, 'good': good, 'golden': golden, 'pcm': pcm(good['path'])}


@pytest.fixture
def work(tmp_path, clips):
    """Pasta com a referência e uma cópia do clipe íntegro, pronta para ser danificada."""
    src = tmp_path / 'in'
    src.mkdir()
    shutil.copy(clips['ref']['path'], src / 'C0001.MP4')
    victim = src / 'C0002.MP4'
    shutil.copy(clips['good']['path'], victim)
    return {'dir': str(src), 'victim': str(victim), 'out': str(tmp_path / 'out'),
            'info': clips['good'], 'golden': clips['golden'], 'pcm': clips['pcm']}


def by_name(clips_, name):
    return next(c for c in clips_ if c.name == name)


def assert_subsequence(got: list[str], golden: list[str]):
    gs = set(golden)
    assert all(h in gs for h in got), 'quadro recuperado diferente do original'
    idx = [golden.index(h) for h in got]
    assert idx == sorted(idx), 'ordem dos quadros alterada'


# ---------------------------------------------------------------- diagnóstico

def test_intact_clip_is_ok(work):
    c = core.diagnose(work['victim'])
    assert c.status == core.OK
    assert c.audio_db is not None and c.audio_db > -40


def test_box_roundtrip(clips):
    pos, raw = core.find_moov(clips['ref']['path'])
    b = rb.Box.parse(raw)[0]
    assert b.serialize() == raw


# ---------------------------------------------------------------- nível 0/1: cabeçalho

def test_header_damage_repaired_bit_exact(work):
    scramble(work['victim'], 0, core.HDR, seed=1)          # só o cabeçalho
    clips_ = core.scan(work['dir'])
    c = by_name(clips_, 'C0002.MP4')
    assert c.status == core.HEADER
    assert os.path.basename(c.reference) == 'C0001.MP4'
    out = core.repair(c, work['out'])
    got, err = frame_md5s(out)
    assert err == 0
    assert got == work['golden']
    assert pcm(out) == work['pcm']


# ---------------------------------------------------------------- nível 1: reconstrução

def test_rebuild_after_interrupted_recording(work):
    info = work['info']
    strip_moov(work['victim'], info)
    cut = info['vid_offs'][5] + 1000                       # corta no meio do bloco 5
    with open(work['victim'], 'r+b') as f:
        f.truncate(cut)
    clips_ = core.scan(work['dir'])
    c = by_name(clips_, 'C0002.MP4')
    assert c.status == core.NO_MOOV
    assert c.extra['mode'] == 'reconstruir'
    out = core.repair(c, work['out'])
    got, err = frame_md5s(out)
    assert err == 0
    # blocos 0-4 inteiros; com B-frames, até 2 quadros finais dependem do bloco perdido
    assert got == work['golden'][:len(got)]
    assert len(got) >= 5 * info['G'] - 2
    assert pcm(out) == work['pcm'][:5 * info['a_chunk']]


def test_rsv_interrupted_recording(work):
    info = work['info']
    strip_moov(work['victim'], info)
    with open(work['victim'], 'r+b') as f:
        f.truncate(info['vid_offs'][5] + 1000)
    rsv = os.path.join(work['dir'], 'C0002.RSV')
    os.rename(work['victim'], rsv)
    c = by_name(core.scan(work['dir']), 'C0002.RSV')
    assert c.status == core.NO_MOOV and c.extra['mode'] == 'reconstruir'
    out = core.repair(c, work['out'])
    assert out.endswith('C0002_RECONSTRUIDO.MP4')
    got, err = frame_md5s(out)
    assert err == 0 and got == work['golden'][:len(got)]
    assert len(got) >= 5 * info['G'] - 2
    assert pcm(out) == work['pcm'][:5 * info['a_chunk']]


def test_rebuild_full_clip_without_moov(work):
    info = work['info']
    strip_moov(work['victim'], info)
    c = core.diagnose(work['victim'])
    p = core.plan_rebuild(c, [os.path.join(work['dir'], 'C0001.MP4')])
    assert len(p.chunks) == info['n_chunks']
    assert p.reason == 'fim da gravação'           # o preenchimento de zeros é reconhecido
    out = core.repair(c, work['out'])
    got, err = frame_md5s(out)
    assert err == 0 and got == work['golden']


# ---------------------------------------------------------------- nível 2: dano no meio / início

def test_resync_skips_damage_in_the_middle(work):
    info = work['info']
    strip_moov(work['victim'], info)
    scramble(work['victim'], info['vid_offs'][3] + 500, 4000, seed=2)   # bloco 3 (meio de GOP)
    c = core.diagnose(work['victim'])
    p = core.plan_rebuild(c, [os.path.join(work['dir'], 'C0001.MP4')])
    assert p.gaps, 'o trecho danificado deveria ser reportado'
    for ch in p.chunks:
        assert not (ch.meta_off <= info['vid_offs'][3] + 500 < ch.audio_off + info['a_chunk'])
    out = core.repair(c, work['out'])
    got, err = frame_md5s(out)
    assert err == 0, 'nenhum quadro recuperado pode ter artefato'
    assert_subsequence(got, work['golden'])
    # blocos 0-2 ficam; bloco 3 (danificado) sai; retoma no próximo IDR (bloco 4)
    G, gold = info['G'], work['golden']
    assert set(gold[:3 * G - 2]) <= set(got)
    assert set(gold[4 * G:]) <= set(got)
    assert not set(gold[3 * G + 2:4 * G]) & set(got)


def test_resync_after_damaged_start(work):
    info = work['info']
    strip_moov(work['victim'], info)
    scramble(work['victim'], 0, info['vid_offs'][0] + 3000, seed=3)    # cabeçalho + 1º bloco
    c = core.diagnose(work['victim'])
    p = core.plan_rebuild(c, [os.path.join(work['dir'], 'C0001.MP4')])
    out = core.repair(c, work['out'])
    got, err = frame_md5s(out)
    assert err == 0
    gop = info['gop']
    assert got == work['golden'][gop:]                     # recomeça no 2º GOP (IDR fechado)
    assert p.chunks[0].starts_idr


# ---------------------------------------------------------------- nível 3: controles manuais

def test_manual_reference_and_offset(work):
    info = work['info']
    strip_moov(work['victim'], info)
    c = core.diagnose(work['victim'])
    p = core.plan_rebuild(c, [os.path.join(work['dir'], 'C0001.MP4')], data_start=core.HDR)
    assert len(p.chunks) == info['n_chunks']
    # offset manual errado: o resync acha o próximo bloco íntegro que começa em IDR
    p = core.plan_rebuild(c, [os.path.join(work['dir'], 'C0001.MP4')], data_start=core.HDR + 7)
    assert len(p.chunks) >= info['n_chunks'] - info['gop'] // info['G']
    with pytest.raises(ValueError):
        core.plan_rebuild(c, [work['victim']])            # referência sem moov


def test_scan_files_finds_reference_in_same_folder(work):
    scramble(work['victim'], 0, core.HDR, seed=4)
    clips_ = core.scan_files([work['victim']])
    assert [c.name for c in clips_] == ['C0002.MP4']
    assert os.path.basename(clips_[0].reference) == 'C0001.MP4'


# ---------------------------------------------------------------- nível 4: extração bruta

def test_extract_with_known_layout(work):
    info = work['info']
    strip_moov(work['victim'], info)
    c = core.diagnose(work['victim'])
    core.plan_rebuild(c, [os.path.join(work['dir'], 'C0001.MP4')])
    core.set_extract(c, [os.path.join(work['dir'], 'C0001.MP4')])
    out = core.repair(c, work['out'])
    assert out.endswith('_EXTRAIDO.MP4')
    got, err = frame_md5s(out)
    assert err == 0 and got == work['golden']
    wav = out[:-4] + '.wav'
    assert pcm(wav) == work['pcm']


def test_extract_without_layout_recovers_video(work):
    info = work['info']
    strip_moov(work['victim'], info)
    prof = rb.load_profile(os.path.join(work['dir'], 'C0001.MP4'), core.find_moov)
    res = rb.extract_raw(work['victim'], work['out'], None, prof)
    # sem layout, o último quadro de cada bloco é descartado (pode ter engolido áudio)
    assert res['frames'] >= info['n_frames'] - info['n_chunks']
    got, _ = frame_md5s(res['mov'])
    assert_subsequence([h for h in got if h in set(work['golden'])], work['golden'])
    assert len(set(got) & set(work['golden'])) >= info['n_frames'] * 0.7


def test_garbage_file_reports_nothing_recoverable(work, tmp_path):
    junk = os.path.join(work['dir'], 'C0003.MP4')
    with open(junk, 'wb') as f:
        f.write(random.Random(9).randbytes(2_000_000))
    c = by_name(core.scan(work['dir']), 'C0003.MP4')
    assert c.status == core.NO_MOOV
    assert c.extra['mode'] == 'extrair'
    core.repair(c, work['out'])
    assert 'nenhum quadro' in c.verify


# ---------------------------------------------------------------- CLI

def test_cli_repair_folder(work, capsys):
    scramble(work['victim'], 0, core.HDR, seed=5)
    rc = cli.main(['repair', work['dir'], '-o', work['out']])
    assert rc == 0
    assert os.path.exists(os.path.join(work['out'], 'C0002_REPARADO.MP4'))
    assert os.path.exists(os.path.join(work['out'], 'relatorio_reparo.csv'))
    assert not os.path.exists(os.path.join(work['out'], 'C0001_REPARADO.MP4'))


def test_originals_are_never_modified(work):
    scramble(work['victim'], 0, core.HDR, seed=6)
    before = hashlib.md5(open(work['victim'], 'rb').read()).hexdigest()
    cli.main(['repair', work['dir'], '-o', work['out']])
    assert hashlib.md5(open(work['victim'], 'rb').read()).hexdigest() == before


# ---------------------------------------------------------------- nomes de saída e XML de câmera

XML = '<?xml version="1.0"?><NonRealTimeMeta><Device manufacturer="Sony" modelName="ILME-FX3"/></NonRealTimeMeta>'


def test_keep_names_and_camera_xml_header(work):
    scramble(work['victim'], 0, core.HDR, seed=1)
    with open(os.path.join(work['dir'], 'C0002M01.XML'), 'w') as f:
        f.write(XML)
    c = by_name(core.scan(work['dir']), 'C0002.MP4')
    out = core.repair(c, work['out'], keep_names=True)
    assert os.path.basename(out) == 'C0002.MP4'                      # mesmo nome: relink direto
    assert open(os.path.join(work['out'], 'C0002M01.XML')).read() == XML
    assert frame_md5s(out)[0] == work['golden']


def test_default_names_copy_xml_on_rebuild(work):
    strip_moov(work['victim'], work['info'])
    with open(os.path.join(work['dir'], 'C0002M01.XML'), 'w') as f:
        f.write(XML)
    c = by_name(core.scan(work['dir']), 'C0002.MP4')
    out = core.repair(c, work['out'])
    assert os.path.basename(out) == 'C0002_RECONSTRUIDO.MP4'
    assert os.path.isfile(os.path.join(work['out'], 'C0002_RECONSTRUIDOM01.XML'))


def test_keep_names_refuses_originals_folder(work):
    scramble(work['victim'], 0, core.HDR, seed=1)
    before = open(work['victim'], 'rb').read()
    c = by_name(core.scan(work['dir']), 'C0002.MP4')
    with pytest.raises(ValueError):
        core.repair(c, work['dir'], keep_names=True)
    assert open(work['victim'], 'rb').read() == before                 # original intacto


def test_cli_keep_names(work):
    scramble(work['victim'], 0, core.HDR, seed=1)
    cli.main(['repair', work['dir'], '-o', work['out'], '--keep-names'])
    assert os.path.isfile(os.path.join(work['out'], 'C0002.MP4'))
