"""Turns a voice recording into text, on this computer.

It uses faster-whisper when that package is installed (pip install faster-whisper). Without it the voice option says so instead of failing
halfway. The speech model is downloaded the first time it is used, then kept; FELIXA_WHISPER_MODEL picks its size ("base" by default).
Nothing is sent to an outside service.
"""
import importlib.util
import os

AUDIO_EXTENSIONS = ('.mp3', '.wav', '.m4a', '.ogg', '.webm', '.flac', '.mp4')


class VoiceUnavailable(RuntimeError):
    pass


def available() -> bool:
    return importlib.util.find_spec('faster_whisper') is not None


def transcribe(path):
    """The spoken sentences of an audio file, one string per phrase. Raises VoiceUnavailable when speech-to-text is not installed."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise VoiceUnavailable('Voice needs the speech-to-text add-on, which is not installed on this computer. '
                               'Install it with: pip install faster-whisper') from None
    model = WhisperModel(os.getenv('FELIXA_WHISPER_MODEL', 'base'), device='cpu', compute_type='int8')
    segments, _info = model.transcribe(path)
    return [s.text.strip() for s in segments if s.text and s.text.strip()]
