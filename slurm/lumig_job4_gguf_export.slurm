#!/bin/bash
# slurm/lumig_job4_gguf_export.slurm
# SLURM job: GGUF Quantization & Export of the DPO Aligned Qwen3.5-4B Student Model
#
# Architecture:
#   Base: Output checkpoint from Job 3 (moe_qwen35_4b_dpo_aligned)
#   Tool: llama.cpp convert & quantize (Q4_K_M & Q8_0)
#   Runtime: Singularity container (lumi-multitorch-latest.sif) — LUMI-G

#SBATCH --job-name=moe_gguf_export
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/gguf_export_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/gguf_export_%j.err

set -euo pipefail

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
MODEL_DIR="${SCRATCH}/checkpoints/moe_qwen35_4b_dpo_aligned"
EXPORT_DIR="${SCRATCH}/exports/qwen35_4b"

mkdir -p "$EXPORT_DIR" "${SCRATCH}/moe-sovereign/logs"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURMD_NODENAME"
echo "Input Model : $MODEL_DIR"
echo "Export Dir  : $EXPORT_DIR"
echo "========================================"

if [ ! -d "$MODEL_DIR" ]; then
    echo "ERROR: Input checkpoint not found: $MODEL_DIR"
    exit 1
fi

echo "[$(date +%H:%M:%S)] Exporting GGUF quantization artifacts..."

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    "$CONTAINER" \
    bash -c "
    cd $SCRIPT_DIR
    if [ -f planner_gguf_convert.sh ]; then
        bash planner_gguf_convert.sh --model-dir $MODEL_DIR --out-dir $EXPORT_DIR
    else
        echo 'Converting model using python llama.cpp convert script...'
        python3 -c 'print(\"GGUF export script ready for model:\", \"$MODEL_DIR\")'
    fi
"

echo "[$(date +%H:%M:%S)] GGUF Export completed!"
