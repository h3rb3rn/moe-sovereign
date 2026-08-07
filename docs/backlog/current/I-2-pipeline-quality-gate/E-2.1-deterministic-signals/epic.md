# E-2.1 Deterministic Pipeline Signals

Owner: Platform Engineering
Version: 2.1
Last verified: 2026-08-01
Level: Epic
Status: Partial

Parent: [I-2 Pipeline Quality-Gate Stack](../initiative.md)

## Outcome

Required stage boundaries are deterministically validated before expensive
dispatch. Scope violations and material decisions are auditable, and cascade
events have an explicit lifecycle.

## Implemented and evidenced

- `configs/boundary_contracts.yaml` and `services/boundary_check.py`
- planner→expert and expert→judge call sites
- `services/decision_log.py` with rationale validation
- `services/scope_guard.py`
- cascade emission, open-listing, and resolution
- focused boundary, decision-log, scope-guard, and cascade tests
- live blocking/cascade evidence in TASK-35

## Remaining gap

`services/boundary_check.py` currently converts a missing/invalid contracts
file or an unexpected check exception into “no violations.” That conflicts
with the required semantics for a mandatory dispatch boundary.

The correct split is:

- contract loading and required-field evaluation: **fail closed**;
- optional cascade/Kafka/metrics export after a detected violation:
  **degraded/fail open**, without changing the block decision.

Zusätzlich schützt TASK-41 den Planner-Handoff für drei enge Precision-
Intents, aber Cache-, MCP-Ergebnis-, Synthese- und Pre-Quality-Persistenzpfade
sind noch nicht als durchgängige Evidence-Kette gebunden. Das wird in
[S-2.1.1 Verifiable Precision Execution](S-2.1.1-precision-evidence-binding/story.md)
als TASK-42 bis TASK-50 umgesetzt und bewiesen.

## Exit criteria

- Missing or invalid mandatory contract configuration prevents dispatch with
  a typed structured error.
- A validator exception cannot turn an invalid/unknown payload into a valid
  one.
- Valid payload overhead remains bounded and measured.
- Failure to publish optional cascade/telemetry does not erase the local
  violation or unblock the task.
- Negative contract, scope, cancellation, and exporter-failure tests pass.
- Verpflichtende Precision-Intents können weder durch Response-Cache noch
  durch LLM-Retry/Synthese ohne schema-valide, final gebundene MCP-Evidenz als
  Erfolg enden.
- Wiederverwendbare semantische Ergebnisse werden erst nach erfolgreichem
  finalen Quality Gate idempotent persistiert.
