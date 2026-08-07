#!/bin/bash
# scripts/submit_lumi_pipeline.sh
# Submits dataset generation + SFT training as a chained SLURM dependency pair.
#
# Usage:
#   bash scripts/submit_lumi_pipeline.sh
#   bash scripts/submit_lumi_pipeline.sh meta-llama/Meta-Llama-3.1-405B-Instruct
#   MODEL=Qwen/Qwen2.5-72B-Instruct bash scripts/submit_lumi_pipeline.sh
#
# Phase 1  — planner_dataset job   (lumi_generate_planner_dataset.sh)
# Phase 2  — SFT training job      (lumi_sft_qwen3_8b.sh, starts only when Phase 1 succeeded)
#
# The dataset path is derived automatically from the Phase 1 job ID so that both
# jobs always refer to the same output directory without manual coordination.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRATCH="/scratch/project_465003058/hornphil"

# ── Phase 1: Dataset generation ───────────────────────────────────────────────
# Allow MODEL override: argument 1 > $MODEL env var > script default (405B)
if [[ $# -ge 1 ]]; then
    TEACHER_MODEL="$1"
elif [[ -n "${MODEL:-}" ]]; then
    TEACHER_MODEL="$MODEL"
else
    TEACHER_MODEL="meta-llama/Meta-Llama-3.1-405B-Instruct"
fi

echo "==> Phase 1: Planner Dataset Generation"
echo "    Teacher model : $TEACHER_MODEL"
echo "    Script        : $SCRIPT_DIR/lumi_generate_planner_dataset.sh"

DATAJOB=$(
    MODEL="$TEACHER_MODEL" \
    sbatch --parsable \
           "$SCRIPT_DIR/lumi_generate_planner_dataset.sh"
)
echo "    Job ID        : $DATAJOB"

# ── Phase 2: SFT Training ─────────────────────────────────────────────────────
# Dataset path is determined by Phase 1's job ID — same pattern used in the dataset script.
DATASET="$SCRATCH/planner_dataset_${DATAJOB}/planner_chat.jsonl"

echo ""
echo "==> Phase 2: Qwen3-8B SFT Training"
echo "    Dependency    : afterok:$DATAJOB"
echo "    Dataset       : $DATASET"
echo "    Script        : $SCRIPT_DIR/lumi_sft_qwen3_8b.sh"

SFTJOB=$(
    DATASET="$DATASET" \
    sbatch --parsable \
           --dependency=afterok:$DATAJOB \
           --job-name="qwen3_sft_dep${DATAJOB}" \
           "$SCRIPT_DIR/lumi_sft_qwen3_8b.sh"
)
echo "    Job ID        : $SFTJOB"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Pipeline submitted successfully."
echo ""
echo "  Dataset job : $DATAJOB"
echo "  SFT job     : $SFTJOB  (starts only when job $DATAJOB succeeds)"
echo ""
echo "Monitor:"
echo "  squeue -u \$USER"
echo "  squeue -j $DATAJOB,$SFTJOB"
echo ""
echo "Logs:"
echo "  $SCRATCH/logs/planner_dataset_${DATAJOB}.out"
echo "  $SCRATCH/logs/qwen3_sft_${SFTJOB}.out"
echo ""
echo "To cancel both jobs:"
echo "  scancel $DATAJOB $SFTJOB"
