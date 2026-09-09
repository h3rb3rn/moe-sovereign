#!/bin/bash
# slurm/lumig_job13_loom_candidates_batch.slurm
# First real (not just smoke-test) batch of Loom memory-ordering candidate
# scenarios for Candidate 1 (docs/experiments/lumig_posttraining_candidates.md),
# feeding the now-complete 3-stage pipeline:
#   1. This job: generate_diverse_training_seeds.py --mode loom (raw candidates)
#   2. generate_loom_seed_examples.py --llm-scenarios-file (real sandbox
#      verification, now on the parallel Netcup VMs)
#   3. curate_coder_expert_dataset.py (final ChatML training data)
#
# GLM-4.5-Air: already proven reliable for --mode loom with the delimiter
# format fix (job 21798693, 2/2 clean after the min-length guard). Modest
# count (20) for this first real pass -- volume decision for the full run
# comes after Stage 2/3 verification confirms end-to-end quality.

#SBATCH --job-name=moe_loom_candidates_batch
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/loom_candidates_batch_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/loom_candidates_batch_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- exiting."
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
OUT_DIR="${SCRATCH}/datasets/loom_candidates"
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
echo "Purpose  : First real Loom candidate batch (Candidate 1 pipeline)"
echo "========================================"

cd "$SCRIPT_DIR"

$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode loom --model "$MODEL" --tensor-parallel-size 8 \
    --count 20 --batch-size 8 --max-tokens 6144 \
    --output "$OUT_DIR/loom_candidates_batch1.jsonl"

LINES=$(wc -l < "$OUT_DIR/loom_candidates_batch1.jsonl" 2>/dev/null || echo 0)
echo "Done: $LINES candidate pairs -> $OUT_DIR/loom_candidates_batch1.jsonl"
