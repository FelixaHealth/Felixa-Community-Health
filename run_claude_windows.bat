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
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is needed. Install the latest Python from https://www.python.org/downloads/ and run this file again.
  pause
  exit /b 1
)
if not exist .venv py -3 -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
if errorlevel 1 (
  echo Could not install the requirements. Check your internet connection and try again.
  pause
  exit /b 1
)
set FELIXA_NO_LOCAL_MODEL=1
set ANTHROPIC_API_KEY=
set FELIXA_ANTHROPIC_API_KEY=
for /f %%p in ('python pick_port.py') do set PORT=%%p
echo Starting Felixa Community Health at http://127.0.0.1:%PORT%
echo Open that address in your browser. Press Control+C here to stop the app.
start "" http://127.0.0.1:%PORT%
python app.py
pause
