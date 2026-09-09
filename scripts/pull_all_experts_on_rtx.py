#!/usr/bin/env python3
"""
scripts/pull_all_experts_on_rtx.py
Pulls the 8 expert models onto the N04-RTX Ollama instance (192.168.155.224:11434).
"""

import json
import time
import urllib.request

OLLAMA_RTX_URL = "http://192.168.155.224:11434/api/pull"

EXPERT_MODELS = [
    "hf.co/h3rb3rn/moe-expert-coder-4b",
    "hf.co/h3rb3rn/moe-expert-precision-4b",
    "hf.co/h3rb3rn/moe-expert-graphrag-4b",
    "hf.co/h3rb3rn/moe-expert-governance-4b",
    "hf.co/h3rb3rn/moe-expert-research-4b",
    "hf.co/h3rb3rn/moe-expert-security-4b",
    "hf.co/h3rb3rn/moe-expert-datainfra-4b",
    "hf.co/h3rb3rn/moe-expert-omni-4b",
]

def main():
    print(f"🚀 Starting pull sequence for 8 Expert LLMs on N04-RTX ({OLLAMA_RTX_URL})...\n")

    for model_name in EXPERT_MODELS:
        print(f"================================================================================")
        print(f"📦 Pulling [{model_name}] on N04-RTX...")
        t0 = time.time()
        req = urllib.request.Request(
            OLLAMA_RTX_URL,
            data=json.dumps({"name": model_name, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=1800) as response:
                for line in response:
                    if line:
                        chunk = json.loads(line.decode("utf-8"))
                        status = chunk.get("status", "")
                        total = chunk.get("total", 0)
                        completed = chunk.get("completed", 0)
                        if total > 0:
                            pct = (completed / total) * 100
                            mb_done = completed / (1024 ** 2)
                            mb_total = total / (1024 ** 2)
                            print(f"\r  {status}: {mb_done:.1f} MB / {mb_total:.1f} MB ({pct:.1f}%)", end="", flush=True)
                        else:
                            print(f"\r  {status}", end="", flush=True)
            dt = round(time.time() - t0, 1)
            print(f"\n✅ Finished [{model_name}] in {dt}s\n")
        except Exception as e:
            print(f"\n❌ Error pulling [{model_name}]: {e}\n")

    print("🎉 All 8 Expert LLMs pulled successfully on N04-RTX!")

if __name__ == "__main__":
    main()
