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
- evidence-synthesis
- citation-grounding
- literature-review
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-research-sft
pipeline_tag: text-generation
library_name: transformers
---

# 📚 MoE Sovereign Research Expert 4B (`moe-expert-research-4b`)
*Evidence-Grounded Literature Review, Technical Trade-Off Synthesis & Citation Verification*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-research-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **Moonshot Kimi-k3** and **Nvidia Nemotron-70B** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the MoE Sovereign compound AI architecture, this model serves as the **Evidence Synthesis, Literature Analysis, and Citation Verification Expert**. It is trained to perform comparative document analysis, extract empirical metrics from research benchmarks, evaluate engineering trade-offs, and produce structured analytical syntheses where every substantive claim is tightly bounded to retrieved context spans.

---

## 🎯 Functional Scope & Capabilities

1. **Evidence-Grounded Synthesis:** Compiles multi-document source texts into cohesive comparative analyses without introducing ungrounded external claims.
2. **Constrained Citation Generation:** Formulates references and factual attributions anchored exclusively to provided source spans (`[Doc ID: Section]`).
3. **Engineering Trade-Off Evaluation:** Structures multi-dimensional trade-off matrices (Latency vs. Throughput, Consistency vs. Availability, Memory vs. Compute).
4. **State-of-the-Art Surveying:** Summarizes architectural evolutions across computer science, distributed systems, and AI systems research.

---

## 📊 Empirical Evaluation (Held-Out Benchmark Suite)

Evaluated on a held-out benchmark suite of **1,000 multi-document research synthesis tasks** with zero training contamination, evaluated across factual entailment (NLI) and citation verification:

| Evaluation Metric | Base Stock Qwen 3.5 4B | `moe-expert-research-4b` (Distilled) | Delta ($\Delta$) |
| :--- | :---: | :---: | :---: |
| **Citation Precision (Valid Provenance)** | 62.1 % | **96.8 %** | **+34.7 %** |
| **Claim-Evidence Entailment (NLI Hold)** | 69.4 % | **95.1 %** | **+25.7 %** |
| **Hallucinated Fact Ratio** | 16.8 % | **2.4 %** | **-14.4 %** |
| **Multi-Source Trade-Off Completeness** | 58.0 % | **91.4 %** | **+33.4 %** |
| **Structured Matrix Formatting Fidelity** | 74.2 % | **98.0 %** | **+23.8 %** |
| **Long-Context Context Span Retrieval** | 63.5 % | **93.2 %** | **+29.7 %** |

*Note: Evaluated at `temperature=0.15` across 3 independent seeds. Citation precision measures the percentage of generated citations that accurately point to supporting evidence in the source text.*

---

## 🔬 Behavioral Comparison

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-research-4b` (Distilled) |
| :--- | :--- | :--- |
| **Citation Grounding** | Invents non-existent DOIs, authors, or paper titles | **Strict In-Context Provenance**; cites exact document tags and section anchors |
| **Trade-Off Analysis** | Generic pros/cons lists ("Fast but complex") | **Quantitative Multi-Axis Trade-Offs** (Latency, O(N) complexity, memory bounds) |
| **Information Extraction**| Omits key technical nuances and quantitative metrics | **Systematic Benchmark Extraction** (Dataset, Sample Size, CI, Hardware) |
| **Synthesis Coherence** | Disjointed paragraph dumps | **Hierarchical Technical Architecture** with clear structural transitions |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Moonshot Kimi-k3 + Nvidia Nemotron-70B ]                             |
|                       |                                                           |
|                       v  (NLI Entailment Filtering + Citation Verification)       |
|  [ SFT Dataset: 34,200 High-Assurance Synthesis & Research Trajectories ]         |
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
- **Dataset Size:** 34,200 verified literature and synthesis trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0072`
- **Token Accuracy (Final):** **`99.84 %`**

---

## ⚠️ Known Limitations & Failure Modes

1. **Closed-World Retrieval Constraint:** When zero retrieval context is provided, the model explicitly acknowledges lack of evidence rather than generating probabilistic general-knowledge claims.
2. **Conflicting Primary Sources:** When input documents present mutually contradictory empirical findings, the model highlights the contradiction for the Judge oracle rather than attempting unilateral arbitration.
3. **Context Length Budgeting:** For document corpora exceeding 64k tokens, iterative chunking via the MoE Sovereign compound pipeline is recommended for maximum extraction recall.

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

prompt = "<|im_start|>user\nSynthesize the technical trade-offs between Raft and Paxos based on the provided papers, with exact citation tags.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.15)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_research4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Research Expert 4B: Evidence-Grounded Synthesis SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-research-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
