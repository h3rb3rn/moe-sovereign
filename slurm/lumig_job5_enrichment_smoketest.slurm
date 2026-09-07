#!/bin/bash
# slurm/lumig_job5_enrichment_smoketest.slurm
# SLURM job: Qwen3.5-35B-A3B offline-batch smoke test on LUMI-G.
#
# Purpose: verify the enrichment generation script actually works on this
# cluster before committing the full "großzügig" multi-shard run
# (slurm/lumig_job5_enrichment_shard.slurm, 4 parallel nodes x several
# hours each). Model history: the first choice, deepseek-ai/DeepSeek-V4-Flash,
# is a CONFIRMED dead end here -- its native blockwise-FP8 checkpoint made
# vLLM raise "deepseek_v4_fp8 quantization is currently not supported in
# rocm" at engine init (job 21676155, 2026-09-01). Switched to
# Qwen/Qwen3.5-35B-A3B: current (not the older Qwen2.5 line), ships
# unquantized (no quantization_config -> plain BF16, avoiding the whole
# class of exotic-quant-on-ROCm failures), and already this project's own
# designated distillation teacher (slurm/lumig_job2_distillation.slurm).
# This job proves on ONE node that the checkpoint (a) downloads, (b) loads
# under vLLM, and (c) produces at least one parseable generation for both
# --mode loom and --mode grounding.
#
# Also serves as the ONE download that populates the shared scratch HF
# cache -- run this alone and let it finish before submitting the shard
# jobs, so 4 simultaneous jobs don't race to download the same checkpoint
# into the same cache path.
#
# Usage:
#   sbatch slurm/lumig_job5_enrichment_smoketest.slurm

#SBATCH --job-name=moe_enrichment_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:30:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/enrichment_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/enrichment_smoketest_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- exiting smoke test."
    pkill -SIGINT -f python3 || true
    sync
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

# No host-level `module load` -- job2/job3 (proven, previously-successful
# training jobs on this cluster) run entirely inside the Singularity
# container without loading any host ROCm/LUMI module, since the container
# brings its own ROCm userspace. An earlier version of this script copied
# `module load LUMI/23.09` / `rocm/5.7.1` from a different, less-proven
# script and failed immediately: LUMI/23.09 is now unsupported on this
# stack and rocm/5.7.1 no longer exists (current default is rocm/6.3.4).

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"
OUT_DIR="${SCRATCH}/datasets/enrichment_smoketest"
MODEL="Qwen/Qwen3.5-35B-A3B"

# job 21676967 (2026-09-01) failed with "LLVM ERROR: IO failure on output
# stream: Disk quota exceeded" ~1m45s into EngineCore worker startup --
# root cause: $USER's /users home filesystem is on the default LUMI-G
# quota (~20GB) and was already at 26GB used, so Triton's/Torch's default
# JIT-cache location under $HOME (~/.triton, ~/.cache) hit EDQUOT on first
# write. Same class of issue the project already solved for HF_HOME
# ("home quota too small", see docs/system/lumi_training_paper.md) --
# apply the identical fix here: redirect every cache dir that would
# otherwise default under $HOME to scratch instead.
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
echo "Model    : $MODEL (unquantized MoE, plain BF16)"
echo "Purpose  : verify load + generation on MI250X before the full shard run"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode loom (count=2) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode loom --model "$MODEL" --tensor-parallel-size 8 \
    --count 2 --batch-size 2 \
    --output "$OUT_DIR/loom_smoketest.jsonl"
LOOM_LINES=$(wc -l < "$OUT_DIR/loom_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] loom smoke test: $LOOM_LINES parsed scenario(s)"

echo "[$(date +%H:%M:%S)] Smoke test: --mode grounding (count-per-category=2, general only) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 8 \
    --count-per-category 2 --batch-size 2 \
    --output "$OUT_DIR/grounding_smoketest.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/grounding_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$LOOM_LINES" -gt 0 ] && [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- safe to submit slurm/lumig_job5_enrichment_shard.slurm (4 shards)."
    exit 0
else
    echo "SMOKE TEST FAILED -- do NOT submit the full shard run. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
