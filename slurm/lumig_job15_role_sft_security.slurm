#!/bin/bash
# slurm/lumig_job15_role_sft_security.slurm
# LUMI-G bulk role_sft generation for role=security
# (~/.claude/plans/zazzy-beaming-koala.md Phase 2, scaling beyond the
# `coder` pilot). zai-org/GLM-4.5-Air: proven 24/24 after the parser fix (job
# 21705930) -- the code/governance-cluster Tier-A teacher for this
# role per the plan's finalized assignment table.
#
# The generic role_sft template + per-role topic-hint rotation +
# {other_hints} negative-constraint fix are proven end-to-end via the
# `coder` pilot (jobs 21814113/21827844/21829009) -- but the mechanical
# _GENERIC_ROLE_ATTRACTOR_KEYWORDS post-filter is CURRENTLY ONLY POPULATED
# FOR `coder`. This role has NOT been individually checked for the same
# kind of thematic collapse coder showed (72% then 54% then 22% lock-free
# concentration across three fix iterations) -- spot-check the first ~100
# written examples for topic diversity before trusting the full 1800-line
# output, per this project's "kein Gemini-Vorfall" discipline. If the same
# collapse pattern shows up, add this role's attractor keywords to
# scripts/generate_diverse_training_seeds.py's _GENERIC_ROLE_ATTRACTOR_KEYWORDS
# rather than assuming it is fine by analogy.
#
# 1800 examples target; throughput/yield is unmeasured for this specific
# role (only `coder`'s ~35-70% yield range, depending on filter state, is
# measured) -- 10h walltime is the same generous first estimate used for
# the coder pilot, not a per-role-verified guarantee.

#SBATCH --job-name=moe_role_sft_security
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=10:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_security_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_security_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- exiting, whatever is already flushed to disk is kept."
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
OUT_DIR="${SCRATCH}/datasets/role_sft_security"
MODEL="zai-org/GLM-4.5-Air"

TRITON_CACHE="${SCRATCH}/triton_cache"
TORCHINDUCTOR_CACHE="${SCRATCH}/torchinductor_cache"
XDG_CACHE="${SCRATCH}/xdg_cache"
SCRATCH_TMP="${SCRATCH}/tmp"
mkdir -p "$TRITON_CACHE" "$TORCHINDUCTOR_CACHE" "$XDG_CACHE" "$SCRATCH_TMP"

export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512

mkdir -p "$OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

SING_OPTS="--bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME=$HF_CACHE --env HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --env ROCR_VISIBLE_DEVICES=$ROCR_VISIBLE_DEVICES --env HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES \
    --env PYTORCH_HIP_ALLOC_CONF=$PYTORCH_HIP_ALLOC_CONF \
    --env TRITON_CACHE_DIR=$TRITON_CACHE --env TORCHINDUCTOR_CACHE_DIR=$TORCHINDUCTOR_CACHE \
    --env XDG_CACHE_HOME=$XDG_CACHE --env TMPDIR=$SCRATCH_TMP"
PYTHON="singularity exec $SING_OPTS $CONTAINER python3"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Model    : $MODEL"
echo "Purpose  : Role_sft LUMI-G bulk generation for role=security (target 1800)"
echo "========================================"

cd "$SCRIPT_DIR"

$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode role_sft --role security --model "$MODEL" --tensor-parallel-size 8 \
    --count 1800 --batch-size 16 --max-tokens 4096 \
    --output "$OUT_DIR/role_sft_security_lumig.jsonl"

LINES=$(wc -l < "$OUT_DIR/role_sft_security_lumig.jsonl" 2>/dev/null || echo 0)
echo "Done: $LINES examples -> $OUT_DIR/role_sft_security_lumig.jsonl"
