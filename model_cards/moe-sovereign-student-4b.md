---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- meta-orchestrator
- workflow-compiler
- graphrag
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/planner-dag-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🛡️ MoE Sovereign Student 4B (`moe-sovereign-student-4b`)
*A Distilled, Sovereign Meta-Orchestrator & AI Workflow Compiler*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-sovereign-student-4b`** is a specialized, high-efficiency small language model (SLM) distilled from high-capacity frontier teacher models (**Meta-Llama-3.1-405B-Instruct** and **GLM-5.2**) on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Rather than acting as a generic conversational chatbot, `moe-sovereign-student-4b` serves as the central **Meta-Orchestrator and AI Workflow Compiler** within the MoE Sovereign Compound AI architecture. It translates ambiguous natural language requests into deterministic, auditable Directed Acyclic Graphs (DAGs), dynamically allocates tasks to specialized domain SLMs, integrates GraphRAG and Correction Memory, and applies paraconsistent consensus verification.

---

## 🎯 Target Use Cases & Functional Scope

Designed for low-latency local deployment (edge servers, single RTX 4090/3090 GPUs, Apple Silicon), `moe-sovereign-student-4b` executes four primary orchestration duties:

1. **Deterministic DAG Workflow Compilation:** Generates structured JSON/YAML task graphs with explicit step dependencies, model allocation, tool contracts, and quality verification gates.
2. **Kahn Topological Scheduling:** Formulates execution schedules with strict lexicographical tie-breaking for parallel agent coordination.
3. **GraphRAG & Knowledge Retrieval Routing:** Synthesizes structured Cypher queries (Neo4j) and vector retrieval parameters (ChromaDB) prior to downstream task dispatch.
4. **Correction Memory & Self-Correction Loops:** Evaluates validation feedback, queries persistent error pattern databases, and triggers targeted re-planning.

---

## 🔬 Behavioral Comparison: Base Qwen 3.5 4B vs. Distilled Planner

| Capability | Base Stock Qwen 3.5 4B | `moe-sovereign-student-4b` (Distilled) |
| :--- | :--- | :--- |
| **Primary Identity** | Unstructured conversational text assistant | **Sovereign Meta-Orchestrator & Workflow Compiler** |
| **Planning Paradigm** | Monolithic text output; susceptible to step skips | **Deterministic Kahn DAG Generation** with explicit stage barriers |
| **Tool Calling (MCP)** | Generic function calling schema | **Strict MCP Tool Contract Generation** with bounded schemas |
| **Knowledge Retrieval** | Relies on internal parametric weights (hallucinations) | **GraphRAG & Vector Retrieval First**; no ungrounded speculation |
| **Fault Tolerance** | Repeats failed prompts in closed loops | **Correction Memory Integration:** Queries anti-pattern database |
| **Consensus Thresholds**| Unweighted majority voting | **Paraconsistent 66% Consensus Verification** |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Meta-Llama-3.1-405B-Instruct + GLM-5.2 ]                             |
|                       |                                                           |
|                       v  (AST Validation + Z3 SMT Ground Truth + 15% Error Memory)|
|  [ SFT Dataset: 37,875 Verified Planning & Routing Trajectories ]                 |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189555`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 37,875 validated planning trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0094`
- **Token Accuracy (Final):** **`99.68 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-sovereign-student-4b-Q4_K_M.gguf
PARAMETER num_ctx 262144
PARAMETER temperature 0.1
TEMPLATE """{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ .Response }}<|im_end|>"""
```

### 2. Python Inference with HuggingFace `transformers`
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

prompt = "<|im_start|>user\nCompile an executable DAG workflow to audit an untrusted microservice container.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.1)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📜 Ethical Considerations & Limitations

- **Specialization:** `moe-sovereign-student-4b` is rigorously trained for orchestration, workflow DAG generation, and tool dispatch. It is not intended for creative prose or unconstrained dialogue.
- **System Integration:** For production operations, the model should be paired with the MoE Sovereign middleware (Neo4j GraphRAG, ChromaDB, and MCP server runners).

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_planner4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign Student 4B: Distilled Meta-Orchestrator for Sovereign Compound AI Systems},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-sovereign-student-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
