# Backlog Decomposition Templates — MoE Sovereign (moe-infra)

Purpose: canonical template index for active backlog sheets.

Every active backlog item must preserve its decomposition level: initiative,
epic, story, or implementation task. Each level has its own folder and template.

## Template Read Order

1. [Initiative Template](initiative/initiative.md)
2. [Epic Template](epic/epic.md)
3. [Story Template](story/story.md)
4. [Implementation Task Template](implementation-task/implementation-task.md)

## Level Boundaries

- Initiative: strategic outcome area and risk reduction.
- Epic: coherent capability or proof package inside an initiative.
- Story: user-visible or system-visible behavior outcome.
- Implementation task: executable engineering work, Docker rebuild, and proof.

## Leakage Rules

- Initiative sheets must not contain story acceptance or task execution steps.
- Epic sheets must not contain implementation-task breakdown or file-edit steps.
- Story sheets may list implementation tasks but must not replace task sheets.
- Implementation-task sheets must not redefine parent meaning.
- If scope, priority, dependency order, or proof gates change, update
  `current/dependency-map.md` and `current/roadmap.md` in the same batch.

## Refinement Checklist

Before declaring any backlog item refined:

- Confirm the sheet level.
- Read this template index and the matching level template.
- Read the parent sheet and existing child sheets.
- Apply the matching template.
- Check for lower-level leakage.
- Check against `docs/ai-memory/01-authority-and-architecture.md`.
- Check against related backlog items, dependencies, and roadmap order.
- Check against current code contracts (grep / read relevant service files).
- Validate all links.
- Update indexes when paths or counts change.
- Update roadmap and dependency map when scope, order, or proof gates change.
