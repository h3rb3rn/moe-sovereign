#!/bin/bash
# scripts/lumi_dynamic_topup.sh
# SLURM job: generates ~50 000 "dynamic"-category planner samples on LUMI-G.
#
# Runs after the main planner_dataset job (20140407) has finished.
# Teacher: Llama-3.3-70B-Instruct (already cached in HF_CACHE).
# Seeds: restricted to _SEEDS_BY_TYPE["dynamic"] via --seed-categories dynamic.
# Augmentation: 250 batches × 20 queries → ~5 000 augmented seeds → 50 000 samples.
#
# Usage (on LUMI login node):
#   sbatch scripts/lumi_dynamic_topup.sh
#   sbatch --dependency=afterok:<JOB_ID> scripts/lumi_dynamic_topup.sh

#SBATCH --job-name=planner_dynamic_topup
#SBATCH --account=project_465003058
#SBATCH --partition=standard-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=12:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/planner_dynamic_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/logs/planner_dynamic_%j.err

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL="${MODEL:-meta-llama/Llama-3.3-70B-Instruct}"
TARGET="${TARGET:-50000}"
CONCURRENCY="${CONCURRENCY:-48}"
HF_CACHE="/scratch/project_465003058/hornphil/hf_cache"
OUTPUT_DIR="/scratch/project_465003058/hornphil/planner_dynamic_${SLURM_JOB_ID}"
SCRIPT_DIR="/scratch/project_465003058/hornphil/scripts"
VLLM_PORT=8080
VLLM_API="http://localhost:${VLLM_PORT}/v1"

declare -A MODEL_HF_MAP=(
    ["llama33-70b"]="meta-llama/Llama-3.3-70B-Instruct"
    ["qwen72b"]="Qwen/Qwen2.5-72B-Instruct"
    ["qwen32b"]="Qwen/Qwen2.5-32B-Instruct"
)
HF_MODEL="${MODEL_HF_MAP[$MODEL]:-$MODEL}"

# TP: 70B → TP=8 (18 GB/GPU BF16)
if echo "$HF_MODEL" | grep -q "70B\|72B\|405B\|235B\|122B\|110B"; then TP=8
elif echo "$HF_MODEL" | grep -q "32B\|34B\|30B"; then TP=4
else TP=2
fi

echo "=== LUMI-G Dynamic Top-up Job ==="
echo "Job ID      : $SLURM_JOB_ID"
echo "Teacher     : $HF_MODEL  (TP=$TP)"
echo "Target      : $TARGET dynamic samples"
echo "Output dir  : $OUTPUT_DIR"
echo "Started     : $(date)"
echo ""

# ── Module setup ──────────────────────────────────────────────────────────────
module purge
module load LUMI/24.03 partition/G

HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null)"
export HF_HOME="$HF_CACHE"
export HUGGINGFACE_HUB_CACHE="$HF_CACHE"
export HF_DATASETS_CACHE="$HF_CACHE"
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7

mkdir -p "$OUTPUT_DIR"
mkdir -p /scratch/project_465003058/hornphil/logs

# ── Launch vLLM teacher server ─────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting vLLM server with $HF_MODEL ..."

singularity exec \
    --bind "$HF_CACHE:$HF_CACHE" \
    --bind "$OUTPUT_DIR:$OUTPUT_DIR" \
    --bind "$SCRIPT_DIR:$SCRIPT_DIR" \
    --env HF_HOME="$HF_CACHE" \
    --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
    --env HUGGINGFACE_HUB_CACHE="$HF_CACHE" \
    /scratch/project_465003058/hornphil/lumi-multitorch-latest.sif \
    python -m vllm.entrypoints.openai.api_server \
        --model "$HF_MODEL" \
        --tensor-parallel-size $TP \
        --dtype bfloat16 \
        --max-model-len 8192 \
        --gpu-memory-utilization 0.85 \
        --port $VLLM_PORT \
        --trust-remote-code \
    &
VLLM_PID=$!

# Wait for vLLM to be ready (up to 10 min)
echo "[$(date +%H:%M:%S)] Waiting for vLLM API ..."
for i in $(seq 1 60); do
    if curl -sf "$VLLM_API/models" > /dev/null 2>&1; then
        echo "[$(date +%H:%M:%S)] vLLM ready after ${i}×10s"
        break
    fi
    sleep 10
done

if ! curl -sf "$VLLM_API/models" > /dev/null 2>&1; then
    echo "ERROR: vLLM did not start in time. Aborting."
    kill $VLLM_PID 2>/dev/null
    exit 1
fi

# ── Run dynamic-only dataset generator ────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting dynamic dataset generator ..."

singularity exec \
    --bind "$HF_CACHE:$HF_CACHE" \
    --bind "$OUTPUT_DIR:$OUTPUT_DIR" \
    --bind "$SCRIPT_DIR:$SCRIPT_DIR" \
    /scratch/project_465003058/hornphil/lumi-multitorch-latest.sif \
    python "$SCRIPT_DIR/generate_planner_dataset.py" \
        --output-dir  "$OUTPUT_DIR" \
        --api-url     "$VLLM_API" \
        --teacher     "$HF_MODEL" \
        --target      "$TARGET" \
        --concurrency "$CONCURRENCY" \
        --augment-factor 250 \
        --seed-categories dynamic \
        --min-score   4 \
        --neg-fraction 0.10 \
        --probe-interval 2000

GEN_EXIT=$?

# ── Teardown ──────────────────────────────────────────────────────────────────
kill $VLLM_PID 2>/dev/null
wait $VLLM_PID 2>/dev/null

echo ""
echo "[$(date +%H:%M:%S)] Generator exit code: $GEN_EXIT"
echo "=== Output ==="
ls -lh "$OUTPUT_DIR/" 2>/dev/null
echo ""
LINES=$(wc -l < "$OUTPUT_DIR/planner_chat.jsonl" 2>/dev/null || echo 0)
echo "Generated samples: $LINES"
echo "Finished: $(date)"

exit $GEN_EXIT
