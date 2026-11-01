"""Start the private model server and the Felixa Community Health web app; download the model on first launch.

With FELIXA_NO_LOCAL_MODEL=1 only the web app starts: no model server, no download, a much smaller machine. Claude (through FELIXA_ANTHROPIC_API_KEY)
is then the only engine, so the launcher refuses to start without that key."""
import os, signal, subprocess, sys, threading, time, urllib.request
if not os.getenv("FELIXA_PASSWORD"):
    sys.exit("FELIXA_PASSWORD is required for online deployment")
if len(os.environ["FELIXA_PASSWORD"]) < 12:
    sys.exit("FELIXA_PASSWORD must be at least 12 characters for online deployment")
if not os.getenv("RAILWAY_VOLUME_MOUNT_PATH") and os.getenv("RAILWAY_ENVIRONMENT_ID"):
    sys.exit("Attach a persistent Railway volume at /data before deploying")
claude_only = os.getenv("FELIXA_NO_LOCAL_MODEL", "").strip() == "1"
if claude_only and not (os.getenv("FELIXA_ANTHROPIC_API_KEY", "").strip() or os.getenv("ANTHROPIC_API_KEY", "").strip()):
    sys.exit("FELIXA_NO_LOCAL_MODEL=1 needs FELIXA_ANTHROPIC_API_KEY: with no local model, Claude is the only engine")
model = os.getenv("FELIXA_LOCAL_MODEL", "").strip() or "qwen2.5:3b"    # hosted default; a blank variable counts as unset
os.environ["FELIXA_LOCAL_MODEL"] = model    # the web app (a child process) asks for this variable, so it must name the model pulled below
ollama = None if claude_only else subprocess.Popen(["ollama", "serve"])
web = None
stopping = False
def stop(*args):
    global stopping
    stopping = True
    for process in (web, ollama):
        if process is not None and process.poll() is None:
            process.terminate()
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    for _ in range(0 if claude_only else 60):
        if ollama.poll() is not None or stopping:
            raise RuntimeError("Model server stopped")
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1):
                break
        except OSError:
            time.sleep(1)
    else:
        if not claude_only:
            raise RuntimeError("Model server failed to start")
    def prepare_model():
        old_model = os.getenv("FELIXA_REMOVE_OLD_MODEL", "")
        if old_model and old_model != model:
            subprocess.run(["ollama", "rm", old_model])
        result = subprocess.run(["ollama", "pull", model])
        if result.returncode:
            print("Model download failed; restart the service to retry", flush=True)
    if not claude_only:
        threading.Thread(target=prepare_model, daemon=True).start()
    web = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", os.getenv("PORT", "8080")])
    while not stopping and web.poll() is None and (ollama is None or ollama.poll() is None):
        time.sleep(1)
    if not stopping:
        raise RuntimeError("A required service stopped unexpectedly")
finally:
    stop()
    for process in (web, ollama):
        if process is not None:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
