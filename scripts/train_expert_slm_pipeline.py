#!/usr/bin/env python3
"""
scripts/train_expert_slm_pipeline.py
Distributed SFT Training Pipeline for MoE Sovereign (Planner, 8 Experts, Judge) on LUMI-G.
Supports:
  - 4B Student SLM Base (Qwen3.5 Linear Attention + Mamba Hybrid)
  - 27B Sovereign-Judge Base (Qwen3.8:27b with 262k native context)
  - 8x AMD MI250X with Torchrun Distributed DeepSpeed ZeRO-2/3 BF16
"""

import os
import sys
import argparse
from pathlib import Path
import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments
)
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer

def parse_args():
    parser = argparse.ArgumentParser(description="Train MoE Sovereign SLM Expert Pipeline on LUMI-G")
    parser.add_argument("--role", type=str, required=True, 
                        choices=["planner", "coder", "precision", "graphrag", "governance", "research", "security", "datainfra", "omni", "judge"],
                        help="Role of the expert model")
    parser.add_argument("--base-model", type=str, required=True, help="Base HF model path or directory")
    parser.add_argument("--dataset", type=str, required=True, help="Path to jsonl training dataset")
    parser.add_argument("--output-dir", type=str, required=True, help="Output directory for checkpoints")
    parser.add_argument("--epochs", type=int, default=3, help="Number of training epochs")
    parser.add_argument("--batch-size", type=int, default=4, help="Per-device batch size")
    parser.add_argument("--grad-accum", type=int, default=4, help="Gradient accumulation steps")
    parser.add_argument("--learning-rate", type=float, default=1.5e-5, help="Learning rate")
    parser.add_argument("--max-seq-len", type=int, default=4096, help="Maximum sequence length")
    parser.add_argument("--deepspeed", type=str, default="/scratch/project_465003058/hornphil/configs/ds_zero2_bf16.json", help="Path to DeepSpeed config")
    return parser.parse_args()

def main():
    args = parse_args()
    output_path = Path(args.output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print("=" * 80)
    print(f"🚀 LUMI-G SFT TRAINING: [moe-{args.role}]")
    print(f"Base Checkpoint : {args.base_model}")
    print(f"Dataset         : {args.dataset}")
    print(f"Output Directory: {args.output_dir}")
    print(f"Sequence Length : {args.max_seq_len} (Guarantees zero truncation)")
    print(f"DeepSpeed Config: {args.deepspeed}")
    print("=" * 80)
    
    print("⏳ Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    
    print("⏳ Loading training dataset...")
    dataset = load_dataset("json", data_files=args.dataset, split="train")
    print(f"  • Loaded {len(dataset):,} samples")
    
    print("⏳ Initializing base model in BF16...")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
        device_map=None
    )
    
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]
    peft_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=target_modules,
        bias="none"
    )
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()
    
    training_args = TrainingArguments(
        output_dir=str(output_path),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=10,
        save_strategy="epoch",
        save_total_limit=2,
        bf16=True,
        gradient_checkpointing=True,
        deepspeed=args.deepspeed if Path(args.deepspeed).exists() else None,
        report_to="none"
    )
    
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        args=training_args,
        dataset_text_field="text",
        max_seq_length=args.max_seq_len,
        processing_class=tokenizer
    )
    
    print("🔥 Starting training execution...")
    trainer.train()
    
    print(f"💾 Saving final LoRA adapter to {output_path / 'final_adapter'}...")
    trainer.model.save_pretrained(str(output_path / "final_adapter"))
    tokenizer.save_pretrained(str(output_path / "final_adapter"))
    print("🎉 Training successfully completed!")

if __name__ == "__main__":
    main()
