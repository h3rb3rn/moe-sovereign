#!/bin/bash
# slurm/lumig_expert_ensemble_pipeline.slurm
# Full End-to-End Pipeline for MoE Sovereign (Planner, 8 Experts, Judge) on LUMI-G
# Stages:
#   1. SFT Training on 8x AMD MI250X (Torchrun Distributed DeepSpeed ZeRO-2/3 BF16 with Qwen3.5 Linear Attention)
#   2. CPU-BF16 LoRA Merge (200 GB RAM)
#   3. GGUF Quantization (Q8_0 & Q4_K_M)

#SBATCH --job-name=moe_expert_pipe
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=18:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/expert_pipe_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/expert_pipe_%j.err

set -euo pipefail

# === Signal Trap for Resilient Checkpoint Saving ===
checkpoint_and_exit() {
    echo "RECEIVED SIGTERM / SIGUSR1 FROM SLURM! FLUSHING BUFFERS AT $(date)"
    pkill -SIGINT -f python3 || true
    sync
    echo "CHECKPOINT FLUSH COMPLETED SAFELY AT $(date)"
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

ROLE="${1:-coder}"
SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
DATASET_DIR="${SCRATCH}/datasets"
DATASET_FILE=$(ls "${DATASET_DIR}/dataset_expert_${ROLE}_"*.jsonl 2>/dev/null | head -n1 || echo "")

export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_HIP_ALLOC_CONF=garbage_collection_threshold:0.8,max_split_size_mb:512
export PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}"
export HF_HOME="${SCRATCH}/cache/huggingface"
export XDG_CACHE_HOME="${SCRATCH}/cache"
export TRITON_CACHE_DIR="${SCRATCH}/cache/triton"
export TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions"
export TMPDIR="${SCRATCH}/tmp"
mkdir -p "${SCRATCH}/cache/huggingface" "${SCRATCH}/cache/triton" "${SCRATCH}/cache/torch_extensions" "${SCRATCH}/tmp"

if [ "$ROLE" == "judge" ]; then
    BASE_MODEL="Qwen/Qwen3.8-27B"
    TRAIN_OUT_DIR="${SCRATCH}/checkpoints/sovereign_judge_27b_sft"
    MERGE_OUT_DIR="${SCRATCH}/checkpoints/merged_sovereign_judge_27b"
    EXPORT_DIR="${SCRATCH}/exports/sovereign-judge-27b"
    BATCH_SIZE=1
    GRAD_ACCUM=16
    LR=1.0e-5
elif [ "$ROLE" == "planner" ]; then
    BASE_MODEL="${SCRATCH}/checkpoints/moe_qwen35_4b_distilled/merged"
    TRAIN_OUT_DIR="${SCRATCH}/checkpoints/moe_sovereign_student_4b_sft"
    MERGE_OUT_DIR="${SCRATCH}/checkpoints/merged_sovereign_student_4b"
    EXPORT_DIR="${SCRATCH}/exports/moe-sovereign-student-4b"
    BATCH_SIZE=4
    GRAD_ACCUM=4
    LR=1.5e-5
else
    # All 8 Experts use the Planner Student SLM as base
    BASE_MODEL="${SCRATCH}/checkpoints/moe_qwen35_4b_distilled/merged"
    TRAIN_OUT_DIR="${SCRATCH}/checkpoints/moe_expert_${ROLE}_sft"
    MERGE_OUT_DIR="${SCRATCH}/checkpoints/merged_expert_${ROLE}"
    EXPORT_DIR="${SCRATCH}/exports/moe-expert-${ROLE}-4b"
    BATCH_SIZE=4
    GRAD_ACCUM=4
    LR=1.5e-5
fi

echo "================================================================================"
echo "🚀 LUMI-G FULL PIPELINE: [moe-${ROLE}]"
echo "Job ID          : $SLURM_JOB_ID"
echo "Node            : $SLURMD_NODENAME"
echo "Role            : $ROLE"
echo "Base Model      : $BASE_MODEL"
echo "Dataset         : $DATASET_FILE"
echo "Train Output    : $TRAIN_OUT_DIR"
echo "Merged Output   : $MERGE_OUT_DIR"
echo "Export Directory: $EXPORT_DIR"
echo "Batch / Accum   : Batch $BATCH_SIZE x Accum $GRAD_ACCUM (LR $LR)"
echo "PYTHONPATH      : $PYTHONPATH"
echo "================================================================================"

if [ -z "$DATASET_FILE" ] || [ ! -f "$DATASET_FILE" ]; then
    echo "ERROR: Dataset for role [$ROLE] not found in $DATASET_DIR"
    exit 1
fi

mkdir -p "$TRAIN_OUT_DIR" "$MERGE_OUT_DIR" "$EXPORT_DIR" "${SCRATCH}/moe-sovereign/logs"

# ── STAGE 1: SFT Training with Torchrun Distributed ─────────────────────────
echo "[$(date +%H:%M:%S)] === STAGE 1: Distributed SFT Training on 8x MI250X (Torchrun) ==="
singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}",HF_HOME="${SCRATCH}/cache/huggingface",XDG_CACHE_HOME="${SCRATCH}/cache",TRITON_CACHE_DIR="${SCRATCH}/cache/triton",TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions",TMPDIR="${SCRATCH}/tmp" \
    "$CONTAINER" \
    torchrun --standalone --nproc_per_node=8 \
        "${SCRIPT_DIR}/train_expert_slm_pipeline.py" \
        --role "$ROLE" \
        --base-model "$BASE_MODEL" \
        --dataset "$DATASET_FILE" \
        --output-dir "$TRAIN_OUT_DIR" \
        --epochs 3 \
        --batch-size "$BATCH_SIZE" \
        --grad-accum "$GRAD_ACCUM" \
        --learning-rate "$LR" \
        --max-seq-len 4096

ADAPTER_DIR="${TRAIN_OUT_DIR}/final_adapter"
if [ ! -d "$ADAPTER_DIR" ]; then
    echo "ERROR: Training failed to produce final adapter at $ADAPTER_DIR"
    exit 1
fi

# ── STAGE 2: CPU-BF16 LoRA Merge ───────────────────────────────────────────
echo "[$(date +%H:%M:%S)] === STAGE 2: CPU-BF16 LoRA Merge ==="
singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}",HF_HOME="${SCRATCH}/cache/huggingface",XDG_CACHE_HOME="${SCRATCH}/cache",TRITON_CACHE_DIR="${SCRATCH}/cache/triton",TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions",TMPDIR="${SCRATCH}/tmp" \
    "$CONTAINER" \
    python3 "${SCRIPT_DIR}/merge_expert_lora_cpu.py" \
        --base-model "$BASE_MODEL" \
        --adapter-dir "$ADAPTER_DIR" \
        --out-dir "$MERGE_OUT_DIR"

# ── STAGE 3: GGUF Q8_0 & Q4_K_M Quantization ──────────────────────────────
echo "[$(date +%H:%M:%S)] === STAGE 3: GGUF Quantization (Q8_0 & Q4_K_M) ==="
singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}",HF_HOME="${SCRATCH}/cache/huggingface",XDG_CACHE_HOME="${SCRATCH}/cache",TRITON_CACHE_DIR="${SCRATCH}/cache/triton",TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions",TMPDIR="${SCRATCH}/tmp" \
    "$CONTAINER" \
    bash "${SCRIPT_DIR}/export_expert_gguf_array.sh" "$ROLE" "$MERGE_OUT_DIR" "$EXPORT_DIR"

echo "[$(date +%H:%M:%S)] 🎉 PIPELINE COMPLETED SUCCESSFULLY FOR [moe-${ROLE}]!"
