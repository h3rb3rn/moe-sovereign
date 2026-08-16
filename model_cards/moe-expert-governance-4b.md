---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- governance
- gdpr-compliance
- auditability
- policy-enforcement
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-governance-sft
pipeline_tag: text-generation
library_name: transformers
---

# ⚖️ MoE Sovereign Governance & Compliance Expert 4B (`moe-expert-governance-4b`)
*Regulatory Compliance, Privacy-by-Design Auditing & Policy Contract Enforcement*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-governance-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **Mistral-Large-2407 (123B)** and **Meta-Llama-3.1-405B-Instruct** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the dedicated **Regulatory Governance, Compliance & Privacy Expert** within the MoE Sovereign compound AI architecture. The model is specifically engineered to audit data pipelines for EU-GDPR, AI Act, BSI IT-Grundschutz, and HIPAA compliance, verify data sovereignty contracts, identify unauthorized PII cross-border data flows, and enforce strict zero-retention policies.

---

## 🎯 Target Use Cases & Functional Scope

1. **Privacy-by-Design Architectural Audits:** Evaluates software blueprints and infrastructure manifests against GDPR Art. 25/32 and technical privacy prerequisites.
2. **PII & Sensitive Data Flow Boundary Checking:** Pinpoints unmasked personal data, biometric markers, and credential exposure across system logs and API contracts.
3. **Audit Trail & Provenance Verification:** Verifies tamper-evident write-ahead logging (WAL), cryptographic hashing, and compliance documentation.
4. **EU AI Act Risk Categorization:** Classifies AI workflows into Prohibited, High-Risk, and Transparency-obligation tiers with actionable remediation steps.

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled Governance

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-governance-4b` (Distilled) |
| :--- | :--- | :--- |
| **Compliance Claims** | Makes vague, blanket claims of "full compliance" | **Precise, Evidence-Based Auditing**; cites technical prerequisites |
| **Legal Grounding** | General paraphrasing of privacy laws | **Exact Article & Paragraph Citations** (GDPR, EU AI Act, BSI C5) |
| **Data Boundary Enforcement**| Ignores cross-border egress or cloud metadata leaks | **Strict Sovereignty Enforcement**; flags all external API dependencies |
| **Remediation Plans** | Generic advice ("use encryption") | **Concrete Infrastructure Directives** (TLS 1.3, HSM key isolation, WAL) |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Mistral-Large-2407 (123B) + Meta-Llama-3.1-405B-Instruct ]          |
|                       |                                                           |
|                       v  (Regulatory Ground Truth + Policy Contract Verification) |
|  [ SFT Dataset: 31,800 High-Precision Governance & Compliance Trajectories ]       |
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
- **Dataset Size:** 31,800 validated compliance trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0060`
- **Token Accuracy (Final):** **`99.88 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-governance-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-governance-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nAudit the following cloud deployment manifest for compliance with GDPR Article 28 data processing agreements and BSI C5 requirements.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.1)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_governance4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Governance Expert 4B: Regulatory Compliance & Privacy Auditing SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-governance-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
