"""Updating Felixa from inside Felixa. The Mac launcher keeps the app in its own folder and remembers the GitHub version it installed; this does the same
from a button: look for a newer version, copy it over the app's files, and start again on the same address. The workplaces and messages live in the data
folder next to the app and are never touched."""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import config

REPO = os.getenv('FELIXA_UPDATE_REPO', 'FelixaHealth/Felixa-Community-Health')
API = os.getenv('FELIXA_UPDATE_API', f'https://api.github.com/repos/{REPO}/commits/main')
ZIP = os.getenv('FELIXA_UPDATE_ZIP', f'https://github.com/{REPO}/archive/refs/heads/main.zip')
KEEP = ('felixa.db', 'uploads', '.env', '.git', '__pycache__')
_cache = {'at': 0.0, 'remote': '', 'error': ''}
_lock = threading.Lock()


def state_dir() -> Path:
    """The folder the launcher keeps its state in: the parent of the data folder (…/Application Support/Felixa)."""
    return config.data_dir().parent


def installed() -> str:
    try:
        return (state_dir() / 'version').read_text().strip()
    except OSError:
        return ''


def available() -> bool:
    """True when this copy can replace its own files: it sits in a folder the launcher manages (it remembers a version) and the folder is writable."""
    return bool(installed()) and not os.getenv('FELIXA_NO_UPDATE') and os.access(config.BASE_DIR, os.W_OK)


def _get(url, timeout=10):
    """The page at `url`. The Mac's own curl first (it trusts the certificates the Mac trusts; Python from python.org often does not), then Python."""
    import shutil as _sh
    import subprocess
    curl = _sh.which('curl')
    if curl:
        try:
            out = subprocess.run([curl, '-fsSL', '--max-time', str(timeout), '-H', 'Accept: application/vnd.github.sha', '-A', 'Felixa', url], capture_output=True, timeout=timeout + 5)
            if out.returncode == 0 and out.stdout:
                return out.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github.sha', 'User-Agent': 'Felixa'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check(force=False) -> dict:
    """{'current', 'latest', 'newer', 'error'}. Asks GitHub at most every ten minutes."""
    with _lock:
        if force or time.time() - _cache['at'] > 600:
            try:
                sha = ''.join(c for c in _get(API).decode('ascii', 'ignore') if c in '0123456789abcdef')
                _cache.update(at=time.time(), remote=sha if len(sha) >= 7 else '', error='' if len(sha) >= 7 else 'GitHub gave no version.')
            except Exception as e:     # no internet, rate limit, anything: the app goes on as it is
                _cache.update(at=time.time(), remote='', error=f'Could not reach GitHub ({type(e).__name__})')
        cur, new = installed(), _cache['remote']
        return {'current': cur[:7], 'latest': new[:7], 'newer': bool(new and cur and new != cur), 'error': _cache['error']}


def cached_newer() -> bool:
    """Without any network: did the last look find a newer version?"""
    cur, new = installed(), _cache['remote']
    return bool(new and cur and new != cur)


def install(download=None) -> str:
    """Copies the newest files over this copy. Returns the new version. `download` is for tests."""
    sha = check(force=True)
    if not sha['latest']:
        raise RuntimeError(sha['error'] or 'No newer version was found.')
    data = (download or (lambda: _get(ZIP, 120)))()
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(tmp)
        roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
        src = roots[0] if roots else None
        if not src or not (src / 'app.py').exists():
            raise RuntimeError('The download did not look like Felixa.')
        for item in src.iterdir():
            if item.name in KEEP:
                continue
            dest = config.BASE_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        for exe in config.BASE_DIR.glob('*.app/Contents/MacOS/*'):
            try:
                exe.chmod(0o755)
            except OSError:
                pass
    full = _cache['remote']
    (state_dir() / 'version').write_text(full)
    return full[:7]


def restart_soon(delay=1.2):
    """Starts the app again in the same process (same address, same data) a moment after the page that asked has been sent."""
    def go():
        time.sleep(delay)
        os.chdir(config.BASE_DIR)
        os.execv(sys.executable, [sys.executable, str(config.BASE_DIR / 'app.py')])
    threading.Thread(target=go, daemon=True).start()


def start_background_check():
    if available():
        threading.Thread(target=check, daemon=True).start()
