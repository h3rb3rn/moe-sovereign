#!/bin/bash
# scripts/lumi_generate_planner_dataset.sh
# SLURM job: vLLM teacher server + planner dataset generator on LUMI-G
#
# Target model : phi4:14b  (fine-tuning target, not run here — see lumi_sft_phi4.sh)
# Teacher model: Llama-3.1-405B (primary, 10/10 quality, Q4_K_M ≈ 223 GB / 1 LUMI-G node)
#                Qwen3-235B-A22B fallback (9/10, 130 GB Q4)
#
# Usage:
#   sbatch lumi_generate_planner_dataset.sh
#   MODEL=Qwen/Qwen3-235B-A22B-Instruct sbatch lumi_generate_planner_dataset.sh
#
# Estimated runtimes (200K samples, 1 LUMI-G node):
#   Llama-3.1-405B  : ~20-24h  (≈302 GPU-h)
#   Qwen3-235B-A22B : ~12-16h  (≈160 GPU-h, MoE = fewer active params)

#SBATCH --job-name=planner_dataset
#SBATCH --account=project_465003058
#SBATCH --partition=standard-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=26:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/planner_dataset_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/logs/planner_dataset_%j.err

# ── Configuration ─────────────────────────────────────────────────────────────
# Primary: Llama-3.1-405B (10/10 quality, Q4_K_M ≈ 223 GB, TP=8 full node)
# Fallback: MODEL=Qwen/Qwen3-235B-A22B-Instruct sbatch ...
MODEL="${MODEL:-meta-llama/Meta-Llama-3.1-405B-Instruct}"
TARGET="${TARGET:-200000}"
CONCURRENCY="${CONCURRENCY:-48}"
OUTPUT_DIR="/scratch/project_465003058/hornphil/planner_dataset_${SLURM_JOB_ID}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VLLM_PORT=8080
VLLM_API="http://localhost:${VLLM_PORT}/v1"

# Model → HF ID map (allows short aliases when overriding MODEL)
declare -A MODEL_HF_MAP=(
    ["llama405b"]="meta-llama/Meta-Llama-3.1-405B-Instruct"
    ["qwen235b"]="Qwen/Qwen3-235B-A22B-Instruct"
    ["qwen3.5:122b"]="Qwen/Qwen3.5-72B-Instruct"
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
CONTAINER="/appl/local/containers/sif-images/lumi-pytorch-rocm-6.2.4-python-3.12-pytorch-v2.5.1-dockerhash-58438f9e.sif"

if [ ! -f "$CONTAINER" ]; then
    echo "Container not found: $CONTAINER — using system Python"
    PYTHON="python3"
    VLLM_CMD="python3 -m vllm.entrypoints.openai.api_server"
else
    PYTHON="singularity exec --bind /scratch,/flash,/projappl $CONTAINER python3"
    VLLM_CMD="singularity exec --bind /scratch,/flash,/projappl $CONTAINER python3 -m vllm.entrypoints.openai.api_server"
fi

# ── Start vLLM server ─────────────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting vLLM server with $HF_MODEL ..."

# Determine tensor parallelism from model size
# 405B dense and large MoE models → TP=8 (full LUMI-G node, 512 GB HBM2e)
# Mid-size MoE (235B, 30B active) → TP=8 recommended for throughput
# Smaller models → TP=4
if echo "$HF_MODEL" | grep -q "405B\|235B\|72B\|122B\|110B"; then
    TP=8
elif echo "$HF_MODEL" | grep -q "35B\|30B"; then
    TP=4
else
    TP=2
fi
echo "Tensor parallelism: TP=$TP"

$VLLM_CMD \
    --model "$HF_MODEL" \
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
