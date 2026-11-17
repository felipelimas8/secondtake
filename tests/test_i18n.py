"""Idiomas: catálogo completo, mensagens preguiçosas e troca na hora."""
import re

import pytest

from secondtake import core, i18n
from secondtake.i18n import CATALOG, Join, L, t


@pytest.fixture(autouse=True)
def back_to_pt():
    yield
    i18n.set_lang('pt')


def test_catalog_has_both_languages_and_same_placeholders():
    for key, (pt, en) in CATALOG.items():
        assert pt and en, key
        ph = lambda s: sorted(re.findall(r'\{(\w+)', s))  # noqa: E731
        assert ph(pt) == ph(en), f'{key}: {ph(pt)} != {ph(en)}'


def test_every_key_used_in_code_exists():
    import pathlib
    src = pathlib.Path(core.__file__).parent
    used = set()
    for f in src.glob('*.py'):
        used |= set(re.findall(r"""(?:\bt|\bL|\btr)\(\s*['"]([a-z]+\.[a-z_A-Z]+)['"]""", f.read_text(encoding='utf-8')))
    missing = sorted(k for k in used if k not in CATALOG)
    assert not missing, missing
    for st in (core.OK, core.HEADER, core.NO_MOOV, core.EMPTY, core.UNKNOWN):
        assert f'status.{st}' in CATALOG


def test_lazy_message_follows_language():
    m = L('diag.rebuild', secs='6.5', chunks=13, extra=Join(L('diag.rebuild_gaps', n=1, mb='13.5')),
          reason=L('reason.end'))
    i18n.set_lang('pt')
    assert str(m).startswith('reconstrução: 6.5 s recuperáveis')
    assert 'fim da gravação' in m
    i18n.set_lang('en')
    assert str(m).startswith('rebuild: 6.5 s recoverable')
    assert 'end of recording' in m
    assert m == 'rebuild: 6.5 s recoverable (13 chunks; 1 damaged section(s) skipped, 13.5 MB) — end of recording'


def test_clip_detail_retranslates(tmp_path):
    f = tmp_path / 'x.MP4'
    f.write_bytes(b'')
    c = core.diagnose(str(f))
    i18n.set_lang('en')
    assert str(c.detail) == 'file is 0 bytes'
    i18n.set_lang('pt')
    assert str(c.detail) == 'arquivo com 0 bytes'


def test_csv_report_in_english(tmp_path):
    f = tmp_path / 'x.MP4'
    f.write_bytes(b'')
    c = core.diagnose(str(f))
    i18n.set_lang('en')
    rep = tmp_path / 'r.csv'
    core.write_report([c], str(rep))
    head, row = rep.read_text(encoding='utf-8').splitlines()[:2]
    assert head.startswith('file,status,state,action,diagnosis')
    assert ',VAZIO,Empty,' in row and 'file is 0 bytes' in row


def test_init_precedence(monkeypatch):
    monkeypatch.setenv('SECONDTAKE_LANG', 'en')
    assert i18n.init() == 'en'
    assert i18n.init('pt') == 'pt'
    assert t('app.repair') == 'Reparar'


def test_auto_preference_follows_system(monkeypatch, tmp_path):
    monkeypatch.delenv('SECONDTAKE_LANG', raising=False)
    monkeypatch.setattr(i18n, '_config_path', lambda: str(tmp_path / 'config.json'))
    monkeypatch.setattr(i18n, 'system_lang', lambda: 'en')
    assert i18n.preference() == 'auto'
    assert i18n.init() == 'en'                       # sem preferência: segue o sistema
    i18n.set_preference('pt')
    assert i18n.preference() == 'pt' and i18n.init() == 'pt'   # fixo
    i18n.set_preference('auto')
    assert i18n.preference() == 'auto' and i18n.init() == 'en'  # voltou a seguir o sistema


@pytest.mark.parametrize('key', ['app.report_failed', 'app.export_failed', 'meta.diagnosis'])
def test_report_failure_and_details_keys(key):
    """B3 e Detalhes (0.9.11): chaves novas nos dois idiomas, com os mesmos parâmetros."""
    pt, en = CATALOG[key]
    assert pt != en
    if key == 'app.report_failed':
        m = L(key, n=3, err='No space left on device')
        assert str(m).startswith('3 clipe(s) processado(s)') and 'log' in str(m)
        i18n.set_lang('en')
        assert str(m).startswith('3 clip(s) processed') and 'Recovered files are safe' in str(m)
