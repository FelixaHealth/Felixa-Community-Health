#!/bin/bash
# Starts Felixa Community Health on this Mac using YOUR Claude login (Claude Code). No API key and no Ollama are needed.
cd "$(dirname "$0")"

fail() {
  echo
  echo "$1"
  echo
  printf "Press any key to close this window."
  read -n 1 -s -r
  echo
  exit 1
}

command -v claude >/dev/null 2>&1 || fail "Claude Code was not found on this Mac.
Install it from https://claude.com/claude-code, then run   claude   once in a terminal and sign in. After that, run this file again."

# The app needs Python 3.10 or newer. A new Mac's built-in python3 is older, so look for a newer one.
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || fail "Python 3.10 or newer is needed, and this Mac only has an older one.
Install the latest Python from https://www.python.org/downloads/ , then run this file again."

# A virtual environment made earlier with an older Python is no good: start it again.
if [ -d ".venv" ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then rm -rf .venv; fi
[ -d ".venv" ] || "$PY" -m venv .venv || fail "Could not create the Python environment."
source .venv/bin/activate
pip install -r requirements.txt || fail "Could not install the requirements. Check your internet connection and try again."

if [ -f ".env" ]; then set -a; source .env; set +a; fi
export FELIXA_NO_LOCAL_MODEL=1
unset ANTHROPIC_API_KEY FELIXA_ANTHROPIC_API_KEY

# macOS keeps port 5000 for AirPlay, so use the first free port from 5050.
PORT=$(python pick_port.py) || fail "No free port was found."
export PORT
echo
echo "Starting Felixa Community Health at http://127.0.0.1:$PORT"
echo "Your browser opens in a few seconds. Press Control+C in this window to stop the app."
(sleep 4; open "http://127.0.0.1:$PORT") &
python app.py
code=$?
if [ "$code" -ne 0 ] && [ "$code" -ne 130 ]; then fail "The app stopped with an error (see the lines above). Copy them and send them to me."; fi
