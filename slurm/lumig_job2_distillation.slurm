#!/bin/bash
# slurm/lumig_job2_distillation.slurm
# SLURM job: Teacher-Student Distillation — SFT fine-tuning of Qwen3.5-4B
#
# Architecture:
#   Teacher: Qwen/Qwen3.5-35B-A3B (official HuggingFace repo)
#   Student: Qwen/Qwen3.5-4B     (locally cached on Lustre scratch)
#   Framework: TRL SFTTrainer + PEFT LoRA + DeepSpeed ZeRO-2 BF16
#   Runtime: Singularity container (lumi-multitorch-latest.sif) — LUMI-G
#
# Usage:
#   sbatch slurm/lumig_job2_distillation.slurm

#SBATCH --job-name=moe_qwen35_4b_distill
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=48:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/distill_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/distill_%j.err

set -euo pipefail

# === Signal Trap for Automatic Checkpoint Save on Timeout / Preemption ===
checkpoint_and_exit() {
    echo "RECEIVED SIGTERM / SIGUSR1 FROM SLURM! SAVING CHECKPOINT & FLUSHING BUFFER..."
    pkill -SIGINT -f python3 || true
    sync
    echo "FLUSH COMPLETED SAFELY AT $(date)"
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

# ── Paths ────────────────────────────────────────────────────────────────────
SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"

# ── Model & Training Config ─────────────────────────────────────────────────
HF_TEACHER_REPO="Qwen/Qwen3.5-35B-A3B"
MODEL_ID="Qwen/Qwen3.5-4B"
DATASET="${SCRATCH}/datasets/moe_system_knowledge_sft.jsonl"
TRAIN_RUN="moe_qwen35_4b_distilled"
OUTPUT_DIR="${SCRATCH}/checkpoints/${TRAIN_RUN}"

# ── Hyperparameters ──────────────────────────────────────────────────────────
EPOCHS=3
MICRO_BATCH=2
GRAD_ACCUM=8
MAX_SEQ_LEN=4096
LR="2e-4"
LORA_R=16
LORA_ALPHA=32
LORA_DROPOUT=0.05
SAVE_STEPS=100
GRADIENT_CHECKPOINTING=true

mkdir -p "$OUTPUT_DIR" "${SCRATCH}/moe-sovereign/logs"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURMD_NODENAME"
echo "Train run   : $TRAIN_RUN"
echo "Teacher     : $HF_TEACHER_REPO"
echo "Student     : $MODEL_ID"
echo "Dataset     : $DATASET"
echo "Output dir  : $OUTPUT_DIR  (fest — Resume-fähig)"
echo "Epochs      : $EPOCHS"
echo "Batch/GPU   : $MICRO_BATCH × grad_accum=$GRAD_ACCUM = $(( MICRO_BATCH * GRAD_ACCUM * 8 )) effective"
echo "Max seq len : $MAX_SEQ_LEN"
echo "LR          : $LR"
echo "LoRA r/α    : $LORA_R / $LORA_ALPHA (dropout=$LORA_DROPOUT)"
echo "Grad ckpt   : $GRADIENT_CHECKPOINTING"
echo "Save steps  : $SAVE_STEPS  (save_total_limit=3)"
echo "ZeRO stage  : 2"
echo "========================================"

# ── Sanity checks ────────────────────────────────────────────────────────────
if [ ! -f "$DATASET" ]; then
    echo "ERROR: Dataset not found: $DATASET"
    exit 1
fi
[ ! -f "$CONTAINER" ] && echo "ERROR: Singularity container not found: $CONTAINER" && exit 1

DATASET_LINES=$(wc -l < "$DATASET")
echo "Dataset lines: $DATASET_LINES"

# ── Checkpoint-Status ────────────────────────────────────────────────────────
LAST_CKPT=$(ls -d "${OUTPUT_DIR}/checkpoint-"* 2>/dev/null | sort -V | tail -1 || true)
if [ -n "$LAST_CKPT" ]; then
    CKPT_STEP=$(basename "$LAST_CKPT" | grep -oP '\d+')
    echo "Resuming from: $LAST_CKPT (step ${CKPT_STEP})"
else
    echo "No checkpoint found — training starts from scratch."
fi

# ── Model Cache ──────────────────────────────────────────────────────────────
HF_CACHE_NAME="$(echo "$MODEL_ID" | sed 's|/|--|g')"
MODEL_CACHE_DIR="$HF_CACHE/models--${HF_CACHE_NAME}"
if [ -d "$MODEL_CACHE_DIR/snapshots" ]; then
    LOCAL_MODEL="$(ls -d ${MODEL_CACHE_DIR}/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
    echo "Using cached student model: $LOCAL_MODEL"
else
    echo "Student model not cached — will download inside container..."
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
    LOCAL_MODEL="$(ls -d ${MODEL_CACHE_DIR}/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
fi

if [ -z "$LOCAL_MODEL" ]; then
    echo "WARNING: Could not resolve local model path. Using MODEL_ID directly: $MODEL_ID"
    LOCAL_MODEL="$MODEL_ID"
fi
echo "Model path: $LOCAL_MODEL"

# ── ROCm / AMD Environment ──────────────────────────────────────────────────
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export NCCL_SOCKET_IFNAME=hsn0
export NCCL_NET_GDR_LEVEL=3
export MASTER_ADDR=$(hostname)
export MASTER_PORT=29500
export OMP_NUM_THREADS=7
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512
export PYTORCH_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512
export CUSTOM_SITE="${SCRATCH}/.user_site"
mkdir -p "$CUSTOM_SITE"

# ── Training starten ─────────────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting SFT distillation for Qwen3.5-4B (run: $TRAIN_RUN) ..."

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="$CUSTOM_SITE" \
    --env HF_HOME="$HF_CACHE" \
    --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
    --env ROCR_VISIBLE_DEVICES="$ROCR_VISIBLE_DEVICES" \
    --env HIP_VISIBLE_DEVICES="$HIP_VISIBLE_DEVICES" \
    --env NCCL_SOCKET_IFNAME="$NCCL_SOCKET_IFNAME" \
    --env NCCL_NET_GDR_LEVEL="$NCCL_NET_GDR_LEVEL" \
    --env MASTER_ADDR="$MASTER_ADDR" \
    --env MASTER_PORT="$MASTER_PORT" \
    --env OMP_NUM_THREADS="$OMP_NUM_THREADS" \
    --env PYTORCH_HIP_ALLOC_CONF="$PYTORCH_HIP_ALLOC_CONF" \
    --env PYTORCH_ALLOC_CONF="$PYTORCH_ALLOC_CONF" \
    --env TOKENIZERS_PARALLELISM="$TOKENIZERS_PARALLELISM" \
    "$CONTAINER" \
    deepspeed --num_gpus 8 \
        "$SCRIPT_DIR/train_planner_sft.py" \
        --model-id       "$LOCAL_MODEL" \
        --dataset        "$DATASET" \
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
        --no-packing \
        --no-4bit

SFT_EXIT=$?
echo "[$(date +%H:%M:%S)] Qwen3.5-4B Distillation SFT exited with code $SFT_EXIT"

echo "========================================"
if [ $SFT_EXIT -eq 0 ]; then
    echo "DISTILLATION SFT COMPLETED — run: $TRAIN_RUN"
    ls -lh "$OUTPUT_DIR/final_adapter/" 2>/dev/null
else
    echo "DISTILLATION SFT FAILED (exit $SFT_EXIT) — run: $TRAIN_RUN"
    echo "Checkpoints preserved in: $OUTPUT_DIR"
    echo "Last checkpoint: $(ls -d ${OUTPUT_DIR}/checkpoint-* 2>/dev/null | sort -V | tail -1 || echo 'none')"
fi
echo "========================================"
exit $SFT_EXIT
