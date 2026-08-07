# Current Status and Next Work — MoE Sovereign

Owner: Platform Engineering
Version: 2.2
Last verified: 2026-08-02

## Current status

- Core deployment and readiness are healthy at the latest live check.
- The local full suite passed 908 tests (latest documentation-closeout rerun:
  5.96 seconds).
- TASK-42 and TASK-43 are complete: mandatory precision requests bypass
  legacy answer caches, freeze the active contract, execute against full
  JSON Schemas and carry typed, hash-bound evidence into the final gate.
- MCP and orchestrator images are healthy with RestartCount 0; the active
  orchestrator loaded 64/64 MCP tools.
- Native and conservative trivial paths respond successfully.
- The earlier TASK-37 timeout, silent malformed-task omission and incomplete
  timeout usage remain historical defects. The current fixed mixed corpus now
  completes through the private template, but one successful mixed scenario
  is not broad production-readiness evidence for every complex workflow.
- TASK-38, TASK-39 and TASK-44 are complete. Pure precision requests now use
  deterministic direct responses across all API facades; mixed requests bind
  isolated typed fact slots after the final model mutation.
- TASK-45 quality-atomic persistence is complete. Reusable semantic writes now
  occur only in the idempotent post-quality commit; Precision response caching
  remains deliberately bypassed until its reader can revalidate typed evidence.
- TASK-46 through TASK-50 are complete. Time/timezone, Decimal finance, exact
  probability and safe structured validation are enforced through typed MCP
  contracts. The fixed rollout corpus passed 13/13 API cases; practical flag
  and image rollback both passed before the final images were restored.
- Precision cache policy remains `bypass`; enabling it requires a reader that
  revalidates the complete evidence envelope.
- TASK-9 remains owned/in progress in `agent_status/agy.md`; verify remote
  LUMI state with its owner before changing training artifacts.
- The working tree is intentionally dirty and the current branch upstream is
  gone. Preserve unrelated changes and do not push.

## Next work

1. Use rollout telemetry to select and specify the next P1 deterministic
   contract: business calendar, version comparison, identifier validation,
   advanced statistics, geospatial calculation or tokenizer metrics.
2. Add full evidence-envelope revalidation to the Precision cache reader
   before changing `PRECISION_CACHE_POLICY=bypass`.
3. Broaden mixed/private-template benchmarks beyond the single validated
   Decimal + probability + code-review workflow.
4. Complete E-2.3 checkpoints/artifact lineage and E-2.5 tenant isolation
   before resilience or multi-tenant readiness claims.
5. Reconcile LUMI TASK-9 from the current remote scheduler state.

## Primary references

- `../../AGENTS.md`
- `../../PROJECT_COMPLIANCE.md`
- `../../AGENT_LASTENHEFT.md` TASK-37 through TASK-39
- `../backlog/current/roadmap.md`
- `../system/systembewertung_2026-07-30.md`

Do not restore old “follow-ups” already superseded by TASK-35/36 evidence.
Do not treat this summary as permission for credentials, deployment,
migration, deletion, push, or external publication.
