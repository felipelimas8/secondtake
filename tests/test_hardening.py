"""Blindagem: CSV, config.json, XML da Sony, pastas de saída, subprocessos e find_moov.

Não precisam de ffmpeg; os testes que usam clipes sintéticos ficam em test_recovery.py.
"""
from __future__ import annotations

import csv
import os
import struct
import subprocess
import sys
import threading
import time

import pytest

from secondtake import core, i18n


# ---------------------------------------------------------------- CSV injection (B1)

def test_csv_safe_prefixes_formulas_only_in_text():
    assert core.csv_safe('=HYPERLINK("http://x")') == '\'=HYPERLINK("http://x")'
    assert core.csv_safe('+cmd') == "'+cmd"
    assert core.csv_safe('-2+3') == "'-2+3"
    assert core.csv_safe('@SUM(A1)') == "'@SUM(A1)"
    assert core.csv_safe('\tx') == "'\tx"
    assert core.csv_safe('C0001.MP4') == 'C0001.MP4'
    assert core.csv_safe(-42.5) == -42.5            # número continua número (pico de áudio)
    assert core.csv_safe(1234) == 1234


def test_report_neutralizes_malicious_file_names(tmp_path):
    folder = tmp_path / '=cmd+calc'   # sem | (proibido no Windows)
    folder.mkdir()
    c = core.Clip(str(folder / '=HYPERLINK(1+1)-C0001.MP4'), 10, core.OK)
    c.audio_db = -80.0
    c.reference = str(tmp_path / '@ref.MP4')
    rep = tmp_path / 'r.csv'
    core.write_report([c], str(rep))
    with open(rep, encoding='utf-8', newline='') as f:
        rows = list(csv.reader(f))
    head, row = rows[0], rows[1]
    cell = dict(zip(head, row))
    name = cell[i18n.t('csv.name')]
    assert name.startswith("'=")
    assert cell[i18n.t('csv.reference')].startswith("'@")
    assert cell[i18n.t('csv.path')].startswith(os.sep) or ':' in cell[i18n.t('csv.path')]
    assert cell[i18n.t('csv.audio_db')] == '-80.0'   # número negativo intacto
    for v in row:
        assert not v.startswith(('=', '+', '@')), v


# ---------------------------------------------------------------- config.json (B2)

@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / 'cfg' / 'config.json'
    monkeypatch.setattr(i18n, '_config_path', lambda: str(p))
    return p


def test_config_concurrent_writes_keep_every_key(cfg_path):
    def worker(n):
        for j in range(25):
            i18n.save_config(**{f't{n}_{j}': j})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    cfg = i18n.load_config()
    assert all(cfg.get(f't{n}_{j}') == j for n in range(8) for j in range(25))
    assert os.listdir(cfg_path.parent) == ['config.json']      # nenhum .tmp sobrando


def test_config_corrupted_or_wrong_type_is_ignored(cfg_path):
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('{"lang": "pt", ', encoding='utf-8')     # gravação cortada (versão antiga)
    assert i18n.load_config() == {}
    cfg_path.write_text('[1, 2]', encoding='utf-8')
    assert i18n.load_config() == {}
    i18n.save_config(lang='en')
    assert i18n.load_config() == {'lang': 'en'}


def test_config_write_failure_leaves_old_file(cfg_path, monkeypatch):
    i18n.save_config(lang='pt')

    def boom(*a, **k):
        raise OSError(28, 'No space left on device')

    with monkeypatch.context() as m:
        m.setattr(i18n.json, 'dump', boom)
        i18n.save_config(lang='en')                             # falha em silêncio
    assert i18n.load_config()['lang'] == 'pt'
    assert sorted(os.listdir(cfg_path.parent)) == ['config.json']


# ---------------------------------------------------------------- XML da Sony (B4)

NS = 'urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20'


def _xml_pair(tmp_path, body: str):
    clip = tmp_path / 'C0001.MP4'
    clip.write_bytes(b'\0' * 16)
    (tmp_path / 'C0001M01.XML').write_text(body, encoding='utf-8')
    return str(clip)


def test_sony_xml_still_reads_camera(tmp_path):
    p = _xml_pair(tmp_path, f'<?xml version="1.0"?><NonRealTimeMeta xmlns="{NS}">'
                            '<Device manufacturer="Sony" modelName="ILME-FX3" serialNo="1234"/>'
                            '</NonRealTimeMeta>')
    assert core.sony_xml(p) == {'camera': 'ILME-FX3', 'serial': '1234'}


def test_sony_xml_rejects_entity_expansion(tmp_path):
    lol = ('<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaa">'
           + ''.join(f'<!ENTITY {chr(98 + i)} "{("&" + chr(97 + i) + ";") * 10}">' for i in range(8))
           + f']><NonRealTimeMeta xmlns="{NS}"><Device modelName="&i;"/></NonRealTimeMeta>')
    p = _xml_pair(tmp_path, lol)
    t0 = time.monotonic()
    assert core.sony_xml(p) == {}
    assert time.monotonic() - t0 < 2


def test_sony_xml_rejects_external_entity(tmp_path):
    secret = tmp_path / 'segredo.txt'
    secret.write_text('senha', encoding='utf-8')
    p = _xml_pair(tmp_path, f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]>'
                            f'<NonRealTimeMeta xmlns="{NS}"><Device modelName="&x;"/></NonRealTimeMeta>')
    assert 'senha' not in str(core.sony_xml(p))


# ---------------------------------------------------------------- pasta de saída (M2)

def test_is_inside_uses_real_paths(tmp_path):
    src = tmp_path / 'CLIP'
    (src / 'sub').mkdir(parents=True)
    (tmp_path / 'CLIPS').mkdir()
    assert core.is_inside(str(src / 'sub'), str(src))
    assert core.is_inside(str(src / 'sub') + os.sep, str(src) + os.sep)
    assert not core.is_inside(str(tmp_path / 'CLIPS'), str(src))     # prefixo de texto não conta
    assert not core.is_inside(str(src), str(src))                    # mesma pasta: vai para Second Take
    assert not core.is_inside(str(tmp_path), str(src))
    link = tmp_path / 'atalho'
    try:
        os.symlink(src, link)
    except (OSError, NotImplementedError):
        return
    assert core.is_inside(str(link / 'sub'), str(src))                # pelo atalho também


def test_out_inside_source_exempts_second_take(tmp_path):
    src = tmp_path / 'CLIP'
    (src / 'Second Take').mkdir(parents=True)
    (src / 'saida').mkdir()
    assert core.out_inside_source(str(src / 'saida'), [str(src)])
    assert not core.out_inside_source(str(src / 'Second Take'), [str(src)])
    assert not core.out_inside_source(str(src), [str(src)])
    assert not core.out_inside_source(str(tmp_path / 'outra'), [str(src)])


def test_same_dir_ignores_case_where_the_disk_does(tmp_path):
    d = tmp_path / 'Cartao'
    d.mkdir()
    other = tmp_path / 'CARTAO'
    if not other.exists():
        pytest.skip('sistema de arquivos diferencia maiúsculas (Linux)')
    assert core.same_dir(str(d), str(other))
    assert core.clip_out_dir(str(d / 'C0001.MP4'), str(other)).endswith('Second Take')


# ---------------------------------------------------------------- subprocessos (M1)

SLEEP = [sys.executable, '-c', 'import time; time.sleep(60)']


def test_run_quiet_timeout_kills_process():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        core.run_quiet(SLEEP, capture_output=True, timeout=1)
    assert time.monotonic() - t0 < 15


def test_run_quiet_timeout_with_cancel_event():
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        core.run_quiet(SLEEP, capture_output=True, text=True, timeout=1, cancel=threading.Event())
    assert time.monotonic() - t0 < 15


def test_run_quiet_cancel_stops_process():
    ev = threading.Event()
    threading.Timer(0.5, ev.set).start()
    t0 = time.monotonic()
    with pytest.raises(core.Cancelled):
        core.run_quiet(SLEEP, capture_output=True, timeout=60, cancel=ev)
    assert time.monotonic() - t0 < 15


def test_run_quiet_with_cancel_returns_output():
    r = core.run_quiet([sys.executable, '-c', 'print("ok")'], capture_output=True, text=True,
                       timeout=30, cancel=threading.Event())
    assert r.returncode == 0 and r.stdout.strip() == 'ok'


def test_verify_cancel_does_not_hang(tmp_path, monkeypatch):
    out = tmp_path / 'C0001_REPARADO.MP4'
    out.write_bytes(b'\0' * 100)
    c = core.Clip(str(tmp_path / 'C0001.MP4'), 100, core.HEADER)
    c.output = str(out)
    monkeypatch.setattr(core, 'ffmpeg_bin', lambda name='ffmpeg': sys.executable)
    real = core.run_quiet
    monkeypatch.setattr(core, 'run_quiet', lambda cmd, **kw: real(SLEEP, **kw))
    ev = threading.Event()
    threading.Timer(0.5, ev.set).start()
    with pytest.raises(core.Cancelled):
        core.verify(c, cancel=ev)


# ---------------------------------------------------------------- find_moov em duas etapas (M3)

def _mp4_with_moov(path, moov_payload: int, tail_zeros: int = 0):
    trak = struct.pack('>I4s', 8 + moov_payload, b'trak') + b'\1' * moov_payload
    moov = struct.pack('>I4s', 8 + len(trak), b'moov') + trak
    with open(path, 'wb') as f:
        f.write(struct.pack('>I4s', 16, b'ftyp') + b'XAVC\0\0\0\0')
        f.write(struct.pack('>I4s', 8 + 3_000_000, b'mdat') + b'\2' * 3_000_000)
        f.write(moov)
        f.write(b'\0' * tail_zeros)
    return 16 + 8 + 3_000_000, moov


@pytest.mark.parametrize('payload, calls', [(300_000, 1), (3_000_000, 2)])
def test_find_moov_reads_1mb_first(tmp_path, monkeypatch, payload, calls):
    p = str(tmp_path / 'C0001.MP4')
    pos, moov = _mp4_with_moov(p, payload)
    seen = []
    real = core._find_moov_in

    def spy(f, size, window):
        seen.append(window)
        return real(f, size, window)

    monkeypatch.setattr(core, '_find_moov_in', spy)
    assert core.find_moov(p) == (pos, moov)
    assert seen[0] == core.TAIL_FIRST and len(seen) == calls


def test_find_moov_none_without_index(tmp_path):
    p = str(tmp_path / 'C0001.MP4')
    with open(p, 'wb') as f:
        f.write(os.urandom(2_500_000))
    assert core.find_moov(p) is None


# ---------------------------------------------------------------- permissões do macOS (B5)

def test_build_script_sets_privacy_strings():
    root = os.path.join(os.path.dirname(__file__), '..')
    sh = open(os.path.join(root, 'scripts', 'build_macos.sh'), encoding='utf-8').read()
    for key in ('NSRemovableVolumesUsageDescription', 'NSNetworkVolumesUsageDescription',
                'NSDesktopFolderUsageDescription', 'NSDocumentsFolderUsageDescription',
                'NSDownloadsFolderUsageDescription'):
        assert key in sh
    assert 'pt-BR.lproj/InfoPlist.strings' in sh and 'en.lproj/InfoPlist.strings' in sh
    # o texto vai entre aspas simples no PlistBuddy: não pode ter apóstrofo
    for line in sh.splitlines():
        if line.startswith(('PRIV_EN=', 'PRIV_PT=')):
            assert "'" not in line


def test_defusedxml_is_a_dependency():
    root = os.path.join(os.path.dirname(__file__), '..')
    assert 'defusedxml' in open(os.path.join(root, 'pyproject.toml'), encoding='utf-8').read()
