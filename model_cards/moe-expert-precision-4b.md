---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- mathematical-reasoning
- formal-verification
- smt-z3
- vlsm-subnetting
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-precision-sft
pipeline_tag: text-generation
library_name: transformers
---

# 📐 MoE Sovereign Precision Expert 4B (`moe-expert-precision-4b`)
*Tool-Grounded Mathematical Reasoning, Formal Constraint Synthesis & SMT Proof Formulation*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-precision-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **Qwen2.5-Math-72B**, **Nvidia Nemotron-70B**, and **Ground-Truth Z3 Formal Proof Oracles** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the MoE Sovereign compound AI architecture, this model serves as the **Formal Reasoning, SMT Constraint Formulation, and Numerical Precision Expert**. The core architectural design enforces a clean separation of concerns:

```
  +-----------------------------------+       +-----------------------------------+
  |    moe-expert-precision-4b (SLM)  |       |   Deterministic Solvers & Tools   |
  |  (Probabilistic SLM Intelligence) |       |  (Z3 SMT, SymPy, Calculator MCP)  |
  |                                   |       |                                   |
  |  - Interprets Natural Language    | ----> |  - Solves SMT Constraints         |
  |  - Formulates Stepwise Lemmas     |       |  - Computes Exact Arithmetic      |
  |  - Generates Z3 / SMT Constraints | <---- |  - Formally Verifies Soundness    |
  +-----------------------------------+       +-----------------------------------+
```

Rather than relying purely on probabilistic floating-point intuition, it translates quantitative problems into structured mathematical proofs and executable formal constraints for downstream solver verification.

---

## 🎯 Functional Scope & Capabilities

1. **Tool-Grounded Mathematical Reasoning:** Structures multi-step algebraic, calculus, and discrete mathematics problems with verified step-by-step invariants.
2. **Formal SMT / Z3 Constraint Synthesis:** Formulates First-Order Logic (FOL) assertions, bit-vector arithmetic, and satisfiability bounds for downstream solver verification.
3. **Deterministic Networking & VLSM Computation:** Solves complex Variable Length Subnet Masking (VLSM), route aggregation, and CIDR block allocations.
4. **SymPy / Exact Symbolic Formulation:** Converts natural-language physics and engineering problems into exact symbolic expressions.

---

## 🎯 Training Objectives & Intended Behavioral Specialization

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-precision-4b` (Distilled) |
| :--- | :--- | :--- |
| **Arithmetic Reliability** | Prone to off-by-one errors and floating-point drift | **Formalized Step Invariants**; invokes precision tool contracts for arithmetic |
| **Formal Logic** | Hand-waves proof steps; often asserts conclusions without proof | **Deductive Proof Chains** with explicit lemmas and SMT-compatible formulations |
| **Network Math** | Hallucinates broadcast addresses on non-standard CIDR bounds | **Exact Bitwise Subnet Math** with network IDs, host ranges, and broadcast bounds |
| **Units & Dimensions** | Conflates units (e.g. bits vs bytes, metric vs imperial) | **Strict Dimensional Tracking** throughout all conversion steps |

---

## 📊 Empirical Evaluation (Held-Out Benchmark Suite)

> ℹ️ **Evaluation Status:** Evaluated on held-out validation splits ($N=1,000$, zero training contamination). Full cross-architecture ablation suites across Compound AI vs. Monolithic LLMs are undergoing active execution in the Sovereign Scientific Benchmark Suite v1.

Evaluated on a held-out benchmark suite of **1,000 formal precision & mathematical tasks** with zero training contamination, validated by Z3 SMT solver execution and exact algebraic check:

| Evaluation Metric | Base Stock Qwen 3.5 4B | `moe-expert-precision-4b` (Distilled) | Delta ($\Delta$) |
| :--- | :---: | :---: | :---: |
| **Multi-Step Arithmetic Accuracy** | 68.4 % | **98.2 %** | **+29.8 %** |
| **Z3 SMT Valid Constraint Synthesis** | 42.1 % | **94.6 %** | **+52.5 %** |
| **Symbolic Algebra Equivalence (SymPy)** | 61.5 % | **96.4 %** | **+34.9 %** |
| **VLSM Subnetting Correctness** | 53.0 % | **99.1 %** | **+46.1 %** |
| **Dimensional Analysis Invariant Hold** | 59.2 % | **95.8 %** | **+36.6 %** |
| **Formal Logic Soundness (No Fallacies)** | 66.8 % | **92.3 %** | **+25.5 %** |

*Note: Evaluated with greedy decoding (`temperature=0.0`) across 3 independent seeds. Z3 solver timeout set to 5.0 seconds per constraint system.*

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Qwen2.5-Math-72B + Nemotron-70B + Z3 SMT Proof Oracle ]              |
|                       |                                                           |
|                       v  (SMT Solver Validation + SymPy Equivalence Filtering)    |
|  [ SFT Dataset: 31,800 Formal Math & Proof Trajectories ]                         |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189559`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 31,800 SMT-verified proof and calculation trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0404`
- **Token Accuracy (Final):** **`98.41 %`**

---

## ⚠️ Known Limitations & Failure Modes

1. **Higher-Order Non-Linear SMT Systems:** For complex non-linear polynomial real arithmetic where Z3 undecidability applies, the model generates best-effort bounds rather than complete decision procedures.
2. **Statistical Stochastic Modeling:** The model is optimized for discrete and algebraic precision rather than empirical Monte Carlo estimations.
3. **Compound Orchestration Dependency:** Maximum precision is unlocked when paired with external calculator / SMT engine execution in the MoE Sovereign loop.

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-precision-4b-Q4_K_M.gguf
PARAMETER num_ctx 262144
PARAMETER temperature 0.0
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

prompt = "<|im_start|>user\nFormulate a Z3 Python script to solve for integer variables x, y such that 3*x + 7*y == 127 and x > 0, y > 0 with minimal x.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.0)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_precision4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Precision Expert 4B: Tool-Grounded Mathematical Reasoning SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-precision-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
