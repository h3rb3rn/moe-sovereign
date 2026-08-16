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
- knowledge-graphs
- cypher
- vector-search
- gguf
- lumi-g
- moe-sovereign
datasets:
- moe-sovereign/expert-graphrag-sft
pipeline_tag: text-generation
library_name: transformers
---

# 🕸️ MoE Sovereign GraphRAG Expert 4B (`moe-expert-graphrag-4b`)
*Knowledge Graph Navigation, Entity Resolution & Multi-Hop Cypher Synthesis*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary

**`moe-expert-graphrag-4b`** is a domain-specialized 4-billion parameter Small Language Model (SLM) distilled from **Moonshot Kimi-k3 (2M Context)** and **Meta-Llama-3.1-405B-Instruct** on the **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

It functions as the dedicated **Knowledge Graph Retrieval & GraphRAG Navigation Expert** within the MoE Sovereign compound AI architecture. The model specializes in decomposing relational questions into efficient multi-hop Neo4j Cypher queries, resolving fuzzy entity identities, synthesizing dense vector hybrid retrieval filters, and extracting structured knowledge subgraphs.

---

## 🎯 Target Use Cases & Functional Scope

1. **Multi-Hop Cypher Query Synthesis:** Generates syntax-valid Neo4j Cypher queries with optimal indexing hints, traversal depth limits, and variable-length relationship matching.
2. **Entity & Relation Disambiguation:** Resolves ambiguous abbreviations, synonyms, and cross-document entities into canonical knowledge graph node URIs.
3. **Hybrid Vector & Graph Retrieval Fusion:** Combines semantic vector similarity thresholds with graph topology distances for high-recall factual extraction.
4. **Knowledge Subgraph Extraction & Triplet Formation:** Extracts strict `(Subject)-[:PREDICATE]->(Object)` triples with provenance citations from unstructured source text.

---

## 🔬 Behavioral Comparison: Stock Qwen 3.5 4B vs. Distilled GraphRAG

| Capability | Base Stock Qwen 3.5 4B | `moe-expert-graphrag-4b` (Distilled) |
| :--- | :--- | :--- |
| **Retrieval Strategy** | Unstructured keyword search or raw text guessing | **Graph Topology Traversal First**; exact Cypher query formulation |
| **Cypher Syntax** | Prone to obsolete Neo4j syntax and cartesian products | **Optimized, Index-Aware Cypher** with bounded path expansions |
| **Entity Resolution** | Confuses similar entities across large enterprise contexts | **Exact Canonical Matching** and contextual alias resolution |
| **Provenance Tracking**| Omits or invents source citations | **Strict Provenance Metadata** attached to every extracted triple |

---

## 🏋️ Training Setup & Distillation Methodology

```
+-----------------------------------------------------------------------------------+
|                            LUMI-G DISTILLATION PIPELINE                           |
|                                                                                   |
|  [ Teachers: Moonshot Kimi-k3 (2M Context) + Meta-Llama-3.1-405B-Instruct ]       |
|                       |                                                           |
|                       v  (Cypher Syntax Validation + Graph Execution Verification) |
|  [ SFT Dataset: 34,200 High-Precision GraphRAG Trajectories ]                     |
|                       |                                                           |
|                       v  (DeepSpeed ZeRO-2, ROCm 7.0, PyTorch 2.6, 8x MI250X)     |
|  [ Student: Qwen3.5-4B Hybrid Linear Attention + Mamba Base ]                     |
|                       |                                                           |
|                       v  (LoRA r=16, alpha=32, target_modules: q/k/v/o/gate/up/down)|
|  [ Output: final_adapter -> CPU-BF16 Merge -> GGUF Q4_K_M & Q8_0 ]                |
+-----------------------------------------------------------------------------------+
```

### Hyperparameters:
- **Compute Cluster:** LUMI-G (8× AMD Instinct MI250X 128GB GPUs, Slurm Job `#21190762`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Size:** 34,200 validated GraphRAG trajectories
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Configuration:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Training Loss (Final):** `0.0073`
- **Token Accuracy (Final):** **`99.84 %`**

---

## 💻 Quickstart Guide (Ollama & Llama.cpp)

### 1. Ollama `Modelfile`
```dockerfile
FROM ./moe-expert-graphrag-4b-Q4_K_M.gguf
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

model_id = "h3rb3rn/moe-expert-graphrag-4b"

tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
model = AutoModelForCausalLM.from_pretrained(
    model_id,
    torch_dtype=torch.bfloat16,
    device_map="auto",
    trust_remote_code=True
)

prompt = "<|im_start|>user\nFormulate a Neo4j Cypher query to retrieve all microservice components affected by a security CVE within 3 hops of dependency.<|im_end|>\n<|im_start|>assistant\n"
inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
outputs = model.generate(**inputs, max_new_tokens=512, temperature=0.1)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

---

## 📑 Citation

```bibtex
@misc{moe_sovereign_2026_graphrag4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign GraphRAG Expert 4B: Knowledge Graph & Cypher Retrieval SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-graphrag-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
