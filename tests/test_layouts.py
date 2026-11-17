"""Layout do cabeçalho por câmera (0.9.11).

Medido em clipes reais: a FX3 começa os dados do mdat em 262144 (mdat em 262128) e a FX30 em
131072 (mdat em 131056), com uma box `meta` de nível superior depois do moov. O app não pode
supor posição fixa: uma FX30 íntegra aparecia como "cabeçalho corrompido", e com uma referência
FX3 na pasta a troca de cabeçalho apagaria os primeiros 128 KiB de dados da cópia.

Clipes sintéticos (tests/synth.py); nada de material real.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess

import pytest

from secondtake import cli, core

import synth

FF = shutil.which('ffmpeg')
pytestmark = pytest.mark.skipif(FF is None, reason='ffmpeg não encontrado')
FX30 = dict(data_start=131072, tail_meta=445029)


def frame_md5s(path):
    r = subprocess.run([FF, '-v', 'error', '-i', path, '-map', '0:v:0', '-f', 'framemd5', '-'],
                       capture_output=True, text=True)
    return [x.split(',')[-1].strip() for x in r.stdout.splitlines() if x and not x.startswith('#')]


def pcm(path):
    return subprocess.run([FF, '-v', 'error', '-i', path, '-map', '0:a:0', '-f', 's16le', '-'],
                          capture_output=True).stdout


def scramble(path, n):
    with open(path, 'r+b') as f:
        f.write(os.urandom(n))


def by(clips, name):
    return next(c for c in clips if c.name == name)


@pytest.fixture(scope='module')
def src(tmp_path_factory):
    d = tmp_path_factory.mktemp('layouts')
    out = {
        'fx3_ref': synth.make_xavc(str(d / 'A0001.MP4'), seconds=3, seed=1),
        'fx3_victim': synth.make_xavc(str(d / 'A0002.MP4'), seconds=4, seed=2),
        'fx30_ref': synth.make_xavc(str(d / 'B0001.MP4'), seconds=3, seed=3, **FX30),
        'fx30_victim': synth.make_xavc(str(d / 'B0002.MP4'), seconds=4, seed=4, **FX30),
    }
    for k in ('fx3_victim', 'fx30_victim'):
        out[k + '_golden'] = (frame_md5s(out[k]['path']), pcm(out[k]['path']))
    return out


@pytest.fixture
def folder(tmp_path, src):
    d = tmp_path / 'in'
    d.mkdir()

    def put(*keys):
        for k in keys:
            shutil.copy(src[k]['path'], d / os.path.basename(src[k]['path']))
        return str(d)
    return {'put': put, 'dir': str(d), 'out': str(tmp_path / 'out'), 'src': src}


# ---------------------------------------------------------------- estrutura

def test_parse_layout_fx3_and_fx30(src):
    a = core.parse_layout(src['fx3_ref']['path'])
    b = core.parse_layout(src['fx30_ref']['path'])
    assert (a['mdat_pos'], a['data_start'], a['mdat_hdr']) == (262128, 262144, 16)
    assert (b['mdat_pos'], b['data_start'], b['mdat_hdr']) == (131056, 131072, 16)
    assert b['mdat_end'] == src['fx30_ref']['moov_pos']          # o mdat da Sony vai até o moov


def test_parse_layout_rejects_broken_headers(tmp_path, src):
    p = tmp_path / 'x.mp4'
    good = open(src['fx30_ref']['path'], 'rb').read()
    p.write_bytes(os.urandom(64) + good[64:])                      # ftyp destruído
    assert core.parse_layout(str(p)) is None
    bad = bytearray(good)
    bad[176:180] = struct.pack('>I', 1000)                          # tamanho do free errado
    p.write_bytes(bytes(bad))
    assert core.parse_layout(str(p)) is None
    p.write_bytes(b'')
    assert core.parse_layout(str(p)) is None


def test_parse_layout_32bit_mdat(tmp_path):
    ftyp = struct.pack('>I4s4sI', 16, b'ftyp', b'isom', 0)
    data = b'\x00' * 100
    p = tmp_path / 'small.mp4'
    p.write_bytes(ftyp + struct.pack('>I4s', 8 + len(data), b'mdat') + data)
    assert core.parse_layout(str(p)) == {'mdat_pos': 16, 'mdat_hdr': 8, 'data_start': 24, 'mdat_end': 124}


# ---------------------------------------------------------------- diagnóstico

def test_intact_fx30_is_ok(src):
    """O caso real do 20261113_RNP_PT_6450: FX30 íntegra não pode ser 'cabeçalho corrompido'."""
    c = core.diagnose(src['fx30_ref']['path'])
    assert c.status == core.OK
    assert c.extra['data_start'] == 131072
    assert core._is_reference(c)


def test_fx30_clips_are_references_for_each_other(folder):
    d = folder['put']('fx30_ref', 'fx30_victim')
    victim = os.path.join(d, 'B0002.MP4')
    scramble(victim, 131072)
    clips = core.scan(d)
    assert by(clips, 'B0001.MP4').status == core.OK
    c = by(clips, 'B0002.MP4')
    assert c.status == core.HEADER and c.extra['data_start'] == 131072
    assert os.path.basename(c.reference) == 'B0001.MP4'


# ---------------------------------------------------------------- troca de cabeçalho

@pytest.mark.parametrize('cam, ref, n', [('fx30', 'B0001.MP4', 131072), ('fx3', 'A0001.MP4', 262144)])
def test_header_repair_bit_exact_with_mixed_cameras_in_folder(folder, cam, ref, n):
    d = folder['put']('fx3_ref', 'fx30_ref', f'{cam}_victim')
    name = os.path.basename(folder['src'][f'{cam}_victim']['path'])
    scramble(os.path.join(d, name), n)
    c = by(core.scan(d), name)
    assert c.status == core.HEADER
    assert os.path.basename(c.reference) == ref                    # mesma câmera/layout, nunca a outra
    out = core.repair(c, folder['out'])
    golden, audio = folder['src'][f'{cam}_victim_golden']
    assert frame_md5s(out) == golden and pcm(out) == audio
    assert core.diagnose(out).status == core.OK
    with open(out, 'rb') as a, open(folder['src'][f'{cam}_victim']['path'], 'rb') as b:
        a.seek(n), b.seek(n)
        assert a.read() == b.read()                                # dados (e o meta do fim) intactos


@pytest.mark.parametrize('victim, other, n', [('fx30_victim', 'fx3_ref', 131072), ('fx3_victim', 'fx30_ref', 262144)])
def test_other_layout_is_never_used_as_reference(folder, victim, other, n):
    d = folder['put'](other, victim)
    name = os.path.basename(folder['src'][victim]['path'])
    path = os.path.join(d, name)
    scramble(path, n)
    before = open(path, 'rb').read()
    c = by(core.scan(d), name)
    assert c.status == core.HEADER and c.reference == ''
    assert 'mesmo layout' in str(c.detail)
    assert not core.repairable(c)
    # forçado pela linha de comando também não passa: recusa em vez de gerar cópia estragada
    ref = os.path.join(d, os.path.basename(folder['src'][other]['path']))
    assert cli.main(['repair', path, '-o', folder['out'], '--mode', 'cabecalho', '--ref', ref]) != 0
    assert not os.path.exists(folder['out']) or not [f for f in os.listdir(folder['out']) if f.endswith('.MP4')]
    assert open(path, 'rb').read() == before


# ---------------------------------------------------------------- reconstrução

def test_rebuild_fx30_without_moov(folder):
    d = folder['put']('fx3_ref', 'fx30_ref', 'fx30_victim')
    info = folder['src']['fx30_victim']
    path = os.path.join(d, 'B0002.MP4')
    with open(path, 'r+b') as f:
        f.truncate(info['moov_pos'])
    c = by(core.scan(d), 'B0002.MP4')
    assert c.extra['mode'] == 'reconstruir' and os.path.basename(c.reference) == 'B0001.MP4'
    out = core.repair(c, folder['out'])
    golden, audio = folder['src']['fx30_victim_golden']
    assert frame_md5s(out) == golden
    assert pcm(out) == audio
