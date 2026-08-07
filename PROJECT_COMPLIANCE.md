# MoE Sovereign Project Compliance

Owner: Platform Engineering
Version: 1.0
Last verified: 2026-07-30
Classification: normative product and engineering policy
Review trigger: security boundary, data-flow, tenancy, inference-provider, or
deployment change

This document defines required behavior. It is not a legal certification and
does not claim blanket GDPR, EU AI Act, ISO, or other regulatory compliance.
Implementation evidence must be established separately.

## 1. Core invariants

1. **Sovereignty:** `local_only=true` prevents prompt, context, embeddings,
   tool arguments, memory, and derived content from reaching a non-local
   endpoint. Unknown endpoint locality is non-local.
2. **Identity:** every protected data-plane action has an authenticated
   principal and an explicit authorization decision. Missing identity never
   maps to an admin, system, default tenant, or global namespace.
3. **Isolation:** tenant/user scope is propagated through caches, retrieval,
   memory, GraphRAG, artifacts, feedback, audit, and tools. Cross-scope reads
   and writes are rejected and audited.
4. **Contracts:** templates, plans, precision-tool tasks, tool arguments, and
   stage payloads are validated before expensive or state-changing work.
   Invalid work is repaired once within the same deadline or ends with an
   explicit structured failure; it is never silently dropped.
5. **Integrity:** provenance and integrity failures cannot be converted into
   positive trust evidence. A hard-block source-integrity failure blocks the
   result.
6. **Human control:** a mandatory HITL gate that cannot be durably created,
   owned, or authorized blocks the response.
7. **Auditability:** security-relevant decisions record principal, request,
   policy/rule, concise rationale, outcome, and timestamp without secrets or
   hidden chain-of-thought.
8. **Bounded execution:** a request has one monotonic deadline and bounded
   token/context/retry budgets across all stages and fallbacks.

## 2. Failure-semantics matrix

| Boundary or capability | Required behavior | Notes |
|---|---|---|
| Authentication and API-key validity | Fail closed | Return 401; no pipeline entry |
| Authorization, ownership, admin/system grants | Fail closed | Return 403 or indistinguishable 404 where enumeration is a risk |
| Tenant/user namespace and cross-tenant access | Fail closed | No default/global fallback |
| `local_only` and endpoint-locality resolution | Fail closed | Unknown locality is rejected |
| Constitution/policy `block` | Fail closed | No draft content escapes |
| Mandatory HITL gate persistence/authorization | Fail closed | Structured blocked/degraded response, never the draft |
| Required template/plan/stage schema | Fail closed or one bounded repair | No silent task loss or expert dispatch with invalid input |
| Precision-tool name/arguments/result schema | Fail closed or explicit expert fallback | Fallback must be intentional, audited, and remain within deadline |
| Provenance/hash integrity hard block | Fail closed | Cannot be offset by unrelated positive factors |
| Durable security audit for state-changing action | Fail closed | Do not mutate if the required audit record cannot be written |
| Optional Llama-Guard provider/warmup | Audited degraded mode | May fail open only after deterministic auth/policy controls and with no false “passed” claim |
| Optional GraphRAG/research enrichment | Fail open as absent evidence | Mark degraded; do not count missing/irrelevant context as provenance |
| Valkey/Chroma response cache | Fail open as cache miss | PostgreSQL authority remains intact |
| Optional telemetry/metrics export | Fail open, locally logged | Never lose cleanup or security decision because an exporter is down |
| Handover/checkpoint convenience | Fail open to explicit non-resumable result | Never claim resumability when persistence failed |

“Fail open” means the optional capability is omitted and the degraded state is
observable. It never means accepting invalid identity, scope, contracts,
integrity, or policy.

## 3. Model, prompt, and tool trust

- System/developer policy and validated template policy are instructions.
  User content, retrieved content, documents, web results, model output, and
  tool output are untrusted payloads.
- Retrieved or generated text cannot grant permissions, select a secret,
  change `local_only`, choose an unapproved endpoint, or authorize another
  tool call.
- Tool dispatch uses an allowlisted registry, typed arguments, per-tool
  authorization, bounded output, and an idempotency key for mutations.
- Model-produced JSON is parsed against a versioned schema. Parsing success
  alone is not semantic validation.
- Deterministic calculations and precision-tool results are independently
  checked before they become trust evidence.
- Prompts and logs must not contain credentials or unrelated tenant data.
  Minimize context before each model call.

## 4. Secrets and sensitive data

- Secrets are supplied through the approved secret/configuration mechanism,
  never committed, pasted into fixtures, embedded in URLs, or written to
  SessionMesh/status logs.
- Display and logs use redacted identifiers only. Authorization headers and
  raw API keys are never logged.
- Temporary credentials require explicit user authorization, minimum scope,
  an expiry, revocation validation, and secure deletion of temporary files.
- Production data must not be copied to tests or external services. Use
  synthetic or irreversibly sanitized fixtures.

## 5. Reliability and resource controls

- Use typed errors across internal boundaries and a stable structured public
  error contract.
- Cleanup runs in `finally` or an equivalent guaranteed lifecycle. On
  cancellation, perform bounded cleanup and re-raise `CancelledError`.
- All network, database, model, and subprocess calls have timeouts derived
  from the remaining request/operation deadline.
- Retries use bounded attempts and backoff, consume the original deadline,
  and require idempotency. Do not retry authorization, validation, or policy
  rejections.
- Context and output limits are propagated to planner, experts, tools,
  synthesis, and judge. Large context is selected from demonstrated need,
  not template maximum.

## 6. Change and release controls

- Preserve unrelated work and use isolated worktrees for parallel agents.
- No direct push to any `main`; use a feature branch and reviewed PR.
- Data migrations require an owner, forward/backward compatibility analysis,
  dry run, backup/restore proof, and explicit operator authorization.
- A release records source commit, dependency lock, image digest, config
  version, migration state, readiness evidence, and rollback command.
- Security-sensitive changes require negative tests for unauthenticated,
  unauthorized, cross-tenant, injection, timeout, cancellation, and degraded
  dependency paths as applicable.

## 7. Evidence and claims

Use these labels consistently:

- **Implemented:** reachable in code and covered by a focused contract test.
- **Validated:** exercised end-to-end in the stated environment and version.
- **Degraded:** produces an explicit reduced-capability result.
- **Planned:** accepted backlog work with no implementation claim.
- **Research:** hypothesis or prototype without production claim.

Benchmarks state method, date, source/image version, prompt/dataset, sample
size, cold/warm condition, comparison basis, and limitations. A successful
internal candidate is not a successful API result.

## 8. Known implementation variances (2026-07-30)

| ID | Variance | Required follow-up |
|---|---|---|
| COMP-01 | `services/boundary_check.py` treats missing/invalid contract configuration and unexpected validator errors as “no violations.” | Make required contract loading/check execution fail closed at dispatch boundaries; keep cascade/telemetry emission independently fail open. |
| COMP-02 | Full fail-closed tenant propagation and storage isolation from E-2.5 are not demonstrated; current user-scoped GraphRAG fields are preparatory only. | Complete and E2E-test API identity, Chroma, Neo4j, cache, memory, and tool isolation before multi-tenant readiness is claimed. |
| COMP-03 | TASK-37 showed stage-local timeouts, silent precision-task loss, and incomplete timeout usage on the complex expert-template path. | Execute TASK-38 and meet its cold/warm P95 and failure-path criteria. |
| COMP-04 | Handover exists, but resumable per-task checkpoints and the planned artifact registry are not present as a complete E-2.3 package. | Implement or narrow the epic contract; validate idempotent resume and provenance. |

Exceptions to this policy require an explicit architecture/security decision
record with owner, expiry/review date, risk, compensating control, and tests.
