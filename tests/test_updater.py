"""Verificação de atualizações (sem rede: a consulta ao GitHub é simulada)."""

import pytest

from secondtake import __version__, i18n, updater


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(i18n, '_config_path', lambda: str(tmp_path / 'config.json'))


def fake(version):
    return lambda: {'version': version, 'page': 'https://x/release', 'dmg': 'https://x/a.dmg', 'notes': ''}


def test_version_compare():
    assert updater.parse_version('v0.10.2') == (0, 10, 2)
    assert updater.is_newer('0.10.0', '0.9.9')
    assert not updater.is_newer('0.6.0', '0.6.0')
    assert not updater.is_newer('v0.5.9', '0.6.0')


def test_reports_newer_version_once_a_day():
    info = updater.check(fetch=fake('99.0.0'))
    assert info and info['version'] == '99.0.0'
    assert updater.check(fetch=fake('99.0.0')) is None          # já verificou hoje
    assert updater.check(force=True, fetch=fake('99.0.0'))      # manual ignora o intervalo


def test_same_version_is_up_to_date():
    assert updater.check(force=True, fetch=fake(__version__)) is None


def test_skip_and_disable():
    updater.skip('99.0.0')
    assert updater.check(fetch=fake('99.0.0')) is None
    i18n.save_config(last_update_check=0)
    assert updater.check(fetch=fake('99.1.0'))                  # versão diferente da pulada aparece
    updater.set_auto(False)
    i18n.save_config(last_update_check=0)
    assert updater.check(fetch=fake('100.0.0')) is None
    assert updater.check(force=True, fetch=fake('100.0.0'))     # manual funciona mesmo desligado


def test_network_error_propagates_only_when_called():
    def boom():
        raise OSError('sem rede')
    with pytest.raises(OSError):
        updater.check(force=True, fetch=boom)
