#!/bin/bash
# slurm/lumig_job5_enrichment_shard.slurm
# SLURM job: Qwen3.5-35B-A3B offline-batch data-parallel enrichment shard.
#
# Model history: deepseek-ai/DeepSeek-V4-Flash was the first choice but is a
# confirmed dead end on this cluster's vLLM build (its native FP8 checkpoint
# raises "deepseek_v4_fp8 quantization is currently not supported in rocm"
# at engine init, job 21676155, 2026-09-01). Switched to Qwen/Qwen3.5-35B-A3B
# -- current, unquantized (plain BF16), already this project's own
# designated distillation teacher (slurm/lumig_job2_distillation.slurm).
#
# ONLY submit this after slurm/lumig_job5_enrichment_smoketest.slurm has
# completed successfully -- it both proves the checkpoint actually loads
# and generates on this cluster's hardware, and populates the shared
# scratch HF cache so these shards don't race each other downloading the
# same checkpoint.
#
# Data-parallel sharding (4 independent single-node jobs), not multi-node
# tensor parallelism: each shard is a complete, low-risk single-node vLLM
# run (matches the proven pattern in scripts/generate_judge_dataset.sh /
# TASK-9's 3-shard datagen), rather than one job spanning multiple nodes
# via Ray -- multi-node TP on ROCm is real added complexity/risk that
# a batch-generation workload like this does not need.
#
# Usage (submit all 4 shards after the smoke test passes):
#   for i in 0 1 2 3; do sbatch slurm/lumig_job5_enrichment_shard.slurm $i; done

#SBATCH --job-name=moe_enrichment_shard
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=08:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/enrichment_shard_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/enrichment_shard_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- current shard output stays on disk (flushed after every generation batch)."
    pkill -SIGINT -f python3 || true
    sync
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

SHARD_ID="${1:?Usage: sbatch lumig_job5_enrichment_shard.slurm <shard_id 0-3>}"
NUM_SHARDS=4

# No host-level `module load` -- see lumig_job5_enrichment_smoketest.slurm's
# comment: job2/job3 run entirely inside the Singularity container, and an
# earlier version of this script's `module load rocm/5.7.1` failed outright
# (module no longer exists on this stack).

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"
OUT_DIR="${SCRATCH}/datasets/enrichment"
MODEL="Qwen/Qwen3.5-35B-A3B"

export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512

# See lumig_job5_enrichment_smoketest.slurm: $USER's /users home is on the
# default LUMI-G quota (~20GB, already over it) -- redirect every cache dir
# that would otherwise default under $HOME to scratch instead.
TRITON_CACHE="${SCRATCH}/triton_cache"
TORCHINDUCTOR_CACHE="${SCRATCH}/torchinductor_cache"
XDG_CACHE="${SCRATCH}/xdg_cache"
SCRATCH_TMP="${SCRATCH}/tmp"
mkdir -p "$TRITON_CACHE" "$TORCHINDUCTOR_CACHE" "$XDG_CACHE" "$SCRATCH_TMP"

# Per-shard targets (x4 shards): ~300 loom scenario-pair attempts total,
# ~3040 grounding requests total (8 categories), ~216 persistence requests
# total (2 categories) -- generous relative to the 40/18/4 hand-written
# baseline this is meant to diversify, without turning into an
# unbounded/runaway generation job.
LOOM_COUNT=75
GROUNDING_COUNT_PER_CATEGORY=95
PERSISTENCE_COUNT_PER_CATEGORY=27

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
echo "Job ID       : $SLURM_JOB_ID"
echo "Shard        : $SHARD_ID / $NUM_SHARDS"
echo "Node         : $SLURMD_NODENAME"
echo "Model        : $MODEL"
echo "Loom count   : $LOOM_COUNT"
echo "Grounding/cat: $GROUNDING_COUNT_PER_CATEGORY"
echo "Persist/cat  : $PERSISTENCE_COUNT_PER_CATEGORY"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Shard $SHARD_ID: --mode loom ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode loom --model "$MODEL" --tensor-parallel-size 8 \
    --count "$LOOM_COUNT" \
    --output "$OUT_DIR/loom_shard${SHARD_ID}.jsonl"

echo "[$(date +%H:%M:%S)] Shard $SHARD_ID: --mode grounding (Candidate 2) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 8 \
    --count-per-category "$GROUNDING_COUNT_PER_CATEGORY" \
    --output "$OUT_DIR/grounding_shard${SHARD_ID}.jsonl"

echo "[$(date +%H:%M:%S)] Shard $SHARD_ID: --mode grounding --persistence (Candidate 4) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --persistence --model "$MODEL" --tensor-parallel-size 8 \
    --count-per-category "$PERSISTENCE_COUNT_PER_CATEGORY" \
    --output "$OUT_DIR/persistence_shard${SHARD_ID}.jsonl"

echo "========================================"
echo "Shard $SHARD_ID complete."
wc -l "$OUT_DIR/loom_shard${SHARD_ID}.jsonl" "$OUT_DIR/grounding_shard${SHARD_ID}.jsonl" "$OUT_DIR/persistence_shard${SHARD_ID}.jsonl" 2>/dev/null || true
echo "========================================"
