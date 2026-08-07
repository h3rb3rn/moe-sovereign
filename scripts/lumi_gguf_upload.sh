#!/bin/bash
# scripts/lumi_gguf_upload.sh
# SLURM job: converts merged Qwen3-8B planner model to GGUF, quantizes to
# Q8_0 and Q4_K_M, then uploads merged safetensors + both GGUFs to HuggingFace.
#
# Requires on LUMI scratch (pre-staged by setup):
#   llama_cpp_tools/llama-quantize  (pre-built CPU binary from ggml-org/llama.cpp)
#   llama_cpp_repo/                 (shallow clone of ggml-org/llama.cpp)
#
# Usage:
#   MERGED=/scratch/.../merged sbatch scripts/lumi_gguf_upload.sh
#   sbatch scripts/lumi_gguf_upload.sh   # uses default path from job 20190726

#SBATCH --job-name=planner_gguf_upload
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus=0
#SBATCH --mem=80G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/gguf_upload_%j.log

set -euo pipefail

SCRATCH="/scratch/project_465003058/hornphil"
SIF="${SCRATCH}/lumi-multitorch-latest.sif"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null)"
LLAMA_TOOLS="${SCRATCH}/llama_cpp_tools"
LLAMA_REPO="${SCRATCH}/llama_cpp_repo"

MERGED_PATH="${MERGED:-${SCRATCH}/qwen3_planner_sft_20190726/merged}"
GGUF_DIR="${SCRATCH}/qwen3_planner_gguf"

HF_USER="h3rb3rn"
HF_REPO_MODEL="${HF_USER}/qwen3-planner-sft"
HF_REPO_GGUF="${HF_USER}/qwen3-planner-sft-GGUF"
MODEL_BASENAME="qwen3-planner-sft"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Merged src  : $MERGED_PATH"
echo "GGUF dir    : $GGUF_DIR"
echo "HF model    : $HF_REPO_MODEL"
echo "HF GGUF     : $HF_REPO_GGUF"
echo "Started     : $(date)"
echo "========================================"

mkdir -p "$GGUF_DIR" "${SCRATCH}/logs"

# Sanity checks
[ -d "$MERGED_PATH" ]              || { echo "ERROR: merged model not found: $MERGED_PATH"; exit 1; }
[ -f "$LLAMA_TOOLS/llama-quantize" ] || { echo "ERROR: llama-quantize not found in $LLAMA_TOOLS"; exit 1; }
[ -f "$LLAMA_REPO/convert_hf_to_gguf.py" ] || { echo "ERROR: convert_hf_to_gguf.py not found in $LLAMA_REPO"; exit 1; }
[ -f "$SIF" ]                      || { echo "ERROR: container not found: $SIF"; exit 1; }

export LD_LIBRARY_PATH="${LLAMA_TOOLS}:${LD_LIBRARY_PATH:-}"

# ── Step 1: Convert BF16 → F16 GGUF ──────────────────────────────────────────
F16_GGUF="${GGUF_DIR}/${MODEL_BASENAME}-F16.gguf"
echo ""
echo "[$(date +%H:%M:%S)] Step 1: Converting merged BF16 model → F16 GGUF ..."

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME="${HF_CACHE}" \
    --env HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}" \
    "$SIF" \
    bash -c "cd ${LLAMA_REPO} && python3 convert_hf_to_gguf.py \
        '${MERGED_PATH}' \
        --outtype f16 \
        --outfile '${F16_GGUF}'"

echo "[$(date +%H:%M:%S)] F16 GGUF: $(du -sh ${F16_GGUF} | cut -f1)"

# ── Step 2: Quantize → Q8_0 ──────────────────────────────────────────────────
Q8_GGUF="${GGUF_DIR}/${MODEL_BASENAME}-Q8_0.gguf"
echo ""
echo "[$(date +%H:%M:%S)] Step 2: Quantizing F16 → Q8_0 ..."
"${LLAMA_TOOLS}/llama-quantize" "$F16_GGUF" "$Q8_GGUF" Q8_0
echo "[$(date +%H:%M:%S)] Q8_0: $(du -sh ${Q8_GGUF} | cut -f1)"

# ── Step 3: Quantize → Q4_K_M ────────────────────────────────────────────────
Q4_GGUF="${GGUF_DIR}/${MODEL_BASENAME}-Q4_K_M.gguf"
echo ""
echo "[$(date +%H:%M:%S)] Step 3: Quantizing F16 → Q4_K_M ..."
"${LLAMA_TOOLS}/llama-quantize" "$F16_GGUF" "$Q4_GGUF" Q4_K_M
echo "[$(date +%H:%M:%S)] Q4_K_M: $(du -sh ${Q4_GGUF} | cut -f1)"

echo ""
echo "=== GGUF output summary ==="
ls -lh "${GGUF_DIR}/"

# ── Step 4: Upload merged safetensors model to HF ────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Step 4: Uploading merged safetensors model → ${HF_REPO_MODEL} ..."

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME="${HF_CACHE}" \
    --env HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}" \
    "$SIF" python3 -c "
from huggingface_hub import HfApi
import os

api = HfApi(token='${HF_TOKEN}')
repo_id = '${HF_REPO_MODEL}'

try:
    api.create_repo(repo_id=repo_id, repo_type='model', exist_ok=True, private=False)
    print(f'Repo ready: {repo_id}')
except Exception as e:
    print(f'Repo create: {e}')

print('Uploading merged model folder ...')
api.upload_folder(
    folder_path='${MERGED_PATH}',
    repo_id=repo_id,
    repo_type='model',
    commit_message='Add Qwen3-8B planner SFT merged model (259K samples, dynamic category)',
    ignore_patterns=['*.tmp', '__pycache__', '*.lock'],
)
print('Upload complete: ${HF_REPO_MODEL}')
"

# ── Step 5: Upload GGUFs to HF ────────────────────────────────────────────────
echo ""
echo "[$(date +%H:%M:%S)] Step 5: Uploading GGUFs → ${HF_REPO_GGUF} ..."

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME="${HF_CACHE}" \
    --env HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}" \
    "$SIF" python3 -c "
from huggingface_hub import HfApi
import os

api = HfApi(token='${HF_TOKEN}')
repo_id = '${HF_REPO_GGUF}'

try:
    api.create_repo(repo_id=repo_id, repo_type='model', exist_ok=True, private=False)
    print(f'Repo ready: {repo_id}')
except Exception as e:
    print(f'Repo create: {e}')

gguf_files = [
    ('${Q8_GGUF}',  '${MODEL_BASENAME}-Q8_0.gguf'),
    ('${Q4_GGUF}',  '${MODEL_BASENAME}-Q4_K_M.gguf'),
]

for local_path, hf_filename in gguf_files:
    size = os.path.getsize(local_path) / 1e9
    print(f'Uploading {hf_filename} ({size:.1f} GB) ...')
    api.upload_file(
        path_or_fileobj=local_path,
        path_in_repo=hf_filename,
        repo_id=repo_id,
        repo_type='model',
        commit_message=f'Add {hf_filename}',
    )
    print(f'  done: {hf_filename}')

print('All GGUFs uploaded to: ${HF_REPO_GGUF}')
"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo "GGUF + UPLOAD COMPLETE"
echo ""
echo "HuggingFace:"
echo "  Merged model : https://huggingface.co/${HF_REPO_MODEL}"
echo "  GGUF         : https://huggingface.co/${HF_REPO_GGUF}"
echo ""
echo "N04-RTX (Ollama-Einbindung):"
echo "  Q4_K_M GGUF  : hf.co/${HF_REPO_GGUF}:Q4_K_M"
echo "  Skript       : bash scripts/n04rtx_ollama_import.sh"
echo ""
echo "Finished: $(date)"
echo "========================================"
