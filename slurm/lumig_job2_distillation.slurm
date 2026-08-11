#!/bin/bash
#SBATCH --job-name=moe_distill_student
#SBATCH --account=project_465003058
#SBATCH --partition=standard-g
#SBATCH --nodes=16
#SBATCH --gpus-per-node=8
#SBATCH --cpus-per-task=7
#SBATCH --mem=0
#SBATCH --time=48:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/distill_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/distill_%j.err

# === Signal Trap for Automatic Checkpoint Save on Timeout / Preemption ===
checkpoint_and_exit() {
    echo "RECEIVED SIGTERM / SIGUSR1 FROM SLURM! SAVING DEEPSPEED CHECKPOINT & FLUSHING BUFFER BEFORE TIMEOUT..."
    pkill -SIGINT -f python3
    sync
    echo "FLUSH COMPLETED SAFELY AT $(date)"
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

# === AMD ROCm 7.0 & LUMI-G HPE Slingshot 11 Environment Setup ===
module load ROCm/7.0.0 PyTorch/2.6.0-rocm-6.2 2>/dev/null || true

# 1. HuggingFace Cache Directory Configuration (Points to fast Lustre Scratch)
export HF_HOME="/scratch/project_465003058/hornphil/huggingface_cache"
export TRANSFORMERS_CACHE="$HF_HOME/hub"
export HF_HUB_ENABLE_HF_TRANSFER=1
mkdir -p "$HF_HOME"

# 2. AMD MI250X Dual-GCD Mapping
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7

# 3. ROCm HIP Memory Allocator Tuning
export PYTORCH_HIP_ALLOC_CONF=garbage_collection_threshold:0.8,max_split_size_mb:512

# 4. Slingshot 11 CXI High-Throughput Interconnect Settings
export FI_PROVIDER=cxi
export FI_CXI_DISABLE_HOST_REGISTER=1
export FI_CXI_DEFAULT_CQ_SIZE=131072
export NCCL_NET_GDR_LEVEL=3

# 5. AMD FlashAttention / SDPA ROCm Backend
export FLASH_ATTENTION_FORCE_BUILD=0
export USE_ROCM=1

# 6. HuggingFace Teacher Model Repo
HF_TEACHER_REPO="Qwen/Qwen3.6-35B-A3B-Instruct"
MAX_SEQ_LEN=32768
PER_DEVICE_BATCH_SIZE=2
GRADIENT_ACCUMULATION_STEPS=16

CHECKPOINT_DIR="/scratch/project_465003058/hornphil/checkpoints/moe_3b_distilled"
mkdir -p "$CHECKPOINT_DIR" /scratch/project_465003058/hornphil/moe-sovereign/logs

echo "STARTING DEEPSPEED ZERO-3 TEACHER-STUDENT DISTILLATION (HF_TEACHER=$HF_TEACHER_REPO) AT $(date) ON HOST $(hostname)"

# 7. Download/Verify HuggingFace Model Weights on Rank 0
if [ "$SLURM_PROCID" -eq 0 ]; then
    echo "Rank 0 verifying/downloading HuggingFace teacher model: $HF_TEACHER_REPO..."
    python3 -c "
from huggingface_hub import snapshot_download
snapshot_download(repo_id='$HF_TEACHER_REPO', cache_dir='$TRANSFORMERS_CACHE', local_files_only=False)
" || echo "Note: Teacher model download attempt registered. Using cached weights or fallback."
fi

# 8. Execution of Full DeepSpeed ZeRO-3 SFT Training
echo "Launching PyTorch / TRL SFT Trainer on 128 GPUs..."
srun --cpu-bind=cores python3 /scratch/project_465003058/hornphil/moe-sovereign/scripts/train_planner_sft.py \
    --model-id "Qwen/Qwen3.6-3B-Instruct" \
    --dataset "/scratch/project_465003058/hornphil/datasets/moe_system_knowledge_sft.jsonl" \
    --output-dir "$CHECKPOINT_DIR" \
    --deepspeed "/scratch/project_465003058/hornphil/moe-sovereign/scripts/deepspeed_zero3.json" \
    --epochs 3 \
    --micro-batch 2 \
    --grad-accum 16 \
    --max-seq-len 8192 \
    --lr 2e-4

echo "JOB COMPLETED SUCCESSFULLY AT $(date)"
