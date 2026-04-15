# Data Flow & Caching

## Normal Request Flow

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI :8002
    participant Cache as ChromaDB/Valkey
    participant Router as Semantic Router
    participant Planner as Planner LLM
    participant Experts as Expert Workers (parallel)
    participant Graph as Neo4j GraphRAG
    participant Merger as Merger LLM
    participant Kafka

    Client->>+API: POST /v1/chat/completions
    API->>+Cache: L1 — ChromaDB semantic search
    Cache-->>-API: Miss (distance > 0.15)

    API->>+Router: Prototype matching (ChromaDB)
    Router-->>-API: No direct match → proceed to planner

    API->>+Cache: L2 — Valkey plan cache
    Cache-->>-API: Miss → call Planner LLM
    API->>+Planner: Decompose request (complexity routing)
    Planner-->>-API: Plan [{task, category, metadata_filters?}]
    Note over Planner,API: metadata_filters stored in AgentState<br/>(e.g. {expert_domain: "code_reviewer"})
    API->>Cache: Write plan to Valkey (TTL 30 min)

    par Fan-Out (parallel)
        API->>+Experts: Expert Worker 1
        API->>Experts: Expert Worker 2
        API->>Experts: MCP Tools (deterministic)
        API->>Experts: SearXNG Research
        API->>+Graph: L3 — Valkey GraphRAG cache?
        Graph-->>-API: Miss → Neo4j 2-hop + procedural traversal
        Graph->>Cache: Filtered ChromaDB query (if metadata_filters set)
        Cache-->>Graph: [Domain-Filtered Memory]
    end
    Experts-->>-API: Results + CONFIDENCE values
    Graph-->>API: [Knowledge Graph] + [Procedural Requirements] + [Domain-Filtered Memory?]

    API->>+Merger: Synthesize — graph context annotated as hard facts
    Merger-->>-API: Final response

    API->>Cache: Write response to ChromaDB (+ expert_domain metadata)
    API->>+Kafka: moe.requests (audit) + moe.ingest (knowledge_type + source_expert tagged)
    Kafka-->>-API: ack
    API-->>Client: SSE stream
```

---

## Cache Hit Fast Path

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI :8002
    participant Chroma as ChromaDB

    Client->>+API: POST /v1/chat/completions
    API->>+Chroma: Vector search (cosine similarity)
    Chroma-->>-API: Hit! (distance < 0.15)
    API-->>-Client: ⚡ Direct response (no LLM calls)
    Note over API,Chroma: Typically < 50 ms
```

---

## Semantic Pre-Router Fast Path

```mermaid
sequenceDiagram
    participant Client
    participant API as FastAPI :8002
    participant Chroma as ChromaDB (prototypes)
    participant Expert as Expert Worker

    Client->>+API: POST /v1/chat/completions
    API->>+Chroma: L1 miss — check routing prototypes
    Chroma-->>-API: Strong match (gap > 0.10): code_reviewer
    Note over API: Planner skipped — direct_expert set
    API->>+Expert: code_reviewer with T1 model
    Expert-->>-API: high confidence → Fast-Path merger
    API-->>-Client: ⚡ Response (planner + judge LLMs skipped)
```

---

## GraphRAG Ingest — Causal Learning Loop & Synthesis Persistence

```mermaid
flowchart TD
    Response["Merger LLM output\n(raw res.content)"] --> SynthDetect

    SynthDetect{"SYNTHESIS_INSIGHT\nblock present?\n(regex scan)"}
    SynthDetect -->|yes| StripTag["Strip tag → res_content_clean\nparse JSON → _synthesis_payload"]
    SynthDetect -->|no| KWDetect
    StripTag --> KWDetect

    KWDetect{"Knowledge-type\nheuristic\n(keyword scan on\nres_content_clean)"}
    KWDetect -->|action + location\nkeywords found| PKafka[("Kafka moe.ingest\nknowledge_type=procedural\nsynthesis_insight=…|null")]
    KWDetect -->|no action markers| FKafka[("Kafka moe.ingest\nknowledge_type=factual\nsynthesis_insight=…|null")]

    PKafka --> Consumer["Kafka Consumer\n(async, decoupled)"]
    FKafka --> Consumer

    Consumer --> ExpertDomain["Extract source_expert\nfrom payload\n(expert_domain tag)"]
    ExpertDomain --> SynthCheck{"synthesis_insight\nfield present?"}
    SynthCheck -->|yes| SynthIngest["ingest_synthesis(expert_domain=…)\nMERGE :Synthesis {expert_domain}\nMERGE (s)-[:RELATED_TO]->(e)"]
    SynthCheck -->|no / null| IngestLLM
    SynthIngest --> IngestLLM

    IngestLLM["Graph Ingest LLM\n(GRAPH_INGEST_MODEL or\nfallback: Judge LLM)\nSemaphore(2) — max 2 concurrent"]

    IngestLLM --> ExtractPrompt["Extended extraction prompt\n— factual triples (IS_A, TREATS, …)\n— procedural triples\n  (NECESSITATES_PRESENCE,\n   DEPENDS_ON_LOCATION,\n   ENABLES_ACTION)"]

    ExtractPrompt --> TypeDetect{"Auto-detect\nknowledge_type\nfrom extracted triples"}
    TypeDetect -->|procedural rel found| PTriples["Procedural triples\n(Action → Location,\n Condition → Action)"]
    TypeDetect -->|factual rels only| FTriples["Factual triples\n(Drug → Disease, …)"]

    PTriples --> Neo4j[("Neo4j\nMERGE — idempotent\nversioned provenance\n+ expert_domain property")]
    FTriples --> Neo4j
    SynthIngest --> Neo4j

    Neo4j --> GraphRAGNode["graph_rag_node\n(next request)\n2-hop traversal\n+ _query_procedural_requirements\n+ :Synthesis nodes via RELATED_TO"]
    GraphRAGNode --> ProcBlock["[Procedural Requirements]\nblock in graph_context"]
    ProcBlock --> MergerAnnotation["Merger receives structured\nknowledge + prior syntheses"]
```

### Knowledge-Type Classification

| `knowledge_type` | Meaning | Example |
|---|---|---|
| `factual` | Entity properties, measurements, taxonomies | "Ibuprofen TREATS Pain" |
| `procedural` | Action→location requirements, enabling conditions | "CarWashing NECESSITATES_PRESENCE CarWashFacility" |

The classification happens at two points:

1. **At publish** (`merger_node`): keyword heuristic on the final response — presence of terms like *requires*, *necessitates*, *on-site*, *physically* (the actual production set also contains German equivalents to support multilingual queries)
2. **At ingest** (`extract_and_ingest`): auto-overrides to `procedural` if any extracted triple uses a procedural relation type

### Synthesis Detection

Before the knowledge-type heuristic runs, the merger response is scanned for a `<SYNTHESIS_INSIGHT>` block. If found:

- The block is parsed into a JSON object with `summary`, `entities`, and `insight_type`
- It is stripped from the user-facing response (`res_content_clean`)
- It is attached to the `moe.ingest` payload as `synthesis_insight`

The consumer then calls `graph_manager.ingest_synthesis()` **in parallel** with the normal triple extraction. The two paths are independent — a synthesis can exist alongside extracted triples from the same response.

See [Graph-basierte Wissensakkumulation](../intelligence/compounding_knowledge.md) for full details.

---

## Feedback Loop (Valkey → Expert Scoring)

```mermaid
flowchart TD
    User["👤 User\nRating 1–5"] --> FeedbackAPI["POST /v1/feedback"]
    FeedbackAPI --> Valkey[("Valkey\nPerformance Scores\nmoe:perf:model:category")]
    Valkey --> Routing["Expert Routing\n(Laplace Smoothing)"]
    Routing --> BetterSelection["Better model\nselection on\nnext request"]

    FeedbackAPI --> FewShotDB[("Valkey\nFew-Shot Examples\nmoe:few_shot:category")]
    FewShotDB --> ExpertPrompts["Expert Prompts\n(automatically improved)"]

    FeedbackAPI --> PlannerDB[("Valkey\nPlanner Patterns\nmoe:planner_success")]
    PlannerDB --> PlannerHint["Planner prefers proven\nexpert combinations"]
```

The feedback system operates on three levels:

1. **Performance scores** — Model ratings per category (Laplace-smoothed)
   - Rating ≥ 4 → score increases; T1 stays preferred
   - Rating ≤ 2 → score decreases, T2 escalation more likely

2. **Few-shot examples** — Good responses (rating 5) stored in Valkey + file
   - Injected into expert prompts for similar future queries
   - Self-correction loop catches numeric discrepancies before they propagate

3. **Planner patterns** — Successful expert combinations stored as signatures
   - `"code_reviewer+technical_support"` → planner reuses this pattern for similar queries

---

## Self-Correction Mechanism

```mermaid
flowchart LR
    Good["Rating 5\n(best response)"] --> FewShot[("Valkey + File\nFew-Shot Store")]
    FewShot --> ExpertContext["Expert Context\n(injected for similar queries)"]
    ExpertContext --> BetterAnswer["Higher quality\nresponse"]
    BetterAnswer --> Good

    Bad["Rating 1–2\n(bad response)"] --> LowerScore["Performance score ↓"]
    LowerScore --> T2Escalation["T2 model preferred\nnext time"]
    T2Escalation --> BetterAnswer

    Num["Numeric mismatch\ndetected (Δ > 1%)"] --> SaveExample["self_correction.py\nsave_few_shot()"]
    SaveExample --> FewShot
```

### Numeric Mismatch Detection

`self_correction.py` runs as a background task after every merger response:

1. Extracts all numbers from the original query and the final response
2. Compares relative differences (flags Δ > 1% but < 1000% to exclude units)
3. Saves correction examples (Valkey `moe:few_shot:{category}`, max 20 LRU + file `/opt/moe-infra/few_shot_examples/*.md`)
4. These examples surface in the planner prompt as *"known error patterns — avoid these"*

---

## Complexity Routing

```mermaid
flowchart TD
    Query["Incoming Query"] --> Heuristic["complexity_estimator.py\n(no LLM call)"]

    Heuristic --> Trivial{"≤ 15 words\nfactual Q (what is, define)"}
    Heuristic --> Moderate{"16–79 words\ncode block or domain marker"}
    Heuristic --> Complex{"≥ 80 words OR\nmulti-step marker\n(compare, analyze, design, …)"}

    Trivial -->|trivial| T_Path["1 T1 expert only\nskip: research, graph,\nthinking, T2"]
    Moderate -->|moderate| M_Path["Planner + 2–4 experts\nskip: thinking"]
    Complex -->|complex| C_Path["Full pipeline\nplanner + all experts\n+ thinking node"]
```

| Level | Max Tasks | Research | Graph | Thinking | T2 Allowed |
|---|---|---|---|---|---|
| `trivial` | 1 | ✗ | ✗ | ✗ | ✗ |
| `moderate` | 2 | ✓ | ✓ | ✗ | ✓ |
| `complex` | 4 | ✓ | ✓ | ✓ | ✓ |

---

## Admin UI — Configuration & Live Monitoring Flow

The Admin UI (`moe-admin`) interacts with multiple backends for configuration management
and live process monitoring.

```mermaid
sequenceDiagram
    participant Admin as Browser (Admin UI)
    participant AdminSvc as moe-admin :8088
    participant ENV as .env file (shared rw mount)
    participant Valkey as terra_cache (Valkey)
    participant Prom as moe-prometheus :9090
    participant Orch as langgraph-orchestrator :8002
    participant Docker as docker-socket-proxy :2375

    Note over Admin,AdminSvc: Configuration Save
    Admin->>+AdminSvc: POST /save (form data)
    AdminSvc->>ENV: write_env(updates) → .env
    AdminSvc->>Orch: restart_orchestrator() via docker-socket-proxy
    AdminSvc->>Orch: _sync_authentik_redirect_uris() (async task)
    AdminSvc-->>-Admin: redirect /dashboard (flash: saved)

    Note over Admin,AdminSvc: Live — Active Requests
    Admin->>+AdminSvc: GET /api/live/active-requests
    AdminSvc->>Valkey: KEYS moe:admin:active:*
    AdminSvc->>Valkey: HGETALL per key (meta: user, model, started_at, …)
    AdminSvc-->>-Admin: JSON [{request_id, username, model, …}]

    Note over Admin,AdminSvc: Live — Process History
    Admin->>+AdminSvc: GET /api/live/completed-requests
    AdminSvc->>Valkey: ZREVRANGE moe:admin:completed 0 N
    AdminSvc-->>-Admin: JSON [{started_at, ended_at, duration, status, …}]

    Note over Admin,AdminSvc: Live — Container Status
    Admin->>+AdminSvc: GET /api/status
    AdminSvc->>Docker: GET /containers/json
    AdminSvc->>Orch: GET /v1/provider-status
    AdminSvc->>Prom: GET /api/v1/query (CPU/RAM metrics)
    AdminSvc-->>-Admin: JSON {containers, orchestrator_status, metrics}
```

### Process Lifecycle Tracking (Valkey)

The orchestrator registers and deregisters every request in Valkey:

| Event | Valkey Key | Operation |
|-------|-----------|-----------|
| Request start | `moe:admin:active:{request_id}` | `HSET` with user, model, started\_at, mode, type |
| Request end (ok) | `moe:admin:completed` (sorted set) | `ZADD` with score=timestamp; `DEL` active key |
| Request killed | `moe:admin:completed` (sorted set) | `ZADD` with `ended_at` + `status=killed` |

The Admin UI live monitor polls `/api/live/active-requests` every 5 seconds and
`/api/live/completed-requests` every 10 seconds.

---

## Claude Code Auto-Save Hook Flow

External tools such as Claude Code can persist session knowledge before context is
compacted or the session ends. Two hook scripts (`hooks/mempal_precompact_hook.sh`,
`hooks/mempal_save_hook.sh`) POST to the `/v1/memory/ingest` endpoint, which enqueues
the content on `moe.ingest` for the same async GraphRAG pipeline used internally.

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant Hook as Hook Script
    participant API as POST /v1/memory/ingest :8002
    participant Kafka as moe.ingest
    participant Consumer as Kafka Consumer
    participant GM as GraphRAGManager
    participant Neo4j

    CC->>Hook: PreCompact event (stdin: {summary, ...})<br/>or Stop event (stdin: {transcript, ...})
    Hook->>Hook: Extract summary text from stdin JSON
    Hook->>API: POST {session_summary, key_decisions, domain}
    API-->>Hook: 200 {"status":"queued"} (async — no waiting)
    API->>Kafka: publish moe.ingest payload<br/>{source_expert:"session", knowledge_type:"factual"}

    Note over Kafka,Consumer: asynchronous — decoupled from hook caller

    Kafka->>Consumer: consume message
    Consumer->>GM: extract_and_ingest(answer=summary,<br/>expert_domain="session")
    GM->>Neo4j: MERGE :Entity + relations<br/>(expert_domain="session")
```

The `expert_domain` is set to `"session"` for all hook-sourced ingests, keeping them
in a dedicated namespace that can be queried or filtered independently from expert-generated knowledge.

See [Memory Palace — Auto-Save Hooks](../intelligence/memory_palace.md#feature-3--claude-code-auto-save-hooks) for full setup and reference.
