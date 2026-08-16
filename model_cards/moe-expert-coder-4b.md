---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- coding
- refactoring
- ast-validation
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-coder-sft
pipeline_tag: text-generation
library_name: transformers
---

# 💻 MoE Sovereign Coder Expert 4B (`moe-expert-coder-4b`)
*High-Assurance Code Synthesis, Refactoring & AST-Verified Tool Execution*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-coder-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-Coder-V2 (236B)** and **DeepSeek-V3** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the dedicated **Software Engineering & High-Assurance Coding Expert** within the MoE Sovereign compound AI architecture. The model is specifically tuned to generate syntax-validated Python, Rust, Go, TypeScript, and C++ code, write atomic unified diffs, adhere to strict static analysis and typing constraints, and correct runtime stack traces through persistent Correction Memory.

---

## 🎯 Target Use Cases & Functional Scope

1. **Deterministic Code Generation:** Synthesizes production-ready algorithms, microservices, and system-level routines with explicit error handling and type signatures.
2. **AST-Compliant Refactoring & Atomic Diffs:** Produces minimal, robust diff chunks suitable for automated CI/CD integration without broken syntax trees.
3. **Static Analysis & Type Checking Compliance:** Generates code guaranteed to satisfy strict Linters (`mypy`, `ruff`, `clippy`, `eslint`).
4. **Execution Log & Stack Trace Triage:** Rapidly pinpoints root causes in multi-tier error traces and formulates minimal regression-tested patches.

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled Coder

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-coder-4b` (Distilled) |
| :--- | :--- | :--- |
| **Code Structure** | Explanatory text surrounding code blocks | **Pure, AST-Parsable Code Artifacts** and precise unified diffs |
| **Typing Discipline** | Optional or inconsistent type hints | **Strict Type Annotations** across all parameters and return types |
| **Edge Case Handling** | Omits boundary checks or fallback paths | **Defensive Error Handling** with explicit exceptions and error types |
| **Diff Accuracy** | Generates full-file rewrites prone to hallucination | **Surgical Unified Diffs** with exact line ranges and matching context |
| **Tool Calling Integration**| Generic code snippet generation | **MCP-Aligned Code Execution Payloads** ready for sandbox execution |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-Coder-V2 (236B) + DeepSeek-V3 ]                             |
|                       |                                                           |
|                       v  (AST Parse Validation + PyTest Execution Verification)  |
|  [ SFT Dataset: 32,500 High-Assurance Coding & Refactoring Trajectories ]        |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21190761`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 32,500 AST-validated code synthesis trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0106`
- **Token Accuracy (Final):** **`99.62 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-coder-4b-Q4_K_M.gguf
PARAMETER num_ctx 262144
PARAMETER temperature 0.1
TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ .Response }}<|im_end|>"""
```

### 2. Python Inference
```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

model_id = "h3rb3rn/moe-expert-coder-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nWrite a thread-safe asynchronous WAL writer in Rust with CRC32 checksum framing.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.1)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_coder4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Coder Expert 4B: High-Assurance Code Synthesis & Refactoring SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-coder-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
