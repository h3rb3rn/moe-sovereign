#!/bin/bash
# Start Ollama in CPU-only mode, pull the embedding model once, then serve.
# OLLAMA_NUM_GPU=0 is set via docker-compose environment — no GPU is claimed.
set -e

MODEL="${MOE_EMBED_MODEL:-nomic-embed-text}"

# Start Ollama server in background
ollama serve &
SERVER_PID=$!

# Wait for the REST API to become available
echo "[moe-embed] Waiting for Ollama server (PID $SERVER_PID)..."
until curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; do
    sleep 2
done
echo "[moe-embed] Server up."

# Pull model — idempotent if already cached in the mounted volume
if ! ollama list 2>/dev/null | grep -q "^${MODEL}"; then
    echo "[moe-embed] Pulling ${MODEL} ..."
    ollama pull "${MODEL}"
    echo "[moe-embed] ${MODEL} ready."
else
    echo "[moe-embed] ${MODEL} already present."
fi

# Hand control back to the server process
wait "$SERVER_PID"
