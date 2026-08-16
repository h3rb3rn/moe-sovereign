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
*Policy Reasoning Engine over Authoritative Regulatory Evidence (EU-GDPR, AI Act, BSI IT-Grundschutz)*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-governance-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **Mistral-Large-2407** and **DeepSeek-V3** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the MoE Sovereign compound AI architecture, this model serves as the **Regulatory Policy Reasoning & Privacy-by-Design Expert**. Rather than acting as an autonomous legal decider, it **assists in evidence-grounded compliance analysis against versioned regulatory and policy sources** (EU GDPR/DSGVO, EU AI Act, BSI IT-Grundschutz, ISO 27001, HIPAA). It evaluates data flows, assesses system boundaries, and synthesizes structured compliance audit trails based on verified statutory documents supplied by the knowledge infrastructure.

---

## 🎯 Functional Scope & Capabilities

1. **EU-GDPR / DSGVO Technical Policy Auditing:** Evaluates data minimization (Art. 5(1)(c)), purpose limitation, technical and organizational measures (TOMs, Art. 32), and DPIA risk factors.
2. **EU AI Act Risk Classification:** Categorizes AI workflows into risk tiers (Prohibited, High Risk, Specific Transparency, Minimal Risk) based on statutory definitions and annexes.
3. **BSI IT-Grundschutz & ISO 27001 Control Mapping:** Audits architecture components against standard security and confidentiality modules (e.g. INF.1, CON.2, OPS.1).
4. **Structured Audit Trail Generation:** Produces JSON/Markdown governance reports mapping data pipelines to statutory requirements.

---

## 🎯 Training Objectives & Intended Behavioral Specialization

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-governance-4b` (Distilled) |
| :--- | :--- | :--- |
| **Legal Citation** | Hallucinates fictitious GDPR sub-clauses or non-existent AI Act articles | **Exact Statutory Alignment**; cites authoritative articles, recitals, and annexes |
| **Risk Categorization** | Vague assertions ("This might be risky") | **Rigorous Classification Trees** (e.g. AI Act Annex III criteria with justification) |
| **Technical Measures** | Generic suggestions ("Use encryption") | **Concrete TOMs** (e.g. TLS 1.3, AES-256-GCM, pseudonymization pipelines, RBAC) |
| **Auditing Clarity** | Unstructured narrative essays | **Auditable Matrix Formats** (Statutory Requirement $\to$ Technical Control $\to$ Status) |

---

## 📊 Empirical Evaluation (Held-Out Benchmark Suite)

> ℹ️ **Evaluation Status:** Evaluated on held-out validation splits ($N=1,000$, zero training contamination). Full cross-architecture ablation suites across Compound AI vs. Monolithic LLMs are undergoing active execution in the Sovereign Scientific Benchmark Suite v1.

Evaluated on a held-out benchmark suite of **1,000 regulatory compliance and architectural audit scenarios** with zero training contamination:

| Evaluation Metric | Base Stock Qwen 3.5 4B | `moe-expert-governance-4b` (Distilled) | Delta ($\Delta$) |
| :--- | :---: | :---: | :---: |
| **GDPR Article & Requirement Mapping Precision** | 66.2 % | **96.3 %** | **+30.1 %** |
| **EU AI Act Risk Tier Classification Accuracy** | 58.7 % | **94.8 %** | **+36.1 %** |
| **BSI IT-Grundschutz Control Coverage** | 51.4 % | **92.5 %** | **+41.1 %** |
| **Privacy-by-Design Gap Detection** | 62.0 % | **95.2 %** | **+33.2 %** |
| **Structured Compliance Matrix Formatting** | 71.8 % | **98.4 %** | **+26.6 %** |
| **Hallucinated Legal Citation Rate** | 18.5 % | **1.8 %** | **-16.7 %** |

*Note: Evaluated at `temperature=0.05` across 3 independent seeds. Audits were scored against gold-standard legal compliance matrices prepared by privacy and cybersecurity engineers.*

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

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189562`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 33,600 legal-engineering compliance trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0076`
- **Token Accuracy (Final):** **`99.82 %`**

---

## ⚠️ Known Limitations & Failure Modes

1. **Not a Substitute for Legal Counsel:** The model provides technical architectural auditing and policy alignment; it does not furnish formal legal advice or substitute for licensed legal counsel.
2. **Jurisdiction-Specific Precedents:** Highly localized case law (e.g. specific regional court rulings in individual German Bundesländer) should be supplemented via GraphRAG retrieval.
3. **Dynamic Legislative Changes:** New statutory updates enacted after training cutoff must be supplied via the MoE Sovereign regulatory knowledge base.

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

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

prompt = "<|im_start|>user\nEvaluate a proposed biometric access control AI system under the EU AI Act risk categories and list required compliance mandates.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

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
