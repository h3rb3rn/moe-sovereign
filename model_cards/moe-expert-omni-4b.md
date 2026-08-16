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

**`moe-expert-omni-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Qwen2.5-72B-Instruct** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the MoE Sovereign compound AI architecture, this model serves strictly as the **Architectural Synthesizer & Cross-Domain Interface Harmonizer**. It is **not a generalist model designed to replace domain specialists**. Instead, its explicit design role is to take structured outputs from multiple specialized experts (Coder, Security, DataInfra, Governance, Precision, GraphRAG), resolve cross-domain interface mismatches, flag semantic tensions, and compile them into a unified, coherent system architecture.

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
                 |  - Reconciles Interface Contracts    |
                 |  - Flags Cross-Domain Contradictions |
                 |  - Compiles Unified System Arch      |
                 |  * NEVER invents missing domain facts|
                 +--------------------------------------+
                                    |
                                    v
                       [ Sovereign Judge 35B / 27B ]
```

---

## 🎯 Functional Scope & Capabilities

1. **Cross-Domain Interface Harmonization:** Aligns REST/gRPC contracts, database schemas, security contexts, and regulatory tags into unified specification documents.
2. **Structural Conflict Flagging:** Detects semantic contradictions between expert proposals (e.g. Coder proposing in-memory caching while Governance mandates zero-persistence for PII).
3. **End-to-End System Blueprinting:** Produces clean architecture diagrams (Mermaid), API specifications (OpenAPI 3.1), and sequence flows combining multi-expert inputs.
4. **Strict Epistemic Discipline:** Prohibited from generating ungrounded domain claims; defers missing technical facts back to domain specialists.

---

## 📊 Empirical Evaluation (Held-Out Benchmark Suite)

Evaluated on a held-out benchmark suite of **1,000 multi-expert synthesis and system integration tasks** with zero training overlap:

| Evaluation Metric | Base Stock Qwen 3.5 4B | `moe-expert-omni-4b` (Distilled) | Delta ($\Delta$) |
| :--- | :---: | :---: | :---: |
| **API / Interface Contract Harmonization Rate** | 64.1 % | **96.2 %** | **+32.1 %** |
| **Cross-Domain Conflict Detection (Recall)** | 52.8 % | **94.5 %** | **+41.7 %** |
| **Architectural Coherence & Consistency** | 67.5 % | **95.8 %** | **+28.3 %** |
| **Synthesized Mermaid Diagram Validity** | 71.0 % | **98.7 %** | **+27.7 %** |
| **Epistemic Discipline (No Fact Hallucination)** | 60.3 % | **96.1 %** | **+35.8 %** |
| **OpenAPI 3.1 Syntax & Schema Validity** | 68.2 % | **97.4 %** | **+29.2 %** |

*Note: Evaluated at `temperature=0.1` across 3 independent seeds. Evaluated on multi-agent merge tasks combining 2 to 6 disparate domain expert outputs.*

---

## 🔬 Behavioral Comparison

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-omni-4b` (Distilled) |
| :--- | :--- | :--- |
| **Multi-Agent Merging** | Concatenates or overwrites domain expert outputs arbitrarily | **Harmonizes Contracts & Interfaces** while preserving domain guarantees |
| **Contradiction Handling**| Ignores security/governance constraints to satisfy code prompt | **Explicitly Flags Conflicts** between security/legal policies and code |
| **Domain Boundary** | Tries to hallucinate complex SQL or Z3 proofs itself | **Integrates Expert Solutions** without inventing unverified domain facts |
| **Architecture Output** | High-level vague prose | **Executable Blueprints** (OpenAPI, Mermaid, Docker Compose topologies) |

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

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189565`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 35,000 multi-expert synthesis trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0075`
- **Token Accuracy (Final):** **`99.82 %`**

---

## ⚠️ Known Limitations & Failure Modes

1. **Not a Standalone Domain Specialist:** Omni should not be called in isolation for deep mathematical proofs, raw kernel driver writing, or standalone regulatory audits without expert inputs.
2. **Upstream Contradiction Resolution:** When two expert outputs present an irreconcilable factual stalemate, Omni surfaces the dispute for the **Sovereign Judge (Belnap-Dunn consensus)** rather than making an arbitrary decision.
3. **Scaling Beyond 8 Domain Tracks:** For architectures involving more than 8 simultaneous expert tracks, two-pass hierarchical synthesis in the orchestrator is recommended.

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

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

## 📑 Citation

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
