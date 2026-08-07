# Authority and Architecture — MoE Sovereign

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30

## Project Authority Model

| Layer | Authority | Notes |
|---|---|---|
| **Template authority** | `admin_expert_templates` (PostgreSQL) | Durable template policy and ownership; code/config still enforce eligibility, safety, and request constraints. |
| **Derived runtime state** | ChromaDB cache, Valkey plan cache, Thompson sampling scores | These are caches and projections — never source of truth. |
| **UI responsibility** | Admin UI surfaces state and emits intent | Must not become a hidden routing authority. |
| **Persistence truth** | PostgreSQL (`admin_expert_templates`, `dynamic_template_feedback_log`, `model_metadata`) | Durable. Not disposable. |
| **Disposable state** | Container filesystems, Valkey cache, ChromaDB collections | Rebuildable only when their durable sources and rebuild procedure are proven. |

## Core Direction

The orchestrator is a compound LangGraph pipeline:
`request boundary → guard/cache/routing → fast path or planner →
tools/experts/research → synthesis/trust/correction → quality/policy gate →
response and bounded learning/audit`.

Template-based routing is the primary mechanism. Dynamic routing (IMoE ONNX
classifier + ChromaDB cache) augments but does not replace template routing.

## Ownership Boundaries

- `services/routing.py` and `services/dynamic_router.py` own routing logic.
  No routing decisions in UI, MCP server, or graph nodes.
- `graph/synthesis.py`, `graph/expert.py`, `graph/planner.py` own pipeline
  execution. They consume routing decisions; they do not make them.
- `admin_ui/` surfaces state and accepts configuration. It does not own
  execution meaning.
- `mcp_server/` owns deterministic tool execution. New capabilities go here,
  not into `main.py`.
- `context_budget.py` is the single source of truth for context window
  resolution. `resolve_requested_ctx()` is the canonical helper.

## Anti-Patterns

- Hidden routing logic in Admin UI or graph nodes.
- Browser/session state required for backend routing correctness.
- Hardcoded server names, IPs, or model names in source.
- Consulting Ollama `/api/ps` as the budget input for PRE-FLIGHT checks
  (use `resolve_requested_ctx()` instead — see Bug B fix, 2026-06-12).
- ChromaDB query text that differs from the stored document text
  (causes permanent cache misses — see Bug C fix, 2026-06-12).
- Calling `log_dynamic_template_feedback()` without wiring its callers
  into the compile path (causes dead feedback loop — see Bug D fix, 2026-06-12).

## Pipeline shape

```
Client → auth/policy/guard → cache + route
                           ├─ eligible conservative direct path
                           └─ planner → MCP / experts / research / GraphRAG
                                      → synthesis + trust + bounded correction
                                      → quality + Constitution + optional HITL
                                      → response + audit/feedback
```

This is a conceptual ownership view, not a generated node inventory. Dynamic
framework/protocol callbacks are catalogued in
`../generated/runtime-entrypoints.md`.

## Key Invariants

- `local_only=True` must exclude all `CLOUD_ENDPOINT` models at routing time.
  Verified by checking `services/dynamic_router.py:_get_cluster_state()`.
- VRAM budget: llama3:70b-class models max ≤60 GB (`≤ safe_ctx` from
  `get_model_context_window(model)`).
- All infrastructure config is read from environment variables — no defaults
  pointing at specific servers.
- Required auth, tenant, `local_only`, schema, integrity, policy, and gate
  boundaries follow the fail-closed matrix in `PROJECT_COMPLIANCE.md`.
- A request carries one monotonic deadline through every stage and retry.
