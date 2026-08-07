# E-2.3 Context Continuity and Crash Resilience

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30
Level: Epic
Status: Partial

Parent: [I-2 Pipeline Quality-Gate Stack](../initiative.md)

## Outcome

Long work can resume without duplicate mutations, and every durable output has
verifiable provenance.

## Implemented

- `services/handover.py` stores a bounded AgentState subset in Valkey.
- retry-budget stuck handling can create a handover.
- handover read/resume routes and focused round-trip tests exist.

## Not implemented as an epic package

- per-expert/task progress checkpoints;
- idempotent claim/complete protocol after a crash;
- artifact registry with SHA-256, producer, phase, and supersession lineage;
- E2E proof that resume continues work rather than merely returning state.

Handover persistence is optional and may degrade to an explicit
non-resumable result. It must never claim that resume is available after a
write failure.

## Exit criteria

- simulated crash resumes at the last committed task checkpoint without a
  duplicate tool/database mutation;
- idempotency prevents duplicate completion/audit events;
- artifact lineage is queryable and hash-verified;
- checkpoint payload, TTL, ownership, and tenant scope are bounded and
  negative-tested;
- resume behavior is exercised through the production API.
