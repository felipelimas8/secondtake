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


def test_no_official_release_yet_is_not_an_error():
    assert updater.check(force=True, fetch=lambda: None) is None


def test_github_404_means_no_release(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    def fake_urlopen(*a, **k):
        raise urllib.error.HTTPError(updater.API_LATEST, 404, 'Not Found', {}, io.BytesIO(b''))
    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    assert updater.fetch_latest() is None
    assert updater.check(force=True) is None


def test_picks_installer_for_each_platform():
    names = ['SecondTake-0.8.0-macOS-arm64.dmg', 'SecondTake-0.8.0-macOS-arm64.zip',
             'SecondTake-0.8.0-Windows-x64-Setup.exe', 'SecondTake-0.8.0-Windows-x64-portatil.zip']
    assert updater.pick_asset(names, platform='darwin').endswith('.dmg')
    assert updater.pick_asset(names, platform='win32').endswith('Setup.exe')
    assert updater.pick_asset(names, platform='linux') is None
    assert updater.pick_asset(names[:2], platform='win32') is None     # release sem Windows: abre a página


# ---- canal beta

def test_beta_versions_sort_before_final():
    k = updater.version_key
    assert k('0.10.0b1') < k('0.10.0b2') < k('0.10.0') < k('0.10.1b1')
    assert k('v0.10.0-beta.2') == k('0.10.0b2')
    assert k('0.9.3') < k('0.10.0b1')
    assert updater.is_newer('0.10.0-beta.2', '0.10.0b1')
    assert not updater.is_newer('0.10.0-beta.1', '0.10.0b1')


def test_latest_beta_ignores_stable_drafts_and_moving_build():
    rels = [
        {'tag_name': 'v0.9.3', 'prerelease': False},
        {'tag_name': 'beta-build', 'prerelease': True},
        {'tag_name': 'main-build', 'prerelease': True},
        {'tag_name': 'v0.10.0-beta.3', 'prerelease': True, 'draft': True},
        {'tag_name': 'v0.10.0-beta.2', 'prerelease': True},
        {'tag_name': 'v0.10.0-beta.10', 'prerelease': True},
    ]
    assert updater.latest_beta(rels)['tag_name'] == 'v0.10.0-beta.10'
    assert updater.latest_beta(rels[:3]) is None


def test_channel_identity_is_separate():
    from secondtake import channel
    assert channel.BETA
    assert channel.APP_NAME == 'Second Take Beta'
    assert channel.BUNDLE_ID == 'br.com.super8.secondtake.beta'
    assert channel.FILE_PREFIX == 'SecondTakeBeta'
    assert channel.WIN_APP_ID != '{{6E3C2B7A-5D1F-4C8E-9A2B-3F7E1D4C5A60}'
