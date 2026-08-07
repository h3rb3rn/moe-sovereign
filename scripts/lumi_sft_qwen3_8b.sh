#!/bin/bash
# scripts/lumi_sft_qwen3_8b.sh
# SLURM job: SFT fine-tuning of Qwen3-8B as MoE Sovereign planner
#
# Model choice rationale (2026-07-22):
#   phi-4:14B was rejected after loss collapse at step 75 on both LR=2e-4 and LR=5e-5.
#   Root cause: identical system prompt across all 200K samples → near-zero dataset entropy.
#   Qwen3-8B selected: confirmed ROCm MI250X + TRL/DeepSpeed ZeRO-2 support, Apache 2.0,
#   strong tool-calling (BFCL-V3), fast local inference (~5 GB Q4).
#   NOTE: HF repo id is "Qwen/Qwen3-8B" — no "-Instruct" suffix in the Qwen3 generation
#   (unlike Qwen2.5). "Qwen/Qwen3-8B-Instruct-2507" does not exist and 404s (job 20177965,
#   20242417 both failed on this before the id was corrected here).
#
# Dataset: planner_chat.jsonl v2 — 5 system prompt variants + 15% negative samples
# Framework: TRL SFTTrainer + PEFT LoRA + DeepSpeed ZeRO-2 BF16
# Hardware : 1 LUMI-G node, 8× AMD MI250X GCDs, 512 GB HBM2e
#
# Key differences vs lumi_sft_phi4.sh:
#   - ZeRO-2 (not ZeRO-3): 8B fits without parameter sharding → faster
#   - LoRA r=16/α=32 (not 64/128): prevents overfitting on low-entropy data
#   - LR=2e-4: standard LoRA-SFT rate (2e-5 is a full-fine-tune rate, mismatched to LoRA —
#     see Whitepaper Episode 11 / eurohpc_lumi_activity_report.md Aktivität 10)
#   - Epochs=3: matches eurohpc_training_concept.md Phase 3; ~3 epochs is where
#     out-of-distribution SFT performance typically peaks before overfitting
#   - max_seq_len=8192: covers measured p99=3673/max=4300 over the full 259,829-sample
#     dataset with margin; ceiling doubles as a circuit-breaker against future pathological
#     samples (no static padding — dynamic per-batch, so headroom is ~free for normal batches)
#   - micro_batch=4 (not 8), grad_accum=4 (not 2): eager attention (no Flash Attention 2 on
#     ROCm) OOM'd at micro_batch=8 on a batch containing a long real sample (job 20269376,
#     "Tried to allocate 12.69 GiB"). effective_batch=128 unchanged either way.
#     micro_batch=2/grad_accum=8 (job 20271148) avoided the OOM but stabilised at ~66s/step —
#     4x the per-GPU micro-batch COUNT vs micro_batch=8 adds enough fixed per-micro-batch
#     overhead (gradient-checkpointing recompute, DeepSpeed comm bucketing, kernel launches)
#     that 3 epochs would need ~109h, exceeding even the 72h small-g partition ceiling.
#     Cancelled at step 31. micro_batch=4 halves that micro-batch count vs micro_batch=2 —
#     verify throughput early; if still too slow, next step down is epochs (not micro_batch=2).
#   - Time limit=38h: was ~32.5h at the old (OOM-prone) micro_batch=8/~19.3s-step rate
#     (job 20190726, 1 epoch extrapolated x3). Actual rate at micro_batch=4 unconfirmed until
#     this run — check early step timing before assuming this budget still holds.
#
# Usage:
#   DATASET=/scratch/.../planner_merged_20190726/planner_chat.jsonl \
#     sbatch scripts/lumi_sft_qwen3_8b.sh
#
# After completion:
#   sbatch scripts/merge_planner_lora.sh --adapter <OUTPUT_DIR>/final_adapter

#SBATCH --job-name=qwen3_planner_sft
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=38:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/logs/qwen3_sft_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/logs/qwen3_sft_%j.err

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_ID="${MODEL_ID:-Qwen/Qwen3-8B}"
# Default matches the dataset actually used by every job in the job history since
# 2026-07-24 (planner_merged_20190726) — job 20323741 (2026-07-28) failed instantly
# ("Dataset not found") because this default still pointed at planner_dataset_v2/, a
# path that was superseded by the merged v2+negative-samples dataset and never re-created
# on scratch. Always double-check this against the current dataset dir before submitting;
# do not rely on the default alone.
DATASET="${DATASET:-/scratch/project_465003058/hornphil/planner_merged_20190726/planner_chat.jsonl}"
HF_CACHE="/scratch/project_465003058/hornphil/hf_cache"
SCRIPT_DIR="/scratch/project_465003058/hornphil/scripts"
OUTPUT_DIR="/scratch/project_465003058/hornphil/qwen3_planner_sft_${SLURM_JOB_ID}"
CONTAINER="/scratch/project_465003058/hornphil/lumi-multitorch-latest.sif"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null)"

# Training hyperparameters (override via env)
EPOCHS="${EPOCHS:-3}"
# micro_batch=2 (not 8): eager attention (no Flash Attention 2 on ROCm) materialises the
# full batch x heads x seq^2 attention matrix explicitly. At micro_batch=8, a batch
# containing one of the dataset's longer real samples (~3600-4300 tokens, see the p99/max
# measured over the full 259,829-row dataset) OOM'd on the very first step (job 20269376,
# 2026-07-26: "Tried to allocate 12.69 GiB ... 540.00 MiB is free"). This never surfaced
# under the old max_seq_len=1536 config because truncation capped every sequence short.
# grad_accum=8 keeps effective_batch=128 unchanged (still within the 16/32/128 range
# validated as stable for LoRA SFT).
MICRO_BATCH="${MICRO_BATCH:-4}"
GRAD_ACCUM="${GRAD_ACCUM:-4}"
MAX_SEQ_LEN="${MAX_SEQ_LEN:-8192}"
LR="${LR:-2e-4}"
LORA_R="${LORA_R:-16}"
LORA_ALPHA="${LORA_ALPHA:-32}"
LORA_DROPOUT="${LORA_DROPOUT:-0.05}"
SAVE_STEPS="${SAVE_STEPS:-500}"
# use_4bit=true (not false): job 20329106 confirmed QLoRA works correctly after the
# device-placement fix (torch.cuda.set_device(local_rank) added to train_planner_sft.py)
# but measured ~149.6s/step — 6060 steps (3 epochs) would need ~252h, far past the 72h
# small-g partition ceiling. All earlier BF16-mode OOMs (jobs 20269376/20276483, "Tried to
# allocate 12.69/6.92 GiB") were themselves very likely artifacts of the same device bug
# (8 ranks piling onto GPU 0), not genuine single-rank memory pressure — QLoRA was solving
# a problem that no longer exists now that each rank owns its own GCD. Testing BF16 first
# with this fix before re-adding QLoRA's ~2x dequant overhead.
USE_4BIT="${USE_4BIT:-false}"
# gradient_checkpointing=false (not true): job 20333367 (BF16, no QLoRA) still measured
# ~148s/step — statistically identical to job 20329106's QLoRA rate (149.6s/step). Same
# speed across two different precisions rules out dequantization as the bottleneck and
# points at gradient-checkpointing recompute (or eager-attention O(seq^2) compute, which
# GC recompute pays for twice per step) instead. Disabling GC trades VRAM for speed — safe
# to try now that the device-placement fix removed the real cause of the earlier OOMs.
GRADIENT_CHECKPOINTING="${GRADIENT_CHECKPOINTING:-false}"
PACKING="${PACKING:-false}"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURMD_NODENAME"
echo "Model       : $MODEL_ID"
echo "Dataset     : $DATASET"
echo "Output dir  : $OUTPUT_DIR"
echo "Epochs      : $EPOCHS"
echo "Batch/GPU   : $MICRO_BATCH × grad_accum=$GRAD_ACCUM = $(( MICRO_BATCH * GRAD_ACCUM * 8 )) effective"
echo "Max seq len : $MAX_SEQ_LEN"
echo "LR          : $LR"
echo "LoRA r/α    : $LORA_R / $LORA_ALPHA (dropout=$LORA_DROPOUT)"
echo "Precision   : $( [ "$USE_4BIT" = "false" ] && echo "BF16" || echo "4-bit NF4 (QLoRA)" )"
echo "Grad ckpt   : $GRADIENT_CHECKPOINTING"
echo "ZeRO stage  : 2 (ZeRO-2, 8B fits without parameter sharding)"
echo "========================================"

mkdir -p "$OUTPUT_DIR"
mkdir -p /scratch/project_465003058/hornphil/logs

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [ ! -f "$DATASET" ]; then
    echo "ERROR: Dataset not found: $DATASET"
    exit 1
fi

DATASET_LINES=$(wc -l < "$DATASET")
echo "Dataset lines: $DATASET_LINES"
if [ "$DATASET_LINES" -lt 10000 ]; then
    echo "WARNING: Dataset has fewer than 10K samples."
fi

# Validate system prompt diversity (at least 3 of 5 variants present)
VARIANT_COUNT=$(python3 -c "
import json, collections
variants = collections.Counter()
with open('$DATASET') as f:
    for i, line in enumerate(f):
        if i >= 2000: break
        d = json.loads(line)
        msgs = d.get('messages', [])
        sys = next((m['content'][:60] for m in msgs if m['role']=='system'), '')
        variants[sys] += 1
print(len(variants))
" 2>/dev/null || echo "0")
echo "System prompt variants in first 2K samples: $VARIANT_COUNT"
if [ "$VARIANT_COUNT" -lt 3 ]; then
    echo "ERROR: Dataset has fewer than 3 system prompt variants — likely v1 dataset with entropy problem."
    echo "Regenerate with generate_planner_dataset.py (v2) before submitting."
    exit 1
fi

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    exit 1
fi

# ── Download Qwen3-8B if not cached ───────────────────────────────────────────
MODEL_CACHE_DIR="$HF_CACHE/models--${MODEL_ID//\//--}"
if [ -d "$MODEL_CACHE_DIR/snapshots" ]; then
    LOCAL_MODEL="$(ls -d $MODEL_CACHE_DIR/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
    echo "Using cached model: $LOCAL_MODEL"
else
    echo "Model not cached — downloading (~5 GB BF16) …"
    singularity exec \
        --bind /scratch/project_465003058:/scratch/project_465003058 \
        --env HF_HOME="$HF_CACHE" \
        --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
        "$CONTAINER" python3 -c "
from huggingface_hub import snapshot_download
import os, time
token = os.environ.get('HUGGING_FACE_HUB_TOKEN','')
t0 = time.time()
p = snapshot_download('$MODEL_ID', token=token or None,
                      cache_dir='$HF_CACHE', ignore_patterns=['*.pt'])
print(f'Downloaded in {(time.time()-t0)/60:.1f} min: {p}')
"
    LOCAL_MODEL="$(ls -d $MODEL_CACHE_DIR/snapshots/*/ 2>/dev/null | head -1)"
    LOCAL_MODEL="${LOCAL_MODEL%/}"
fi

echo "Model path: $LOCAL_MODEL"

# ── ROCm / AMD environment ─────────────────────────────────────────────────────
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export NCCL_SOCKET_IFNAME=hsn0
export NCCL_NET_GDR_LEVEL=3
export MASTER_ADDR=$(hostname)
export MASTER_PORT=29500
export OMP_NUM_THREADS=7
# expandable_segments:True was tried first (jobs 20269376/20276483 showed 17-19 GiB
# "reserved by PyTorch but unallocated" alongside OOM — classic fragmentation signature).
# Confirmed dead end (job 20276792, identical OOM despite the flag): this build's
# c10/hip/HIPAllocatorConfig.h gates expandable_segments behind
# PYTORCH_C10_DRIVER_API_SUPPORTED at compile time, which this container does not have —
# TORCH_WARN_ONCE fires and the setting is silently ignored.
# max_split_size_mb/garbage_collection_threshold are baseline caching-allocator knobs
# (predate expandable_segments, no driver-API dependency — same allocator class used by
# every ROCm PyTorch build). Job 20278004's QLoRA OOM showed "1.50 GiB reserved by
# PyTorch but unallocated" against a 2.32 GiB request — a splittable-block fragmentation
# gap, not a raw-memory shortfall (only <1 GiB short of the ask). Capping split size to
# 128 MiB stops the allocator from carving large reserved-but-idle blocks that can't
# satisfy this request; garbage_collection_threshold=0.6 forces earlier release of cached
# blocks back to the driver before that pressure builds.
export PYTORCH_HIP_ALLOC_CONF=max_split_size_mb:128,garbage_collection_threshold:0.6
export PYTORCH_ALLOC_CONF=max_split_size_mb:128,garbage_collection_threshold:0.6

# ── Launch training ────────────────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting SFT training …"

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME="$HF_CACHE" \
    --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
    --env ROCR_VISIBLE_DEVICES="$ROCR_VISIBLE_DEVICES" \
    --env HIP_VISIBLE_DEVICES="$HIP_VISIBLE_DEVICES" \
    --env NCCL_SOCKET_IFNAME="$NCCL_SOCKET_IFNAME" \
    --env NCCL_NET_GDR_LEVEL="$NCCL_NET_GDR_LEVEL" \
    --env MASTER_ADDR="$MASTER_ADDR" \
    --env MASTER_PORT="$MASTER_PORT" \
    --env OMP_NUM_THREADS="$OMP_NUM_THREADS" \
    --env PYTORCH_HIP_ALLOC_CONF="$PYTORCH_HIP_ALLOC_CONF" \
    --env PYTORCH_ALLOC_CONF="$PYTORCH_ALLOC_CONF" \
    "$CONTAINER" \
    deepspeed --num_gpus 8 \
        "$SCRIPT_DIR/train_planner_sft.py" \
        --model-id       "$LOCAL_MODEL" \
        --dataset        "$DATASET" \
        --output-dir     "$OUTPUT_DIR" \
        --deepspeed      "$SCRIPT_DIR/deepspeed_zero2_qwen3.json" \
        --epochs         "$EPOCHS" \
        --micro-batch    "$MICRO_BATCH" \
        --grad-accum     "$GRAD_ACCUM" \
        --max-seq-len    "$MAX_SEQ_LEN" \
        --lr             "$LR" \
        --lora-r         "$LORA_R" \
        --lora-alpha     "$LORA_ALPHA" \
        --lora-dropout   "$LORA_DROPOUT" \
        --save-steps     "$SAVE_STEPS" \
        --hf-cache       "$HF_CACHE" \
        --val-split      0.005 \
        $( [ "$PACKING" = "false" ] && echo "--no-packing" ) \
        $( [ "$USE_4BIT" = "false" ] && echo "--no-4bit" ) \
        $( [ "$GRADIENT_CHECKPOINTING" = "false" ] && echo "--no-gradient-checkpointing" )

SFT_EXIT=$?
echo "[$(date +%H:%M:%S)] Training exited with code $SFT_EXIT"

# ── Summary ───────────────────────────────────────────────────────────────────
echo "========================================"
if [ $SFT_EXIT -eq 0 ]; then
    echo "SFT COMPLETED successfully"
    ls -lh "$OUTPUT_DIR/final_adapter/" 2>/dev/null
    echo ""
    echo "Nächster Schritt — LoRA-Merge + GGUF-Konversion:"
    echo "  sbatch scripts/merge_planner_lora.sh \\"
    echo "    --adapter $OUTPUT_DIR/final_adapter \\"
    echo "    --base    $LOCAL_MODEL"
    echo ""
    echo "Danach A/B-Test: Qwen3-SFT vs. Basis-Modell ohne Fine-Tuning"
    echo "  python3 scripts/eval_planner.py --model $OUTPUT_DIR/final_adapter"
else
    echo "SFT FAILED with exit code $SFT_EXIT"
    echo "Check: /scratch/project_465003058/hornphil/logs/qwen3_sft_${SLURM_JOB_ID}.err"
fi
echo "========================================"
exit $SFT_EXIT
