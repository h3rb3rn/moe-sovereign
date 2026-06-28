#!/bin/bash
#SBATCH --job-name=judge-datagen
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus=8                     # 8 GCDs for vLLM inference (Qwen-32B needs ~70GB in FP16)
#SBATCH --time=08:00:00              # 8h — generates ~3000-5000 samples at ~5s/sample
#SBATCH --output=/scratch/project_465003058/hornphil/logs/datagen_%j.log
#SBATCH --mem=200G

# ── Config ──────────────────────────────────────────────────────────────────
SCRATCH=/scratch/project_465003058/hornphil
SCRIPTS_DIR="${SCRATCH}/scripts"
DATA_DIR="${SCRATCH}/data"
MODELS_DIR="${SCRATCH}/models"
HF_CACHE="${SCRATCH}/hf_cache"
LOGS_DIR="${SCRATCH}/logs"

SNAPSHOT=$(ls "${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/" 2>/dev/null | head -1)
BASE_MODEL="${HF_CACHE}/models--Qwen--Qwen2.5-32B-Instruct/snapshots/${SNAPSHOT}"

SEED_FILE="${DATA_DIR}/routellm_train.jsonl"
# Allow sharding: each parallel job writes to its own file and processes a different offset
SHARD_ID="${SHARD_ID:-0}"
TARGET="${TARGET:-30000}"
OFFSET="${OFFSET:-0}"
OUTPUT_FILE="${DATA_DIR}/paraconsistent_shard${SHARD_ID}.jsonl"
VLLM_PORT="${VLLM_PORT:-8080}"
VLLM_URL="http://localhost:${VLLM_PORT}/v1"

echo "🗄️  Sovereign Judge Dataset Generation"
echo "   Base model  : ${BASE_MODEL}"
echo "   Seed file   : ${SEED_FILE}"
echo "   Output file : ${OUTPUT_FILE}"
echo "   Shard ID    : ${SHARD_ID}"
echo "   Offset      : ${OFFSET}"
echo "   Target      : ${TARGET} samples"
echo "   Start       : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "${DATA_DIR}" "${LOGS_DIR}"

# ── Environment ──────────────────────────────────────────────────────────────
module purge
module use /appl/local/laifs/modules
module load lumi-aif-singularity-bindings

export SIF=/appl/local/laifs/containers/lumi-multitorch-latest.sif
export HF_HOME="${HF_CACHE}"
export TRANSFORMERS_CACHE="${HF_CACHE}/transformers"
export TOKENIZERS_PARALLELISM=false
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7

# ── Copy seed data to scratch if needed ──────────────────────────────────────
if [ ! -f "${SEED_FILE}" ]; then
    echo "⚙️  Seed file not found on scratch, using existing paraconsistent_training_data.jsonl as fallback..."
    # If RouteLLM data is not on scratch, we generate from the existing small dataset
    # The user should upload routellm/train.jsonl to scratch separately, OR we bootstrap from
    # the existing data directory. Check if there's a routellm directory:
    SEED_FILE="${DATA_DIR}/paraconsistent_training_data.jsonl"
    echo "   Using seed: ${SEED_FILE}"
fi

echo ""
echo "── Checking seed file ──────────────────────────────────────────────"
wc -l "${SEED_FILE}"

# ── Start vLLM inference server ──────────────────────────────────────────────
echo ""
echo "── Starting vLLM inference server ─────────────────────────────────"
singularity exec \
    --bind /pfs,/scratch,/projappl,/project \
    "${SIF}" \
    python3 -m vllm.entrypoints.openai.api_server \
        --model "${BASE_MODEL}" \
        --dtype bfloat16 \
        --tensor-parallel-size 8 \
        --max-model-len 4096 \
        --gpu-memory-utilization 0.90 \
        --port "${VLLM_PORT}" \
        --host 0.0.0.0 \
        --served-model-name qwen \
        --trust-remote-code \
    > "${LOGS_DIR}/vllm_${SLURM_JOB_ID}.log" 2>&1 &

VLLM_PID=$!
echo "   vLLM PID: ${VLLM_PID}"
echo "   Waiting for server to initialize (~90s)..."
sleep 90

# Retry health check up to 10 minutes
for i in $(seq 1 20); do
    if curl -sf "http://localhost:${VLLM_PORT}/v1/models" > /dev/null 2>&1; then
        echo "   ✅ vLLM healthy after $((90 + i*30))s"
        break
    fi
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "❌ vLLM process died. Check ${LOGS_DIR}/vllm_${SLURM_JOB_ID}.log"
        exit 1
    fi
    echo "   Attempt ${i}/20: not ready yet, retrying in 30s..."
    sleep 30
done

# ── Run dataset generator ─────────────────────────────────────────────────────
echo ""
echo "── Starting dataset generation (Shard=${SHARD_ID}, Offset=${OFFSET}, Target=${TARGET}) ──"
singularity exec \
    --bind /pfs,/scratch,/projappl,/project \
    "${SIF}" \
    python3 "${SCRIPTS_DIR}/generate_judge_dataset.py" \
        --seed_file "${SEED_FILE}" \
        --output_file "${OUTPUT_FILE}" \
        --api_url "${VLLM_URL}" \
        --model qwen \
        --target "${TARGET}" \
        --offset "${OFFSET}" \
        --use_existing_answers \
        --seed 42

GEN_EXIT=$?

# ── Cleanup ──────────────────────────────────────────────────────────────────
kill "${VLLM_PID}" 2>/dev/null

echo ""
echo "── Summary ──────────────────────────────────────────────────────────"
echo "   End        : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "   Exit code  : ${GEN_EXIT}"

if [ ${GEN_EXIT} -eq 0 ]; then
    SAMPLE_COUNT=$(wc -l < "${OUTPUT_FILE}" 2>/dev/null || echo "0")
    echo "   Samples    : ${SAMPLE_COUNT}"
    echo ""
    echo "✅ Dataset generation complete!"
    echo ""
    echo "   Next: Submit training job with new dataset:"
    echo "   sbatch ${SCRIPTS_DIR}/train_judge_lora.sh"
else
    echo "❌ Generation failed (exit code ${GEN_EXIT})"
    echo "   Check ${LOGS_DIR}/vllm_${SLURM_JOB_ID}.log for vLLM errors."
    exit ${GEN_EXIT}
fi
