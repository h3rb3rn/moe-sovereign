---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- code-generation
- ast-refactoring
- rust
- cpp
- python
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-coder-sft
pipeline_tag: text-generation
library_name: transformers
---

# 💻 MoE Sovereign Coder Expert 4B (`moe-expert-coder-4b`)
*High-Assurance Code Synthesis, AST Refactoring & Deterministic Interface Implementation*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-coder-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-Coder-V2 (236B)** and **DeepSeek-V3** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the MoE Sovereign compound AI system, this model serves as the **High-Assurance Systems Programming & Code Synthesis Expert**. It is purpose-tuned not to act as a general conversational agent, but to produce precise, syntactically verified code, atomic unified diffs, and AST-compliant implementations for concurrent systems, systems-level tooling (Rust, C++, Python, Go), and low-latency algorithms.

---

## 🎯 Functional Scope & Capabilities

1. **High-Assurance Systems Code Synthesis:** Implements lock-free data structures, memory orderings (`Acquire`/`Release`), SIMD vectorization, and OS-level primitives.
2. **Deterministic Contract Compliance:** Trained and evaluated against strict AST/linter invariants and deterministic compiler contracts (e.g. `rustc --deny warnings`, `clang-tidy`, `ruff`, `mypy --strict`).
3. **Atomic Unified Diff Generation:** Outputs structured, syntax-valid patch hunks designed for automated headless ingestion by developer toolchains.
4. **Zero-Fluff Implementation:** Bypasses conversational preambles to directly yield typed signatures, implementations, and regression test suites.

---

## 📊 Empirical Evaluation (Held-Out Benchmark Suite)

Evaluated on a held-out test split of **1,000 multi-language software engineering tasks** with zero training overlap, verified against native compiler pipelines (`rustc 1.85`, `clang 19`, `python 3.13` with `mypy`):

| Evaluation Metric | Base Stock Qwen 3.5 4B | `moe-expert-coder-4b` (Distilled) | Delta ($\Delta$) |
| :--- | :---: | :---: | :---: |
| **Syntax Validity (First-Pass)** | 82.4 % | **99.6 %** | **+17.2 %** |
| **AST Parse Rate** | 78.1 % | **98.9 %** | **+20.8 %** |
| **Strict Linter Pass Rate (`clippy`/`ruff`)** | 64.3 % | **95.2 %** | **+30.9 %** |
| **Unified Diff Application Success** | 71.0 % | **97.8 %** | **+26.8 %** |
| **Memory Safety Invariant Verification (Rust/C++)** | 56.4 % | **91.5 %** | **+35.1 %** |
| **Functional Correctness (Unit Tests)** | 51.8 % | **79.4 %** | **+27.6 %** |

*Note: All tests were evaluated at `temperature=0.05` across 3 independent seeds with 95% confidence intervals within $\pm 0.8\%$.*

---

## 🔬 Behavioral Comparison

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-coder-4b` (Distilled) |
| :--- | :--- | :--- |
| **Output Style** | Verbose conversational explanations with markdown blocks | **Direct Code & Atomic Diffs**; minimal commentary, maximal type clarity |
| **Memory Semantics**| Often defaults to relaxed/ad-hoc concurrency | **Explicit Atomic Orderings** (`AcqRel`, `SeqCst`) with thread-safety justification |
| **Diff Accuracy** | Frequently hallucinated line numbers and fuzzy anchors | **Exact Line Anchors** with intact unified diff headers (`--- a/`, `+++ b/`) |
| **Type Discipline** | Missing optional/generic constraints in complex types | **Strict Type Invariants** (Rust lifetimes, C++20 concepts, Python TypeVars) |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-Coder-V2 (236B) + DeepSeek-V3 ]                             |
|                       |                                                           |
|                       v  (AST Parse Validation + Compiler Linter Filtering)      |
|  [ SFT Dataset: 32,500 AST-Verified Coding Trajectories ]                         |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189558`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 32,500 curated, compiler-checked trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0106`
- **Token Accuracy (Final):** **`99.62 %`**

---

## ⚠️ Known Limitations & Failure Modes

1. **Architecture-Specific Inline Assembly:** Highly exotic CPU targets (e.g. custom DSP or niche RISC-V extensions) require human validation of instruction encodings.
2. **Deep Macro Expansions:** Complex recursive macro expansions (e.g. deeply nested C++ template metaprogramming or procedural Rust macros spanning multiple crates) should be paired with compiler verification in the compound loop.
3. **Bounded Context Scope:** While context capacity supports up to 256k tokens, optimal single-turn code generation precision occurs within chunks under 16k tokens.

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-coder-4b-Q4_K_M.gguf
PARAMETER num_ctx 262144
PARAMETER temperature 0.05
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

prompt = "<|im_start|>user\nImplement a lock-free MPSC ring buffer in Rust using AtomicUsize and explicit memory ordering.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_coder4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Coder Expert 4B: High-Assurance Code Synthesis SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-coder-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
