"""Verificação de atualizações pelo GitHub Releases.

Consulta anônima à API pública do GitHub (sem enviar dados do usuário), no
máximo uma vez por dia quando automática. Só considera versões oficiais
(tags vX.Y.Z, sem pré-releases). Não baixa nem instala nada sozinho: avisa e
abre o instalador da versão nova.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.request

from . import __version__, i18n

REPO = 'felipelimas8/secondtake'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
PAGE_LATEST = f'https://github.com/{REPO}/releases/latest'
DAY = 24 * 3600


def parse_version(v: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2). Partes não numéricas viram 0."""
    nums = re.findall(r'\d+', v or '')
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def _ssl_context():
    # o Python do python.org e os apps do PyInstaller nem sempre acham os certificados do sistema
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_latest(timeout: float = 6.0) -> dict:
    """Última versão oficial: {'version', 'page', 'dmg', 'notes'}. Levanta exceção se falhar."""
    req = urllib.request.Request(API_LATEST, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'SecondTake/{__version__}',
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        data = json.load(r)
    tag = data.get('tag_name', '')
    dmg = next((a.get('browser_download_url') for a in data.get('assets', [])
                if a.get('name', '').lower().endswith('.dmg')), None)
    return {
        'version': tag.lstrip('vV'),
        'page': data.get('html_url') or PAGE_LATEST,
        'dmg': dmg,
        'notes': (data.get('body') or '').strip(),
    }


def check(force: bool = False, fetch=fetch_latest) -> dict | None:
    """Retorna a versão nova (dict) se houver, senão None.

    Sem `force`, respeita a preferência de verificação automática, o intervalo
    de 24 h e a versão que o usuário pediu para pular.
    """
    cfg = i18n.load_config()
    if not force:
        if not cfg.get('auto_update', True):
            return None
        if time.time() - cfg.get('last_update_check', 0) < DAY:
            return None
    info = fetch()
    i18n.save_config(last_update_check=time.time())
    if not is_newer(info['version']):
        return None
    if not force and cfg.get('skip_version') == info['version']:
        return None
    return info


def skip(version: str):
    i18n.save_config(skip_version=version)


def auto_enabled() -> bool:
    return bool(i18n.load_config().get('auto_update', True))


def set_auto(enabled: bool):
    i18n.save_config(auto_update=bool(enabled))
