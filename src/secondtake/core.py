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
from dataclasses import dataclass, field, asdict

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
    stem = os.path.splitext(path)[0]
    for cand in (stem + 'M01.XML', stem + 'M01.xml'):
        if os.path.exists(cand):
            try:
                import xml.etree.ElementTree as ET
                r = ET.parse(cand).getroot()
                ns = {'n': r.tag.split('}')[0][1:]}
                vf = r.find('.//n:VideoFrame', ns)
                dev = r.find('n:Device', ns)
                return {
                    'codec': vf.get('videoCodec', '') if vf is not None else '',
                    'fps': vf.get('captureFps', '') if vf is not None else '',
                    'camera': dev.get('modelName', '') if dev is not None else '',
                }
            except Exception:
                pass
    return {}


# ---------------------------------------------------------------- diagnóstico

@dataclass
class Clip:
    path: str
    size: int
    status: str = UNKNOWN
    detail: str = ''
    camera: str = ''
    codec: str = ''
    fps: str = ''
    moov_pos: int = -1
    audio_db: float | None = None
    reference: str = ''
    output: str = ''
    verify: str = ''
    extra: dict = field(default_factory=dict)

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
    c.__dict__.update({k: v for k, v in sony_xml(path).items()})
    if c.size == 0:
        c.status, c.detail = EMPTY, 'arquivo com 0 bytes'
        return c
    with open(path, 'rb') as f:
        head = f.read(HDR)
    if head.count(0) == len(head):
        c.status, c.detail = EMPTY, 'início zerado'
    m = find_moov(path)
    if m is None:
        c.status = NO_MOOV
        c.detail = 'índice (moov) ausente — gravação interrompida'
        return c
    c.moov_pos, moov = m
    try:
        tracks = parse_moov(moov)
    except Exception as e:
        c.status, c.detail = UNKNOWN, f'moov ilegível: {e}'
        return c
    c.extra['tracks'] = ','.join(f'{t.handler}:{t.fmt}' for t in tracks)
    if any(o >= c.moov_pos for t in tracks for o in t.offsets):
        c.status, c.detail = UNKNOWN, 'moov aponta para fora dos dados'
        return c
    c.audio_db = _audio_peak_db(path, tracks)
    good_head = head[4:8] == b'ftyp' and head[MDAT_POS + 4:MDAT_POS + 8] == b'mdat'
    if good_head:
        c.status, c.detail = OK, ''
    elif c.status != EMPTY:
        c.status = HEADER
        c.detail = 'cabeçalho corrompido, moov íntegro — reparável'
    else:
        c.status = HEADER
        c.detail = 'início zerado, moov íntegro — reparável'
    if c.audio_db is not None and c.audio_db < -75:
        c.detail = (c.detail + '; ' if c.detail else '') + 'áudio em silêncio na gravação original'
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
            clips.append(Clip(p, os.path.getsize(p), UNKNOWN, f'erro: {e}'))
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
    msg = f'reconstrução: {p.seconds:.1f} s recuperáveis ({len(p.chunks)} blocos'
    if p.gaps:
        msg += f'; {len(p.gaps)} trecho(s) danificado(s) pulado(s), {p.gap_bytes / 1e6:.1f} MB'
    if lost > 1e6 and p.reason != 'fim da gravação':
        msg += f'; {lost / 1e6:.1f} MB finais descartados'
    c.detail = msg + f') — {p.reason}'
    return p


def set_extract(c: Clip, refs: list[str], why: str = ''):
    c.extra['mode'] = 'extrair'
    c.extra['refs'] = list(refs)
    c.reference = refs[0] if refs else ''
    c.detail = (why + ' — ' if why else '') + 'extração bruta: vídeo .h264 (+ MP4 limpo)'


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
                c.detail += '; sem clipe de referência válido na pasta'
            continue
        try:
            plan_rebuild(c, [r.path for r in pool[:40]])
        except Exception as e:
            set_extract(c, [r.path for r in pool[:40]], f'reconstrução falhou ({e})')
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
            clips.append(Clip(p, os.path.getsize(p), UNKNOWN, f'erro: {e}'))
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
        raise ValueError(f'{c.name}: status {c.status} não é reparável')
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
            c.verify = 'nenhum quadro H.264 válido encontrado — sem recuperação possível'
            return c.output
        c.verify = f"{res['frames']} quadros extraídos" + (f", {res['audio_s']:.1f} s de áudio" if res['wav'] else ', sem áudio')
        return c.output
    if not c.reference:
        raise ValueError(f'{c.name}: sem referência')
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


def ffmpeg_bin(name='ffmpeg'):
    for p in (shutil.which(name), f'/opt/homebrew/bin/{name}', f'/usr/local/bin/{name}'):
        if p and os.path.exists(p):
            return p
    return None


def verify(c: Clip) -> str:
    if c.extra.get('mode') == 'extrair':
        return c.verify
    ff = ffmpeg_bin()
    if not ff or not c.output:
        c.verify = 'ffmpeg não encontrado — verificação pulada'
        return c.verify
    fp = ffmpeg_bin('ffprobe')
    if not fp:
        c.verify = 'ffprobe não encontrado — verificação pulada'
        return c.verify
    r = subprocess.run([fp, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                        '-show_entries', 'stream=nb_frames,nb_read_frames', '-of', 'csv=p=0',
                        c.output], capture_output=True, text=True)
    try:
        total, read = (int(x) for x in r.stdout.strip().split(',')[:2])
        lost = total - read
        c.verify = f'{read}/{total} quadros decodificados' + (f' ({lost} perdidos no início)' if lost else '')
    except ValueError:
        c.verify = 'falha ao decodificar o vídeo'
    if c.audio_db is not None:
        c.verify += f' · áudio pico {c.audio_db} dBFS'
    return c.verify


def write_report(clips: list[Clip], path: str):
    cols = ['name', 'status', 'detail', 'camera', 'codec', 'fps', 'size',
            'audio_db', 'reference', 'output', 'verify', 'path']
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(cols)
        for c in clips:
            d = asdict(c)
            d['name'] = c.name
            d['reference'] = os.path.basename(c.reference)
            w.writerow([d.get(k, '') for k in cols])
