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

- Python 3.10+, sem dependências obrigatórias além da biblioteca padrão.
- Toda correção ou formato novo vem acompanhado de teste em `tests/`. Use o `tests/synth.py` para gerar clipes sintéticos com o dano que você quer cobrir.
- Rode `python3 -m pytest` antes de abrir o PR.
- Mensagens da interface em português. Se for traduzir, mantenha as chaves num lugar só.

## Licença

Ao contribuir, você concorda em licenciar sua contribuição sob a GPL v3 ou posterior.
