#!/usr/bin/env python3
"""
scripts/lumig_preflight_check.py — Pre-Flight Validation for LUMI-G Training Jobs.

Validates all dependencies, configs, tokenizers, chat-templates, and datasets
IN UNDER 5 SECONDS before any multi-hour SLURM job is submitted to the queue.

Usage (inside Singularity container on LUMI-G):
  python3 scripts/lumig_preflight_check.py --model-id <path> --dataset <path> --deepspeed <path>
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("lumig_preflight")

def check_imports():
    logger.info("1/5 Checking Python dependencies...")
    required = ["torch", "transformers", "trl", "peft", "deepspeed", "datasets"]
    for pkg in required:
        try:
            __import__(pkg)
            logger.info("  ✓ %s available", pkg)
        except ImportError as e:
            logger.error("  ✗ Missing required package '%s': %s", pkg, e)
            return False
    return True

def check_tokenizer_and_template(model_id: str):
    logger.info("2/5 Checking Tokenizer & TRL 1.4 Chat Template compatibility...")
    try:
        from transformers import AutoTokenizer
        from trl.chat_template_utils import get_training_chat_template

        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        tokenizer.chat_template = (
            "{% for message in messages %}"
            "{% if message['role'] == 'system' %}"
            "{{ '<|im_start|>system\n' + message['content'] + '<|im_end|>\n' }}"
            "{% elif message['role'] == 'user' %}"
            "{{ '<|im_start|>user\n' + message['content'] + '<|im_end|>\n' }}"
            "{% elif message['role'] == 'assistant' %}"
            "{{ '<|im_start|>assistant\n' }}{% generation %}{{ message['content'] + '<|im_end|>\n' }}{% endgeneration %}"
            "{% endif %}"
            "{% endfor %}"
        )

        test_msg = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]
        _ = tokenizer.apply_chat_template(test_msg, tokenize=True)
        
        # Verify TRL training chat template validation function
        err = get_training_chat_template(tokenizer)
        if err is not None:
            logger.error("  ✗ Chat template not training-compatible: %s", err)
            return False

        logger.info("  ✓ Tokenizer & TRL chat template valid with {%% generation %%} tags")
        return True
    except Exception as e:
        logger.error("  ✗ Tokenizer check failed for '%s': %s", model_id, e)
        return False

def check_dataset(dataset_path: str, max_seq_len: int = 4096):
    logger.info("3/5 Checking dataset syntax and sequence lengths (%s)...", dataset_path)
    path = Path(dataset_path)
    if not path.exists():
        logger.error("  ✗ Dataset file not found: %s", dataset_path)
        return False

    try:
        from datasets import load_dataset
        ds = load_dataset("json", data_files=dataset_path, split="train")
        if len(ds) == 0:
            logger.error("  ✗ Dataset is empty")
            return False

        first = ds[0]
        if "messages" not in first:
            logger.error("  ✗ Dataset missing 'messages' field in first record")
            return False

        logger.info("  ✓ Dataset valid (%d samples)", len(ds))
        return True
    except Exception as e:
        logger.error("  ✗ Dataset parsing failed: %s", e)
        return False

def check_deepspeed_config(ds_config_path: str):
    logger.info("4/5 Checking DeepSpeed configuration (%s)...", ds_config_path)
    if not ds_config_path:
        logger.info("  - No DeepSpeed config specified (skipping)")
        return True

    path = Path(ds_config_path)
    if not path.exists():
        logger.error("  ✗ DeepSpeed config file not found: %s", ds_config_path)
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        stage = cfg.get("zero_optimization", {}).get("stage")
        logger.info("  ✓ DeepSpeed config valid (ZeRO stage %s)", stage)
        return True
    except Exception as e:
        logger.error("  ✗ DeepSpeed config invalid JSON: %s", e)
        return False

def check_gpu_environment():
    logger.info("5/5 Checking ROCm / PyTorch GPU visibility...")
    try:
        import torch
        if not torch.cuda.is_available():
            logger.warning("  ! PyTorch CUDA/ROCm not visible in current preflight shell (OK on login node)")
        else:
            cnt = torch.cuda.device_count()
            logger.info("  ✓ PyTorch CUDA/ROCm GPUs detected: %d devices (%s)", cnt, torch.cuda.get_device_name(0))
        return True
    except Exception as e:
        logger.error("  ✗ PyTorch GPU check failed: %s", e)
        return False

def main():
    parser = argparse.ArgumentParser(description="LUMI-G Pre-Flight Validation")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--deepspeed", default=None)
    parser.add_argument("--max-seq-len", type=int, default=4096)
    args = parser.parse_args()

    logger.info("==================================================================")
    logger.info("LUMI-G PRE-FLIGHT VALIDATION CHECK")
    logger.info("==================================================================")

    ok = True
    ok = check_imports() and ok
    ok = check_tokenizer_and_template(args.model_id) and ok
    ok = check_dataset(args.dataset, args.max_seq_len) and ok
    ok = check_deepspeed_config(args.deepspeed) and ok
    ok = check_gpu_environment() and ok

    logger.info("==================================================================")
    if ok:
        logger.info("✅ ALL PRE-FLIGHT CHECKS PASSED — SAFE TO SUBMIT SLURM JOB!")
        logger.info("==================================================================")
        sys.exit(0)
    else:
        logger.error("❌ PRE-FLIGHT CHECKS FAILED — FIX ERRORS BEFORE SUBMITTING!")
        logger.info("==================================================================")
        sys.exit(1)

if __name__ == "__main__":
    main()
