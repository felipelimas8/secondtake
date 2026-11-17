"""secondtake.rebuild — modo avançado: reconstrói o índice (moov) de clipes Sony XAVC S.

Para arquivos cuja gravação foi interrompida (sem moov). Usa um clipe de
referência da mesma câmera/configuração como molde. O mdat XAVC S é gravado
em blocos regulares, um por "chunk" (~0,5 s):

    [rtmd: G amostras de tamanho fixo][vídeo: G access units][áudio LPCM: A bytes]

O vídeo é varrido NAL a NAL (cada access unit começa com um AUD, NAL tipo 9);
metadados e áudio têm tamanho fixo por chunk, lido da referência. Com isso as
tabelas de amostras são reconstruídas e um moov novo é escrito no fim de uma
cópia do arquivo. Vídeo, áudio e metadados são preservados até o último chunk
completo. Originais nunca são alterados.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from .i18n import L, msg
from .safeio import atomic_write, fsync_path, part_path, unique_stem

CONTAINERS = {b'moov', b'trak', b'edts', b'mdia', b'minf', b'stbl', b'dinf'}


# ---------------------------------------------------------------- árvore de boxes

class Box:
    def __init__(self, typ: bytes, data: bytes = b'', children=None):
        self.typ = typ
        self.data = data
        self.children = children

    @classmethod
    def parse(cls, buf: bytes, s: int = 0, e: int | None = None) -> list['Box']:
        e = len(buf) if e is None else e
        out, p = [], s
        while p + 8 <= e:
            size, typ = struct.unpack('>I4s', buf[p:p + 8])
            hdr = 8
            if size == 1:
                size = struct.unpack('>Q', buf[p + 8:p + 16])[0]
                hdr = 16
            elif size == 0:
                size = e - p
            if size < hdr or p + size > e:
                break
            if typ in CONTAINERS:
                out.append(cls(typ, children=cls.parse(buf, p + hdr, p + size)))
            else:
                out.append(cls(typ, buf[p + hdr:p + size]))
            p += size
        return out

    def serialize(self) -> bytes:
        body = b''.join(c.serialize() for c in self.children) if self.children is not None else self.data
        return struct.pack('>I4s', len(body) + 8, self.typ) + body

    def find(self, *path: bytes) -> 'Box | None':
        node = self
        for t in path:
            node = next((c for c in (node.children or []) if c.typ == t), None)
            if node is None:
                return None
        return node

    def all(self, typ: bytes):
        return [c for c in (self.children or []) if c.typ == typ]


def _full(version_flags: int, payload: bytes) -> bytes:
    return struct.pack('>I', version_flags) + payload


def _set_duration(box: Box, offset_v0: int, offset_v1: int, value: int):
    d = bytearray(box.data)
    if d[0] == 1:
        d[offset_v1:offset_v1 + 8] = struct.pack('>Q', value)
    else:
        d[offset_v0:offset_v0 + 4] = struct.pack('>I', value)
    box.data = bytes(d)


def _timescale(mdhd: Box) -> int:
    d = mdhd.data
    return struct.unpack('>I', d[20:24] if d[0] == 1 else d[12:16])[0]


def _table(box: Box | None, width: int) -> list[tuple]:
    if box is None:
        return []
    n = struct.unpack('>I', box.data[4:8])[0]
    fmt = '>' + 'I' * width
    return [struct.unpack(fmt, box.data[8 + 4 * width * i:8 + 4 * width * (i + 1)]) for i in range(n)]


# ---------------------------------------------------------------- perfil da referência

@dataclass
class Profile:
    ref_path: str
    header: bytes            # bytes do arquivo de referência até o início dos dados do mdat
    data_start: int          # offset do primeiro chunk
    moov: Box
    G: int                   # quadros por chunk
    meta_size: int           # bytes por amostra rtmd
    audio_chunk: int         # bytes de áudio por chunk
    audio_spc: int           # amostras de áudio por chunk
    audio_bps: int           # bytes por amostra de áudio (todos os canais)
    v_delta: int             # duração de um quadro (timescale do vídeo)
    v_ts: int
    a_ts: int
    m_ts: int                # timescale do filme (mvhd)
    gop: int                 # quadros entre keyframes
    ctts_first: list[int]    # offsets de composição do 1º GOP
    ctts_steady: list[int]   # offsets dos GOPs seguintes
    video_stsd: bytes
    n_keys: int = 1          # keyframes na referência (mais = GOP/ctts confiáveis)

    def key(self):
        return (self.G, self.meta_size, self.audio_chunk, self.v_delta, self.v_ts, self.video_stsd)


def _expand(entries):
    out = []
    for cnt, val in entries:
        out += [val] * cnt
    return out


_PROFILE_CACHE: dict[tuple, 'Profile'] = {}
_PROFILE_CACHE_MAX = 64


def load_profile(ref_path: str, find_moov) -> Profile:
    """Perfil de um clipe de referência, com cache por (caminho real, tamanho, data de
    modificação): numa pasta grande a mesma referência é testada para vários clipes.
    O Profile não é alterado depois de criado (build_moov copia o moov), então é seguro reusar."""
    try:
        st = os.stat(ref_path)
        key = (os.path.realpath(ref_path), st.st_size, st.st_mtime_ns, getattr(find_moov, '__name__', ''))
    except OSError:
        key = None
    if key is not None and key in _PROFILE_CACHE:
        return _PROFILE_CACHE[key]
    prof = _load_profile(ref_path, find_moov)
    if key is not None:
        if len(_PROFILE_CACHE) >= _PROFILE_CACHE_MAX:
            _PROFILE_CACHE.pop(next(iter(_PROFILE_CACHE)))
        _PROFILE_CACHE[key] = prof
    return prof


def clear_profile_cache():
    _PROFILE_CACHE.clear()


def _load_profile(ref_path: str, find_moov) -> Profile:
    pos, raw = find_moov(ref_path)
    moov = Box.parse(raw)[0]
    traks = {}
    for t in moov.all(b'trak'):
        h = t.find(b'mdia', b'hdlr').data[8:12]
        traks[h] = t
    v, a, m = traks.get(b'vide'), traks.get(b'soun'), traks.get(b'meta')
    if v is None or a is None:
        raise ValueError(L('err.ref_tracks'))
    vst = v.find(b'mdia', b'minf', b'stbl')
    ast = a.find(b'mdia', b'minf', b'stbl')
    G = _table(vst.find(b'stsc'), 3)[0][1]
    audio_spc = _table(ast.find(b'stsc'), 3)[0][1]
    asd = ast.find(b'stsd').data
    ch, bits = struct.unpack('>HH', asd[8 + 24:8 + 28])
    audio_bps = ch * bits // 8
    meta_size = 0
    if m is not None:
        meta_size = struct.unpack('>I', m.find(b'mdia', b'minf', b'stbl', b'stsz').data[4:8])[0]
    stts = _table(vst.find(b'stts'), 2)
    v_delta = stts[0][1]
    stss = [x[0] for x in _table(vst.find(b'stss'), 1)]
    gop = (stss[1] - stss[0]) if len(stss) > 1 else G
    ctts = _expand(_table(vst.find(b'ctts'), 2))
    first = ctts[:gop]
    steady = ctts[gop:2 * gop] if len(ctts) >= 2 * gop else first
    # início dos dados = menor offset de chunk
    offs = []
    for t in (v, a, m):
        if t is None:
            continue
        st = t.find(b'mdia', b'minf', b'stbl')
        co = st.find(b'stco') or st.find(b'co64')
        if co.typ == b'stco':
            offs.append(struct.unpack('>I', co.data[8:12])[0])
        else:
            offs.append(struct.unpack('>Q', co.data[8:16])[0])
    data_start = min(offs)
    with open(ref_path, 'rb') as f:
        header = f.read(data_start)
    if header[data_start - 12:data_start - 8] != b'mdat' and header[data_start - 4:data_start] != b'mdat':
        raise ValueError(L('err.ref_layout'))
    mvhd = moov.find(b'mvhd').data
    m_ts = struct.unpack('>I', mvhd[20:24] if mvhd[0] == 1 else mvhd[12:16])[0]
    return Profile(ref_path, header, data_start, moov, G, meta_size,
                   audio_spc * audio_bps, audio_spc, audio_bps, v_delta,
                   _timescale(v.find(b'mdia', b'mdhd')), _timescale(a.find(b'mdia', b'mdhd')),
                   m_ts, gop, first, steady, vst.find(b'stsd').data, max(1, len(stss)))


# ---------------------------------------------------------------- varredura do mdat

@dataclass
class Chunk:
    meta_off: int
    video_off: int
    frame_sizes: list[int]
    audio_off: int
    keys: list[int] = None   # índices (0..G-1) dos quadros IDR dentro do bloco

    @property
    def starts_idr(self):
        return bool(self.keys) and self.keys[0] == 0


class ScanError(Exception):
    pass


AUD_RE = None


def _resync(f, pos: int, prof: Profile, size: int):
    """Procura o próximo bloco íntegro a partir de pos. Retorna (meta_off, sizes, end, keys) ou None."""
    import re
    global AUD_RE
    if AUD_RE is None:
        AUD_RE = re.compile(rb'\x00\x00\x00[\x02\x03][\x09\x29\x49\x69]')
    meta_len = prof.G * prof.meta_size
    p = pos + meta_len
    win = 8 << 20
    while p < size:
        f.seek(p)
        buf = f.read(win + 5)
        for m in AUD_RE.finditer(buf):
            v = p + m.start()
            cand = v - meta_len
            if cand < pos:
                continue
            try:
                sizes, end, keys = _read_last_aware(f, v, prof, size)
            except ScanError:
                continue
            if end + prof.audio_chunk > size:
                continue
            return cand, sizes, end, keys
        p += win
    return None


def scan_chunks(path: str, prof: Profile, data_start: int | None = None,
                progress=None, resync: bool = True):
    """Percorre o mdat em blocos. Com resync, pula trechos danificados e continua
    no próximo bloco íntegro que começa com keyframe (IDR).
    Retorna (chunks, fim_dos_dados, motivo, trechos_pulados[(início, fim)])."""
    size = os.path.getsize(path)
    pos = prof.data_start if data_start is None else data_start
    chunks: list[Chunk] = []
    gaps: list[tuple[int, int]] = []
    reason = L('reason.eof')
    need_idr = True          # após o início ou um trecho pulado, só aceita bloco que começa em IDR
    gap_start = None
    with open(path, 'rb') as f:
        while True:
            meta_off = pos
            video_off = meta_off + prof.G * prof.meta_size
            if video_off >= size or _is_tail(f, meta_off, size):
                reason = L('reason.end')
                break
            try:
                sizes, end, keys = _read_last_aware(f, video_off, prof, size)
                if end + prof.audio_chunk > size:
                    raise ScanError(L('reason.audio_incomplete'))
            except ScanError as e:
                tail = size - meta_off
                big = prof.G * prof.meta_size + prof.audio_chunk + 64 * (1 << 20)
                if tail < big and not resync:
                    reason = L('reason.cut')
                    break
                if not resync:
                    reason = msg(e)
                    break
                found = _resync(f, meta_off + 1, prof, size)
                if found is None:
                    reason = L('reason.cut') if tail < big else L('reason.no_good_after', pos=meta_off)
                    break
                if gap_start is None:
                    gap_start = meta_off
                meta_off, sizes, end, keys = found
                video_off = meta_off + prof.G * prof.meta_size
                need_idr = True
            ch = Chunk(meta_off, video_off, sizes, end, keys)
            pos = end + prof.audio_chunk
            if need_idr and not ch.starts_idr:
                if gap_start is None:
                    gap_start = meta_off
                continue
            if gap_start is not None:
                gaps.append((gap_start, meta_off))
                gap_start = None
            need_idr = False
            chunks.append(ch)
            if progress:
                progress(pos, size)
    if gap_start is not None:
        gaps.append((gap_start, pos))
    return chunks, pos, reason, gaps


def _is_aud(f, p: int) -> bool:
    f.seek(p)
    h = f.read(5)
    return len(h) == 5 and struct.unpack('>I', h[:4])[0] in (2, 3) and h[4] & 0x1F == 9


def _is_tail(f, pos: int, size: int) -> bool:
    """Fim da gravação: fim do arquivo ou preenchimento com zeros (antes do moov)."""
    if pos > size:
        return False
    if pos == size:
        return True
    f.seek(pos)
    b = f.read(min(4096, size - pos))
    return b.count(0) == len(b)


def _read_last_aware(f, video_off: int, prof: Profile, size: int):
    """Lê os G access units de um bloco. O fim do último AU é confirmado quando,
    depois do áudio do bloco, os metadados e o AUD do bloco seguinte batem
    (ou quando o áudio termina exatamente no fim do arquivo)."""
    if not _is_aud(f, video_off):
        raise ScanError(L('reason.no_aud', pos=video_off))
    sizes, p, start, seen, keys = [], video_off, video_off, 0, []
    skip = prof.audio_chunk + prof.G * prof.meta_size
    while True:
        if seen == prof.G and p > start:
            if _is_aud(f, p + skip) or _is_tail(f, p + prof.audio_chunk, size):
                sizes.append(p - start)
                return sizes, p, keys
        if p + 5 > size:
            raise ScanError(L('reason.eof_video'))
        f.seek(p)
        hdr = f.read(5)
        n = struct.unpack('>I', hdr[:4])[0]
        t = hdr[4] & 0x1F
        if n == 0 or n > (64 << 20) or hdr[4] & 0x80 or t == 0 or t > 23:
            raise ScanError(L('reason.bad_nal', pos=p))
        if t == 9:
            if seen > 0:
                sizes.append(p - start)
                start = p
            seen += 1
            if seen > prof.G:
                raise ScanError(L('reason.too_many', g=prof.G, pos=video_off))
        elif t == 5 and (not keys or keys[-1] != seen - 1):
            keys.append(seen - 1)
        p += 4 + n


# ---------------------------------------------------------------- moov novo

def _stbl_tables(stbl: Box, entries: dict[bytes, bytes]):
    keep = []
    for c in stbl.children:
        if c.typ in (b'stts', b'ctts', b'stsc', b'stsz', b'stco', b'co64', b'stss', b'sdtp'):
            continue
        keep.append(c)
    for typ in (b'stts', b'ctts', b'stsc', b'stsz', b'stco', b'co64', b'stss'):
        if typ in entries:
            keep.append(Box(typ, entries[typ]))
    stbl.children = keep


def _co(offsets: list[int]) -> tuple[bytes, bytes]:
    if max(offsets, default=0) > 0xFFFFFFFF:
        return b'co64', _full(0, struct.pack('>I', len(offsets)) + b''.join(struct.pack('>Q', o) for o in offsets))
    return b'stco', _full(0, struct.pack('>I', len(offsets)) + b''.join(struct.pack('>I', o) for o in offsets))


def _rle(values: list[int]) -> list[tuple[int, int]]:
    out = []
    for v in values:
        if out and out[-1][1] == v:
            out[-1][0] += 1
        else:
            out.append([1, v])
    return [tuple(x) for x in out]


def build_moov(prof: Profile, chunks: list[Chunk]) -> bytes:
    moov = Box.parse(prof.moov.serialize())[0]  # cópia profunda
    G, n_chunks = prof.G, len(chunks)
    n_frames = G * n_chunks
    v_dur = n_frames * prof.v_delta
    a_samples = prof.audio_spc * n_chunks
    movie_dur = round(v_dur * prof.m_ts / prof.v_ts)

    _set_duration(moov.find(b'mvhd'), 16, 24, movie_dur)
    for t in moov.all(b'trak'):
        h = t.find(b'mdia', b'hdlr').data[8:12]
        _set_duration(t.find(b'tkhd'), 20, 28, movie_dur)
        elst = t.find(b'edts', b'elst')
        if elst is not None:
            _set_duration(elst, 8, 8, movie_dur)
        mdhd = t.find(b'mdia', b'mdhd')
        stbl = t.find(b'mdia', b'minf', b'stbl')
        if h == b'vide':
            _set_duration(mdhd, 16, 24, v_dur)
            sizes = [s for c in chunks for s in c.frame_sizes]
            keys = [ci * G + k + 1 for ci, c in enumerate(chunks) for k in (c.keys or [])]
            if not keys:
                keys = list(range(1, n_frames + 1, prof.gop))
            ctts = _ctts_for(prof, keys, n_frames)
            co_t, co_d = _co([c.video_off for c in chunks])
            e = {
                b'stts': _full(0, struct.pack('>III', 1, n_frames, prof.v_delta)),
                b'stsc': _full(0, struct.pack('>IIII', 1, 1, G, 1)),
                b'stsz': _full(0, struct.pack('>II', 0, n_frames) + b''.join(struct.pack('>I', s) for s in sizes)),
                b'stss': _full(0, struct.pack('>I', len(keys)) + b''.join(struct.pack('>I', k) for k in keys)),
                co_t: co_d,
            }
            if prof.ctts_first:
                r = _rle(ctts)
                e[b'ctts'] = _full(0, struct.pack('>I', len(r)) + b''.join(struct.pack('>II', a, b) for a, b in r))
            _stbl_tables(stbl, e)
        elif h == b'soun':
            _set_duration(mdhd, 16, 24, round(a_samples * prof.a_ts / prof.a_ts))
            co_t, co_d = _co([c.audio_off for c in chunks])
            _stbl_tables(stbl, {
                b'stts': _full(0, struct.pack('>III', 1, a_samples, 1)),
                b'stsc': _full(0, struct.pack('>IIII', 1, 1, prof.audio_spc, 1)),
                b'stsz': _full(0, struct.pack('>II', prof.audio_bps, a_samples)),
                co_t: co_d,
            })
        else:
            _set_duration(mdhd, 16, 24, v_dur)
            co_t, co_d = _co([c.meta_off for c in chunks])
            _stbl_tables(stbl, {
                b'stts': _full(0, struct.pack('>III', 1, n_frames, prof.v_delta)),
                b'stsc': _full(0, struct.pack('>IIII', 1, 1, G, 1)),
                b'stsz': _full(0, struct.pack('>II', prof.meta_size, n_frames)),
                co_t: co_d,
            })
    return moov.serialize()


# ---------------------------------------------------------------- API

@dataclass
class Plan:
    profile: Profile
    chunks: list[Chunk]
    data_end: int
    reason: str
    data_start: int
    gaps: list = None

    @property
    def gap_bytes(self) -> int:
        return sum(b - a for a, b in (self.gaps or []))

    @property
    def seconds(self) -> float:
        return len(self.chunks) * self.profile.G * self.profile.v_delta / self.profile.v_ts


def _min_chunk(prof: 'Profile') -> int:
    """Menor tamanho possível de um bloco (rtmd + áudio, sem contar o vídeo): perder menos
    que isso no começo não pode ser um bloco inteiro."""
    return prof.G * prof.meta_size + prof.audio_chunk


def starts_at_first_chunk(p: 'Plan') -> bool:
    """O 1º bloco recuperado é o 1º gravado? (no máximo uma fração de bloco perdida no início,
    ex.: um SEI antes do 1º AUD). Só assim o timecode do XML vale para o 1º quadro."""
    if not p.chunks:
        return False
    return p.chunks[0].meta_off - p.data_start < _min_chunk(p.profile)


def plan(path: str, refs: list[str], find_moov, data_start: int | None = None) -> Plan:
    """Testa cada referência e fica com a que explica mais blocos do arquivo."""
    best, seen, errors = None, {}, []
    for r in refs:
        try:
            prof = load_profile(r, find_moov)
        except Exception as e:
            errors.append(f'{os.path.basename(r)}: {e}')
            continue
        k = prof.key()
        if k in seen and seen[k].n_keys >= prof.n_keys:
            continue
        seen[k] = prof
        start = prof.data_start if data_start is None else data_start
        chunks, end, reason, gaps = scan_chunks(path, prof, start)
        if best is None or (len(chunks), prof.n_keys) > (len(best.chunks), best.profile.n_keys):
            best = Plan(prof, chunks, end, reason, start, gaps)
        # as referências chegam em ordem de assinatura (XML/moov): se esta já explica o arquivo
        # inteiro, sem trechos pulados e com GOP confiável, não precisa testar as outras
        if chunks and prof.n_keys >= 2 and getattr(reason, 'key', '') == 'reason.end' \
                and sum(b - a for a, b in gaps) < _min_chunk(prof):
            break
    if best is None or not best.chunks:
        from .i18n import Join
        raise ValueError(Join(L('err.no_compatible'), best.reason if best is not None else '', sep=' — '))
    return best


def write(path: str, p: Plan, dst: str, chunk_cb=None) -> str:
    prof = p.profile
    hdr = bytearray(prof.header[:p.data_start] if p.data_start <= len(prof.header) else prof.header)
    if len(hdr) != p.data_start:
        raise ValueError(L('err.offset_mismatch'))
    # mdat com largesize ocupa os 16 bytes finais do cabeçalho
    mdat_pos = p.data_start - 16
    if hdr[mdat_pos + 4:mdat_pos + 8] != b'mdat':
        raise ValueError(L('err.ref_mdat'))
    hdr[mdat_pos + 8:mdat_pos + 16] = struct.pack('>Q', p.data_end - mdat_pos)
    moov = build_moov(prof, p.chunks)
    total = p.data_end - p.data_start
    copied = 0
    with open(path, 'rb') as src, atomic_write(dst) as out:
        out.write(hdr)
        src.seek(p.data_start)
        left = total
        while left > 0:
            b = src.read(min(16 << 20, left))
            if not b:
                break
            out.write(b)
            left -= len(b)
            copied += len(b)
            if chunk_cb:
                chunk_cb(copied, total)
        out.write(moov)
    return dst


# ---------------------------------------------------------------- nível 4: extração bruta

SONY_NAL_TYPES = {1, 5, 6, 7, 8, 9, 12}


def avcc_params(stsd_data: bytes) -> tuple[list[bytes], list[bytes]]:
    i = stsd_data.find(b'avcC')
    if i < 0:
        return [], []
    p = i + 4 + 5
    n_sps = stsd_data[p] & 0x1F
    p += 1
    sps, pps = [], []
    for _ in range(n_sps):
        ln = struct.unpack('>H', stsd_data[p:p + 2])[0]
        sps.append(stsd_data[p + 2:p + 2 + ln])
        p += 2 + ln
    n_pps = stsd_data[p]
    p += 1
    for _ in range(n_pps):
        ln = struct.unpack('>H', stsd_data[p:p + 2])[0]
        pps.append(stsd_data[p + 2:p + 2 + ln])
        p += 2 + ln
    return sps, pps


SC = b'\x00\x00\x00\x01'


def _au_to_annexb(au: bytes, params: bytes) -> bytes:
    out, p, idr = [], 0, False
    while p + 4 <= len(au):
        n = struct.unpack('>I', au[p:p + 4])[0]
        nal = au[p + 4:p + 4 + n]
        if nal and nal[0] & 0x1F == 5 and not idr:
            out.append(params)
            idr = True
        out.append(SC + nal)
        p += 4 + n
    return b''.join(out)


def _wav_header(n_bytes: int, rate: int, ch: int, bits: int) -> bytes:
    ba = ch * bits // 8
    return (b'RIFF' + struct.pack('<I', 36 + n_bytes) + b'WAVEfmt ' +
            struct.pack('<IHHIIHH', 16, 1, ch, rate, rate * ba, ba, bits) +
            b'data' + struct.pack('<I', n_bytes))


def extract_raw(path: str, out_dir: str, plan_: Plan | None = None,
                profile: Profile | None = None, progress=None, timecode: str = '') -> dict:
    """Último recurso: grava o vídeo como stream H.264 (.h264) e o áudio como .wav,
    e monta um MP4 limpo (com ffmpeg, se houver). Com um Plan (layout conhecido),
    áudio e vídeo saem completos; sem Plan, varre o arquivo atrás de quadros H.264
    válidos e recupera só o vídeo."""
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    # um só número para o trio .h264/.wav/.MP4 (C0001_EXTRAIDO_2.*), sem sobrescrever nada
    base = unique_stem(out_dir, f'{stem}_EXTRAIDO', ['.h264', '.wav', '.MP4'])
    h264 = base + '.h264'
    prof = plan_.profile if plan_ else profile
    params = b''
    if prof:
        sps, pps = avcc_params(prof.video_stsd)
        params = b''.join(SC + x for x in sps + pps)
    res = {'h264': h264, 'wav': None, 'mov': None, 'frames': 0, 'audio_s': 0.0, 'timecode': ''}
    samples: list[tuple[int, int, bool]] = []
    size = os.path.getsize(path)
    with open(path, 'rb') as f, atomic_write(h264) as out:
        if params:
            out.write(params)
        if plan_:
            wav = base + '.wav'
            ch, bits = 2, 16
            asd = None
            for t in prof.moov.all(b'trak'):
                if t.find(b'mdia', b'hdlr').data[8:12] == b'soun':
                    asd = t.find(b'mdia', b'minf', b'stbl', b'stsd').data
            if asd:
                ch, bits = struct.unpack('>HH', asd[8 + 24:8 + 28])
            fmt = asd[12:16] if asd else b'twos'
            total = len(plan_.chunks) * prof.audio_chunk
            with atomic_write(wav) as w:
                w.write(_wav_header(total, prof.a_ts, ch, bits))
                for i, c in enumerate(plan_.chunks):
                    p = c.video_off
                    for s in c.frame_sizes:
                        f.seek(p)
                        out.write(_au_to_annexb(f.read(s), params))
                        p += s
                        res['frames'] += 1
                    f.seek(c.audio_off)
                    a = f.read(prof.audio_chunk)
                    if fmt in (b'twos', b'lpcm', b'ipcm') and bits == 16:
                        import array
                        arr = array.array('h', a)
                        arr.byteswap()
                        a = arr.tobytes()
                    w.write(a)
                    if progress:
                        progress(i + 1, len(plan_.chunks))
            res['wav'] = wav
            res['audio_s'] = total / (prof.audio_bps * prof.a_ts)
        else:
            import re
            rx = re.compile(rb'\x00\x00\x00[\x02\x03][\x09\x29\x49\x69]')
            pos, win = 0, 8 << 20
            while pos < size:
                f.seek(pos)
                buf = f.read(win + 5)
                if not buf:
                    break
                nxt = pos + win
                for m in rx.finditer(buf):
                    p = pos + m.start()
                    if p < pos:
                        continue
                    au_start, frames = p, []
                    q = p
                    while q + 5 <= size:
                        f.seek(q)
                        h = f.read(5)
                        n = struct.unpack('>I', h[:4])[0]
                        t = h[4] & 0x1F
                        if n == 0 or n > (64 << 20) or h[4] & 0x80 or t not in SONY_NAL_TYPES or q + 4 + n > size:
                            break
                        if t == 9 and q != au_start:
                            frames.append((au_start, q - au_start))
                            au_start = q
                        q += 4 + n
                    # descarta o último AU da sequência (pode ter engolido áudio)
                    for a0, ln in frames:
                        f.seek(a0)
                        au = f.read(ln)
                        out.write(_au_to_annexb(au, params))
                        samples.append((a0, ln, _has_idr(au)))
                        res['frames'] += 1
                    if frames:
                        nxt = frames[-1][0] + frames[-1][1]
                        break
                pos = nxt
                if progress:
                    progress(min(pos, size), size)
    if not res['frames']:
        os.remove(h264)
        res['h264'] = None
        return res
    mov = base + '.MP4'
    if plan_ is not None:
        # layout conhecido: monta o MP4 completo e copia só vídeo+áudio para um MP4 limpo
        tmp = os.path.join(out_dir, f'.{os.path.basename(base)}_tmp.MP4')
        try:
            write(path, plan_, tmp)
            # o timecode do XML só vale se o 1º quadro recuperado é o 1º quadro gravado
            tc = timecode if starts_at_first_chunk(plan_) else ''
            if _ffmpeg_copy(tmp, mov, tc):
                res['mov'] = mov
                res['timecode'] = tc
            elif tc and _ffmpeg_copy(tmp, mov):      # timecode recusado pelo ffmpeg: MP4 sem tmcd
                res['mov'] = mov
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    elif prof is not None and samples:
        if write_video_mp4(path, samples, prof, mov):
            res['mov'] = mov
    return res


def _ffmpeg():
    from .core import ffmpeg_bin
    return ffmpeg_bin('ffmpeg')


def _ffmpeg_copy(src: str, dst: str, timecode: str = '') -> bool:
    """Copia vídeo + áudio para um MP4 limpo (sem recodificar). Com `timecode`
    (HH:MM:SS:FF, ou ;FF em drop-frame), grava também uma trilha tmcd."""
    import sys
    ff = _ffmpeg()
    if not ff:
        return False
    import subprocess
    from .core import ffmpeg_timeout
    kw = {'creationflags': 0x08000000} if sys.platform == 'win32' else {}  # sem janela de console
    tmp = part_path(dst)
    ok = False
    try:
        # grava no .part (com -f mp4, já que a extensão .part não diz o formato) e só renomeia no fim
        r = subprocess.run([ff, '-v', 'error', '-y', '-i', src, '-map', '0:v:0', '-map', '0:a?',
                            '-c', 'copy'] + (['-timecode', timecode] if timecode else [])
                           + ['-f', 'mp4', tmp], capture_output=True,
                           timeout=ffmpeg_timeout(os.path.getsize(src)), **kw)
        if r.returncode == 0 and os.path.exists(tmp):
            fsync_path(tmp)
            os.replace(tmp, dst)
            ok = True
    except (subprocess.TimeoutExpired, OSError):
        ok = False
    finally:
        if not ok and os.path.exists(tmp):
            os.remove(tmp)
    return ok


def _has_idr(au: bytes) -> bool:
    p = 0
    while p + 5 <= len(au):
        n = struct.unpack('>I', au[p:p + 4])[0]
        if au[p + 4] & 0x1F == 5:
            return True
        p += 4 + n
    return False


def _ctts_for(prof: Profile, keys: list[int], n: int) -> list[int]:
    ctts, bounds = [], keys + [n + 1]
    for gi in range(len(keys)):
        ln = bounds[gi + 1] - bounds[gi]
        pat = prof.ctts_first if (gi == 0 and keys[0] == 1) else prof.ctts_steady
        ctts += (pat * (ln // max(1, len(pat)) + 1))[:ln] if pat else []
    if keys and keys[0] > 1:
        ctts = [prof.ctts_first[0] if prof.ctts_first else 0] * (keys[0] - 1) + ctts
    return ctts


def write_video_mp4(path: str, samples: list[tuple[int, int, bool]], prof: Profile, dst: str) -> bool:
    """Grava um MP4 só de vídeo com os quadros encontrados (modo sem layout).
    Começa no primeiro IDR; ctts/fps vêm da referência."""
    first = next((i for i, s in enumerate(samples) if s[2]), None)
    if first is None:
        return False
    samples = samples[first:]
    n = len(samples)
    keys = [i + 1 for i, s in enumerate(samples) if s[2]]
    ctts = _ctts_for(prof, keys, n)
    ftyp = struct.pack('>I4s4sI4s4s', 24, b'ftyp', b'isom', 0x200, b'isom', b'avc1')
    data_off = len(ftyp) + 16
    offs, p = [], data_off
    for _, ln, _ in samples:
        offs.append(p)
        p += ln
    moov = Box.parse(prof.moov.serialize())[0]
    moov.children = [c for c in moov.children if c.typ != b'trak' or
                     c.find(b'mdia', b'hdlr').data[8:12] == b'vide']
    v_dur = n * prof.v_delta
    movie_dur = round(v_dur * prof.m_ts / prof.v_ts)
    _set_duration(moov.find(b'mvhd'), 16, 24, movie_dur)
    t = moov.find(b'trak')
    _set_duration(t.find(b'tkhd'), 20, 28, movie_dur)
    elst = t.find(b'edts', b'elst')
    if elst is not None:
        _set_duration(elst, 8, 8, movie_dur)
    _set_duration(t.find(b'mdia', b'mdhd'), 16, 24, v_dur)
    co_t, co_d = _co(offs)
    e = {
        b'stts': _full(0, struct.pack('>III', 1, n, prof.v_delta)),
        b'stsc': _full(0, struct.pack('>IIII', 1, 1, 1, 1)),
        b'stsz': _full(0, struct.pack('>II', 0, n) + b''.join(struct.pack('>I', s[1]) for s in samples)),
        b'stss': _full(0, struct.pack('>I', len(keys)) + b''.join(struct.pack('>I', k) for k in keys)),
        co_t: co_d,
    }
    if ctts:
        r = _rle(ctts)
        e[b'ctts'] = _full(0, struct.pack('>I', len(r)) + b''.join(struct.pack('>II', a, b) for a, b in r))
    _stbl_tables(t.find(b'mdia', b'minf', b'stbl'), e)
    with open(path, 'rb') as src, atomic_write(dst) as out:
        out.write(ftyp)
        out.write(struct.pack('>I4sQ', 1, b'mdat', 16 + p - data_off))
        for off, ln, _ in samples:
            src.seek(off)
            out.write(src.read(ln))
        out.write(moov.serialize())
    return True
