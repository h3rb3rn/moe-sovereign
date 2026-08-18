---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- code-generation
- ast-refactoring
- capability-externalization
- rust
- cpp
- python
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-coder-sft
pipeline_tag: text-generation
library_name: transformers
---

# 💻 MoE Sovereign Coder Expert 4B (`moe-expert-coder-4b`)
*Systems Code Synthesis, AST Refactoring & Tool-Assisted Interface Implementation*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-coder-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-Coder-V2 (236B)** and **DeepSeek-V3** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the open-source **MoE Sovereign** compound AI system, this model operates as the **Systems Programming, Code Synthesis & Refactoring Expert**. Rather than acting as a general-purpose conversational assistant, it is specialized for generating typed systems code, atomic unified diffs, and AST-compliant implementations in languages such as Rust, C++, Python, and Go.

---

## 🔬 Research Motivation: Capability Externalization

In the MoE Sovereign architecture, code quality is achieved through the interaction of specialized generation and deterministic verification:

> **"The language model proposes code and refactoring patches; external deterministic tooling (AST parsers, linters, compilers, and unit tests) validates and enforces correctness invariants."**

This architectural separation enables a compact 4B model to achieve high developer utility by offloading syntax validation and static checks to native compilers rather than relying purely on internal parameter memory.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Systems Code Synthesis:** Implements concurrent data structures, explicit memory orderings (`Acquire`/`Release`), SIMD vectorization, and OS-level primitives.
2. **Static-Analysis-Aware Generation:** Trained to generate code compatible with strict static-analysis and linting pipelines (e.g. `rustc --deny warnings`, `clang-tidy`, `ruff`, `mypy --strict`).
3. **Atomic Unified Diff Generation:** Outputs structured patch hunks designed for direct headless application by developer toolchains.
4. **Focused Technical Implementation:** Focuses on typed signatures, implementation logic, and regression tests with minimal conversational filler.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-coder-4b` (Distilled) |
| :--- | :--- | :--- |
| **Output Style** | Conversational explanations surrounding code blocks | **Direct Code & Atomic Diffs** with concise inline type annotations |
| **Concurrency Primitives**| Frequently defaults to unconstrained or relaxed primitives | **Explicit Atomic Orderings** (`AcqRel`, `SeqCst`) with synchronization notes |
| **Patch Generation** | Often suggests entire file rewrites with approximate line ranges | **Structured Unified Diffs** targeting specific modified AST blocks |
| **Type Discipline** | Occasionally omits strict generic bounds or lifetime markers | **Explicit Type Signatures** (Rust lifetimes, C++20 concepts, Python TypeVars) |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-Coder-V2 (236B) + DeepSeek-V3 ]                             |
|                       |                                                           |
|                       v  (AST Parse Validation + Compiler Linter Filtering)      |
|  [ SFT Dataset: 32,500 AST-Verified Coding Trajectories ]                         |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X 128GB GPUs, Slurm Job `#21189558`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 32,500 curated, compiler-filtered programming trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0106`, Training Token Accuracy `99.62%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-coder-4b` is intended for local execution on user-owned hardware. High-performance compute on LUMI-G was utilized strictly during the offline distillation phase to compress capabilities from 200B+ teachers into a lightweight 4B model.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Operates within entry-level GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress. The evaluation protocol measures:
- First-pass syntax validity across Rust, C++, Python, and Go
- AST parse rates on generated patches
- Static analysis pass rates against strict linter rulesets
- Functional correctness on isolated unit test suites

> ℹ️ *Note: Training loss (`0.0106`) and training-token accuracy (`99.62%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-coder-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-coder-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nImplement a lock-free MPSC ring buffer in Rust using AtomicUsize and explicit memory ordering.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## ⚠️ Limitations

1. **Probabilistic Generation:** The model does not inherently guarantee compiler pass or runtime safety; code outputs should always be validated through the compiler and test suites.
2. **Deep Macro Metaprogramming:** Highly complex macro expansions (e.g. extensive procedural macros in Rust or complex C++ template metaprogramming) may require human inspection.
3. **Target Architecture Nuances:** Exotic embedded architectures or custom assembly instructions may not be fully covered in the training distribution.
4. **Context Chunking:** For large multi-file refactorings spanning tens of thousands of lines, the model performs best when orchestrated with targeted AST sub-chunks.

---

## 📑 Citation & Reproducibility

```bibtex
@misc{moe_sovereign_2026_coder4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Coder Expert 4B: Systems Code Synthesis SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-coder-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
