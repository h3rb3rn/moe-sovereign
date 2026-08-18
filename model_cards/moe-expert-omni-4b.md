---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- architectural-synthesis
- interface-harmonization
- capability-externalization
- cross-domain-integration
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-omni-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🌐 MoE Sovereign Omni Expert 4B (`moe-expert-omni-4b`)
*Cross-Domain Architectural Synthesis, Interface Harmonization & Multi-Expert Reconciliation*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-omni-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Qwen2.5-72B-Instruct** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the open-source **MoE Sovereign** compound AI system, this model operates strictly as the **Architectural Synthesizer & Cross-Domain Interface Harmonizer**. It is explicitly **not designed to replace individual domain specialists**. Instead, its role is to take specialized candidate outputs from peer models (Coder, Security, DataInfra, Governance, Precision, GraphRAG), identify cross-domain interface mismatches, detect semantic tensions, and compile them into a unified system specification.

```
  +------------------+    +-------------------+    +-------------------+
  | Coder Expert 4B  |    | Security Expert 4B|    |DataInfra Expert 4B|
  +--------+---------+    +---------+---------+    +---------+---------+
           |                        |                        |
           +------------------------+------------------------+
                                    |
                                    v
                 +--------------------------------------+
                 |      moe-expert-omni-4b (SLM)        |
                 |  - Harmonizes Interface Contracts    |
                 |  - Flags Cross-Domain Contradictions |
                 |  - Compiles Unified System Blueprint |
                 |  * Synthesizer, not domain oracle    |
                 +--------------------------------------+
                                    |
                                    v
                       [ Sovereign Consensus Gate ]
```

---

## 🔬 Research Motivation: Capability Externalization

In compound AI systems, subtasks are executed by domain-focused specialists. However, domain solutions often interact at shared boundaries:

> **"`moe-expert-omni-4b` specializes in the synthesis problem: reconciling interfaces, resolving naming discrepancies, and highlighting policy-versus-implementation trade-offs without needing to reproduce the deep domain capabilities of each specialist."**

This allows the compound AI system to maintain narrow, high-assurance expert models while still presenting a cohesive, holistic solution to the user.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Cross-Domain Interface Harmonization:** Aligns REST/gRPC contracts, database models, and security RBAC definitions into consistent specification files (e.g. OpenAPI 3.1).
2. **Structural Contradiction Flagging:** Identifies conflicts between domain recommendations (e.g. performance optimizations in code that conflict with regulatory data-retention rules).
3. **System Architecture Blueprinting:** Synthesizes multi-component architecture diagrams (Mermaid format), deployment manifests (Docker Compose), and workflow sequence charts.
4. **Epistemic Discipline:** Trained to integrate provided specialist findings rather than inventing unverified domain facts.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-omni-4b` (Distilled) |
| :--- | :--- | :--- |
| **Multi-Agent Merging** | Arbitrarily concatenates or overwrites domain outputs | **Harmonizes Interfaces & Contracts** while preserving domain constraints |
| **Conflict Detection** | Frequently overlooks subtle cross-domain tensions | **Explicitly Flags Contradictions** between security/legal policies and code |
| **Domain Scope** | Attempts to generate complex SQL or Z3 proofs itself | **Integrates Specialist Outputs** without fabricating missing facts |
| **Architecture Output** | High-level vague prose descriptions | **Structured System Specifications** (OpenAPI, Mermaid topologies) |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-V3 + Qwen2.5-72B-Instruct ]                                 |
|                       |                                                           |
|                       v  (Multi-Domain Consistency & Schema Validation Filtering) |
|  [ SFT Dataset: 35,000 Multi-Expert Harmonization Trajectories ]                  |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X 128GB GPUs, Slurm Job `#21189565`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 35,000 multi-expert synthesis and harmonization trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0075`, Training Token Accuracy `99.82%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-omni-4b` is designed for local deployment, providing fast architectural synthesis on commodity workstations.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Operates with low memory overhead on consumer GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across cross-domain interface contract harmonization, multi-agent conflict detection, and architectural diagram syntax validity.

> ℹ️ *Note: Training loss (`0.0075`) and training-token accuracy (`99.82%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-omni-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-omni-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nHarmonize the provided Coder REST API endpoints and Security RBAC policies into a unified OpenAPI 3.1 specification.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.1)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## ⚠️ Limitations

1. **Not a Standalone Specialist:** Omni is designed to integrate specialized inputs; querying it for deep specialized calculations or low-level kernel drivers without domain specialist inputs is out of scope.
2. **Context Capacity:** Designed and trained for multi-turn context maintenance across long context windows up to the supported window; very large inputs should be structured hierarchically.
3. **Conflict Arbitration:** When two expert outputs present an irreconcilable factual contradiction, Omni flags the tension for upstream consensus arbitration rather than making arbitrary unilateral decisions.

---

## 📑 Citation & Reproducibility

```bibtex
@misc{moe_sovereign_2026_omni4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Omni Expert 4B: Cross-Domain Architectural Synthesis SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-omni-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
