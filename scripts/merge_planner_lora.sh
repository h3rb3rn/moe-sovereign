#!/bin/bash
# scripts/merge_planner_lora.sh
# SLURM job: merges LoRA adapter into Qwen3-8B base and optionally converts to GGUF.
#
# Usage:
#   sbatch scripts/merge_planner_lora.sh --adapter <ADAPTER_PATH>
#   ADAPTER=/scratch/.../final_adapter sbatch scripts/merge_planner_lora.sh

#SBATCH --job-name=planner_lora_merge
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus=0
#SBATCH --mem=60G
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/merge_planner_%j.log

set -euo pipefail

SCRATCH="/scratch/project_465003058/hornphil"
HF_CACHE="${SCRATCH}/hf_cache"
SIF="${SCRATCH}/lumi-multitorch-latest.sif"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null)"

# Accept --adapter <path> as argument or ADAPTER env var
ADAPTER_PATH="${ADAPTER:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --adapter) ADAPTER_PATH="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$ADAPTER_PATH" ]; then
    echo "ERROR: ADAPTER_PATH not set. Use --adapter <path> or ADAPTER=<path> env var."
    exit 1
fi

SNAPSHOT=$(ls "${HF_CACHE}/models--Qwen--Qwen3-8B/snapshots/" 2>/dev/null | head -1)
BASE_MODEL="${HF_CACHE}/models--Qwen--Qwen3-8B/snapshots/${SNAPSHOT}"
MERGED_PATH="${ADAPTER_PATH%/final_adapter}/merged"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Base model  : $BASE_MODEL"
echo "Adapter     : $ADAPTER_PATH"
echo "Output      : $MERGED_PATH"
echo "Started     : $(date)"
echo "========================================"

if [ ! -f "${ADAPTER_PATH}/adapter_model.safetensors" ]; then
    echo "ERROR: Adapter not found at ${ADAPTER_PATH}"
    exit 1
fi

if [ ! -d "$BASE_MODEL" ]; then
    echo "ERROR: Base model not found at ${BASE_MODEL}"
    echo "Available snapshots:"
    ls "${HF_CACHE}/models--Qwen--Qwen3-8B/snapshots/" 2>/dev/null || echo "  (none)"
    exit 1
fi

mkdir -p "$MERGED_PATH"
mkdir -p "${SCRATCH}/logs"

export HF_HOME="${HF_CACHE}"
export TOKENIZERS_PARALLELISM=false

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME="${HF_CACHE}" \
    --env HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}" \
    --env TOKENIZERS_PARALLELISM=false \
    "$SIF" python3 -c "
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base_model_path = '${BASE_MODEL}'
adapter_path    = '${ADAPTER_PATH}'
merged_path     = '${MERGED_PATH}'

print('Loading tokenizer ...')
tokenizer = AutoTokenizer.from_pretrained(adapter_path, trust_remote_code=True)

print('Loading Qwen3-8B base model on CPU (BF16) ...')
base_model = AutoModelForCausalLM.from_pretrained(
    base_model_path,
    torch_dtype=torch.bfloat16,
    device_map='cpu',
    trust_remote_code=True,
)

print('Applying LoRA adapter ...')
model = PeftModel.from_pretrained(base_model, adapter_path)

print('Merging and unloading LoRA weights ...')
model = model.merge_and_unload()

print(f'Saving merged model to {merged_path} ...')
model.save_pretrained(merged_path, safe_serialization=True)
tokenizer.save_pretrained(merged_path)

print('Merge complete.')
"

MERGE_EXIT=$?
echo ""
echo "[$(date +%H:%M:%S)] Merge exit code: $MERGE_EXIT"

if [ $MERGE_EXIT -eq 0 ]; then
    echo "========================================"
    echo "MERGE COMPLETED"
    ls -lh "${MERGED_PATH}/"
    du -sh "${MERGED_PATH}/"
    echo ""
    echo "Naechste Schritte:"
    echo "  Auf lokalen Host kopieren:"
    echo "    rsync -avz lumi-g:${MERGED_PATH}/ /opt/deployment/moe-sovereign/models/qwen3-planner-sft/"
    echo ""
    echo "  Eval:"
    echo "    python3 scripts/eval_planner.py --model ${MERGED_PATH}"
    echo "========================================"
else
    echo "MERGE FAILED with exit code $MERGE_EXIT"
    exit $MERGE_EXIT
fi
