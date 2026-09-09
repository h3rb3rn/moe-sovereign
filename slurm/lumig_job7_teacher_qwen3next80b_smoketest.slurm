#!/bin/bash
# slurm/lumig_job7_teacher_qwen3next80b_smoketest.slurm
# Phase 1 teacher-model smoke test (see ~/.claude/plans/zazzy-beaming-koala.md)
# for the LUMI-G full-finetuning plan: Tier A / reasoning-cluster teacher
# Qwen/Qwen3-Next-80B-A3B-Instruct (Qwen3NextForCausalLM, unquantized, ~163GB
# weights -- comfortable fit vs. the ~512GB/node HBM2e ceiling, confirmed
# architecture-registered + ungated via a config-only HF Hub check, no
# weights downloaded, prior to this job).
#
# Purpose: prove real load + real generation on this cluster before this
# model is used as the Tier A backbone teacher for the Planner/precision/
# graphrag/research/omni/judge role-cluster's synthetic training data.
# Mirrors the already-successful Qwen3.5-35B-A3B smoke test pattern
# (lumig_job5_enrichment_smoketest.slurm) -- same script, same --mode
# grounding call, only the --model changes. --mode loom deliberately
# skipped here: it has a known, separately-tracked 0-parsed-results bug
# (--max-tokens too low), unrelated to this teacher model's own viability.
#
# Usage:
#   sbatch slurm/lumig_job7_teacher_qwen3next80b_smoketest.slurm

#SBATCH --job-name=moe_teacher_qwen3next80b_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_qwen3next80b_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_qwen3next80b_smoketest_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- exiting smoke test."
    pkill -SIGINT -f python3 || true
    sync
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

# No host-level `module load` -- see lumig_job5_enrichment_smoketest.slurm's
# comment: training/generation runs entirely inside the Singularity
# container, which brings its own ROCm userspace.

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"
OUT_DIR="${SCRATCH}/datasets/teacher_smoketests"
MODEL="Qwen/Qwen3-Next-80B-A3B-Instruct"

# See lumig_job5_enrichment_smoketest.slurm: $HOME is on the small default
# LUMI-G quota -- redirect every cache dir that would otherwise default
# under $HOME to scratch instead.
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
echo "Model    : $MODEL (Qwen3NextForCausalLM, unquantized, ~163GB)"
echo "Purpose  : Phase 1 teacher-model viability -- Tier A reasoning cluster"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode grounding (count-per-category=3) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 8 \
    --count-per-category 3 --batch-size 3 \
    --output "$OUT_DIR/qwen3next80b_grounding_smoketest.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/qwen3next80b_grounding_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- Qwen3-Next-80B-A3B-Instruct confirmed as a viable Tier A teacher."
    echo "Sample output:"
    head -c 2000 "$OUT_DIR/qwen3next80b_grounding_smoketest.jsonl"
    exit 0
else
    echo "SMOKE TEST FAILED -- 0 parsed results. Do NOT use this model as a teacher yet. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
