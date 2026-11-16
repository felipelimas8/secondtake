# Second Take

**Recuperação de clipes de câmera corrompidos — dá uma segunda chance ao take.**
*Recovery for corrupted camera clips — [English below](#english).*

O Second Take diagnostica e recupera arquivos de vídeo **Sony XAVC S** (FX3, FX3A, FX30 e similares) danificados por cartão retirado durante a gravação, bateria que acabou, cópia incompleta ou setores ruins. Ele preserva **vídeo, áudio LPCM e metadados** e **nunca altera os arquivos originais**: tudo é gravado como cópia numa pasta de saída.

Desenvolvido pela [Super8 Produtora](https://instagram.com/super8produtora) a partir de um caso real de set, e distribuído como software livre (GPL v3).

## O que ele resolve

| Nível | Situação | O que o Second Take faz | Sufixo da saída |
|---|---|---|---|
| 1 | Início do arquivo corrompido, índice (`moov`) íntegro | Troca o cabeçalho pelo de um clipe bom da mesma câmera e mantém o índice original | `_REPARADO` |
| 1 | Gravação interrompida: sem índice | Reconstrói o índice varrendo os dados brutos, bloco a bloco, usando um clipe de referência como molde | `_RECONSTRUIDO` |
| 2 | Trechos danificados no meio ou no começo | Pula os blocos ilegíveis e retoma no próximo keyframe (IDR), sem artefatos | `_RECONSTRUIDO` |
| 3 | Casos difíceis | Modo **Avançado**: escolha manual da referência, offset dos dados e simulação antes de gravar | — |
| 4 | Nada remonta | Extração bruta: vídeo `.h264`, áudio `.wav` e um MP4 limpo | `_EXTRAIDO` |

O app escolhe o nível automaticamente para cada arquivo e gera um `relatorio_reparo.csv` com diagnóstico, referência usada, pico de áudio e verificação. Ele avisa, por exemplo, quando o áudio já foi gravado em silêncio, que é um problema do set e não do arquivo.

### Como funciona

O XAVC S grava os dados em blocos regulares de cerca de 0,5 s: `[metadados rtmd][G quadros H.264][áudio LPCM]`. Cada quadro começa com um *access unit delimiter* (NAL tipo 9). Com um clipe íntegro da mesma câmera e da mesma configuração como molde (tamanho dos blocos, `avcC`, padrão de GOP e `ctts`), o Second Take reconhece cada bloco no arquivo danificado e reescreve as tabelas de amostras. Os bytes de vídeo e áudio não são recodificados: o resultado é **bit a bit idêntico** ao que a câmera gravou.

## Instalação

**App para macOS:** baixe o `.zip` mais recente em [Releases](../../releases), descompacte e arraste o **Second Take** para Aplicativos. Na primeira vez, clique com o botão direito, escolha **Abrir** e confirme. Enquanto o app não for notarizado pela Apple, esse passo é necessário.

**Pelo código:**
```bash
git clone https://github.com/felipelimas8/secondtake.git
cd secondtake
python3 -m pip install -e .
secondtake-gui            # interface
secondtake --help         # linha de comando
```

Requisitos: Python 3.10+ com Tkinter (o instalador do python.org já inclui). O **ffmpeg** é opcional (`brew install ffmpeg`). Ele é usado para conferir os quadros reparados e montar o MP4 limpo da extração bruta.

## Uso

### Interface
1. **Analisar**: escolhe uma pasta de brutas e examina todos os clipes. **Analisar arquivos…** examina só os clipes escolhidos e busca referências na mesma pasta.
2. Os arquivos reparáveis já vêm selecionados. Clique em **Reparar selecionados**.
3. **Avançado…** abre os controles manuais de um arquivo: modo, referência, offset e **Simular**.

### Linha de comando
```bash
secondtake scan  /Volumes/CARTAO/PRIVATE/M4ROOT/CLIP
secondtake repair C0042.MP4 C0043.MP4 -o ~/Movies/SecondTake --verify
secondtake repair C0042.MP4 -o saida --mode reconstruir --ref C0041.MP4
```

**Dica:** o disco de saída precisa ter permissão de escrita. Discos **NTFS** são somente leitura no macOS, então use outro destino.

## Limitações conhecidas

- **Câmeras validadas:** só a Sony FX3, em XAVC S-I 4K 4:2:2 10-bit (23,98p e 59,94p), com material real. Outras câmeras XAVC S devem funcionar, mas ainda não foram validadas. Relate o resultado numa *issue*.
- **XAVC HS (HEVC)** e outras marcas (Canon, Panasonic, DJI, GoPro, iPhone) ainda não são suportados.
- **Referência obrigatória:** a reconstrução precisa de pelo menos um clipe íntegro da mesma câmera, com a mesma resolução, fps e codec.
- **Último bloco:** numa gravação interrompida, o último bloco (cerca de 0,5 s) costuma ser perdido. Com B-frames, até 2 quadros do fim de um GOP incompleto também se perdem.

## Desenvolvimento

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

A suíte de testes **não usa material de câmera**. O `tests/synth.py` gera clipes sintéticos no layout XAVC S com o ffmpeg (libx264, LPCM, metadados), e os testes simulam cada tipo de dano, conferindo quadro a quadro (`framemd5`) e amostra a amostra que o recuperado é idêntico ao original.

**Nunca envie material de clientes para o repositório**, nem em *issues*. Veja o [CONTRIBUTING.md](CONTRIBUTING.md).

Para gerar o app no macOS: `scripts/build_macos.sh`.

## Licença

Copyright © 2026 Super8 Produtora. Distribuído sob a **GNU General Public License v3.0 ou posterior**: veja [LICENSE](LICENSE). Você pode usar, estudar, modificar e redistribuir. Versões modificadas que forem distribuídas também precisam ter o código aberto sob a GPL.

---

## English

**Second Take** diagnoses and recovers corrupted **Sony XAVC S** clips (FX3, FX3A, FX30 and similar) damaged by a card pulled mid-recording, a dead battery, an incomplete copy or bad sectors. It preserves **video, LPCM audio and metadata**, and **never modifies the originals**: everything is written as a copy to an output folder.

| Level | Damage | What it does |
|---|---|---|
| 1 | Corrupted header, intact `moov` | Swaps in a good header from a clip shot on the same camera |
| 1 | Interrupted recording (no `moov`) | Rebuilds the index by walking the raw XAVC chunks, using a reference clip as template |
| 2 | Damage in the middle or at the start | Skips unreadable chunks and resumes at the next IDR keyframe |
| 3 | Hard cases | **Advanced** dialog: manual reference, data offset and dry-run |
| 4 | Nothing else works | Raw extraction to `.h264` + `.wav` + clean MP4 |

Nothing is re-encoded, so the recovered frames are bit-identical to what the camera wrote. The test suite proves this on synthetic XAVC S clips generated with ffmpeg, so no client footage is needed.

```bash
pip install -e .
secondtake repair /path/to/CLIP -o ~/Movies/SecondTake --verify
secondtake-gui
```

Licensed under GPL-3.0-or-later. The UI and messages are in Portuguese for now, and translations are welcome.
