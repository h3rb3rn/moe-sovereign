# Backlog Index — MoE Sovereign (moe-infra)

Purpose: canonical AI-readable index for current open initiatives, epics,
stories, and implementation tasks.

## Restore Rule

Read this file first, then:

1. `current/current.md`
2. matching level template under `templates/`
3. `current/dependency-map.md`
4. `current/roadmap.md`
5. `current/stories.md`
6. `current/implementation-tasks.md`
7. `archive/archive.md` (completed work)

## Authority

1. `../../PROJECT_COMPLIANCE.md`
2. this backlog index and `current/current.md`
3. `../../docs/ai-memory/07-current-status-and-next-work.md`
4. `../../AGENT_LASTENHEFT.md` (historical task board — resolved TASKs archived here)
5. source files linked from relevant tasks

## Global Rules

- Active backlog work uses initiative, epic, story, and implementation-task levels.
- Every level has its own sheet. Use templates under `templates/`.
- Stories must be functional, testable, and clean.
- No workaround stories, fallback stories, or compatibility-layer stories.
- Refinement is complete only after checking: functional concept, related
  backlog items, dependency order, and current code contracts.
- Completed work moves to `archive/archive.md`, not the active backlog.
- AGENT_LASTENHEFT.md TASK-1 through TASK-8 (all done 2026-06-12/16) are
  the historical predecessor to this backlog — treat them as resolved archive.

## Current Initiatives

- [I-1 Sovereign-14B SFT Pipeline](current/I-1-sovereign-sft/initiative.md)
- [I-2 Pipeline Quality Gate Stack](current/I-2-pipeline-quality-gate/initiative.md)
