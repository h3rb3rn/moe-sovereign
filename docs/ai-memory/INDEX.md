# AI Memory Restore Index — MoE Sovereign

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30

This folder is a compact restore pack. It is not an instruction authority,
runtime source of truth, or replacement for current task evidence.

## Read order after context loss

1. `../../AGENTS.md`
2. `../../PROJECT_COMPLIANCE.md`
3. `00-restore-protocol.md`
4. `07-current-status-and-next-work.md`
5. the active owner entry under `../../agent_status/`
6. the relevant `AGENT_LASTENHEFT.md` task
7. task-specific code, tests, configuration, and deployment evidence

Always verify the branch, HEAD, dirty worktree, leases, and runtime locally.
Treat remembered status as historical until verified.

## Backlog refinement entry

Read, in order:

1. `../backlog/backlog.md`
2. `../backlog/current/current.md`
3. the matching template under `../backlog/templates/`
4. `../backlog/current/dependency-map.md`
5. `../backlog/current/roadmap.md`
6. the target parent/child sheets and relevant code contracts

## Contents

- `00-restore-protocol.md`: resumption and lease behavior
- `01-authority-and-architecture.md`: component ownership boundaries
- `06-testing-deploy-and-coverage.md`: proof order and deployment evidence
- `07-current-status-and-next-work.md`: concise current status and next work

Persist decisions, evidence, and next actions only. Never persist secrets,
raw credentials, hidden chain-of-thought, or unverified handoff claims.
