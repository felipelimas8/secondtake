"""Verificação de atualizações pelo GitHub Releases.

Consulta anônima à API pública do GitHub (sem enviar dados do usuário), no
máximo uma vez por dia quando automática. O app estável só considera versões
oficiais (tags vX.Y.Z, sem pré-releases); o Second Take Beta só considera as
pré-releases beta (tags vX.Y.Z-beta.N). Não baixa nem instala nada sozinho: avisa e
abre o instalador da versão nova.
"""
from __future__ import annotations

import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request

from . import __version__, i18n
from .channel import BETA

REPO = 'felipelimas8/secondtake'
API_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
API_LIST = f'https://api.github.com/repos/{REPO}/releases?per_page=40'
PAGE_LATEST = f'https://github.com/{REPO}/releases/latest'
DAY = 24 * 3600


def parse_version(v: str) -> tuple[int, ...]:
    """'v0.10.2' -> (0, 10, 2). Partes não numéricas viram 0."""
    nums = re.findall(r'\d+', v or '')
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - len(nums[:3]))


_BETA_RE = re.compile(r'(?:b|beta)[.\-]?(\d+)\s*$', re.I)


def version_key(v: str) -> tuple[int, ...]:
    """Chave de ordenação que entende betas: 0.10.0b1 < 0.10.0b2 < 0.10.0.

    Aceita a forma do __version__ ('0.10.0b1') e a da tag ('v0.10.0-beta.1').
    """
    m = _BETA_RE.search(v or '')
    base = (v or '')[:m.start()] if m else v
    return parse_version(base) + ((0, int(m.group(1))) if m else (1, 0))


def is_beta(v: str) -> bool:
    return bool(_BETA_RE.search(v or ''))


def is_newer(latest: str, current: str = __version__) -> bool:
    return version_key(latest) > version_key(current)


def _ssl_context():
    # o Python do python.org e os apps do PyInstaller nem sempre acham os certificados do sistema
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'SecondTake/{__version__}',
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return json.load(r)


def _info(data: dict) -> dict:
    assets = data.get('assets', [])
    return {
        'version': data.get('tag_name', '').lstrip('vV'),
        'page': data.get('html_url') or PAGE_LATEST,
        'download': pick_asset([a.get('name', '') for a in assets],
                               {a.get('name', ''): a.get('browser_download_url') for a in assets}),
        'notes': (data.get('body') or '').strip(),
    }


def latest_beta(releases: list[dict]) -> dict | None:
    """Entre as releases do GitHub, a pré-release beta mais nova (ignora rascunhos e a beta-build móvel)."""
    betas = [r for r in releases
             if r.get('prerelease') and not r.get('draft') and is_beta(r.get('tag_name', ''))]
    return max(betas, key=lambda r: version_key(r['tag_name']), default=None)


def fetch_latest(timeout: float = 6.0) -> dict | None:
    if BETA:
        try:
            r = latest_beta(_get(API_LIST, timeout))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        return _info(r) if r else None
    return _fetch_stable(timeout)


def _fetch_stable(timeout: float = 6.0) -> dict | None:
    """Última versão oficial: {'version', 'page', 'download', 'notes'}.

    Retorna None quando ainda não há nenhuma release oficial publicada (o GitHub
    responde 404 se só existem pré-releases/rascunhos, ou se o repositório é privado).
    Outros erros de rede levantam exceção.
    """
    try:
        data = _get(API_LATEST, timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    return _info(data)


def pick_asset(names: list[str], urls: dict | None = None, platform: str | None = None):
    """Escolhe o instalador certo para o sistema: .dmg no macOS, -Setup.exe no Windows."""
    platform = platform or sys.platform
    if platform == 'darwin':
        want = lambda n: n.lower().endswith('.dmg')  # noqa: E731
    elif platform == 'win32':
        want = lambda n: n.lower().endswith('setup.exe')  # noqa: E731
    else:
        return None
    name = next((n for n in names if want(n)), None)
    if name is None:
        return None
    return (urls or {}).get(name, name)


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
    if not info or not is_newer(info['version']):
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
