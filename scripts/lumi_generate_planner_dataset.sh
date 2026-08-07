#!/bin/bash
# scripts/lumi_generate_planner_dataset.sh
# SLURM job: vLLM teacher server + planner dataset generator on LUMI-G
#
# Target model : phi4:14b  (fine-tuning target, not run here — see lumi_sft_phi4.sh)
# Teacher model: Qwen/Qwen2.5-72B-Instruct  (primary, 72B dense, 8-9/10 quality, 144 GB BF16)
#                Fits on 1 LUMI-G node: TP=8 → 18 GB/GPU, 46 GB KV-cache per GCD.
#                Llama-3.x models require HF license approval at:
#                  https://huggingface.co/meta-llama/Llama-3.3-70B-Instruct
#                  https://huggingface.co/meta-llama/Meta-Llama-3.1-70B-Instruct
#                Fallback: Qwen/Qwen2.5-32B-Instruct (already cached, smaller)
#
# Usage:
#   sbatch lumi_generate_planner_dataset.sh
#   MODEL=Qwen/Qwen2.5-32B-Instruct sbatch lumi_generate_planner_dataset.sh
#
# Estimated runtimes (200K samples, 1 LUMI-G node, TP=8):
#   Qwen2.5-72B : ~6-10h  (≈48-80 GPU-h)
#   Qwen2.5-32B : ~3-5h   (≈24-40 GPU-h, already cached)

#SBATCH --job-name=planner_dataset
#SBATCH --account=project_465003058
#SBATCH --partition=standard-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=20:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/planner_dataset_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/logs/planner_dataset_%j.err

# ── Configuration ─────────────────────────────────────────────────────────────
# Primary: Qwen2.5-72B (72B dense, accessible, 144 GB BF16, TP=8 on 1 LUMI-G node)
# Fallback: MODEL=Qwen/Qwen2.5-32B-Instruct sbatch ...  (already cached)
MODEL="${MODEL:-meta-llama/Meta-Llama-3.1-405B-Instruct}"
TARGET="${TARGET:-200000}"
CONCURRENCY="${CONCURRENCY:-48}"
HF_CACHE="/scratch/project_465003058/hornphil/hf_cache"
OUTPUT_DIR="/scratch/project_465003058/hornphil/planner_dataset_${SLURM_JOB_ID}"
SCRIPT_DIR="/scratch/project_465003058/hornphil/scripts"
VLLM_PORT=8080
VLLM_API="http://localhost:${VLLM_PORT}/v1"

# Model → HF ID map (allows short aliases when overriding MODEL)
declare -A MODEL_HF_MAP=(
    ["llama33-70b"]="meta-llama/Llama-3.3-70B-Instruct"
    ["qwen72b"]="Qwen/Qwen2.5-72B-Instruct"
    ["qwen32b"]="Qwen/Qwen2.5-32B-Instruct"
)
HF_MODEL="${MODEL_HF_MAP[$MODEL]:-$MODEL}"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURMD_NODENAME"
echo "Model       : $MODEL  →  $HF_MODEL"
echo "Target      : $TARGET samples"
echo "Concurrency : $CONCURRENCY"
echo "Output dir  : $OUTPUT_DIR"
echo "========================================"

mkdir -p "$OUTPUT_DIR"
mkdir -p /scratch/project_465003058/hornphil/logs

# ── Load modules ──────────────────────────────────────────────────────────────
module load LUMI/23.09
module load partition/G
module load rocm/5.7.1

# ── Container / Python environment ───────────────────────────────────────────
# Assumes the lumi-multitorch container is available with vLLM installed.
# If not: pip install vllm httpx in a virtual environment first.
CONTAINER="/scratch/project_465003058/hornphil/lumi-multitorch-latest.sif"

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    echo "Expected lumi-multitorch-latest.sif in hornphil scratch directory."
    exit 1
fi

HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null)"
SING_OPTS="--bind /scratch/project_465003058:/scratch/project_465003058 --env HF_HOME=$HF_CACHE --env HUGGING_FACE_HUB_TOKEN=$HF_TOKEN"

PYTHON="singularity exec $SING_OPTS $CONTAINER python3"
VLLM_CMD="singularity exec $SING_OPTS $CONTAINER python3 -m vllm.entrypoints.openai.api_server"

# Resolve local HF snapshot path to avoid re-downloading gated models
MODEL_DIR="$HF_CACHE/models--$(echo $HF_MODEL | tr '/' '--')"
SNAP_PATH="$(ls -d $MODEL_DIR/snapshots/*/  2>/dev/null | head -1)"
if [ -n "$SNAP_PATH" ] && [ -d "$SNAP_PATH" ]; then
    VLLM_MODEL="${SNAP_PATH%/}"
    echo "Using local snapshot: $VLLM_MODEL"
else
    VLLM_MODEL="$HF_MODEL"
    echo "No local snapshot found, will download: $HF_MODEL"
fi

# ── Start vLLM server ─────────────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting vLLM server with $VLLM_MODEL ..."

# Tensor parallelism:
# ≥70B dense models → TP=8 (uses all 8 GCDs for max throughput & KV-cache per GCD)
# MoE or smaller models → TP=4
if echo "$HF_MODEL" | grep -q "70B\|72B\|405B\|235B\|122B\|110B"; then
    TP=8
elif echo "$HF_MODEL" | grep -q "35B\|30B\|32B"; then
    TP=4
else
    TP=2
fi
echo "Tensor parallelism: TP=$TP"

$VLLM_CMD \
    --model "$VLLM_MODEL" \
    --tensor-parallel-size $TP \
    --port $VLLM_PORT \
    --host 0.0.0.0 \
    --dtype bfloat16 \
    --max-model-len 4096 \
    --gpu-memory-utilization 0.90 \
    --enable-prefix-caching \
    --max-num-seqs $CONCURRENCY \
    --served-model-name "$(basename $HF_MODEL)" \
    &
VLLM_PID=$!

# ── Wait for vLLM to become ready (max 20 min) ────────────────────────────────
echo "[$(date +%H:%M:%S)] Waiting for vLLM to be ready ..."
READY=0
for i in $(seq 1 120); do
    if curl -s "http://localhost:${VLLM_PORT}/v1/models" | grep -q '"id"'; then
        echo "[$(date +%H:%M:%S)] vLLM ready after ${i}×10s"
        READY=1
        break
    fi
    sleep 10
done

if [ $READY -eq 0 ]; then
    echo "ERROR: vLLM did not become ready within 20 minutes."
    kill $VLLM_PID 2>/dev/null
    exit 1
fi

# Auto-detect registered model name from vLLM
VLLM_MODEL=$(curl -s "http://localhost:${VLLM_PORT}/v1/models" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data['data'][0]['id'])
")
echo "[$(date +%H:%M:%S)] vLLM serving model: $VLLM_MODEL"

# ── Run the dataset generator ─────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting dataset generator ..."

cd "$SCRIPT_DIR/.."
$PYTHON scripts/generate_planner_dataset.py \
    --output-dir "$OUTPUT_DIR" \
    --api-url "$VLLM_API" \
    --teacher "$VLLM_MODEL" \
    --target "$TARGET" \
    --concurrency "$CONCURRENCY" \
    --augment-factor 400 \
    --min-score 5 \
    --probe-interval 1000 \
    --max-error-rate 0.40 \
    --circuit-breaker-failures 10 \
    --circuit-breaker-reset 60 \
    --log-level INFO

GEN_EXIT=$?
echo "[$(date +%H:%M:%S)] Generator exited with code $GEN_EXIT"

# ── Cleanup ───────────────────────────────────────────────────────────────────
kill $VLLM_PID 2>/dev/null
wait $VLLM_PID 2>/dev/null

# ── Summary ───────────────────────────────────────────────────────────────────
echo "========================================"
echo "Output directory: $OUTPUT_DIR"
ls -lh "$OUTPUT_DIR/"
if [ -f "$OUTPUT_DIR/planner_chat.jsonl" ]; then
    LINES=$(wc -l < "$OUTPUT_DIR/planner_chat.jsonl")
    echo "Chat samples: $LINES"
fi
echo "========================================"
exit $GEN_EXIT
