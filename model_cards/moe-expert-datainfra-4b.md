---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- database-architecture
- cypher
- sql-optimization
- index-tuning
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-datainfra-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🗄️ MoE Sovereign DataInfra Expert 4B (`moe-expert-datainfra-4b`)
*Database Architecture, SQL/Cypher Query Optimization, Sharding & Index Tuning*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-datainfra-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-Coder-V2 (236B)** and **Qwen2.5-72B** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the dedicated **Database Architecture, Data Infrastructure & High-Throughput Query Optimization Expert** within the MoE Sovereign compound AI architecture. The model specializes in crafting performant PostgreSQL, DuckDB, ClickHouse, and Neo4j queries, designing normalized/denormalized schemas, diagnosing query execution plans (`EXPLAIN ANALYZE`), and configuring distributed sharding strategies.

---

## 🎯 Target Use Cases & Functional Scope

1. **High-Throughput SQL & Cypher Synthesis:** Writes complex window functions, CTEs, lateral joins, and graph traversal queries tuned for low-latency execution.
2. **Query Plan Diagnosis (`EXPLAIN ANALYZE`):** Interprets database execution trees, identifying sequential scans, bad join algorithms, and missing composite indices.
3. **Storage Engine & Index Architecture:** Recommends optimal storage structures (B-Tree, BRIN, GIN, HNSW vector indices, LSM trees) for specialized workload profiles.
4. **Data Pipeline & Schema Migration:** Synthesizes backward-compatible Liquibase/Flyway migrations and transactional ETL pipelines.

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled DataInfra

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-datainfra-4b` (Distilled) |
| :--- | :--- | :--- |
| **SQL Performance** | Writes naive queries prone to table-scans and N+1 bottlenecks | **Index-Aware Query Optimization** using CTEs, partition pruning, and batching |
| **Execution Plan Analysis**| Vaguely summarizes query plan text | **Pinpoints Costly Operators** (HashJoin vs NestedLoop, disk spills, buffer hits) |
| **Vector DB Configuration**| Generic vector distance formulas | **Exact HNSW / IVF Parameter Tuning** (`efSearch`, `m`, distance metrics) |
| **Schema Integrity** | Omits foreign key cascades or locking constraints | **Strict Transactional Invariants** (ACID, isolation levels, row-level locks) |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-Coder-V2 (236B) + Qwen2.5-72B ]                             |
|                       |                                                           |
|                       v  (SQL Query Plan Optimization + Schema Validation Checks) |
|  [ SFT Dataset: 33,100 Production Database & Infrastructure Trajectories ]        |
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
- **Dataset Size:** 33,100 validated data infrastructure trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0077`
- **Token Accuracy (Final):** **`99.84 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-datainfra-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-datainfra-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nOptimize this PostgreSQL multi-table join with EXPLAIN output showing sequential scan on 50M rows.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.1)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_datainfra4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign DataInfra Expert 4B: Database Architecture & Query Optimization SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-datainfra-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
