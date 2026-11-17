"""Canal de distribuição do Second Take: estável ou beta.

Este arquivo é o único ponto que muda entre os branches:
  main -> CHANNEL = 'stable'  ("Second Take")
  beta -> CHANNEL = 'beta'    ("Second Take Beta")

O app Beta tem nome, bundle id, ícone, pasta de ajustes e log próprios, então
pode ficar instalado ao lado da versão estável sem que uma interfira na outra.
A variável de ambiente SECONDTAKE_CHANNEL força o canal (usada nos testes).
"""
import os

CHANNEL = os.environ.get('SECONDTAKE_CHANNEL') or 'beta'
BETA = CHANNEL == 'beta'

APP_NAME = 'Second Take Beta' if BETA else 'Second Take'
FILE_PREFIX = 'SecondTakeBeta' if BETA else 'SecondTake'          # nomes dos instaladores
BUNDLE_ID = 'br.com.super8.secondtake.beta' if BETA else 'br.com.super8.secondtake'
ICON_PNG = 'icon_beta.png' if BETA else 'icon.png'
ICON_ICO = 'icon_beta.ico' if BETA else 'icon.ico'
# AppId do Inno Setup: diferente para o Beta não desinstalar/substituir a estável
WIN_APP_ID = ('{{9B1D4E2F-7A3C-4F6B-8D5E-2C1A0F9E8B71}' if BETA
              else '{{6E3C2B7A-5D1F-4C8E-9A2B-3F7E1D4C5A60}')


if __name__ == '__main__':
    # usado pelos scripts de build: CHAVE=valor (com --sh, entre aspas para o eval do bash)
    import shlex
    import sys
    q = shlex.quote if '--sh' in sys.argv else str
    for k in ('CHANNEL', 'APP_NAME', 'FILE_PREFIX', 'BUNDLE_ID', 'ICON_ICO', 'WIN_APP_ID'):
        print(f'{k}={q(globals()[k])}')
