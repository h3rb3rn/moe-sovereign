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
- capability-externalization
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
*Evidence-Grounded Synthesis, Technical Trade-Off Analysis & Provenance Grounding*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-research-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **Moonshot Kimi-k3** and **Nvidia Nemotron-70B** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X GCDs (4× physical modules, 64GB HBM2e per GCD)).

Within the open-source **MoE Sovereign** compound AI architecture, this model operates as the **Evidence Synthesis, Literature Analysis & Provenance Grounding Expert**. It is trained to perform comparative document analysis, extract empirical metrics from research literature, evaluate engineering trade-offs, and produce structured analytical reviews where claims are linked to retrieved context spans.

---

## 🔬 Research Motivation: Capability Externalization

A central challenge in language modeling is hallucination during factual summarization:

> **"Rather than asking the neural weights to store the scientific literature, MoE Sovereign externalizes document storage into verified vector and document indexes; `moe-expert-research-4b` specializes in synthesizing provided evidence without extrapolating unverified claims."**

This enables research teams to maintain an auditable provenance chain from raw source documents to final synthesized reports.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Evidence-Grounded Synthesis:** Compiles multi-document source texts into structured comparative analyses constrained to supplied context.
2. **Provenance-Aware Attribution:** Formulates citations linked to specific source tags (`[Doc ID: Section]`) present in the input prompt.
3. **Engineering Trade-Off Evaluation:** Formulates structured trade-off matrices (e.g. latency vs. throughput, consistency vs. partition tolerance).
4. **Empirical Literature Surveying:** Extracts methodology, sample size ($N$), hardware environments, and confidence intervals from technical literature.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-research-4b` (Distilled) |
| :--- | :--- | :--- |
| **Citation Attribution** | Tendency to invent plausibly sounding authors, titles, or DOIs | **Strict In-Context Provenance**; limits citations to provided document tags |
| **Trade-Off Analysis** | Generic pros/cons lists without operational context | **Multi-Dimensional Technical Trade-Offs** (complexity bounds, resource overhead) |
| **Information Extraction**| Frequently glosses over experimental parameters and constraints | **Systematic Extraction** of benchmarks, datasets, and hardware baselines |
| **Synthesis Structure** | Disjointed narrative text blocks | **Hierarchical Technical Reviews** with structured comparative sections |

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

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X GCDs (4× physical modules, 64GB HBM2e per GCD), Slurm Job `#21189560`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 34,200 verified literature review and synthesis trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.04652`, Training Token Accuracy `99.83%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-research-4b` is intended for local execution on commodity hardware, enabling researchers to process internal documents privately without transmitting proprietary data to external APIs.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Runs efficiently on consumer GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across citation accuracy, claim-evidence entailment (NLI), and multi-document synthesis fidelity.

> ℹ️ *Note: Training loss (`0.04652`) and training-token accuracy (`99.83%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

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

prompt = "<|im_start|>user\nSynthesize the trade-offs between Raft and Paxos based on the provided papers, with exact citation tags.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.15)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## ⚠️ Limitations

1. **Retrieval Grounding Dependency:** When no source documents are provided in context, the model explicitly acknowledges the absence of evidence rather than generating unsubstantiated claims.
2. **Conflicting Primary Sources:** When input documents contain conflicting claims, the model highlights the divergence for upstream consensus arbitration rather than unilaterally deciding truth.
3. **Context Length Management:** For document sets exceeding context capacity, hierarchical retrieval and chunking via the MoE Sovereign compound pipeline is recommended.

---

## 📑 Citation & Reproducibility

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
