#!/bin/bash
# scripts/n04rtx_ollama_import.sh
# Auf N04-RTX ausführen: lädt Q4_K_M GGUF von HuggingFace herunter
# und bindet es als "qwen3-planner:q4_k_m" in den ollama-rgtx Container ein.
#
# Voraussetzung: GGUF ist auf https://huggingface.co/h3rb3rn/qwen3-planner-sft-GGUF
# Usage: bash scripts/n04rtx_ollama_import.sh

set -euo pipefail

HF_USER="h3rb3rn"
HF_REPO="qwen3-planner-sft-GGUF"
GGUF_FILE="qwen3-planner-sft-Q4_K_M.gguf"
GGUF_URL="https://huggingface.co/${HF_USER}/${HF_REPO}/resolve/main/${GGUF_FILE}"

MODEL_NAME="qwen3-planner"
MODEL_TAG="q4_k_m"
OLLAMA_CONTAINER="ollama-rgtx"

WORK_DIR="/tmp/qwen3_planner_import"
mkdir -p "$WORK_DIR"

echo "========================================"
echo "N04-RTX Ollama Import — Qwen3 Planner"
echo "HF repo   : ${HF_USER}/${HF_REPO}"
echo "GGUF      : ${GGUF_FILE}"
echo "Model     : ${MODEL_NAME}:${MODEL_TAG}"
echo "Container : ${OLLAMA_CONTAINER}"
echo "========================================"

# ── Step 1: GGUF von HuggingFace herunterladen ────────────────────────────────
GGUF_LOCAL="${WORK_DIR}/${GGUF_FILE}"
if [ -f "$GGUF_LOCAL" ]; then
    echo "[$(date +%H:%M:%S)] GGUF bereits vorhanden ($(du -sh ${GGUF_LOCAL} | cut -f1)), überspringe Download."
else
    echo "[$(date +%H:%M:%S)] Lade GGUF herunter (~4.7 GB) ..."
    HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || cat /home/philipp/.cache/huggingface/token 2>/dev/null || echo '')"
    if [ -n "$HF_TOKEN" ]; then
        curl -fL --progress-bar -H "Authorization: Bearer ${HF_TOKEN}" \
            -o "$GGUF_LOCAL" "$GGUF_URL"
    else
        curl -fL --progress-bar -o "$GGUF_LOCAL" "$GGUF_URL"
    fi
    echo "[$(date +%H:%M:%S)] Download fertig: $(du -sh ${GGUF_LOCAL} | cut -f1)"
fi

# ── Step 2: Modelfile erstellen ───────────────────────────────────────────────
MODELFILE="${WORK_DIR}/Modelfile"
cat > "$MODELFILE" << 'MODELFILE_CONTENT'
FROM /models/qwen3-planner-sft-Q4_K_M.gguf

PARAMETER num_ctx 8192
PARAMETER temperature 0.0
PARAMETER top_p 1.0
PARAMETER repeat_penalty 1.0

SYSTEM """You are the orchestrator of MoE Sovereign, a Mixture-of-Experts AI system.
Decompose the user request into 1–4 subtasks. Each subtask is routed to a specialist expert or tool.

MANDATORY: Output ONLY a JSON array. No text, no markdown, no explanation outside the array. Experts receive only their own task description — NOT the original query. Write complete, self-contained task descriptions. Extract all numerical constraints (model sizes, voltages, doses, bitrates, MTU values …) into task descriptions as IMMUTABLE_CONSTANTS so experts cannot hallucinate defaults.

──────────────────────────────────────────────────────────────────
LLM EXPERT CATEGORIES
──────────────────────────────────────────────────────────────────
"general"            General questions, explanations, summaries
"technical_support"  Troubleshooting, installation, config, DevOps, networking (NOT arithmetic)
"code_reviewer"      Code generation, review, debugging, refactoring
"math"               Mathematical proofs, derivations, theoretical mathematics (NOT arithmetic)
"data_analyst"       Data analysis, statistics interpretation, ML model selection
"science"            Physics, chemistry, biology, research methodology
"creative_writer"    Stories, poems, creative content, worldbuilding
"medical_consult"    Medical questions, symptoms, drugs (NOT diagnoses)
"legal_advisor"      Legal interpretation — ALWAYS after legal_get_paragraph for §-queries
"translation"        Text translation between languages
"agentic_coder"      Long-running coding agents, file system access, multi-step automation
"dynamic"            Niche domain expert (Bauphysik, GMP/Pharma, Seeschifffahrt, Geotechnik …)
                     REQUIRES "domain" field, e.g. {"task": "...", "category": "dynamic", "domain": "Bauphysik/GEG"}

MCP TOOL CATEGORIES
"search_and_summarize"  Web search + summarisation
"precision_tools"    ALL arithmetic, unit conversions, financial calculations, statistics
"legal_get_paragraph" Retrieve law paragraph text (§) — ALWAYS before legal_advisor
"agentic_search"     Deep research loops with web access

Output format: [{"task": "...", "category": "...", "domain": "..." (if dynamic)}]"""
MODELFILE_CONTENT

echo "[$(date +%H:%M:%S)] Modelfile erstellt."

# ── Step 3: GGUF in Container mounten und Modell erstellen ───────────────────
echo "[$(date +%H:%M:%S)] Kopiere GGUF in Container-Volume ..."

# Finde das Ollama-Models-Verzeichnis des Containers
CONTAINER_MODELS_DIR=$(docker inspect "$OLLAMA_CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/root/.ollama"}}{{.Source}}{{end}}{{end}}' 2>/dev/null \
    || echo "")

if [ -n "$CONTAINER_MODELS_DIR" ]; then
    MODELS_DEST="${CONTAINER_MODELS_DIR}/models_import"
    mkdir -p "$MODELS_DEST"
    cp "$GGUF_LOCAL" "${MODELS_DEST}/${GGUF_FILE}"
    GGUF_IN_CONTAINER="/root/.ollama/models_import/${GGUF_FILE}"
else
    # Fallback: direkt in /tmp im Container
    docker cp "$GGUF_LOCAL" "${OLLAMA_CONTAINER}:/tmp/${GGUF_FILE}"
    GGUF_IN_CONTAINER="/tmp/${GGUF_FILE}"
fi

# Modelfile anpassen (FROM-Pfad auf Container-internen Pfad)
sed -i "s|FROM /models/.*|FROM ${GGUF_IN_CONTAINER}|" "$MODELFILE"
docker cp "$MODELFILE" "${OLLAMA_CONTAINER}:/tmp/Modelfile_qwen3_planner"

echo "[$(date +%H:%M:%S)] Erstelle Ollama-Modell ${MODEL_NAME}:${MODEL_TAG} ..."
docker exec "$OLLAMA_CONTAINER" \
    ollama create "${MODEL_NAME}:${MODEL_TAG}" \
    -f /tmp/Modelfile_qwen3_planner

echo ""
echo "========================================"
echo "Import abgeschlossen."
echo ""
echo "Test:"
echo "  docker exec ${OLLAMA_CONTAINER} ollama run ${MODEL_NAME}:${MODEL_TAG} \\"
echo "    'Erkläre die Grundlagen der Thermodynamik'"
echo ""
echo "Über Proxy (Port 11434):"
echo "  curl -s http://localhost:11434/api/generate -d '{\"model\": \"${MODEL_NAME}:${MODEL_TAG}\", \"prompt\": \"Test\"}'"
echo "========================================"
