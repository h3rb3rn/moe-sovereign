# I-2 Pipeline Quality-Gate Stack

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30
Level: Initiative
Status: Partial

## Strategic outcome

Make planner → tools/experts → synthesis behavior contract-valid, bounded,
observable, resumable where promised, and isolated by principal/tenant.

## Verified implementation

- Boundary checks, Definition of Ready, scope guard, decision log, cascade
  lifecycle, trust score, quality gate, HITL, self-critique, and Cynefin
  classification have production call sites and focused tests.
- TASK-35/36 live validation proved authentication/ownership for gates,
  boundary blocking/cascades, quality blocking, readiness, terminal cleanup,
  and the conservative trivial fast path.
- Handover snapshots and resume routes exist with unit coverage.

## Open proof gaps

- Required boundary configuration/check failures still fail open (COMP-01).
- TASK-37 reproduced silent precision-task loss and a 900-second template
  timeout despite a correct internal candidate (COMP-03/TASK-38).
- Handover is not the planned per-task checkpoint and artifact-provenance
  package (COMP-04).
- Full fail-closed storage/tool isolation for multiple tenants is not
  implemented or E2E-proven (COMP-02).
- Cynefin classification exists; the caller-visible supervised pre-dispatch
  contract remains unproven.

## Epics

- [E-2.1 Deterministic pipeline signals](E-2.1-deterministic-signals/epic.md)
- [E-2.2 Trust score and correction loop](E-2.2-trust-score-self-critique/epic.md)
- [E-2.3 Context continuity and crash resilience](E-2.3-context-resilience/epic.md)
- [E-2.4 Adaptive complexity control](E-2.4-complexity-classification/epic.md)
- [E-2.5 Multi-tenant data isolation](E-2.5-multi-tenant/epic.md)

## Initiative exit criteria

- Every planned task executes, is explicitly repaired/fallbacked, or returns
  a structured failure; no task disappears between nodes.
- One monotonic deadline, token/context budgets, and bounded retries apply
  across every model/tool stage.
- Trust/provenance uses only relevant validated sources and deterministic
  tool evidence.
- Mandatory security/policy/tenant/contract boundaries fail closed according
  to `PROJECT_COMPLIANCE.md`.
- E-2.3 and E-2.5 proof packages pass their negative and E2E tests.
- The complex expert-template benchmark meets TASK-38 cold/warm criteria.
