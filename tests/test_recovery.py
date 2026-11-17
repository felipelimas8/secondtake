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


def test_keep_names_in_originals_folder_goes_to_subfolder(work):
    """Manter o nome na pasta dos originais: vai para a subpasta Second Take, nunca por cima."""
    scramble(work['victim'], 0, core.HDR, seed=1)
    before = open(work['victim'], 'rb').read()
    c = by_name(core.scan(work['dir']), 'C0002.MP4')
    out = core.repair(c, work['dir'], keep_names=True)
    assert out == os.path.join(work['dir'], core.SAME_FOLDER_DIR, 'C0002.MP4')
    assert open(work['victim'], 'rb').read() == before                 # original intacto
    c2 = by_name(core.scan(work['dir']), 'C0002.MP4')                 # de novo: numera, não sobrescreve
    assert os.path.basename(core.repair(c2, work['dir'], keep_names=True)) == 'C0002_2.MP4'


def test_cli_keep_names(work):
    scramble(work['victim'], 0, core.HDR, seed=1)
    cli.main(['repair', work['dir'], '-o', work['out'], '--keep-names'])
    assert os.path.isfile(os.path.join(work['out'], 'C0002.MP4'))


# ---------------------------------------------------------------- escrita segura (0.9.7)

def _prepare(work, mode):
    """Clipe danificado pronto para `core.repair` no modo pedido."""
    ref = os.path.join(work['dir'], 'C0001.MP4')
    if mode == 'cabecalho':
        scramble(work['victim'], 0, core.HDR, seed=11)
        return by_name(core.scan(work['dir']), 'C0002.MP4')
    strip_moov(work['victim'], work['info'])
    c = core.diagnose(work['victim'])
    core.plan_rebuild(c, [ref])
    if mode == 'extrair':
        core.set_extract(c, [ref])
    return c


@pytest.mark.parametrize('mode', ['cabecalho', 'reconstruir', 'extrair'])
def test_write_error_leaves_no_truncated_output(work, mode):
    """Disco cheio / SSD desconectado no meio da escrita: nada com nome final, nem .part."""
    c = _prepare(work, mode)
    assert c.extra.get('mode', 'cabecalho') == mode
    calls = []

    def boom(done, total):
        calls.append(done)
        # chamado depois de cada bloco gravado: já há dados no disco quando falha
        raise OSError(28, 'No space left on device')

    with pytest.raises(OSError):
        core.repair(c, work['out'], boom)
    assert len(calls) == 1
    assert os.listdir(work['out']) == []
    assert not c.output


def test_atomic_write_removes_part_on_error(tmp_path):
    from secondtake.safeio import atomic_write, part_path
    dst = str(tmp_path / 'C0001_REPARADO.MP4')
    with pytest.raises(RuntimeError):
        with atomic_write(dst) as f:
            f.write(b'x' * 1000)
            assert os.path.exists(part_path(dst))
            raise RuntimeError('falhou no meio')
    assert os.listdir(tmp_path) == []
    with atomic_write(dst) as f:
        f.write(b'ok')
    assert os.listdir(tmp_path) == ['C0001_REPARADO.MP4']
    assert open(dst, 'rb').read() == b'ok'


def test_existing_output_gets_numbered_suffix(work):
    """Reparar de novo na mesma pasta cria _2, sem tocar no resultado anterior."""
    scramble(work['victim'], 0, core.HDR, seed=12)
    c = by_name(core.scan(work['dir']), 'C0002.MP4')
    first = core.repair(c, work['out'])
    assert os.path.basename(first) == 'C0002_REPARADO.MP4'
    before = hashlib.md5(open(first, 'rb').read()).hexdigest()
    second = core.repair(c, work['out'])
    assert os.path.basename(second) == 'C0002_REPARADO_2.MP4'
    third = core.repair(c, work['out'])
    assert os.path.basename(third) == 'C0002_REPARADO_3.MP4'
    assert hashlib.md5(open(first, 'rb').read()).hexdigest() == before
    got, err = frame_md5s(second)
    assert err == 0 and got == work['golden']


def test_extract_twice_numbers_the_whole_set(work):
    """Extração: .h264, .wav e .MP4 ganham o mesmo número."""
    c = _prepare(work, 'extrair')
    core.repair(c, work['out'])
    out2 = core.repair(c, work['out'])
    assert os.path.basename(out2) == 'C0002_EXTRAIDO_2.MP4'
    names = set(os.listdir(work['out']))
    for ext in ('.h264', '.wav', '.MP4'):
        assert f'C0002_EXTRAIDO{ext}' in names and f'C0002_EXTRAIDO_2{ext}' in names
    assert not [n for n in names if n.endswith('.part') or n.startswith('.')]


def test_unique_path_skips_existing_and_in_progress(tmp_path):
    from secondtake.safeio import part_path, unique_path, unique_stem
    p = str(tmp_path / 'C0001_REPARADO.MP4')
    assert unique_path(p) == p
    open(p, 'wb').close()
    open(part_path(str(tmp_path / 'C0001_REPARADO_2.MP4')), 'wb').close()   # outra gravação em curso
    assert unique_path(p) == str(tmp_path / 'C0001_REPARADO_3.MP4')
    # o XML existente também reserva o número
    open(tmp_path / 'C0009_REPARADOM01.XML', 'wb').close()
    assert unique_stem(str(tmp_path), 'C0009_REPARADO', ['.MP4', 'M01.XML']) == str(tmp_path / 'C0009_REPARADO_2')


def test_space_needed_depends_on_mode():
    a = core.Clip('/x/C0001.MP4', 1_000_000, core.HEADER)
    a.extra['mode'] = 'cabecalho'
    b = core.Clip('/x/C0002.MP4', 1_000_000, core.NO_MOOV)
    b.extra['mode'] = 'reconstruir'
    e = core.Clip('/x/C0003.MP4', 1_000_000, core.NO_MOOV)
    e.extra['mode'] = 'extrair'
    assert core.space_needed([a]) == 1_050_000
    assert core.space_needed([b]) == 1_050_000
    assert core.space_needed([e]) == 3_000_000
    assert core.space_needed([a, b, e]) == 5_100_000


# ---------------------------------------------------------------- timeouts (0.9.7)

def test_ffprobe_timeout_does_not_hang(work, monkeypatch):
    """Um ffprobe que não responde é encerrado e vira aviso, sem travar o reparo."""
    import sys
    import time
    scramble(work['victim'], 0, core.HDR, seed=13)
    c = by_name(core.scan(work['dir']), 'C0002.MP4')
    core.repair(c, work['out'])
    real_run = core.run_quiet
    seen = {}

    def hung_ffprobe(cmd, **kw):
        seen.update(kw)                         # mesmo timeout/flags que o verify passaria
        return real_run([sys.executable, '-c', 'import time; time.sleep(60)'], **kw)

    monkeypatch.setattr(core, 'ffmpeg_bin', lambda name='ffmpeg': sys.executable)
    monkeypatch.setattr(core, 'run_quiet', hung_ffprobe)
    monkeypatch.setattr(core, 'ffprobe_timeout', lambda size: 1.0)
    t0 = time.monotonic()
    v = core.verify(c)
    assert time.monotonic() - t0 < 20
    assert seen.get('timeout') == 1.0
    assert 'cancelada' in str(v)
    assert os.path.exists(c.output)              # o arquivo recuperado continua lá


def test_ffprobe_timeout_scales_with_size():
    assert core.ffprobe_timeout(0) == core.FFPROBE_BASE_S
    assert core.ffprobe_timeout(45_000_000_000) > core.ffprobe_timeout(1_000_000_000) > 60


def test_ffmpeg_copy_timeout_cleans_up(work, monkeypatch):
    monkeypatch.setattr(core, 'ffmpeg_timeout', lambda size: 0.001)
    dst = os.path.join(work['dir'], 'saida.MP4')
    assert rb._ffmpeg_copy(work['victim'], dst) is False
    assert not os.path.exists(dst)
    assert not [n for n in os.listdir(work['dir']) if n.endswith('.part')]


# ---------------------------------------------------------------- saída na pasta dos originais (0.9.7)

def test_output_in_source_folder_goes_to_subfolder(work):
    """Saída = pasta dos originais: grava em 'Second Take/', nunca ao lado do original."""
    scramble(work['victim'], 0, core.HDR, seed=14)
    before = set(os.listdir(work['dir']))
    c = by_name(core.scan(work['dir']), 'C0002.MP4')
    out = core.repair(c, work['dir'])
    assert out == os.path.join(work['dir'], 'Second Take', 'C0002_REPARADO.MP4')
    assert set(os.listdir(work['dir'])) == before | {'Second Take'}
    got, err = frame_md5s(out)
    assert err == 0 and got == work['golden']
    # a análise da pasta ignora a subpasta: só os originais aparecem
    assert sorted(x.name for x in core.scan(work['dir'])) == ['C0001.MP4', 'C0002.MP4']


def test_source_folder_check_with_trailing_slash(work):
    sub = core.clip_out_dir(work['victim'], work['dir'] + os.sep)
    assert sub == os.path.join(work['dir'] + os.sep, 'Second Take')
    assert core.clip_out_dir(work['victim'], work['out']) == work['out']


def test_cli_output_in_source_folder(work):
    scramble(work['victim'], 0, core.HDR, seed=15)
    assert cli.main(['repair', work['dir'], '-o', work['dir']]) == 0
    sub = os.path.join(work['dir'], 'Second Take')
    assert os.path.exists(os.path.join(sub, 'C0002_REPARADO.MP4'))
    assert os.path.exists(os.path.join(sub, 'relatorio_reparo.csv'))
    assert not os.path.exists(os.path.join(work['dir'], 'relatorio_reparo.csv'))


def test_cli_beside_saves_in_each_clip_folder(work, tmp_path, clips):
    """--beside: cada recuperado vai para <pasta do clipe>/Second Take, com um relatório por pasta."""
    scramble(work['victim'], 0, core.HDR, seed=16)
    card_b = tmp_path / 'cartao_b'
    card_b.mkdir()
    shutil.copy(clips['ref']['path'], card_b / 'C0001.MP4')
    other = card_b / 'C0005.MP4'
    shutil.copy(clips['good']['path'], other)
    scramble(str(other), 0, core.HDR, seed=17)
    rc = cli.main(['repair', work['victim'], str(other), '--beside'])
    assert rc == 0
    for d, name in ((work['dir'], 'C0002'), (str(card_b), 'C0005')):
        sub = os.path.join(d, 'Second Take')
        assert sorted(os.listdir(sub)) == sorted([f'{name}_REPARADO.MP4', 'relatorio_reparo.csv'])
        got, err = frame_md5s(os.path.join(sub, f'{name}_REPARADO.MP4'))
        assert err == 0 and got == work['golden']


def test_cli_needs_output_or_beside(work):
    with pytest.raises(SystemExit):
        cli.main(['repair', work['dir']])
    with pytest.raises(SystemExit):
        cli.main(['repair', work['dir'], '-o', work['out'], '--beside'])


def test_group_by_folder_and_beside_dir(tmp_path):
    a = core.Clip(str(tmp_path / 'A' / 'C0001.MP4'), 1)
    b = core.Clip(str(tmp_path / 'B' / 'C0002.MP4'), 1)
    c = core.Clip(str(tmp_path / 'A' / 'C0003.MP4'), 1)
    g = core.group_by_folder([a, b, c])
    assert list(g) == [str(tmp_path / 'A'), str(tmp_path / 'B')]
    assert g[str(tmp_path / 'A')] == [a, c]
    assert core.beside_dir(a.path) == str(tmp_path / 'A' / 'Second Take')


# ---------------------------------------------------------------- cancelar e cache (blindagem)

@pytest.mark.parametrize('mode', ['cabecalho', 'reconstruir', 'extrair'])
def test_cancel_mid_write_discards_clip(work, mode):
    """Cancelar no meio de um clipe: o .part é apagado e nada fica na saída."""
    c = _prepare(work, mode)

    def cancel(done, total):
        raise core.Cancelled()

    with pytest.raises(core.Cancelled):
        core.repair(c, work['out'], cancel)
    assert os.listdir(work['out']) == []


def test_reference_profile_is_cached(work, monkeypatch):
    ref = os.path.join(work['dir'], 'C0001.MP4')
    rb.clear_profile_cache()
    calls = []
    real = rb._load_profile
    monkeypatch.setattr(rb, '_load_profile', lambda p, fm: calls.append(p) or real(p, fm))
    a = rb.load_profile(ref, core.find_moov)
    b = rb.load_profile(ref, core.find_moov)
    assert a is b and len(calls) == 1
    os.utime(ref, ns=(0, 10**9))                 # arquivo trocado: lê de novo
    rb.load_profile(ref, core.find_moov)
    assert len(calls) == 2


def test_scan_reuses_reference_for_many_clips(work, clips, monkeypatch):
    """Pasta com vários clipes sem índice: a referência é lida uma vez só."""
    for n in (3, 4, 5):
        dst = os.path.join(work['dir'], f'C000{n}.MP4')
        shutil.copy(clips['good']['path'], dst)
        strip_moov(dst, work['info'])
    strip_moov(work['victim'], work['info'])
    rb.clear_profile_cache()
    calls = []
    real = rb._load_profile
    monkeypatch.setattr(rb, '_load_profile', lambda p, fm: calls.append(p) or real(p, fm))
    found = core.scan(work['dir'])
    assert sum(1 for c in found if c.extra.get('mode') == 'reconstruir') == 4
    assert calls.count(os.path.join(work['dir'], 'C0001.MP4')) == 1


def test_cli_warns_when_output_inside_source(work, capsys):
    scramble(work['victim'], 0, core.HDR, seed=18)
    out = os.path.join(work['dir'], 'saida')
    assert cli.main(['repair', work['dir'], '-o', out]) == 0
    assert 'dentro da pasta de origem' in capsys.readouterr().err
    assert os.path.exists(os.path.join(out, 'C0002_REPARADO.MP4'))
