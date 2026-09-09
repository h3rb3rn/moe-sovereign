#!/usr/bin/env bash
# ==============================================================================
# Sovereign Judge 27B Import & GPU Offload Optimization Script
# ==============================================================================
set -euo pipefail

DEST_DIR="/opt/ollama/models"
MODEL_FILE="${DEST_DIR}/sovereign-judge-27b-Q4_K_M.gguf"
MODELFILE_SRC="/opt/deployment/moe-sovereign/moe-infra/models/Modelfile.sovereign-judge-27b"
MODELFILE_DST="${DEST_DIR}/Modelfile.sovereign-judge-27b"

mkdir -p "${DEST_DIR}"

echo "📋 Copying Modelfile definition..."
cp -f "${MODELFILE_SRC}" "${MODELFILE_DST}"

if [ ! -f "${MODEL_FILE}" ]; then
    echo "📥 Sovereign Judge 27B GGUF file missing at ${MODEL_FILE}."
    echo "💡 Run rsync from LUMI-G scratch or specify local path:"
    echo "   rsync -avzP hornphil@efp.lumi.csc.fi:/scratch/project_465003058/hornphil/moe-sovereign/sovereign-judge-27b-Q4_K_M.gguf ${MODEL_FILE}"
    exit 1
fi

echo "🚀 Building Ollama model 'sovereign-judge:27b' with 258k context window..."
docker exec -i ollama ollama create sovereign-judge:27b -f /root/.ollama/Modelfile.sovereign-judge-27b

echo "✅ sovereign-judge:27b created successfully in Ollama!"
echo "💡 GPU Allocation Strategy:"
echo "   - OLLAMA_SCHED_SPREAD=false ensures RTX 3060 (12GB) GPUs fill 100% first."
echo "   - Excess layers and 258k KV-cache overflow spill onto RTX 2060 GPUs."
