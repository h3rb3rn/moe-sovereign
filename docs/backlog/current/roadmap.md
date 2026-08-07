# Current Roadmap

Owner: Platform Engineering
Version: 1.2
Last verified: 2026-08-02

## Now — release blockers

1. Close COMP-01: required boundary contract loading and evaluation fail
   closed, while cascade/telemetry export failures remain independently
   degraded.
2. Keep the completed TASK-42 through TASK-50 precision release on
   `PRECISION_CACHE_POLICY=bypass` until the cache reader revalidates the full
   typed evidence envelope.
3. Expand private-template/mixed-workflow validation beyond the single fixed
   rollout scenario before a general complex-workflow readiness claim.

## Next — incomplete I-2 proof packages

1. E-2.3: add idempotent per-task checkpoints and an artifact registry with
   SHA-256 lineage; prove crash/resume behavior.
2. E-2.4: define and test the caller-visible supervised confirmation
   contract before expert cost is incurred.
3. E-2.5: implement fail-closed identity and storage namespace propagation
   across every persistence/retrieval/tool boundary.
4. Generate runtime/tool/model inventories from machine-readable sources and
   reject stale checked-in catalogs in CI.
5. Refine P1 deterministic contracts only after S-2.1.1: business calendars,
   version comparison, identifiers, statistics, geospatial calculations and
   tokenizer-versioned metrics.

## Later — model and capacity work

1. Complete and verify the active LUMI-G TASK-9 run from its owner status log.
2. Produce versioned I-1 datasets and train/evaluate the intended Sovereign
   controller; do not equate dynamic prompt generation with a deployed model.
3. Separate warm planner/judge capacity only after TASK-38 measurements show
   the remaining bottleneck and a rollback/cost plan exists.

## Release gates

- No production-ready claim while any P0 item in
  [system assessment](../../system/systembewertung_2026-07-30.md) remains.
- No multi-tenant-ready claim before E-2.5 negative E2E tests pass.
- No performance claim without dated cold/warm methodology, sample size,
  source/image version, and limitations.
- No deploy without source-to-image traceability, readiness proof, and
  rollback instructions.
