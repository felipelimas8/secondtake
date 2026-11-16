#!/usr/bin/env python3
"""secondtake.core — diagnóstico e reparo de clipes Sony XAVC (MP4) com cabeçalho danificado.

Modo simples: repara arquivos cujo início (ftyp/uuid/free/mdat) foi corrompido mas o
`moov` no fim do arquivo continua íntegro — o índice original é mantido.
Modo avançado (secondtake.rebuild):
  · reconstrói o `moov` de gravações interrompidas a partir de um clipe de
    referência da mesma câmera, pulando trechos danificados (resync por keyframe);
  · último recurso: extração bruta do vídeo (.h264) e do áudio (.wav) + MP4 limpo.
Vídeo, áudio LPCM e metadados são preservados. Originais nunca são alterados.

Uso pelo Terminal:
  secondtake scan   PASTA|ARQUIVOS...
  secondtake repair PASTA|ARQUIVOS... -o PASTA_SAIDA [--verify]
"""
from __future__ import annotations

import array
import csv
import math
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field

from .i18n import L, Join, msg, t as tr

HDR = 262144                 # ftyp + uuid + free + cabeçalho mdat (layout XAVC S)
MDAT_POS = HDR - 16          # 262128
TAIL_SCAN = 16_000_000       # bytes lidos do fim procurando o moov
VIDEO_EXT = ('.mp4', '.mov')

OK = 'OK'
HEADER = 'CABECALHO_DANIFICADO'
NO_MOOV = 'SEM_MOOV'
EMPTY = 'VAZIO'
UNKNOWN = 'DESCONHECIDO'

REPAIRABLE = {HEADER, NO_MOOV, UNKNOWN}


# ---------------------------------------------------------------- MP4 parsing

def _boxes(buf: bytes, start: int = 0, end: int | None = None):
    end = len(buf) if end is None else end
    p = start
    while p + 8 <= end:
        size, typ = struct.unpack('>I4s', buf[p:p + 8])
        hdr = 8
        if size == 1:
            size = struct.unpack('>Q', buf[p + 8:p + 16])[0]
            hdr = 16
        elif size == 0:
            size = end - p
        if size < hdr or p + size > end:
            return
        yield typ, p + hdr, p + size
        p += size


def _child(buf, s, e, name):
    for t, cs, ce in _boxes(buf, s, e):
        if t == name:
            return cs, ce
    return None


def _path(buf, s, e, *names):
    for n in names:
        r = _child(buf, s, e, n)
        if r is None:
            return None
        s, e = r
    return s, e


@dataclass
class Track:
    handler: str
    fmt: str
    offsets: list[int]
    sizes: list[int]
    sample_size: int = 0
    channels: int = 0


def parse_moov(moov: bytes) -> list[Track]:
    tracks = []
    for t, s, e in _boxes(moov, 8):
        if t != b'trak':
            continue
        hd = _path(moov, s, e, b'mdia', b'hdlr')
        handler = moov[hd[0] + 8:hd[0] + 12].decode('latin1') if hd else '?'
        stbl = _path(moov, s, e, b'mdia', b'minf', b'stbl')
        if not stbl:
            continue
        ss, se = stbl
        fmt, ch, bits = '', 0, 0
        sd = _child(moov, ss, se, b'stsd')
        if sd:
            ent = sd[0] + 8
            fmt = moov[ent + 4:ent + 8].decode('latin1')
            if handler == 'soun':
                ch, bits = struct.unpack('>HH', moov[ent + 24:ent + 28])
        offsets = []
        for tag, fmt_s, w in ((b'stco', '>I', 4), (b'co64', '>Q', 8)):
            c = _child(moov, ss, se, tag)
            if c:
                n = struct.unpack('>I', moov[c[0] + 4:c[0] + 8])[0]
                b = c[0] + 8
                offsets = [struct.unpack(fmt_s, moov[b + i * w:b + (i + 1) * w])[0] for i in range(n)]
        sizes, ssize = [], 0
        sz = _child(moov, ss, se, b'stsz')
        if sz:
            ssize, n = struct.unpack('>II', moov[sz[0] + 4:sz[0] + 12])
            if ssize == 0:
                b = sz[0] + 12
                sizes = list(struct.unpack(f'>{n}I', moov[b:b + 4 * n]))
        tracks.append(Track(handler, fmt, offsets, sizes, ssize, ch))
    return tracks


def find_moov(path: str) -> tuple[int, bytes] | None:
    size = os.path.getsize(path)
    base = max(0, size - TAIL_SCAN)
    with open(path, 'rb') as f:
        f.seek(base)
        tail = f.read()
    i = len(tail)
    while True:
        i = tail.rfind(b'moov', 0, i)
        if i < 4:
            return None
        msize = struct.unpack('>I', tail[i - 4:i])[0]
        if 8 < msize <= len(tail) - (i - 4) and b'trak' in tail[i:i - 4 + msize]:
            return base + i - 4, tail[i - 4:i - 4 + msize]


# ---------------------------------------------------------------- Sony XML

def sony_xml(path: str) -> dict:
    """Metadados do XML que as Sony gravam ao lado do clipe (C0001M01.XML)."""
    stem = os.path.splitext(path)[0]
    for cand in (stem + 'M01.XML', stem + 'M01.xml'):
        if not os.path.exists(cand):
            continue
        try:
            import xml.etree.ElementTree as ET
            r = ET.parse(cand).getroot()
            ns = {'n': r.tag.split('}')[0][1:]}
            g = lambda q, a: (r.find(q, ns).get(a, '') if r.find(q, ns) is not None else '')  # noqa: E731
            item = lambda name: g(f".//n:Item[@name='{name}']", 'value')  # noqa: E731
            tc = r.find('.//n:LtcChangeTable/n:LtcChange', ns)
            tc_fps = g('.//n:LtcChangeTable', 'tcFps')
            out = {
                'codec': g('.//n:VideoFrame', 'videoCodec'),
                'fps': g('.//n:VideoFrame', 'captureFps'),
                'format_fps': g('.//n:VideoFrame', 'formatFps'),
                'camera': g('n:Device', 'modelName'),
                'serial': g('n:Device', 'serialNo'),
                'gamma': item('CaptureGammaEquation'),
                'primaries': item('CaptureColorPrimaries'),
                'created': g('n:CreationDate', 'value'),
                'audio_codec': g('.//n:AudioRecPort', 'audioCodec'),
                'lens': g('.//n:Lens', 'modelName') or item('LensModelName'),
            }
            if tc is not None and tc.get('value') and len(tc.get('value')) == 8:
                # LTC em BCD, bytes invertidos (FF SS MM HH) com bits de flag misturados
                b = bytes.fromhex(tc.get('value'))
                ff, ss, mm, hh = b[0] & 0x3F, b[1] & 0x7F, b[2] & 0x7F, b[3] & 0x3F
                out['timecode'] = f'{hh:02x}:{mm:02x}:{ss:02x}:{ff:02x}'
            if tc_fps:
                out['tc_fps'] = tc_fps
            return {k: v for k, v in out.items() if v and v != '4294967295'}
        except Exception:
            pass
    return {}


def _ue(bits, pos):
    """Exp-Golomb sem sinal (SPS H.264)."""
    z = 0
    while pos < len(bits) and bits[pos] == '0':
        z += 1
        pos += 1
    pos += 1
    val = (1 << z) - 1 + (int(bits[pos:pos + z], 2) if z else 0)
    return val, pos + z


H264_PROFILES = {66: 'Baseline', 77: 'Main', 88: 'Extended', 100: 'High', 110: 'High 10',
                 122: 'High 4:2:2', 244: 'High 4:4:4'}


def _avc_info(stsd: bytes) -> dict:
    i = stsd.find(b'avcC')
    if i < 0:
        return {}
    a = i + 4
    prof, level = stsd[a + 1], stsd[a + 3]
    info = {'profile': H264_PROFILES.get(prof, str(prof)), 'level': f'{level // 10}.{level % 10}'}
    try:
        ln = int.from_bytes(stsd[a + 6:a + 8], 'big')
        sps = stsd[a + 8:a + 8 + ln]
        raw = bytearray()
        z = 0
        for b in sps[1:]:                       # remove emulation prevention (00 00 03)
            if z >= 2 and b == 3:
                z = 0
                continue
            raw.append(b)
            z = z + 1 if b == 0 else 0
        bits = ''.join(f'{b:08b}' for b in raw)
        pos = 24
        _, pos = _ue(bits, pos)                 # seq_parameter_set_id
        chroma, depth = 1, 8
        if prof in (100, 110, 122, 244, 44, 83, 86, 118, 128):
            chroma, pos = _ue(bits, pos)
            if chroma == 3:
                pos += 1
            dl, pos = _ue(bits, pos)
            depth = dl + 8
        info['chroma'] = {0: '4:0:0', 1: '4:2:0', 2: '4:2:2', 3: '4:4:4'}.get(chroma, '?')
        info['bit_depth'] = depth
    except Exception:
        pass
    return info


def _fps_label(fps: float) -> str:
    for nominal, label in ((23.976, '23.976'), (29.97, '29.97'), (59.94, '59.94'), (119.88, '119.88'),
                           (47.952, '47.952')):
        if abs(fps - nominal) < 0.01:
            return label
    return f'{fps:.3f}'.rstrip('0').rstrip('.')


def read_meta(path: str, moov: bytes | None = None) -> dict:
    """Metadados técnicos do clipe: do moov (quando existe) e do XML da Sony."""
    from .rebuild import Box
    meta = {'file_size': os.path.getsize(path)}
    meta.update({f'xml_{k}': v for k, v in sony_xml(path).items()})
    if moov is None:
        m = find_moov(path)
        moov = m[1] if m else None
    if not moov:
        return meta
    try:
        root = Box.parse(moov)[0]
        mv = root.find(b'mvhd').data
        ts, dur = (struct.unpack('>IQ', mv[20:32]) if mv[0] == 1 else struct.unpack('>II', mv[12:20]))
        if ts:
            meta['duration'] = dur / ts
        for t in root.all(b'trak'):
            h = t.find(b'mdia', b'hdlr').data[8:12]
            md = t.find(b'mdia', b'mdhd').data
            mts, mdur = (struct.unpack('>IQ', md[20:32]) if md[0] == 1 else struct.unpack('>II', md[12:20]))
            stbl = t.find(b'mdia', b'minf', b'stbl')
            sd = stbl.find(b'stsd').data
            ent = 8
            fmt = sd[ent + 4:ent + 8].decode('latin1')
            if h == b'vide':
                w, hh = struct.unpack('>HH', sd[ent + 32:ent + 36])
                sz = stbl.find(b'stsz').data
                fixed, n = struct.unpack('>II', sz[4:12])
                vbytes = fixed * n if fixed else sum(struct.unpack(f'>{n}I', sz[12:12 + 4 * n]))
                meta.update({'video_codec': fmt, 'width': w, 'height': hh, 'frames': n})
                if mdur and mts:
                    meta['fps'] = n * mts / mdur
                    meta['video_bitrate'] = vbytes * 8 / (mdur / mts)
                meta.update(_avc_info(sd))
            elif h == b'soun':
                ch, bits = struct.unpack('>HH', sd[ent + 24:ent + 28])
                meta.update({'audio_codec': {'twos': 'LPCM', 'sowt': 'LPCM', 'lpcm': 'LPCM',
                                             'mp4a': 'AAC'}.get(fmt, fmt),
                             'audio_channels': ch, 'audio_bits': bits, 'audio_rate': mts})
        if meta.get('duration'):
            meta['bitrate'] = meta['file_size'] * 8 / meta['duration']
    except Exception:
        pass
    return meta


def meta_rows(c: 'Clip') -> list[tuple[str, str]]:
    """Linhas (rótulo, valor) prontas para exibir, já no idioma atual."""
    m = c.extra.get('meta') or {}
    rows = []

    def add(key, val):
        if val not in (None, '', '—'):
            rows.append((tr(key), str(val)))

    add('meta.camera', ' · '.join(x for x in (m.get('xml_camera'), m.get('xml_serial') and f"S/N {m['xml_serial']}")
                                  if x))
    add('meta.created', (m.get('xml_created') or '').replace('T', ' ')[:19])
    add('meta.timecode', m.get('xml_timecode'))
    add('meta.lens', m.get('xml_lens'))
    if m.get('width'):
        add('meta.resolution', f"{m['width']} × {m['height']}")
    codec = ' '.join(x for x in ('H.264' if m.get('video_codec') == 'avc1' else m.get('video_codec'),
                                 m.get('profile'), m.get('level') and f"L{m['level']}") if x)
    if m.get('chroma'):
        codec += f" · {m['chroma']} {m.get('bit_depth', '')}-bit"
    add('meta.codec', codec)
    add('meta.xavc', m.get('xml_codec'))
    if m.get('fps'):
        fps = _fps_label(m['fps'])
        cap = m.get('xml_fps', '').rstrip('p')
        add('meta.fps', fps + (f"  ({tr('meta.capture')} {cap})" if cap and cap != fps else ''))
    elif m.get('xml_fps'):
        add('meta.fps', m['xml_fps'])
    if m.get('duration'):
        s = m['duration']
        add('meta.duration', f'{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}')
    if m.get('video_bitrate'):
        add('meta.bitrate', f"{m['video_bitrate'] / 1e6:.0f} Mb/s")
    elif m.get('bitrate'):
        add('meta.bitrate_file', f"{m['bitrate'] / 1e6:.0f} Mb/s")
    add('meta.color', ' / '.join(x for x in (m.get('xml_gamma'), m.get('xml_primaries')) if x))
    if m.get('audio_codec'):
        add('meta.audio', f"{m['audio_codec']} · {m.get('audio_channels')} ch · {m.get('audio_rate', 0) / 1000:g} kHz"
                          f" · {m.get('audio_bits')}-bit")
    add('meta.size', f"{m.get('file_size', c.size) / 1e9:.2f} GB" if m.get('file_size', c.size) > 1e9
        else f"{m.get('file_size', c.size) / 1e6:.1f} MB")
    return rows


# ---------------------------------------------------------------- diagnóstico

@dataclass
class Clip:
    path: str
    size: int
    status: str = UNKNOWN
    detail: object = ''          # str, L ou Join — traduzido na hora de exibir
    camera: str = ''
    codec: str = ''
    fps: str = ''
    moov_pos: int = -1
    audio_db: float | None = None
    reference: str = ''
    output: str = ''
    verify: object = ''
    extra: dict = field(default_factory=dict)

    def note(self, m):
        """Acrescenta uma observação ao diagnóstico."""
        self.detail = Join(*(self.detail.parts if isinstance(self.detail, Join) else [self.detail]), m, sep='; ')

    @property
    def name(self):
        return os.path.basename(self.path)

    @property
    def group(self):
        return (self.camera, self.codec)


def _audio_peak_db(path: str, tracks: list[Track], samples: int = 12) -> float | None:
    a = next((t for t in tracks if t.handler == 'soun' and t.offsets), None)
    if a is None:
        return None
    if a.fmt not in ('twos', 'sowt', 'lpcm', 'ipcm', 'in24'):
        return None
    big = a.fmt in ('twos', 'lpcm', 'ipcm')
    step = max(1, len(a.offsets) // samples)
    peak = 0
    with open(path, 'rb') as f:
        for o in a.offsets[1::step][:samples]:
            f.seek(o)
            buf = f.read(16000)
            arr = array.array('h', buf[:len(buf) // 2 * 2])
            if big and sys.byteorder == 'little':
                arr.byteswap()
            if arr:
                peak = max(peak, max(abs(x) for x in arr))
    return round(20 * math.log10(max(1, peak) / 32768), 1)


def diagnose(path: str) -> Clip:
    c = Clip(path, os.path.getsize(path))
    xml = sony_xml(path)
    c.camera, c.codec, c.fps = xml.get('camera', ''), xml.get('codec', ''), xml.get('fps', '')
    if c.size == 0:
        c.status, c.detail = EMPTY, L('diag.empty_file')
        return c
    with open(path, 'rb') as f:
        head = f.read(HDR)
    if head.count(0) == len(head):
        c.status, c.detail = EMPTY, L('diag.zero_start')
    m = find_moov(path)
    if m is None:
        c.status = NO_MOOV
        c.detail = L('diag.no_moov')
        c.extra['meta'] = read_meta(path, b'')
        return c
    c.moov_pos, moov = m
    c.extra['meta'] = read_meta(path, moov)
    try:
        tracks = parse_moov(moov)
    except Exception as e:
        c.status, c.detail = UNKNOWN, L('diag.bad_moov', err=e)
        return c
    c.extra['tracks'] = ','.join(f'{t.handler}:{t.fmt}' for t in tracks)
    if any(o >= c.moov_pos for t in tracks for o in t.offsets):
        c.status, c.detail = UNKNOWN, L('diag.moov_out')
        return c
    c.audio_db = _audio_peak_db(path, tracks)
    good_head = head[4:8] == b'ftyp' and head[MDAT_POS + 4:MDAT_POS + 8] == b'mdat'
    if good_head:
        c.status, c.detail = OK, ''
    elif c.status != EMPTY:
        c.status = HEADER
        c.detail = L('diag.header')
    else:
        c.status = HEADER
        c.detail = L('diag.header_zero')
    if c.audio_db is not None and c.audio_db < -75:
        c.note(L('diag.silent'))
    return c


def scan(folder: str, progress=None) -> list[Clip]:
    files = []
    for dp, dn, fn in os.walk(folder):
        dn[:] = [d for d in dn if not d.startswith('.') and d.upper() != 'SUB']
        for f in sorted(fn):
            if f.lower().endswith(VIDEO_EXT) and not f.startswith('._'):
                files.append(os.path.join(dp, f))
    clips = []
    for i, p in enumerate(files):
        try:
            clips.append(diagnose(p))
        except Exception as e:
            clips.append(Clip(p, os.path.getsize(p), UNKNOWN, L('diag.error', err=e)))
        if progress:
            progress(i + 1, len(files), clips[-1])
    _assign_references(clips, progress)
    return clips


def _is_reference(c: Clip) -> bool:
    if c.status != OK:
        return False
    with open(c.path, 'rb') as f:
        h = f.read(HDR)
    return h[4:8] == b'ftyp' and h[MDAT_POS + 4:MDAT_POS + 8] == b'mdat' \
        and struct.unpack('>I', h[MDAT_POS:MDAT_POS + 4])[0] == 1


def _ref_candidates(c: Clip, refs: list[Clip]) -> list[Clip]:
    same_dir = lambda r: os.path.dirname(r.path) == os.path.dirname(c.path)
    pool = [r for r in refs if c.codec and r.group == c.group] or list(refs)
    pool.sort(key=lambda r: (not same_dir(r), -r.size))
    return pool


def plan_rebuild(c: Clip, refs: list[str], data_start: int | None = None):
    """Simula a reconstrução (modo avançado) sem gravar nada."""
    from . import rebuild as rb
    p = rb.plan(c.path, refs, find_moov, data_start)
    c.reference = p.profile.ref_path
    c._plan = p
    c.extra['mode'] = 'reconstruir'
    lost = os.path.getsize(c.path) - p.data_end
    extra = Join(
        L('diag.rebuild_gaps', n=len(p.gaps), mb=f'{p.gap_bytes / 1e6:.1f}') if p.gaps else '',
        L('diag.rebuild_lost', mb=f'{lost / 1e6:.1f}') if lost > 1e6 and getattr(p.reason, 'key', '') != 'reason.end'
        else '')
    c.detail = L('diag.rebuild', secs=f'{p.seconds:.1f}', chunks=len(p.chunks), extra=extra, reason=p.reason)
    return p


def set_extract(c: Clip, refs: list[str], why=''):
    c.extra['mode'] = 'extrair'
    c.extra['refs'] = list(refs)
    c.reference = refs[0] if refs else ''
    c.detail = Join(why, L('diag.extract'), sep=' — ')


def _assign_references(clips: list[Clip], progress=None, extra_refs: list[Clip] | None = None):
    refs = [c for c in clips if _is_reference(c)] + list(extra_refs or [])
    todo = [c for c in clips if c.status in REPAIRABLE]
    for i, c in enumerate(todo):
        pool = _ref_candidates(c, refs)
        if c.status == HEADER:
            if pool:
                c.reference = pool[0].path
                c.extra['mode'] = 'cabecalho'
            else:
                c.note(L('diag.no_ref'))
            continue
        try:
            plan_rebuild(c, [r.path for r in pool[:40]])
        except Exception as e:
            set_extract(c, [r.path for r in pool[:40]], L('diag.rebuild_failed', err=msg(e)))
        if progress:
            progress(i + 1, len(todo), c)


def _quick_ref(path: str) -> Clip | None:
    """Checagem leve de um possível clipe de referência (cabeçalho + moov)."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            h = f.read(HDR)
        if h[4:8] != b'ftyp' or h[MDAT_POS + 4:MDAT_POS + 8] != b'mdat':
            return None
        if find_moov(path) is None:
            return None
        c = Clip(path, size, OK)
        c.__dict__.update(sony_xml(path))
        return c
    except Exception:
        return None


def scan_files(paths: list[str], progress=None, max_refs: int = 30) -> list[Clip]:
    """Analisa só os arquivos escolhidos. Referências são buscadas nas mesmas
    pastas (clipes vizinhos pelo nome, até max_refs por pasta), sem listá-las."""
    paths = [os.path.abspath(p) for p in paths]
    clips = []
    for i, p in enumerate(paths):
        try:
            clips.append(diagnose(p))
        except Exception as e:
            clips.append(Clip(p, os.path.getsize(p), UNKNOWN, L('diag.error', err=e)))
        if progress:
            progress(i + 1, len(paths), clips[-1])
    chosen = set(paths)
    refs = []
    for d in sorted({os.path.dirname(p) for p in paths}):
        sibs = sorted(os.path.join(d, f) for f in os.listdir(d)
                      if f.lower().endswith(VIDEO_EXT) and not f.startswith('._'))
        sibs = [s for s in sibs if s not in chosen]
        targets = [p for p in paths if os.path.dirname(p) == d]
        idx = {p: i for i, p in enumerate(sorted(sibs + targets))}

        def dist(s):  # vizinhos mais próximos (pelo nome) dos arquivos escolhidos primeiro
            return min(abs(idx[s] - idx[t]) for t in targets)

        found = 0
        for s in sorted(sibs, key=dist):
            r = _quick_ref(s)
            if r:
                refs.append(r)
                found += 1
                if found >= max_refs:
                    break
    _assign_references(clips, progress, refs)
    return clips


# ---------------------------------------------------------------- reparo

def repair(c: Clip, out_dir: str, chunk_cb=None) -> str:
    if c.status not in REPAIRABLE:
        raise ValueError(L('err.not_repairable', name=c.name, status=c.status))
    os.makedirs(out_dir, exist_ok=True)
    stem, ext = os.path.splitext(c.name)
    mode = c.extra.get('mode', 'cabecalho' if c.status == HEADER else 'reconstruir')
    if mode == 'extrair':
        from . import rebuild as rb
        prof = None
        for r in c.extra.get('refs') or ([c.reference] if c.reference else []):
            try:
                prof = rb.load_profile(r, find_moov)
                break
            except Exception:
                continue
        res = rb.extract_raw(c.path, out_dir, getattr(c, '_plan', None),
                             prof, chunk_cb)
        c.output = res['mov'] or res['h264'] or ''
        if not res['frames']:
            c.verify = L('verify.nothing')
            return c.output
        c.verify = Join(L('verify.extracted', n=res['frames']),
                        L('verify.extracted_audio', s=f"{res['audio_s']:.1f}") if res['wav']
                        else L('verify.extracted_noaudio'))
        return c.output
    if not c.reference:
        raise ValueError(L('err.no_ref', name=c.name))
    if mode == 'reconstruir':
        from . import rebuild as rb
        p = getattr(c, '_plan', None) or plan_rebuild(c, [c.reference])
        dst = os.path.join(out_dir, f'{stem}_RECONSTRUIDO{ext}')
        rb.write(c.path, p, dst, chunk_cb)
        c.output = dst
        return dst
    with open(c.reference, 'rb') as f:
        hdr = bytearray(f.read(HDR))
    hdr[HDR - 8:HDR] = struct.pack('>Q', c.moov_pos - MDAT_POS)
    os.makedirs(out_dir, exist_ok=True)
    stem, ext = os.path.splitext(c.name)
    dst = os.path.join(out_dir, f'{stem}_REPARADO{ext}')
    copied = 0
    with open(c.path, 'rb') as src, open(dst, 'wb') as out:
        src.seek(HDR)
        out.write(hdr)
        while True:
            b = src.read(16 * 1024 * 1024)
            if not b:
                break
            out.write(b)
            copied += len(b)
            if chunk_cb:
                chunk_cb(copied, c.size)
    for ext_xml in ('M01.XML',):
        xml = os.path.splitext(c.path)[0] + ext_xml
        if os.path.exists(xml):
            shutil.copy2(xml, os.path.join(out_dir, f'{stem}_REPARADO{ext_xml}'))
    c.output = dst
    return dst


def run_quiet(cmd: list[str], **kw):
    """subprocess.run sem abrir janela de console no Windows."""
    if sys.platform == 'win32':
        kw.setdefault('creationflags', 0x08000000)  # CREATE_NO_WINDOW
    return subprocess.run(cmd, **kw)


def ffmpeg_bin(name='ffmpeg'):
    """Caminho do ffmpeg/ffprobe: PATH e os locais de instalação mais comuns (macOS e Windows)."""
    cands = [shutil.which(name)]
    if sys.platform == 'win32':
        exe = name + '.exe'
        local = os.environ.get('LOCALAPPDATA', '')
        cands += [os.path.join(p, exe) for p in (
            r'C:\ffmpeg\bin',
            os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'ffmpeg', 'bin'),
            r'C:\ProgramData\chocolatey\bin',
            os.path.join(local, 'Microsoft', 'WinGet', 'Links'),
            os.path.join(os.path.expanduser('~'), 'scoop', 'shims'),
        )]
    else:
        cands += [f'/opt/homebrew/bin/{name}', f'/usr/local/bin/{name}', f'/usr/bin/{name}']
    for p in cands:
        if p and os.path.exists(p):
            return p
    return None


def verify(c: Clip) -> str:
    if c.extra.get('mode') == 'extrair':
        return c.verify
    ff = ffmpeg_bin()
    if not ff or not c.output:
        c.verify = L('verify.no_ffmpeg')
        return c.verify
    fp = ffmpeg_bin('ffprobe')
    if not fp:
        c.verify = L('verify.no_ffprobe')
        return c.verify
    r = run_quiet([fp, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                        '-show_entries', 'stream=nb_frames,nb_read_frames', '-of', 'csv=p=0',
                        c.output], capture_output=True, text=True)
    try:
        total, read = (int(x) for x in r.stdout.strip().split(',')[:2])
        lost = total - read
        c.verify = Join(L('verify.frames', read=read, total=total), L('verify.lost', n=lost) if lost else '')
    except ValueError:
        c.verify = L('verify.fail')
    if c.audio_db is not None:
        c.verify = Join(c.verify, L('verify.audio_peak', db=c.audio_db))
    return c.verify


def write_report(clips: list[Clip], path: str):
    """Relatório CSV no idioma atual. A coluna `status` mantém o código fixo (para scripts);
    `status_label` traz o nome traduzido."""
    cols = ['name', 'status', 'status_label', 'action', 'detail', 'camera', 'codec', 'fps', 'size',
            'resolution', 'video', 'duration', 'bitrate', 'color', 'audio', 'timecode', 'created',
            'audio_db', 'reference', 'output', 'verify', 'path']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([tr(f'csv.{k}') for k in cols])
        for c in clips:
            mode = c.extra.get('mode') if c.status in REPAIRABLE else None
            row = {
                'name': c.name, 'status': c.status, 'status_label': tr(f'status.{c.status}'),
                'action': tr(f'mode.{mode}') if mode else '', 'detail': str(c.detail),
                'camera': c.camera, 'codec': c.codec, 'fps': c.fps, 'size': c.size,
                'audio_db': '' if c.audio_db is None else c.audio_db,
                'reference': os.path.basename(c.reference), 'output': c.output,
                'verify': str(c.verify), 'path': c.path,
            }
            m = c.extra.get('meta') or {}
            row.update({
                'resolution': f"{m['width']}x{m['height']}" if m.get('width') else '',
                'video': ' '.join(str(x) for x in (m.get('profile'), m.get('chroma'),
                                                   m.get('bit_depth') and f"{m['bit_depth']}bit") if x),
                'duration': f"{m['duration']:.2f}" if m.get('duration') else '',
                'bitrate': f"{(m.get('video_bitrate') or m['bitrate']) / 1e6:.1f}" if m.get('bitrate') else '',
                'color': ' / '.join(x for x in (m.get('xml_gamma'), m.get('xml_primaries')) if x),
                'audio': f"{m.get('audio_codec')} {m.get('audio_channels')}ch {m.get('audio_rate')}Hz "
                         f"{m.get('audio_bits')}bit" if m.get('audio_codec') else '',
                'timecode': m.get('xml_timecode', ''), 'created': m.get('xml_created', ''),
            })
            if not row['fps'] and m.get('fps'):
                row['fps'] = _fps_label(m['fps'])
            w.writerow([row[k] for k in cols])
