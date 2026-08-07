#!/usr/bin/env python3
"""
scripts/train_planner_sft.py
SFT fine-tuning of Qwen/Qwen3-8B as MoE Sovereign structured planner.

Framework  : TRL 1.4 SFTTrainer + PEFT QLoRA (4-bit NF4) + DeepSpeed ZeRO-2 BF16-compute
Dataset    : planner_chat.jsonl  (259,829 chat-format samples, Llama-3.1-405B teacher)
Target     : Qwen3-8B planner that outputs valid JSON task arrays

ROCm / LUMI-G MI250X constraints enforced here:
  - attn_implementation="eager"   (no Flash Attention 2 on ROCm 7.0)
  - 4-bit NF4 quantization (BitsAndBytes) — added after jobs 20269376/20276483/20276792
    OOM'd on the eager-attention forward pass at micro_batch=8 and =4 (BF16 model weights
    alone are ~16.4 GB of the ~64 GB/GCD budget). ZeRO-2 (not ZeRO-3) shards only optimizer
    states/gradients, not parameters, so quantized weight blocks stay intact per-GPU —
    unlike ZeRO-3, which cannot shard quantized blocks (see train_judge_lora.py, which
    disables QLoRA specifically under ZeRO-3 for this reason).
  - device_map=None               (DeepSpeed handles placement, not device_map)
  - torch_dtype=bfloat16          (MI250X native format; NF4 compute dtype also bfloat16)
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from pathlib import Path

import torch
from datasets import load_dataset
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    TrainerCallback,
    set_seed,
)
from transformers.trainer_utils import get_last_checkpoint
from trl import SFTConfig, SFTTrainer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("train_planner_sft")


# ── Argument parsing ────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SFT phi-4 planner — DeepSpeed ZeRO-3 BF16")
    p.add_argument("--model-id",       default="microsoft/phi-4")
    p.add_argument("--dataset",        required=True,
                   help="Path to planner_chat.jsonl")
    p.add_argument("--output-dir",     required=True,
                   help="Checkpoint and final model output directory")
    p.add_argument("--deepspeed",      default=None,
                   help="Path to DeepSpeed ZeRO-3 JSON config")
    p.add_argument("--epochs",         type=int,   default=3)
    p.add_argument("--micro-batch",    type=int,   default=4,
                   help="Per-GPU micro batch size")
    p.add_argument("--grad-accum",     type=int,   default=2,
                   help="Gradient accumulation steps (effective_batch = micro×gpus×grad_accum)")
    p.add_argument("--max-seq-len",    type=int,   default=8192)
    p.add_argument("--lr",             type=float, default=2e-4)
    p.add_argument("--warmup-ratio",   type=float, default=0.03)
    p.add_argument("--lora-r",         type=int,   default=64)
    p.add_argument("--lora-alpha",     type=int,   default=128)
    p.add_argument("--lora-dropout",   type=float, default=0.05)
    p.add_argument("--seed",           type=int,   default=42)
    p.add_argument("--save-steps",     type=int,   default=500)
    p.add_argument("--logging-steps",  type=int,   default=25)
    p.add_argument("--val-split",      type=float, default=0.005,
                   help="Fraction held out for validation (0 = no eval)")
    p.add_argument("--packing",        action="store_true", default=True,
                   help="Pack multiple short samples into one sequence")
    p.add_argument("--no-packing",     dest="packing", action="store_false")
    p.add_argument("--no-4bit",        dest="use_4bit", action="store_false", default=True,
                   help="Disable QLoRA 4-bit quantization (falls back to full BF16 + LoRA). "
                        "Earlier micro_batch>=4 OOMs (jobs 20269376/20276483) turned out to be "
                        "caused by a missing torch.cuda.set_device(local_rank) call, not "
                        "genuine per-rank memory pressure — see fix in main().")
    p.add_argument("--gradient-checkpointing", dest="gradient_checkpointing",
                   action="store_true", default=True,
                   help="Trade compute for memory via activation recompute in the backward pass")
    p.add_argument("--no-gradient-checkpointing", dest="gradient_checkpointing",
                   action="store_false",
                   help="Disable activation recompute. Jobs 20329106 (QLoRA) and 20333367 "
                        "(BF16) both measured ~148-150s/step despite the device-placement fix "
                        "— identical across precisions, pointing at gradient-checkpointing "
                        "recompute (or eager-attention O(seq^2) compute) rather than "
                        "quantization/dequantization as the bottleneck. Only safe to try now "
                        "that per-rank OOM is no longer a risk after the device fix.")
    p.add_argument("--lora-targets",    default=None,
                   help="Comma-separated LoRA target modules (auto-detected from model-id if unset)")
    p.add_argument("--hf-cache",       default=None,
                   help="HuggingFace cache directory")
    p.add_argument("--local_rank",     type=int, default=0,
                   help="Injected by DeepSpeed launcher — do not set manually")
    return p.parse_args()


# ── LoRA target modules ────────────────────────────────────────────────────────
# phi-4: fused projections (qkv combined, gate+up combined)
PHI4_LORA_TARGETS = ["qkv_proj", "o_proj", "gate_up_proj", "down_proj"]

# Qwen3 / Qwen3.5: separate Q/K/V projections, split MLP gate/up
QWEN3_LORA_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj",
                       "gate_proj", "up_proj", "down_proj"]


def _resolve_lora_targets(model_id: str, override: str | None) -> list[str]:
    """Return LoRA target module list, auto-detected from model_id unless overridden."""
    if override:
        return [t.strip() for t in override.split(",") if t.strip()]
    lower = model_id.lower()
    if "qwen3" in lower or "qwen3.5" in lower or "qwen2.5" in lower:
        return QWEN3_LORA_TARGETS
    return PHI4_LORA_TARGETS


# ── ETA callback ────────────────────────────────────────────────────────────────

class ETACallback(TrainerCallback):
    """Logs estimated remaining time every logging_steps."""

    def __init__(self, total_steps: int) -> None:
        self.total_steps = total_steps
        self._t0: float | None = None

    def on_step_end(self, args, state, control, **kwargs):
        import time
        if self._t0 is None:
            self._t0 = time.time()
        if state.global_step % args.logging_steps == 0 and state.global_step > 0:
            elapsed = time.time() - self._t0
            rate = state.global_step / elapsed
            remaining = (self.total_steps - state.global_step) / rate if rate > 0 else 0
            logger.info(
                "Step %d/%d | loss=%.4f | %.2f steps/s | ETA %.0f min",
                state.global_step, self.total_steps,
                state.log_history[-1].get("loss", float("nan")) if state.log_history else float("nan"),
                rate, remaining / 60,
            )


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    set_seed(args.seed)

    local_rank = int(os.environ.get("LOCAL_RANK", 0))
    is_main = local_rank == 0

    # Critical: pin this process to its own GPU BEFORE any CUDA/HIP allocation happens.
    # SFTConfig/TrainingArguments (which triggers Accelerate's PartialState and would
    # normally do this) is only constructed much later, after from_pretrained() already
    # ran. Without this call, every one of the 8 launched processes defaults to
    # torch.cuda.current_device() == 0, so all 8 ranks load/quantize their model copy onto
    # GPU 0 simultaneously while GPUs 1-7 sit idle. This — not batch size, not allocator
    # fragmentation — is the real root cause behind jobs 20269376/20276483/20276792/20278004/
    # 20328849: each OOM'd at a different point only because BF16 vs. QLoRA vs. the fp32
    # upcast step in prepare_model_for_kbit_training changed how much per-rank memory 8x
    # stacking on one device could tolerate before it finally overflowed. Job 20328849's
    # "GPU 0 ... of which 0 bytes is free" with only 2.52 GiB attributed to the crashing
    # rank is the smoking gun: the other ~61 GiB was the other 7 ranks' copies.
    if torch.cuda.is_available():
        torch.cuda.set_device(local_rank)

    if args.hf_cache:
        os.environ["HF_HOME"] = args.hf_cache
        os.environ["TRANSFORMERS_CACHE"] = args.hf_cache

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    last_ckpt = get_last_checkpoint(str(out_dir))
    if is_main and last_ckpt:
        logger.info("Resuming from checkpoint: %s", last_ckpt)

    if is_main:
        logger.info("=== phi-4 Planner SFT ===")
        logger.info("Model        : %s", args.model_id)
        logger.info("Dataset      : %s", args.dataset)
        logger.info("Output dir   : %s", out_dir)
        logger.info("Epochs       : %d", args.epochs)
        logger.info("Micro-batch  : %d / GPU", args.micro_batch)
        logger.info("Grad accum   : %d", args.grad_accum)
        logger.info("Max seq len  : %d", args.max_seq_len)
        logger.info("LoRA r/α     : %d / %d", args.lora_r, args.lora_alpha)
        logger.info("Packing      : %s", args.packing)

    # ── Dataset ────────────────────────────────────────────────────────────────
    if is_main:
        logger.info("Loading dataset from %s …", args.dataset)

    raw = load_dataset("json", data_files=args.dataset, split="train")

    if is_main:
        logger.info("Dataset size: %d samples", len(raw))

    if args.val_split > 0:
        split = raw.train_test_split(test_size=args.val_split, seed=args.seed)
        train_ds, eval_ds = split["train"], split["test"]
        if is_main:
            logger.info("Train: %d | Eval: %d", len(train_ds), len(eval_ds))
    else:
        train_ds, eval_ds = raw, None

    # ── Tokenizer ──────────────────────────────────────────────────────────────
    if is_main:
        logger.info("Loading tokenizer …")

    tokenizer = AutoTokenizer.from_pretrained(
        args.model_id,
        trust_remote_code=True,
        padding_side="right",
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
        tokenizer.pad_token_id = tokenizer.eos_token_id

    # ── Preprocessing: keep native conversational format ────────────────────────
    # `messages` is passed through unmodified (no flattening to a "text" field) so
    # SFTTrainer recognises the dataset as conversational and assistant_only_loss=True
    # (set below in SFTConfig) can mask the loss to the assistant turn only. Flattening
    # via apply_chat_template(tokenize=False) here would strip the role structure TRL
    # needs for that feature.

    # Hard preflight gate: abort before the 8-GPU DeepSpeed job burns GPU-hours on a
    # dataset whose target answer would land outside max_seq_len. This check previously
    # only logged a WARNING and let training proceed anyway (see Episode 11 postmortem).
    #
    # Runs on EVERY rank, not just is_main: this executes before deepspeed.initialize(),
    # so the 8 launcher processes are still independent (no NCCL/RCCL collectives yet).
    # Each rank loads the same dataset file and computes identical p50/p95/p99 values, so
    # the abort decision is deterministic across ranks without needing a broadcast. If only
    # rank 0 exited here, ranks 1-7 would proceed into deepspeed.initialize() and hang on
    # the first collective op waiting for a rank that already exited — killed only by the
    # SLURM time limit, hours later.
    sample_lengths = [
        len(tokenizer.apply_chat_template(train_ds[i]["messages"], tokenize=True,
                                           add_generation_prompt=False))
        for i in range(min(200, len(train_ds)))
    ]
    p50 = sorted(sample_lengths)[len(sample_lengths) // 2]
    p95 = sorted(sample_lengths)[int(len(sample_lengths) * 0.95)]
    p99 = sorted(sample_lengths)[int(len(sample_lengths) * 0.99)]
    if is_main:
        logger.info("Token length p50=%d p95=%d p99=%d (max_seq=%d)",
                    p50, p95, p99, args.max_seq_len)
    if p99 > args.max_seq_len:
        if is_main:
            pct_truncated = sum(1 for l in sample_lengths if l > args.max_seq_len) * 100 // len(sample_lengths)
            logger.error(
                "ABORT: p99 (%d) > max_seq_len (%d) — %d%% of samples would be truncated "
                "and the assistant target could fall outside the loss window. "
                "Raise --max-seq-len or fix the dataset before resubmitting.",
                p99, args.max_seq_len, pct_truncated,
            )
        sys.exit(1)

    # ── Model ──────────────────────────────────────────────────────────────────
    bnb_config: BitsAndBytesConfig | None = None
    if args.use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )

    if is_main:
        logger.info("Loading model %s (%s, eager attention) …", args.model_id,
                    "4-bit NF4" if args.use_4bit else "BF16")

    model = AutoModelForCausalLM.from_pretrained(
        args.model_id,
        torch_dtype=torch.bfloat16,
        attn_implementation="eager",   # no Flash Attention 2 on ROCm 7.0
        quantization_config=bnb_config,
        device_map=None,               # DeepSpeed handles placement, not device_map
        trust_remote_code=True,
    )
    model.config.use_cache = False     # required for gradient checkpointing

    if args.use_4bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    if is_main:
        total_params = sum(p.numel() for p in model.parameters())
        logger.info("Model loaded: %.2fB parameters", total_params / 1e9)

    # ── LoRA ───────────────────────────────────────────────────────────────────
    lora_targets = _resolve_lora_targets(args.model_id, getattr(args, "lora_targets", None))
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=lora_targets,
        bias="none",
        inference_mode=False,
    )

    if is_main:
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        logger.info("LoRA adapters: r=%d α=%d targets=%s",
                    args.lora_r, args.lora_alpha, lora_targets)
        logger.info("Trainable params before PEFT: %.2fM", trainable / 1e6)

    # ── Training arguments ─────────────────────────────────────────────────────
    steps_per_epoch = math.ceil(len(train_ds) / (args.micro_batch * args.grad_accum * 8))
    total_steps     = steps_per_epoch * args.epochs

    if is_main:
        logger.info(
            "Steps: %d/epoch × %d epochs = %d total | effective_batch=%d",
            steps_per_epoch, args.epochs, total_steps,
            args.micro_batch * args.grad_accum * 8,
        )

    sft_config = SFTConfig(
        # I/O
        output_dir=str(out_dir),

        # Training schedule
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.micro_batch,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=args.gradient_checkpointing,

        # Optimiser
        optim="adamw_torch",
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        weight_decay=0.0,
        max_grad_norm=1.0,

        # Precision — BF16 only (no FP16 on MI250X)
        bf16=True,
        fp16=False,

        # Sequence
        max_length=args.max_seq_len,
        packing=args.packing,
        # Conversational dataset (native "messages" column, see preprocessing above) —
        # restricts the loss to assistant turns instead of the full system+user+assistant
        # sequence. Root-cause fix for the Episode 11 truncation bug: with full-sequence
        # loss, a growing system prompt could silently starve the assistant target of any
        # training signal even when tokens remain within max_seq_len.
        assistant_only_loss=True,
        # Without this, SFTTrainer's internal chat-template tokenization of the
        # conversational dataset runs single-process per rank (~46 examples/s for
        # 258K rows ≈ 93 min/rank). With 8 independent DeepSpeed ranks tokenizing at
        # slightly different paces and no shared progress checkpoint, the first rank
        # to finish hits the initial NCCL barrier and the RCCL watchdog aborts the
        # whole job after its default 30-minute collective-op timeout (job 20242481,
        # 2026-07-25 — died on ALLREDUCE timeout mid-tokenization, never reached
        # step 1). dataset_num_proc parallelises the same datasets.map() call this
        # previously ran manually via format_sample(..., num_proc=4).
        dataset_num_proc=4,

        # Logging & checkpointing
        logging_steps=args.logging_steps,
        logging_first_step=True,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=3,
        eval_strategy="steps" if eval_ds is not None else "no",
        eval_steps=args.save_steps if eval_ds is not None else None,

        # Reproducibility
        seed=args.seed,
        data_seed=args.seed,

        # Misc
        report_to="none",
        remove_unused_columns=True,
        dataloader_num_workers=4,
        dataloader_pin_memory=False,   # not beneficial on ROCm

        # DeepSpeed
        deepspeed=args.deepspeed,
    )

    # ── Trainer ────────────────────────────────────────────────────────────────
    trainer = SFTTrainer(
        model=model,
        args=sft_config,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        peft_config=lora_config,
        processing_class=tokenizer,
        callbacks=[ETACallback(total_steps)],
    )

    if is_main:
        trainable_after = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
        all_params      = sum(p.numel() for p in trainer.model.parameters())
        logger.info("Trainable: %.2fM / %.2fB (%.2f%%)",
                    trainable_after / 1e6, all_params / 1e9,
                    100 * trainable_after / all_params)

    # ── Train ──────────────────────────────────────────────────────────────────
    if is_main:
        logger.info("Starting training …")

    trainer.train(resume_from_checkpoint=last_ckpt)

    # ── Save adapter ───────────────────────────────────────────────────────────
    if is_main:
        logger.info("Saving LoRA adapter to %s/final_adapter …", out_dir)

    trainer.save_model(str(out_dir / "final_adapter"))
    tokenizer.save_pretrained(str(out_dir / "final_adapter"))

    if is_main:
        logger.info("=== Training complete ===")
        logger.info("LoRA adapter : %s/final_adapter", out_dir)
        logger.info("Next step    : merge adapter + convert to GGUF (scripts/merge_planner_lora.sh)")


if __name__ == "__main__":
    main()
