#!/usr/bin/env python3
"""CPU-BF16 LoRA Merge Script for MoE Sovereign Expert SLMs on LUMI-G.

Merges LoRA adapter checkpoints into the base model on CPU with high RAM allocation
to prevent GPU OOM errors and prepare clean full weights for GGUF conversion.
"""

import sys
import argparse
from pathlib import Path
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

def parse_args():
    parser = argparse.ArgumentParser(description="Merge LoRA Adapter on CPU")
    parser.add_argument("--base-model", type=str, required=True, help="Path to base model")
    parser.add_argument("--adapter-dir", type=str, required=True, help="Path to LoRA adapter dir")
    parser.add_argument("--out-dir", type=str, required=True, help="Output directory for merged model")
    return parser.parse_args()

def main():
    args = parse_args()
    
    print("================================================================================")
    print("🔄 CPU-BF16 LORA MERGE PIPELINE")
    print(f"Base Model : {args.base_model}")
    print(f"Adapter Dir: {args.adapter_dir}")
    print(f"Output Dir : {args.out_dir}")
    print("================================================================================")
    
    out_path = Path(args.out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    print("⏳ Loading tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained(args.adapter_dir, trust_remote_code=True)
    
    print("⏳ Loading base model on CPU in BF16...")
    base_model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        device_map="cpu",
        trust_remote_code=True,
        low_cpu_mem_usage=True
    )
    
    print("⏳ Loading LoRA adapter onto base model...")
    peft_model = PeftModel.from_pretrained(
        base_model,
        args.adapter_dir,
        torch_dtype=torch.bfloat16,
        device_map="cpu"
    )
    
    print("🔥 Merging weights and unloading adapter...")
    merged_model = peft_model.merge_and_unload()
    
    print(f"💾 Saving merged full-weight model to {out_path}...")
    merged_model.save_pretrained(str(out_path), safe_serialization=True)
    tokenizer.save_pretrained(str(out_path))
    
    print(f"✅ CPU-BF16 Merge completed successfully -> {out_path}")

if __name__ == "__main__":
    main()
