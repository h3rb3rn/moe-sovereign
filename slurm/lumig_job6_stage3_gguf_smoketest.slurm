#!/bin/bash
# slurm/lumig_job6_stage3_gguf_smoketest.slurm
# Isolated Stage-3-only smoke test for the LUMI-G full-finetuning plan,
# Phase 0 (see ~/.claude/plans/zazzy-beaming-koala.md).
#
# Purpose: prove scripts/export_expert_gguf_array.sh's PATH-independence
# fix (SINGULARITY_BIN resolved once and passed through explicitly) works
# end-to-end, WITHOUT burning GPU-hours on new training. 9 of 10 prior
# pipeline runs failed at exactly this step with
# "singularity: command not found" (see docs/experiments/
# antigravity_frontier_pipeline_postmortem.md for the training-data context
# that made those 9 runs moot anyway).
#
# Runs against the already-existing, technically-valid
# merged_sovereign_student_4b checkpoint (real Stage-1/2 output from the
# earlier campaign -- its LoRA weights are not reused for real training
# since they were trained on fabricated data, but the merged BF16
# checkpoint format is real and sufficient to prove the export step).
#
# Minimal resource request (1 GPU, not 8) since GGUF conversion/quantization
# is CPU-bound work -- the GPU allocation only buys container/node access on
# small-g, no CUDA/ROCm kernels run here.

#SBATCH --job-name=moe_stage3_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/stage3_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/stage3_smoketest_%j.err

set -euo pipefail

SCRATCH="/scratch/project_465003058/hornphil"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
MERGED_DIR="${SCRATCH}/checkpoints/merged_sovereign_student_4b"
EXPORT_DIR="${SCRATCH}/exports/_stage3_smoketest"

mkdir -p "$EXPORT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -d "$MERGED_DIR" ]; then
    echo "ERROR: expected pre-existing merged checkpoint not found: $MERGED_DIR" >&2
    exit 1
fi

echo "================================================================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Node     : $SLURMD_NODENAME"
echo "Purpose  : Stage-3-only GGUF export smoke test (Phase 0 of the LUMI-G"
echo "           full-finetuning plan) -- no new training, verifies only that"
echo "           the SINGULARITY_BIN PATH fix resolves the historical bug."
echo "Input    : $MERGED_DIR"
echo "Output   : $EXPORT_DIR"
echo "================================================================================"

# role="planner" -> MODEL_NAME=moe-sovereign-student-4b inside the export script,
# matching $MERGED_DIR's actual content.
bash "${SCRIPT_DIR}/export_expert_gguf_array.sh" planner "$MERGED_DIR" "$EXPORT_DIR"

echo "================================================================================"
echo "Smoke test finished. Verifying real output files exist (not just exit 0):"
ls -lh "$EXPORT_DIR"
for suffix in Q8_0 Q4_K_M; do
    f="${EXPORT_DIR}/moe-sovereign-student-4b-${suffix}.gguf"
    if [ ! -s "$f" ]; then
        echo "FAIL: expected output missing or empty: $f" >&2
        exit 1
    fi
    echo "OK: $f ($(stat -c%s "$f") bytes)"
done
echo "Stage 3 smoke test PASSED."
echo "================================================================================"
