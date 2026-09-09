#!/bin/bash
# slurm/lumig_job14_coder_pilot_bulk.slurm
# LUMI-G bulk portion of the `coder` pilot role's role_sft generation
# (~/.claude/plans/zazzy-beaming-koala.md Phase 2/3 pilot). GLM-4.5-Air:
# already proven for --mode role_sft --role coder (job 21798692, 4/5 real,
# high-quality examples after the delimiter-format fix).
#
# 1800 examples target for this LUMI-G portion; the remaining ~700 of the
# 2500 role_sft target come from OpenRouter (Kimi K3 depth + Mistral
# Large 3 volume) in parallel, plus a separate 500-raw Loom-candidate batch
# (also OpenRouter/Kimi K3) verified against the Netcup sandbox VMs.
# Throughput is unmeasured at this scale (prior tests were 5-20 examples) --
# 10h walltime is a generous first estimate, not a guarantee; check
# progress via the incremental JSONL output before assuming it needs more.

#SBATCH --job-name=moe_coder_pilot_bulk
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=10:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/coder_pilot_bulk_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/coder_pilot_bulk_%j.err

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
OUT_DIR="${SCRATCH}/datasets/coder_pilot"
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
echo "Purpose  : Coder pilot -- LUMI-G bulk role_sft portion (target 1800)"
echo "========================================"

cd "$SCRIPT_DIR"

$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode role_sft --role coder --model "$MODEL" --tensor-parallel-size 8 \
    --count 1800 --batch-size 16 --max-tokens 4096 \
    --output "$OUT_DIR/role_sft_coder_lumig_glm45air.jsonl"

LINES=$(wc -l < "$OUT_DIR/role_sft_coder_lumig_glm45air.jsonl" 2>/dev/null || echo 0)
echo "Done: $LINES examples -> $OUT_DIR/role_sft_coder_lumig_glm45air.jsonl"
