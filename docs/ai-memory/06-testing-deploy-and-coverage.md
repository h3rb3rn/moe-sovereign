# Testing, Deployment, and Proof — MoE Sovereign

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30

## Verification order

| Layer | Proof | When |
|---|---|---|
| 1 | Syntax, schema, generated-file, governance, and diff checks | Every relevant change |
| 2 | Focused unit/contract tests | Before any rebuild |
| 3 | Full relevant test suite | Before deployment claim |
| 4 | Build/recreate affected service and inspect startup/readiness | Runtime/image/config/dependency change only |
| 5 | API, persistence, authorization, and failure-path integration | Changed seam |
| 6 | Real MoE-API E2E, including cold/warm runs | Routing/model/deadline/performance change |
| 7 | External/LUMI-G proof | Only after local layers pass and with explicit scope |

Do not rebuild first and then use the rebuilt service as the only proof. Do
not use an unknown stale container for deployment parity.

## Current verified baseline

- Full local suite: **669 passed** on 2026-07-29 (TASK-36).
- Core image:
  `sha256:286a5752e829e3dff0366f4faa3791f20a7d603bfd3546feef34d33c7e4e53f9`.
- Core readiness: graph, Valkey, user DB, Neo4j, MCP, and Chroma positive.
- Conservative trivial request: HTTP 200/exact `OK` in 10.27 seconds.
- Complex private template: not production-ready; TASK-37 returned HTTP 504
  after 900 seconds despite a correct internal candidate.

These are dated facts, not permanent guarantees. Re-verify after relevant
changes.

## Service names

| Compose service | Runtime role |
|---|---|
| `langgraph-app` | core orchestrator (`langgraph-orchestrator` container) |
| `moe-admin` | Admin UI |
| `mcp-precision` | deterministic MCP tools |

## Required evidence for “done”

- command and exact result;
- source/commit or dirty snapshot identity;
- container image digest when deployed;
- readiness and rollback;
- negative/failure paths;
- terminal cleanup and audit;
- benchmark method/sample/cold-warm conditions where performance is claimed.

For governance/backlog changes run
`python3 scripts/check_governance.py --check` and a MkDocs build. No service
rebuild is required.
