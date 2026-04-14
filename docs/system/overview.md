# Sovereign MoE — System Documentation

> Last updated: 2026-03-29 — Version 2.1.0  
> Project directory: `/opt/deployment/moe-infra`

---

## 1. Overview

**Sovereign MoE** is a fully self-hosted Mixture-of-Experts LLM system. Incoming requests are analyzed by an orchestrator and distributed to specialized LLM experts, external search tools, and precision calculation tools. The results are synthesized by a Judge LLM.

The system is **OpenAI API-compatible** (including correctly terminating streaming) and can be embedded as a drop-in replacement in clients like Open WebUI.

### Core Principles

- **No cloud dependency** — all LLMs run locally via Ollama on own GPU hardware
- **Specialization over generalization** — the best available models are used per category
- **Exactness over LLM estimation** — calculations, data, hashes are deterministic via MCP server
- **Learning through use** — feedback flows back into expert performance scores and the knowledge graph
- **Persistent background processing** — Kafka decouples the HTTP path from ingest and logging

---

## 2. Hardware & Infrastructure

The system supports multiple Ollama-based inference servers. Admins configure inference endpoints via **Admin UI → Servers**. Each server can host multiple models for different expert categories.

Inference server URLs and model assignments are configured via `INFERENCE_SERVERS` in `.env` or through the Admin UI dashboard.

### VRAM Management

VRAM management is handled dynamically via the `CLUSTER_HARDWARE` JSON object in `.env`. Each GPU class receives a configurable priority. The orchestrator routes requests to inference servers based on availability and model assignments. Ollama queues requests internally per node. There are no static semaphores.

For details on cluster configuration: [Ollama Cluster](toolstack/ollama_cluster.md) and [LiteLLM Gateway](toolstack/lite_llm.md).

---

## 3. System Architecture

```mermaid
flowchart TD
    CLIENT["☁ Client\n(Open WebUI, curl, SDK, Claude Code)"]
    NGINX["Nginx (host-native)\nTLS/Let's Encrypt\n→ :8002"]

    subgraph ORCH["LangGraph Orchestrator · Port 8002"]
        CACHE["🔍 cache_lookup\nChromaDB semantic search"]
        SROUTER["⚡ semantic_router\nPrototype matching"]
        PLANNER["🧠 planner\n(Judge-LLM)\nValkey plan cache TTL 30m"]
        WORKERS["👥 workers\nT1 + T2 expert models\nGPU semaphore managed"]
        RESEARCH["🌐 research\n(SearXNG)"]
        MATH["∑ math\n(SymPy)"]
        MCP_NODE["🔧 mcp\n(26 Precision Tools)"]
        GRAPHRAG["🗃 graph_rag\n(Neo4j 2-hop)\nValkey cache TTL 1h"]
        THINKING["💭 thinking\n(CoT, conditional)"]
        MERGER["⚖ merger\n(Judge-LLM)\nFast-Path for single expert"]
        CRITIC["🔎 critic\n(medical/legal only)"]
    end

    CLIENT -->|"HTTPS"| NGINX
    NGINX -->|"POST /v1/chat/completions"| CACHE
    CACHE -->|"Hit (< 0.15)"| CLIENT
    CACHE -->|"Miss"| SROUTER
    SROUTER -->|"Direct match"| WORKERS
    SROUTER -->|"No match"| PLANNER
    PLANNER --> WORKERS & RESEARCH & MATH & MCP_NODE & GRAPHRAG
    WORKERS & RESEARCH & MATH & MCP_NODE & GRAPHRAG --> THINKING
    THINKING --> MERGER
    MERGER -->|"Fast-Path"| CRITIC
    MERGER -->|"Full synthesis"| CRITIC
    CRITIC --> CLIENT

    MERGER --> CHROMADB[("ChromaDB :8001\nSemantic cache + prototypes")]
    MERGER --> REDIS[("Valkey :6379\nPlan/Graph/Perf cache\nActive request registry")]
    MERGER --> PG[("Postgres :5432\nterra_checkpoints\nLangGraph state")]
    MERGER -->|"moe.ingest + moe.requests"| KAFKA[("Kafka :9092\nKRaft mode")]
    KAFKA -->|"Consumer → extract_and_ingest"| NEO4J[("Neo4j :7687\nKnowledge graph")]
    MCP_NODE --> MCPSERVER["MCP Precision Tools\n:8003"]
```

---

## 4. Docker Services

```bash
sudo docker compose up -d           # start all services
sudo docker compose down            # stop
sudo docker compose logs -f <name>  # live logs
```

The full stack consists of **18 Docker services** plus the host-level Nginx (not containerized).
See [Docker Services Reference](services.md) for details on each service, and
[Webserver & Reverse Proxy](webserver.md) for the external access architecture.

### Service Overview

| Container | Image / Build | Ports (Host→Container) | Function |
|---|---|---|---|
| `langgraph-orchestrator` | `./Dockerfile` | `8002:8000` | Core orchestrator, FastAPI, LangGraph |
| `mcp-precision` | `./mcp_server/Dockerfile` | `8003:8003` | 26 deterministic precision tools |
| `neo4j-knowledge` | `neo4j` (pinned SHA) | `7474:7474`, `7687:7687` | Knowledge graph (GraphRAG + ontology) |
| `terra_cache` | `valkey/valkey` (pinned SHA) | `6379:6379` | Valkey — cache, scoring, session metadata |
| `terra_checkpoints` | `postgres:17-alpine` | (internal only) | Postgres — LangGraph `AsyncPostgresSaver` |
| `chromadb-vector` | `chromadb/chroma` (pinned SHA) | `8001:8000` | Vector database for semantic caching |
| `moe-kafka` | `confluentinc/cp-kafka:7.7.0` | `9092:9092` | Kafka event streaming (KRaft, no Zookeeper) |
| `docker-socket-proxy` | `tecnavia/docker-socket-proxy:0.3.0` | `2375:2375` | Read-only Docker API proxy for moe-admin |
| `moe-admin` | `./admin_ui/Dockerfile` | `8088:8088` | Admin UI: config, users, monitoring |
| `moe-prometheus` | `prom/prometheus` (pinned SHA) | `9090:9090` | Metrics scraper (90-day TSDB) |
| `moe-grafana` | `grafana/grafana` (pinned SHA) | `3001:3000` | Dashboards & visualization |
| `node-exporter` | `prom/node-exporter` (pinned SHA) | `9100:9100` | Host metrics (CPU, RAM, disk, net) |
| `cadvisor` | `gcr.io/cadvisor` (pinned SHA) | `9338:8080` | Container resource metrics |
| `moe-docs` | `squidfunk/mkdocs-material` (pinned SHA) | `8098:8000` | MkDocs documentation server |
| `moe-docs-sync` | custom Python | — | Periodic docs sync agent (every 15 min) |
| `moe-dozzle-init` | `python:3.12-alpine` | — | One-shot init: Dozzle bcrypt users.yml |
| `moe-dozzle` | `amir20/dozzle` (pinned SHA) | `9999:8080` | Container log viewer with auth |
| `moe-caddy` | `caddy` (pinned SHA) | `80:80`, `443:443` | Internal reverse proxy for docs/dozzle |

### Host Volumes

| Path | Contents |
|---|---|
| `/opt/moe-infra/neo4j-data` | Neo4j data |
| `/opt/moe-infra/redis-data` | Valkey persistence (historical path name) |
| `/opt/moe-infra/langgraph-checkpoints` | Postgres data for `terra_checkpoints` |
| `/opt/moe-infra/chroma-data` | ChromaDB vector data |
| `/opt/moe-infra/kafka-data` | Kafka log segments |
| `/opt/moe-infra/agent-logs` | Orchestrator logs |

---

## 5. LangGraph Pipeline

### Pipeline Flow

```mermaid
flowchart TD
    START([START]) --> CACHE_LOOKUP
    CACHE_LOOKUP["cache_lookup_node"] -->|"cache_hit=True"| MERGER_NODE
    CACHE_LOOKUP -->|"cache_hit=False"| PLANNER_NODE["planner_node"]

    PLANNER_NODE --> EW["expert_worker"]
    PLANNER_NODE --> RN["research_node"]
    PLANNER_NODE --> MN["math_node_wrapper"]
    PLANNER_NODE --> MCPN["mcp_node"]
    PLANNER_NODE --> GRN["graph_rag_node"]

    EW --> RFN["research_fallback_node"]
    RN --> RFN
    MN --> RFN
    MCPN --> RFN
    GRN --> RFN

    RFN --> THINKING_NODE["thinking_node\n(CoT, conditionally active)"]
    THINKING_NODE --> MERGER_NODE["merger_node"]
    MERGER_NODE --> CRITIC_NODE["critic_node\n(fact-check, only medical/legal)"]
    CRITIC_NODE --> END([END])
```

### Nodes in Detail

#### `cache_lookup_node`
- Queries ChromaDB for 3 semantically most similar entries
- Skips entries with `flagged=True` (negative feedback)
- Distance < `CACHE_HIT_THRESHOLD` (0.15) → `cache_hit=True` → entire pipeline bypassed

#### `planner_node`
- Judge LLM decomposes request into 1–4 subtasks as JSON array
- `_sanitize_plan()` validates each entry: must be dict with `task`+`category`; strings, empty dicts, and unknown categories are discarded (logged)
- Fallback to `[{"task": input, "category": "general"}]` on JSON parse error
- Knows all expert categories, 16 MCP tools, and research option

**Planner Rules (enforced in prompt):**
- `precision_tools` always beats `math` — never both for the same task
- `research` only for genuinely external/current information needs
- Never keywords or questions as tasks — always task descriptions

#### `expert_worker`
- Filters tasks to LLM expert categories (all except `precision_tools`, `research`)
- **Two-Tier logic:** T1 (≤20B) first — if `CONFIDENCE: high` → T2 skipped; otherwise T2 escalates
- Sorts experts by performance score (best first); score < 0.3 → skipped
- Injects `chat_history` (last 4 turns, max 3000 chars) into all expert messages
- Output cap: `MAX_EXPERT_OUTPUT_CHARS` (2400 chars ≈ 600 tokens)
- Returns `expert_models_used` (`["model::category", ...]`)

#### `research_node`
- Active only for `research` task in plan
- Uses `search_query` from plan (optimized by planner)
- Returns structured source citations via `search.results()` (title + URL numbered)

#### `research_fallback_node`
- Runs after fan-in of all parallel nodes
- For each `CONFIDENCE: low` expert result: targeted web research with citation tracking
- Aggregates new results in `web_research`

#### `thinking_node`
- **Conditionally active:** for plans with >1 task OR if at least one expert reports `CONFIDENCE: low`
- Magistral:24b runs 4-step Chain-of-Thought:
  1. Problem decomposition
  2. Source evaluation (which info is reliable, contradictions?)
  3. Knowledge gaps
  4. Conclusion
- Output as `reasoning_trace` → prioritized section in merger prompt
- Progress appears in Open WebUI `<think>` panel

#### `math_node_wrapper`
- Active only if `math` task in plan AND no `precision_tools` task present
- SymPy-based (symbolic)

#### `mcp_node`
- All `precision_tools` tasks in parallel via `asyncio.gather`
- HTTP POST to MCP server `/invoke`

#### `graph_rag_node`
- Parallel to all other nodes after planner
- Term extraction from request (regex, no LLM call)
- 2-hop traversal in Neo4j

#### `merger_node`
- Calls `_dedup_by_category()` — keeps only the highest-confidence expert result per category
- Source priority: Reasoning trace > MCP > Knowledge graph > Experts > Web > Cache
- On cache hit: direct return, no LLM call
- After response (> 150 chars), publishes:
  - ChromaDB: new cache document (sync)
  - Valkey: response metadata (async task)
  - Kafka `moe.requests`: audit log (async task)
  - Kafka `moe.ingest`: GraphRAG ingest job (async task)

#### `critic_node`
- **Only active** if plan contains `medical_consult` or `legal_advisor`
- Second Judge LLM pass checks merger response for factual errors and dangerous statements
- Response `CONFIRMED` → response unchanged; error found → corrected version

### State Schema (`AgentState`)

| Field | Type | Description |
|---|---|---|
| `input` | str | User query |
| `response_id` | str | `chatcmpl-<uuid>` for feedback tracking |
| `mode` | str | `default` \| `code` \| `concise` |
| `plan` | List[Dict] | Subtask plan |
| `expert_results` | Annotated[list, add] | Expert responses (fan-in) |
| `expert_models_used` | Annotated[list, add] | `["model::category", ...]` |
| `web_research` | str | SearXNG result with source citations |
| `cached_facts` | str | Nearest cache hit content |
| `cache_hit` | bool | Cache short-circuit triggered |
| `math_result` | str | SymPy result |
| `mcp_result` | str | MCP tools result |
| `graph_context` | str | Neo4j context block |
| `final_response` | str | Final synthesized response |
| `prompt_tokens` | Annotated[int, add] | Accumulated prompt tokens across all LLM calls |
| `completion_tokens` | Annotated[int, add] | Accumulated completion tokens across all LLM calls |
| `chat_history` | List[Dict] | Conversation context (last 4 turns) for experts |
| `reasoning_trace` | str | CoT output from `thinking_node` |

---

## 6. Expert System

### Two-Tier System

Experts are divided into tiers by model size:

| Tier | Threshold | Role |
|---|---|---|
| T1 | ≤ 20B parameters | Fast initial assessment |
| T2 | > 20B parameters | Premium escalation for non-high confidence |

T1 runs first. If at least one T1 result returns `CONFIDENCE: high` → T2 is skipped. Otherwise all T2 models run in parallel.

### Configured Experts

| Category | Model 1 | Tier | Model 2 | Tier |
|---|---|---|---|---|
| `general` | `gemma3:27b` | T2 | `qwen3.5:35b` | T2 |
| `math` | `phi4:14b` | T1 | `qwq:32b` | T2 |
| `technical_support` | `deepseek-coder-v2:16b` | T1 | `devstral:24b` | T2 |
| `creative_writer` | `gemma3:27b` | T2 | `qwen3.5:35b` | T2 |
| `code_reviewer` | `devstral:24b` | T2 | `qwen3-coder:30b` | T2 |
| `medical_consult` | `phi4:14b` | T1 | `gemma3:27b` | T2 |
| `legal_advisor` | `magistral:24b` | T2 | `command-r:35b` | T2 |
| `translation` | `translategemma:27b` | T2 | `qwen3.5:35b` | T2 |
| `reasoning` | `phi4:14b` | T1 | `deepseek-r1:32b` | T2 |

**Judge LLM:** `magistral:24b` (planner, merger, thinking node, critic, GraphRAG extraction)

### System Prompts

| Category | Role & Behavior |
|---|---|
| `general` | Versatile, fact-based, structured |
| `math` | Solution steps + LaTeX, back-verification |
| `technical_support` | IT engineer, executable steps, concrete commands |
| `creative_writer` | Vivid, stylistically confident, register matching context |
| `code_reviewer` | Senior SWE, bugs/security/performance, concrete code |
| `medical_consult` | Factual, guideline-based, always recommend doctor visit |
| `legal_advisor` | German law (§§), recommend individual legal consultation |
| `translation` | Professional, idiomatic, cultural nuances |
| `reasoning` | Chain-of-Thought, state assumptions, justified conclusions |

---

## 7. MCP Precision Tools Server

**Port:** 8003 · **File:** `mcp_server/server.py`

### REST Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Status + tool list |
| `/tools` | GET | Tool descriptions (for planner prompt) |
| `/invoke` | POST | `{"tool": "name", "args": {...}}` |
| `/mcp/sse` | GET | MCP SSE endpoint (Claude Desktop etc.) |

### All 16 Tools

| Tool | Description |
|---|---|
| `calculate` | Exact arithmetic, formulas, percentages (safe AST evaluator) |
| `solve_equation` | Algebraic equations (SymPy) |
| `date_diff` | Exact date difference (days, years, months) |
| `date_add` | Date arithmetic (add/subtract days/months/years) |
| `day_of_week` | Day of week, calendar week, day of year |
| `unit_convert` | Physical units (pint): km/h→m/s, °F→°C, ... |
| `statistics_calc` | mean, median, stdev, variance, min, max, sum, count, mode |
| `hash_text` | MD5, SHA1, SHA224, SHA256, SHA384, SHA512 |
| `base64_codec` | Base64 encode / decode |
| `regex_extract` | Regex matching with flags (i, m, s) |
| `subnet_calc` | CIDR: network, broadcast, mask, host range |
| `text_analyze` | Words, characters, sentences, paragraphs, reading time |
| `prime_factorize` | Prime factorization via SymPy |
| `gcd_lcm` | GCD and LCM |
| `json_query` | JSON path queries (key.sub, array[0]) |
| `roman_numeral` | Arabic ↔ Roman (1–3999) |

---

## 8. GraphRAG & Knowledge Ontology

**File:** `graph_rag/manager.py`, `graph_rag/ontology.py` · **DB:** Neo4j 5

### Base Ontology (loaded on startup, idempotent)

- **104 entities** — Medical, Legal, Technical, Math/Science
- **100 relations** — IS_A, TREATS, CAUSES, INTERACTS_WITH, CONTRAINDICATES, DEFINES, USES, IMPLEMENTS, DEPENDS_ON, EXTENDS, RELATED_TO, AFFECTS, etc.

### Context Query

1. Term extraction from request (regex, prefers nouns/proper nouns)
2. Fuzzy search on `name` + `aliases_str` (case-insensitive)
3. 2-hop traversal: direct + indirect relations
4. Output as `[Knowledge Graph]` text block in merger prompt

### Background Ingest (via Kafka)

1. Merger response → `moe.ingest` topic
2. Kafka consumer calls `extract_and_ingest()`
3. Judge LLM extracts up to 8 triples (JSON format)
4. Conflict check: TREATS vs. CAUSES/CONTRAINDICATES (and vice versa)
5. New triples are stored with `r.verified=false`

### Feedback Integration

| Rating | Neo4j |
|---|---|
| 1–2 | `r.flagged=true, r.verified=false` |
| 4–5 | `r.verified=true, r.flagged=false` |

---

## 9. Kafka Event Streaming

> Detailed documentation: **[Kafka](kafka.md)**

### Quick Overview

| Topic | Producer | Consumer | Content |
|---|---|---|---|
| `moe.ingest` | `merger_node` | `_kafka_consumer_loop` | GraphRAG ingest job |
| `moe.requests` | `merger_node` | `_kafka_consumer_loop` (logging) | Audit log |
| `moe.feedback` | `/v1/feedback` | — | Feedback events |

**Kafka setup:** `confluentinc/cp-kafka:7.7.0`, KRaft mode (no Zookeeper), port 9092, 7-day retention.

**Graceful degradation:** If Kafka fails, the system continues operating fully — HTTP responses, cache, Valkey unaffected.

---

## 10. Memory & Learning Mechanisms

### Four Memory Levels

| Level | Storage | Grows from | TTL |
|---|---|---|---|
| Semantic cache | ChromaDB | Every merger response > 150 chars | unlimited |
| Knowledge graph | Neo4j | Kafka `moe.ingest` consumer | unlimited |
| Expert performance | Valkey | `POST /v1/feedback` | unlimited |
| LangGraph checkpoints | Valkey | Every request | unlimited |

### Expert Performance Scoring

```
Key:    moe:perf:{model}:{category}
Fields: total, positive, negative

Score = (positive + 1) / (total + 2)   # Laplace smoothing
```

- Under 5 ratings: 0.5 (neutral)
- Score < 0.3 after ≥ 5 ratings → expert is skipped

### Feedback Effect

| Rating | ChromaDB | Expert Score | Neo4j |
|---|---|---|---|
| 1–2 (negative) | `flagged=True` | `negative++` | `flagged=true` |
| 3 (neutral) | — | — | — |
| 4–5 (positive) | — | `positive++` | `verified=true` |

---

## 11. OpenAI API & Streaming

### Streaming (SSE) — fully OpenAI-compatible

Each chunk contains `id`, `object`, `created`, `model`, `choices`:

```
1. First chunk:    delta={"role":"assistant","content":""},  finish_reason=null
2. Content chunks: delta={"content":"<50 chars>"},           finish_reason=null
3. Stop chunk:     delta={},                                  finish_reason="stop"
4. Final:          data: [DONE]
```

The stop chunk with `finish_reason: "stop"` is the signal for Open WebUI (and all OpenAI-compatible clients) to close the stream and free LLMs. Without this chunk, the client would wait indefinitely.

### Non-Streaming

```json
{
  "id":      "chatcmpl-<uuid>",
  "object":  "chat.completion",
  "created": 1774783273,
  "model":   "moe-orchestrator",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "..."}, "finish_reason": "stop"}],
  "usage":   {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

---

## 12. API Reference

**Base URL:** `http://<host>:8002`

### Chat Completions

```http
POST /v1/chat/completions
{"model":"moe-orchestrator","messages":[{"role":"user","content":"..."}],"stream":false}
```

### Models

```http
GET /v1/models
```

### Feedback

```http
POST /v1/feedback
{"response_id":"chatcmpl-...","rating":4}
```

### Graph

```http
GET /graph/stats
GET /graph/search?q=Ibuprofen&limit=10
```

---

## 13. Configuration & Environment Variables

| Variable | Default | Description |
|---|---|---|
| `INFERENCE_SERVERS` | — | JSON array of inference server configurations |
| `JUDGE_ENDPOINT` | — | Server for judge LLM (configured server name) |
| `JUDGE_MODEL` | `gemma3:4b` | Judge model |
| `EXPERT_MODELS` | `{}` | JSON: category → `[{model, endpoint}]` |
| `SEARXNG_URL` | — | SearXNG instance |
| `REDIS_URL` | `redis://terra_cache:6379` | Valkey connection (`redis://` is the protocol) |
| `POSTGRES_CHECKPOINT_URL` | `postgresql://langgraph:***@terra_checkpoints:5432/langgraph` | LangGraph checkpoint store |
| `CHROMA_HOST` | `chromadb-vector` | ChromaDB host |
| `MCP_URL` | `http://mcp-precision:8003` | MCP server |
| `NEO4J_URI` | `bolt://neo4j-knowledge:7687` | Neo4j |
| `NEO4J_USER` | `neo4j` | Neo4j user |
| `NEO4J_PASS` | `moe-sovereign` | Neo4j password |
| `KAFKA_URL` | `kafka://moe-kafka:9092` | Kafka bootstrap |
| `CLUSTER_HARDWARE` | JSON object | GPU nodes, priorities, model assignments |
| `MAX_EXPERT_OUTPUT_CHARS` | `2400` | Output cap per expert |
| `CACHE_HIT_THRESHOLD` | `0.15` | Cosine distance threshold |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |

---

## 14. Data Persistence

| Data | Storage | TTL | Valkey Key / ChromaDB Collection |
|---|---|---|---|
| LangGraph checkpoints | Valkey | unlimited | `langgraph:*` |
| Semantic cache | ChromaDB | unlimited | `moe_fact_cache` |
| Expert performance | Valkey | unlimited | `moe:perf:{model}:{cat}` |
| Response metadata | Valkey | 7 days | `moe:response:{id}` |
| Knowledge graph | Neo4j | unlimited | Volume `/opt/moe-infra/neo4j-data` |
| Kafka events | Kafka | 7 days / 512 MB | `/opt/moe-infra/kafka-data` |

---

## 15. Deployment

### First Start

```bash
cd /opt/deployment/moe-infra
cp temp.env .env
# configure INFERENCE_SERVERS, SEARXNG_URL, EXPERT_MODELS
sudo mkdir -p /opt/moe-infra/{neo4j-data,neo4j-logs,redis-data,langgraph-checkpoints,chroma-data,kafka-data,agent-logs}
sudo docker compose up -d --build
sudo docker compose logs -f langgraph-orchestrator
```

### After Code Changes

```bash
# Orchestrator
sudo docker compose build langgraph-app && sudo docker compose up -d langgraph-app

# MCP server
sudo docker compose build mcp-precision && sudo docker compose up -d mcp-precision
```

### Startup Sequence (Orchestrator)

1. Valkey client
2. Kafka producer (12 attempts, 5–60s backoff)
3. Load MCP tool descriptions
4. Neo4j GraphRAG (6 attempts, 10–60s backoff)
5. Start Kafka consumer as background task
6. AsyncValkeySaver → compile LangGraph graph
7. FastAPI on port 8000

### Health Checks

```bash
curl http://localhost:8002/v1/models          # orchestrator
curl http://localhost:8002/graph/stats        # GraphRAG
curl http://localhost:8003/health             # MCP server
sudo docker exec terra_cache valkey-cli ping  # Valkey
sudo docker exec moe-kafka kafka-topics \
  --bootstrap-server localhost:9092 --list    # Kafka topics
# Neo4j Browser: http://localhost:7474
```

---

## 16. Project Structure

```mermaid
graph LR
    ROOT[/opt/deployment/moe-infra/]

    ROOT --> MAIN[main.py<br/>LangGraph orchestrator, FastAPI, nodes, Kafka]
    ROOT --> MATH[math_node.py<br/>SymPy math node]
    ROOT --> REQ[requirements.txt]
    ROOT --> DF[Dockerfile]
    ROOT --> DC[docker-compose.yml<br/>all services]
    ROOT --> ENV[.env<br/>not in git]
    ROOT --> GR[graph_rag/]
    ROOT --> MCPD[mcp_server/]
    ROOT --> ADMIN[admin_ui/<br/>Flask admin panel]
    ROOT --> DOCS[docs/<br/>MkDocs source]

    GR --> GRM[manager.py<br/>Neo4j client, query_context, extract_and_ingest]
    GR --> GRO[ontology.py<br/>base ontology]

    MCPD --> MCPS[server.py<br/>deterministic precision tools]
    MCPD --> MCPDF[Dockerfile]
```

---

## 17. Quality Mechanisms

### Confidence Scoring

All experts produce structured output with self-assessed confidence:

```
KEY_MESSAGE: [1-2 sentences]
CONFIDENCE: high | medium | low
DETAILS: [full response]
```

`high` = established expert knowledge · `medium` = nuances possible · `low` = data gaps, uncertainty

### Automated Quality Assurance Chain

```mermaid
flowchart TD
    EO[Expert output] --> FB{CONFIDENCE: low?}
    FB -->|yes| RF[research_fallback<br/>web research with citations]
    FB -->|no| TN
    RF --> TN

    TN[thinking_node<br/>active if &gt;1 task OR low confidence<br/>4-step CoT: decomposition → sources → gaps → conclusion]
    TN --> MN

    MN[merger_node<br/>_dedup_by_category: best confidence per category wins<br/>Source priority: Reasoning &gt; MCP &gt; Graph &gt; Experts &gt; Web &gt; Cache]
    MN --> CN

    CN{critic_node<br/>only medical_consult / legal_advisor<br/>factual errors? dangerous statements?}
    CN -->|CONFIRMED| OUT([pass])
    CN -->|error| FIX([corrected version])
```

### Open WebUI Integration

- **`<think>` panel:** Progress reports from all nodes via `contextvars.ContextVar` → SSE stream → "Thinking" panel
- **Internal requests:** `_is_openwebui_internal()` detects title/follow-up/autocomplete → fast path without pipeline
- **Token tracking:** Accumulated `usage` fields (all LLM calls) in every response

### Output Modes

| Model ID | Mode | Behavior |
|---|---|---|
| `moe-orchestrator` | `default` | Full responses with explanations |
| `moe-orchestrator-code` | `code` | Source code only, no prose |
| `moe-orchestrator-concise` | `concise` | Max 120 words |

---

*Generated on 2026-03-29 — Version 2.1.0*
