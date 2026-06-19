# Restore Protocol — MoE Sovereign (moe-infra)

## Immediate Behavior

When context is lost, reload memory in this order:

1. `INDEX.md`
2. this file
3. `07-current-status-and-next-work.md`
4. `../../AGENT_LASTENHEFT.md` Section 1 and Section 3
5. task-specific backlog or source files

Do not restart from scratch if restored memory already anchors the current
direction. Continue from the current goal and verify against active rules.

## Working Contract

- Work toward the goal and the goal only.
- No workaround architecture. Rewrite cleanly when a seam fights the target design.
- No hardcoded server names, URLs, or model names. Configuration lives in Admin UI.
- Every `local_only=True` code path must be verified before merging — data
  sovereignty is non-negotiable.
- VRAM is a hard constraint. Never request context windows beyond node capacity
  (≤60 GB rule for llama3:70b-class models).
- Delete replaced legacy code. Archive only explicit reference material.
- Test clean. Do not recycle unknown runtime or container state for verification.
- Ask questions and challenge weak or underspecified input.
- Do not start coding while current-seam questions are unanswered.
- Before editing any file, check `../../agent_status/` for other agents marking
  the same file `in_progress`.

## Docker Rebuild Rule

After any Python or template change, rebuild and restart the affected service:

```
sudo docker compose build <service> && sudo docker compose up -d <service>
```

A plain `restart` does NOT reload `env_file` values — always use `up -d` after
`.env` changes (requires `docker compose up -d <service>` to recreate).

Service names: `langgraph-app`, `moe-admin`, `mcp-precision`.

## Status Protocol

Before starting any task from `AGENT_LASTENHEFT.md` Section 3:

1. Open `../../agent_status/<your-tool-name>.md` (create from `_template.md`
   if it does not exist).
2. Append a status entry with: timestamp (UTC), task ID, current understanding,
   short plan (3-6 bullets), pre-conditions verified.
3. Set the task's `Status:` and `Owner:` fields in Section 3.
4. Only then start working.

Never leave a task `in_progress` with no recent status entry.

## Mandatory Backlog Refinement Prep

Before refining active backlog work, read:

1. `../backlog/backlog.md`
2. `../backlog/current/current.md`
3. matching level template
4. dependency map and roadmap
5. target initiative, epic, story, and task sheets

Refinement is complete only after checking the target item against:

- the functional concept and authority model
- related backlog items, dependencies, and roadmap order
- current code contracts that implement or constrain the seam
