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
- capability-externalization
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

## 📌 Executive Summary & Architectural Role

**`moe-sovereign-student-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Qwen3-Planner-35B** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the open-source **MoE Sovereign** compound AI system, this model operates exclusively as the **Meta-Orchestrator and AI Workflow Compiler**. It is explicitly **not intended to function as a general-purpose conversational chatbot**. Its designated role is to parse incoming natural-language requests, infer intent and operational constraints, and compile them into structured, typed Directed Acyclic Graphs (DAGs) that allocate tasks to specialized domain SLMs and deterministic precision tools.

---

## 🔬 Research Motivation: Capability Externalization

Monolithic Large Language Models (70B–400B+) attempt to internalize encyclopedic knowledge, mathematical calculation, formal verification, and workflow management within a single set of neural weights. MoE Sovereign investigates the research question:

> *"How much model capability can be externalized into specialized models, structured knowledge, deterministic tools, memory, and orchestration while maintaining useful task quality under real-world compute and sovereignty constraints?"*

In this paradigm, **`moe-sovereign-student-4b`** does not need to memorize vast factual databases or perform arithmetic internally. Instead, its neural capacity is dedicated to semantic comprehension, task decomposition, and correct utilization of controllable external infrastructure:

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
      [ Specialized 4B Experts ]  [ MCP Precision Tools ] [ Knowledge & Memory ]
      - Coder Expert 4B           - SMT / Z3 Solvers      - Neo4j GraphRAG
      - Precision Expert 4B       - Decimal Arithmetics   - Semantic Cache
      - Security Expert 4B        - Subnet Calculators    - Correction Memory
      - DataInfra Expert 4B       - Linter Contracts      - Vector DB
                 │                       │                       │
                 └───────────────────────┼───────────────────────┘
                                         │
                                         ▼
                             [ Sovereign Consensus Gate ]
                             (Multi-Agent Review / Judge)
```

---

## 🎯 Intended Functional Scope & Capabilities

1. **DAG Task Compilation:** Decomposes complex user prompts into discrete, machine-executable JSON task arrays with explicit dependency references (`depends_on`).
2. **Domain Expert Allocation:** Maps subtasks to appropriate specialized expert categories (`code_reviewer`, `precision_tools`, `graphrag`, `governance`, `security`, `datainfra`, `research`, `omni`).
3. **MCP Tool Parameterization:** Extracts and formats explicit parameters for registered Model Context Protocol (MCP) precision tools.
4. **Structured Output Discipline:** Trained to produce valid JSON task specifications directly, avoiding conversational preambles or postambles.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-sovereign-student-4b` (Distilled) |
| :--- | :--- | :--- |
| **Output Format** | Conversational responses wrapped in explanatory prose | **Direct JSON Task Array**; trained for structured schema adherence |
| **Task Granularity** | Prone to over-fragmentation or single unstructured blocks | **Bounded Task DAG** (typically 1–4 discrete, actionable subtasks) |
| **Tool Calling Schema** | High variance in parameter names; occasional format drift | **Targeted Parameter Extraction** aligned with registered MCP schemas |
| **Problem Solving Approach** | Attempts internal probabilistic generation for all domains | **Infrastructure Delegation**; routes calculation and retrieval to tools |

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

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X 128GB GPUs, Slurm Job `#21189557`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 35,000 verified planning and orchestration trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0094`, Training Token Accuracy `99.78%`

---

## 🖥️ Consumer Hardware Deployment

MoE Sovereign is designed for self-hosted execution where users run the system on **their own hardware**—ranging from consumer GPUs, workstations, and local clusters to cloud infrastructure. High-Performance Computing (LUMI-G) was utilized strictly during the temporary distillation phase to compress capability into compact SLMs, thereby reducing inference hardware requirements.

### Deployment Characteristics:
- **Local Host Execution:** Available in standard Hugging Face format and optimized GGUF quantizations (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Inference Runtime:** Supported in standard local runtimes (e.g. Ollama, `llama.cpp`, vLLM).
- **Target Profile:** Fits comfortably within entry-level 6 GB / 8 GB / 12 GB consumer GPUs (e.g. RTX 2060/3060, Apple Silicon unified memory).

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model and comparative compound configurations is in progress. The evaluation methodology is designed to isolate and measure three distinct sources of capability:

1. **Base Model Baseline:** Stock Qwen 3.5 4B (Direct Inference).
2. **Domain Specialization:** Distilled `moe-sovereign-student-4b` standalone.
3. **Compound AI System:** `moe-sovereign-student-4b` operating inside the MoE Sovereign runtime (with MCP tools, GraphRAG, and consensus gates).

> ℹ️ *Note: Training loss (`0.0094`) and training-token accuracy (`99.78%`) reported above describe optimization during training and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

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

system_prompt = (
    "You are a specialized planner model in a Mixture of Experts system. "
    "Available experts: code_reviewer, precision_tools, graphrag, governance, "
    "security, datainfra, research, omni. Produce an executable JSON task array."
)
user_prompt = "Build a lock-free ring buffer in Rust and verify its memory safety with an SMT solver."

prompt = f"<|im_start|>system\n{system_prompt}<|im_end|>\n<|im_start|>user\n{user_prompt}<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=256, temperature=0.0)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## ⚠️ Limitations

1. **Probabilistic Planning:** While trained for strict schema adherence, the model is probabilistic; runtime validation in MoE Sovereign (e.g. JSON schema validators) is required to guarantee structural correctness.
2. **Dynamic Tool Schema Sensitivity:** When newly defined MCP tools outside the training distribution are introduced, detailed tool signatures must be provided in the system context.
3. **Execution Plan Bounds:** The model is optimized for high-leverage decomposition into 1–4 subtasks; extremely deep workflows with dozens of nested steps require iterative re-planning via the orchestrator loop.
4. **Dependence on Downstream Components:** The quality of the final user outcome depends heavily on the availability and correctness of the allocated domain experts and tools.

---

## 📑 Citation & Reproducibility

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
