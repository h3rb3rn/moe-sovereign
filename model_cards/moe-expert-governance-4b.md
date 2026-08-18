---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- regulatory-compliance
- gdpr-dsgvo
- eu-ai-act
- capability-externalization
- bsi-it-grundschutz
- hipaa
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-governance-sft
pipeline_tag: text-generation
library_name: transformers
---

# ⚖️ MoE Sovereign Governance Expert 4B (`moe-expert-governance-4b`)
*Evidence-Grounded Policy Analysis, Privacy Engineering & Technical Control Mapping*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-governance-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **Mistral-Large-2407** and **DeepSeek-V3** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the open-source **MoE Sovereign** compound AI architecture, this model operates as the **Regulatory Policy Reasoning & Privacy Engineering Specialist**. It is designed to assist in evaluating data flows, architecture specifications, and data retention policies against technical controls derived from regulatory frameworks (EU GDPR/DSGVO, EU AI Act, BSI IT-Grundschutz, ISO 27001, HIPAA).

---

## 🔬 Research Motivation: Capability Externalization

In regulatory and compliance engineering, legal statutes are versioned and subject to administrative interpretation:

> **"The language model does not act as an autonomous legal authority; instead, it performs policy reasoning over authoritative regulatory texts and system documentation supplied by controlled knowledge infrastructure."**

This externalized approach ensures that compliance evaluations can be audited, cited, and updated whenever statutory guidelines or internal corporate policies evolve.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Evidence-Grounded Policy Analysis:** Evaluates data minimization (GDPR Art. 5), purpose limitation, and technical and organizational measures (TOMs, Art. 32) against system design documents.
2. **AI Act Technical Risk Categorization:** Assists in mapping AI workflows to statutory risk tiers (Prohibited, High Risk, Specific Transparency, Minimal Risk) based on statutory definitions.
3. **Control Mapping (BSI IT-Grundschutz / ISO 27001):** Maps architecture components to standard security modules (e.g. INF.1, CON.2, OPS.1).
4. **Audit Trail Documentation:** Synthesizes structured compliance matrices linking statutory requirements to technical controls.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-governance-4b` (Distilled) |
| :--- | :--- | :--- |
| **Statutory Alignment** | Prone to citing non-existent articles or generic legal concepts | **Authoritative Article Citation** grounded in provided statutory text |
| **Risk Categorization** | Qualitative impressions ("seems risky") | **Structured Criteria Trees** referencing statutory classification annexes |
| **Technical Measures** | High-level non-technical suggestions | **Concrete Technical Controls** (encryption standards, RBAC, access logging) |
| **Output Formats** | Narrative essays without audit structure | **Auditable Matrix Formats** (Requirement $\to$ Control $\to$ Assessment) |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Mistral-Large-2407 + DeepSeek-V3 ]                                   |
|                       |                                                           |
|                       v  (Legal Expert Filtering + Statutory Cross-Validation)    |
|  [ SFT Dataset: 33,600 Verified Regulatory Governance Trajectories ]               |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X 128GB GPUs, Slurm Job `#21189562`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 33,600 legal-engineering compliance trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0076`, Training Token Accuracy `99.82%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-governance-4b` is designed for on-premise deployment, ensuring that sensitive organizational architectures and compliance documents remain strictly within the sovereign enterprise environment.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Runs efficiently on consumer GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across statutory mapping precision, AI Act risk tier classification consistency, and control gap identification.

> ℹ️ *Note: Training loss (`0.0076`) and training-token accuracy (`99.82%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-governance-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-governance-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nEvaluate a proposed biometric access control AI system under the EU AI Act risk categories and list required technical controls.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## ⚠️ Limitations

1. **No Legal Advice:** The model provides technical architectural auditing and policy mapping; it does **not** provide legal advice, legal opinions, or authoritative compliance certification.
2. **Jurisdiction Nuances:** Regional case law and member-state specifics must be supplied via context documents.
3. **Statutory Cutoff:** Regulatory updates occurring after model training must be provided via the external knowledge base.

---

## 📑 Citation & Reproducibility

```bibtex
@misc{moe_sovereign_2026_governance4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Governance Expert 4B: Regulatory Policy Reasoning SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-governance-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
