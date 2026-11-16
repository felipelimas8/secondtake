# Changelog

## 0.7.0 — 2026-11-16
- **Verificação de atualizações**: ao abrir (no máximo uma vez por dia) o app consulta a última versão oficial no GitHub e, se houver uma nova, mostra uma faixa com **Baixar** (abre o `.dmg` novo), **Agora não** e **Pular esta versão**.
- **Second Take > Verificar atualizações…** para checar na hora.
- Ajustes (⌘,): opção para desligar a verificação automática.
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
