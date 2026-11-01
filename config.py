"""Paths, limits and model settings for Felixa Community Health.

Environment variables are read when called, not at import, so a test or a second instance can point
at its own data directory and model server without re-importing anything.
"""
import os
from pathlib import Path

PRODUCT = 'Felixa Community Health'
PRODUCT_LONG = 'Felixa Community Health'
VERSION = 'bei-1.0'

BASE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 15_000_000         # per file
MAX_UPLOAD_FILES = 5                  # files of that size one upload may carry: its body is held to this many plus MAX_FORM_BYTES
MAX_FORM_BYTES = 2_000_000            # body of any other request (sign-in, onboarding steps), and what rides along with files or a paste
RATING_BATCH = 3                      # units per model call when rating the 13 signals (small models need small batches)
UPLOAD_EXTENSIONS = ('.csv', '.json', '.txt', '.md', '.log', '.pdf', '.docx')
UPLOAD_PREFIX = 'upload-'             # start of the name of the temporary copy made while an uploaded file is read


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def data_dir() -> Path:
    """The folder that holds the database. One the app creates is owner-only (the messages are in it); an existing one is left as it is."""
    path = Path(os.getenv('FELIXA_DATA_DIR', str(BASE_DIR)))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / 'felixa.db'


def upload_dir() -> Path:
    path = data_dir() / 'uploads'
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def sweep_uploads() -> int:
    """Deletes the temporary upload copies a process left behind when it was killed while reading a file (an out-of-memory kill,
    for example). Run at start-up, when nothing is being read yet: one instance owns a data directory. Only files the app made
    (UPLOAD_PREFIX) are touched. Returns how many were removed."""
    removed = 0
    for path in upload_dir().glob(UPLOAD_PREFIX + '*'):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def ollama_url() -> str:
    return os.getenv('FELIXA_OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')


def model_name() -> str:
    return os.getenv('FELIXA_LOCAL_MODEL', 'qwen2.5:7b')


def model_timeout() -> int:
    return _int('FELIXA_MODEL_TIMEOUT', 480)


def num_threads():
    """Ollama picks its own thread count unless FELIXA_NUM_THREAD is set (the hosted deploy used 4)."""
    raw = os.getenv('FELIXA_NUM_THREAD', '')
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def max_units() -> int:
    """Upper bound on communication units stored per analysis."""
    return _int('FELIXA_MAX_UNITS', 5000)


def max_rated_units() -> int:
    """Units sent through the 13-signal rating engine. Larger uploads are sampled evenly."""
    return _int('FELIXA_MAX_RATED_UNITS', 300)


def local_model_enabled() -> bool:
    """False when the app runs without a model of its own (FELIXA_NO_LOCAL_MODEL=1, the light hosted setup): Claude is then the only engine."""
    return os.getenv('FELIXA_NO_LOCAL_MODEL', '').strip() != '1'


def anthropic_key() -> str:
    """The Claude API key the server uses for the Claude engines, or '' when there is none. With one set, people can pick Claude for free:
    the server's key pays for it. The key is read from the environment only, and is never stored, logged or shown."""
    return (os.getenv('FELIXA_ANTHROPIC_API_KEY') or os.getenv('ANTHROPIC_API_KEY') or '').strip()


def anthropic_url() -> str:
    return os.getenv('FELIXA_ANTHROPIC_URL', 'https://api.anthropic.com').rstrip('/')
