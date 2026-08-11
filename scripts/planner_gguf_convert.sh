#!/bin/bash
# scripts/planner_gguf_convert.sh
# SLURM job: converts the merged Qwen3-8B planner SFT model to GGUF Q4_K_M.
#
# Adapted from scripts/gguf_convert.sh (Sovereign Judge 35B MoE conversion) —
# NOTE: the Judge script's --no-mtp concern (Episode 10, whitepaper) does not
# apply here. That flag addresses Qwen3-MoE's multi-token-prediction block,
# which only exists in the MoE architecture. Qwen3-8B (this model) is dense,
# has no MTP block, so --no-mtp is intentionally omitted.
#
# Resources scaled down vs. the 35B Judge job (8B dense is ~1/4 the params):
# 2h time limit (was 6h), 100G mem (was 200G) — llama.cpp build is reused,
# not rebuilt (already present from the Judge conversion, job 19987816).

#SBATCH --job-name=qwen3-planner-gguf
#SBATCH --account=project_465003058
#SBATCH --partition=small
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=100G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/planner_gguf_convert_%j.log

set -e

SCRATCH=/scratch/project_465003058/hornphil
MERGED=${SCRATCH}/qwen3_planner_v4/merged
OUTDIR=${SCRATCH}/qwen3_planner_v4/gguf-q4km
LLAMACPP=${SCRATCH}/llama.cpp
VENV_PKGS=${SCRATCH}/my_venv/lib/python3.12/site-packages
SIF=${SCRATCH}/lumi-multitorch-latest.sif

echo "=== Qwen3-8B Planner GGUF Q4_K_M Konvertierung ==="
echo "  Merged : ${MERGED}"
echo "  Output : ${OUTDIR}"
echo "  Start  : $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ ! -d "${MERGED}" ]; then
    echo "ERROR: Merged model not found at ${MERGED} — did the merge job succeed?"
    exit 1
fi

mkdir -p "${OUTDIR}"

if [ ! -f "${LLAMACPP}/build/bin/llama-quantize" ]; then
    echo "ERROR: llama-quantize nicht gefunden — Build fehlgeschlagen?"
    exit 1
fi
echo "=== llama-quantize gefunden ==="

# --- Schritt 1: BF16 SafeTensors -> GGUF F16 (via Singularity, hat torch) ---
GGUF_F16="${OUTDIR}/qwen3-planner-f16.gguf"
if [ ! -f "${GGUF_F16}" ]; then
    echo "=== Schritt 1: BF16 SafeTensors -> GGUF F16 ==="
    singularity exec \
        --bind /pfs,/scratch,/projappl,/project \
        --env PYTHONPATH=${VENV_PKGS} \
        "${SIF}" \
        python3 "${LLAMACPP}/convert_hf_to_gguf.py" \
            "${MERGED}" \
            --outfile "${GGUF_F16}" \
            --outtype f16
    echo "=== F16 GGUF fertig: $(du -sh ${GGUF_F16}) ==="
else
    echo "=== F16 GGUF existiert bereits, ueberspringe ==="
fi

# --- Schritt 2: GGUF F16 -> Q4_K_M ---
GGUF_Q4="${OUTDIR}/qwen3-planner-q4_k_m.gguf"
echo "=== Schritt 2: F16 -> Q4_K_M (32 Threads) ==="
"${LLAMACPP}/build/bin/llama-quantize" \
    "${GGUF_F16}" \
    "${GGUF_Q4}" \
    Q4_K_M \
    32

echo ""
echo "  End    : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  F16    : $(du -sh ${GGUF_F16})"
echo "  Q4_K_M : $(du -sh ${GGUF_Q4})"
echo "=== FERTIG ==="
