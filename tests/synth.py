"""Gera clipes sintéticos no layout Sony XAVC S para os testes.

Nenhum material de câmera real é necessário: o ffmpeg (libx264) codifica um
padrão de teste e um tom, e este módulo monta o arquivo como uma FX3 grava:

    ftyp | uuid | free (até 262128) | mdat 64-bit
    por bloco: [rtmd G×R][vídeo G access units com AUD][LPCM big-endian]
    zeros de preenchimento | moov (vídeo avc1 + áudio twos + metadados rtmd)

O moov é escrito aqui de forma independente do código do Second Take, para que
os testes não validem o programa contra ele mesmo.
"""
from __future__ import annotations

import os
import struct
import subprocess
import tempfile

HDR = 262144


def box(t: bytes, payload: bytes) -> bytes:
    return struct.pack('>I4s', 8 + len(payload), t) + payload


def full(t: bytes, payload: bytes, version: int = 0, flags: int = 0) -> bytes:
    return box(t, struct.pack('>I', (version << 24) | flags) + payload)


def _children(buf, s, e):
    p = s
    while p + 8 <= e:
        size, t = struct.unpack('>I4s', buf[p:p + 8])
        h = 8
        if size == 1:
            size = struct.unpack('>Q', buf[p + 8:p + 16])[0]
            h = 16
        yield t, p + h, p + size, p
        p += size


def _find(buf, s, e, *path):
    for name in path:
        for t, cs, ce, _ in _children(buf, s, e):
            if t == name:
                s, e = cs, ce
                break
        else:
            return None
    return s, e


def _source_samples(mov: str):
    """Lê do .mov do ffmpeg: AUs do vídeo, flags de keyframe, ctts, stsd, elst e PCM."""
    data = open(mov, 'rb').read()
    ms, me = _find(data, 0, len(data), b'moov')
    out = {}
    for t, s, e, _ in _children(data, ms, me):
        if t != b'trak':
            continue
        hd = _find(data, s, e, b'mdia', b'hdlr')
        kind = data[hd[0] + 8:hd[0] + 12]
        st = _find(data, s, e, b'mdia', b'minf', b'stbl')
        tab = {tt: (cs, ce) for tt, cs, ce, _ in _children(data, *st)}

        def ents(tag, w):
            if tag not in tab:
                return []
            cs, _ = tab[tag]
            n = struct.unpack('>I', data[cs + 4:cs + 8])[0]
            return [struct.unpack('>' + 'I' * w, data[cs + 8 + 4 * w * i:cs + 8 + 4 * w * (i + 1)]) for i in range(n)]

        cs, _ = tab[b'stsz']
        ssize, n = struct.unpack('>II', data[cs + 4:cs + 12])
        sizes = [ssize] * n if ssize else list(struct.unpack(f'>{n}I', data[cs + 12:cs + 12 + 4 * n]))
        if b'co64' in tab:
            cs, _ = tab[b'co64']
            k = struct.unpack('>I', data[cs + 4:cs + 8])[0]
            offs = list(struct.unpack(f'>{k}Q', data[cs + 8:cs + 8 + 8 * k]))
        else:
            offs = [x[0] for x in ents(b'stco', 1)]
        stsc = ents(b'stsc', 3)
        # expande amostras -> offsets
        per_chunk = []
        for i, (first, spc, _) in enumerate(stsc):
            last = stsc[i + 1][0] - 1 if i + 1 < len(stsc) else len(offs)
            per_chunk += [spc] * (last - first + 1)
        pos, si = [], 0
        for ci, o in enumerate(offs):
            for _ in range(per_chunk[ci]):
                pos.append(o)
                o += sizes[si]
                si += 1
        if kind == b'vide':
            out['frames'] = [data[p:p + z] for p, z in zip(pos, sizes)]
            keys = {x[0] for x in ents(b'stss', 1)}
            out['keys'] = [i + 1 in keys for i in range(n)] if keys else [True] * n
            ctts = []
            for c, v in ents(b'ctts', 2):
                ctts += [v] * c
            out['ctts'] = ctts
            sd = tab[b'stsd']
            out['stsd'] = data[sd[0]:sd[1]]
            el = _find(data, s, e, b'edts', b'elst')
            out['media_time'] = struct.unpack('>i', data[el[0] + 12:el[0] + 16])[0] if el else 0
        elif kind == b'soun':
            out['pcm'] = b''.join(data[p:p + z] for p, z in zip(pos, sizes))
    return out


def make_xavc(path: str, seconds: float = 4.0, seed: int = 1, G: int = 12, gop: int = 24,
              meta_size: int = 1024, pad: int = 1 << 20, bframes: int = 2, codec: str = 'h264') -> dict:
    """Cria um clipe sintético XAVC S 23,976p + LPCM 48 kHz estéreo.
    codec='hevc' gera um XAVC HS de teste (libx265, Main 4:2:2 10, trilha hvc1 + hvcC); o layout
    dos blocos é o mesmo do XAVC S, porque o da câmera ainda não foi medido num arquivo real.
    Retorna informações úteis aos testes (n_chunks, frames, offsets dos blocos)."""
    gop_opts = f'keyint={gop}:min-keyint={gop}:scenecut=0:bframes={bframes}:b-adapt=0:open-gop=0'
    if codec == 'hevc':
        venc = ['-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv422p10le', '-tag:v', 'hvc1',
                '-x265-params', f'aud=1:{gop_opts}:log-level=error']
    else:
        venc = ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                '-x264-params', f'aud=1:{gop_opts}']
    with tempfile.TemporaryDirectory() as td:
        mov = os.path.join(td, 'src.mov')
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y',
            '-f', 'lavfi', '-i', f'testsrc2=size=320x180:rate=24000/1001:duration={seconds}',
            '-f', 'lavfi', '-i', f'sine=frequency={220 + 110 * seed}:sample_rate=48000:duration={seconds}',
            *venc, '-c:a', 'pcm_s16be', '-ac', '2', '-f', 'mov', mov], check=True)
        src = _source_samples(mov)
    frames, keys, ctts = src['frames'], src['keys'], src['ctts']
    n_chunks = len(frames) // G
    frames, keys, ctts = frames[:n_chunks * G], keys[:n_chunks * G], ctts[:n_chunks * G]
    v_ts, v_delta, a_rate, m_ts = 24000, 1001, 48000, 48000
    spc = G * v_delta * a_rate // v_ts            # 24024 amostras por bloco
    a_chunk = spc * 4
    pcm = src['pcm'].ljust(n_chunks * a_chunk, b'\0')[:n_chunks * a_chunk]

    ftyp = box(b'ftyp', b'XAVC' + struct.pack('>I', 0x01001f20) + b'XAVCmp42iso2')
    uuid = box(b'uuid', bytes(range(16)) + b'\x00' * 124)
    free_len = HDR - 16 - len(ftyp) - len(uuid)
    free = struct.pack('>I4s', free_len, b'free') + b'\0' * (free_len - 8)
    body = bytearray()
    meta_offs, vid_offs, aud_offs = [], [], []
    for ci in range(n_chunks):
        meta_offs.append(HDR + len(body))
        for k in range(G):
            body += (b'\x00\x1c\x01\x00' + struct.pack('>I', ci * G + k)).ljust(meta_size, b'\x5a')
        vid_offs.append(HDR + len(body))
        for f in frames[ci * G:(ci + 1) * G]:
            body += f
        aud_offs.append(HDR + len(body))
        body += pcm[ci * a_chunk:(ci + 1) * a_chunk]
    data_end = HDR + len(body)
    mdat = struct.pack('>I4sQ', 1, b'mdat', data_end + pad - (HDR - 16))  # inclui o preenchimento

    n = len(frames)
    v_dur = n * v_delta
    mv_dur = v_dur * m_ts // v_ts
    matrix = struct.pack('>9I', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)

    def tkhd(tid, w=0, h=0, vol=0):
        return full(b'tkhd', struct.pack('>IIIII', 0, 0, tid, 0, mv_dur) + b'\0' * 8 +
                    struct.pack('>hhhH', 0, 0, vol, 0) + matrix + struct.pack('>II', w << 16, h << 16), flags=7)

    def elst(mt):
        return box(b'edts', full(b'elst', struct.pack('>IIiI', 1, mv_dur, mt, 0x10000)))

    def mdhd(ts, dur):
        return full(b'mdhd', struct.pack('>IIIIHH', 0, 0, ts, dur, 0x55c4, 0))

    def hdlr(t):
        return full(b'hdlr', struct.pack('>I4s', 0, t) + b'\0' * 12 + b'\0')

    dinf = box(b'dinf', full(b'dref', struct.pack('>I', 1) + full(b'url ', b'', flags=1)))

    def co(offs):
        if max(offs) > 0xFFFFFFFF:
            return full(b'co64', struct.pack('>I', len(offs)) + b''.join(struct.pack('>Q', o) for o in offs))
        return full(b'stco', struct.pack('>I', len(offs)) + b''.join(struct.pack('>I', o) for o in offs))

    def rle(vals):
        out = []
        for v in vals:
            if out and out[-1][1] == v:
                out[-1][0] += 1
            else:
                out.append([1, v])
        return out

    stsc_g = full(b'stsc', struct.pack('>IIII', 1, 1, G, 1))
    stts_v = full(b'stts', struct.pack('>III', 1, n, v_delta))
    v_stbl = box(b'stbl', box(b'stsd', src['stsd']) + stts_v +
                 (full(b'ctts', struct.pack('>I', len(rle(ctts))) +
                       b''.join(struct.pack('>II', a, b) for a, b in rle(ctts))) if ctts else b'') +
                 stsc_g +
                 full(b'stsz', struct.pack('>II', 0, n) + b''.join(struct.pack('>I', len(f)) for f in frames)) +
                 co(vid_offs) +
                 full(b'stss', struct.pack('>I', sum(keys)) + b''.join(struct.pack('>I', i + 1) for i, k in enumerate(keys) if k)))
    vtrak = box(b'trak', tkhd(1, 320, 180) + elst(src['media_time']) + box(b'mdia', mdhd(v_ts, v_dur) + hdlr(b'vide') + box(
        b'minf', full(b'vmhd', b'\0' * 8, flags=1) + dinf + v_stbl)))
    a_samples = spc * n_chunks
    twos = box(b'twos', b'\0' * 6 + struct.pack('>HHHIHHHHI', 1, 0, 0, 0, 2, 16, 0, 0, a_rate << 16))
    a_stbl = box(b'stbl', full(b'stsd', struct.pack('>I', 1) + twos) +
                 full(b'stts', struct.pack('>III', 1, a_samples, 1)) +
                 full(b'stsc', struct.pack('>IIII', 1, 1, spc, 1)) +
                 full(b'stsz', struct.pack('>II', 4, a_samples)) + co(aud_offs))
    atrak = box(b'trak', tkhd(2, vol=0x100) + elst(0) + box(b'mdia', mdhd(a_rate, a_samples) + hdlr(b'soun') + box(
        b'minf', full(b'smhd', b'\0' * 4) + dinf + a_stbl)))
    rtmd = box(b'rtmd', b'\0' * 6 + struct.pack('>H', 1))
    m_stbl = box(b'stbl', full(b'stsd', struct.pack('>I', 1) + rtmd) + stts_v + stsc_g +
                 full(b'stsz', struct.pack('>II', meta_size, n)) + co(meta_offs))
    mtrak = box(b'trak', tkhd(3) + elst(0) + box(b'mdia', mdhd(v_ts, v_dur) + hdlr(b'meta') + box(
        b'minf', full(b'nmhd', b'') + dinf + m_stbl)))
    mvhd = full(b'mvhd', struct.pack('>IIIIIH', 0, 0, m_ts, mv_dur, 0x10000, 0x100) + b'\0' * 10 +
                matrix + b'\0' * 24 + struct.pack('>I', 4))
    moov = box(b'moov', mvhd + vtrak + atrak + mtrak)
    with open(path, 'wb') as out:
        out.write(ftyp + uuid + free + mdat)
        out.write(body)
        out.write(b'\0' * pad)          # preenchimento antes do moov, como nas Sony
        out.write(moov)
    return {'path': path, 'n_chunks': n_chunks, 'n_frames': n, 'G': G, 'gop': gop,
            'meta_offs': meta_offs, 'vid_offs': vid_offs, 'aud_offs': aud_offs,
            'data_end': data_end, 'moov_pos': data_end + pad, 'a_chunk': a_chunk}
