---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- meta-orchestrator
- ai-workflow-compiler
- task-planning
- dag-generation
- mcp-tools
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/planner-orchestration-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🧠 MoE Sovereign Student 4B (`moe-sovereign-student-4b`)
*Meta-Orchestrator & AI Workflow Compiler for Compound AI Systems*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Core Architectural Hypothesis

**`moe-sovereign-student-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Qwen3-Planner-35B** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

### The Sovereign Hypothesis: Small Models Directing Deterministic Infrastructure
Traditional monolithic AI paradigms force massive 70B–400B models to act simultaneously as memory repositories, domain calculators, code linters, and planners. MoE Sovereign inverts this paradigm:

> **"The small model does not need to know everything. Its sole responsibility is to operate the compound infrastructure correctly."**

`moe-sovereign-student-4b` is not a generic chatbot. It functions exclusively as a **Meta-Orchestrator & Workflow Compiler**: decomposing natural language user requests into executable, typed Direct Acyclic Graphs (DAGs), selecting specialized 4B domain experts, parameterizing Model Context Protocol (MCP) precision tools, and steering knowledge graph traversal.

```
                                  [ User Request ]
                                         │
                                         ▼
                     ┌───────────────────────────────────────┐
                     │     moe-sovereign-student-4b (SLM)    │
                     │       (Meta-Orchestrator Compiler)    │
                     └───────────────────┬───────────────────┘
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 ▼                       ▼                       ▼
      [ 8x Specialized 4B ]     [ 65x MCP Precision ]   [ GraphRAG / Memory ]
      - Coder Expert 4B         - SMT / Z3 Solvers      - Neo4j Knowledge Graph
      - Precision Expert 4B     - Decimal Arithmetics   - Semantic Episodic Cache
      - Security Expert 4B      - Subnet Calculators    - Correction Memory
      - DataInfra Expert 4B     - Linting Contracts     - Vector DB
                 │                       │                       │
                 └───────────────────────┼───────────────────────┘
                                         │
                                         ▼
                             [ Sovereign Judge 35B / 27B ]
                             (Belnap-Dunn Consensus Gate)
```

---

## 🎯 Functional Scope & Capabilities

1. **Deterministic DAG Task Compilation:** Compiles user requests into structured JSON task arrays with explicit dependencies (`depends_on`), priority weights, and execution contracts.
2. **Domain Expert Allocation:** Routes subtasks to specialized 4B domain experts (`code_reviewer`, `precision_tools`, `graphrag`, `governance`, `security`, `datainfra`, `research`, `omni`).
3. **MCP Tool Parameterization:** Extracts precision arguments for 65+ deterministic MCP tools (e.g. `subnet_calc`, `decimal_finance`, `ast_grep`, `z3_solve`).
4. **Autonomous Schema Conformance:** Trained with strict JSON Schema invariants, ensuring zero markdown noise outside the task array.

---

## 🎯 Training Objectives & Intended Behavioral Specialization

| Capability | Base Stock Qwen 3.5 4B | `moe-sovereign-student-4b` (Distilled) |
| :--- | :--- | :--- |
| **Output Discipline** | Generates conversational preambles and explanations around JSON | **Pure JSON Task Array**; zero preamble, zero postamble markdown |
| **Task Granularity** | Over-plans into 10+ vague tasks or under-plans into 1 generic prompt | **Optimal Bounded DAG** (1–4 discrete, machine-executable subtasks) |
| **Tool Parameterization**| Invents non-existent parameters or misses required schemas | **Exact MCP Schema Conformance** matching registered tool signatures |
| **Epistemic Modesty** | Attempts to answer complex math/code directly with hallucinations | **Delegates to Specialized Experts** and deterministic precision tools |

---

## 📊 Empirical Evaluation (Held-Out Benchmark Suite)

> ℹ️ **Evaluation Status:** Evaluated on held-out validation splits ($N=1,000$, zero training contamination). Full cross-architecture ablation suites across Compound AI vs. Monolithic LLMs are undergoing active execution in the Sovereign Scientific Benchmark Suite v1.

Evaluated on a held-out benchmark suite of **1,000 multi-step planning and orchestration tasks** across multidisciplinary engineering problems with zero training contamination:

| Evaluation Metric | Base Stock Qwen 3.5 4B | `moe-sovereign-student-4b` (Distilled) | Delta ($\Delta$) |
| :--- | :---: | :---: | :---: |
| **Strict JSON Schema Conformance Rate** | 68.3 % | **99.7 %** | **+31.4 %** |
| **Executable Task DAG Validity** | 61.5 % | **97.8 %** | **+36.3 %** |
| **Domain Expert Routing Precision** | 59.2 % | **96.4 %** | **+37.2 %** |
| **MCP Tool Contract Parameterization F1** | 53.0 % | **95.1 %** | **+42.1 %** |
| **Over-Planning / Hallucinated Step Ratio** | 21.4 % | **1.8 %** | **-19.6 %** |
| **Mean Planning Latency (TTFT)** | 1,420 ms | **185 ms** | **-87.0 %** |

*Note: Evaluated at `temperature=0.0` across 3 independent seeds. Latency measured on single RTX 3060 (12GB) with batch size 1.*

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-V3 + Qwen3-Planner-35B ]                                    |
|                       |                                                           |
|                       v  (JSON Schema Invariant Verification + DAG Linter Check)  |
|  [ SFT Dataset: 35,000 Validated Orchestration & Routing Trajectories ]           |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189557`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 35,000 verified planning trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0094`
- **Token Accuracy (Final):** **`99.78 %`**

---

## ⚠️ Known Limitations & Failure Modes

1. **Not a Direct Content Producer:** The model is not trained to write long-form essays or full code repos directly; its outputs are execution plans for downstream experts.
2. **Dynamic Tool Discovery:** When custom MCP tools not present in the training distribution are introduced, detailed JSON schemas must be injected via the system prompt.
3. **Recursive Re-Planning:** For multi-turn iterative plan repairs, the orchestrator should feed execution logs back into the model's context window.

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-sovereign-student-4b-Q4_K_M.gguf
PARAMETER num_ctx 262144
PARAMETER temperature 0.0
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

model_id = "h3rb3rn/moe-sovereign-student-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>system\nYou are a specialized planner model in a Mixture of Experts system. Available experts: code_reviewer, precision_tools, graphrag, governance, security, datainfra, research, omni. Produce an executable JSON task array.<|im_end|>\n<|im_start|>user\nBuild a lock-free ring buffer in Rust and verify its memory safety with an SMT solver.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=256, temperature=0.0)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_student4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Student 4B: Meta-Orchestrator & AI Workflow Compiler SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-sovereign-student-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
