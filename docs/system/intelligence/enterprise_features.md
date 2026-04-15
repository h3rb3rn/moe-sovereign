# Enterprise Architecture Features

These features were inspired by an analysis of proprietary enterprise AI platforms
(Palantir AIP, Databricks Mosaic AI, Glean) and adapted for the open-source,
sovereignty-first architecture of MoE Sovereign.

## Confidence Decay & Self-Healing Knowledge Graph

### Problem

False associations from bad queries contaminate the Neo4j knowledge graph permanently.
For example, a query about "car wash automation" might create a relation
`(CarWash)-[USES]->(Neo4j)` that pollutes all future technical queries.

### Solution: Trust Score

Every relation receives a computed **trust score**:

$$
\text{trust} = \text{confidence} \times w_{\text{source}} \times \text{decay} \times v_{\text{bonus}}
$$

| Factor | Values | Description |
|--------|--------|-------------|
| `confidence` | 0.0 - 1.0 | Base confidence from the extracting LLM |
| `w_source` | ontology: 1.0, healer: 0.9, extracted: 0.6 | Source reliability weight |
| `decay` | max(0.3, 1 - days/365) | Temporal decay over one year |
| `v_bonus` | verified: 1.5x, else 1.0 | Bonus for human-verified relations |

### Automatic Cleanup

Relations with `trust < 0.2` that are:

- **Unverified** (`verified = false`)
- **Single-assertion** (`version = 1`, never re-confirmed by a later query)

...are automatically deleted during:

1. **Phase 3 of Graph Linting** (triggered via Kafka `moe.linting` topic)
2. **Phase 0 of the Nightly Gap Healer** (runs at 02:00 via systemd timer)

All deletions are logged to the `moe.audit` Kafka topic for auditability.

### Prometheus Metrics

- `moe_linting_decay_deleted_total` — Relations removed by confidence decay

---

## Multi-Tenant RBAC (Graph-Level Isolation)

### Problem

All users see all Neo4j entities. Multi-tenant deployments need data isolation
without modifying the LLM pipeline.

### Solution: Tenant-Filtered Queries

1. **Permission Type**: `graph_tenant` — assigns tenant slugs to users via the Admin UI
2. **Neo4j Property**: `tenant_id` on Entity nodes (indexed for performance)
3. **Pipeline Propagation**: `tenant_ids` extracted from user permissions, passed through `AgentState`
4. **Query Filtering**: Cypher includes `WHERE e.tenant_id IN $tenant_ids OR e.tenant_id IS NULL`

Entities with `tenant_id = NULL` are shared/public and visible to all users.
New entities created during a request are tagged with the requesting user's primary tenant.

### Admin Configuration

Navigate to **Users** > select user > **Permissions** > add `graph_tenant` permission
with the tenant slug as resource ID (e.g., `acme-corp`, `internal`).

---

## Inline Provenance Tags

### Problem

Final answers have no source attribution. Users cannot verify which claims come from
the knowledge graph vs. general LLM knowledge.

### Solution: [REF:entity] Tags

The merger prompt includes a `PROVENANCE_INSTRUCTION` that marks knowledge-graph-derived
facts with `[REF:entity_name]` tags. These are:

1. **Extracted** post-merger via regex
2. **Stripped** from the user-visible content for clean output
3. **Returned** as `metadata.sources` in the API response:

```json
{
  "choices": [{"message": {"content": "The answer..."}}],
  "metadata": {
    "sources": [
      {"type": "neo4j", "label": "PostgreSQL"},
      {"type": "neo4j", "label": "TLS_1.3"}
    ]
  }
}
```

This is backward-compatible with the OpenAI chat completion format.

---

## Blast-Radius Estimation & Quarantine

### Problem

A single erroneous triple can affect many future queries if it connects to
densely-linked entities in the knowledge graph.

### Solution: Pre-Write Impact Check

Before each triple is written to Neo4j, `_estimate_blast_radius()` counts how many
entities are reachable within 2 hops from both subject and object:

```
OPTIONAL MATCH (a:Entity {name: $s})-[*1..2]-(n1:Entity)
OPTIONAL MATCH (b:Entity {name: $o})-[*1..2]-(n2:Entity)
RETURN count(DISTINCT n) AS reach
```

If `reach > 20` (configurable via `_BLAST_RADIUS_THRESHOLD`):

1. The triple is **not written** to Neo4j
2. It is stored in a Valkey sorted set `moe:quarantine` (TTL 7 days)
3. The **Admin UI Quarantine page** shows all quarantined triples
4. An admin can **Approve** (write to Neo4j) or **Reject** (discard)

### Quarantine Page

Navigate to **Quarantine** in the Admin UI navigation bar. The page shows:

| Column | Description |
|--------|-------------|
| Subject / Object | Entity names and types |
| Relation | Relation type (e.g., TREATS, USES) |
| Reach | Number of connected entities (blast radius) |
| Source Model | Which LLM generated the triple |
| Confidence | Extraction confidence score |

### Prometheus Metrics

- `moe_quarantine_added_total` — Triples sent to quarantine

---

## Cache Performance Analysis

A controlled A/B test measured the actual impact of the L1 semantic cache
(ChromaDB, cosine distance < 0.15):

| Query Type | Latency | Tokens | Result |
|------------|--------:|-------:|--------|
| Cold (first time) | 280.7s | 8,001 | Full pipeline |
| Exact repeat | 293.3s | 9,128 | **No cache hit** |
| Semantically similar | timeout | 0 | Pipeline stalled |

**Finding:** The L1 cache did not trigger for standard queries in this test.
The most likely causes: (1) the cosine distance threshold of 0.15 is very strict,
(2) the cache primarily benefits repeated *plan patterns* (L2 Valkey cache, SHA-256
hash match) rather than full-response caching.

The accumulation effect observed in the benchmark runs (55% latency reduction between
Run 1 and Run 2) is primarily driven by **GraphRAG context enrichment** and **model
warmth**, not by the L1 semantic cache. This is an honest result — the cache hierarchy
adds value for specific patterns (identical queries within 30 minutes), but is not
a general-purpose acceleration layer.

## Floating Node Discovery

### Problem

Pinning every expert model to a specific inference node is operationally rigid.
When a node is rebooted or its VRAM is occupied by another model, the pinned
expert fails even though the same model may already be warm on a different node.

### Solution: Empty Endpoint = All Nodes

When an expert's `endpoint` field in a template is left **empty** (or set to `""`),
the orchestrator enters **floating mode**: it queries *every* configured inference
server for availability of the requested model.

```
raw_ep = [s["name"] for s in INFERENCE_SERVERS_LIST]
```

Node selection follows a 3-phase strategy:

| Phase | Check | Description |
|-------|-------|-------------|
| 0 | Sticky session | Valkey lookup for recent user-node affinity (see below) |
| 1 | Warm model | Ollama `/api/ps` (5s cache) — prefer nodes where the model is already loaded in VRAM |
| 2 | Load score | Among warm (or cold) candidates, pick the node with the lowest `running / gpu_count` ratio |

This means floating experts automatically migrate to whichever node currently has
the model warm, without any admin intervention.

---

## Sticky Sessions

### Problem

Floating mode could bounce a user between nodes on every request, causing
unnecessary model loads and cold-start latency.

### Solution: Valkey-Backed User-Node Affinity

After each successful node selection, the orchestrator stores an affinity record:

```
SET moe:sticky:{user_id}:{model_base} {node_name} EX 300
```

| Parameter | Value |
|-----------|-------|
| Key pattern | `moe:sticky:{user_id}:{model_base}` |
| Value | Node name (e.g., `N04-RTX`) |
| TTL | 300 seconds (5 minutes) |

On the next request, Phase 0 of `_select_node()` checks for a sticky hit before
evaluating warm/cold status. If the sticky node is still in the allowed endpoint
list, it is used immediately — skipping the more expensive `/api/ps` fan-out.

The 5-minute TTL balances affinity with adaptability: short interactive sessions
stay on the same node, while idle users naturally lose their affinity and get
re-routed to the currently optimal node.

---

## Model Registry

### Problem

The warm-model check (`/api/ps`) requires an HTTP fan-out to every inference server.
At scale (many nodes, many requests), this creates unnecessary network traffic.

### Solution: Valkey ZSET Registry with Heartbeat

A background task polls each inference server every 60 seconds and registers
all currently loaded models in Valkey sorted sets:

```
ZADD moe:model_registry:{model_base} {timestamp} {node_name}
EXPIRE moe:model_registry:{model_base} 120
```

| Parameter | Value |
|-----------|-------|
| Key pattern | `moe:model_registry:{model_base}` |
| Score | Unix timestamp of last heartbeat |
| Member | Node name |
| TTL | 120 seconds (2x heartbeat interval) |

This provides a fast O(1) lookup for "which nodes currently have model X warm?"
without requiring a live HTTP call. The 120s TTL ensures stale entries are
automatically cleaned when a node goes offline or unloads a model.

---

## Validation

All four features were validated in a benchmark run after deployment:

| Feature | Impact on Score | Impact on Latency |
|---------|----------------|-------------------|
| Confidence Decay | None (6.0/10 stable) | < 50ms per linting run |
| Tenant RBAC | None | < 10ms per query (indexed) |
| Provenance Tags | None | 0ms (prompt addition only) |
| Blast-Radius | None | < 50ms per ingest (2-hop query) |
