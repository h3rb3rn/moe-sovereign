# Epic Template — MoE Sovereign (moe-infra)

Level: Template
Applies To: Epic

```markdown
# <Epic ID> <Epic Name>

Level: Epic
Status: <Open | Blocked | Done | Archived>

Parent Initiative: <Initiative ID> <Initiative Name> (`../initiative.md`)

## Epic Goal

<The capability or proof package this epic delivers.>

## Initiative Alignment

<How this epic supports the parent initiative outcome and risk reduction.>

## Users / Consumers

- <Who consumes this capability or proof.>

## Capability / Proof Package

<The coherent capability, proof package, or behavior area owned by this epic.>

## Scope

- <Included epic-level capability or proof boundary.>

## Out Of Scope

- <Excluded capability or proof boundary.>

## Dependencies / Preconditions

- <Inputs, completed stories, system capabilities, or environment prerequisites
  (e.g. LUMI-G SSH cert, specific service healthy, prior epic done).>

## Current Status

<Known current state, evidence, blockers, and remaining seam.>

## Known Facts / Evidence

- <Evidence from code, tests, runtime, docs, or prior proof.>

## Success Semantics

- <What is true when the epic succeeds — prefer exact commands or log patterns.>

## Failure Semantics

- <How failure is recognized: failing test, error log pattern, wrong rowcount.>

## Downstream Contract

<What this epic unlocks and what it does not imply.>

## Quality Constraints

- <Non-negotiable quality, simplicity, performance, or architecture constraint.>
- VRAM budget respected (≤60 GB for 70B-class models).
- `local_only` compliance maintained.
- No hardcoded infra in new code.

## Risks / Assumptions

- <Risk or assumption that may affect delivery or proof.>

## Refinement Check

- Authority model check:
- Related backlog check:
- Code-contract check:
- Refinement result:

## Exit Criteria

- `pytest tests/ -q` passes after rebuild.
- In-container E2E confirms the capability end-to-end.
- AGENT_LASTENHEFT.md updated (if task is tracked there).

## Story Breakdown

- <Story ID> <Story Name>: `<story-folder>/story.md`

## Source Material

- <source name>: `<path>`
```

## Level Rules

- Describe one coherent capability or proof package.
- Keep completion criteria at epic level.
- Do not include implementation-task breakdown.
- Do not include file-edit steps.
- Link stories as child decomposition units.
