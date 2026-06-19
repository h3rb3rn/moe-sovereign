# Story Template — MoE Sovereign (moe-infra)

Level: Template
Applies To: Story

```markdown
# <Story ID> <Story Name>

Level: Story
Status: <Open | Blocked | Done | Archived>

Parent Epic: <Epic ID> <Epic Name> (`../epic.md`)

## Behavior Outcome

As a <role / system component>, I can <capability / behavior>, so that
<value / result>.

## Value / Reason

<Why this behavior matters and which parent epic outcome it supports.>

## Current Seam

<Current failure, gap, or unproven behavior.>

## Preconditions

- <What must be true before this story can be worked or proven.>

## Acceptance Criteria

- <Functional condition that must be true when the story is done.>

## Proof Boundary

<How this story is proven and what proof layer owns it (unit / in-container /
live MoE-API).>

## Non-Goals

- <Behavior, implementation, or proof intentionally excluded.>

## Dependencies

- <Stories, systems, or proof gates this story depends on.>

## Refinement Check

- Authority model check:
- Related backlog check:
- Code-contract check:
- Refinement result:

## Implementation Tasks

- <Task ID> <Task Name>: `<task-file>.md`

## Source Material

- <source name>: `<path>`
```

## Level Rules

- Describe behavior outcome and acceptance, not engineering steps.
- Stories must be functional and testable.
- Stories must not encode workarounds, fallback routes, or compatibility layers.
- Proof boundary must name the exact layer (unit test / in-container E2E /
  live MoE-API) — do not leave it open.
- Link implementation tasks as child decomposition units.
