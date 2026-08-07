# Current Backlog — MoE Sovereign

Owner: Platform Engineering
Version: 2.1
Last verified: 2026-08-01

## Status vocabulary

| Status | Meaning |
|---|---|
| Planned | Accepted intent; no implementation claim |
| Partial | Some reachable implementation exists, but the epic proof package or exit criteria are incomplete |
| Implemented | Production call path and focused contract tests exist |
| Validated | Stated behavior was also exercised end-to-end in the named environment/version |
| Blocked | Progress requires an external state change or explicit decision |

## Active initiatives

| ID | Initiative | Status | Current proof/gap |
|---|---|---|---|
| I-1 | [Sovereign SFT and dynamic prompts](I-1-sovereign-sft/initiative.md) | Partial | Dynamic prompt/template generation is implemented; the complete trained Sovereign controller artifact is not validated |
| I-2 | [Pipeline quality-gate stack](I-2-pipeline-quality-gate/initiative.md) | Partial | Core quality controls are reachable; boundary failure semantics, complex-template E2E, checkpoint/artifact, and full tenancy remain open |

## I-2 epic status

| ID | Epic | Status | Evidence boundary |
|---|---|---|---|
| E-2.1 | [Deterministic pipeline signals](I-2-pipeline-quality-gate/E-2.1-deterministic-signals/epic.md) | Partial | Boundary, decision, scope, cascade and narrow intent guard exist; COMP-01 and end-to-end precision evidence binding remain |
| E-2.2 | [Trust score and correction loop](I-2-pipeline-quality-gate/E-2.2-trust-score-self-critique/epic.md) | Partial | Trust/HITL are live-tested; complex-template completion and provenance are TASK-38 gaps |
| E-2.3 | [Context continuity and crash resilience](I-2-pipeline-quality-gate/E-2.3-context-resilience/epic.md) | Partial | Handover exists; resumable task checkpoints and artifact registry do not |
| E-2.4 | [Adaptive complexity control](I-2-pipeline-quality-gate/E-2.4-complexity-classification/epic.md) | Partial | Cynefin classification is integrated; supervised pre-dispatch contract is not proven |
| E-2.5 | [Multi-tenant data isolation](I-2-pipeline-quality-gate/E-2.5-multi-tenant/epic.md) | Planned | User-scoped fields are preparatory, not proof of fail-closed storage isolation |

See the [roadmap](roadmap.md), [dependency map](dependency-map.md), and
[system assessment](../../system/systembewertung_2026-07-30.md) for
priorities and operational evidence.
