#!/bin/bash
#SBATCH --job-name=sovereign-judge-lora
#SBATCH --account=project_465003058
#SBATCH --partition=small-g          # 1 node, up to 8 MI250x GCDs (each 64 GB HBM2e)
#SBATCH --nodes=1
#SBATCH --gpus=1                     # 1 GCD = 64 GB HBM2e — sufficient for 32B QLoRA with checkpointing
#SBATCH --time=04:00:00              # Max 4 h; 3-epoch LoRA on ~700 samples with 32B
#SBATCH --output=/scratch/project_465003058/hornphil/logs/judge_lora_%j.log

# ── Directories ────────────────────────────────────────────────────────────
SCRATCH=/scratch/project_465003058/hornphil
DATA_DIR="${SCRATCH}/data"
MODELS_DIR="${SCRATCH}/models"
SCRIPTS_DIR="${SCRATCH}/scripts"
LOGS_DIR="${SCRATCH}/logs"
HF_CACHE="${SCRATCH}/hf_cache"

mkdir -p "${LOGS_DIR}" "${MODELS_DIR}"

# ── Model configuration ─────────────────────────────────────────────────────
# Qwen/Qwen2.5-32B-Instruct — already cached in HF_CACHE from previous LUMI runs.
# Snapshot hash from June 2026 cache.
SNAPSHOT=$(ls "${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/" 2>/dev/null | head -1)
BASE_MODEL="${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/${SNAPSHOT}"

# Fallback: use HF model ID directly (requires internet — NOT available on compute nodes)
if [ ! -d "${BASE_MODEL}" ]; then
    echo "⚠️  Local model snapshot not found at ${BASE_MODEL}"
    echo "   Trying to use HF model ID directly (may fail without internet on compute node)."
    BASE_MODEL="Qwen/Qwen2.5-32B-Instruct"
fi

DATASET_PATH="${DATA_DIR}/paraconsistent_training_data.jsonl"
OUTPUT_DIR="${MODELS_DIR}/sovereign-judge-32b-lora"

echo "🚀 Sovereign Judge LoRA Training"
echo "   Base model  : ${BASE_MODEL}"
echo "   Dataset     : ${DATASET_PATH}"
echo "   Output      : ${OUTPUT_DIR}"
echo "   GPUs        : ${SLURM_GPUS}"
echo "   Job ID      : ${SLURM_JOB_ID}"
echo "   Start       : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Verify dataset exists ────────────────────────────────────────────────────
if [ ! -f "${DATASET_PATH}" ]; then
    echo "❌ Dataset not found at ${DATASET_PATH} — aborting."
    exit 1
fi

SAMPLE_COUNT=$(wc -l < "${DATASET_PATH}")
echo "   Dataset samples: ${SAMPLE_COUNT}"

# ── Environment ─────────────────────────────────────────────────────────────
module purge
module use /appl/local/laifs/modules
module load lumi-aif-singularity-bindings

export SIF=/appl/local/laifs/containers/lumi-multitorch-latest.sif
export HF_HOME="${HF_CACHE}"
export TRANSFORMERS_CACHE="${HF_CACHE}/transformers"
export TOKENIZERS_PARALLELISM=false

# ROCm-specific optimisations for AMD MI250x (using 1 GPU to avoid device-map partitioning issues)
export ROCR_VISIBLE_DEVICES=0
export HIP_VISIBLE_DEVICES=0
export PYTORCH_ALLOC_CONF=expandable_segments:True
export MASTER_ADDR=localhost
export MASTER_PORT=29500

echo ""
echo "── Container info ──────────────────────────────────────────────────"
singularity exec --bind /pfs,/scratch,/projappl,/project "${SIF}" python3 -c "
import torch
print(f'PyTorch version : {torch.__version__}')
print(f'CUDA/ROCm available: {torch.cuda.is_available()}')
print(f'Device count    : {torch.cuda.device_count()}')
for i in range(torch.cuda.device_count()):
    print(f'  GPU {i}: {torch.cuda.get_device_name(i)}')
"

echo ""
echo "── Checking required packages ──────────────────────────────────────"
singularity exec --bind /pfs,/scratch,/projappl,/project "${SIF}" python3 -c "
import importlib, sys
missing = []
for pkg in ['trl', 'peft', 'transformers', 'datasets', 'bitsandbytes']:
    try:
        m = importlib.import_module(pkg.replace('-', '_'))
        print(f'  ✅ {pkg} {getattr(m, \"__version__\", \"?\")}')
    except ImportError:
        missing.append(pkg)
        print(f'  ❌ {pkg} — MISSING')
if missing:
    sys.exit(1)
"

if [ $? -ne 0 ]; then
    echo "❌ Missing packages. Install them in the container or use a custom SIF."
    echo "   Attempting pip install inside container..."
    singularity exec --bind /pfs,/scratch,/projappl,/project "${SIF}" \
        pip install --quiet trl peft bitsandbytes accelerate 2>&1 | tail -5
fi

echo ""
echo "── Starting training ───────────────────────────────────────────────"
singularity exec \
    --bind /pfs,/scratch,/projappl,/project \
    "${SIF}" \
    python3 -u "${SCRIPTS_DIR}/train_judge_lora.py" \
        --base_model  "${BASE_MODEL}" \
        --dataset_path "${DATASET_PATH}" \
        --output_dir  "${OUTPUT_DIR}" \
        --epochs      3 \
        --batch_size  1 \
        --max_seq_len 2048 \
        --lora_r      16 \
        --lora_alpha  32

TRAIN_EXIT=$?

echo ""
echo "── Post-training ────────────────────────────────────────────────────"
echo "   End        : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "   Exit code  : ${TRAIN_EXIT}"

if [ ${TRAIN_EXIT} -eq 0 ]; then
    echo ""
    echo "   Output directory contents:"
    ls -lh "${OUTPUT_DIR}/" 2>/dev/null
    ls -lh "${OUTPUT_DIR}/final-adapter/" 2>/dev/null
    ls -lh "${OUTPUT_DIR}/merged/" 2>/dev/null
    echo ""
    echo "✅ Training completed successfully."
    echo ""
    echo "   To copy the merged model back to the deployment host, run:"
    echo "   scp -r lumi-g:${OUTPUT_DIR}/merged/ /opt/deployment/moe-sovereign/moe-infra/models/sovereign-judge-32b/"
else
    echo "❌ Training failed with exit code ${TRAIN_EXIT}."
    exit ${TRAIN_EXIT}
fi
