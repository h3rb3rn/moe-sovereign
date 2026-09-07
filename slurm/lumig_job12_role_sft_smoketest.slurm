#!/bin/bash
# slurm/lumig_job12_role_sft_smoketest.slurm
# Phase 2 smoke test (see ~/.claude/plans/zazzy-beaming-koala.md) for the
# new `--mode role_sft` in scripts/generate_diverse_training_seeds.py --
# proves the mode actually produces real, parseable, correctly-ChatML-
# formatted training examples before any full-scale multi-role generation
# is submitted. Per the plan's non-negotiable principle: a code path that
# has never actually run is not "done", no matter how carefully it was
# written (this is the exact lesson from the Antigravity postmortem).
#
# Uses role=coder (Candidate 1, highest priority per
# docs/experiments/lumig_posttraining_candidates.md) with GLM-4.5-Air
# (Tier A, already confirmed 24/24 in Phase 1, fast-loading) -- deliberately
# NOT one of the slow-loading Tier B models, so this smoke test stays cheap
# and isolates whether role_sft itself works from the separate, already-
# understood RAM/Lustre-prefetch walltime issue.
#
# Usage:
#   sbatch slurm/lumig_job12_role_sft_smoketest.slurm

#SBATCH --job-name=moe_role_sft_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_smoketest_%j.err

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
OUT_DIR="${SCRATCH}/datasets/role_sft_smoketests"
MODEL="zai-org/GLM-4.5-Air"
ROLE="coder"

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
echo "Model    : $MODEL"
echo "Role     : $ROLE"
echo "Purpose  : Phase 2 role_sft mode smoke test"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode role_sft --role $ROLE (count=5) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode role_sft --role "$ROLE" --model "$MODEL" --tensor-parallel-size 8 \
    --count 5 --batch-size 5 --max-tokens 3072 \
    --output "$OUT_DIR/role_sft_${ROLE}_smoketest.jsonl"
LINES=$(wc -l < "$OUT_DIR/role_sft_${ROLE}_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] role_sft smoke test: $LINES parsed example(s)"

echo "========================================"
if [ "$LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- role_sft mode confirmed working for role=$ROLE."
    echo "Sample output (first record):"
    head -1 "$OUT_DIR/role_sft_${ROLE}_smoketest.jsonl"
    exit 0
else
    echo "SMOKE TEST FAILED -- 0 parsed results. Do NOT scale role_sft yet. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
