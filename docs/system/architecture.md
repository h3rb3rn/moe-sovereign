# MoE Sovereign — System Architecture

> Last updated: 2026-06-13 — Version 3.0.0
> Project directory: `/opt/moe-sovereign`

---

## 1. Pipeline Overview

MoE Sovereign is a LangGraph-based Multi-Model LLM Orchestrator. The system is designed to route incoming queries to the most optimal specialist LLMs on local GPU hardware while allowing dynamic integration of cloud providers (like AIHUB, LiteLLM, or custom OpenAI endpoints) without hardcoding them in the software stack.

The request lifecycle is managed by an intelligent **Infrastructure Mixture of Experts (IMoE) Gating Layer** followed by a structured **LangGraph Execution Pipeline**.

---

## 2. IMoE Gating Layer & Pipeline Flowchart

Before entering the LangGraph pipeline, each prompt is analyzed by the **IMoE Gating Layer** to compile a request-specific dynamic template.

```mermaid
flowchart TD
    IN([Client Request]) --> GATE_EMBED["all-MiniLM-L6-v2\nLocal Embedding Model"]
    GATE_EMBED --> GATE_CACHE{"ChromaDB Template Cache\ncosine distance < 0.18?"}
    
    GATE_CACHE -->|Hit 🎯| TMPL_USE["Reuse Compiled Template\n(No database duplicate)"]
    GATE_CACHE -->|Miss| GATE_ONNX["⚡ Sovereign Router (ONNX)\nFeed-Forward Classifier < 5ms CPU"]
    
    GATE_ONNX --> ONNX_OUT["ONNX Classifier Outputs\n- 14 Expert Categories (0-1)\n- 4 Complexity Levels\n- 2 Retrieval Gates (web/graph)"]
    
    ONNX_OUT --> DYN_ROUTE["🔀 Dynamic Routing & Allocator\n- Thompson Sampling per model/category\n- VRAM Context Clamping (resolve_requested_ctx)\n- Compliance Gate (local_only)"]
    
    DYN_ROUTE --> TMPL_COMPILE["Dynamic Expert Template (JSON)\n- Expert models/endpoints\n- System prompts & budget limits"]
    
    TMPL_COMPILE --> WRITE_CACHE["Write to ChromaDB Template Cache\n+ log to dynamic_template_feedback_log"]
    
    TMPL_USE --> LANGGRAPH["LangGraph Execution Pipeline"]
    WRITE_CACHE --> LANGGRAPH
```

### The LangGraph Pipeline

Once the template is compiled, the request enters the main execution pipeline:

```mermaid
flowchart TD
    LANGGRAPH --> L1_CACHE{"L1 Response Cache\nChromaDB semantic hit\ncosine distance < 0.15?"}
    
    L1_CACHE -->|Hit ⚡| Done([Stream Response])
    L1_CACHE -->|Miss| PLAN_CACHE{"L2 Plan Cache\nValkey hit?"}
    
    PLAN_CACHE -->|Hit| PARALLEL["Parallel execution fan-out"]
    PLAN_CACHE -->|Miss| PLAN_LLM["Planner LLM\n(Decomposes query to subtasks)\nWrites back to Valkey (TTL 30 min)"]
    
    PLAN_LLM --> PARALLEL
    
    subgraph PARALLEL ["Parallel Expert Execution"]
        direction LR
        WORKERS["👥 Expert Workers\nT1 + T2 confidence-gated\n(resolve_requested_ctx)"]
        RESEARCH["🌐 Web Research\nSearXNG + Splash"]
        MATH["∑ Math Node\nSymPy calculations"]
        MCP["🔧 MCP Node\n16 deterministic tools"]
        GRAG["🗃 GraphRAG Node\nNeo4j 2-hop + CAG\n+ Domain-Filtered ChromaDB"]
    end
    
    WORKERS --> MERGE_CHECK{"Merger Fast-Path Check\n1 expert, high confidence\nno web/mcp/graph?"}
    RESEARCH --> MERGE_CHECK
    MATH --> MERGE_CHECK
    MCP --> MERGE_CHECK
    GRAG --> MERGE_CHECK
    
    MERGE_CHECK -->|Yes ⚡| MERGE_FP["Fast-Path\nSkip Judge LLM\n(save 1.5k-4k tokens)"]
    MERGE_CHECK -->|No| MERGE_JUDGE["⚖ Merger / Judge LLM\nPre-flight VRAM Clamping\nCompress-on-overflow"]
    
    MERGE_FP --> THINKING_CHECK{"Complex query & thinking active?"}
    MERGE_JUDGE --> THINKING_CHECK
    
    THINKING_CHECK -->|Yes| THINKING_NODE["💭 Thinking Node\n4-step CoT reasoning trace"]
    THINKING_NODE --> CRITIC_CHECK{"medical_consult / legal_advisor?"}
    THINKING_CHECK -->|No| CRITIC_CHECK
    
    CRITIC_CHECK -->|Yes| CRITIC["🔎 Critic Node\nAsync self-evaluation\n& verification"]
    CRITIC_CHECK -->|No| SAVE_STATE
    CRIT --> SAVE_STATE
    
    SAVE_STATE["Background Saves\n- ChromaDB L1 Cache\n- Valkey Thompson stats\n- Postgres log & thread checkpoint\n- Kafka (moe.ingest / moe.requests)"]
    
    SAVE_STATE --> Done
```

---

## 3. Node Descriptions

| Node | Function | Key Logic & Safeguards |
|---|---|---|
| `cache_lookup` | ChromaDB semantic response cache | Cosine distance < 0.15 → hard hit (skips all LLM nodes); 0.15-0.50 → soft hit (few-shot examples injected) |
| `planner` | Task decomposition | Generates `[{task, category, search_query?, mcp_tool?, metadata_filters?}]`. Extracts `metadata_filters` from the first task for domain-scoped memory retrieval. |
| `expert_worker` | Specialist execution | Two-tier routing. Runs T1 first. If T1 confidence is low, escalates to T2. Limits VRAM allocation and context windows using `resolve_requested_ctx()`. |
| `research` | SearXNG web search | Multi-query web search. Runs if the compiled template enables web research or `research` category is planned. |
| `math` | SymPy calculation | Executed if `math` is in the plan, providing zero-hallucination symbolic calculations. |
| `mcp` | MCP Precision Tools | Executes 16 deterministic system tools (CIDR, date math, hashing, unit conversions) via HTTP. |
| `graph_rag` | Neo4j knowledge base | Performs a 2-hop traversal in Neo4j. If `metadata_filters` are present, queries ChromaDB with a `where` filter and appends results as `[Domain-Filtered Memory]`. |
| `merger` | Response synthesis | The Judge LLM synthesizes all inputs. Runs a **PRE-FLIGHT** context check using `resolve_requested_ctx()` and proportional pruning to prevent OOMs on judge nodes. |
| `critic` | Post-generation verification | Actively reviews medical and legal expert outputs for correctness, applying self-correction logs to Valkey and files. |

---

## 4. Hardware & VRAM Context Clamping

To prevent VRAM page out and OOM cascades, the system strictly enforces context budgets via `resolve_requested_ctx()` in `context_budget.py`.

Static Postgres metadata overrides live node `/api/ps` telemetry. This prevents VRAM allocations from expanding beyond the physical GPU capacities of:
- **N04-RTX:** 60 GB total VRAM (Pins `qwen3.6:35b` at 32k ctx and `llama3.3-70b-ctx4k` at 4k ctx).
- **N11-M10:** 32 GB total VRAM (Max 30B parameter models at constrained context).

---

## 5. Configuration Strategy & Dynamic Admin UI

Operational variables must remain fully configurable and not hardcoded to a single provider.
- All endpoints and cloud-model parameters are derived from the `INFERENCE_SERVERS` JSON list.
- Cloud providers (such as AIHUB, LiteLLM, custom OpenAI servers) can be added or toggled dynamically in the **Admin UI → Inference Servers**.
- The `SYSTEM_API_KEY` is loaded from `.env` to authenticate internal system loops without baking plaintext keys into source files.

---

## 6. Development Roadmap Preview

The system is currently preparing for:
1. **LUMI-G Supercomputer CPT/SFT Training:** Retraining the `Sovereign-14B` model using node-hours on LUMI-G to replace the judge model with a dedicated distilled local alternative.
2. **JMoE Framework:** An adversarial debate engine utilizing paraconsistent Belnap-Dunn logic (True / False / Inconsistent / Unknown) to arbitrate truth across conflicting expert predictions.
