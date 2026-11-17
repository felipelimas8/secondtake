# Contribuindo com o Second Take

Obrigado por ajudar! Algumas regras para manter o projeto útil e seguro.

## Material de câmera e privacidade

- **Nunca** envie vídeos de clientes, nem trechos, para o repositório, *issues* ou *pull requests*.
- Para relatar um arquivo que não recuperou, rode `secondtake scan ARQUIVO --csv diag.csv` e anexe o CSV, que contém só a estrutura, sem imagem nem áudio. Informe também:
  - câmera e firmware;
  - codec, resolução, fps e áudio (canais e bits);
  - o que aconteceu no set (cartão retirado, bateria, cópia interrompida…).
- Se for possível gravar um **clipe de teste seu** com a mesma configuração e o mesmo tipo de dano, ele ajuda muito. Combine o envio por fora do GitHub.

## Código

- Python 3.10+. Ambiente de desenvolvimento igual ao do build: `pip install --require-hashes -r requirements-build.txt` e `pip install --no-deps -e .` (veja o README). Não troque versão de dependência sem regenerar o lockfile e testar o app empacotado.
- Toda correção ou formato novo vem acompanhado de teste em `tests/`. Use o `tests/synth.py` para gerar clipes sintéticos com o dano que você quer cobrir (`codec='hevc'` para XAVC HS).
- Rode `python3 -m pytest` antes de abrir o PR.
- Todo texto de interface, diagnóstico, CSV e linha de comando entra em português e inglês no `src/secondtake/i18n.py`. O `test_i18n` acusa chave faltando.

## Licença

Ao contribuir, você concorda em licenciar sua contribuição sob a GPL v3 ou posterior.
