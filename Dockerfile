FROM ollama/ollama:latest
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PATH="/opt/venv/bin:$PATH" FELIXA_DATA_DIR=/data OLLAMA_MODELS=/data/models OLLAMA_HOST=127.0.0.1:11434 FELIXA_REQUIRE_AUTH=1
EXPOSE 8080
ENTRYPOINT ["python", "start_online.py"]
