"""Turns a voice recording into text, on this computer.

It uses faster-whisper when that package is installed (pip install faster-whisper). Without it the voice option says so instead of failing
halfway. The speech model is downloaded the first time it is used, then kept; FELIXA_WHISPER_MODEL picks its size ("base" by default).
Nothing is sent to an outside service.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.ogg', '.webm', '.flac', '.mp4')


class VoiceUnavailable(RuntimeError):
    pass


def helper_python():
    """The Python of the voice environment (faster-whisper installed in it), kept next to the app's own data: FELIXA_VOICE_PYTHON, or <state folder>/venv-voice."""
    given = os.getenv('FELIXA_VOICE_PYTHON')
    if given:
        return given if Path(given).exists() else None
    import config
    for folder in (config.data_dir().parent, Path.home() / 'Library' / 'Application Support' / 'Felixa'):      # next to the data, or where the Mac app keeps its state
        found = folder / 'venv-voice' / 'bin' / 'python'
        if found.exists():
            return str(found)
    return None


def _in_process() -> bool:
    try:
        return importlib.util.find_spec('faster_whisper') is not None
    except ValueError:                      # a stand-in module without a spec (tests)
        return 'faster_whisper' in sys.modules


def available() -> bool:
    return _in_process() or helper_python() is not None


def transcribe(path):
    """The spoken sentences of an audio file, one string per phrase. Raises VoiceUnavailable when speech-to-text is not installed."""
    if not _in_process():
        py = helper_python()
        if not py:
            raise VoiceUnavailable('Voice needs the speech-to-text add-on, which is not installed on this computer. '
                                   'Install it with: pip install faster-whisper') from None
        script = str(Path(__file__).resolve().parent / 'voice_cli.py')
        base = [py, script, path]
        done = subprocess.run(base, capture_output=True, text=True, timeout=3600)
        if done.returncode != 0 and sys.platform == 'darwin' and os.path.exists('/usr/bin/arch'):      # the app may run translated (Intel) while the voice Python is native
            done = subprocess.run(['/usr/bin/arch', '-arm64'] + base, capture_output=True, text=True, timeout=3600)
        if done.returncode != 0:
            raise VoiceUnavailable('The speech-to-text add-on could not read that recording: ' + (done.stderr.strip().splitlines() or ['unknown error'])[-1][:200])
        return json.loads(done.stdout.strip().splitlines()[-1])
    from faster_whisper import WhisperModel
    model = WhisperModel(os.getenv('FELIXA_WHISPER_MODEL', 'base'), device='cpu', compute_type='int8')
    segments, _info = model.transcribe(path)
    return [s.text.strip() for s in segments if s.text and s.text.strip()]
