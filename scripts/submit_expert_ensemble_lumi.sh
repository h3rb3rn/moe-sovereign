#!/bin/bash
# scripts/submit_expert_ensemble_lumi.sh
# Master Parallel Submission Driver for MoE Sovereign (Planner, 8 Experts, Judge) on LUMI-G

set -euo pipefail

SCRATCH="/scratch/project_465003058/hornphil"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
SLURM_DIR="${SCRATCH}/moe-sovereign/slurm"

echo "================================================================================"
echo "🚀 SUBMITTING MOE SOVEREIGN MASTER TRAINING PIPELINE (10 MODELS) ON LUMI-G"
echo "Grant: project_465003058 | Target: Planner (4B) + 8 Experts (4B) + Judge (32B)"
echo "Mode: PARALLEL EXECUTION (Max throughput on small-g partition)"
echo "================================================================================"

ROLES=("planner" "coder" "precision" "graphrag" "governance" "research" "security" "datainfra" "omni" "judge")

for ROLE in "${ROLES[@]}"; do
    echo "⚡ Submitting pipeline for: [moe-${ROLE}]"
    JOB_ID=$(sbatch --parsable "${SLURM_DIR}/lumig_expert_ensemble_pipeline.slurm" "$ROLE")
    echo "   -> Submitted Job ID: $JOB_ID"
done

echo "================================================================================"
echo "🎉 ALL 10 PIPELINES SUCCESSFULLY SUBMITTED TO SLURM!"
echo "To monitor the execution queue, run: squeue -u hornphil"
echo "Logs will be written to: ${SCRATCH}/moe-sovereign/logs/"
echo "================================================================================"
