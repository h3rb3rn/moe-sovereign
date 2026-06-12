# Sovereign MoE — System Documentation

> Last updated: 2026-06-13 — Version 3.0.0
> Project directory: `/opt/moe-sovereign`

---

## 1. Overview

**Sovereign MoE** is a fully self-hosted, sovereign Multi-Model LLM Orchestration System. Incoming requests are analyzed by an intelligent gating network, decomposed into subtasks, and distributed to specialized LLM experts, external search tools, and deterministic precision tools. Results are synthesized by a Judge LLM into a single coherent response.

The system is **OpenAI API-compatible** and can be used as a drop-in replacement in clients like Open WebUI, Claude Code, Continue.dev, and any OpenAI SDK.

### Core Principles

- **Sovereign by design** — all LLMs run locally via Ollama on own GPU hardware; cloud is optional and opt-in
- **Specialization over generalization** — the best available model per category, dynamically selected
- **Exact over estimated** — calculations, hashes, data queries run deterministically via MCP tools
- **Learning through use** — every request feeds back into the routing policy, knowledge graph, and expert performance scores
- **Infrastructure-adaptive routing** — the ONNX gating network adapts to the live cluster state in real time
- **Compliance-first** — a `local_only` mode enforces zero data egress; all cloud endpoints are disabled automatically
- **No hardcoded infrastructure** — all endpoints, models, and tokens are configured via Admin UI; zero hardcodes in source

---

## 2. Development Status (as of 2026-06-13)

```mermaid
gantt
    title MoE Sovereign — Development Roadmap
    dateFormat YYYY-MM-DD
    section Phase 1 — Core Infrastructure
    LangGraph Orchestrator        :done, 2026-01-01, 2026-03-15
    Expert Routing (T1/T2)        :done, 2026-02-01, 2026-03-15
    GraphRAG / Neo4j              :done, 2026-02-15, 2026-04-01
    MCP Precision Tools           :done, 2026-02-01, 2026-03-01
    Kafka Event Streaming         :done, 2026-03-01, 2026-04-15
    Semantic Cache (ChromaDB)     :done, 2026-02-15, 2026-03-15
    section Phase 2 — IMoE Gating Network
    Synthetic Dataset (665 Prompts) :done, 2026-06-10, 2026-06-11
    ONNX Router Prototype (Local) :done, 2026-06-11, 2026-06-11
    Dynamic Router Service        :done, 2026-06-11, 2026-06-12
    Feedback Loop Wiring          :done, 2026-06-12, 2026-06-12
    ChromaDB Template Cache Fix   :done, 2026-06-12, 2026-06-12
    VRAM Context Clamping         :done, 2026-06-12, 2026-06-13
    section Phase 3 — Sovereign-14B Training
    Dataset Expansion (100k traces) :active, 2026-06-13, 2026-06-27
    LUMI-G Phase 1 (Setup+RCCL)   :2026-06-27, 2026-06-30
    LUMI-G CPT + SFT + RLSF       :2026-07-01, 2026-07-31
    section Phase 4 — JMoE Framework
    Paraconsistent Judge (Belnap-Dunn) :2026-07-15, 2026-08-15
    Adversarial Debate Engine     :2026-07-15, 2026-08-31
```

### Component Status

| Component | Status | Since |
|---|---|---|
| LangGraph Orchestrator | ✅ Production | 2026-03 |
| OpenAI-compatible API | ✅ Production | 2026-03 |
| Expert Routing (T1/T2, 14 categories) | ✅ Production | 2026-04 |
| GraphRAG / Neo4j (14.796 entities) | ✅ Production | 2026-04 |
| MCP Precision Tools (16 tools) | ✅ Production | 2026-03 |
| Semantic Response Cache (ChromaDB) | ✅ Production | 2026-03 |
| Kafka Event Streaming | ✅ Production | 2026-04 |
| Thompson Sampling (expert scoring) | ✅ Production | 2026-04 |
| **IMoE ONNX Gating Network** | ✅ Production | **2026-06-12** |
| **Dynamic Template Router** | ✅ Production | **2026-06-12** |
| **ChromaDB Template Cache** | ✅ Production | **2026-06-12** |
| **Feedback-Loop → Retraining Buffer** | ✅ Production | **2026-06-12** |
| **VRAM Context Budget Clamping** | ✅ Production | **2026-06-13** |
| Sovereign-14B (LUMI-G Training) | 🔄 In Preparation | — |
| JMoE Adversarial Debate Engine | 🔄 Planned | — |

---

## 3. Hardware & Infrastructure

```mermaid
graph TB
    subgraph KI_NODE05["ki-vm-node05 (Host Server)"]
        ORCH["langgraph-orchestrator :8002\nLangGraph + FastAPI + IMoE Router"]
        ADMIN["moe-admin :8088\nAdmin UI (Config, Users, Monitoring)"]
        MCP["mcp-precision :8003\n16 Deterministic Precision Tools"]
        EMBED["moe-embed\nall-MiniLM-L6-v2 (local)"]
        CHROMA["chromadb-vector\n3.310 Documents\nSemantic Cache + Template Cache"]
        NEO4J["neo4j-knowledge :7687\n14.796 Entities\n15.565 Relations"]
        PG["terra_checkpoints :5432\nPostgres — LangGraph State\nTemplates, Feedback Log"]
        VALKEY["terra_cache :6379\nValkey — Thompson Sampling\nPlan Cache, Context Budget Cache"]
        KAFKA["moe-kafka :9092\nKRaft — Audit, Ingest, Feedback"]
        GRAFANA["moe-grafana :3001\nPrometheus + Dashboards"]
    end

    subgraph N04_RTX["N04-RTX (node-0X.internal)"]
        direction TB
        GPU_RTX["5× GPU\n60 GB VRAM total"]
        OLLAMA_RTX["Ollama :11434\nqwen3.6:35b (warm)\nqwen3-coder:30b\nllama3.3-70b-ctx4k\n+ weitere"]
    end

    subgraph N11_M10["N11-M10 (node-0X.internal)"]
        direction TB
        GPU_M10["4× GPU\n32 GB VRAM total"]
        OLLAMA_M10["Ollama :11434\nqwen3.6:35b (Judge)\nphi4:14b\n+ weitere"]
    end

    ORCH --> N04_RTX
    ORCH --> N11_M10
    ORCH --> CHROMA
    ORCH --> NEO4J
    ORCH --> PG
    ORCH --> VALKEY
    ORCH --> KAFKA
    ORCH --> MCP
    ORCH --> EMBED
```

### VRAM Budget (enforced)

| Node | VRAM | Judge Limit | Expert Limit |
|---|---|---|---|
| N04-RTX | 60 GB | `qwen3.6:35b` @32k ctx = ~26 GB | `llama3.3-70b-ctx4k` @4k = ~43 GB |
| N11-M10 | 32 GB | `qwen3.6:35b` @32k ctx = ~26 GB | max 1× 30B model |

Context windows are enforced via `resolve_requested_ctx()` in `context_budget.py` — static DB metadata overrides live Ollama `/api/ps` to prevent OOM reload cascades.

---

## 4. IMoE Gating Network (NEW — June 2026)

The **Infrastructure Mixture of Experts (IMoE) Gating Network** is the core innovation of the current development sprint. Instead of static admin-defined templates, a lightweight ONNX classifier dynamically compiles optimal routing configurations per request.

```mermaid
flowchart TD
    PROMPT["User Prompt"] --> EMBED_LOCAL["all-MiniLM-L6-v2\nLocal Embedding Model\n384-dim vector"]
    EMBED_LOCAL --> CACHE_CHECK{"ChromaDB\nTemplate Cache\ncosine distance < 0.18?"}
    CACHE_CHECK -->|"🎯 HIT (distance ≈ 0.000)"| CACHED_TMPL["Cached Template\nReuse existing row\n→ No DB duplicate"]
    CACHE_CHECK -->|"MISS"| ONNX["Sovereign Router\n(sovereign_router.onnx)\nCPU AVX-512 < 5ms"]

    ONNX --> OUTPUTS["Classifier Outputs"]

    subgraph OUTPUTS["Classifier Outputs"]
        CATS["14 Expert Categories\n(multi-label float ∈ [0,1])"]
        COMP["4 Complexity Levels\ntrivial / moderate / complex / memory_recall"]
        GATES["2 Retrieval Gates\nweb_research / graphrag"]
    end

    OUTPUTS --> ALLOC["Dynamic Allocation\nModel Scoring per Category"]

    subgraph ALLOC["Dynamic Allocation"]
        LIVE["Live Cluster State\n(INFERENCE_SERVERS, /api/tags, /api/ps)"]
        THOMPSON["Thompson Sampling\nβ-Bernoulli per (model, category)"]
        COMPLIANCE["Compliance Gate\nlocal_only=True → Cloud blocked"]
        META["model_metadata DB\nparams, ctx, benchmarks (MMLU, HumanEval)"]
    end

    ALLOC --> TMPL["Dynamic Expert Template\n(JSON config with models, ctx budgets,\nsystem prompts, retrieval gates)"]
    TMPL --> DB["Postgres\nadmin_expert_templates\n+ dynamic_template_feedback_log"]
    TMPL --> CACHE_WRITE["ChromaDB\nTemplate Cache\n(raw prompt as document)"]
    TMPL --> ORCH_PIPE["LangGraph Pipeline\n→ Expert Execution"]
```

### ONNX Model Details

| Parameter | Value |
|---|---|
| Architecture | Multi-Task Feed-Forward Classifier |
| Base embeddings | `all-MiniLM-L6-v2` (384 dim) |
| Training dataset | 665 synthetic prompts (12 expert domains) |
| Training | 40 epochs, loss 0.285 → 0.032 |
| Training hardware | Local Dev Server (RTX 3060) (LUMI-G export prepared) |
| Inference latency | < 5ms CPU (AVX-512) |
| Deployment | `/app/models/sovereign_router.onnx` |
| Outputs | 14 category scores + 4 complexity classes + 2 gates |

> [!NOTE]
> The MoE Sovereign project has been officially awarded a **EuroHPC Development Grant** (Proposal No. **EHPC-DEV-2026D06-XXX**) of **4,500 node-hours (18,000 GPU-hours)** on the **LUMI-G supercomputer** (AMD MI250x partition). While the current 22M parameter gating model was trained locally to establish the pipeline, the approved EuroHPC resources are reserved for the upcoming large-scale `Sovereign-14B` LLM training (Phase 3) and full-scale dataset retraining.

### Allocation Scoring Formula

For each candidate model $M$ in category $C$:

$$\text{Score}(M, C) = w_{\text{warmed}} \cdot \mathbb{I}(M_{\text{warm}}) + w_{\text{local}} \cdot \mathbb{I}(M_{\text{local}}) + w_{\text{bench}} \cdot \text{Benchmark}(M) + w_{\text{feedback}} \cdot \text{ThompsonSample}(M, C)$$

- **Warmed bonus**: Models already loaded in GPU VRAM are strongly preferred
- **Local priority**: On-premise nodes score higher than cloud endpoints
- **Benchmark**: MMLU/HumanEval/GSM8k scores from `model_metadata` Postgres table
- **Thompson Sample**: Beta-Bernoulli distribution from live success/failure history in Valkey

---

## 5. System Architecture — Full Pipeline

```mermaid
flowchart TD
    CLIENT["☁ Client\n(Open WebUI, curl, SDK, Claude Code, Continue.dev)"]
    NGINX["Nginx (host-native)\nTLS / Let's Encrypt → :8002"]

    subgraph ORCH["LangGraph Orchestrator · Port 8002"]
        subgraph GATE["IMoE Gating Layer (NEW)"]
            CHROMA_TMPL["🎯 ChromaDB Template Cache\nSemantic match < 0.18"]
            ONNX_ROUTER["⚡ Sovereign Router ONNX\n14 categories + complexity + gates\n< 5ms CPU"]
            DYN_ALLOC["🔀 Dynamic Allocator\nThompson Sampling + Compliance Gate"]
        end

        CACHE_LOOKUP["🔍 cache_lookup\nSemantic response cache"]
        PLANNER["🧠 planner_node\n(Judge LLM + dynamic template)"]

        subgraph PARALLEL["Parallel Expert Execution"]
            WORKERS["👥 expert_worker\nT1 + T2, confidence-gated"]
            RESEARCH["🌐 research_node\n(SearXNG + Splash)"]
            MATH["∑ math_node\n(SymPy)"]
            MCP_N["🔧 mcp_node\n(16 Precision Tools)"]
            GRAPHRAG["🗃 graph_rag_node\n(Neo4j 2-hop)"]
        end

        THINKING["💭 thinking_node\n(CoT, conditional on complexity)"]
        MERGER["⚖ merger_node\n(Judge LLM)\nPRE-FLIGHT ctx-check\ncompress-on-overflow"]
        CRITIC["🔎 critic_node\n(medical / legal only)"]
    end

    CLIENT -->|"HTTPS"| NGINX
    NGINX -->|"POST /v1/chat/completions"| GATE
    GATE --> CACHE_LOOKUP
    CACHE_LOOKUP -->|"Hit < 0.15"| CLIENT
    CACHE_LOOKUP -->|"Miss"| PLANNER
    PLANNER --> PARALLEL
    PARALLEL --> THINKING
    THINKING --> MERGER
    MERGER --> CRITIC
    CRITIC --> CLIENT

    MERGER -.->|"background"| CHROMADB[("ChromaDB\nSemantic Cache")]
    MERGER -.->|"background"| VALKEY[("Valkey\nThompson Sampling\nPlan / Graph Cache")]
    MERGER -.->|"background"| PG_DB[("Postgres\nLangGraph State\nTemplates, Feedback")]
    MERGER -.->|"moe.ingest + moe.requests"| KAFKA_BUS[("Kafka\nKRaft")]
    KAFKA_BUS -.->|"consumer"| NEO4J_DB[("Neo4j\nKnowledge Graph\n14.796 Entities")]
```

---

## 6. LangGraph Pipeline — Node Details

### Pipeline Flow

```mermaid
flowchart LR
    START([START]) --> GATE_L["IMoE Gate\n(ONNX + ChromaDB)"]
    GATE_L --> CACHE["cache_lookup\nChromaDB response cache"]
    CACHE -->|"Hit"| MERGE
    CACHE -->|"Miss"| PLAN["planner_node\nJudge LLM + dynamic template"]

    PLAN --> EW["expert_worker\nT1→T2 confidence escalation"]
    PLAN --> RN["research_node\nSearXNG"]
    PLAN --> MN["math_node\nSymPy"]
    PLAN --> MCPN["mcp_node\n16 tools"]
    PLAN --> GRN["graph_rag_node\nNeo4j"]

    EW --> RF["research_fallback\nlow-confidence web enrichment"]
    RN --> RF
    MN --> RF
    MCPN --> RF
    GRN --> RF

    RF --> THINK["thinking_node\n4-step CoT"]
    THINK --> MERGE["merger_node\nJudge LLM synthesis\nPRE-FLIGHT ctx budget"]
    MERGE --> CRIT["critic_node\nmedical/legal only"]
    CRIT --> END([END])
```

### Key Node Behaviours

#### `planner_node`
- Receives dynamic template from IMoE gate (`complexity_level`, expert categories, retrieval gates)
- Judge LLM decomposes query into 1–4 typed subtasks
- `_sanitize_plan()` validates; falls back to `[{task: input, category: "general"}]`
- **Context clamping**: `resolve_requested_ctx()` enforces per-model VRAM-safe limits

#### `expert_worker`
- **T1 (≤ 20B)** runs first; `CONFIDENCE: high` → T2 skipped; otherwise T2 escalates
- Thompson-sampled performance scores gate expert selection (score < 0.3 → skip)
- Injects chat history (last 4 turns, max 3000 chars) into all messages
- Output cap: `MAX_EXPERT_OUTPUT_CHARS` (2400 chars)
- Confidence-weighted merge in `merger_node`: `★★★ PRIMARY > ★★☆ SUPPORTING > ★☆☆ BACKGROUND`

#### `merger_node`
- **PRE-FLIGHT**: `resolve_requested_ctx()` computes available context budget *before* the LLM call
- On overflow: `compress_prompt_to_fit()` prunes expert inputs proportionally
- Source priority: `Reasoning trace > MCP > Knowledge Graph > Experts > Web > Cache`
- Background writes: ChromaDB cache, Valkey metadata, Kafka audit, Kafka ingest

#### `thinking_node`
- Active if plan has > 1 task **OR** any expert returns `CONFIDENCE: low`
- 4-step Chain-of-Thought: (1) decomposition → (2) source evaluation → (3) gaps → (4) conclusion
- Output as `reasoning_trace` → top-priority section in merger prompt

---

## 7. Expert System

### Expert Categories (14 total)

| Category | T1 Model | T2 Model | Notes |
|---|---|---|---|
| `general` | `gemma3:12b` | `qwen3-coder:30b` | Default fallback |
| `math` | `phi4:14b` | `qwq:32b` | STEM-focused |
| `technical_support` | `deepseek-coder-v2:16b` | `devstral:24b` | DevOps/IT |
| `creative_writer` | `gemma3:27b` | `qwen3.5:35b` | Diverse architectures |
| `code_reviewer` | `devstral:24b` | `qwen3-coder:30b` | Security + modern patterns |
| `medical_consult` | `phi4:14b` | `gemma3:27b` | Safety-critical, triggers critic node |
| `legal_advisor` | `magistral:24b` | `command-r:35b` | Citation-aware RAG |
| `translation` | `translategemma:27b` | `qwen3.5:35b` | Specialist + multilingual |
| `reasoning` | `phi4:14b` | `deepseek-r1:32b` | True CoT reasoning |
| `vision` | — | multimodal model | Image understanding |
| `data_analyst` | `phi4:14b` | `qwen3-coder:30b` | Data analysis |
| `science` | `phi4:14b` | `qwen3.5:35b` | Scientific reasoning |
| `tool_expert` | `qwen3-coder:30b` | — | Tool/API usage |
| `research` | SearXNG | — | Web research gate |

**Current active config (`.env`):** All categories mapped to `qwen3-coder:30b@N04-RTX` as forced override during testing. Revert to per-category config via Admin UI → Expert Templates.

**Judge LLM:** `qwen3.6:35b@N11-M10` — planner, merger, thinking node, critic, GraphRAG extraction

---

## 8. Feedback Loop & Policy Learning

```mermaid
flowchart LR
    REQUEST["User Request\n→ Pipeline"] --> RESPONSE["Response\n+ response_id"]
    RESPONSE --> FEEDBACK["POST /v1/feedback\n{response_id, rating: 1-5}"]

    FEEDBACK --> CHROMA_F["ChromaDB\nrating 1-2: flagged=True\nrating 4-5: entry preserved"]
    FEEDBACK --> VALKEY_F["Valkey\nmoe:perf:{model}:{category}\ntotal++ / positive++ / negative++"]
    FEEDBACK --> NEO4J_F["Neo4j\nrating 1-2: r.flagged=true\nrating 4-5: r.verified=true"]
    FEEDBACK --> PG_F["Postgres\ndynamic_template_feedback_log\nuser_rating updated"]
    FEEDBACK --> JSONL["logs/retraining_dataset.jsonl\nRating 4-5 → positive sample\nRating 1-2 → DPO negative sample"]

    JSONL -->|"future"| LUMI["LUMI-G\nSovereign Router Retraining"]

    subgraph THOMPSON["Thompson Sampling (live)"]
        TS_SCORE["Score = (positive+1)/(total+2)\nLaplace smoothed\nScore < 0.3 → skip model"]
    end

    VALKEY_F --> THOMPSON
    THOMPSON --> REQUEST
```

### Expert Performance Scoring

```
Key:    moe:perf:{model}:{category}
Fields: total, positive, negative

Score = (positive + 1) / (total + 2)   # Laplace smoothing
```

| Score | Behaviour |
|---|---|
| < 5 ratings | 0.5 (neutral start) |
| ≥ 0.7 | Preferred; warmed bonus applied |
| < 0.3 after ≥5 ratings | Expert skipped for this category |

---

## 9. MCP Precision Tools

**Port:** 8003 · **File:** `mcp_server/server.py`

All 16 tools run deterministically — no LLM estimation:

| Tool | Description |
|---|---|
| `calculate` | Exact arithmetic, formulas, percentages |
| `solve_equation` | Algebraic equations (SymPy) |
| `date_diff` | Exact date difference (days, years, months) |
| `date_add` | Date arithmetic |
| `day_of_week` | Day of week, calendar week |
| `unit_convert` | Physical units (pint) |
| `statistics_calc` | mean, median, stdev, variance, ... |
| `hash_text` | MD5, SHA1, SHA256, SHA512 |
| `base64_codec` | Base64 encode / decode |
| `regex_extract` | Regex matching with flags |
| `subnet_calc` | CIDR: network, broadcast, host range |
| `text_analyze` | Words, chars, sentences, reading time |
| `prime_factorize` | Prime factorization |
| `gcd_lcm` | GCD and LCM |
| `json_query` | JSON path queries |
| `roman_numeral` | Arabic ↔ Roman |

---

## 10. GraphRAG & Knowledge Graph

**File:** `graph_rag/manager.py` · **DB:** Neo4j 5

```mermaid
flowchart LR
    QUERY["User Query"] --> EXTRACT["Term Extraction\n(regex, no LLM call)"]
    EXTRACT --> FUZZY["Fuzzy Search\nname + aliases_str\ncase-insensitive"]
    FUZZY --> NEO4J_QUERY["Neo4j 2-hop Traversal\ndirect + indirect relations"]
    NEO4J_QUERY --> CTX["[Knowledge Graph]\ncontext block → merger prompt"]

    MERGER_RESP["Merger Response"] -->|"Kafka moe.ingest"| CONSUMER["Kafka Consumer"]
    CONSUMER --> EXTRACT_LLM["Judge LLM\nextract up to 8 triples"]
    EXTRACT_LLM --> CONFLICT["Conflict Check\nTREATS ↔ CAUSES / CONTRAINDICATES"]
    CONFLICT --> STORE["Neo4j MERGE\nr.verified=false (pending)"]

    FEEDBACK_NEO["Feedback\nrating 4-5"] -->|"verified=true"| STORE
    FEEDBACK_NEO2["Feedback\nrating 1-2"] -->|"flagged=true"| STORE
```

**Current state:** 14.796 entities · 15.565 relations · Growing with every request

### Base Ontology

- **104 base entities** — Medical, Legal, Technical, Math/Science domains
- **100+ relation types** — IS_A, TREATS, CAUSES, IMPLEMENTS, DEPENDS_ON, EXTENDS, ...

---

## 11. Memory Architecture (4 Levels)

```mermaid
graph TD
    subgraph L1["L1 — Semantic Response Cache (ChromaDB)"]
        L1A["Grows from: every merger response > 150 chars"]
        L1B["Hit threshold: cosine distance < 0.15"]
        L1C["3.310 documents (current)"]
    end
    subgraph L2["L2 — Knowledge Graph (Neo4j)"]
        L2A["Grows from: Kafka moe.ingest consumer"]
        L2B["14.796 entities · 15.565 relations"]
        L2C["Background: verified by feedback ratings"]
    end
    subgraph L3["L3 — Expert Performance (Valkey)"]
        L3A["Grows from: POST /v1/feedback"]
        L3B["moe:perf:{model}:{category} — total/positive/negative"]
        L3C["Thompson Sampling β-distribution per (model, category)"]
    end
    subgraph L4["L4 — Dynamic Template Cache (ChromaDB + Postgres)"]
        L4A["Grows from: every IMoE routing decision"]
        L4B["ChromaDB: raw prompt → template_id (cosine < 0.18)"]
        L4C["Postgres: admin_expert_templates + feedback_log"]
    end
```

---

## 12. Configuration (Admin UI)

All operational parameters are configured via **MoE Admin UI** (`:8088`) or `.env`. No infrastructure values are hardcoded in source.

### Key Environment Variables

| Variable | Description |
|---|---|
| `INFERENCE_SERVERS` | JSON array of all inference endpoints (Ollama + Cloud) — **single source of truth** |
| `JUDGE_MODEL` / `JUDGE_ENDPOINT` | Model and node for planner/merger/critic |
| `JUDGE_NUM_CTX` | Context window for judge (enforced by `resolve_requested_ctx()`) |
| `EXPERT_MODELS` | JSON: category → `[{model, endpoint, enabled, forced}]` |
| `POLICY_LOG_PATH` | Container-internal path for policy training JSONL |
| `SYSTEM_API_KEY` | System account API key for cloud model discovery |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |

> [!IMPORTANT]
> Cloud endpoints for the IMoE Dynamic Router are derived automatically from `INFERENCE_SERVERS` entries with `api_type != "ollama"`. Configure AIHUB or any other cloud provider via **Admin UI → Inference Servers** — no `.env` changes or container restarts needed.

---

## 13. Docker Services

The full stack consists of **50+ Docker services** on `ki-vm-node05`. Core services:

| Container | Ports | Function |
|---|---|---|
| `langgraph-orchestrator` | `8002→8000` | Core orchestrator, FastAPI, LangGraph, IMoE Router |
| `mcp-precision` | `8003→8003` | 16 deterministic precision tools |
| `moe-admin` | `8088→8088` | Admin UI: config, users, templates, monitoring |
| `moe-embed` | internal | Local embedding model (`all-MiniLM-L6-v2`) |
| `chromadb-vector` | internal | Semantic response + template cache |
| `neo4j-knowledge` | `7474, 7687` | Knowledge graph (GraphRAG + ontology) |
| `terra_checkpoints` | internal | Postgres — LangGraph state, templates, feedback |
| `terra_cache` | internal | Valkey — Thompson Sampling, plan cache, ctx cache |
| `moe-kafka` | `9092` | Kafka KRaft — audit, ingest, feedback events |
| `moe-grafana` | `3001` | Prometheus dashboards |
| `moe-jupyterlab` | `8899` | Jupyter for data analysis and model experiments |
| `moe-mlflow` | `5002` | ML experiment tracking |
| `open-webui` | `3000` | Chat frontend |
| `moe-docs` | `8098` | This documentation |

---

## 14. Quality & Safety Mechanisms

```mermaid
flowchart TD
    EO["Expert Output"] --> FB{"CONFIDENCE: low?"}
    FB -->|"yes"| RF["research_fallback\nTargeted web research\n+ source citations"]
    FB -->|"no"| TN

    RF --> TN["thinking_node\n(active if >1 task OR low confidence)\n4-step CoT reasoning trace"]
    TN --> MN["merger_node\n_dedup_by_category: best confidence wins\nSource priority: Reasoning > MCP > Graph > Expert > Web"]
    MN --> CN{"critic_node\nmedical_consult / legal_advisor only"}
    CN -->|"CONFIRMED"| OUT(["✅ Response"])
    CN -->|"error found"| FIX(["✅ Corrected Response"])
```

### VRAM Safety (Context Budget)

```mermaid
flowchart LR
    MODEL["Model name"] --> DB_LOOKUP["Postgres model_metadata\n(ctx_window override)"]
    DB_LOOKUP -->|"found"| CLAMP["resolve_requested_ctx()\nstate_num_ctx OR env_num_ctx OR static_ctx\nclamped to safe DB limit"]
    DB_LOOKUP -->|"not found"| PARSE["Name-based parsing\n(e.g. ctx4k → 4096)"]
    CLAMP --> PRE_FLIGHT["PRE-FLIGHT check\n(merger/expert)\noverflow → compress_prompt_to_fit()"]
    PARSE --> PRE_FLIGHT
```

---

## 15. API Reference

**Base URL:** `http://<host>:8002`

```http
# Chat (streaming or non-streaming)
POST /v1/chat/completions
Authorization: Bearer <api-key>
{"model": "moe-auto", "messages": [{"role": "user", "content": "..."}], "stream": false}

# Available models
GET /v1/models

# Feedback (1-5 rating)
POST /v1/feedback
{"response_id": "chatcmpl-...", "rating": 4}

# Knowledge graph stats
GET /graph/stats
GET /graph/search?q=<term>&limit=10
```

**Model IDs:**

| ID | Mode |
|---|---|
| `moe-auto` | Full pipeline with dynamic routing |
| `moe-orchestrator` | Default (full explanations) |
| `moe-orchestrator-code` | Code only, no prose |
| `moe-orchestrator-concise` | Max 120 words |

---

## 16. Project Structure

```mermaid
graph LR
    ROOT["/opt/moe-sovereign/"]

    ROOT --> MAIN["main.py\nFastAPI, LangGraph, nodes, Kafka"]
    ROOT --> GRAPH["graph/\nexpert.py · synthesis.py · planner.py"]
    ROOT --> SVC["services/\ndynamic_router.py ← IMoE Gating\ninference.py · routing.py\npolicy_log.py · context_budget.py"]
    ROOT --> CFG["config.py\nINFERENCE_SERVERS → URL_MAP\n+ TOKEN_MAP + API_TYPE_MAP"]
    ROOT --> ADM["admin_ui/\ndatabase.py · Dockerfile"]
    ROOT --> MCP_D["mcp_server/\nserver.py · Dockerfile"]
    ROOT --> GRAPH_RAG["graph_rag/\nmanager.py · ontology.py"]
    ROOT --> MODELS["models/\nsovereign_router.onnx\nsovereign_router.onnx.data"]
    ROOT --> SCRIPTS["scripts/\ndataset_generator.py\nindex_models_metadata.py\ntrain_router_onnx.py"]
    ROOT --> TESTS["tests/\ntest_dynamic_router.py\ntest_context_index.py"]
    ROOT --> DOCS["docs/\nMkDocs source"]
    ROOT --> ENV[".env\n(not in git — Admin UI source)"]
```

---

## 17. What's Next — Development Preview

The next development phase focuses on three parallel tracks:

### Track A — Training Data Pipeline
Expansion of the synthetic training dataset from 665 to 100,000 traces across three data types: routing decisions, multi-agent debate logs (Proponent vs. Skeptic), and paraconsistent logical maps. This dataset will serve as the foundation for full-scale LLM training on the LUMI-G supercomputer.

### Track B — LUMI-G Sovereign-14B Training
Using the 4,500 allocated node-hours on LUMI-G (AMD MI250x), a custom `Sovereign-14B` model will be trained through three stages: Continual Pre-Training (CPT) on domain knowledge, Supervised Fine-Tuning (SFT) on routing and planning behavior, and Reinforcement Learning from System Feedback (RLSF) using real cluster telemetry. The trained model will replace the current Judge LLM.

### Track C — JMoE Adversarial Framework
Implementation of the Judicial Mixture of Experts framework: an adversarial debate engine (Proponent vs. Skeptic agents) combined with a paraconsistent Judge based on Belnap-Dunn 4-valued logic (True / False / Inconsistent / Unknown). This replaces single-model synthesis with verifiable, formally grounded truth arbitration.

---

*Generated on 2026-06-13 — Version 3.0.0*
