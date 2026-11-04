"""Turns one recording into text: `python voice_cli.py recording.mp3` prints a JSON list of phrases.
Felixa runs this with the Python that has faster-whisper (the voice environment next to the app), so the app itself needs no speech libraries."""
import json
import os
import sys

from faster_whisper import WhisperModel

model = WhisperModel(os.getenv('FELIXA_WHISPER_MODEL', 'base'), device='cpu', compute_type='int8')
segments, _info = model.transcribe(sys.argv[1])
print(json.dumps([s.text.strip() for s in segments if s.text and s.text.strip()]))
