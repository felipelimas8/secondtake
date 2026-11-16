import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

# os testes de recuperação conferem mensagens em português
os.environ['SECONDTAKE_LANG'] = 'pt'
from secondtake import i18n  # noqa: E402

i18n.set_lang('pt')
