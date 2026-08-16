---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- precision
- mathematical-reasoning
- formal-verification
- smt-solvers
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-precision-sft
pipeline_tag: text-generation
library_name: transformers
---

# 📐 MoE Sovereign Precision & Math Expert 4B (`moe-expert-precision-4b`)
*Deterministic Mathematical Reasoning, Formal Proofs & SMT Constraint Synthesis*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-precision-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **Qwen2.5-Math-72B**, **Nvidia Nemotron-70B**, and ground-truth **Z3 SMT Solver proofs** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the **Deterministic Mathematical & Formal Verification Expert** within the MoE Sovereign compound AI architecture. The model is specifically optimized for formal logic synthesis, mathematical proofs, dimensional analysis, constraint optimization, and integration with external symbolic solvers (SymPy, Z3, Lean4).

---

## 🎯 Target Use Cases & Functional Scope

1. **Formal Proofs & Logic Verification:** Constructs step-by-step rigorous deductive proofs and identifies invalid inference steps.
2. **SMT & SAT Constraint Formulation:** Translates complex operational requirements into valid Z3 Python and SMT-LIB2 problem definitions.
3. **High-Precision Numerical & Algebraic Computation:** Solves non-trivial differential equations, matrix transformations, and discrete optimization problems.
4. **Physical & Unit Dimensional Analysis:** Enforces SI unit consistency across physical systems and engineering telemetry.

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled Precision

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-precision-4b` (Distilled) |
| :--- | :--- | :--- |
| **Reasoning Chain** | Intuitive next-token guesses prone to arithmetic slips | **Strict Deductive Proof Chains** with explicit lemmas and invariants |
| **Solver Integration** | Unstructured equations in plain text | **Executable Z3 / SymPy Formulations** with verifiable sat/unsat checks |
| **Edge Precision** | Rounding errors and floating-point ambiguity | **Exact Symbolic Representation** (fractions, radicals, formal terms) |
| **Constraint Validation**| Loose constraint checks | **Formal Invariant Verification** with bounded domain guarantees |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Qwen2.5-Math-72B + Nemotron-70B + Z3 SMT Ground Truth Solver ]       |
|                       |                                                           |
|                       v  (Symbolic Proof Verification + Formal Invariant Checks)  |
|  [ SFT Dataset: 35,000 Verified Logic & Mathematical Proof Trajectories ]         |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189557`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 35,000 formally validated proof trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0404`
- **Token Accuracy (Final):** **`98.41 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-precision-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-precision-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nFormulate a Z3 solver script in Python to verify deadlock-freedom in a 5-node distributed consensus ring.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_precision4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Precision Expert 4B: Deterministic Mathematical & SMT Verification SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-precision-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
