"""XAVC HS (H.265 / HEVC), suporte de base (0.9.11).

O que já funciona com HEVC: reconhecer o codec (hvc1/hev1), ler perfil/nível/4:2:2/10-bit
do hvcC, trocar o cabeçalho (nível 1, o índice original é mantido) e gerar a prévia.
O que ainda NÃO funciona: reconstruir o índice e a extração bruta, que dependem do layout dos
blocos da câmera; isso espera um clipe XAVC HS real. Estes testes garantem que o app recusa
esses casos com um diagnóstico claro, em vez de tentar com o parser H.264.

Clipes sintéticos (tests/synth.py, codec='hevc'); nada de material real.
"""
from __future__ import annotations

import io
import os
import shutil
import struct
import subprocess

import pytest

from secondtake import cli, core, i18n, rebuild as rb

import synth

FF = shutil.which('ffmpeg')


def _has_x265() -> bool:
    if FF is None:
        return False
    r = subprocess.run([FF, '-hide_banner', '-encoders'], capture_output=True, text=True)
    return 'libx265' in r.stdout


needs_x265 = pytest.mark.skipif(not _has_x265(), reason='ffmpeg com libx265 não encontrado')


def frame_md5s(path):
    r = subprocess.run([FF, '-v', 'error', '-i', path, '-map', '0:v:0', '-f', 'framemd5', '-'],
                       capture_output=True, text=True)
    return [x.split(',')[-1].strip() for x in r.stdout.splitlines() if x and not x.startswith('#')]


def pcm(path):
    return subprocess.run([FF, '-v', 'error', '-i', path, '-map', '0:a:0', '-f', 's16le', '-'],
                          capture_output=True).stdout


def scramble(path, start, length):
    with open(path, 'r+b') as f:
        f.seek(start)
        f.write(os.urandom(length))


def strip_moov(path, info):
    with open(path, 'r+b') as f:
        f.truncate(info['moov_pos'])


def by(clips, name):
    return next(c for c in clips if c.name == name)


def hvcc(profile=4, level=153, chroma=2, depth=10, nals=()):
    """hvcC mínimo (ISO/IEC 14496-15) dentro de uma entrada hvc1, como num stsd."""
    body = bytes([1, profile & 0x1F]) + b'\0' * 4 + b'\0' * 6 + bytes([level]) + b'\xf0\x00' + b'\xfc' + \
        bytes([0xFC | chroma, 0xF8 | (depth - 8), 0xF8 | (depth - 8)]) + b'\0\0' + b'\x0f'
    arrays = bytes([len(nals)])
    for nal in nals:
        arrays += bytes([0x80 | ((nal[0] >> 1) & 0x3F)]) + struct.pack('>HH', 1, len(nal)) + nal
    hv = struct.pack('>I4s', 8 + len(body) + len(arrays), b'hvcC') + body + arrays
    entry = struct.pack('>I4s', 8 + 78 + len(hv), b'hvc1') + b'\0' * 78 + hv
    return struct.pack('>I', 0) + struct.pack('>I', 1) + entry


# ---------------------------------------------------------------- sem ffmpeg

@pytest.mark.parametrize('kw, profile, chroma, depth, level', [
    (dict(profile=4, chroma=2, depth=10, level=153), 'Main 4:2:2 10', '4:2:2', 10, '5.1'),
    (dict(profile=2, chroma=1, depth=10, level=150), 'Main 10', '4:2:0', 10, '5'),
    (dict(profile=1, chroma=1, depth=8, level=123), 'Main', '4:2:0', 8, '4.1'),
    (dict(profile=4, chroma=3, depth=12, level=156), 'Main 4:4:4 12', '4:4:4', 12, '5.2'),
])
def test_hevc_info_from_hvcc(kw, profile, chroma, depth, level):
    info = core._hevc_info(hvcc(**kw))
    assert info == {'profile': profile, 'chroma': chroma, 'bit_depth': depth, 'level': level}


def test_hvcc_params_keep_order_and_types():
    vps, sps, pps = b'\x40\x01\xaa', b'\x42\x01\xbb\xcc', b'\x44\x01\xdd'
    st = hvcc(nals=(vps, sps, pps))
    assert rb.video_family(st) == 'hevc'
    assert rb.hvcc_params(st) == [vps, sps, pps]
    assert [(n[0] >> 1) & 0x3F for n in rb.hvcc_params(st)] == [32, 33, 34]


def test_hvcc_params_truncated_is_safe():
    st = hvcc(nals=(b'\x40\x01' + b'\xaa' * 20,))
    assert rb.hvcc_params(st[:-5]) == []
    assert rb.hvcc_params(b'') == []
    assert core._hevc_info(b'hvcC' + b'\0' * 5) == {}


def test_video_family_from_moov_or_xml():
    assert core.video_family({'video_codec': 'hvc1'}) == 'hevc'
    assert core.video_family({'video_codec': 'hev1'}) == 'hevc'
    assert core.video_family({'video_codec': 'avc1'}) == 'avc'
    assert core.video_family({'xml_codec': 'HEVC_3840_2160_H422_10@L51'}) == 'hevc'
    assert core.video_family({'xml_codec': 'AVC_3840_2160_H422IP@L52'}) == 'avc'
    assert core.video_family({}) == ''
    # o moov manda sobre o XML
    assert core.video_family({'video_codec': 'avc1', 'xml_codec': 'HEVC_X'}) == 'avc'


def test_hevc_messages_are_bilingual():
    for k in ('diag.hevc_rebuild', 'err.hevc_rebuild', 'err.hevc_ref'):
        pt, en = i18n.CATALOG[k]
        assert pt and en and pt != en


# ---------------------------------------------------------------- com clipes sintéticos

@pytest.fixture(scope='session')
def hs(tmp_path_factory):
    d = tmp_path_factory.mktemp('hs')
    ref = synth.make_xavc(str(d / 'H0001.MP4'), seconds=3, seed=1, codec='hevc')
    good = synth.make_xavc(str(d / 'H0002.MP4'), seconds=4, seed=2, codec='hevc')
    avc = synth.make_xavc(str(d / 'C0001.MP4'), seconds=3, seed=3)
    avc_victim = synth.make_xavc(str(d / 'C0002.MP4'), seconds=4, seed=4)
    golden = frame_md5s(good['path'])
    assert len(golden) == good['n_frames']
    return {'ref': ref, 'good': good, 'avc': avc, 'avc_victim': avc_victim,
            'golden': golden, 'pcm': pcm(good['path'])}


@pytest.fixture
def folder(tmp_path, hs):
    src = tmp_path / 'in'
    src.mkdir()

    def put(*keys):
        for k in keys:
            shutil.copy(hs[k]['path'], src / os.path.basename(hs[k]['path']))
    return {'dir': str(src), 'out': str(tmp_path / 'out'), 'put': put, 'hs': hs}


@needs_x265
def test_intact_hevc_clip_metadata(hs):
    c = core.diagnose(hs['good']['path'])
    assert c.status == core.OK
    m = c.extra['meta']
    assert m['video_codec'] == 'hvc1'
    assert (m['profile'], m['chroma'], m['bit_depth']) == ('Main 4:2:2 10', '4:2:2', 10)
    rows = dict(core.meta_rows(c))
    assert rows['Codec'].startswith('H.265 Main 4:2:2 10')
    assert '4:2:2 10-bit' in rows['Codec']
    assert core.signature(c)['vcodec'] == 'hevc'


@needs_x265
def test_hevc_header_repair_bit_exact_and_prefers_hevc_reference(folder):
    folder['put']('avc', 'ref', 'good')                 # AVC e HEVC íntegros na mesma pasta
    victim = os.path.join(folder['dir'], 'H0002.MP4')
    scramble(victim, 0, core.HDR)
    c = by(core.scan(folder['dir']), 'H0002.MP4')
    assert c.status == core.HEADER
    assert os.path.basename(c.reference) == 'H0001.MP4'   # mesma família de codec primeiro
    out = core.repair(c, folder['out'])
    assert frame_md5s(out) == folder['hs']['golden']
    assert pcm(out) == folder['hs']['pcm']


@needs_x265
def test_hevc_without_moov_is_refused_with_clear_diagnosis(folder, capsys):
    folder['put']('ref', 'good')
    victim = os.path.join(folder['dir'], 'H0002.MP4')
    strip_moov(victim, folder['hs']['good'])
    before = open(victim, 'rb').read()
    c = by(core.scan(folder['dir']), 'H0002.MP4')
    assert c.status == core.NO_MOOV
    assert c.reference == ''                           # fora do lote de reparo da interface
    assert not core.repairable(c)                      # nem selecionando a linha à mão
    assert 'XAVC HS' in str(c.detail)
    with pytest.raises(ValueError, match='H.265'):
        core.repair(c, folder['out'])
    # forçado pela linha de comando também não passa
    assert cli.main(['repair', victim, '-o', folder['out'], '--mode', 'reconstruir',
                     '--ref', os.path.join(folder['dir'], 'H0001.MP4')]) != 0
    assert cli.main(['repair', victim, '-o', folder['out'], '--mode', 'extrair',
                     '--ref', os.path.join(folder['dir'], 'H0001.MP4')]) != 0
    assert not os.path.exists(folder['out']) or not [f for f in os.listdir(folder['out'])
                                                    if f.upper().endswith('.MP4')]
    assert open(victim, 'rb').read() == before          # original intacto


@needs_x265
def test_hevc_reference_is_never_used_to_rebuild_h264(folder):
    folder['put']('ref', 'avc', 'avc_victim')
    victim = os.path.join(folder['dir'], 'C0002.MP4')
    strip_moov(victim, folder['hs']['avc_victim'])
    with pytest.raises(ValueError, match='H.265'):
        rb.load_profile(os.path.join(folder['dir'], 'H0001.MP4'), core.find_moov)
    c = by(core.scan(folder['dir']), 'C0002.MP4')
    assert c.extra['mode'] == 'reconstruir'
    assert os.path.basename(c.reference) == 'C0001.MP4'


@needs_x265
def test_hevc_thumbnails(folder):
    from PIL import Image
    folder['put']('good')
    victim = os.path.join(folder['dir'], 'H0002.MP4')
    pngs = core.thumbnails(victim)
    assert len(pngs) == 3 and all(pngs)
    for png in pngs:
        assert Image.open(io.BytesIO(png)).width == core.THUMB_WIDTH
    scramble(victim, 0, core.HDR)                       # sem cabeçalho: mesma prévia
    assert core.thumbnails(victim) == pngs


@needs_x265
def test_sniff_codec_from_mdat(hs, tmp_path):
    assert rb.sniff_video_family(hs['good']['path']) == 'hevc'
    assert rb.sniff_video_family(hs['avc']['path']) == 'avc'
    noise = tmp_path / 'noise.bin'
    noise.write_bytes(os.urandom(core.HDR + (4 << 20)))
    assert rb.sniff_video_family(str(noise)) == ''
