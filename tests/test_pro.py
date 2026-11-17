"""Diferenciais de pós-produção (0.9.8): datas do arquivo, timecode na extração,
pareamento de referência pela assinatura técnica (XML da Sony + moov) e prévia em 3 quadros.

Usa clipes sintéticos (tests/synth.py); nada de material real.
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import threading

import pytest

from secondtake import core, rebuild as rb

import synth

FF = shutil.which('ffmpeg')
FP = shutil.which('ffprobe')
pytestmark = pytest.mark.skipif(FF is None or FP is None, reason='ffmpeg/ffprobe não encontrados')

NS = 'urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20'
SHOT = 1_794_571_200 * 10**9          # 13/11/2026 ~10h (UTC), em ns: "hora da filmagem"


def write_xml(clip: str, fps='23.98p', codec='AVC_3840_2160_H422IP@L52', camera='ILME-FX3',
              tc_value=None, tc_fps='24', width='3840', height='2160', channels='2'):
    """XML de câmera no formato da Sony (C0001M01.XML), com os campos que o app lê."""
    ltc = ''
    if tc_value:
        ltc = (f'<LtcChangeTable tcFps="{tc_fps}" halfStep="false">'
               f'<LtcChange frameCount="0" value="{tc_value}" status="increment"/></LtcChangeTable>')
    body = (f'<?xml version="1.0" encoding="UTF-8"?><NonRealTimeMeta xmlns="{NS}">'
            f'{ltc}<CreationDate value="2026-11-13T10:00:00-03:00"/>'
            f'<VideoFormat><VideoFrame videoCodec="{codec}" captureFps="{fps}" formatFps="{fps}"/>'
            f'<VideoLayout pixel="{width}" numOfVerticalLine="{height}" aspectRatio="16:9"/></VideoFormat>'
            f'<AudioFormat numOfChannel="{channels}"><AudioRecPort port="DIRECT" audioCodec="LPCM16" '
            f'trackDst="CH1"/></AudioFormat>'
            f'<Device manufacturer="Sony" modelName="{camera}" serialNo="4242"/></NonRealTimeMeta>')
    with open(os.path.splitext(clip)[0] + 'M01.XML', 'w', encoding='utf-8') as f:
        f.write(body)


def strip_moov(path, info):
    with open(path, 'r+b') as f:
        f.truncate(info['moov_pos'])


def scramble(path, start, length):
    with open(path, 'r+b') as f:
        f.seek(start)
        f.write(os.urandom(length))


def tc_of(path):
    r = subprocess.run([FP, '-v', 'error', '-show_entries', 'stream=codec_tag_string:stream_tags=timecode',
                        '-of', 'compact', path], capture_output=True, text=True)
    return r.stdout


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    d = tmp_path_factory.mktemp('lib')
    return {
        'ref': synth.make_xavc(str(d / 'ref.MP4'), seconds=3, seed=1),
        'good': synth.make_xavc(str(d / 'good.MP4'), seconds=6, seed=2),
        'other': synth.make_xavc(str(d / 'other.MP4'), seconds=8, seed=3, G=6, gop=12),
    }


@pytest.fixture
def card(tmp_path, lib):
    """Pasta CLIP de um cartão: C0001 (referência íntegra) e C0002 (vítima), com XML."""
    d = tmp_path / 'CLIP'
    d.mkdir()
    shutil.copy(lib['ref']['path'], d / 'C0001.MP4')
    shutil.copy(lib['good']['path'], d / 'C0002.MP4')
    write_xml(str(d / 'C0001.MP4'))
    write_xml(str(d / 'C0002.MP4'), tc_value='05302010')        # 10:20:30:05
    victim = str(d / 'C0002.MP4')
    os.utime(victim, ns=(SHOT, SHOT))
    return {'dir': str(d), 'victim': victim, 'out': str(tmp_path / 'out'), 'info': lib['good']}


def _by(clips, name):
    return next(c for c in clips if c.name == name)


# ---------------------------------------------------------------- datas do arquivo

@pytest.mark.parametrize('mode', ['cabecalho', 'reconstruir', 'extrair'])
def test_output_keeps_original_dates(card, mode):
    if mode == 'cabecalho':
        scramble(card['victim'], 0, core.HDR)
    else:
        strip_moov(card['victim'], card['info'])
    os.utime(card['victim'], ns=(SHOT, SHOT))                    # o dano mudou a data; volta à da filmagem
    c = _by(core.scan(card['dir']), 'C0002.MP4')
    if mode == 'extrair':
        core.set_extract(c, [os.path.join(card['dir'], 'C0001.MP4')])
    core.repair(c, card['out'])
    outs = [os.path.join(card['out'], n) for n in os.listdir(card['out']) if not n.endswith('.XML')]
    assert outs
    for p in outs:
        assert os.stat(p).st_mtime_ns == SHOT, os.path.basename(p)


def test_copy_times_does_not_copy_locked_flags(tmp_path):
    from secondtake.safeio import copy_times
    src, dst = tmp_path / 'a', tmp_path / 'b'
    src.write_bytes(b'x')
    dst.write_bytes(b'y')
    os.chmod(src, 0o444)
    os.utime(src, ns=(SHOT, SHOT))
    try:
        copy_times(os.stat(src), str(dst), str(tmp_path / 'nao_existe'))
        assert os.stat(dst).st_mtime_ns == SHOT
        assert os.access(dst, os.W_OK)                           # continua gravável
    finally:
        os.chmod(src, 0o644)                                     # o Windows não apaga somente-leitura


# ---------------------------------------------------------------- timecode

def test_sony_xml_timecode_and_drop_frame(tmp_path):
    clip = str(tmp_path / 'C0001.MP4')
    open(clip, 'wb').close()
    write_xml(clip, tc_value='05302010', tc_fps='24')
    assert core.sony_xml(clip)['timecode'] == '10:20:30:05'
    write_xml(clip, tc_value='45302010', tc_fps='30')            # bit 0x40 = drop-frame
    assert core.sony_xml(clip)['timecode'] == '10:20:30;05'
    write_xml(clip, tc_value='45302010', tc_fps='24')            # 23,98 não tem drop-frame
    assert core.sony_xml(clip)['timecode'] == '10:20:30:05'
    x = core.sony_xml(clip)
    assert (x['width'], x['height'], x['audio_ch']) == ('3840', '2160', '2')


def test_extracted_mp4_gets_timecode_from_xml(card):
    strip_moov(card['victim'], card['info'])
    c = _by(core.scan(card['dir']), 'C0002.MP4')
    core.set_extract(c, [os.path.join(card['dir'], 'C0001.MP4')])
    out = core.repair(c, card['out'])
    info = tc_of(out)
    assert 'tmcd' in info and 'timecode=10:20:30:05' in info


def test_no_timecode_when_start_was_lost(card):
    """Começo do arquivo danificado: o 1º quadro recuperado não é o do XML, então sem tmcd."""
    strip_moov(card['victim'], card['info'])
    scramble(card['victim'], card['info']['meta_offs'][0], 50_000)
    c = _by(core.scan(card['dir']), 'C0002.MP4')
    core.set_extract(c, [os.path.join(card['dir'], 'C0001.MP4')])
    out = core.repair(c, card['out'])
    assert out.endswith('.MP4')
    assert 'tmcd' not in tc_of(out)


def test_bad_timecode_still_gives_clean_mp4(card, monkeypatch):
    strip_moov(card['victim'], card['info'])
    c = _by(core.scan(card['dir']), 'C0002.MP4')
    core.set_extract(c, [os.path.join(card['dir'], 'C0001.MP4')])
    monkeypatch.setattr(core, 'sony_xml', lambda p: {'timecode': '99:99:99;99'})
    out = core.repair(c, card['out'])
    assert out.endswith('_EXTRAIDO.MP4') and os.path.exists(out)


# ---------------------------------------------------------------- pareamento pela assinatura

def test_reference_with_same_signature_comes_first(card, lib, monkeypatch):
    """Uma referência maior, na mesma pasta, mas de outra configuração (59,94p, outro bloco)
    não é mais a primeira tentativa: a de assinatura idêntica é, e basta ela."""
    other = os.path.join(card['dir'], 'C0009.MP4')
    shutil.copy(lib['other']['path'], other)
    write_xml(other, fps='59.94p', codec='AVC_1920_1080_HP@L42', width='1920', height='1080')
    strip_moov(card['victim'], card['info'])
    rb.clear_profile_cache()
    scans = []
    real = rb.scan_chunks
    monkeypatch.setattr(rb, 'scan_chunks', lambda path, prof, *a, **k: scans.append(prof.ref_path)
                        or real(path, prof, *a, **k))
    c = _by(core.scan(card['dir']), 'C0002.MP4')
    assert os.path.basename(c.reference) == 'C0001.MP4'
    assert [os.path.basename(p) for p in scans] == ['C0001.MP4']          # parou na primeira
    assert c.extra['mode'] == 'reconstruir'


def test_signature_uses_moov_when_there_is_no_xml(card):
    os.remove(os.path.join(card['dir'], 'C0001M01.XML'))
    ref = _by(core.scan(card['dir']), 'C0001.MP4')
    sig = core.signature(ref)
    assert sig['width'] == '320' and sig['height'] == '180'
    assert sig['format_fps'] == '23.98' and sig['audio_ch'] == '2'


def test_fps_key_normalizes_labels():
    assert core._fps_key('23.98p') == core._fps_key(23.976) == '23.98'
    assert core._fps_key('59.94p') == core._fps_key(59.94006) == '59.94'
    assert core._fps_key('50i') == '50i'
    assert core._fps_key('25') == '25'
    assert core.match_score({'codec': 'A', 'width': '3840'}, {'codec': 'A', 'width': '1920'}) == (1, 1)


def test_scan_files_quick_reference_has_signature(card):
    scramble(card['victim'], 0, core.HDR)
    c = core.scan_files([card['victim']])[0]
    assert os.path.basename(c.reference) == 'C0001.MP4'


# ---------------------------------------------------------------- prévia (3 quadros)

def _decoded_frame(path, index, width=core.THUMB_WIDTH):
    """Quadro `index` (ordem de decodificação = de exibição nos keyframes) decodificado pelo ffmpeg."""
    r = subprocess.run([FF, '-v', 'error', '-i', path, '-vf',
                        f'select=eq(n\\,{index}),scale={width}:-2:flags=bilinear,format=rgb24',
                        '-frames:v', '1', '-f', 'rawvideo', '-'], capture_output=True)
    return r.stdout


def test_thumbnails_match_the_real_frames(card):
    from PIL import Image
    pngs = core.thumbnails(card['victim'])
    assert len(pngs) == 3 and all(pngs)
    m = core.find_moov(card['victim'])
    samples, keys, _ = core.video_samples(m[1])
    for fr, png in zip(core.THUMB_FRACS, pngs):
        target = int(len(samples) * fr)
        k = max(x for x in keys if x <= target)
        im = Image.open(io.BytesIO(png)).convert('RGB')
        assert im.width == core.THUMB_WIDTH
        assert im.tobytes() == _decoded_frame(card['victim'], k)


def test_thumbnails_work_with_damaged_header(card):
    before = core.thumbnails(card['victim'])
    scramble(card['victim'], 0, core.HDR)
    c = core.diagnose(card['victim'])
    assert c.status == core.HEADER
    assert core.thumbnail_source(c) == card['victim']
    assert core.thumbnails(card['victim']) == before                     # mesmos quadros


def test_thumbnails_after_rebuild_use_the_output(card):
    strip_moov(card['victim'], card['info'])
    c = _by(core.scan(card['dir']), 'C0002.MP4')
    assert core.thumbnail_source(c) is None                              # sem índice: só depois do reparo
    assert core.thumbnails(card['victim']) == []
    out = core.repair(c, card['out'])
    assert core.thumbnail_source(c) == out
    assert len([p for p in core.thumbnails(out) if p]) == 3


def test_thumbnails_without_ffmpeg(card, monkeypatch):
    monkeypatch.setattr(core, 'ffmpeg_bin', lambda name='ffmpeg': None)
    assert core.thumbnails(card['victim']) == []


def test_run_quiet_sends_input_with_cancel_event():
    r = core.run_quiet([sys.executable, '-c', 'import sys; print(len(sys.stdin.buffer.read()))'],
                       input=b'x' * 3_000_000, capture_output=True, timeout=30, cancel=threading.Event())
    assert r.returncode == 0 and r.stdout.strip() == b'3000000'
