# MoE Sovereign — System Architecture

## Overview

MoE Sovereign is a LangGraph-based Multi-Model Orchestrator. Each incoming query is decomposed by a planner LLM into typed tasks, routed to specialist models in parallel, enriched with knowledge graph context and optional web research, then synthesized by a judge LLM into a single coherent response.

All caching is multi-layered: semantic vector cache (ChromaDB), plan cache (Valkey), GraphRAG cache (Valkey), and performance-scored expert routing (Valkey). The API is fully OpenAI-compatible.

---

## LangGraph Pipeline

```mermaid
flowchart TD
    IN([Client Request]) --> CACHE

    CACHE{cache_lookup\nChromaDB semantic\nhit < 0.15}
    CACHE -->|HIT ⚡| MERGE
    CACHE -->|MISS| PLAN

    PLAN[planner\nphi4:14b\nValkey plan cache\nTTL 30 min]
    PLAN --> PAR

    subgraph PAR [Parallel Execution]
        direction LR
        W[workers\nTier 1 + Tier 2\nexpert models]
        R[research\nSearXNG\nweb search]
        M[math\nSymPy\ncalculation]
        MCP[mcp\nPrecision Tools\n20 deterministic tools]
        GR[graph_rag\nNeo4j\nValkey cache TTL 1h]
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

    style CACHE fill:#1e3a5f,color:#fff
    style MERGE fill:#1e3a5f,color:#fff
    style PAR fill:#0d2137,color:#ccc
    style FP fill:#1a4a1a,color:#fff
    style OUT fill:#2d1b4e,color:#fff
```

### Node Descriptions

| Node | Function | Key Logic |
|---|---|---|
| `cache_lookup` | ChromaDB semantic similarity | distance < 0.15 → hard hit; 0.15–0.50 → soft/few-shot examples |
| `planner` | Task decomposition (phi4:14b) | Produces `[{task, category, search_query?, mcp_tool?}]`; Valkey plan cache TTL=30 min |
| `workers` | Parallel expert execution | Two-tier routing; T1 (≤20B) first, T2 (>20B) only if T1 confidence < threshold |
| `research` | SearXNG web search | Single or multi-query deep search; always runs if `research` category in plan |
| `math` | SymPy calculation | Runs only if `math` category in plan AND no `precision_tools` task |
| `mcp` | MCP Precision Tools | 20 deterministic tools via HTTP; runs if `precision_tools` in plan |
| `graph_rag` | Neo4j knowledge graph | Entity + relation context; Valkey cache TTL=1h |
| `research_fallback` | Conditional extra search | Triggers if merger needs more context |
| `thinking` | Chain-of-thought reasoning | Generates `reasoning_trace`; activated by `force_think` modes |
| `merger` | Response synthesis (Judge LLM) | Fast-path bypasses Judge for single high-confidence experts |
| `critic` | Post-generation validation | Async self-evaluation; flags low-quality cache entries |

---

## Service Topology

```mermaid
graph LR
    subgraph Clients
        CC[Claude Code]
        OC[Open Code]
        CD[Continue.dev]
        CU[curl / any OpenAI client]
    end

    subgraph Core [:8002]
        ORCH[langgraph-orchestrator\nFastAPI + LangGraph]
    end

    subgraph Storage
        REDIS[(terra_cache\nRedis Stack :6379)]
        CHROMA[(chromadb-vector\nChromaDB :8001)]
        NEO4J[(neo4j-knowledge\nNeo4j :7687/:7474)]
        KAFKA[moe-kafka\nKafka :9092]
    end

    subgraph Tools
        MCP[mcp-precision\nMCP Server :8003]
        SEARX[SearXNG\nexternal]
    end

    subgraph GPU_Inference
        RTX[Ollama RTX\nconfigured via\nINFERENCE_SERVERS]
        TESLA[Ollama Tesla\noptional]
    end

    subgraph Observability
        PROM[moe-prometheus :9090]
        GRAF[moe-grafana :3001]
        NODE[node-exporter :9100]
        CADV[cadvisor :9338]
    end

    subgraph Admin
        ADMUI[moe-admin :8088]
    end

    CC & OC & CD & CU -->|OpenAI API| ORCH

    ORCH --> REDIS
    ORCH --> CHROMA
    ORCH --> NEO4J
    ORCH --> KAFKA
    ORCH --> MCP
    ORCH --> SEARX
    ORCH --> RTX
    ORCH -.-> TESLA

    ADMUI --> ORCH
    ADMUI -->|/var/run/docker.sock| DOCKER[(Docker API)]
    ADMUI --> PROM

    PROM --> ORCH
    PROM --> NODE
    PROM --> CADV
    PROM --> GRAF

    KAFKA -->|moe.ingest| ORCH
    KAFKA -->|moe.feedback| ORCH
```

### Kafka Topics

| Topic | Publisher | Consumer | Purpose |
|---|---|---|---|
| `moe.ingest` | orchestrator | orchestrator | GraphRAG entity ingestion from responses |
| `moe.requests` | orchestrator | orchestrator | Audit log (input, answer snippet, models used) |
| `moe.feedback` | orchestrator | orchestrator | User ratings → plan pattern learning & model scoring |

---

## Caching Architecture

```mermaid
graph TD
    Q([Query]) --> L1

    L1{L1: ChromaDB\nSemantic Cache\ncosine distance}
    L1 -->|< 0.15 hard hit| DONE([Return cached response])
    L1 -->|0.15–0.50 soft hit| FEW[Few-shot examples\nfor experts]
    L1 -->|> 0.50 miss| L2

    L2{L2: Valkey\nPlan Cache\nmoe:plan:sha256[:16]}
    L2 -->|TTL 30 min hit| SKIP_PLAN[Skip planner LLM\n~1,600 tokens saved]
    L2 -->|miss| PLAN_LLM[Planner LLM call]
    PLAN_LLM -->|write-back| L2

    SKIP_PLAN --> L3

    L3{L3: Valkey\nGraphRAG Cache\nmoe:graph:sha256[:16]}
    L3 -->|TTL 1h hit| SKIP_NEO4J[Skip Neo4j query\n1–3s saved]
    L3 -->|miss| NEO4J_Q[Neo4j query]
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
| Semantic cache | ChromaDB collection `moe_fact_cache` | permanent (flagged if bad) | ChromaDB |
| Plan cache | `moe:plan:{sha256(query[:300])[:16]}` | 30 min | Valkey |
| GraphRAG cache | `moe:graph:{sha256(query[:200]+categories)[:16]}` | 1 h | Valkey |
| Perf scores | `moe:perf:{model}:{category}` | permanent | Valkey Hash |
| Response metadata | `moe:response:{response_id}` | 7 days | Valkey Hash |
| Planner patterns | `moe:planner_success` (sorted set) | 180 days | Valkey ZSet |
| Ontology gaps | `moe:ontology_gaps` (sorted set) | 90 days | Valkey ZSet |

---

## Expert Routing

```mermaid
flowchart LR
    PLAN([Plan Tasks]) --> SEL

    SEL{Category\nin plan?}
    SEL -->|precision_tools| MCP[MCP Node\ndeterministic]
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
| `system_prompt` | `str` | Client system prompt (e.g., file context from Claude Code) |
| `plan` | `List[Dict]` | `[{task, category, search_query?, mcp_tool?, mcp_args?}]` |
| `expert_results` | `List[str]` | Accumulated expert outputs (reducers: `operator.add`) |
| `expert_models_used` | `List[str]` | `["model::category", ...]` for metrics |
| `web_research` | `str` | SearXNG results with inline citations |
| `cached_facts` | `str` | ChromaDB hard cache hit content |
| `cache_hit` | `bool` | True if hard cache hit — skips most nodes |
| `math_result` | `str` | SymPy output |
| `mcp_result` | `str` | MCP precision tool output |
| `graph_context` | `str` | Neo4j entity + relation context |
| `final_response` | `str` | Synthesized answer from merger |
| `prompt_tokens` | `int` | Cumulative across all nodes (reducer: `operator.add`) |
| `completion_tokens` | `int` | Cumulative across all nodes |
| `chat_history` | `List[Dict]` | Compressed conversation turns |
| `reasoning_trace` | `str` | Chain-of-thought from `thinking_node` |
| `soft_cache_examples` | `str` | Few-shot examples from soft cache |
| `images` | `List[Dict]` | Extracted image blocks for vision expert |

---

## Configuration Reference

### Core

| Variable | Default | Description |
|---|---|---|
| `URL_RTX` | — | Ollama base URL for primary GPU (e.g., `http://192.168.1.10:11434/v1`) |
| `URL_TESLA` | — | Ollama base URL for secondary GPU (optional) |
| `INFERENCE_SERVERS` | `""` | JSON array of server configs (overrides URL_RTX/URL_TESLA) |
| `JUDGE_ENDPOINT` | `RTX` | Which server runs the judge/merger LLM |
| `PLANNER_MODEL` | `phi4:14b` | Model for task decomposition |
| `PLANNER_ENDPOINT` | `RTX` | Which server runs the planner |
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

### Claude Code Integration

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_CODE_PROFILES` | `[]` | JSON array of integration profiles (set via Admin UI) |
| `CLAUDE_CODE_MODELS` | (8 claude-* model IDs) | Comma-separated Anthropic model IDs to route through MoE |
| `TOOL_MAX_TOKENS` | `8192` | Max tokens for tool-use responses |
| `REASONING_MAX_TOKENS` | `16384` | Max tokens for extended thinking |

### Infrastructure

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://terra_cache:6379` | Redis connection |
| `NEO4J_URI` | `bolt://neo4j-knowledge:7687` | Neo4j Bolt endpoint |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASS` | `moe-sovereign` | Neo4j password |
| `KAFKA_URL` | `kafka://moe-kafka:9092` | Kafka broker |

---

## API Endpoints

### Orchestrator (`:8002`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/chat/completions` | Main chat endpoint (OpenAI-compatible, streaming) |
| `POST` | `/v1/messages` | Anthropic Messages API format |
| `GET` | `/v1/models` | List all modes as model IDs |
| `POST` | `/v1/feedback` | Submit rating (1–5) for a response |
| `GET` | `/v1/provider-status` | Rate-limit status for Claude Code |
| `GET` | `/metrics` | Prometheus metrics scrape |
| `GET` | `/graph/stats` | Neo4j entity/relation counts |
| `GET` | `/graph/search?q=term` | Semantic search in knowledge graph |
| `GET` | `/v1/admin/ontology-gaps` | Unknown terms found in queries |
| `GET` | `/v1/admin/planner-patterns` | Learned expert-combination patterns |

### Admin UI (`:8088`)

| Path | Description |
|---|---|
| `/` | Dashboard — system overview |
| `/profiles` | Claude Code integration profiles |
| `/skills` | Skill management (CRUD + upstream sync) |
| `/servers` | Inference server health & model list |
| `/mcp-tools` | MCP tool enable/disable |
| `/monitoring` | Prometheus/Grafana integration |
| `/tool-eval` | Tool invocation logs |

---

## Performance Optimizations

| Optimization | Savings | Condition |
|---|---|---|
| ChromaDB hard cache | Full pipeline skip | Cosine distance < 0.15 |
| Valkey plan cache (TTL 30 min) | ~1,600 tokens, 2–5 s | Same query within 30 min |
| Valkey GraphRAG cache (TTL 1 h) | 1–3 s, Neo4j query | Same query+categories within 1 h |
| Merger Fast-Path | ~1,500–4,000 tokens, 3–8 s | 1 expert + `hoch` + no extra context |
| Query normalization | +20–30% cache hit rate | Lowercase + strip punctuation before lookup |
| History compression | ~600–1,800 tokens | History > 2,000 chars → old turns → `[…]` |
| Two-tier routing | T2 LLM call skipped | T1 expert returns `hoch` confidence |
| VRAM unload after inference | VRAM freed for judge | Async `keep_alive=0` after each expert |
| Soft cache few-shot | Better accuracy without hit | Distance 0.15–0.50 → in-context examples |
| Feedback-driven scoring | Optimal model selection | Laplace score from user feedback |

---

## Formal Logic State Management

This section documents the transition from purely heuristic LLM-based routing
to a hybrid approach grounded in formal mathematical logic. The theoretical
foundation spans algebraic logic, algorithmic information theory, and Bayesian
statistics. All implementations are derived from peer-reviewed literature; no
formal logic primitive is introduced without an attributed mathematical basis.

---

### Scientific Foundation & Acknowledgement

The core algebraic framework is drawn from:

> **A. de Vries**, *"Algebraic hierarchy of logics unifying fuzzy logic and
> quantum logic"*, arXiv:0707.2161 \[math.LO\], 2007.
> <https://arxiv.org/abs/0707.2161>

Professor de Vries establishes that fuzzy logic is the most general framework
in an algebraic hierarchy — containing paraconsistent, quantum, intuitionistic,
and Boolean logics as special cases via lattice-theoretic embedding. This
unified view makes it possible to treat LLM routing and knowledge-graph state
management under a single, mathematically coherent theory rather than as
independent engineering heuristics.

The implementations in this system directly apply three of the four logic
layers de Vries formalises:

- **§2 — Paraconsistent logic:** contradictions between experts are tolerated
  and preserved rather than causing pipeline failure.
- **§3 — Intuitionistic logic (Heyting algebras):** LLM-generated claims are
  treated as unproven (⊥) until constructively verified by an executor.
- **§4 — Fuzzy logic (t-norms):** routing decisions use continuous confidence
  values in \[0, 1\] rather than binary flags.

Beyond the algebraic hierarchy, the following classical results are used:

| Author | Year | Result | Used for |
|---|---|---|---|
| K. Gödel | 1932 | Gödel t-norm `T_G(a,b) = min(a,b)` | Conservative routing conjunction |
| J. Łukasiewicz | 1920 | Łukasiewicz t-norm `T_Ł(a,b) = max(0, a+b−1)` | Tolerant routing conjunction |
| A. Kolmogorov | 1965 | Algorithmic information content (AIC) | Complexity estimation via zlib |
| G. Chaitin | 1966 | Kolmogorov complexity upper bound via compression | Complexity estimation |
| Ratcliff & Metzener | 1988 | Ratcliff/Obershelp string similarity | Fuzzy entity deduplication |
| A. de Vries | 2014 | Fuzzy profile matching via numerical attributes | Entity merging threshold model |

---

### 1 — Intuitionistic Logic — `ConstructiveProof[T]`

**Basis:** De Vries (2007), §3 — Heyting algebras.  
**Location:** `pipeline/logic_types.py`

A formula ϕ is valid in intuitionistic logic only when an explicit *proof
object* exists; the default truth value without a proof is ⊥ (the bottom
element of the Heyting lattice). The generic Pydantic model
`ConstructiveProof[T]` enforces this on every LLM output:

```python
class ConstructiveProof(BaseModel, Generic[T]):
    content:      T
    is_proven:    bool = False        # ⊥ by default — LLM output is unproven
    proof_method: Literal["unverified", "sandbox_exec", "unit_test", "static_analysis"]
```

`is_proven` may only be set to `True` by an executor node performing a
constructive verification (sandbox run, test suite pass). LLM assertion alone
is never sufficient — this mirrors the intuitionistic rejection of the law of
excluded middle.

---

### 2 — Paraconsistent Logic — Expert Conflict Registry

**Basis:** De Vries (2007), §2 — paraconsistent systems reject *ex
contradictione quodlibet*: from A ∧ ¬A it does not follow that every formula
is derivable. Contradictions are tolerated as structured data.  
**Location:** `pipeline/state.py`, `parsing.py`, `main.py`, `graph_rag/manager.py`

#### 2a — LLM Expert Conflicts

When two experts in the same domain category return significantly divergent
outputs (divergence ratio ≥ 0.35 via `_collect_conflicts`), the `merger_node`
records both propositions in `conflict_registry` before deduplication:

```python
conflict_registry: Annotated[list, operator.add]  # List[ConflictEntry-dict]
```

Each `ConflictEntry` carries `category`, `proposition_a`, `proposition_b`,
`divergence_score ∈ [0,1]`, and a lifecycle `resolution` flag:
`pending → resolved | dismissed`. No entry is ever deleted.

The `resolve_conflicts_node` implements two resolution strategies:

- **Strategy A — Auto-dismiss:** divergence score < 0.5 → dismiss as noise.
- **Strategy B — Judge arbitration:** safety-critical categories with score
  ≥ 0.5 → invoke judge LLM; result stored in `resolved_by`.

**Before:** `_dedup_by_category` silently discarded divergent knowledge.
**After:** All contradictions are preserved for audit and downstream reasoning.

#### 2b — Knowledge Graph Conflicts

Paraconsistent tolerance is extended to the Neo4j knowledge graph. When
`extract_and_ingest` updates an existing relation (version > 1) and its
confidence shifts by ≥ 0.30, the conflict is logged to Redis
`moe:graph_conflict_log` (TTL 30 days) as a structured entry with
`prev_confidence`, `new_confidence`, `prev_model`, `new_model`, and `triple`:

```
({s})-[{rel}]->({o})  conf 0.85→0.41  v3  [pending]
```

This preserves the information that a previously high-confidence fact is now
contested — rather than silently overwriting the relation property.

---

### 3 — Fuzzy Logic — T-Norm Routing

**Basis:** De Vries (2007), §4 — fuzzy logics as the most general framework;
t-norms define logical conjunction over \[0, 1\]-valued truth degrees.  
**Location:** `parsing.py` (`_compute_routing_confidence`), `main.py` (`fuzzy_router_node`)

The `planner_node` previously emitted binary routing flags (`skip_research:
bool`). The `fuzzy_router_node` replaces this with a two-stage process:

1. **Confidence derivation** (`_compute_routing_confidence`): derives
   `vector_confidence` and `graph_confidence` ∈ \[0, 1\] from plan category
   distribution, search-query presence, and complexity level.

2. **T-norm conjunction**: combines the derived confidence with a complexity
   weight via Gödel t-norm `min(a, b)` — the most conservative conjunction,
   which bounds the result by the weaker of the two signals:

```python
tnorm_vector = goedel_tnorm(vector_conf, complexity_score)
tnorm_graph  = goedel_tnorm(graph_conf,  complexity_score)
skip_research    = tnorm_vector < FUZZY_VECTOR_THRESHOLD   # default 0.30
enable_graphrag  = tnorm_graph  >= FUZZY_GRAPH_THRESHOLD   # default 0.35
```

Both thresholds are configurable via environment variables. The
Łukasiewicz t-norm (`max(0, a+b-1)`) is available in `pipeline/logic_types.py`
for contexts where partial evidence from either signal should suffice.

---

### 4 — Algorithmic Information Content — Complexity Estimation

**Basis:** Kolmogorov (1965), Chaitin (1966) — the algorithmic information
content of a string is the length of its shortest description (Kolmogorov
complexity). Lossless compression provides a computable upper bound.  
**Location:** `complexity_estimator.py`

The `_aic_compressibility` function uses zlib (DEFLATE = LZ77 + Huffman) as a
practical Kolmogorov approximation:

```python
compressibility = 1.0 - len(zlib.compress(text.encode(), level=9)) / len(text.encode())
```

A high compressibility score indicates a redundant, low-information prompt
(simple); a low score indicates an information-dense prompt (complex). This
AIC signal acts as a tie-breaker in `estimate_complexity()` when keyword
heuristics are ambiguous:

- `compressibility < 0.15` and `n ≥ 35 words` → upgrade to `complex`
- `compressibility > 0.55` and `n ≤ 15 words` → downgrade to `trivial`

Critically, the AIC signal is only applied in the ambiguous middle range — it
cannot override a definitive keyword match (e.g., a research-paper marker
always yields `complex` regardless of compressibility).

---

### 5 — Bayesian Maximum-Entropy — Infrastructure-Adaptive Expert Scoring

**Basis:** Bayesian prior adjustment under the maximum-entropy principle:
given a load constraint on an inference node, the prior over that node's
performance should reflect the available capacity.  
**Location:** `main.py` (`_get_expert_score`, `_get_model_node_load`)

MoE Sovereign uses Thompson Sampling (Beta distribution) for stochastic expert
selection. Previously, the Beta prior was determined solely by historical
feedback (`α = positive + 1`, `β = failures + 1`). The infrastructure-adaptive
extension inflates `β` proportionally to the node's current load:

```
β_adj = β × (1 + LOAD_PENALTY × node_load)
```

where `node_load ∈ [0, 1]` is read from the `_ps_cache` (populated by
`_pick_inference_server`, no additional API calls) and `LOAD_PENALTY`
defaults to `2.0` (configurable via env). At `load = 0`: no penalty. At
`load = 1`: `β` triples, reducing the expected Thompson sample from
`α/(α+β)` to `α/(α+3β)` — steering selection toward less-loaded nodes.

The Beta distribution remains mathematically well-defined for all positive
`(α, β_adj)`, preserving the exploration property of Thompson Sampling.

---

### 6 — Fuzzy Profile Matching — Entity Deduplication

**Basis:** De Vries (2014) — fuzzy profile matching via numerical attribute
similarity; tolerance-based identity under partial information.  
**Location:** `graph_rag/manager.py` (`_fuzzy_resolve_entity_name`)

Before every Neo4j `MERGE`, incoming entity names are resolved against a
session-local index of known entity names built from a single prefix-batched
query. Resolution uses the Ratcliff/Obershelp algorithm (SequenceMatcher):

```python
score = SequenceMatcher(None, name.lower(), candidate.lower()).ratio()
```

A candidate is accepted as canonical when `score ≥ 0.82` (configurable via
`_FUZZY_ENTITY_THRESHOLD`). At equal scores, the shorter name is preferred
as the canonical form. This prevents duplicate Neo4j nodes for entities
that appear under alternate spellings across different knowledge sources
(e.g., `"Einstein, Albert"` → resolved to `"Albert Einstein"`).

The threshold 0.82 was calibrated to tolerate case, punctuation, and minor
spelling variants while rejecting unrelated short names where high ratio
scores are coincidental.

---

### Implementation Summary

| Component | Logic / Theory | Pub. basis | Status |
|---|---|---|---|
| `ConstructiveProof[T]` | Intuitionistic / Heyting algebra | De Vries 2007, §3 | ✅ Active |
| `conflict_registry` (LLM experts) | Paraconsistent | De Vries 2007, §2 | ✅ Active |
| `resolve_conflicts_node` (Strategy A+B) | Paraconsistent | De Vries 2007, §2 | ✅ Active |
| `moe:graph_conflict_log` (Neo4j) | Paraconsistent | De Vries 2007, §2 | ✅ Active |
| `fuzzy_router_node` (Gödel t-norm) | Fuzzy / T-norm | De Vries 2007, §4; Gödel 1932 | ✅ Active |
| `_compute_routing_confidence` | Fuzzy | De Vries 2007, §4 | ✅ Active |
| `_aic_compressibility` | Algorithmic information | Kolmogorov 1965; Chaitin 1966 | ✅ Active |
| Load-adaptive Thompson β | Bayesian max-entropy | Statistical learning theory | ✅ Active |
| `_fuzzy_resolve_entity_name` | Fuzzy profile matching | De Vries 2014; Ratcliff 1988 | ✅ Active |
| `ConstructiveProof` executor node | Intuitionistic | De Vries 2007, §3 | ⏳ Planned |

---

### References

- A. de Vries, *Algebraic hierarchy of logics unifying fuzzy logic and quantum logic*, arXiv:0707.2161 [math.LO], 2007. <https://arxiv.org/abs/0707.2161>
- A. de Vries, *Profile matching via fuzzy numerical attributes*, 2014.
- K. Gödel, *Zum intuitionistischen Aussagenkalkül*, Anzeiger Akademie der Wissenschaften Wien, 1932.
- J. Łukasiewicz, *O logice trójwartościowej*, Ruch Filozoficzny 5, 1920.
- A. N. Kolmogorov, *Three approaches to the quantitative definition of information*, Problems of Information Transmission 1(1), 1965.
- G. J. Chaitin, *On the length of programs for computing finite binary sequences*, Journal of the ACM 13(4), 1966.
- J. W. Ratcliff & D. E. Metzener, *Pattern Matching: The Gestalt Approach*, Dr. Dobb's Journal, 1988.
