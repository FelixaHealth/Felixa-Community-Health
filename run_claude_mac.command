#!/bin/bash
# Starts Felixa Community Health on this Mac using YOUR Claude login (Claude Code). No API key and no Ollama are needed.
cd "$(dirname "$0")"
if ! command -v claude >/dev/null 2>&1; then
  echo "Claude Code was not found on this Mac."
  echo "Install it from https://claude.com/claude-code, then run   claude   once in a terminal and sign in. After that, run this file again."
  exit 1
fi
if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -r requirements.txt
if [ -f ".env" ]; then set -a; source .env; set +a; fi
export FELIXA_NO_LOCAL_MODEL=1
unset ANTHROPIC_API_KEY FELIXA_ANTHROPIC_API_KEY
echo "Opening http://127.0.0.1:5000 in your browser. Press Control+C here to stop the app."
(sleep 4; open http://127.0.0.1:5000) &
python app.py
