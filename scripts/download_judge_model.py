"""
download_judge_model.py — Download Qwen2.5-7B-Instruct to LUMI-G scratch (login node)

Run this ONCE from a LUMI login node (internet-enabled) BEFORE submitting
train_judge_lora.sh to the compute partition (no internet there).

Usage:
    python3 download_judge_model.py

Env vars:
    JUDGE_BASE_MODEL  HF model id (default: Qwen/Qwen2.5-7B-Instruct)
    HF_HOME           Cache directory (default: /scratch/project_465003058/hornphil/hf_cache)
"""

import os
import sys

HF_HOME = os.getenv("HF_HOME", "/scratch/project_465003058/hornphil/hf_cache")
BASE_MODEL = os.getenv("JUDGE_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")

os.makedirs(HF_HOME, exist_ok=True)
os.environ["HF_HOME"] = HF_HOME

print(f"Downloading {BASE_MODEL!r} → {HF_HOME}")

try:
    from huggingface_hub import snapshot_download

    path = snapshot_download(
        repo_id=BASE_MODEL,
        cache_dir=HF_HOME,
        ignore_patterns=["*.msgpack", "*.h5", "*.ot", "flax_model*", "tf_model*"],
    )
    print(f"✅ Model downloaded to: {path}")
except ImportError:
    print("huggingface_hub not installed. Trying transformers AutoModel...")
    from transformers import AutoModelForCausalLM, AutoTokenizer

    AutoTokenizer.from_pretrained(BASE_MODEL, cache_dir=HF_HOME, trust_remote_code=True)
    AutoModelForCausalLM.from_pretrained(BASE_MODEL, cache_dir=HF_HOME, trust_remote_code=True)
    print(f"✅ Model downloaded via transformers to: {HF_HOME}")
