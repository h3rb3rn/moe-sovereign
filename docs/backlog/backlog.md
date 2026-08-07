# Backlog Index — MoE Sovereign

Owner: Platform Engineering
Version: 2.0
Last verified: 2026-07-30

This is the canonical index for intended work. It does not override tested
runtime evidence or the normative project rules.

## Restore order

1. `../../AGENTS.md`
2. `../../PROJECT_COMPLIANCE.md`
3. [Current backlog](current/current.md)
4. [Dependency map](current/dependency-map.md)
5. [Roadmap](current/roadmap.md)
6. [Stories index](current/stories.md)
7. [Implementation-task index](current/implementation-tasks.md)
8. [Archive](archive/archive.md)

Use the matching level template from
[decomposition templates](templates/decomposition-templates.md) before
refining an initiative, epic, story, or implementation task.

## Evidence rules

- Current implementation claims require a reachable production call path and
  focused contract/integration evidence.
- “Defined”, “importable”, or “unit-tested in isolation” is not an
  end-to-end claim.
- Required behavior comes from `PROJECT_COMPLIANCE.md`; current behavior that
  violates it is a gap, not an implicit policy exception.
- Completed work moves to the archive only after the applicable exit criteria
  pass. Historical detail remains in `AGENT_LASTENHEFT.md`.
- Update the dependency map and roadmap in the same change when scope,
  dependency order, status, or proof gates change.

## Current initiatives

- [I-1 Sovereign SFT and dynamic prompts](current/I-1-sovereign-sft/initiative.md)
- [I-2 Pipeline quality-gate stack](current/I-2-pipeline-quality-gate/initiative.md)
