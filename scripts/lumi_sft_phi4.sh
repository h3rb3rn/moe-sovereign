#!/bin/bash
# scripts/lumi_sft_phi4.sh
# SLURM job: SFT fine-tuning of phi-4 (14B) as MoE Sovereign planner
#
# Teacher dataset: Llama-3.3-70B-Instruct (200K samples, job 20022782)
# Framework      : TRL SFTTrainer + PEFT LoRA + DeepSpeed ZeRO-3 BF16
# Hardware       : 1 LUMI-G node, 8× AMD MI250X GCDs, 512 GB HBM2e
#
# Estimated runtime: 25-40h for 3 epochs on 200K samples
# GPU-h cost        : 8 GPUs × ~32h ≈ 256 GPU-h  (budget: 4500 GPU-h)
#
# Usage:
#   DATASET=/scratch/.../planner_dataset_20022782/planner_chat.jsonl \
#     sbatch lumi_sft_phi4.sh
#
# After completion, run:
#   sbatch scripts/merge_planner_lora.sh --adapter <OUTPUT_DIR>/final_adapter

#SBATCH --job-name=phi4_planner_sft
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=72:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/phi4_sft_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/logs/phi4_sft_%j.err

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_ID="${MODEL_ID:-microsoft/phi-4}"
DATASET="${DATASET:-/scratch/project_465003058/hornphil/planner_dataset_20022782/planner_chat.jsonl}"
HF_CACHE="/scratch/project_465003058/hornphil/hf_cache"
SCRIPT_DIR="/scratch/project_465003058/hornphil/scripts"
OUTPUT_DIR="/scratch/project_465003058/hornphil/phi4_planner_sft_${SLURM_JOB_ID}"
CONTAINER="/scratch/project_465003058/hornphil/lumi-multitorch-latest.sif"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null)"

# Training hyperparameters (override via env)
EPOCHS="${EPOCHS:-3}"
MICRO_BATCH="${MICRO_BATCH:-2}"
GRAD_ACCUM="${GRAD_ACCUM:-4}"
MAX_SEQ_LEN="${MAX_SEQ_LEN:-3072}"
PACKING="${PACKING:-false}"
LR="${LR:-5e-5}"
LORA_R="${LORA_R:-64}"
LORA_ALPHA="${LORA_ALPHA:-128}"
SAVE_STEPS="${SAVE_STEPS:-500}"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURMD_NODENAME"
echo "Model       : $MODEL_ID"
echo "Dataset     : $DATASET"
echo "Output dir  : $OUTPUT_DIR"
echo "Epochs      : $EPOCHS"
echo "Batch/GPU   : $MICRO_BATCH × grad_accum=$GRAD_ACCUM = $(( MICRO_BATCH * GRAD_ACCUM * 8 )) effective"
echo "Max seq len : $MAX_SEQ_LEN"
echo "LoRA r/α    : $LORA_R / $LORA_ALPHA"
echo "========================================"

mkdir -p "$OUTPUT_DIR"
mkdir -p /scratch/project_465003058/hornphil/logs

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -f "$DATASET" ]; then
    echo "ERROR: Dataset not found: $DATASET"
    echo "Check that job 20022782 completed: ls planner_dataset_20022782/"
    exit 1
fi

DATASET_LINES=$(wc -l < "$DATASET")
echo "Dataset lines: $DATASET_LINES"
if [ "$DATASET_LINES" -lt 10000 ]; then
    echo "WARNING: Dataset has fewer than 10K samples — generation job may not be complete."
fi

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    exit 1
fi

# ── Download phi-4 if not cached ───────────────────────────────────────────────
MODEL_CACHE_DIR="$HF_CACHE/models--${MODEL_ID//\//--}"
if [ -d "$MODEL_CACHE_DIR/snapshots" ]; then
    LOCAL_MODEL="$(ls -d $MODEL_CACHE_DIR/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
    echo "Using cached phi-4: $LOCAL_MODEL"
else
    echo "phi-4 not cached — downloading (~28 GB BF16) …"
    singularity exec \
        --bind /scratch/project_465003058:/scratch/project_465003058 \
        --env HF_HOME="$HF_CACHE" \
        --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
        "$CONTAINER" python3 -c "
from huggingface_hub import snapshot_download
import os, time
token = os.environ.get('HUGGING_FACE_HUB_TOKEN','')
t0 = time.time()
p = snapshot_download('$MODEL_ID', token=token or None,
                      cache_dir='$HF_CACHE', ignore_patterns=['*.pt'])
print(f'Downloaded in {(time.time()-t0)/60:.1f} min: {p}')
"
    LOCAL_MODEL="$(ls -d $MODEL_CACHE_DIR/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
fi

echo "Model path: $LOCAL_MODEL"

# ── Set ROCm / AMD environment ─────────────────────────────────────────────────
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export NCCL_SOCKET_IFNAME=hsn0
export NCCL_NET_GDR_LEVEL=3
export MASTER_ADDR=$(hostname)
export MASTER_PORT=29500
export OMP_NUM_THREADS=7
export PYTORCH_ALLOC_CONF=expandable_segments:True

# ── Launch training via DeepSpeed inside container ────────────────────────────
echo "[$(date +%H:%M:%S)] Starting SFT training …"

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
    --env PYTORCH_ALLOC_CONF="$PYTORCH_ALLOC_CONF" \
    "$CONTAINER" \
    deepspeed --num_gpus 8 \
        "$SCRIPT_DIR/train_planner_sft.py" \
        --model-id       "$LOCAL_MODEL" \
        --dataset        "$DATASET" \
        --output-dir     "$OUTPUT_DIR" \
        --deepspeed      "$SCRIPT_DIR/deepspeed_zero3_phi4.json" \
        --epochs         "$EPOCHS" \
        --micro-batch    "$MICRO_BATCH" \
        --grad-accum     "$GRAD_ACCUM" \
        --max-seq-len    "$MAX_SEQ_LEN" \
        --lr             "$LR" \
        --lora-r         "$LORA_R" \
        --lora-alpha     "$LORA_ALPHA" \
        --save-steps     "$SAVE_STEPS" \
        --hf-cache       "$HF_CACHE" \
        $( [ "$PACKING" = "false" ] && echo "--no-packing" )

SFT_EXIT=$?
echo "[$(date +%H:%M:%S)] Training exited with code $SFT_EXIT"

# ── Summary ───────────────────────────────────────────────────────────────────
echo "========================================"
if [ $SFT_EXIT -eq 0 ]; then
    echo "SFT COMPLETED successfully"
    ls -lh "$OUTPUT_DIR/final_adapter/" 2>/dev/null
    echo ""
    echo "Next step — merge LoRA + GGUF-Konversion:"
    echo "  sbatch scripts/merge_planner_lora.sh \\"
    echo "    --adapter $OUTPUT_DIR/final_adapter \\"
    echo "    --base    $LOCAL_MODEL"
else
    echo "SFT FAILED with exit code $SFT_EXIT"
    echo "Check: /scratch/project_465003058/hornphil/logs/phi4_sft_${SLURM_JOB_ID}.err"
fi
echo "========================================"
exit $SFT_EXIT
