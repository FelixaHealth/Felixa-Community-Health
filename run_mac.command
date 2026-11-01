#!/bin/bash
cd "$(dirname "$0")"
if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama is not installed. Install Ollama first."
  exit 1
fi
if [ ! -d ".venv" ]; then python3 -m venv .venv; fi
source .venv/bin/activate
pip install -r requirements.txt
if [ -f ".env" ]; then set -a; source .env; set +a; fi
MODEL=${FELIXA_LOCAL_MODEL:-qwen2.5:7b}
# Look for the whole name, as the app does (no tag means :latest): another size of the same model, qwen2.5:3b for qwen2.5:7b, is not the model asked for.
case "$MODEL" in *:*) WANT=$MODEL ;; *) WANT=$MODEL:latest ;; esac
if ! ollama list | awk 'NR>1 {print $1}' | grep -qxF "$WANT"; then ollama pull "$MODEL"; fi
python app.py
