#!/bin/bash
# slurm/lumig_job11_teacher_mistral_large_smoketest.slurm
# Phase 1 teacher-model smoke test (see ~/.claude/plans/zazzy-beaming-koala.md)
# for the LUMI-G full-finetuning plan: Tier B / governance-cluster stretch
# teacher mistralai/Mistral-Large-Instruct-2411 (MistralForCausalLM,
# unquantized, ~490GB weights -- VERY tight fit, ~61.25GB weights alone per
# GPU vs. 64GB physical, the tightest of all surveyed models). Confirmed
# architecture-registered + ungated (no license-gate wall despite the name)
# via a config-only HF Hub check, no weights downloaded, prior to this job.
#
# This is the single riskiest Phase 1 smoke test in the plan --
# --gpu-memory-utilization raised to 0.97 (the practical ceiling before
# leaving no room at all for ROCm/ncclL driver overhead) and
# --max-model-len reduced to 2048. A clean OOM here is an expected,
# real, useful finding (drop this model from Tier B; Tier A GLM-4.5-Air
# still covers the governance cluster) -- not a bug to route around.
#
# Usage:
#   sbatch slurm/lumig_job11_teacher_mistral_large_smoketest.slurm

#SBATCH --job-name=moe_teacher_mistrallarge_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_mistrallarge_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_mistrallarge_smoketest_%j.err

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
MODEL="mistralai/Mistral-Large-Instruct-2411"

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
echo "Model    : $MODEL (MistralForCausalLM, unquantized, ~490GB -- VERY TIGHT)"
echo "Purpose  : Phase 1 teacher-model viability -- Tier B governance-cluster stretch"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode grounding (count-per-category=2, reduced max-model-len/max gpu-mem-util for very tight fit) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 8 \
    --max-model-len 2048 --gpu-memory-utilization 0.97 \
    --count-per-category 2 --batch-size 2 \
    --output "$OUT_DIR/mistrallarge_grounding_smoketest.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/mistrallarge_grounding_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- Mistral-Large-Instruct-2411 confirmed as a viable Tier B teacher."
    exit 0
else
    echo "SMOKE TEST FAILED (or OOM) -- 0 parsed results. Drop this model from Tier B; Tier A (GLM-4.5-Air) still covers the governance cluster. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
