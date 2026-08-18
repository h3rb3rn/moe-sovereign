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
- capability-externalization
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
*Vulnerability Classification, Secret Leakage Detection & STRIDE Threat Modeling*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-security-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Mistral-Large-2407** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the open-source **MoE Sovereign** compound AI architecture, this model operates as the **Static Security Analysis, Threat Surface Modeling & Hardening Expert**. It is trained for detecting credential and secret leaks, classifying Common Weakness Enumeration (CWE) patterns, formulating STRIDE threat models, and synthesizing production Linux security profiles (Seccomp, AppArmor, Kubernetes NetworkPolicies).

---

## 🔬 Research Motivation: Capability Externalization

In security engineering, high assurance requires combining semantic code analysis with deterministic scanners:

> **"The neural model identifies semantic vulnerabilities, architectural attack surfaces, and context-dependent secret patterns; deterministic external scanners and security tools validate token entropy, verify signatures, and enforce static security policies."**

This hybrid architecture provides defense-in-depth while keeping model execution private, local, and auditable.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Static Application Security Analysis (SAST):** Identifies common vulnerability patterns such as injection vectors (CWE-89, CWE-78), memory safety flaws, and broken access controls (CWE-862).
2. **Secret & Key Leakage Detection:** Trained for secret and credential leakage detection across configuration files, source trees, and deployment scripts.
3. **STRIDE Threat Modeling:** Formulates systematic threat vectors across architectural trust boundaries and microservice components.
4. **Hardening Manifest Synthesis:** Generates concrete Linux isolation policies (Seccomp filters, AppArmor profiles, CSP headers).

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-security-4b` (Distilled) |
| :--- | :--- | :--- |
| **Vulnerability Classification**| Generic warning labels without standard taxonomic alignment | **Standardized CWE Mapping** with explicit remediation guidance |
| **Secret Detection Focus** | Treats credentials as generic strings | **Targeted High-Entropy Scanning** for private keys and auth tokens |
| **Hardening Directives** | General high-level advice ("keep dependencies updated") | **Concrete Hardening Manifests** (Seccomp JSON, AppArmor syntax) |
| **Threat Modeling** | Ad-hoc lists of unstructured security concerns | **Structured STRIDE Matrix** mapped directly to system boundaries |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-V3 + Mistral-Large-2407 ]                                   |
|                       |                                                           |
|                       v  (CWE Benchmark Verification + Exploit Validation)        |
|  [ SFT Dataset: 32,800 High-Assurance Security Trajectories ]                     |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X 128GB GPUs, Slurm Job `#21189561`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 32,800 validated security audit and vulnerability trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0078`, Training Token Accuracy `99.83%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-security-4b` is designed for local deployment within sovereign security perimeters and internal code review pipelines.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Operates with minimal memory overhead on consumer GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across CWE-1000 classification accuracy, secret detection recall/precision on synthetic corpora, and false-positive rates on benign code.

> ℹ️ *Note: Training loss (`0.0078`) and training-token accuracy (`99.83%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

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

## ⚠️ Limitations

1. **Static Heuristics:** The model performs static heuristic analysis; dynamic runtime vulnerabilities require companion dynamic testing (fuzzing, DAST).
2. **Novel Exploit Vectors:** Highly novel zero-day exploitation techniques outside standard CWE categories should be audited by security professionals.
3. **No Certification Guarantee:** Outputs from the model do not constitute formal security certification or compliance attestation.

---

## 📑 Citation & Reproducibility

```bibtex
@misc{moe_sovereign_2026_security4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Security Expert 4B: Static Security Analysis & Threat Modeling SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-security-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
