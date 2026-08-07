# Refinement Checklist — MoE Sovereign

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30

Use this before declaring any backlog item refined.

## Required Reads

- `AGENTS.md`
- `PROJECT_COMPLIANCE.md`
- `docs/ai-memory/INDEX.md`
- `docs/ai-memory/01-authority-and-architecture.md`
- `docs/backlog/backlog.md`
- `docs/backlog/current/current.md`
- matching level template under `docs/backlog/templates/`
- dependency map and roadmap
- parent and child backlog sheets
- relevant code contracts (grep / read service files for the seam)

## Required Checks

- Correct decomposition level (initiative / epic / story / task).
- No lower-level leakage (initiative does not contain task steps).
- Authority model checked: which component owns the seam?
- Functional concept checked against `01-authority-and-architecture.md`.
- Related backlog items and dependency order checked.
- Current code contracts checked (no seam left unread).
- Source material linked.
- Open questions captured.
- `local_only` compliance implications considered.
- VRAM budget implications considered.
- No hardcoded infra introduced.
- Dependency map and roadmap updated if scope / order / gates changed.
- Failure semantics checked against the compliance matrix; no blanket
  fail-open rule on a mandatory boundary.
- Prompt/retrieval/tool output treated as untrusted data.
- Deadline, retry idempotency, cancellation, tenant, secret, and audit
  implications considered where relevant.
- Production claims name their proof layer and date.
- Links validated.

## Refinement Result

Record:

- what changed in the refined item
- what did not change
- remaining open questions
- next decomposition level to refine
