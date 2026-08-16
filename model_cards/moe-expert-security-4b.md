---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- cybersecurity
- vulnerability-detection
- secret-scanning
- threat-modeling
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-security-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🛡️ MoE Sovereign Security Expert 4B (`moe-expert-security-4b`)
*Vulnerability Detection, Threat Modeling, Secret Sanitization & Binary Hardening*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-security-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Mistral-Large-2407** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the dedicated **Cybersecurity, Vulnerability Analysis & Hardening Expert** within the MoE Sovereign compound AI architecture. The model is specifically tuned to detect Common Weakness Enumerations (CWEs), analyze attack surfaces via STRIDE threat modeling, detect embedded credentials or cryptographic flaws, and generate verifiable security patches.

---

## 🎯 Target Use Cases & Functional Scope

1. **Static Application Security Testing (SAST):** Scans source code repositories for buffer overflows, memory safety violations, SQL injection, and SSRF flaws.
2. **Secret & Key Leakage Sanitization:** Accurately identifies high-entropy API tokens, private certificates, and environment secret leaks with zero false negatives.
3. **STRIDE Threat Modeling:** Formulates systematic threat vectors across complex microservice architectures and CI/CD pipelines.
4. **Hardening & Remediation Synthesis:** Generates concrete infrastructure hardening manifests (AppArmor profiles, Seccomp filters, NetworkPolicies).

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled Security

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-security-4b` (Distilled) |
| :--- | :--- | :--- |
| **Vulnerability Precision** | High rate of false positives on benign code patterns | **Deterministic CWE Classification** with verifiable exploitation paths |
| **Secret Detection** | Misses obfuscated or fragmented credentials | **High-Entropy Token & Key Detection** with regex validation |
| **Hardening Directives**| Generic recommendations ("use HTTPS") | **Production-Grade Hardening Manifests** (Seccomp, SELinux, CSP) |
| **Threat Modeling** | Ad-hoc lists of general security risks | **Structured STRIDE Matrix** mapped directly to trust boundaries |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-V3 + Mistral-Large-2407 ]                                   |
|                       |                                                           |
|                       v  (CWE Benchmark Verification + CVE Exploit Validation)    |
|  [ SFT Dataset: 32,800 High-Assurance Security Trajectories ]                     |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189561`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 32,800 validated security audit trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0078`
- **Token Accuracy (Final):** **`99.83 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-security-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-security-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nAnalyze this C++ memory buffer management snippet for potential CWE-122 heap-based buffer overflow vulnerabilities.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_security4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Security Expert 4B: Cybersecurity & Threat Modeling SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-security-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
