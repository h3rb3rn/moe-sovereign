#!/bin/bash
# scripts/lumi_merge_and_sft.sh
# SLURM job: merges two planner JSONL datasets, then runs Qwen3-8B SFT.
#
# Intended use: combine the main 200K dataset with the 50K dynamic top-up
# before fine-tuning, so the planner learns all categories in one pass.
#
# Required env vars (set via sbatch --export or environment):
#   DATASET1  — main dataset   (e.g. planner_dataset_20140407/planner_chat.jsonl)
#   DATASET2  — top-up dataset (e.g. planner_dynamic_20149876/planner_chat.jsonl)
#
# Usage:
#   DATASET1=".../planner_dataset_20140407/planner_chat.jsonl" \
#   DATASET2=".../planner_dynamic_20149876/planner_chat.jsonl" \
#   sbatch scripts/lumi_merge_and_sft.sh
#
#   Or with dependency:
#   DATASET1=... DATASET2=... \
#   sbatch --dependency=afterok:<TOPUP_JOB_ID> scripts/lumi_merge_and_sft.sh

#SBATCH --job-name=qwen3_sft_merged
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=38:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/qwen3_sft_merged_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/logs/qwen3_sft_merged_%j.err

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SCRATCH="/scratch/project_465003058/hornphil"
HF_CACHE="$SCRATCH/hf_cache"
SCRIPT_DIR="$SCRATCH/scripts"
CONTAINER="$SCRATCH/lumi-multitorch-latest.sif"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null)"
MODEL_ID="${MODEL_ID:-Qwen/Qwen3-8B}"

# Dataset paths — must be set as env vars before submission
DATASET1="${DATASET1:?ERROR: DATASET1 env var not set}"
DATASET2="${DATASET2:?ERROR: DATASET2 env var not set}"

MERGED_DIR="$SCRATCH/planner_merged_${SLURM_JOB_ID}"
MERGED_DATASET="$MERGED_DIR/planner_chat.jsonl"
OUTPUT_DIR="$SCRATCH/qwen3_planner_sft_${SLURM_JOB_ID}"

# Training hyperparameters
EPOCHS="${EPOCHS:-3}"
MICRO_BATCH="${MICRO_BATCH:-8}"
GRAD_ACCUM="${GRAD_ACCUM:-2}"
MAX_SEQ_LEN="${MAX_SEQ_LEN:-8192}"
LR="${LR:-2e-4}"
LORA_R="${LORA_R:-16}"
LORA_ALPHA="${LORA_ALPHA:-32}"
LORA_DROPOUT="${LORA_DROPOUT:-0.05}"
SAVE_STEPS="${SAVE_STEPS:-500}"
PACKING="${PACKING:-false}"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURMD_NODENAME"
echo "Model       : $MODEL_ID"
echo "Dataset 1   : $DATASET1"
echo "Dataset 2   : $DATASET2"
echo "Merged into : $MERGED_DATASET"
echo "Output dir  : $OUTPUT_DIR"
echo "========================================"

mkdir -p "$MERGED_DIR" "$OUTPUT_DIR"
mkdir -p "$SCRATCH/logs"

# ── Sanity checks ─────────────────────────────────────────────────────────────
for DS in "$DATASET1" "$DATASET2"; do
    if [ ! -f "$DS" ]; then
        echo "ERROR: Dataset not found: $DS"
        exit 1
    fi
done

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    exit 1
fi

# ── Merge datasets ────────────────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Merging datasets ..."
cat "$DATASET1" "$DATASET2" > "$MERGED_DATASET"

LINES1=$(wc -l < "$DATASET1")
LINES2=$(wc -l < "$DATASET2")
LINES_TOTAL=$(wc -l < "$MERGED_DATASET")
echo "  Dataset 1: $LINES1 samples"
echo "  Dataset 2: $LINES2 samples"
echo "  Merged   : $LINES_TOTAL samples"

if [ "$LINES_TOTAL" -lt 10000 ]; then
    echo "ERROR: Merged dataset has fewer than 10K samples."
    exit 1
fi

# Validate system prompt diversity (≥3 of 5 variants)
VARIANT_COUNT=$(singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    "$CONTAINER" python3 -c "
import json, collections
variants = collections.Counter()
with open('$MERGED_DATASET') as f:
    for i, line in enumerate(f):
        if i >= 2000: break
        d = json.loads(line)
        msgs = d.get('messages', [])
        sys = next((m['content'][:60] for m in msgs if m['role']=='system'), '')
        variants[sys] += 1
print(len(variants))
" 2>/dev/null || echo "0")
echo "System prompt variants in first 2K samples: $VARIANT_COUNT"
if [ "$VARIANT_COUNT" -lt 3 ]; then
    echo "ERROR: Fewer than 3 system prompt variants — dataset entropy too low."
    exit 1
fi

# ── Download model if not cached ──────────────────────────────────────────────
MODEL_CACHE_DIR="$HF_CACHE/models--${MODEL_ID//\//--}"
if [ -d "$MODEL_CACHE_DIR/snapshots" ]; then
    LOCAL_MODEL="$(ls -d $MODEL_CACHE_DIR/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
    echo "Using cached model: $LOCAL_MODEL"
else
    echo "Downloading model $MODEL_ID ..."
    singularity exec \
        --bind /scratch/project_465003058:/scratch/project_465003058 \
        --env HF_HOME="$HF_CACHE" \
        --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
        "$CONTAINER" python3 -c "
from huggingface_hub import snapshot_download
import os, time
t0 = time.time()
p = snapshot_download('$MODEL_ID', token=os.environ.get('HUGGING_FACE_HUB_TOKEN') or None,
                      cache_dir='$HF_CACHE', ignore_patterns=['*.pt'])
print(f'Downloaded in {(time.time()-t0)/60:.1f} min: {p}')
"
    LOCAL_MODEL="$(ls -d $MODEL_CACHE_DIR/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
fi
echo "Model path: $LOCAL_MODEL"

# ── ROCm / AMD environment ─────────────────────────────────────────────────────
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export NCCL_SOCKET_IFNAME=hsn0
export NCCL_NET_GDR_LEVEL=3
export MASTER_ADDR=$(hostname)
export MASTER_PORT=29500
export OMP_NUM_THREADS=7

# ── Launch training ────────────────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting SFT training on $LINES_TOTAL samples ..."

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME="$HF_CACHE" \
    --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
    --env ROCR_VISIBLE_DEVICES="$ROCR_VISIBLE_DEVICES" \
    --env HIP_VISIBLE_DEVICES="$HIP_VISIBLE_DEVICES" \
    --env NCCL_SOCKET_IFNAME="$NCCL_SOCKET_IFNAME" \
    --env NCCL_NET_GDR_LEVEL="$NCCL_NET_GDR_LEVEL" \
    --env MASTER_ADDR="$MASTER_ADDR" \
    --env MASTER_PORT="$MASTER_PORT" \
    --env OMP_NUM_THREADS="$OMP_NUM_THREADS" \
    "$CONTAINER" \
    deepspeed --num_gpus 8 \
        "$SCRIPT_DIR/train_planner_sft.py" \
        --model-id       "$LOCAL_MODEL" \
        --dataset        "$MERGED_DATASET" \
        --output-dir     "$OUTPUT_DIR" \
        --deepspeed      "$SCRIPT_DIR/deepspeed_zero2_qwen3.json" \
        --epochs         "$EPOCHS" \
        --micro-batch    "$MICRO_BATCH" \
        --grad-accum     "$GRAD_ACCUM" \
        --max-seq-len    "$MAX_SEQ_LEN" \
        --lr             "$LR" \
        --lora-r         "$LORA_R" \
        --lora-alpha     "$LORA_ALPHA" \
        --lora-dropout   "$LORA_DROPOUT" \
        --save-steps     "$SAVE_STEPS" \
        --hf-cache       "$HF_CACHE" \
        --val-split      0.005 \
        $( [ "$PACKING" = "false" ] && echo "--no-packing" )

SFT_EXIT=$?
echo "[$(date +%H:%M:%S)] Training exited with code $SFT_EXIT"

echo "========================================"
if [ $SFT_EXIT -eq 0 ]; then
    echo "SFT COMPLETED — merged dataset: $LINES_TOTAL samples ($LINES1 main + $LINES2 dynamic)"
    ls -lh "$OUTPUT_DIR/final_adapter/" 2>/dev/null
    echo ""
    echo "Nächste Schritte:"
    echo "  sbatch scripts/merge_planner_lora.sh --adapter $OUTPUT_DIR/final_adapter"
    echo "  python3 scripts/eval_planner.py --model $OUTPUT_DIR/final_adapter"
else
    echo "SFT FAILED with exit code $SFT_EXIT"
    echo "Check: $SCRATCH/logs/qwen3_sft_merged_${SLURM_JOB_ID}.err"
fi
echo "========================================"
exit $SFT_EXIT
