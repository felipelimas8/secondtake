"""Gera um pacote de clipes de TESTE (sintéticos, sem material de cliente) para experimentar o Second Take.

Uso: python3 scripts/make_samples.py [pasta_de_saída]   (requer ffmpeg com libx264)
Saída: SecondTake-amostras-teste.zip
"""
import os
import random
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tests'))
import synth  # noqa: E402

LEIAME = """SECOND TAKE — clipes de teste / test clips
==========================================

Arquivos SINTÉTICOS no formato Sony XAVC S (padrão de cores + tom de áudio).
Não são material de câmera real. Abra a pasta inteira no Second Take
("Analisar pasta" ou arraste a pasta para a janela).

  C0001.MP4  íntegro — usado como referência        | intact — used as reference
  C0002.MP4  cabeçalho danificado → "Reparar"         | damaged header → "Repair"
  C0003.MP4  gravação interrompida, sem índice        | interrupted recording, no index
             → "Reconstruir"                          |   → "Rebuild"
  C0004.MP4  sem índice + trecho danificado no meio   | no index + damaged section
             → "Reconstruir", pula o trecho           |   → "Rebuild", skips it
  C0005.MP4  dados aleatórios → nada recuperável      | random data → nothing recoverable

Os resultados (_REPARADO / _RECONSTRUIDO) devem abrir no DaVinci Resolve,
VLC ou IINA com imagem e áudio. (O QuickTime não abre H.264 4:2:2.)
"""


def scramble(path, start, length, seed):
    rnd = random.Random(seed)
    with open(path, 'r+b') as f:
        f.seek(start)
        f.write(bytes(rnd.getrandbits(8) for _ in range(length)))


def main(out_dir='dist'):
    work = os.path.join(out_dir, 'amostras')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    p = lambda n: os.path.join(work, n)  # noqa: E731
    synth.make_xavc(p('C0001.MP4'), seconds=4, seed=1)
    synth.make_xavc(p('C0002.MP4'), seconds=6, seed=2)
    scramble(p('C0002.MP4'), 0, synth.HDR, 1)
    i3 = synth.make_xavc(p('C0003.MP4'), seconds=6, seed=3)
    with open(p('C0003.MP4'), 'r+b') as f:
        f.truncate(i3['vid_offs'][-2] + 5000)
    i4 = synth.make_xavc(p('C0004.MP4'), seconds=6, seed=4)
    with open(p('C0004.MP4'), 'r+b') as f:
        f.truncate(i4['moov_pos'])
    scramble(p('C0004.MP4'), i4['vid_offs'][3] + 300, 4000, 2)
    with open(p('C0005.MP4'), 'wb') as f:
        f.write(random.Random(5).randbytes(1_500_000))
    with open(p('LEIA-ME.txt'), 'w', encoding='utf-8') as f:
        f.write(LEIAME)
    z = os.path.join(out_dir, 'SecondTake-amostras-teste.zip')
    with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
        for n in sorted(os.listdir(work)):
            zf.write(os.path.join(work, n), os.path.join('SecondTake-amostras-teste', n))
    shutil.rmtree(work)
    print('OK:', z)


if __name__ == '__main__':
    main(*sys.argv[1:])
