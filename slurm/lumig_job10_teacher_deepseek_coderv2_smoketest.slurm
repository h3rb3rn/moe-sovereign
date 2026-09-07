#!/bin/bash
# slurm/lumig_job10_teacher_deepseek_coderv2_smoketest.slurm
# Phase 1 teacher-model smoke test (see ~/.claude/plans/zazzy-beaming-koala.md)
# for the LUMI-G full-finetuning plan: Tier B / coder-cluster stretch
# teacher deepseek-ai/DeepSeek-Coder-V2-Instruct (DeepseekV2ForCausalLM,
# unquantized, ~472GB weights -- TIGHT fit, ~59GB weights alone per GPU vs.
# 64GB physical). Confirmed architecture-registered + ungated via a
# config-only HF Hub check, no weights downloaded, prior to this job.
# Intended, if it passes, as a code-depth booster specifically for
# Candidate 1 (memory-ordering) alongside the Tier A GLM-4.5-Air baseline.
#
# Same tight-fit handling as job9 (Qwen3-235B): --gpu-memory-utilization
# raised to 0.95, --max-model-len reduced to 2048. A clean OOM here is a
# real, useful finding, not a bug -- Tier A (GLM-4.5-Air) alone still
# covers coder/security/datainfra if this model doesn't fit.
#
# --time bumped 02:00:00 -> 04:00:00 after job 21701732's first attempt:
# same root cause as job9 (Qwen3-235B) -- "Checkpoint size (439.11 GiB)
# exceeds 90% of available RAM (441.98 GiB). Skipping auto-prefetch", then
# ~200s/shard direct-Lustre reads (55 shards), hit the 2h wall at 89%
# loaded. Not an OOM/capacity failure -- a walltime-too-short failure.
#
# Usage:
#   sbatch slurm/lumig_job10_teacher_deepseek_coderv2_smoketest.slurm

#SBATCH --job-name=moe_teacher_dscoderv2_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_dscoderv2_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_dscoderv2_smoketest_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- exiting smoke test."
    pkill -SIGINT -f python3 || true
    sync
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"
OUT_DIR="${SCRATCH}/datasets/teacher_smoketests"
MODEL="deepseek-ai/DeepSeek-Coder-V2-Instruct"

TRITON_CACHE="${SCRATCH}/triton_cache"
TORCHINDUCTOR_CACHE="${SCRATCH}/torchinductor_cache"
XDG_CACHE="${SCRATCH}/xdg_cache"
SCRATCH_TMP="${SCRATCH}/tmp"
mkdir -p "$TRITON_CACHE" "$TORCHINDUCTOR_CACHE" "$XDG_CACHE" "$SCRATCH_TMP"

export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512

mkdir -p "$OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    exit 1
fi

SING_OPTS="--bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME=$HF_CACHE --env HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --env ROCR_VISIBLE_DEVICES=$ROCR_VISIBLE_DEVICES --env HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES \
    --env PYTORCH_HIP_ALLOC_CONF=$PYTORCH_HIP_ALLOC_CONF \
    --env TRITON_CACHE_DIR=$TRITON_CACHE --env TORCHINDUCTOR_CACHE_DIR=$TORCHINDUCTOR_CACHE \
    --env XDG_CACHE_HOME=$XDG_CACHE --env TMPDIR=$SCRATCH_TMP"
PYTHON="singularity exec $SING_OPTS $CONTAINER python3"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Node     : $SLURMD_NODENAME"
echo "Model    : $MODEL (DeepseekV2ForCausalLM, unquantized, ~472GB -- TIGHT)"
echo "Purpose  : Phase 1 teacher-model viability -- Tier B coder-cluster stretch"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode grounding (count-per-category=2, reduced max-model-len/high gpu-mem-util for tight fit) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 8 \
    --max-model-len 2048 --gpu-memory-utilization 0.95 \
    --count-per-category 2 --batch-size 2 \
    --output "$OUT_DIR/dscoderv2_grounding_smoketest.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/dscoderv2_grounding_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- DeepSeek-Coder-V2-Instruct confirmed as a viable Tier B teacher."
    exit 0
else
    echo "SMOKE TEST FAILED (or OOM) -- 0 parsed results. Drop this model from Tier B; Tier A (GLM-4.5-Air) still covers coder/security/datainfra. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
