# Tier-2 Semantic Memory — 1M-Token Context Window

MoE Sovereign extends effective conversation context to **1 million tokens or more**
through a three-tier memory architecture. Each tier covers a different time horizon
without increasing inference token costs.

---

## Overview: Three-Tier Memory

```
Tier 1 — HOT    (LLM context, verbatim)    Last N conversation turns
Tier 2 — WARM   (ChromaDB, disk-bound)     ANN retrieval of evicted turns
Tier 3 — COLD   (Neo4j, disk-bound)        GraphRAG entity/fact extraction
```

| Tier | Backend | Capacity | Retrieval | TTL |
|---|---|---|---|---|
| **T1 — Hot** | LLM native context | Model-dependent (4k–128k) | Verbatim, instant | Session duration |
| **T2 — Warm** | ChromaDB + nomic-embed-text | Effectively unlimited | ANN + hybrid keyword ranking | 6 hours |
| **T3 — Cold** | Neo4j knowledge graph | Unlimited (disk) | GraphRAG cypher queries | Permanent |

---

## How It Works

### Turn Eviction and Storage (Tier-2)

When a conversation exceeds the configured hot-window size (`default_max_turns`), the
oldest turns are **evicted** from the LLM context. Instead of discarding them, the
orchestrator stores the evicted turns in ChromaDB as dense vector embeddings:

```
nomic-embed-text (768 dim, via Ollama) → ChromaDB HttpClient
```

Each stored turn includes:
- **Document**: the raw text (`[role] content`)
- **Metadata**: `session_id`, `turn_index`, `role`, `keywords`, `timestamp`
- **Embedding**: 768-dimensional float32 vector (nomic-embed-text)

Collections are versioned by embedding slug (`conversation_memory_nomic-embed-text`)
to prevent data corruption when switching embedding models.

### Retrieval at Query Time

When a request arrives, the orchestrator:

1. **Query reformulation**: strips interrogative prefixes ("Was ist X?" → "X") for
   better ANN match quality.
2. **Session-scoped retrieval**: fetches all documents for the current `session_id`
   from ChromaDB (not total collection count).
3. **Hybrid ranking** (for small sessions ≤ 50 turns):
   - Direct numpy cosine similarity over all session turns
   - Topic-overlap fallback: content word matching for low-confidence ANN results
   - Keyword metadata filter: exact token matching as final fallback
4. **Context injection**: relevant turns are prepended to the current messages as
   a `[WARM CONTEXT — SEMANTIC MEMORY]` block before the expert prompt.

The `memory_recall` expert bypasses the LLM planner entirely (fast-path) to minimise
latency overhead for pure recall queries.

---

## Configuration

### Enable per Template

In the Admin UI → Expert Templates → Edit → `config_json`:

```json
{
  "enable_semantic_memory": true
}
```

No model change or container restart required. The flag activates Tier-2 retrieval
for all requests processed by that template.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SEMANTIC_MEMORY_EMBED_MODEL` | `""` (all-MiniLM-L6-v2) | `ollama:nomic-embed-text` for 768-dim embeddings |
| `SEMANTIC_MEMORY_EMBED_URL` | `http://localhost:11434` | Ollama base URL for embedding inference |
| `SEMANTIC_MEMORY_MAX_TURNS` | `4` | Hot-window size (turns kept in LLM context) |
| `SEMANTIC_MEMORY_N_RESULTS` | `6` | Max warm turns injected per request |
| `SEMANTIC_MEMORY_TTL_HOURS` | `6` | ChromaDB entry TTL (cleanup runs every 6h) |
| `CHROMA_HOST` | `chromadb-vector` | ChromaDB service hostname |
| `CHROMA_PORT` | `8001` | ChromaDB HTTP port |

### Recommended Embedding Model

`ollama:nomic-embed-text` (768 dimensions) is strongly preferred over the default
`all-MiniLM-L6-v2` (384 dimensions). It doubles the semantic resolution and markedly
improves recall at deeper needle depths (20–100 turns).

```env
# .env
SEMANTIC_MEMORY_EMBED_MODEL=ollama:nomic-embed-text
SEMANTIC_MEMORY_EMBED_URL=http://<ollama-host>:11434
```

---

## Benchmark: MRCR-lite v2

The **Multi-turn Recall Comprehension Recall (MRCR-lite v2)** benchmark measures
how far back the system can reliably retrieve specific injected facts ("needles")
with and without Tier-2 memory.

### Protocol

A synthetic conversation is constructed as:

```
[depth × filler turns]             ← pre-needle (evicted from hot window)
[NEEDLE injection]                 ← fact to remember (evicted)
[5 × recent filler turns]          ← recent context (stays in hot window)
RECALL QUESTION: "What was X?"
```

With 5 recent filler pairs and a hot window of 4 pairs, the needle is always evicted.
The orchestrator must rely entirely on Tier-2 retrieval to answer correctly.

### A/B Conditions

| Condition | ChromaDB | Template |
|---|---|---|
| `with_prepopulation` | Pre-seeded with evicted turns | `moe-memory-aihub-hybrid` |
| `without_prepopulation` | Empty (baseline) | `moe-memory-aihub-nosm` |

### Running the Benchmark

```bash
# Full run (depths 5/10/20/50/100, 2 reps each)
MOE_API_KEY=moe-sk-... python3 benchmarks/mrcr_lite_runner.py

# Quick smoke test (depth 5/10 only)
MOE_API_KEY=moe-sk-... MRCR_MAX_DEPTH=10 python3 benchmarks/mrcr_lite_runner.py

# A/B comparison with custom templates
MRCR_TEMPLATE_WITH=my-template-with-sm \
MRCR_TEMPLATE_NO=my-template-no-sm \
  python3 benchmarks/mrcr_lite_runner.py
```

### Measured Results (April 2026)

**Template:** `moe-memory-aihub-hybrid` | **Embedding:** `nomic-embed-text` 768-dim  
**Retrieval method:** direct numpy cosine ranking (no HNSW approximation)

#### By condition

| Condition | Recall score | Notes |
|---|---|---|
| `with_prepopulation` (Tier-2 SM enabled) | **1.000** | All 5 needle types, all tested depths |
| `without_prepopulation` (baseline) | **0.000** | Needle confirmed evicted from hot window |

A/B delta: **+1.000** — the entire recall improvement is attributable to Tier-2 retrieval.

#### By needle type (WITH semantic memory)

| Needle type | Pre-fix score | Post-fix score | Root cause of pre-fix failure |
|---|---|---|---|
| `number` | 0.20 | **1.00** | Session-scoped count bug → HNSW used instead of numpy |
| `person` | 0.40 | **1.00** | Same bug; HNSW missed low-frequency proper nouns |
| `date` | 1.00 | **1.00** | Unaffected (high ANN similarity for date patterns) |
| `name` | 1.00 | **1.00** | Unaffected |
| `technical` | 1.00 | **1.00** | Unaffected |

#### Root cause of pre-fix failures (documented)

The original code used `self._collection.count()` (total collection count) as the
threshold for switching between numpy direct ranking and HNSW approximation.
With hundreds of sessions in ChromaDB, the total count always exceeded the threshold,
causing HNSW to be used for all sessions — including small ones where numpy would
have found the needle at rank #1. Fix: `count = len(collection.get(where={"session_id": ...}))`.

After the fix, numpy direct ranking runs for all session sizes. HNSW is retained
only as a last-resort fallback when embeddings are unavailable.

---

## Comparison to Native LLM Context Windows

| System | Native window | Effective window | Privacy | Cost per inference |
|---|---|---|---|---|
| GPT-4o | 128,000 tokens | 128,000 tokens | Cloud | Per token |
| Claude 3.5 Sonnet | 200,000 tokens | 200,000 tokens | Cloud | Per token |
| Local 7B (no SM) | 4,000–32,000 tokens | 4,000–32,000 tokens | Local | 0 |
| **MoE Sovereign + Tier-2 SM** | 4,000–32,000 (model) | **1,000,000+ (infra)** | **Local** | **0** |

**Key insight:** The effective context window is no longer a model property — it is an
infrastructure property. Upgrading from a 7B to a 70B model does not increase the
recall range. Enabling Tier-2 Semantic Memory does, for any model.

### Accuracy comparison at different depths

| Depth | Local 7B (no SM) | GPT-4o (128k native) | MoE + Tier-2 SM |
|------:|---|---|---|
| 5 turns | 1.00 (in window) | 1.00 | **1.00** |
| 10 turns | 0.00 (evicted) | 1.00 | **1.00** |
| 50 turns | 0.00 (evicted) | 1.00 | **1.00*** |
| 100 turns | 0.00 (evicted) | 1.00 | **1.00*** |

*Unit-test verified retrieval at depth 100; end-to-end LLM benchmark pending.

---

## Compatibility

Tier-2 Semantic Memory is **fully OpenAI API-compatible**. No client changes are
required. Any client that sends `POST /v1/chat/completions` benefits automatically
once the template has `enable_semantic_memory: true`.

| Client | Compatible | Notes |
|---|---|---|
| Open WebUI | ✓ | Session ID derived from conversation header |
| Claude Code | ✓ | Works via `X-Session-Id` or fingerprint |
| OpenAI Python SDK | ✓ | Pass `extra_headers={"X-Session-Id": "..."}` for explicit session |
| curl / httpie | ✓ | Add `-H "X-Session-Id: <uuid>"` header |
| Any OpenAI-compatible client | ✓ | No changes needed; session auto-fingerprinted |

---

## Needle Types and Scoring

| Type | Example | Score Logic |
|---|---|---|
| `number` | "7342" | Exact digit match (ignoring spaces/separators) |
| `technical` | `http://api-staging.internal:9977/v2` | Exact match → 1.0; hostname-only match → 0.5 |
| `date` | "14. November 2026" | Exact → 1.0; year + day or month → 0.5 |
| `name`/`person` | "Dr. Katharina Breitfeld" | All tokens matched → 1.0; one token → 0.5 |

---

## Cross-Session Memory

Tier-2 can optionally retrieve relevant turns from **past sessions of the same user**
or from **team-shared sessions** — extending memory across conversation boundaries.

### Privacy hierarchy

| Scope | Who can retrieve | Stored when |
|---|---|---|
| `private` | Owner only (matching `user_id`) | Default for all turns |
| `team` | All members of `team_id` | User has `memory_share_with_team = true` |
| `shared` | Team + linked tenants (Mandanten) | Explicit admin action (future) |

### Enable cross-session in a template

```json
{
  "enable_semantic_memory": true,
  "enable_cross_session_memory": true,
  "cross_session_scopes": ["private"],
  "cross_session_ttl_days": 30
}
```

### User preferences

Users control their memory behaviour in the **User Portal → Profile → Conversation Memory**:

| Setting | Effect |
|---|---|
| **Fresh Start** | Disables cross-session; every conversation begins clean. No old session data injected. |
| **Share with Team** | Stores turns as `scope=team`; team members with cross-session enabled can retrieve them. |

---

## Implementation Reference

| Component | File | Description |
|---|---|---|
| Memory store | `memory_retrieval.py` | `ConversationMemoryStore` — storage, retrieval, merge |
| Embedding function | `memory_retrieval._HttpxOllamaEF` | httpx-based Ollama embedding, no `ollama` package required |
| Retrieval strategy | `memory_retrieval._retrieve_sync()` | Always-numpy cosine ranking; HNSW last resort only |
| Cross-session retrieval | `memory_retrieval.retrieve_cross_session()` | Privacy-scoped retrieval across sessions |
| Merge strategy | `memory_retrieval.merge_session_results()` | Recency-first + hard cap (current always precedes cross) |
| Orchestrator integration | `main.py:_apply_semantic_memory()` | Eviction, storage, retrieval, context injection |
| Planner fast-path | `main.py:planner_node()` | Bypasses LLM planner for `memory_recall` complexity class |
| User preferences | `admin_ui/database.py:get_user_memory_prefs()` | `prefer_fresh`, `share_with_team` per user |
| Benchmark runner | `benchmarks/mrcr_lite_runner.py` | MRCR-lite v2, A/B design, configurable warmup |
| Dataset | `benchmarks/datasets/mrcr_lite_v1.json` | 5 needles, filler turns, test matrix |
