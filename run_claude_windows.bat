@echo off
rem Starts Felixa Community Health using YOUR Claude login (Claude Code). No API key and no Ollama are needed.
cd /d %~dp0
where claude >nul 2>nul
if errorlevel 1 (
  echo Claude Code was not found on this computer.
  echo Install it from https://claude.com/claude-code, then run   claude   once in a terminal and sign in. After that, run this file again.
  pause
  exit /b 1
)
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
set FELIXA_NO_LOCAL_MODEL=1
set ANTHROPIC_API_KEY=
set FELIXA_ANTHROPIC_API_KEY=
echo Open http://127.0.0.1:5000 in your browser. Press Control+C here to stop the app.
py app.py
pause
