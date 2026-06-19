# Initiative Template — MoE Sovereign (moe-infra)

Level: Template
Applies To: Initiative

```markdown
# <Initiative ID> <Initiative Name>

Level: Initiative
Status: <Open | Blocked | Done | Archived>

Parent: Current Backlog Decomposition (`../../current/current.md`)

## Strategic Outcome

<The system or research outcome this initiative exists to achieve.>

## Value / Reason

<Why this stream matters now — in terms of model capability, routing quality,
or sovereignty compliance.>

## Risk Reduced

- <Risk reduced by completing the initiative.>

## Users / Stakeholders

- <Role, system, or downstream component affected.>

## Current Seam

<What is currently broken, uncertain, expensive, or unproven.>

## Scope

- <Included strategic capability or proof area.>

## Out Of Scope

- <Excluded capability or change.>

## Success Measures

- <Observable initiative-level success measure — prefer measurable outcomes
  (e.g. "pytest tests/ green after rebuild", "LUMI-G SLURM job COMPLETED").>

## Dependencies / Preconditions

- <External, roadmap, or system prerequisite (e.g. LUMI-G SSH cert valid,
  specific Docker service healthy).>

## Roadmap / Priority Notes

<How this initiative fits into the roadmap.>

## Refinement Check

- Authority model check (docs/ai-memory/01-authority-and-architecture.md):
- Related backlog check:
- Code-contract check:
- Refinement result:

## Epics

- <Epic ID> <Epic Name>: `<epic-folder>/epic.md`

## Source Material

- <source name>: `<path>`
```

## Level Rules

- Describe strategic outcome and risk reduction only.
- Do not include story acceptance criteria.
- Do not include implementation-task steps or file-edit plans.
- Link epics as child decomposition units.
