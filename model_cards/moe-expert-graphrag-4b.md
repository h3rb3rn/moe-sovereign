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
- capability-externalization
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
*Knowledge Graph Retrieval, Multi-Hop Cypher Generation & Entity Resolution*

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Base Model: Qwen 3.5 4B Hybrid Mamba](https://img.shields.io/badge/Base_Model-Qwen3.5--4B-violet.svg)](https://huggingface.co/Qwen/Qwen3.5-4B)
[![Trained on: LUMI-G Supercomputer](https://img.shields.io/badge/Trained_on-LUMI--G_MI250X-green.svg)](https://www.lumi-supercomputer.eu/)

---

## 📌 Executive Summary & Architectural Role

**`moe-expert-graphrag-4b`** is a specialized 4-billion parameter Small Language Model (SLM) distilled from **DeepSeek-V3** and **Qwen2.5-72B-Instruct** on the EuroHPC **LUMI-G Supercomputer** (8× AMD Instinct™ MI250X 128GB GPUs).

Within the open-source **MoE Sovereign** compound AI system, this model operates as the **Knowledge Graph Navigation & GraphRAG Retrieval Specialist**. It is designed to navigate enterprise ontologies, translate complex questions into parameterized Cypher queries, resolve ambiguous entity mentions, and extract structured semantic triples `(Subject, Predicate, Object)` from unstructured text.

---

## 🔬 Research Motivation: Capability Externalization

A central premise of MoE Sovereign is that language models should not be treated as static knowledge repositories:

> **"The small model does not need to memorize enterprise facts within its parameters. Its task is to formulate accurate structured queries and interpret the retrieved knowledge graph topology."**

By externalizing knowledge storage into persistent graph databases (e.g. Neo4j), enterprise knowledge can be updated dynamically, audited, access-controlled, and versioned without requiring expensive continuous model re-training.

---

## 🎯 Intended Functional Scope & Capabilities

1. **Multi-Hop Cypher Query Generation:** Formulates openCypher / Neo4j 5.x graph queries (`MATCH`, `WHERE`, `OPTIONAL MATCH`, `WITH`, `RETURN`) with explicit depth bounds.
2. **Entity Resolution & Linking:** Maps ambiguous colloquial mentions in user prompts to canonical entity nodes in the enterprise ontology.
3. **Structured Triplet Extraction:** Deconstructs unstructured technical text into validated knowledge graph triples with provenance attributes.
4. **Vector + Graph Hybrid Fusion:** Synthesizes unstructured vector search results with explicit relational graph paths.

---

## 🎯 Intended Behavioral Specialization

> *Note: The following table describes the intended specialization introduced by the distillation and training process. It should not be interpreted as a quantitative benchmark. Measured comparisons against the base model are reported in the Evaluation section.*

| Capability / Dimension | Base Stock Qwen 3.5 4B | `moe-expert-graphrag-4b` (Distilled) |
| :--- | :--- | :--- |
| **Cypher Syntax Discipline**| Prone to deprecated syntax or missing variable projections | **Modern Neo4j 5.x Cypher** with parameterized inputs and index hints |
| **Entity Resolution** | High rate of hallucinated entity identifiers | **Schema-Grounded Entity Resolution** using ontology bounds |
| **Multi-Hop Traversal**| Struggles with path depth constraints ($\ge 3$ hops) | **Bounded Path Traversal** (`(a)-[:REL*1..3]->(b)`) avoiding runaway scans |
| **Triple Extraction** | Free-form natural language predicates | **Ontology-Constrained Triples** mapped directly to schema relationship types |

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

### Reproducible Training Details:
- **Compute Infrastructure:** EuroHPC LUMI-G (8× AMD Instinct™ MI250X 128GB GPUs, Slurm Job `#21189563`)
- **Base Architecture:** Qwen3.5-4B (Hybrid Linear Attention + Mamba in BF16)
- **Dataset Scale:** 33,000 verified Cypher and graph-traversal trajectories
- **Optimization Strategy:** DeepSpeed ZeRO-2, PyTorch 2.6, ROCm 7.0
- **Epochs:** 3.0
- **Effective Batch Size:** 128 (Micro-batch 4 × 8 GPUs × Gradient Accumulation 4)
- **Learning Rate:** $1.5 \times 10^{-5}$ with Cosine Decay and Warmup
- **LoRA Hyperparameters:** $r=16$, $\alpha=32$, Dropout $0.05$, Target Modules: `q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj`
- **Optimization Outcome:** Final Training Loss `0.0083`, Training Token Accuracy `99.81%`

---

## 🖥️ Consumer Hardware Deployment

`moe-expert-graphrag-4b` is designed for local deployment alongside existing database infrastructure on commodity hardware.

### Deployment Characteristics:
- **Quantized Formats:** Available in GGUF formats (`Q4_K_M` ~2.6 GB, `Q8_0` ~4.2 GB).
- **Runtime Compatibility:** Supported natively in Ollama, `llama.cpp`, and vLLM.
- **Hardware Profile:** Operates with low memory overhead on consumer GPUs (6 GB–12 GB VRAM) or CPU memory.

> *Consumer-hardware runtime measurements (VRAM residency, throughput tokens/sec, latency to first token, and energy consumption) are currently being evaluated across reference hardware tiers and will be published with the reproducible benchmark suite.*

---

## 📊 Evaluation

Systematic held-out evaluation against the unmodified base model is in progress across Cypher syntax validity, schema-compliant execution against live graph databases, and entity linking accuracy.

> ℹ️ *Note: Training loss (`0.0083`) and training-token accuracy (`99.81%`) reported above describe optimization progress on the training split and must not be interpreted as held-out capability benchmarks. Empirical held-out benchmark results with dataset versions, sample counts ($N$), and confidence intervals will be released in the project's technical report.*

---

## 💻 Quickstart Guide (Ollama & Python)

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

## ⚠️ Limitations

1. **Schema Context Dependency:** Requires the target graph schema or ontology definition to be supplied in context for domain-specific relationships.
2. **Database Execution Safeguards:** Generated Cypher queries should be executed with query timeouts and memory limits in the database driver to prevent expensive cartesian joins.
3. **Large Graph Results:** Massive graph result sets (>1,000 nodes) should be paginated rather than rendered in a single prompt context.

---

## 📑 Citation & Reproducibility

```bibtex
@misc{moe_sovereign_2026_graphrag4b,
  author = {Horn, Philipp and MoE Sovereign Core AI Team},
  title = {MoE Sovereign GraphRAG Expert 4B: Knowledge Graph Retrieval SLM},
  year = {2026},
  publisher = {Hugging Face},
  howpublished = {\url{https://huggingface.co/h3rb3rn/moe-expert-graphrag-4b}},
  note = {Trained on the EuroHPC LUMI-G Supercomputer}
}
```
