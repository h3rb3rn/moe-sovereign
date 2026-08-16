#!/bin/bash
# scripts/export_expert_gguf_array.sh
# Automated GGUF Quantization for MoE Sovereign (Planner, 8 Experts, Judge)
# Exports:
# 1. Q8_0 (Reference Quality)
# 2. Q4_K_M (Production Target)

set -euo pipefail

ROLE="${1:-coder}"
MERGED_DIR="${2:-/scratch/project_465003058/hornphil/checkpoints/merged_expert_${ROLE}}"
EXPORT_DIR="${3:-/scratch/project_465003058/hornphil/exports/moe-expert-${ROLE}-4b}"

echo "================================================================================"
echo "📦 GGUF EXPORT & QUANTIZATION PIPELINE: [moe-${ROLE}]"
echo "Input Directory : $MERGED_DIR"
echo "Export Directory: $EXPORT_DIR"
echo "================================================================================"

mkdir -p "$EXPORT_DIR"

LLAMA_CPP_DIR="/scratch/project_465003058/hornphil/llama.cpp"
if [ ! -d "$LLAMA_CPP_DIR" ]; then
    echo "Cloning llama.cpp for GGUF conversion..."
    git clone --depth 1 https://github.com/ggerganov/llama.cpp "$LLAMA_CPP_DIR"
    make -C "$LLAMA_CPP_DIR" llama-quantize -j16
fi

if [ "$ROLE" == "judge" ]; then
    MODEL_NAME="sovereign-judge-27b"
elif [ "$ROLE" == "planner" ]; then
    MODEL_NAME="moe-sovereign-student-4b"
else
    MODEL_NAME="moe-expert-${ROLE}-4b"
fi

F16_GGUF="${EXPORT_DIR}/${MODEL_NAME}-F16.gguf"
Q8_GGUF="${EXPORT_DIR}/${MODEL_NAME}-Q8_0.gguf"
Q4_GGUF="${EXPORT_DIR}/${MODEL_NAME}-Q4_K_M.gguf"

# 1. Convert HuggingFace model to F16 GGUF
echo "⏳ Converting HuggingFace model to F16 GGUF..."
python3 "${LLAMA_CPP_DIR}/convert_hf_to_gguf.py" "$MERGED_DIR" \
    --outfile "$F16_GGUF" \
    --outtype f16

# 2. Quantize to Q8_0 (Reference)
echo "⏳ Quantizing to Q8_0 (High Precision Reference)..."
"${LLAMA_CPP_DIR}/llama-quantize" "$F16_GGUF" "$Q8_GGUF" Q8_0

# 3. Quantize to Q4_K_M (Production Target)
echo "⏳ Quantizing to Q4_K_M (Production Target)..."
"${LLAMA_CPP_DIR}/llama-quantize" "$F16_GGUF" "$Q4_GGUF" Q4_K_M

# 4. Cleanup raw F16 GGUF to save scratch disk space
rm -f "$F16_GGUF"

echo "================================================================================"
echo "✅ GGUF EXPORT COMPLETED FOR [${MODEL_NAME}]"
ls -lh "$EXPORT_DIR"
echo "================================================================================"
