#!/bin/bash
# slurm/lumig_job9_teacher_qwen3_235b_smoketest.slurm
# Phase 1 teacher-model smoke test (see ~/.claude/plans/zazzy-beaming-koala.md)
# for the LUMI-G full-finetuning plan: Tier B / reasoning-cluster stretch
# teacher Qwen/Qwen3-235B-A22B-Instruct-2507 (Qwen3MoeForCausalLM,
# unquantized, ~470GB weights -- TIGHT fit vs. ~512GB/node HBM2e, ~58.75GB
# weights alone per GPU vs. 64GB physical). Confirmed architecture-
# registered + ungated via a config-only HF Hub check, no weights
# downloaded, prior to this job.
#
# Unlike the Tier A smoke tests, this one is EXPECTED to be marginal:
# vLLM's default gpu_memory_utilization=0.9 (57.6GB/GPU budget) is smaller
# than the weights alone need, so --gpu-memory-utilization is raised to
# 0.95 and --max-model-len reduced to 2048 to minimize the additional KV-
# cache reservation vLLM tries to make. If this still OOMs, that is a
# real, useful finding (drop this model from Tier B, Tier A alone still
# covers the reasoning cluster) -- not a bug to route around.
#
# --time bumped 02:00:00 -> 04:00:00 after job 21701731's first attempt:
# NOT an OOM -- vLLM logged "Checkpoint size (437.90 GiB) exceeds 90% of
# available RAM (447.86 GiB). Skipping auto-prefetch", then loaded
# safetensors shards directly off Lustre at ~100s/shard (118 shards) instead
# of the ~1s/shard seen for comfortably-RAM-sized checkpoints -- job hit the
# 2h wall at 78% loaded, never got to generate. This is a real, separate
# 4th constraint (checkpoint size vs. node RAM, not GPU VRAM) worth folding
# back into the plan's compatibility matrix.
#
# Usage:
#   sbatch slurm/lumig_job9_teacher_qwen3_235b_smoketest.slurm

#SBATCH --job-name=moe_teacher_qwen3_235b_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_qwen3_235b_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_qwen3_235b_smoketest_%j.err

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
MODEL="Qwen/Qwen3-235B-A22B-Instruct-2507"

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
echo "Model    : $MODEL (Qwen3MoeForCausalLM, unquantized, ~470GB -- TIGHT)"
echo "Purpose  : Phase 1 teacher-model viability -- Tier B reasoning-cluster stretch"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode grounding (count-per-category=2, reduced max-model-len/high gpu-mem-util for tight fit) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 8 \
    --max-model-len 2048 --gpu-memory-utilization 0.95 \
    --count-per-category 2 --batch-size 2 \
    --output "$OUT_DIR/qwen3_235b_grounding_smoketest.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/qwen3_235b_grounding_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- Qwen3-235B-A22B-Instruct confirmed as a viable Tier B teacher."
    exit 0
else
    echo "SMOKE TEST FAILED (or OOM) -- 0 parsed results. Drop this model from Tier B; Tier A (Qwen3-Next-80B-A3B) still covers the reasoning cluster. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
