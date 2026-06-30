"""
train_judge_lora.py — LoRA SFT fine-tuning for the Sovereign Judge Model (LUMI-G)

Trains a Qwen-2.5-7B (or configurable base) on the paraconsistent debate dataset
using LoRA / QLoRA via HuggingFace TRL's SFTTrainer.

Usage on LUMI-G (inside Singularity container):
    python3 train_judge_lora.py \
        --dataset_path /scratch/.../paraconsistent_training_data.jsonl \
        --base_model  /scratch/.../models/Qwen2.5-7B-Instruct \
        --output_dir  /scratch/.../models/sovereign-judge-7b-lora

Env vars (optional overrides):
    JUDGE_BASE_MODEL  — HF model id or local path (default: Qwen/Qwen2.5-7B-Instruct)
    JUDGE_DATASET_PATH
    JUDGE_OUTPUT_DIR
    JUDGE_MAX_SEQ_LEN  (default 2048)
    JUDGE_EPOCHS       (default 3)
    JUDGE_BATCH_SIZE   (default 4)
    JUDGE_LR           (default 2e-4)
    JUDGE_LORA_R       (default 16)
    JUDGE_LORA_ALPHA   (default 32)
"""

import os
import sys
import json
import argparse
import logging
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("train_judge_lora")

# ---------------------------------------------------------------------------
# Attempt imports — fail with a helpful message if TRL/PEFT are unavailable
# ---------------------------------------------------------------------------
try:
    import torch
    from datasets import Dataset
    from transformers import (
        AutoTokenizer,
        AutoModelForCausalLM,
        BitsAndBytesConfig,
        DataCollatorForLanguageModeling,
    )
    from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
    from trl import SFTTrainer, SFTConfig
except ImportError as e:
    logger.error(
        "Required package missing: %s\n"
        "Install with: pip install trl peft transformers datasets bitsandbytes",
        e,
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class TrainingConfig:
    base_model: str = os.getenv("JUDGE_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
    dataset_path: str = os.getenv(
        "JUDGE_DATASET_PATH",
        "/scratch/project_465003058/hornphil/data/paraconsistent_training_data.jsonl",
    )
    output_dir: str = os.getenv(
        "JUDGE_OUTPUT_DIR",
        "/scratch/project_465003058/hornphil/models/sovereign-judge-7b-lora",
    )
    max_seq_len: int = int(os.getenv("JUDGE_MAX_SEQ_LEN", "2048"))
    epochs: int = int(os.getenv("JUDGE_EPOCHS", "3"))
    batch_size: int = int(os.getenv("JUDGE_BATCH_SIZE", "4"))
    grad_accum: int = int(os.getenv("JUDGE_GRAD_ACCUM", "4"))
    lr: float = float(os.getenv("JUDGE_LR", "2e-4"))
    warmup_ratio: float = 0.05
    lora_r: int = int(os.getenv("JUDGE_LORA_R", "16"))
    lora_alpha: int = int(os.getenv("JUDGE_LORA_ALPHA", "32"))
    lora_dropout: float = 0.05
    use_4bit: bool = True  # QLoRA — 4-bit NF4 quantization on AMD MI250x via ROCm
    use_flash_attn: bool = False  # Flash-Attention 2 not always available on ROCm
    hf_cache: str = os.getenv(
        "HF_HOME", "/scratch/project_465003058/hornphil/hf_cache"
    )


# Detect DDP launch (torchrun sets LOCAL_RANK)
_LOCAL_RANK = int(os.getenv("LOCAL_RANK", "-1"))
_IS_DDP = _LOCAL_RANK >= 0
if _IS_DDP:
    import torch
    torch.cuda.set_device(_LOCAL_RANK)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

ALPACA_PROMPT_TEMPLATE = (
    "### Instruction:\n{instruction}\n\n"
    "### Input:\n{input}\n\n"
    "### Response:\n{output}"
)


def load_dataset_from_jsonl(path: str) -> Dataset:
    """Load Alpaca-format JSONL debate samples."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Skipping invalid JSON line: %s", e)
    logger.info("Loaded %d training samples from %s", len(records), path)
    if not records:
        raise ValueError(f"Dataset at {path!r} is empty — aborting.")
    return Dataset.from_list(records)


def format_prompt(example: dict) -> dict:
    """Apply Alpaca template → 'text' field consumed by SFTTrainer."""
    text = ALPACA_PROMPT_TEMPLATE.format(
        instruction=example.get("instruction", ""),
        input=example.get("input", ""),
        output=example.get("output", ""),
    )
    return {"text": text}


# ---------------------------------------------------------------------------
# LoRA config
# ---------------------------------------------------------------------------

def get_lora_config(cfg: TrainingConfig) -> LoraConfig:
    return LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=cfg.lora_r,
        lora_alpha=cfg.lora_alpha,
        lora_dropout=cfg.lora_dropout,
        bias="none",
        # Target the attention + MLP projection layers in Qwen-2.5 / Llama-3 family
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LoRA SFT training for Sovereign Judge")
    p.add_argument("--base_model", default=None)
    p.add_argument("--dataset_path", default=None)
    p.add_argument("--output_dir", default=None)
    p.add_argument("--epochs", type=int, default=None)
    p.add_argument("--batch_size", type=int, default=None)
    p.add_argument("--lr", type=float, default=None)
    p.add_argument("--max_seq_len", type=int, default=None)
    p.add_argument("--lora_r", type=int, default=None)
    p.add_argument("--lora_alpha", type=int, default=None)
    p.add_argument("--no_4bit", action="store_true", help="Disable QLoRA 4-bit quantization")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = TrainingConfig()

    # CLI overrides (if provided)
    if args.base_model:
        cfg.base_model = args.base_model
    if args.dataset_path:
        cfg.dataset_path = args.dataset_path
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.epochs is not None:
        cfg.epochs = args.epochs
    if args.batch_size is not None:
        cfg.batch_size = args.batch_size
    if args.lr is not None:
        cfg.lr = args.lr
    if args.max_seq_len is not None:
        cfg.max_seq_len = args.max_seq_len
    if args.lora_r is not None:
        cfg.lora_r = args.lora_r
    if args.lora_alpha is not None:
        cfg.lora_alpha = args.lora_alpha
    if args.no_4bit:
        cfg.use_4bit = False

    os.makedirs(cfg.output_dir, exist_ok=True)
    os.makedirs(cfg.hf_cache, exist_ok=True)
    os.environ["HF_HOME"] = cfg.hf_cache
    os.environ["TRANSFORMERS_CACHE"] = os.path.join(cfg.hf_cache, "transformers")

    logger.info("=== Sovereign Judge LoRA Training ===")
    logger.info("Base model : %s", cfg.base_model)
    logger.info("Dataset    : %s", cfg.dataset_path)
    logger.info("Output     : %s", cfg.output_dir)
    logger.info("Epochs     : %d | Batch: %d | LR: %g | LoRA r=%d α=%d",
                cfg.epochs, cfg.batch_size, cfg.lr, cfg.lora_r, cfg.lora_alpha)
    logger.info("4-bit QLoRA: %s", cfg.use_4bit)

    # ---- Tokenizer ----
    logger.info("Loading tokenizer from %s ...", cfg.base_model)
    tokenizer = AutoTokenizer.from_pretrained(
        cfg.base_model,
        trust_remote_code=True,
        padding_side="right",
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # ---- Dataset ----
    raw_dataset = load_dataset_from_jsonl(cfg.dataset_path)

    def format_and_tokenize(example: dict) -> dict:
        text = ALPACA_PROMPT_TEMPLATE.format(
            instruction=example.get("instruction", ""),
            input=example.get("input", ""),
            output=example.get("output", ""),
        )
        tokenized = tokenizer(
            text,
            truncation=True,
            max_length=cfg.max_seq_len,
            padding=False,
        )
        return {
            "input_ids": tokenized["input_ids"],
            "attention_mask": tokenized["attention_mask"],
            "text": text,
        }

    dataset = raw_dataset.map(format_and_tokenize, remove_columns=raw_dataset.column_names)
    logger.info("Dataset formatted. Sample:\n%s", dataset[0]["text"][:200])
    logger.info("Max token length in dataset: %d", max(len(x) for x in dataset["input_ids"]))

    # ---- Model ----
    bnb_config: Optional[BitsAndBytesConfig] = None
    if cfg.use_4bit:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
        )

    logger.info("Loading base model (DDP=%s, local_rank=%d) ...", _IS_DDP, _LOCAL_RANK)
    # In DDP mode each rank loads its own complete model copy onto its assigned GPU.
    # device_map='auto' must NOT be used with DDP — it triggers pipeline parallelism
    # which conflicts with the DDP gradient synchronisation and causes OOM/hangs.
    if _IS_DDP:
        device_map = {""f"cuda:{_LOCAL_RANK}"}
    else:
        device_map = "auto"

    model = AutoModelForCausalLM.from_pretrained(
        cfg.base_model,
        quantization_config=bnb_config,
        device_map=device_map,
        trust_remote_code=True,
        attn_implementation="eager",  # Flash-Attn 2 may not be available on MI250x
        dtype=torch.bfloat16,
    )

    if cfg.use_4bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    # Apply LoRA adapters
    lora_cfg = get_lora_config(cfg)
    model = get_peft_model(model, lora_cfg)
    model.print_trainable_parameters()

    # ---- Training config (SFTConfig = TrainingArguments + SFT params in TRL 1.4) ----
    training_args = SFTConfig(
        output_dir=cfg.output_dir,
        num_train_epochs=cfg.epochs,
        per_device_train_batch_size=cfg.batch_size,
        gradient_accumulation_steps=cfg.grad_accum,
        learning_rate=cfg.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=cfg.warmup_ratio,
        fp16=False,
        bf16=True,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=5,
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=False,
        report_to="none",
        dataloader_num_workers=0,
        optim="adamw_torch",
        max_grad_norm=0.3,
        weight_decay=0.001,
        seed=42,
        # SFT-specific (TRL 1.4 moved these into SFTConfig)
        max_length=cfg.max_seq_len,
        dataset_text_field="text",
        packing=False,
    )

    # ---- SFT Trainer ----
    # TRL 1.4: SFTConfig handles max_length/packing; data_collator passed separately.
    collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer,
        mlm=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        processing_class=tokenizer,
        data_collator=collator,
    )

    logger.info("Starting LoRA SFT training ...")
    trainer.train()

    # ---- Save LoRA adapters ----
    adapter_path = os.path.join(cfg.output_dir, "final-adapter")
    trainer.model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    logger.info("LoRA adapter saved to %s", adapter_path)

    # ---- Merge LoRA into base model (full model export) ----
    logger.info("Merging LoRA adapter into base model ...")
    try:
        from peft import PeftModel

        merged_path = os.path.join(cfg.output_dir, "merged")
        os.makedirs(merged_path, exist_ok=True)

        base_for_merge = AutoModelForCausalLM.from_pretrained(
            cfg.base_model,
            torch_dtype=torch.float16,
            device_map="cpu",
            trust_remote_code=True,
        )
        merged_model = PeftModel.from_pretrained(base_for_merge, adapter_path)
        merged_model = merged_model.merge_and_unload()
        merged_model.save_pretrained(merged_path)
        tokenizer.save_pretrained(merged_path)
        logger.info("Merged model saved to %s", merged_path)
    except Exception as exc:
        logger.warning("Merge step failed (adapters still saved): %s", exc)

    logger.info("=== Training completed successfully ===")


if __name__ == "__main__":
    main()
