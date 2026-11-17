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
import logging
import math
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field

from .i18n import L, Join, msg, t as tr
from .safeio import atomic_write, copy_times, unique_stem

log = logging.getLogger('secondtake')

HDR = 262144                 # cabeçalho da FX3 (ftyp + uuid + free + mdat): dados em 0x40000.
                             # A FX30 começa os dados em 131072 (0x20000). O app não depende
                             # disso: lê a estrutura (parse_layout) e o 1º offset do moov.
LAYOUT_MAX = 64 << 20        # o mdat tem de começar antes disto (cabeçalho Sony: 128 a 256 KiB)
TAIL_SCAN = 16_000_000       # bytes lidos do fim procurando o moov
VIDEO_EXT = ('.mp4', '.mov', '.rsv')   # .RSV: arquivo temporário da Sony (gravação interrompida)


def out_ext(name: str) -> str:
    """Extensão do arquivo recuperado: .RSV vira .MP4 (é um XAVC S sem índice)."""
    ext = os.path.splitext(name)[1]
    return '.MP4' if ext.lower() == '.rsv' else ext

OK = 'OK'
HEADER = 'CABECALHO_DANIFICADO'
NO_MOOV = 'SEM_MOOV'
EMPTY = 'VAZIO'
UNKNOWN = 'DESCONHECIDO'

REPAIRABLE = {HEADER, NO_MOOV, UNKNOWN}

# saída apontando para a pasta dos originais: os resultados vão para esta subpasta,
# que a análise de pasta ignora (para não listar os recuperados junto com os originais)
SAME_FOLDER_DIR = 'Second Take'


def same_dir(a: str, b: str) -> bool:
    """Mesma pasta? Usa samefile (pega maiúsculas/minúsculas no APFS/NTFS e links)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def is_inside(child: str, parent: str) -> bool:
    """`child` é subpasta de `parent` (não a mesma)? Compara caminhos reais, sem
    diferença de maiúsculas/minúsculas no Mac/Windows e sem se enganar com /a/bc × /a/b."""
    if same_dir(child, parent):
        return False
    c = os.path.normcase(os.path.realpath(os.path.abspath(child)))
    p = os.path.normcase(os.path.realpath(os.path.abspath(parent)))
    try:
        return os.path.commonpath([c, p]) == p
    except ValueError:                          # discos diferentes no Windows
        return False


def out_inside_source(out_dir: str, src_dirs) -> bool:
    """Saída dentro de uma pasta de origem (fora a subpasta Second Take, que é a nossa)?"""
    if os.path.basename(os.path.normpath(out_dir)) == SAME_FOLDER_DIR:
        return False
    return any(is_inside(out_dir, d) for d in src_dirs)


class Cancelled(Exception):
    """O usuário cancelou o lote. O clipe em andamento é descartado (o .part é apagado)."""


def clip_out_dir(src_path: str, out_dir: str) -> str:
    """Pasta onde o recuperado de `src_path` é gravado: `out_dir`, ou `out_dir/Second Take`
    quando `out_dir` é a própria pasta do original."""
    if same_dir(os.path.dirname(os.path.abspath(src_path)), out_dir):
        return os.path.join(out_dir, SAME_FOLDER_DIR)
    return out_dir


def beside_dir(src_path: str) -> str:
    """Modo "Salvar junto dos originais": `<pasta do clipe>/Second Take`."""
    return os.path.join(os.path.dirname(os.path.abspath(src_path)), SAME_FOLDER_DIR)


def group_by_folder(clips: list) -> dict[str, list]:
    """{pasta do original: [clipes]} na ordem em que aparecem."""
    groups: dict[str, list] = {}
    for c in clips:
        groups.setdefault(os.path.dirname(os.path.abspath(c.path)), []).append(c)
    return groups


def report_dir(out_dir: str, clips: list) -> str:
    """Pasta do relatório CSV: junto dos recuperados."""
    for c in clips:
        if same_dir(os.path.dirname(os.path.abspath(c.path)), out_dir):
            return os.path.join(out_dir, SAME_FOLDER_DIR)
    return out_dir


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


TAIL_FIRST = 1 << 20         # 1ª leitura: 1 MB do fim (o moov da Sony tem poucas centenas de KB)


def _find_moov_in(f, size: int, window: int) -> tuple[int, bytes] | None:
    base = max(0, size - window)
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


def parse_layout(path: str) -> dict | None:
    """Estrutura do início do arquivo: `ftyp` na posição 0 e boxes encadeadas, com tamanhos
    válidos, até o `mdat`. Retorna {'mdat_pos', 'mdat_hdr' (8 ou 16), 'data_start', 'mdat_end'}
    ou None se o cabeçalho não fecha. Não supõe posição fixa: vale para FX3 (dados em 262144),
    FX30 (131072) e qualquer layout íntegro."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            p = 0
            for _ in range(64):
                f.seek(p)
                h = f.read(16)
                if len(h) < 8:
                    return None
                sz, typ = struct.unpack('>I4s', h[:8])
                hl = 8
                if sz == 1:
                    if len(h) < 16:
                        return None
                    sz, hl = struct.unpack('>Q', h[8:16])[0], 16
                elif sz == 0:
                    sz = size - p
                if (p == 0 and typ != b'ftyp') or not all(32 <= b < 127 for b in typ) \
                        or sz < hl or p + sz > size:
                    return None
                if typ == b'mdat':
                    return {'mdat_pos': p, 'mdat_hdr': hl, 'data_start': p + hl, 'mdat_end': p + sz}
                p += sz
                if p > LAYOUT_MAX:
                    return None
    except OSError:
        return None
    return None


def first_offset(tracks: list['Track']) -> int | None:
    """Início dos dados segundo o índice: o menor offset de amostra entre as trilhas.
    Funciona com o cabeçalho destruído, porque vem do moov."""
    offs = [o for t in tracks for o in t.offsets]
    return min(offs) if offs else None


def find_moov(path: str) -> tuple[int, bytes] | None:
    """Procura o moov no fim do arquivo: primeiro em 1 MB e, só se não achar
    (moov grande ou zeros de preenchimento depois dele), em TAIL_SCAN (16 MB)."""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        m = _find_moov_in(f, size, TAIL_FIRST)
        if m is None and size > TAIL_FIRST:
            m = _find_moov_in(f, size, TAIL_SCAN)
    return m


# ---------------------------------------------------------------- Sony XML

def sony_xml(path: str) -> dict:
    """Metadados do XML que as Sony gravam ao lado do clipe (C0001M01.XML)."""
    stem = os.path.splitext(path)[0]
    for cand in (stem + 'M01.XML', stem + 'M01.xml'):
        if not os.path.exists(cand):
            continue
        try:
            from defusedxml import ElementTree as ET   # recusa entidades (XXE, "billion laughs")
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
                'width': g('.//n:VideoLayout', 'pixel'),
                'height': g('.//n:VideoLayout', 'numOfVerticalLine'),
                'audio_ch': g('.//n:AudioFormat', 'numOfChannel'),
                'lens': g('.//n:Lens', 'modelName') or item('LensModelName'),
            }
            if tc is not None and tc.get('value') and len(tc.get('value')) == 8:
                # LTC em BCD, bytes invertidos (FF SS MM HH) com bits de flag misturados
                b = bytes.fromhex(tc.get('value'))
                ff, ss, mm, hh = b[0] & 0x3F, b[1] & 0x7F, b[2] & 0x7F, b[3] & 0x3F
                # bit 6 do byte de quadros = drop-frame (só existe em 29,97/59,94: tcFps 30/60)
                df = bool(b[0] & 0x40) and tc_fps in ('30', '60')
                out['timecode'] = f'{hh:02x}:{mm:02x}:{ss:02x}{";" if df else ":"}{ff:02x}'
            if tc_fps:
                out['tc_fps'] = tc_fps
            return {k: v for k, v in out.items() if v and v != '4294967295'}
        except ImportError:
            raise
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


HEVC_PROFILES = {1: 'Main', 2: 'Main 10', 3: 'Main Still Picture', 4: 'RExt', 9: 'SCC'}


def _hevc_info(stsd: bytes) -> dict:
    """Perfil, nível, amostragem de cor e bit depth do hvcC (ISO/IEC 14496-15, 8.3.3.1).
    No perfil RExt (4) o nome sai da combinação de chroma e bit depth, como a Sony
    descreve o XAVC HS: Main 4:2:2 10."""
    i = stsd.find(b'hvcC')
    if i < 0 or len(stsd) < i + 4 + 22:
        return {}
    a = i + 4
    prof = stsd[a + 1] & 0x1F
    level = stsd[a + 12]
    chroma = stsd[a + 16] & 0x03
    depth = (stsd[a + 17] & 0x07) + 8
    ch = {0: '4:0:0', 1: '4:2:0', 2: '4:2:2', 3: '4:4:4'}[chroma]
    name = HEVC_PROFILES.get(prof, str(prof))
    if prof == 4:
        name = f"Main {ch}{'' if depth == 8 else f' {depth}'}" if chroma else f'Monochrome {depth}'
    lv = level / 30
    return {'profile': name, 'level': f'{lv:.1f}'.rstrip('0').rstrip('.') if lv % 1 else f'{int(lv)}',
            'chroma': ch, 'bit_depth': depth}


CODEC_LABEL = {'avc1': 'H.264', 'avc3': 'H.264', 'hvc1': 'H.265', 'hev1': 'H.265'}


def video_family(m: dict) -> str:
    """'avc' ou 'hevc' pelos metadados do clipe: o moov manda; sem moov, o conteúdo do mdat
    (rebuild.sniff_video_family) e, por último, o codec do XML da Sony."""
    vc = m.get('video_codec', '')
    if vc in ('hvc1', 'hev1'):
        return 'hevc'
    if vc in ('avc1', 'avc3'):
        return 'avc'
    if m.get('stream_family'):
        return m['stream_family']
    x = str(m.get('xml_codec', '')).upper()
    if 'HEVC' in x or 'H265' in x:
        return 'hevc'
    if 'AVC' in x or 'H264' in x:
        return 'avc'
    return ''


def is_hevc(c: 'Clip') -> bool:
    return video_family(c.extra.get('meta') or {}) == 'hevc'


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
                meta.update(_avc_info(sd) or _hevc_info(sd))
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


def fmt_size(n) -> str:
    """Tamanho de arquivo em unidades decimais (1 MB = 1.000.000 B), como o Finder e os
    cartões de memória. Única função de tamanho do app: tabela, Detalhes, avisos de espaço."""
    n = float(n or 0)
    if n < 1000:
        return f'{int(n)} B'
    for u in ('KB', 'MB', 'GB', 'TB'):
        n /= 1000
        if n < 1000 or u == 'TB':
            return f'{n:.2f} {u}' if u in ('GB', 'TB') else f'{n:.1f} {u}'
    return ''


def diag_text(c: 'Clip') -> str:
    """Diagnóstico completo do clipe, no idioma atual: resultado da verificação (se houver)
    + diagnóstico. É o mesmo texto da coluna da tabela, sem o corte da borda."""
    verify, detail = str(c.verify or ''), str(c.detail or '')
    if verify and detail and not verify.startswith(('ERRO', 'ERROR')):
        return f'{verify} · {detail}'
    return verify or detail or '—'


def meta_rows(c: 'Clip') -> list[tuple[str, str]]:
    """Linhas (rótulo, valor) prontas para exibir, já no idioma atual."""
    m = c.extra.get('meta') or {}
    rows = []

    def add(key, val):
        if val not in (None, '', '—'):
            rows.append((tr(key), str(val)))

    add('meta.diagnosis', diag_text(c))          # texto inteiro (na tabela a coluna corta)
    add('meta.camera', ' · '.join(x for x in (m.get('xml_camera'), m.get('xml_serial') and f"S/N {m['xml_serial']}")
                                  if x))
    add('meta.created', (m.get('xml_created') or '').replace('T', ' ')[:19])
    add('meta.timecode', m.get('xml_timecode'))
    add('meta.lens', m.get('xml_lens'))
    if m.get('width'):
        add('meta.resolution', f"{m['width']} × {m['height']}")
    codec = ' '.join(x for x in (CODEC_LABEL.get(m.get('video_codec'), m.get('video_codec')),
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
    add('meta.size', fmt_size(m.get('file_size', c.size)))
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


def _sniff(c: Clip):
    """Sem índice utilizável, o codec vem do conteúdo (H.264 × H.265), antes de escolher o modo."""
    from . import rebuild as rb
    fam = rb.sniff_video_family(c.path)
    if fam:
        c.extra.setdefault('meta', {})['stream_family'] = fam


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
        if path.lower().endswith('.rsv'):
            c.detail = L('diag.rsv')
        c.extra['meta'] = read_meta(path, b'')
        _sniff(c)
        return c
    c.moov_pos, moov = m
    c.extra['meta'] = read_meta(path, moov)
    try:
        tracks = parse_moov(moov)
    except Exception as e:
        c.status, c.detail = UNKNOWN, L('diag.bad_moov', err=e)
        _sniff(c)
        return c
    c.extra['tracks'] = ','.join(f'{t.handler}:{t.fmt}' for t in tracks)
    if any(o >= c.moov_pos for t in tracks for o in t.offsets):
        c.status, c.detail = UNKNOWN, L('diag.moov_out')
        _sniff(c)
        return c
    c.audio_db = _audio_peak_db(path, tracks)
    start = first_offset(tracks)
    if start is not None:
        c.extra['data_start'] = start
    lay = parse_layout(path)
    # cabeçalho íntegro = estrutura fecha até o mdat, e o índice aponta para dentro dele
    good_head = lay is not None and start is not None and lay['data_start'] <= start \
        and all(o < lay['mdat_end'] for t in tracks for o in t.offsets)
    if good_head:
        c.extra['layout'] = lay
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


def repairable(c: Clip) -> bool:
    """Pode entrar num lote de reparo: tem referência, ou é extração bruta (que funciona
    sem referência: varre o arquivo atrás de NALs H.264). H.265 sem índice nunca entra."""
    if c.status not in REPAIRABLE:
        return False
    if c.reference:
        return True
    return c.extra.get('mode') == 'extrair' and not is_hevc(c)


def auto_selected(c: Clip) -> bool:
    """Já vem selecionado depois da análise: troca de cabeçalho ou reconstrução. A extração
    bruta (último recurso) só entra se o usuário selecionar a linha."""
    return repairable(c) and bool(c.reference) and c.extra.get('mode') != 'extrair'


def scan(folder: str, progress=None) -> list[Clip]:
    files = []
    for dp, dn, fn in os.walk(folder):
        dn[:] = [d for d in dn if not d.startswith('.') and d.upper() != 'SUB' and d != SAME_FOLDER_DIR]
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
    """Clipe íntegro que serve de molde: estrutura válida e dados começando logo após o mdat,
    como a Sony grava (o layout exato vem de parse_layout, não de uma posição fixa)."""
    if c.status != OK:
        return False
    lay = c.extra.get('layout') or parse_layout(c.path)
    return lay is not None and c.extra.get('data_start', lay['data_start']) == lay['data_start']


def ref_data_start(r: Clip) -> int | None:
    lay = r.extra.get('layout') or parse_layout(r.path)
    return lay['data_start'] if lay else None


def _fps_key(v) -> str:
    """'23.98p' / '23.976' / 23.976 -> '23.98'; '50i' -> '50i'."""
    if v in (None, ''):
        return ''
    txt = str(v).strip().lower()
    inter = txt.endswith('i')
    try:
        num = float(txt.rstrip('pi'))
    except ValueError:
        return txt
    return f'{num:.2f}'.rstrip('0').rstrip('.') + ('i' if inter else '')


SIG_KEYS = ('vcodec', 'camera', 'codec', 'format_fps', 'capture_fps', 'width', 'height', 'audio_ch', 'audio_bits')


def signature(c: Clip) -> dict:
    """Assinatura técnica do clipe para escolher a referência: modelo da câmera, codec XAVC,
    fps de gravação e de captura (S&Q), resolução e áudio. Vem do XML da Sony
    (C0001M01.XML) e, onde faltar, do índice (moov) do próprio arquivo."""
    m = c.extra.get('meta') or {}
    xml = {k[4:]: v for k, v in m.items() if k.startswith('xml_')}
    if not xml:                                   # referências rápidas guardam o XML nos atributos
        xml = {k: getattr(c, k) for k in ('camera', 'codec', 'fps') if getattr(c, k, '')}
    sig = {
        'vcodec': video_family(m),
        'camera': xml.get('camera', '') or c.camera,
        'codec': xml.get('codec', '') or c.codec,
        'format_fps': _fps_key(xml.get('format_fps') or m.get('fps')),
        'capture_fps': _fps_key(xml.get('fps') or c.fps),
        'width': str(xml.get('width') or m.get('width') or ''),
        'height': str(xml.get('height') or m.get('height') or ''),
        'audio_ch': str(xml.get('audio_ch') or m.get('audio_channels') or ''),
        'audio_bits': str(m.get('audio_bits') or ''),
    }
    return {k: v for k, v in sig.items() if v}


def match_score(a: dict, b: dict) -> tuple[int, int]:
    """(divergências, coincidências) entre duas assinaturas, só nos campos que as duas têm."""
    common = [k for k in SIG_KEYS if k in a and k in b]
    diff = sum(1 for k in common if a[k] != b[k])
    return diff, len(common) - diff


def _ref_candidates(c: Clip, refs: list[Clip]) -> list[Clip]:
    """Referências em ordem de preferência: assinatura idêntica primeiro (menos divergências,
    mais coincidências), depois mesma pasta e maior arquivo (mais keyframes para o GOP).
    Ninguém é descartado: a reconstrução ainda testa as outras se a primeira não servir."""
    same_dir = lambda r: os.path.dirname(r.path) == os.path.dirname(c.path)  # noqa: E731
    sig = signature(c)
    scored = []
    for r in refs:
        diff, hits = match_score(sig, signature(r))
        scored.append(((diff, -hits, not same_dir(r), -r.size), r))
    scored.sort(key=lambda x: x[0])
    return [r for _, r in scored]


def plan_rebuild(c: Clip, refs: list[str], data_start: int | None = None):
    """Simula a reconstrução (modo avançado) sem gravar nada."""
    from . import rebuild as rb
    if is_hevc(c):
        raise ValueError(L('err.hevc_rebuild'))
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
            # só serve referência com o mesmo início de dados (FX3 256 KiB × FX30 128 KiB):
            # o cabeçalho copiado tem de terminar exatamente onde os dados do clipe começam
            start = c.extra.get('data_start')
            same = [r for r in pool if ref_data_start(r) == start]
            if same:
                c.reference = same[0].path
                c.extra['mode'] = 'cabecalho'
            elif pool:
                c.note(L('diag.no_ref_layout', start=start))
            else:
                c.note(L('diag.no_ref'))
            continue
        if is_hevc(c):
            # XAVC HS sem índice: a varredura de blocos e a extração bruta só entendem H.264.
            # Fica sem referência (não entra no lote) e com o motivo no diagnóstico.
            c.note(L('diag.hevc_rebuild'))
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
        lay = parse_layout(path)
        if lay is None:
            return None
        m = find_moov(path)
        if m is None or m[0] < lay['data_start']:
            return None
        c = Clip(path, size, OK)
        c.extra['layout'] = lay
        xml = sony_xml(path)
        c.camera, c.codec, c.fps = xml.get('camera', ''), xml.get('codec', ''), xml.get('fps', '')
        c.extra['meta'] = read_meta(path, m[1])
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

def out_name(stem: str, tag: str, ext: str, keep_names: bool = False) -> str:
    """Nome do arquivo de saída: `C0001_REPARADO.MP4` ou, com keep_names, `C0001.MP4`
    (mesmo nome do original, para o relink no Resolve/Premiere)."""
    return f'{stem}{ext}' if keep_names else f'{stem}_{tag}{ext}'


def has_sidecar(src_path: str) -> bool:
    base = os.path.splitext(src_path)[0]
    return any(os.path.isfile(base + suf) for suf in ('M01.XML', 'M01.xml'))


def out_base(src_path: str, out_dir: str, stem: str, tag: str, ext: str, keep_names: bool) -> str:
    """Caminho da saída sem extensão: `C0001_REPARADO` (ou `C0001` com keep_names), numerado
    `_2`, `_3`… se já existir. O XML de câmera reserva o mesmo número, para clipe e XML casarem.
    Nunca sobrescreve; e o original nunca está em out_dir (clip_out_dir manda para a subpasta
    Second Take), então manter o nome é seguro mesmo saindo "na pasta dos originais"."""
    endings = [ext, 'M01.XML'] if has_sidecar(src_path) else [ext]
    return unique_stem(out_dir, stem if keep_names else f'{stem}_{tag}', endings)


def copy_sidecar(src_path: str, out_dir: str, out_stem: str) -> str | None:
    """Copia o XML de câmera da Sony (C0001M01.XML) para junto da saída, com o nome casando
    com o clipe novo, para o Resolve/Catalyst lerem timecode, modelo e espaço de cor."""
    base = os.path.splitext(src_path)[0]
    for suf in ('M01.XML', 'M01.xml'):
        xml = base + suf
        if os.path.isfile(xml):
            dst = os.path.join(out_dir, out_stem + 'M01.XML')
            shutil.copy2(xml, dst)
            return dst
    return None


def repair(c: Clip, out_dir: str, chunk_cb=None, keep_names: bool = False) -> str:
    if c.status not in REPAIRABLE:
        raise ValueError(L('err.not_repairable', name=c.name, status=c.status))
    out_dir = clip_out_dir(c.path, out_dir)      # nunca grava ao lado do original
    os.makedirs(out_dir, exist_ok=True)
    st = os.stat(c.path)                         # datas do original, antes de qualquer leitura
    stem, ext = os.path.splitext(c.name)
    mode = c.extra.get('mode', 'cabecalho' if c.status == HEADER else 'reconstruir')
    if mode != 'cabecalho' and is_hevc(c):
        raise ValueError(L('err.hevc_rebuild'))
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
                             prof, chunk_cb, timecode=sony_xml(c.path).get('timecode', ''))
        copy_times(st, res['mov'], res['h264'], res['wav'])
        c.output = res['mov'] or res['h264'] or ''
        if not res['frames']:
            c.verify = L('verify.nothing')
            return c.output
        c.verify = Join(L('verify.extracted', n=res['frames']),
                        L('verify.extracted_audio', s=f"{res['audio_s']:.1f}") if res['wav']
                        else L('verify.extracted_noaudio'),
                        L('verify.extracted_tc', tc=res['timecode']) if res.get('timecode') else '')
        return c.output
    if not c.reference:
        raise ValueError(L('err.no_ref', name=c.name))
    if mode == 'reconstruir':
        from . import rebuild as rb
        p = getattr(c, '_plan', None) or plan_rebuild(c, [c.reference])
        oext = out_ext(c.name)
        dst = out_base(c.path, out_dir, stem, 'RECONSTRUIDO', oext, keep_names) + oext
        rb.write(c.path, p, dst, chunk_cb)
        copy_sidecar(c.path, out_dir, os.path.splitext(os.path.basename(dst))[0])
        copy_times(st, dst)
        c.output = dst
        return dst
    start = c.extra.get('data_start')
    if start is None:
        m = find_moov(c.path)
        start = first_offset(parse_moov(m[1])) if m else None
    lay = parse_layout(c.reference)
    if start is None or lay is None or lay['data_start'] != start:
        # nunca colar o cabeçalho de um layout diferente: apagaria o começo dos dados
        raise ValueError(L('err.ref_layout_mismatch', ref=os.path.basename(c.reference),
                           a=lay['data_start'] if lay else '?', b=start if start is not None else '?'))
    with open(c.reference, 'rb') as f:
        hdr = bytearray(f.read(start))
    mdat_size = c.moov_pos - lay['mdat_pos']           # o mdat da Sony vai até o moov
    if lay['mdat_hdr'] == 16:
        hdr[start - 8:start] = struct.pack('>Q', mdat_size)
    elif mdat_size <= 0xFFFFFFFF:
        hdr[start - 8:start - 4] = struct.pack('>I', mdat_size)
    else:
        raise ValueError(L('err.ref_layout_mismatch', ref=os.path.basename(c.reference), a=start, b=start))
    oext = out_ext(c.name)
    dst = out_base(c.path, out_dir, stem, 'REPARADO', oext, keep_names) + oext
    copied = 0
    with open(c.path, 'rb') as src, atomic_write(dst) as out:
        src.seek(start)
        out.write(hdr)
        while True:
            b = src.read(16 * 1024 * 1024)
            if not b:
                break
            out.write(b)
            copied += len(b)
            if chunk_cb:
                chunk_cb(copied, c.size)
    copy_sidecar(c.path, out_dir, os.path.splitext(os.path.basename(dst))[0])
    copy_times(st, dst)
    c.output = dst
    return dst


# espaço ocupado na saída, em múltiplos do tamanho do original, por modo de reparo.
# Extração bruta: .h264 (~vídeo) + .wav + MP4 temporário + MP4 final -> perto de 3x no pico.
SPACE_FACTOR = {'cabecalho': 1.05, 'reconstruir': 1.05, 'extrair': 3.0}


def space_needed(clips: list[Clip]) -> int:
    """Bytes livres necessários na pasta de saída para reparar `clips`."""
    total = 0
    for c in clips:
        mode = c.extra.get('mode', 'cabecalho' if c.status == HEADER else 'reconstruir')
        total += c.size * SPACE_FACTOR.get(mode, 3.0)
    return int(total)


# limites de tempo dos subprocessos (s): uma base fixa + o tamanho do arquivo numa taxa
# bem conservadora, para nunca cortar um trabalho legítimo e ainda assim não travar
# para sempre num arquivo que deixe o ffprobe/ffmpeg preso.
FFPROBE_BASE_S, FFPROBE_RATE = 60, 10e6      # ffprobe -count_frames decodifica o clipe todo
FFMPEG_BASE_S, FFMPEG_RATE = 60, 5e6         # ffmpeg -c copy (só E/S)


def ffprobe_timeout(size: int) -> float:
    return FFPROBE_BASE_S + size / FFPROBE_RATE


def ffmpeg_timeout(size: int) -> float:
    return FFMPEG_BASE_S + size / FFMPEG_RATE


def run_quiet(cmd: list[str], timeout: float | None = None, cancel=None, **kw):
    """subprocess.run sem abrir janela de console no Windows.

    `timeout`: encerra o processo e levanta subprocess.TimeoutExpired.
    `cancel` (threading.Event): se for acionado, encerra o processo e levanta Cancelled."""
    if sys.platform == 'win32':
        kw.setdefault('creationflags', 0x08000000)  # CREATE_NO_WINDOW
    if cancel is None:
        return subprocess.run(cmd, timeout=timeout, **kw)
    import time
    text = kw.pop('text', False)
    data = kw.pop('input', None)
    if kw.pop('capture_output', False):
        kw['stdout'] = kw['stderr'] = subprocess.PIPE
    if data is not None:
        kw['stdin'] = subprocess.PIPE
    deadline = None if timeout is None else time.monotonic() + timeout
    with subprocess.Popen(cmd, text=text, **kw) as proc:
        while True:
            try:
                # a entrada só pode ser enviada na 1ª chamada; nas seguintes o envio continua sozinho
                out, err = proc.communicate(input=data, timeout=0.25)
                return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                data = None
                if cancel.is_set():
                    proc.kill()
                    proc.communicate()
                    raise Cancelled()
                if deadline is not None and time.monotonic() > deadline:
                    proc.kill()
                    proc.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout)


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


def verify(c: Clip, cancel=None) -> str:
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
    limit = ffprobe_timeout(os.path.getsize(c.output) if os.path.exists(c.output) else c.size)
    try:
        r = run_quiet([fp, '-v', 'error', '-count_frames', '-select_streams', 'v:0',
                       '-show_entries', 'stream=nb_frames,nb_read_frames', '-of', 'csv=p=0',
                       c.output], capture_output=True, text=True, timeout=limit, cancel=cancel)
    except subprocess.TimeoutExpired:
        # o subprocess.run já matou o ffprobe; o arquivo recuperado continua lá
        c.verify = L('verify.timeout', s=int(limit))
        return c.verify
    try:
        total, read = (int(x) for x in r.stdout.strip().split(',')[:2])
        lost = total - read
        c.verify = Join(L('verify.frames', read=read, total=total), L('verify.lost', n=lost) if lost else '')
    except ValueError:
        c.verify = L('verify.fail')
    if c.audio_db is not None:
        c.verify = Join(c.verify, L('verify.audio_peak', db=c.audio_db))
    return c.verify


# ---------------------------------------------------------------- prévia (thumbnails)

THUMB_FRACS = (0.1, 0.5, 0.9)
THUMB_WIDTH = 192            # px; o painel mostra em ~86 px (nítido em tela Retina)
THUMB_TIMEOUT = 20.0         # s por quadro


def video_samples(moov: bytes):
    """Tabela do vídeo a partir do moov: ([(offset, tamanho)], índices de keyframe (0-based),
    dados do stsd). Lê stco/co64 + stsc + stsz + stss."""
    from . import rebuild as rb
    root = rb.Box.parse(moov)[0]
    for t in root.all(b'trak'):
        if t.find(b'mdia', b'hdlr').data[8:12] != b'vide':
            continue
        stbl = t.find(b'mdia', b'minf', b'stbl')
        sz = stbl.find(b'stsz').data
        fixed, n = struct.unpack('>II', sz[4:12])
        sizes = [fixed] * n if fixed else list(struct.unpack(f'>{n}I', sz[12:12 + 4 * n]))
        co = stbl.find(b'stco')
        if co is not None:
            k = struct.unpack('>I', co.data[4:8])[0]
            chunk_offs = list(struct.unpack(f'>{k}I', co.data[8:8 + 4 * k]))
        else:
            co = stbl.find(b'co64')
            k = struct.unpack('>I', co.data[4:8])[0]
            chunk_offs = list(struct.unpack(f'>{k}Q', co.data[8:8 + 8 * k]))
        stsc = rb._table(stbl.find(b'stsc'), 3)
        samples, si = [], 0
        for ci, off in enumerate(chunk_offs):
            first = ci + 1
            spc = next((e[1] for e in reversed(stsc) if e[0] <= first), 0)
            for _ in range(spc):
                if si >= n:
                    break
                samples.append((off, sizes[si]))
                off += sizes[si]
                si += 1
        ss = stbl.find(b'stss')
        keys = sorted(x[0] - 1 for x in rb._table(ss, 1)) if ss is not None else list(range(len(samples)))
        return samples, keys, stbl.find(b'stsd').data
    return [], [], b''


def thumbnail_source(c: Clip) -> str | None:
    """Arquivo de onde tirar a prévia: o recuperado, se existir; senão o próprio clipe,
    desde que tenha índice (íntegro ou só com o cabeçalho danificado)."""
    if c.output and os.path.isfile(c.output) and c.output.lower().endswith(('.mp4', '.mov')):
        return c.output
    if c.moov_pos >= 0 and c.status in (OK, HEADER):
        return c.path
    return None


def thumbnails(path: str, fracs=THUMB_FRACS, width: int = THUMB_WIDTH, cancel=None) -> list:
    """Quadros de prévia (PNG em memória) a 10 %, 50 % e 90 % do clipe.

    Não depende do cabeçalho do arquivo: o keyframe mais próximo de cada ponto é lido direto
    pelas tabelas do moov, recebe os parâmetros do avcC/hvcC e é decodificado pelo ffmpeg via pipe.
    Por isso funciona também em clipes com cabeçalho danificado. H.264 (avc1)
    e H.265 (hvc1/hev1, parâmetros do hvcC); retorna [] sem ffmpeg, sem moov ou em outro codec. Itens None = quadro ilegível."""
    from . import rebuild as rb
    ff = ffmpeg_bin()
    m = find_moov(path)
    if not ff or m is None:
        return []
    try:
        samples, keys, stsd = video_samples(m[1])
    except Exception:
        return []
    fam = rb.video_family(stsd)
    if fam == 'hevc':
        nals = rb.hvcc_params(stsd)
        demux = 'hevc'
    else:
        sps, pps = rb.avcc_params(stsd)
        nals = sps and sps + pps
        demux = 'h264'
    if not samples or not keys or not nals:
        return []
    params = b''.join(rb.SC + x for x in nals)
    out = []
    with open(path, 'rb') as f:
        for fr in fracs:
            target = min(len(samples) - 1, int(len(samples) * fr))
            k = max((x for x in keys if x <= target), default=keys[0])
            off, ln = samples[k]
            f.seek(off)
            au = (params + rb._au_to_annexb(f.read(ln), b'', idr=lambda n: False)) if fam == 'hevc' \
                else rb._au_to_annexb(f.read(ln), params)
            try:
                r = run_quiet([ff, '-v', 'error', '-f', demux, '-i', '-', '-frames:v', '1',
                               '-vf', f'scale={width}:-2:flags=bilinear,format=rgb24',
                               '-f', 'image2pipe', '-c:v', 'png', '-'],
                              input=au, capture_output=True, timeout=THUMB_TIMEOUT,
                              **({'cancel': cancel} if cancel is not None else {}))
                out.append(r.stdout if r.returncode == 0 and r.stdout.startswith(b'\x89PNG') else None)
            except subprocess.TimeoutExpired:
                out.append(None)
    return out


CSV_FORMULA = ('=', '+', '-', '@', '\t', '\r')


def csv_safe(v):
    """Evita injeção de fórmula ao abrir o CSV no Excel/Numbers/Sheets: texto que começa
    com = + - @ (ou tab/CR) ganha um apóstrofo na frente. Números ficam como estão."""
    if isinstance(v, str) and v.startswith(CSV_FORMULA):
        return "'" + v
    return v


def write_batch_reports(clips: list[Clip], todo: list[Clip], out: str | None, name: str):
    """Relatórios CSV do fim de um lote de reparo.

    out=None ("salvar junto dos originais"): um relatório em cada <pasta do clipe>/Second Take,
    com os clipes daquela pasta. Senão, um relatório só, junto dos recuperados.
    Retorna (onde mostrar, [(caminho, erro)]). Uma falha aqui (disco cheio, sem permissão,
    pasta removida) vai para o log e para a lista, e nunca afeta os clipes já recuperados."""
    failures = []

    def one(group, rdir):
        path = os.path.join(rdir, name)
        try:
            os.makedirs(rdir, exist_ok=True)
            write_report(group, path)
        except Exception as e:
            log.exception('relatório CSV não gravado: %s', path)
            failures.append((path, e))

    if out is None:
        folders = group_by_folder(todo)
        for d in folders:
            one([c for c in clips if os.path.dirname(os.path.abspath(c.path)) == d],
                os.path.join(d, SAME_FOLDER_DIR))
        shown = os.path.join(next(iter(folders)), SAME_FOLDER_DIR) if len(folders) == 1 else None
    else:
        shown = report_dir(out, todo)            # saída = origem: relatório na subpasta Second Take
        one(clips, shown)
    return shown, failures


def short_err(e: BaseException) -> str:
    """Erro curto para a barra de status: sem o caminho (que vai inteiro para o log)."""
    if isinstance(e, OSError) and e.strerror:
        return e.strerror
    return str(msg(e))


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
            w.writerow([csv_safe(row[k]) for k in cols])
