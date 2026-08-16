---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- cross-domain
- multi-turn-dialogue
- system-synthesis
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-omni-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🌐 MoE Sovereign Omni Expert 4B (`moe-expert-omni-4b`)
*Cross-Domain Synthesis, Multi-Turn Workflow Integration & Holistic Solution Architecture*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-omni-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **Meta-Llama-3.1-405B-Instruct** and **Nvidia Nemotron-70B** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the dedicated **Cross-Domain Synthesis & Multi-Turn Integration Expert** within the MoE Sovereign compound AI architecture. When a complex enterprise problem spans across software code, regulatory privacy constraints, mathematical optimization, and distributed database layers, `moe-expert-omni-4b` synthesizes the outputs of specialized domain experts into a unified, coherent, and executable solution architecture.

---

## 🎯 Target Use Cases & Functional Scope

1. **Cross-Disciplinary System Integration:** Merges disparate outputs (e.g. security audits, SQL schemas, and Rust microservices) into a single cohesive architectural artifact.
2. **Multi-Turn Context Maintenance:** Preserves deep context across extended operational sessions (up to 256k tokens) without drift or instruction forgetfulness.
3. **Executive & Technical Dual-Perspective Reporting:** Formulates deliverables with clear executive summaries followed by rigorous technical implementation specifications.
4. **Inter-Agent Discrepancy Resolution:** Harmonizes terminology and interface contracts when multiple domain SLMs propose differing schema definitions.

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled Omni

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-omni-4b` (Distilled) |
| :--- | :--- | :--- |
| **Holistic Synthesis** | Generates disjointed paragraphs without clear unification | **Unified Systems Architecture** integrating code, governance, and infrastructure |
| **Context Retention** | Degrades over long multi-turn sessions (256k tokens) | **Structured State Tracking**; retains all session invariants across turns |
| **Clarity & Hierarchy** | Generic conversational rambling | **Crisp Engineering Hierarchy** (Executive, Architecture, Proofs, Action Items) |
| **Inter-Domain Consistency**| Allows conflicting assumptions between code and database schemas | **Strict Contract Reconciliation** across all architectural layers |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Meta-Llama-3.1-405B-Instruct + Nvidia Nemotron-70B ]                 |
|                       |                                                           |
|                       v  (Multi-Turn Coherence Checks + Cross-Domain Validation)  |
|  [ SFT Dataset: 35,500 Multi-Turn Holistic Synthesis Trajectories ]               |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189563`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 35,500 validated cross-domain synthesis trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0089`
- **Token Accuracy (Final):** **`99.80 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-omni-4b-Q4_K_M.gguf
PARAMETER num_ctx 262144
PARAMETER temperature 0.2
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

prompt = "<|im_start|>user\nIntegrate the outputs from the Coder, DataInfra, and Governance experts into an end-to-end sovereign deployment architecture.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.2)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_omni4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Omni Expert 4B: Cross-Domain Synthesis & Integration SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-omni-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
