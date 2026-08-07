# E-2.2 Trust Score and Correction Loop

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30
Level: Epic
Status: Partial

Parent: [I-2 Pipeline Quality-Gate Stack](../initiative.md)

## Outcome

Expert/tool evidence produces a deterministic, explainable trust verdict.
Borderline results receive bounded correction, policy-sensitive results use
an authorized HITL gate, and blocked drafts never escape.

## Implemented and evidenced

- trust-score calculation and state propagation;
- self-critique conditional path with bounded rounds;
- final quality gate and Constitution enforcement;
- Valkey-backed HITL gate plus authenticated owner/admin/system routes;
- usage/decision/audit fields and focused tests;
- live gate authorization and quality-block evidence in TASK-35/36.

## Remaining gaps

- TASK-37 showed that valid MCP precision evidence was not executed or
  attributed because planner tool fields were missing.
- Irrelevant GraphRAG context can influence `aux_context`/trust despite weak
  provenance.
- A correct internal thinking candidate did not reach the API before the
  900-second timeout; a second judge path consumed the remaining budget.
- Timeout usage did not reflect actual stage tokens and quality state.

## Exit criteria

- TASK-38 mixed precision/expert plan executes all tasks or reports an
  explicit repair/fallback/failure.
- Only relevant, validated MCP/retrieval evidence contributes to trust.
- A validated candidate is returned as an explicitly degraded result when
  optional refinement cannot fit the shared deadline.
- No mandatory gate or policy block leaks draft content.
- Cold/warm E2E meets TASK-38 correctness, audit, cleanup, and P95 criteria.
