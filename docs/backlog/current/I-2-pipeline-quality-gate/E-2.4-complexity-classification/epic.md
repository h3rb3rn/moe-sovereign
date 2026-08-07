# E-2.4 Adaptive Complexity Control

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30
Level: Epic
Status: Partial

Parent: [I-2 Pipeline Quality-Gate Stack](../initiative.md)

## Outcome

Deterministic Cynefin-style classification selects bounded autonomy and work
budgets without another classification LLM call.

## Implemented and evidenced

- `services/cynefin.py` deterministic classifier;
- classification from updated planner state and final trust state;
- state/usage propagation and focused unit/integration tests;
- conservative trivial fast-path eligibility remains a separate,
  independently tested optimization.

## Remaining gap

The original epic also promises caller-visible `supervised` confirmation
before expensive expert dispatch. That API/state-machine contract is not
demonstrated by the classifier implementation alone.

## Exit criteria

- complexity/autonomy mapping is versioned and deterministically tested;
- `supervised` mode pauses before expert cost, returns an authenticated
  approval handle, and resumes idempotently;
- missing classification cannot weaken a Constitution, tenant, `local_only`,
  or mandatory HITL decision;
- latency overhead and failure behavior are measured.
