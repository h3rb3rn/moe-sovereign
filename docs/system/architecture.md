# MoE Sovereign — System Architecture

## Overview

MoE Sovereign is a LangGraph-based Multi-Model Orchestrator. Each incoming query is decomposed by a planner LLM into typed tasks, routed to specialist models in parallel, enriched with knowledge graph context and optional web research, then synthesized by a judge LLM into a single coherent response.

All caching is multi-layered: semantic vector cache (ChromaDB), plan cache (Valkey), GraphRAG cache (Valkey), and performance-scored expert routing (Valkey). The API is fully OpenAI-compatible.

!!! info "Formal Logic State Layer"
    As of 2026-05-07, the pipeline state is grounded in formal algebraic logic theory
    (A. de Vries, arXiv:0707.2161, 2007): **paraconsistent** conflict registry,
    **intuitionistic** constructive proof validation, **fuzzy T-norm** routing,
    AIC-based complexity estimation, and infrastructure-adaptive Thompson sampling.
    See **[Formal Logic Architecture](../ARCHITECTURE.md)** for the full technical reference.

---

## LangGraph Pipeline

```mermaid
flowchart TD
    IN([Client Request]) --> CACHE

    CACHE{cache_lookup\nChromaDB semantic\nhit < 0.15}
    CACHE -->|HIT ⚡| MERGE
    CACHE -->|MISS| SROUTE

    SROUTE[semantic_router\nChromaDB prototype\nmatching — optional\ndirect expert bypass]
    SROUTE --> PLAN

    PLAN[planner\nconfigurable model\nValkey plan cache TTL 30 min\nextract metadata_filters]
    PLAN --> PAR

    subgraph PAR [Parallel Execution]
        direction LR
        W[workers\nTier 1 + Tier 2\nexpert models]
        R[research\nSearXNG\nweb search]
        M[math\nSymPy\ncalculation]
        MCP[mcp\nMCP Precision Tools\n26 deterministic tools]
        GR[graph_rag\nNeo4j 2-hop traversal\n+ Procedural Requirements\n+ Filtered ChromaDB lookup\nValkey cache TTL 1h]
    end

    PAR --> RF[research_fallback\nconditional\nweb fallback]
    RF --> THINK[thinking\nchain-of-thought\nreasoning trace]
    THINK --> MERGE

    MERGE{merger\nJudge LLM\nor Fast-Path ⚡}
    MERGE -->|single hoch expert\nno extra context| FP[⚡ Fast-Path\ndirect return]
    MERGE -->|ensemble / multi| JUDGE[Judge LLM\nsynthesis]

    JUDGE --> CRIT[critic\npost-validation\nself-evaluation]
    FP --> CRIT
    CRIT --> OUT([Streaming Response])

    CRIT -.->|background| KAFKA[Kafka moe.ingest\nknowledge_type tagged\nsynthesis_insight optional]
    KAFKA -.-> INGEST[Graph Ingest LLM\nfactual + causal extraction\nNeo4j MERGE]
    KAFKA -.-> SYNTHP[Synthesis Persistence\ningest_synthesis\nNeo4j :Synthesis node]
    LINTING["moe.linting\nKafka trigger"] -.->|on-demand| JANITOR[run_graph_linting\norphan cleanup\nconflict resolution]
    JANITOR -.-> INGEST

    style CACHE fill:#1e3a5f,color:#fff
    style MERGE fill:#1e3a5f,color:#fff
    style PAR fill:#0d2137,color:#ccc
    style FP fill:#1a4a1a,color:#fff
    style OUT fill:#2d1b4e,color:#fff
    style KAFKA fill:#4a2a00,color:#fff
    style INGEST fill:#3a1a00,color:#fff
```

### Node Descriptions

| Node | Function | Key Logic |
|---|---|---|
| `cache_lookup` | ChromaDB semantic similarity | distance < 0.15 → hard hit; 0.15–0.50 → soft/few-shot examples |
| `semantic_router` | Fast pre-routing | Matches query to known category prototypes in ChromaDB; bypasses planner for clear-cut queries |
| `planner` | Task decomposition | Produces `[{task, category, search_query?, mcp_tool?, metadata_filters?}]`; extracts optional `metadata_filters` from first task for scoped downstream retrieval; Valkey plan cache TTL=30 min; complexity routing (trivial/moderate/complex) |
| `workers` | Parallel expert execution | Two-tier routing; T1 (≤20B) first, T2 (>20B) only if T1 confidence < threshold |
| `research` | SearXNG web search | Single or multi-query deep search; always runs if `research` category in plan |
| `math` | SymPy calculation | Runs only if `math` category in plan AND no `precision_tools` task |
| `mcp` | MCP Precision Tools | 26 deterministic tools via HTTP; runs if `precision_tools` in plan |
| `graph_rag` | Neo4j knowledge graph | Generic 2-hop entity/relation traversal + targeted procedural requirement lookup; Valkey cache TTL=1h; if `metadata_filters` is set in state, also performs a filtered ChromaDB query (`where` clause) and appends results as `[Domain-Filtered Memory]` to `graph_context` |
| `research_fallback` | Conditional extra search | Triggers if merger needs more context |
| `thinking` | Chain-of-thought reasoning | Generates `reasoning_trace`; activated by `force_think` modes |
| `merger` | Response synthesis (Judge LLM) | Fast-path bypasses Judge for single high-confidence experts; tags Kafka events with `knowledge_type` and `source_expert`; tags ChromaDB inserts with `expert_domain`; emits optional `<SYNTHESIS_INSIGHT>` block → stripped from user response, persisted as `:Synthesis` node in Neo4j |
| `critic` | Post-generation validation | Async self-evaluation; flags low-quality cache entries |

---

## Service Topology

```mermaid
graph LR
    subgraph Clients
        CC[Claude Code]
        OC[OpenCode / Cursor]
        CD[Continue.dev]
        CU[curl / any OpenAI client]
        OWU[Open WebUI]
    end

    subgraph Access["External Access Layer"]
        NGINX[Nginx\nhost-native\nTLS/Let's Encrypt]
        CADDY[moe-caddy :80/:443\nDocs + Logs TLS]
    end

    subgraph Core ["Core [:8002]"]
        ORCH[langgraph-orchestrator\nFastAPI + LangGraph]
    end

    subgraph Storage
        REDIS[(terra_cache\nValkey :6379\ncache + scoring)]
        PG[(terra_checkpoints\nPostgres :5432\nLangGraph state)]
        CHROMA[(chromadb-vector\nChromaDB :8001)]
        NEO4J[(neo4j-knowledge\nNeo4j :7687/:7474)]
        KAFKA[moe-kafka\nKafka :9092\nKRaft mode]
    end

    subgraph Tools
        MCP[mcp-precision\nMCP Server :8003\n26 tools]
        SEARX[SearXNG\nexternal / self-hosted]
        MINIO[(moe-storage\nMinIO :9000\nS3 object store)]
    end

    subgraph GPU_Inference
        INF1[Inference Server 1\nconfigured via\nINFERENCE_SERVERS]
        INF2[Inference Server 2\noptional]
    end

    subgraph Observability
        PROM[moe-prometheus :9090]
        GRAF[moe-grafana :3001]
        NODE[node-exporter :9100]
        CADV[cadvisor :9338]
    end

    subgraph Admin
        ADMUI[moe-admin :8088]
        DPROXY[docker-socket-proxy\n:2375 read-only]
        DOCS[moe-docs :8098\nMkDocs]
        DOZZLE[moe-dozzle :9999\nLog Viewer]
    end

    subgraph SSO["External: authentik_network"]
        AUTK[Authentik\nOIDC/SSO]
    end

    CC & OC & CD & CU & OWU -->|"OpenAI API HTTPS"| NGINX
    NGINX -->|":8002"| ORCH
    NGINX -->|":8088"| ADMUI
    NGINX -->|":80/:443"| CADDY
    CADDY --> DOCS & DOZZLE

    ORCH --> REDIS
    ORCH --> PG
    ORCH --> CHROMA
    ORCH --> NEO4J
    ORCH --> KAFKA
    ORCH --> MCP
    ORCH --> SEARX
    MCP -->|"S3 PUT / presign"| MINIO
    NGINX -->|"files.<domain>"| MINIO
    ORCH --> INF1
    ORCH -.-> INF2

    ADMUI --> ORCH
    ADMUI --> DPROXY
    ADMUI --> PROM
    ADMUI --> REDIS
    ADMUI --> PG
    ADMUI -.->|"OIDC server-side"| AUTK

    PROM -->|"scrape /metrics"| ORCH
    PROM --> NODE
    PROM --> CADV
    GRAF --> PROM

    KAFKA -.->|"moe.ingest consumer"| ORCH
    KAFKA -.->|"moe.feedback consumer"| ORCH
```

### Kafka Topics

| Topic | Publisher | Consumer | Payload Fields |
|---|---|---|---|
| `moe.ingest` | orchestrator (merger_node), `/v1/memory/ingest` endpoint | orchestrator (consumer loop) | `input`, `answer`, `domain`, `source_expert`, `source_model`, `confidence`, `knowledge_type`, `synthesis_insight` |
| `moe.requests` | orchestrator (merger_node) | orchestrator (log) | `response_id`, `input`, `answer`, `expert_models_used`, `cache_hit`, `ts` |
| `moe.feedback` | orchestrator (feedback endpoint) | orchestrator (score worker) | `response_id`, `rating`, `positive`, `ts` |
| `moe.linting` | any external trigger | orchestrator (consumer loop → `run_graph_linting`) | `{}` (empty payload sufficient) |

The `knowledge_type` field on `moe.ingest` distinguishes **factual** (entities, measurements, definitions) from **procedural** (action→location requirements, causal chains) ingests. The Graph Ingest LLM adapts its extraction strategy accordingly.

The optional `synthesis_insight` field carries a JSON object `{summary, entities, insight_type}` when the merger produced a novel multi-source synthesis. The consumer creates a `:Synthesis` node in Neo4j for it. See [Graph-basierte Wissensakkumulation](intelligence/compounding_knowledge.md).

The `source_expert` field carries the dominant expert category (e.g. `"medical_consult"`, `"code_reviewer"`) that produced the response. The consumer forwards it to `extract_and_ingest()` and `ingest_synthesis()` as `expert_domain`, tagging all resulting Neo4j nodes and relations. See [Memory Palace](intelligence/memory_palace.md).

---

## Caching Architecture

```mermaid
graph TD
    Q([Query]) --> L1

    L1{L1: ChromaDB\nSemantic Cache\ncosine distance}
    L1 -->|< 0.15 hard hit| DONE([Return cached response\nno LLM calls])
    L1 -->|0.15–0.50 soft hit| FEW[Few-shot examples\ninjected into experts]
    L1 -->|> 0.50 miss| SROUTE[semantic_router\nprototype matching]

    SROUTE -->|direct expert match| SKIP_PLAN[Skip planner LLM]
    SROUTE -->|no match| L2

    L2{"L2: Valkey\nPlan Cache\nmoe:plan:sha256[:16]"}
    L2 -->|TTL 30 min hit| SKIP_PLAN
    SKIP_PLAN --> L3
    L2 -->|miss| PLAN_LLM[Planner LLM call]
    PLAN_LLM -->|write-back| L2

    L3{"L3: Valkey\nGraphRAG Cache\nmoe:graph:sha256[:16]"}
    L3 -->|TTL 1h hit| SKIP_NEO4J[Skip Neo4j query\n1–3s saved]
    L3 -->|miss| NEO4J_Q[Neo4j query\n+ procedural traversal]
    NEO4J_Q -->|write-back| L3

    SKIP_NEO4J --> L4

    L4{L4: Valkey\nPerformance Scores\nmoe:perf:model:category}
    L4 -->|Laplace-smoothed\nscore ≥ 0.3| TIER1[Prefer high-scoring\nT1 model]
    L4 -->|score < 0.3| TIER2[Fallback to T2]

    style L1 fill:#1e3a5f,color:#fff
    style L2 fill:#3a1e5f,color:#fff
    style L3 fill:#1e5f3a,color:#fff
    style L4 fill:#5f3a1e,color:#fff
    style DONE fill:#1a4a1a,color:#fff
```

### Cache Key Reference

| Cache | Key Pattern | TTL | Storage |
|---|---|---|---|
| Semantic cache | ChromaDB collection `moe_fact_cache` | permanent (flagged if bad) | ChromaDB — metadata: `ts`, `input`, `flagged`, `expert_domain` |
| Routing prototypes | ChromaDB collection `task_type_prototypes` | permanent | ChromaDB |
| Plan cache | `moe:plan:{sha256(query[:300])[:16]}` | 30 min | Valkey |
| GraphRAG cache | `moe:graph:{sha256(query[:200]+categories)[:16]}` | 1 h | Valkey |
| Perf scores | `moe:perf:{model}:{category}` | permanent | Valkey Hash |
| Response metadata | `moe:response:{response_id}` | 7 days | Valkey Hash |
| Few-shot examples | `moe:few_shot:{category}` | permanent (max 20 LRU) | Valkey List |
| Planner patterns | `moe:planner_success` (sorted set) | 180 days | Valkey ZSet |
| Ontology gaps | `moe:ontology_gaps` (sorted set) | 90 days | Valkey ZSet |
| Healer state | `moe:maintenance:ontology:dedicated` | permanent | Valkey Hash |
| Healer run history | `moe:maintenance:ontology:runs` | max 200 entries | Valkey List |

---

## Ontology Gap Healer

Unknown terms collected during inference are stored in the `moe:ontology_gaps` sorted set (score = Unix timestamp). The **Ontology Gap Healer** (`scripts/gap_healer_templates.py`) processes this queue using an MoE curator template and writes classified entities to Neo4j.

Two modes are supported:

- **One-shot** (`type=oneshot`): processes the current queue once, then exits.
- **Dedicated daemon** (`type=dedicated`): runs continuously in a loop. The `auto_restart` flag in the `moe:maintenance:ontology:dedicated` Redis hash ensures a new subprocess is spawned ~30 s after each batch completes. On container restart, `_auto_resume_dedicated_healer()` detects `auto_restart=1` and resumes automatically after a 5-second ASGI warmup delay.

An internal watchdog task (`_watchdog_dedicated_healer`) runs every 60 s and:
1. Verifies PID liveness via `os.kill(pid, 0)` — triggers restart if the process is dead.
2. Detects stalls (no Redis counter update for 5 min) and marks `stalled=1`.

Completed runs (both modes) are appended to `moe:maintenance:ontology:runs` and visible under **Admin → Statistics → Healer-Historie**.

---

## State Persistence

LangGraph's `AsyncPostgresSaver` writes every run's checkpointed state to a **dedicated Postgres instance** (`terra_checkpoints`, Postgres 17, port 5432). Each node transition serializes the full `AgentState` into the `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` tables, keyed by thread ID.

Why Postgres and not Valkey? The earlier `AsyncRedisSaver` implementation relies on RediSearch (`FT.CREATE`, `FT._LIST`) to index checkpoint metadata. RediSearch is not part of Valkey proper, and the drop-in `valkey-search` module requires AVX2 CPU instructions that are unavailable on the current deployment hardware. Postgres avoids that constraint, offers ACID guarantees for concurrent session writes, and is trivial to back up.

Valkey (`terra_cache`) retains responsibility for all ephemeral and derived state: plan cache, GraphRAG cache, performance scores, few-shot examples, session metadata, and the `moe:active:*` live-request registry. See the [Caching Architecture](#caching-architecture) section above for cache key layout.

---

## Expert Routing

```mermaid
flowchart LR
    PLAN([Plan Tasks]) --> SEL

    SEL{Category\nin plan?}
    SEL -->|precision_tools| MCP[MCP Node\n26 deterministic tools]
    SEL -->|research| WEB[Research Node\nSearXNG]
    SEL -->|math| MATH[Math Node\nSymPy]
    SEL -->|expert category| ROUTE

    ROUTE{Expert\nRouting}
    ROUTE -->|forced ensemble| BOTH[T1 + T2\nin parallel]
    ROUTE -->|normal| T1[Tier 1\n≤20B params\nfast]

    T1 -->|confidence == hoch| MERGE_CHECK
    T1 -->|confidence < hoch| T2[Tier 2\n>20B params\nhigh quality]
    T2 --> MERGE_CHECK

    MERGE_CHECK{Merger\nFast-Path\ncheck}
    MERGE_CHECK -->|1 expert, hoch\nno web/mcp/graph| FP[⚡ Fast-Path\nskip Judge LLM\n1,500–4,000 tokens saved]
    MERGE_CHECK -->|multi / ensemble\nor extra context| JUDGE[Judge LLM\nsynthesis]
```

### Expert Categories

| Category | Planner Trigger Keywords | Tier Preference |
|---|---|---|
| `general` | General knowledge questions, definitions, explanations | T1 |
| `math` | Calculation, equation, formula, statistics | T1 |
| `technical_support` | IT, server, Docker, network, debugging, DevOps | T1 |
| `creative_writer` | Writing, creativity, storytelling, marketing | T1 |
| `code_reviewer` | Code, programming, review, security, refactoring | T1 |
| `medical_consult` | Medicine, symptoms, diagnosis, medication | T1 |
| `legal_advisor` | Law, statute, BGB, StGB, contract, judgments | T1 |
| `translation` | Translate, language, translation | T1 |
| `reasoning` | Analysis, logic, complex argumentation, strategy | T2 |
| `vision` | Image, screenshot, document, photo, recognition | T2 |
| `data_analyst` | Data, CSV, table, visualization, pandas | T1 |
| `science` | Chemistry, biology, physics, environment, research | T1 |

---

## AgentState

The LangGraph state object passed through all nodes:

| Field | Type | Description |
|---|---|---|
| `input` | `str` | Original user query (after skill resolution) |
| `response_id` | `str` | UUID for feedback tracking |
| `mode` | `str` | Active mode: `default`, `code`, `concise`, `agent`, `agent_orchestrated`, `research`, `report`, `plan` |
| `system_prompt` | `str` | Client system prompt — carries file context for coding agents (see [Context Extension](intelligence/context_extension.md)) |
| `plan` | `List[Dict]` | `[{task, category, search_query?, mcp_tool?, mcp_args?}]` |
| `complexity_level` | `str` | `trivial` / `moderate` / `complex` — from heuristic estimator (no LLM call) |
| `expert_results` | `List[str]` | Accumulated expert outputs (reducers: `operator.add`) |
| `expert_models_used` | `List[str]` | `["model::category", ...]` for metrics |
| `web_research` | `str` | SearXNG results with inline citations |
| `cached_facts` | `str` | ChromaDB hard cache hit content |
| `cache_hit` | `bool` | True if hard cache hit — skips most nodes |
| `math_result` | `str` | SymPy output |
| `mcp_result` | `str` | MCP precision tool output |
| `graph_context` | `str` | Neo4j entity + relation context; may include `[Procedural Requirements]` block |
| `final_response` | `str` | Synthesized answer from merger |
| `prompt_tokens` | `int` | Cumulative across all nodes (reducer: `operator.add`) |
| `completion_tokens` | `int` | Cumulative across all nodes |
| `chat_history` | `List[Dict]` | Compressed conversation turns |
| `reasoning_trace` | `str` | Chain-of-thought from `thinking_node` |
| `soft_cache_examples` | `str` | Few-shot examples from soft cache |
| `images` | `List[Dict]` | Extracted image blocks for vision expert |
| `judge_model_override` | `str` | Template-specific judge model (parsed from `model@endpoint`) |
| `planner_model_override` | `str` | Template-specific planner model |
| `user_experts` | `dict` | Per-user expert config override |
| `direct_expert` | `str` | Set by semantic_router — skips planner when a clear category match exists |
| `metadata_filters` | `Dict` | Optional domain filters extracted by planner from first task; used by `graph_rag_node` for scoped ChromaDB `where` clause retrieval |

---

## Operation Modes

| Mode ID | Model String | Purpose | Special Behaviour |
|---|---|---|---|
| `default` | `moe-orchestrator` | Complete answers with explanation | Full pipeline |
| `code` | `moe-orchestrator-code` | Source code only | No explanations, no CONFIDENCE block |
| `concise` | `moe-orchestrator-concise` | Short, precise (≤120 words) | Expert max 4 sentences |
| `agent` | `moe-orchestrator-agent` | Coding agents (OpenCode, Continue.dev) | `force_categories=[code_reviewer, technical_support]`, no `<think>` wrapper |
| `agent_orchestrated` | `moe-orchestrator-agent-orchestrated` | Claude Code — full pipeline | `force_think=True`, all experts available, no `<think>` SSE wrapper |
| `research` | `moe-orchestrator-research` | Deep research report | `force_think=True`, web research prioritized |
| `report` | `moe-orchestrator-report` | Structured markdown report | `force_think=True`, section headings enforced |
| `plan` | `moe-orchestrator-plan` | Plan & Execute | Shows full execution plan, `force_think=True` |

---

## Configuration Reference

### Core

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_SERVERS` | `""` | JSON array of server configs — set via Admin UI |
| `JUDGE_MODEL` | `magistral:24b` | Default merger/judge model name |
| `JUDGE_ENDPOINT` | — | Which inference server runs the judge LLM |
| `PLANNER_MODEL` | `phi4:14b` | Model for task decomposition |
| `PLANNER_ENDPOINT` | — | Which inference server runs the planner |
| `GRAPH_INGEST_MODEL` | `""` | Dedicated model for background GraphRAG extraction — falls back to judge if empty |
| `GRAPH_INGEST_ENDPOINT` | `""` | Inference server for the graph ingest LLM |
| `EXPERT_MODELS` | `{}` | JSON: expert category → model list (set via Admin UI) |
| `MCP_URL` | `http://mcp-precision:8003` | MCP precision tools server |
| `SEARXNG_URL` | — | SearXNG instance for web research |

### Caching & Thresholds

| Variable | Default | Description |
|---|---|---|
| `CACHE_HIT_THRESHOLD` | `0.15` | ChromaDB cosine distance for hard cache hit |
| `SOFT_CACHE_THRESHOLD` | `0.50` | Distance threshold for few-shot examples |
| `SOFT_CACHE_MAX_EXAMPLES` | `2` | Max few-shot examples per query |
| `CACHE_MIN_RESPONSE_LEN` | `150` | Min chars to store a response in cache |
| `MAX_EXPERT_OUTPUT_CHARS` | `2400` | Max chars per expert output (~600 tokens) |

### Expert Routing

| Variable | Default | Description |
|---|---|---|
| `EXPERT_TIER_BOUNDARY_B` | `20` | GB parameter threshold for Tier 1 vs Tier 2 |
| `EXPERT_MIN_SCORE` | `0.3` | Laplace score threshold to consider a model |
| `EXPERT_MIN_DATAPOINTS` | `5` | Minimum feedback points before score is used |

### History & Timeouts

| Variable | Default | Description |
|---|---|---|
| `HISTORY_MAX_TURNS` | `4` | Conversation turns to include |
| `HISTORY_MAX_CHARS` | `3000` | Max total history chars |
| `JUDGE_TIMEOUT` | `900` | Merger/judge LLM timeout (seconds) |
| `EXPERT_TIMEOUT` | `900` | Expert model timeout (seconds) |
| `PLANNER_TIMEOUT` | `300` | Planner timeout (seconds) |

### Claude Code / Agent Integration

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_CODE_PROFILES` | `[]` | JSON array of integration profiles (set via Admin UI) |
| `CLAUDE_CODE_MODELS` | (8 claude-* IDs) | Comma-separated Anthropic model IDs to route through MoE |
| `TOOL_MAX_TOKENS` | `8192` | Max tokens for tool-use responses |
| `REASONING_MAX_TOKENS` | `16384` | Max tokens for extended thinking responses |

### Infrastructure

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://terra_cache:6379` | Valkey connection string (`redis://` is the protocol scheme) |
| `POSTGRES_CHECKPOINT_URL` | `postgresql://langgraph:***@terra_checkpoints:5432/langgraph` | Dedicated Postgres for LangGraph `AsyncPostgresSaver` checkpoints |
| `NEO4J_URI` | `bolt://neo4j-knowledge:7687` | Neo4j Bolt endpoint |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASS` | — | Neo4j password |
| `KAFKA_URL` | `kafka://moe-kafka:9092` | Kafka broker |

---

## API Endpoints

### Orchestrator (`:8002`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | Main chat endpoint (OpenAI-compatible, streaming SSE) |
| `POST` | `/v1/messages` | Anthropic Messages API format |
| `GET` | `/v1/models` | List all modes as model IDs |
| `POST` | `/v1/feedback` | Submit rating (1–5) for a response |
| `GET` | `/v1/provider-status` | Rate-limit status for Claude Code |
| `GET` | `/metrics` | Prometheus metrics scrape endpoint |
| `GET` | `/graph/stats` | Neo4j entity/relation counts |
| `GET` | `/graph/search?q=term` | Semantic search in knowledge graph |
| `POST` | `/v1/memory/ingest` | Persist session summary / key decisions from external hooks (Claude Code) into the knowledge base via Kafka |
| `GET` | `/v1/admin/ontology-gaps` | Unknown terms found in queries (Valkey ZSet) |
| `GET` | `/v1/admin/planner-patterns` | Learned expert-combination patterns |

### Admin UI (`:8088`)

| Path | Description |
|---|---|
| `/` | Dashboard — system config & model assignment |
| `/profiles` | Claude Code integration profiles |
| `/skills` | Skill management (CRUD + upstream sync) |
| `/servers` | Inference server health & model list |
| `/mcp-tools` | MCP tool enable/disable |
| `/monitoring` | Prometheus/Grafana integration |
| `/tool-eval` | Tool invocation logs |
| `/users` | User management |
| `/expert-templates` | Expert template CRUD with `model@endpoint` assignment |

---

## Performance Optimizations

| Optimization | Savings | Condition |
|---|---|---|
| ChromaDB hard cache | Full pipeline skip | Cosine distance < 0.15 |
| Semantic pre-router | Planner LLM skipped | Clear query–category prototype match |
| Valkey plan cache (TTL 30 min) | ~1,600 tokens, 2–5 s | Same query within 30 min |
| Valkey GraphRAG cache (TTL 1 h) | 1–3 s, Neo4j query | Same query+categories within 1 h |
| Merger Fast-Path | ~1,500–4,000 tokens, 3–8 s | 1 expert + `hoch` + no extra context |
| Complexity routing (trivial) | T2, research, graph all skipped | ≤15-word simple factual queries |
| Query normalization | +20–30% cache hit rate | Lowercase + strip punctuation before lookup |
| History compression | ~600–1,800 tokens | History > 2,000 chars → old turns → `[…]` |
| Two-tier routing | T2 LLM call skipped | T1 expert returns `hoch` confidence |
| VRAM unload after inference | VRAM freed for judge | Async `keep_alive=0` after each expert |
| Soft cache few-shot | Better accuracy without hard hit | Distance 0.15–0.50 → in-context examples |
| Feedback-driven scoring | Optimal model selection | Laplace score from user feedback |
| Ingest semaphore (limit 2) | Prevents GPU saturation | Background ingest concurrent calls capped |
