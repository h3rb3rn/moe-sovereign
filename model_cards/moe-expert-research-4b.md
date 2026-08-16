---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- research
- academic-synthesis
- citation-grounding
- deep-investigation
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-research-sft
pipeline_tag: text-generation
library_name: transformers
---

# 📚 MoE Sovereign Research Expert 4B (`moe-expert-research-4b`)
*Deep Investigation, Academic Synthesis & Rigorous Citation Grounding*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-research-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **Moonshot Kimi-k3** and **Nvidia Nemotron-70B** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the dedicated **Deep Investigation & Academic Synthesis Expert** within the MoE Sovereign compound AI architecture. The model is specifically tuned for comprehensive literature reviews, multi-document cross-comparison, evidence contradiction detection, rigorous scientific citation formatting (BibTeX, DOI, IEEE), and grounded abstractive summarization.

---

## 🎯 Target Use Cases & Functional Scope

1. **Multi-Source Evidence Synthesis:** Compares dozens of technical whitepapers, patents, and scientific publications, extracting key methodology trade-offs without losing nuance.
2. **Contradiction & Discrepancy Detection:** Identifies conflicting benchmark claims, incompatible baseline assumptions, or methodology gaps across peer papers.
3. **Formal Citation & Provenance Anchoring:** Attaches verifiable bibliographic metadata, section references, and equation numbers to every analytical assertion.
4. **Structured Research Briefings:** Formulates structured state-of-the-art reports (Executive Overview, Methodology, Findings, Limitations, Open Questions).

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled Research

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-research-4b` (Distilled) |
| :--- | :--- | :--- |
| **Citation Integrity** | Hallucinates plausible-sounding authors and DOIs | **Zero Citation Hallucination**; strictly binds to verified evidence |
| **Comparative Depth** | Superficial bullet-point summaries | **Multi-Dimensional Matrix Comparison** (complexity, trade-offs, limits) |
| **Epistemic Modality** | Overconfident assertion of uncertain facts | **Calibrated Epistemic Modality** (clearly distinguishes proven vs. speculative) |
| **Synthesis Structure**| Unstructured flowing text | **Structured Scientific Briefs** with formal methodology sections |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Moonshot Kimi-k3 + Nvidia Nemotron-70B ]                             |
|                       |                                                           |
|                       v  (Citation Graph Verification + Peer Review Ground Truth) |
|  [ SFT Dataset: 33,400 Grounded Academic Research Trajectories ]                  |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189560`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 33,400 validated academic synthesis trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0072`
- **Token Accuracy (Final):** **`99.84 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-research-4b-Q4_K_M.gguf
PARAMETER num_ctx 262144
PARAMETER temperature 0.15
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

model_id = "h3rb3rn/moe-expert-research-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nSynthesize the architectural trade-offs between FlashAttention-3 and State Space Models (Mamba-2) for long-context retrieval.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.15)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_research4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Research Expert 4B: Deep Investigation & Academic Synthesis SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-research-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
