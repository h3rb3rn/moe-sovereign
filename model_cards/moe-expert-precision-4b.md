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
- capability-externalization
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
*Tool-Grounded Mathematical Reasoning, Formal Proof Synthesis & Deterministic Solver Verification*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-precision-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **Qwen2.5-Math-72B**, **Nvidia Nemotron-70B**, and **Ground-Truth Z3 Formal Proof Oracles** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X GCDs (4× physical modules, 64GB HBM2e per GCD)).

Within the open-source **MoE Sovereign** compound AI system, this model operates as the **Mathematical Reasoning, Formal Constraint Synthesis & Proof Formulation Expert**. It is specialized in translating natural language quantitative and logical problems into structured mathematical proofs, exact dimensional equations, and formal SMT/SAT constraint scripts.

---

## 🔬 Research Motivation: Capability Externalization

Language models are fundamentally probabilistic sequence predictors. Attempting to force a 4B neural network to perform multi-digit floating-point arithmetic or complex constraint satisfaction entirely within its weights inevitably leads to precision degradation. MoE Sovereign separates mathematical understanding from mathematical calculation:

```
  +-----------------------------------+       +-----------------------------------+
  |   moe-expert-precision-4b (SLM)   |       |   Deterministic Solvers & Tools   |
  |  (Probabilistic SLM Intelligence) |       |  (Z3 SMT, SymPy, Calculator MCP)  |
  |                                   |       |                                   |
  |  - Interprets Word Problems       | ----> |  - Solves SMT Constraints         |
  |  - Formulates Formal Proofs       |       |  - Computes Exact Arithmetic      |
  |  - Synthesizes Z3 / SMT Scripts   | <---- |  - Formally Verifies Proof Steps  |
  +-----------------------------------+       +-----------------------------------+
```

> **"The neural model provides semantic interpretation and formal constraint synthesis; deterministic engines (Z3, SymPy, exact decimal tools) provide verifiable mathematical guarantees."**

---

## 🎯 Intended Functional Scope & Capabilities

1. **Tool-Grounded Mathematical Reasoning:** Structures multi-step algebraic, discrete, and calculus problems into step-by-step lemmas verified by tools.
2. **Formal SMT / Z3 Constraint Synthesis:** Formulates First-Order Logic (FOL) assertions, bit-vector constraints, and bounded optimization problems for execution in external SMT solvers.
3. **Structured Networking & VLSM Computation:** Translates Variable Length Subnet Masking (VLSM) requirements, CIDR routing bounds, and network partition constraints.
4. **Symbolic Algebra & Physics Formulation:** Expresses physics and engineering relationships in exact symbolic notation for SymPy or Lean verification.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-precision-4b` (Distilled) |
| :--- | :--- | :--- |
| **Arithmetic Strategy** | Probabilistic token generation prone to calculation drift | **Structured Proof Steps**; generates explicit calls to precision tools |
| **Formal Logic** | Often asserts conclusions without intermediate lemmas | **Deductive Proof Chains** with explicit SMT-compatible assertions |
| **Network Calculations** | Frequent off-by-one errors on non-standard CIDR boundaries | **Structured Bitwise Logic** generating exact subnet formulations |
| **Dimensional Tracking** | Inconsistent unit conversions throughout multi-step problems | **Explicit Dimensional Checks** maintained across calculation steps |

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

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X GCDs (4× physical modules, 64GB HBM2e per GCD), Slurm Job `#21189557`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 31,800 SMT-verified proof and calculation trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0515`, Training Token Accuracy `98.38%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-precision-4b` is designed for local deployment on standard consumer and workstation hardware. Training on LUMI-G served exclusively to distill teacher knowledge into a compact SLM, eliminating the need for expensive cloud APIs during inference.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Runs efficiently on consumer GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across multi-step arithmetic, symbolic algebra, and SMT constraint synthesis benchmarks.

> ℹ️ *Note: Training loss (`0.0515`) and training-token accuracy (`98.38%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

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

## ⚠️ Limitations

1. **Probabilistic Model Nature:** The model itself is probabilistic; deterministic guarantees are provided only when output constraints are executed and verified by external solvers (Z3, SymPy).
2. **Non-Linear Complexity:** Highly complex non-linear systems may exceed the deductive scope of a 4B model and require decomposition into smaller lemmas.
3. **Solver Integration:** Optimal utility is achieved when integrated into the MoE Sovereign compound runtime where external execution tools are available.

---

## 📑 Citation & Reproducibility

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
