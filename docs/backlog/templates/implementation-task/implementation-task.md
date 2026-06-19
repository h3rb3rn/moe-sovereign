# Implementation Task Template — MoE Sovereign (moe-infra)

Level: Template
Applies To: Implementation Task

```markdown
# <Task ID> <Task Name>

Level: Implementation Task
Status: <Open | Blocked | Done | Archived>

Parent Story: <Story ID> <Story Name> (`story.md`)

## Objective

<Concrete engineering objective for this task.>

## Scope

- <Files, scripts, tests, or docs in scope.>

## Out Of Scope

- <Changes explicitly excluded from this task.>

## Code / Document Anchors

- <file or document>: `<path>`

## Implementation Notes

- <Concrete steps, constraints, or sequencing for the implementation.>

## Acceptance

- <Task-level condition that must be true.>

## Proof

- Syntax: `python3 -m py_compile <file>`
- Unit: `python3 -m pytest tests/<relevant_test_file> -q`
- Rebuild: `sudo docker compose build <service> && sudo docker compose up -d <service>`
- E2E: `<exact command or curl call that proves the behavior>`
- <Any additional evidence required>

## Failure / Stop Conditions

- <When to stop instead of continuing with assumptions or workarounds.>
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- <Legacy code / docs to delete, archive, or update after proof.>
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check:
- Related backlog check:
- Code-contract check:
- Refinement result:

## Source Material

- <source name>: `<path>`
```

## Level Rules

- Describe executable work only.
- Name relevant files, scripts, commands, tests, and cleanup steps.
- Always include the Docker rebuild step in Proof.
- Do not redefine parent story, epic, or initiative meaning.
- Include failure / stop conditions.
- Delete or archive replaced legacy material after proof.
