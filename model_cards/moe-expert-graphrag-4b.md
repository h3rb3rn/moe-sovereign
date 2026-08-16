---
language:
- en
- de
license: apache-2.0
base_model: Qwen/Qwen3.5-4B
tags:
- compound-ai
- domain-expert
- graphrag
- neo4j
- cypher
- knowledge-graph
- entity-resolution
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-graphrag-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🕸️ MoE Sovereign GraphRAG Expert 4B (`moe-expert-graphrag-4b`)
*Multi-Hop Knowledge Graph Traversal, Cypher Query Generation & Entity Resolution*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-graphrag-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Qwen2.5-72B-Instruct** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the MoE Sovereign compound AI architecture, this model serves as the **Knowledge Graph Navigation & GraphRAG Retrieval Specialist**. It does not attempt to memorize static enterprise facts within its weights. Instead, it is trained on the operational mechanics of structured knowledge retrieval: compiling natural language questions into multi-hop Cypher queries, resolving fuzzy entity identifiers, performing hybrid vector-graph fusion, and extracting semantic triples `(Subject, Predicate, Object)` from unstructured documents.

---

## 🎯 Functional Scope & Capabilities

1. **Multi-Hop Cypher Query Generation:** Formulates syntactically valid openCypher / Neo4j 5.x graph queries (`MATCH`, `WHERE`, `OPTIONAL MATCH`, `WITH`, `RETURN`).
2. **Entity Resolution & Linking:** Maps ambiguous colloquial mentions in user prompts to canonical entity nodes in the enterprise knowledge graph.
3. **Structured Triplet Extraction:** Deconstructs unstructured text into validated knowledge graph triples with provenance metadata.
4. **Vector + Graph Hybrid Fusion:** Synthesizes dense semantic embeddings with explicit relational graph topologies.

---

## 🎯 Training Objectives & Intended Behavioral Specialization

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-graphrag-4b` (Distilled) |
| :--- | :--- | :--- |
| **Cypher Syntax** | Uses outdated syntax, invalid aggregations, or missing variable projections | **Modern Neo4j 5.x Cypher** with parameterized inputs and efficient index hints |
| **Entity Resolution** | Hallucinates plausible but non-existent entity IDs | **Strict Grounding in Schema**; applies fuzzy matching with distance thresholds |
| **Multi-Hop Traversal**| Struggles beyond 1-hop relationships; gets stuck in recursive loops | **Precise Path Traversal** (`(a)-[:REL*1..3]->(b)`) with bounded depth |
| **Graph Triples** | Produces arbitrary natural language labels without ontology bounds | **Ontology-Constrained Triples** mapped directly to domain schema nodes |

---

## 📊 Empirical Evaluation (Held-Out Benchmark Suite)

> ℹ️ **Evaluation Status:** Evaluated on held-out validation splits ($N=1,000$, zero training contamination). Full cross-architecture ablation suites across Compound AI vs. Monolithic LLMs are undergoing active execution in the Sovereign Scientific Benchmark Suite v1.

Evaluated on a held-out benchmark suite of **1,000 graph retrieval and Cypher generation tasks** verified against a live Neo4j 5.25 graph instance with zero training contamination:

| Evaluation Metric | Base Stock Qwen 3.5 4B | `moe-expert-graphrag-4b` (Distilled) | Delta ($\Delta$) |
| :--- | :---: | :---: | :---: |
| **Valid Cypher Syntax Rate** | 71.2 % | **98.4 %** | **+27.2 %** |
| **Executable Cypher (Schema-Compliant)** | 64.7 % | **95.1 %** | **+30.4 %** |
| **Correct Graph Answer Extraction** | 58.1 % | **91.8 %** | **+33.7 %** |
| **Hallucinated Entity Identifier Rate** | 12.4 % | **1.9 %** | **-10.5 %** |
| **Multi-Hop Traversal Correctness ($\ge 3$ hops)** | 46.5 % | **88.6 %** | **+42.1 %** |
| **Triplet Extraction F1 Score** | 63.8 % | **94.2 %** | **+30.4 %** |

*Note: Evaluated at `temperature=0.0` across 3 independent seeds. Executable queries were executed directly against a multi-tenant enterprise ontology graph.*

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: DeepSeek-V3 + Qwen2.5-72B-Instruct ]                                 |
|                       |                                                           |
|                       v  (Neo4j Cypher Execution Validation + Triplet F1 Filter)  |
|  [ SFT Dataset: 33,000 Validated Cypher & GraphRAG Trajectories ]                 |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21189563`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 33,000 verified Cypher and graph-traversal trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0083`
- **Token Accuracy (Final):** **`99.81 %`**

---

## ⚠️ Known Limitations & Failure Modes

1. **Schema Visibility Requirement:** The model relies on the active schema/ontology definition being supplied in the context; without schema hints, complex domain-specific relationship types cannot be deduced.
2. **Unbounded Graph Cartesian Products:** While the model is trained to avoid cartesian products (`MATCH (a), (b)` without predicates), complex graph aggregations should be safeguarded by DB query timeouts.
3. **Graph Topology Size:** Queries returning more than 10,000 nodes are best streamed through cursor pagination rather than loaded in a single context window.

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-graphrag-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-graphrag-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nGenerate a parameterized Neo4j Cypher query to find all microservices that depend on Kafka cluster 'kafka-prod-01' up to 3 hops.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.0)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_graphrag4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign GraphRAG Expert 4B: Knowledge Graph Traversal & Cypher SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-graphrag-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
