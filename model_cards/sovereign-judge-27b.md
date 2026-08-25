---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-27B
tags:
- compound-ai
- judge
- paraconsistent-consensus
- capability-externalization
- verification-oracle
- self-correction
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/judge-verification-sft
pipeline_tag: text-generation
library_name: transformers
---

# ⚖️ MoE Sovereign Judge 27B (`sovereign-judge-27b`)
*Paraconsistent Consensus Oracle, Self-Correction Gatekeeper & Formal Output Verifier*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 27B](https://img.shields.io/badge/Base_Model-Qwen3.5--27B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-27B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`sovereign-judge-27b`** is a high-capacity 27-billion parameter verification and evaluation model distilled from **Meta-Llama-3.1-405B-Instruct**, **Nvidia Nemotron-70B**, and **Z3 SMT Formal Proof Oracles** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X GCDs (4× physical modules, 64GB HBM2e per GCD)).

Within the open-source **MoE Sovereign** compound AI system, `sovereign-judge-27b` operates as the top-level **Quality Gatekeeper, Self-Correction Oracle, and Consensus Arbitrator**. When 4B domain SLMs generate candidate solutions or when multi-agent debates produce conflicting propositions, `sovereign-judge-27b` evaluates formal consistency, detects logical contradictions, checks regulatory alignment, and decides whether an output passes the consensus threshold or requires bounded self-correction.

---

## 🔬 Research Motivation: Capability Externalization

In high-assurance compound AI systems, quality control is separated from task execution:

> **"Instead of keeping a massive 405B model continuously active in the hot path of every user request, MoE Sovereign delegates execution to lightweight 4B domain specialists and routes results to `sovereign-judge-27b` only when multi-expert arbitration, contradiction resolution, or quality gating is required."**

This architectural gating ensures that large compute resources are invoked selectively, maintaining high throughput and energy efficiency for standard requests.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Consensus Arbitration:** Evaluates outputs from peer domain models, filtering out outliers and calculating calibrated consensus scores.
2. **Formal Self-Correction Triggering:** When an execution plan or code artifact fails validation gates, generates minimal, surgical correction directives for the Planner.
3. **Multi-Aspect Quality Scoring:** Evaluates candidate responses along 5 rigorous axes: Factual Grounding, Security Hardening, Syntactic Validity, Regulatory Compliance, and Efficiency.
4. **Correction Memory Ingestion:** Extracts detected failure patterns, abstracts the underlying anti-pattern, and formats new entries for persistent Correction Memory.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 27B | `sovereign-judge-27b` (Distilled) |
| :--- | :--- | :--- |
| **Evaluation Stance** | Lenient, sycophantic rating of AI outputs | **Strict Verification**; flags logic flaws, missing bounds, and hallucinations |
| **Consensus Handling** | Simple majority vote or naive averaging | **Contradiction-Filtering Logic:** Evaluates conflicting propositions without logic explosion |
| **Self-Correction** | Generates generic instructions to "try again" | **Surgical Failure Analysis:** Identifies specific violated invariants and actionable remediation |
| **Memory Extraction** | No automated memory abstraction | **Correction Memory Extraction:** Generalizes runtime errors into reusable patterns |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Meta-Llama-3.1-405B-Instruct + Nemotron-70B + Z3 SMT Oracle ]        |
|                       |                                                           |
|                       v  (Formal Ground Truth + Multi-Agent Debate Transcripts)   |
|  [ SFT Dataset: 40,000 High-Assurance Evaluation & Arbitration Trajectories ]     |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-27B BF16 Base ]                                               |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X GCDs (4× physical modules, 64GB HBM2e per GCD), Slurm Job `#21263413`)
- **Base Architecture:** Qwen3.5-27B in BF16
- **Dataset Scale:** 40,000 validated evaluation & arbitration trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 2 × 8 GPUs × Gradient Accumulation 8)
- **Learning Rate:** $1.0 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Precision:** Pure BF16 with DeepSpeed ZeRO-2

---

## 🖥️ Consumer Hardware Deployment

While 27B models are larger than 4B domain SLMs, `sovereign-judge-27b` is optimized for local execution via quantization. In typical multi-GPU workstation setups, the judge can be hosted on a dedicated workstation GPU (e.g. RTX 3090/4090 24GB or dual 12GB cards) or offloaded to CPU memory, as it is invoked only for gated arbitration rounds.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~16.5 GB, `Q8_0` ~29 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Operates within 24GB VRAM workstation GPUs or shared host RAM.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across multi-agent arbitration benchmarks, formal contradiction detection, and self-correction effectiveness.

> ℹ️ *Note: Full evaluation benchmarks will be published upon completion of training and quantization.*

---

## 💻 Quickstart Guide (Ollama & Python)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./sovereign-judge-27b-Q4_K_M.gguf
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

model_id = "h3rb3rn/sovereign-judge-27b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nArbitrate between these two conflicting expert responses regarding database isolation levels and identify the formal contradiction.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## ⚠️ Limitations

1. **Probabilistic Judgment:** While specialized for arbitration, the judge model remains a probabilistic model; formal assertions should be backed by deterministic tooling where possible.
2. **Compute Profile:** At 27B parameters, inference latency is higher than for 4B domain specialists; the orchestrator should invoke the judge conditionally based on task complexity.
3. **Domain Stalemate:** When multiple experts provide completely ungrounded or out-of-distribution proposals, the judge may reject all proposals and request complete re-planning.

---

## 📑 Citation & Reproducibility

```bibtex
@misc{moe_sovereign_2026_judge27b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Judge 27B: Paraconsistent Consensus & Output Verifier SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/sovereign-judge-27b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
