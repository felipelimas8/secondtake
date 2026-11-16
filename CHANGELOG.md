# Changelog

## 0.4.0 — 2026-11-16
Primeira versão pública (antes chamada "XAVC Rescue").
- **Nível 1:** reparo de cabeçalho com `moov` íntegro; reconstrução do `moov` de gravações interrompidas a partir de um clipe de referência.
- **Nível 2:** resync de blocos danificados no meio ou no início, retomando sempre em keyframe IDR.
- **Nível 3:** janela Avançado (referência manual, offset dos dados, simulação).
- **Nível 4:** extração bruta (`.h264` + `.wav` + MP4 limpo).
- "Analisar arquivos…" para examinar só os clipes escolhidos.
- CLI `secondtake` e interface `secondtake-gui`.
- Suíte de testes com clipes sintéticos XAVC S (sem material de câmera).
