---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- sql-optimization
- postgresql
- duckdb
- clickhouse
- query-planning
- capability-externalization
- schema-migrations
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-datainfra-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🗄️ MoE Sovereign DataInfra Expert 4B (`moe-expert-datainfra-4b`)
*Database Query Optimization, `EXPLAIN ANALYZE` Diagnosis & Schema Migration Engineering*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-datainfra-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Qwen2.5-72B-Instruct** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the open-source **MoE Sovereign** compound AI architecture, this model operates as the **Data Engineering, Analytical SQL & Storage Engine Expert**. It is purpose-built for synthesizing complex SQL (PostgreSQL, DuckDB, ClickHouse), diagnosing execution bottlenecks from `EXPLAIN ANALYZE` outputs, recommending composite and covering indexes, and generating zero-downtime DDL schema migrations.

---

## 🔬 Research Motivation: Capability Externalization

Database engineering tasks present clear objective verification boundaries:

> **"The neural model analyzes relational algebra, suggests query rewrites, and designs index structures; external database engines deterministically validate syntax, measure execution buffers, and execute migration rollbacks."**

By coupling SLM reasoning with live query execution plan analysis in the MoE Sovereign runtime, database infrastructure tuning is grounded in measurable performance metrics rather than speculative language generation.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Analytical & Relational SQL Synthesis:** Writes complex CTEs, window functions (`LEAD`, `LAG`, `DENSE_RANK`), and aggregations across PostgreSQL, DuckDB, and ClickHouse dialects.
2. **Query Plan (`EXPLAIN ANALYZE`) Diagnosis:** Analyzes execution plans to detect sequential scans, hash spills, and nested loop bottlenecks, recommending optimal indexing strategies.
3. **Structured Schema Migrations:** Generates backward-compatible DDL migrations (e.g. concurrent index creation, phased constraint validation).
4. **Storage Engine Configuration:** Recommends memory allocation (`work_mem`, `shared_buffers`) and partitioning schemes based on query workloads.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-datainfra-4b` (Distilled) |
| :--- | :--- | :--- |
| **Index Recommendation** | Recommends generic single-column indexes without selectivity context | **Targeted Composite / Covering Indexes** (`INCLUDE`) tailored to query filters |
| **Schema Migration Safety**| Frequently suggests blocking DDL operations | **Zero-Downtime DDL Patterns** (`CONCURRENTLY`, non-blocking constraints) |
| **Dialect Nuances** | Conflates PostgreSQL, MySQL, and OLAP dialect idioms | **Dialect-Specific Idioms** (e.g. ClickHouse `ARRAY JOIN`, DuckDB Parquet scans) |
| **Execution Plan Analysis**| Vague narrative descriptions of slow queries | **Buffer-Aware Diagnosis** mapping cost estimates to physical page I/O |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-V3 + Qwen2.5-72B-Instruct ]                                 |
|                       |                                                           |
|                       v  (PostgreSQL/DuckDB Engine Execution + DDL Plan Check)    |
|  [ SFT Dataset: 33,200 Validated Database Engineering Trajectories ]              |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X 128GB GPUs, Slurm Job `#21189564`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 33,200 database engineering and query optimization trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0079`, Training Token Accuracy `99.82%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-datainfra-4b` is designed to be hosted locally alongside database servers on commodity workstations or edge hardware.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Operates with minimal memory overhead on consumer GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across multi-dialect SQL syntax validity, executable schema compliance, and measured execution cost reduction on standard database benchmarks (e.g. TPC-H query variations).

> ℹ️ *Note: Training loss (`0.0079`) and training-token accuracy (`99.82%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-datainfra-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-datainfra-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nOptimize this PostgreSQL query containing a nested subquery and recommend a covering index for table 'order_items'.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.05)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## ⚠️ Limitations

1. **Table Statistics Context:** Query plan optimization is based on standard relational heuristics; real-world database planner decisions depend on dynamic catalog statistics (`ANALYZE`).
2. **Proprietary Dialects:** The model focuses on open SQL standards (PostgreSQL, DuckDB, ClickHouse, SQLite); proprietary database engines are not explicitly optimized.
3. **Execution Safety:** Schema migrations and DDL statements should always be validated on staging environments before applying to production databases.

---

## 📑 Citation & Reproducibility

```bibtex
@misc{moe_sovereign_2026_datainfra4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign DataInfra Expert 4B: Query Optimization & Database Engineering SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-datainfra-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
