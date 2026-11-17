# Changelog

## 0.9.8 — 2026-11-17
- **Windows de volta**: a 0.9.7 saiu só para Mac, porque um teste novo usava um nome de pasta inválido no Windows (`|`) e derrubou o build do Windows. O teste foi corrigido; o app em si não muda em relação à 0.9.7.
- **Build do Mac**: `scripts/build_macos.sh` passa a montar e assinar tudo numa pasta temporária no disco interno, e funciona com o projeto num SSD exFAT ou numa pasta do iCloud. A assinatura é feita de dentro para fora (bibliotecas, frameworks e por último o app), sem o `codesign --deep`, e conferida com `codesign --verify --strict`.

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
