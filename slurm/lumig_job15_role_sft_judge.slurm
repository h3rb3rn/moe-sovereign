#!/bin/bash
# slurm/lumig_job15_role_sft_judge.slurm
# LUMI-G bulk role_sft generation for role=judge
# (~/.claude/plans/zazzy-beaming-koala.md Phase 2, scaling beyond the
# `coder` pilot). Qwen/Qwen3-Next-80B-A3B-Instruct: proven 24/24 in the Phase 1
# teacher smoke test (job 21699042) -- the reasoning-cluster Tier-A
# teacher for this role per the plan's finalized assignment table.
#
# Unlike the 7 generic expert roles, judge does NOT use the generic
# _ROLE_SFT_GENERATION_TEMPLATE / _GENERIC_ROLE_TOPIC_HINTS path at all --
# it uses its own hand-crafted _JUDGE_CRITIC_SFT_GENERATION_TEMPLATE +
# _JUDGE_CRITIC_PATTERN_FOCUS pattern-cycling (built directly from the real
# production rules in graph/planner.py / graph/synthesis.py), already
# structurally validated in the role_sft smoke test (job 21798692, 4/5
# real). The `coder` pilot's thematic-collapse bug (a single static prompt
# with no topic scaffolding) does not apply to this role's generation path
# -- still spot-check the first ~100 written examples for genuine
# diversity across the pattern set before trusting the full 1800-line
# output, per this project's "kein Gemini-Vorfall" discipline, but do not
# assume the coder-specific keyword-filter mechanism is relevant here.
#
# 1800 examples target; throughput/yield is unmeasured for this specific
# role (only `coder`'s ~35-70% yield range, depending on filter state, is
# measured) -- 10h walltime is the same generous first estimate used for
# the coder pilot, not a per-role-verified guarantee.

#SBATCH --job-name=moe_role_sft_judge
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=10:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_judge_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_judge_%j.err

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
OUT_DIR="${SCRATCH}/datasets/role_sft_judge"
MODEL="Qwen/Qwen3-Next-80B-A3B-Instruct"

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
echo "Purpose  : Role_sft LUMI-G bulk generation for role=judge (target 1800)"
echo "========================================"

cd "$SCRIPT_DIR"

$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode role_sft --role judge --model "$MODEL" --tensor-parallel-size 8 \
    --count 1800 --batch-size 16 --max-tokens 4096 \
    --output "$OUT_DIR/role_sft_judge_lumig.jsonl"

LINES=$(wc -l < "$OUT_DIR/role_sft_judge_lumig.jsonl" 2>/dev/null || echo 0)
echo "Done: $LINES examples -> $OUT_DIR/role_sft_judge_lumig.jsonl"
