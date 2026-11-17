# Changelog

## 0.9.11 — 2026-11-17
- **XAVC HS (H.265), primeiro passo**: o app reconhece clipes HEVC (`hvc1`/`hev1`) e mostra no Detalhes o perfil, o nível, a amostragem 4:2:2 e o 10-bit (lidos do `hvcC`). A prévia de 3 quadros e a **troca de cabeçalho** (cabeçalho corrompido, índice íntegro) funcionam, com preferência por uma referência também H.265.
- **XAVC HS sem índice**: a reconstrução e a extração bruta ainda não entendem H.265. Esses clipes aparecem como "XAVC HS (H.265) sem índice: reconstrução ainda não suportada" e ficam fora do lote, em vez de passar pelo leitor H.264. O codec é detectado também pelo conteúdo do arquivo quando o índice e o XML sumiram, e uma referência H.265 nunca é usada para reconstruir um clipe H.264.
- **Build reproduzível**: dependências do build e dos testes travadas com hash em `requirements-build.txt` (as mesmas versões da 0.9.10: CustomTkinter 6.0.0, PyInstaller 6.22.3, tkinterdnd2 0.7.0). Actions do GitHub fixadas por SHA; só o job que publica tem permissão de escrita.
- **Teste de fumaça**: antes de publicar, o GitHub abre o app empacotado de verdade no macOS (a partir do `.zip` distribuído) e no Windows (`SECONDTAKE_SMOKE=1`, `scripts/smoke_test.py`).
- **Release inteira ou nada**: macOS e Windows são gerados em paralelo e a release só é criada quando os dois passam. Antes, uma falha no Windows deixava a release como Latest só com os arquivos do Mac (foi o que aconteceu na 0.9.7).
- Teste de fumaça: confere que a janela foi exibida (`state`) em vez de `winfo_ismapped`, que é sempre falso no runner Windows do GitHub (serviço sem área de trabalho) e barrava a release.
- **FX30: clipes íntegros apareciam como "cabeçalho corrompido"**. O diagnóstico procurava o `mdat` numa posição fixa (262128, layout da FX3), mas a FX30 grava os dados a partir de 131072 e ainda põe uma box `meta` depois do `moov`. Agora o cabeçalho é validado pela estrutura (`ftyp` no início, boxes encadeadas até o `mdat`, índice apontando para dentro dele), o que vale para FX3, FX30, FX3A e outros layouts íntegros. Clipes FX30 sadios voltam a servir de referência.
- **Troca de cabeçalho segura entre câmeras diferentes**: o início dos dados de cada clipe vem do próprio índice, e só serve de referência um clipe com o mesmo layout. Antes, com uma FX3 na pasta, o reparo de uma FX30 copiaria 256 KiB de cabeçalho por cima dos primeiros 128 KiB de dados da cópia. Sem referência compatível, o app avisa ("sem referência com o mesmo layout") em vez de gerar um arquivo estragado; forçar pela linha de comando também é recusado.
- **SHA256SUMS.txt** em cada release e na pré-release da `main`, para conferir os downloads.
- Empacotamento: o PyInstaller passa a levar `PIL._tkinter_finder` explicitamente (o hook do Pillow o deixava de fora; no Linux o ícone do cabeçalho quebrava a abertura).
- **Relatório que não grava não some mais em silêncio** (auditoria B3): se o CSV do fim do lote não puder ser gravado (disco cheio, sem permissão, pasta removida), o erro vai para o log com o caminho e o traceback, e a barra de status mostra um aviso em laranja. Os clipes já recuperados não são afetados. Com "Salvar junto dos originais", uma pasta com problema não impede os relatórios das outras. Exportar CSV ganhou o mesmo tratamento.
- **Mesmo tamanho na tabela e no Detalhes**: os dois (e os avisos de espaço livre) usam unidades decimais, como o Finder e os cartões (1 MB = 1.000.000 bytes). Antes a tabela dividia por 1024 e mostrava 768.7 MB para um clipe que o Detalhes mostrava como 806.0 MB.
- **Detalhes do clipe**: painel 20 px mais largo, para formatos como `AVC200_3840_2160_H422P@L52` caberem numa linha; o **diagnóstico completo** aparece no topo dos metadados (na tabela ele é cortado na borda); quando os metadados não cabem na altura da janela, a área rola, com a barra aparecendo só nesse caso (a prévia continua fixa no topo).
- **Último recurso sem referência**: quando não há referência válida e o clipe só pode ser extraído, selecionar a linha habilita o botão Reparar direto, sem passar pelo Avançado. A extração bruta continua fora da seleção automática, e a barra de status avisa "selecione a linha para extrair".
- README atualizado (arquitetura, metadados/timecode/prévia, testes, build).

## 0.9.10 — 2026-11-17
- **Tela de apresentação**: ao abrir, aparece uma tela com o ícone, o nome e a versão, nas cores do tema do sistema. Fica pelo menos 1,2 s na tela (sem somar ao tempo de carregamento) e some com um fade curto; um clique nela pula a espera.
- **Título da janela**: só "Second Take". A versão continua no cabeçalho e no Sobre.

## 0.9.9 — 2026-11-17
- **Prévia no Detalhes**: três quadros do clipe (10 %, 50 % e 90 %) no topo do painel, gerados em segundo plano pelo ffmpeg. Funciona com clipes íntegros, com cabeçalho danificado (os quadros são lidos direto pelo índice) e com o arquivo recuperado, que aparece logo depois do reparo. Em S-Log3 a imagem vem sem LUT, e o painel avisa.
- **Datas do arquivo preservadas**: o recuperado fica com a data de modificação (e de acesso) do clipe original, então a ilha de edição ordena pela hora da filmagem e não pela hora do reparo.
- **Timecode na extração bruta**: o MP4 limpo ganha uma trilha de timecode com o LTC do XML da câmera (com drop-frame em 29,97/59,94), desde que o começo da gravação tenha sido recuperado.
- **Referência pela assinatura técnica**: o app compara modelo da câmera, codec XAVC, fps de gravação e de captura (S&Q), resolução e áudio, lidos do XML da Sony ou do índice do arquivo, e testa primeiro a referência idêntica. Quando ela explica o clipe inteiro, as outras nem são lidas, o que deixa a análise mais rápida em pastas grandes.

## 0.9.8 — 2026-11-17
- **Windows de volta**: a 0.9.7 saiu só para Mac, porque um teste novo usava um nome de pasta inválido no Windows (`|`) e derrubou o build do Windows. O teste foi corrigido; o app em si não muda em relação à 0.9.7.

## 0.9.7 — 2026-11-17
- **Sem arquivo cortado com nome de pronto**: o resultado é gravado primeiro num arquivo oculto `.part` e só recebe o nome final (`_REPARADO`, `_RECONSTRUIDO`, `_EXTRAIDO`) depois de gravado por inteiro no disco. Se o disco encher ou o SSD desconectar no meio, o `.part` é apagado e nada fica com cara de concluído.
- **Nada é sobrescrito**: reparar de novo na mesma pasta de saída cria `_2`, `_3`… em vez de apagar o resultado anterior. O XML de câmera e os arquivos da extração (`.h264`, `.wav`, `.MP4`) recebem o mesmo número do clipe.
- **Salvar junto dos originais**: nova chave no rodapé, logo abaixo da pasta de saída. Ligada, cada recuperado vai para a subpasta **Second Take** dentro da pasta do próprio clipe (com um relatório em cada uma), mesmo analisando arquivos de cartões diferentes. A escolha fica salva. Na linha de comando: `--beside` no lugar de `-o`.
- **Nunca ao lado do original**: se a pasta de saída escolhida for a própria pasta dos clipes, o app também grava na subpasta **Second Take**. Ao analisar a pasta de novo, essa subpasta é ignorada.
- **Espaço livre conferido por modo**: a extração bruta passa a exigir cerca de 3× o tamanho do clipe (antes, 1,05×, o que podia acabar com o disco cheio no meio).
- **Verificação não trava mais**: o ffprobe e o ffmpeg têm limite de tempo proporcional ao tamanho do arquivo. Se o ffprobe não responder, a verificação é cancelada com um aviso e o arquivo recuperado continua salvo.
- **Cancelar**: durante o reparo, o botão Reparar vira **Cancelar**. O clipe em andamento é interrompido e descartado (inclusive a verificação com ffprobe); os já reparados ficam, e o relatório é gravado com o que foi feito.
- **Saída dentro da origem**: a checagem passou a comparar os caminhos reais (atalhos, maiúsculas/minúsculas, `/CLIP` × `/CLIPS`). A linha de comando também avisa.
- **Análise mais rápida em pastas grandes**: o índice (`moov`) é procurado primeiro só no último 1 MB do arquivo (antes, sempre 16 MB), e o clipe de referência é lido uma vez só, mesmo quando serve a vários clipes.
- **Segurança**: o relatório CSV neutraliza nomes de arquivo que o Excel/Numbers leriam como fórmula (`=`, `+`, `-`, `@`); o `config.json` é gravado de forma atômica e protegido contra gravações simultâneas; o XML da câmera é lido com `defusedxml` (nova dependência), que recusa entidades maliciosas.
- **Mac**: os avisos de acesso a cartões/SSDs externos, volumes de rede e às pastas Mesa, Documentos e Transferências passam a explicar, em português e inglês, por que o app precisa do acesso.

## 0.9.6 — 2026-11-16
- Mac: o item **Sobre o Second Take** aparece sempre no menu do app, qualquer que seja a versão do Python/Tk usada no build (na 0.9.5 ele ainda faltava nos builds com Tk 8.6).

## 0.9.5 — 2026-11-16
- Mac: o item **Sobre o Second Take** voltou ao menu do app quando o app é gerado com Python/Tk 9 (Homebrew), que não cria esse item sozinho.
- (A 0.9.4 foi retirada: saiu por engano com arquivos da versão beta.)

## 0.9.3 — 2026-11-16
- **Arquivos .RSV**: o app passa a aceitar direto o arquivo temporário que as câmeras Sony deixam quando a gravação é interrompida (bateria, cartão removido, superaquecimento). Ele aparece como "Sem índice" e sai reconstruído como `_RECONSTRUIDO.MP4`. Funciona na análise de pasta, na escolha de arquivos e no arrastar e soltar.

## 0.9.2 — 2026-11-16
- Instalador do Mac: atalho **Abrir Ajustes de Segurança**, que abre direto Privacidade e Segurança para clicar em "Abrir Mesmo Assim".
- README com imagem de capa.

## 0.9.1 — 2026-11-16
- Clipes de teste nas releases e imagem de prévia para links.

## 0.9.0 — 2026-11-16
- **Arrastar e soltar**: solte clipes ou uma pasta em qualquer lugar da janela para analisar. A área da lista se destaca ao arrastar. Mac e Windows.
- **Detalhes do clipe**: painel lateral com câmera, número de série, data, timecode, resolução, codec (perfil, nível, 4:2:2/4:2:0, 8/10-bit), formato XAVC, quadros/s (e de captura, em S&Q), duração, bitrate do vídeo, perfil de cor (S-Log3/S-Gamut3.Cine) e áudio. Também no relatório CSV. Lidos do índice do arquivo e do XML da Sony; funciona até em clipes danificados.
- **Clipes de teste**: cada release traz `SecondTake-amostras-teste.zip`, com 5 clipes sintéticos (íntegro, cabeçalho danificado, sem índice, trecho danificado e lixo) para experimentar o app sem material real. Gerados por `scripts/make_samples.py`.
- Instalador do Mac com um só "Leia-me · Read me.txt" bilíngue.

## 0.8.0 — 2026-11-16
- **Windows**: instalador `SecondTake-…-Windows-x64-Setup.exe` (Inno Setup, em português e inglês, instala só para o usuário, sem senha de administrador, com atalho no menu Iniciar e desinstalador) e versão portátil `.zip`.
- No Windows: menu **Arquivo** com Ajustes (Ctrl+,) e Sair; ícone próprio na janela e no executável; ffmpeg encontrado também em winget, Chocolatey, Scoop e `C:\ffmpeg`; sem janelas de console piscando ao verificar; idioma do Windows detectado.
- A verificação de atualizações baixa o instalador certo para cada sistema (`.dmg` no Mac, `Setup.exe` no Windows).
- "Como instalar em outro PC" com as instruções do SmartScreen.
- GitHub Actions gera e publica os arquivos do Windows junto com os do Mac, nas releases oficiais e na prévia "Última versão (main)".

## 0.7.1 — 2026-11-16
- Atualizações: quando não há versão oficial visível (repositório privado ou só pré-releases), o app considera que está atualizado em vez de mostrar "HTTP Error 404".
- CI: testes completos só em Linux; o macOS roda testes + build do `.dmg` uma vez por push (fila menor).

## 0.7.0 — 2026-11-16
- **Verificação de atualizações**: ao abrir (no máximo uma vez por dia) o app consulta a última versão oficial no GitHub e, se houver uma nova, mostra uma faixa com **Baixar** (abre o `.dmg` novo), **Agora não** e **Pular esta versão**.
- **Second Take > Verificar atualizações…** para checar na hora.
- Ajustes (⌘,): opção para desligar a verificação automática.
- Sem nenhuma versão oficial publicada (ou com o repositório privado), a verificação considera o app atualizado em vez de mostrar erro.
- A consulta é anônima (API pública do GitHub) e só considera versões oficiais `v*`, nunca a prévia `main-build`.

## 0.6.0 — 2026-11-16
- **Português e inglês**, no menu **Idioma** da barra de menus ou em **Ajustes…** (⌘,). O padrão é **Automático**, que segue o idioma do macOS; escolher um idioma deixa ele fixo. A troca é na hora (inclusive dos diagnósticos já feitos).
- Diagnósticos, relatório CSV e linha de comando (`--lang pt|en`) também nos dois idiomas. No CSV, a coluna `status` mantém o código fixo para scripts, e `status_label` traz o nome traduzido.
- Menu **Ajuda** com página do projeto, relatar problema e instruções de instalação para colegas.
- Instalador bilíngue: fundo com os passos em PT e EN, mais "How to open.txt".
- Alinhamento: rótulos e valores da janela Sobre alinhados pela primeira linha; títulos das colunas da tabela alinhados à esquerda também no macOS.

## 0.5.0 — 2026-11-16
- **Interface nova** em CustomTkinter, que segue o modo claro/escuro do macOS:
  - cabeçalho com origem;
  - cartões de resumo (analisados, íntegros, reparáveis, último recurso);
  - filtro "Problemas/Todos" e busca por nome;
  - tabela com status e ação;
  - estado vazio com as duas formas de começar;
  - rodapé com saída, progresso e uma única ação principal ("Reparar N clipes").
- Linhas íntegras ficam discretas; o que precisa de atenção se destaca.
- A extração bruta não entra mais na seleção automática: é último recurso, escolhido pelo usuário.
- Verificação de espaço livre na saída antes de reparar.
- Duplo clique numa linha abre o Avançado.
- Janelas Sobre e Avançado redesenhadas.
- **Instalador `.dmg`**: janela com o ícone do app, atalho para Aplicativos, os passos para liberar o app no Gatekeeper e o arquivo "Como abrir.txt".
- **Ícone próprio**: claquete "TAKE 2", no app, no Dock, na janela Sobre e no volume do DMG.
- Sobre > **Como instalar em outro Mac**: copia o link do instalador e as instruções para mandar a colegas.

## 0.4.1 — 2026-11-16
- Janela **Sobre** própria: o que o app faz, formatos suportados, autoria, licença, links do projeto e estado do ffprobe; botão para copiar as informações para uma *issue*.
- Copyright e categoria do app registrados no Info.plist (aparecem em "Obter Informações" no Finder).
- README: instrução do Gatekeeper para versões recentes do macOS.
- `build_macos.sh` compatível com o bash 3.2 do macOS.

## 0.4.0 — 2026-11-16
Primeira versão pública (antes chamada "XAVC Rescue").
- **Nível 1:** reparo de cabeçalho com `moov` íntegro; reconstrução do `moov` de gravações interrompidas a partir de um clipe de referência.
- **Nível 2:** resync de blocos danificados no meio ou no início, retomando sempre em keyframe IDR.
- **Nível 3:** janela Avançado (referência manual, offset dos dados, simulação).
- **Nível 4:** extração bruta (`.h264` + `.wav` + MP4 limpo).
- "Analisar arquivos…" para examinar só os clipes escolhidos.
- CLI `secondtake` e interface `secondtake-gui`.
- Suíte de testes com clipes sintéticos XAVC S (sem material de câmera).
