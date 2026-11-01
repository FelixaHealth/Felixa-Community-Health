@echo off
cd /d %~dp0
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not installed. Install Ollama first.
  pause
  exit /b 1
)
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
rem A line of .env that begins with # is a comment. Any other line is NAME=value and the whole rest of the line is the value, so a comment can not follow a value.
if exist .env (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do set "%%a=%%b"
)
if "%FELIXA_LOCAL_MODEL%"=="" set FELIXA_LOCAL_MODEL=qwen2.5:7b
rem Look for the whole name, then the space that ends the first column of the list: another size of the same model, qwen2.5:3b for qwen2.5:7b, is not the model asked for.
ollama list | findstr /b /l /c:"%FELIXA_LOCAL_MODEL% " >nul
if errorlevel 1 ollama pull %FELIXA_LOCAL_MODEL%
py app.py
pause
