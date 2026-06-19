# Implementation Checklist — MoE Sovereign (moe-infra)

Use this before coding and before closing implementation work.

## Before Coding

- Current story / task is refined (see refinement checklist).
- Open questions for the current seam are answered.
- Authority model understood: what is truth, what is cache, what is projection
  (`docs/ai-memory/01-authority-and-architecture.md`).
- Current code contracts read (grep / read relevant service files).
- No fallback / workaround path is being introduced.
- Verification path is known (which pytest test, which E2E command).
- Status entry written to `agent_status/<tool>.md` per Status Protocol
  (`AGENT_LASTENHEFT.md` Section 0).
- No other agent is `in_progress` on the same files (check `agent_status/`).

## During Coding

- Keep edits scoped to the current task.
- Prefer existing project patterns (async psycopg3, redis-py, openai-client).
- No hardcoded server names, IPs, or model names.
- All code, comments, and docstrings in English.
- UI strings through translation system (`t(request, 'key')`).
- Delete replaced legacy code after proof — do not comment out.

## Before Closing

- `python3 -m pytest tests/ -q` — all existing tests pass.
- Affected service rebuilt and restarted:
  `sudo docker compose build <service> && sudo docker compose up -d <service>`
- Container startup logs clean (no new errors).
- In-container E2E or live MoE-API proof run completed and documented.
- `AGENT_LASTENHEFT.md` task Status set to `done`, Resolution notes written.
- `docs/ai-memory/07-current-status-and-next-work.md` updated.
- Backlog item moved to archive if it leaves active backlog.
- `agent_status/<tool>.md` final entry written (`done` / `blocked: <reason>`).
- Run `/project:simplicity-review` on the diff. Document any finding you
  consciously reject (with reason) in the task's Resolution notes.
- GitOps: changes committed to feature branch, PR opened — never push to main.
