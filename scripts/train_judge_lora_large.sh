#!/bin/bash
#SBATCH --job-name=judge-lora-large
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus=8                     # 8 GCDs × 64 GB — DDP, each GPU holds full QLoRA model (~17 GB)
#SBATCH --time=12:00:00              # 10k samples × 3 epochs ≈ 2-4h; 12h buffer
#SBATCH --output=/scratch/project_465003058/hornphil/logs/judge_lora_large_%j.log
#SBATCH --mem=200G

# ── Directories ──────────────────────────────────────────────────────────────
SCRATCH=/scratch/project_465003058/hornphil
SCRIPTS_DIR="${SCRATCH}/scripts"
DATA_DIR="${SCRATCH}/data"
MODELS_DIR="${SCRATCH}/models"
HF_CACHE="${SCRATCH}/hf_cache"
LOGS_DIR="${SCRATCH}/logs"

mkdir -p "${LOGS_DIR}" "${MODELS_DIR}"

# ── Model configuration ───────────────────────────────────────────────────────
SNAPSHOT=$(ls "${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/" 2>/dev/null | head -1)
BASE_MODEL="${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/${SNAPSHOT}"

DATASET_PATH="${DATA_DIR}/paraconsistent_large.jsonl"

# Fallback: use the small dataset if the large one doesn't exist yet
if [ ! -f "${DATASET_PATH}" ]; then
    echo "⚠️  Large dataset not found, falling back to original 140-sample dataset."
    DATASET_PATH="${DATA_DIR}/paraconsistent_training_data.jsonl"
fi

OUTPUT_DIR="${MODELS_DIR}/sovereign-judge-32b-lora-v2"

echo "🚀 Sovereign Judge LoRA Large Training (DDP, 8 GPUs)"
echo "   Base model  : ${BASE_MODEL}"
echo "   Dataset     : ${DATASET_PATH}"
echo "   Samples     : $(wc -l < "${DATASET_PATH}")"
echo "   Output      : ${OUTPUT_DIR}"
echo "   GPUs        : ${SLURM_GPUS}"
echo "   Job ID      : ${SLURM_JOB_ID}"
echo "   Start       : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Environment ──────────────────────────────────────────────────────────────
# module purge
# module use /appl/local/laifs/modules
# module load lumi-aif-singularity-bindings

export SIF=/scratch/project_465003058/hornphil/lumi-multitorch-latest.sif
export HF_HOME="${HF_CACHE}"
export TRANSFORMERS_CACHE="${HF_CACHE}/transformers"
export TOKENIZERS_PARALLELISM=false

# DDP environment (all 8 GPUs, each with full model copy)
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export MASTER_ADDR=localhost
export MASTER_PORT=29500
# Do NOT set PYTORCH_ALLOC_CONF=expandable_segments (not supported on ROCm)

echo ""
echo "── Container info ──────────────────────────────────────────────────"
singularity exec --bind /pfs,/scratch,/projappl,/project "${SIF}" python3 -c "
import torch
print(f'PyTorch version : {torch.__version__}')
print(f'ROCm available  : {torch.cuda.is_available()}')
print(f'Device count    : {torch.cuda.device_count()}')
for i in range(torch.cuda.device_count()):
    props = torch.cuda.get_device_properties(i)
    print(f'  GPU {i}: {props.name} ({props.total_memory // 1024**3} GB)')
"

echo ""
echo "── Starting DDP training (torchrun, 8 GPUs) ─────────────────────────"
# Use torchrun for proper DDP — each GPU gets an independent copy of the 4-bit model
# (no pipeline parallelism, no device_map across GPUs)
singularity exec \
    --bind /pfs,/scratch,/projappl,/project \
    "${SIF}" \
    torchrun \
        --nproc_per_node=8 \
        --master_addr="${MASTER_ADDR}" \
        --master_port="${MASTER_PORT}" \
        "${SCRIPTS_DIR}/train_judge_lora.py" \
            --base_model  "${BASE_MODEL}" \
            --dataset_path "${DATASET_PATH}" \
            --output_dir  "${OUTPUT_DIR}" \
            --epochs      3 \
            --batch_size  2 \
            --grad_accum  8 \
            --max_seq_len 2048 \
            --lora_r      64 \
            --lora_alpha  128

TRAIN_EXIT=$?

echo ""
echo "── Post-training ────────────────────────────────────────────────────"
echo "   End        : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "   Exit code  : ${TRAIN_EXIT}"

if [ ${TRAIN_EXIT} -eq 0 ]; then
    echo ""
    ls -lh "${OUTPUT_DIR}/" 2>/dev/null
    ls -lh "${OUTPUT_DIR}/final-adapter/" 2>/dev/null
    echo ""
    echo "✅ Training completed successfully."
    echo ""
    echo "   Next: merge adapters:"
    echo "   sbatch ${SCRIPTS_DIR}/merge_judge_lora.sh"
else
    echo "❌ Training failed with exit code ${TRAIN_EXIT}."
    exit ${TRAIN_EXIT}
fi
