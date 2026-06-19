# Current Backlog Decomposition — MoE Sovereign (moe-infra)

Purpose: single entry point for all active work.

## Active Initiatives

| ID | Name | Status | Epics |
|---|---|---|---|
| I-1 | Sovereign-14B SFT Pipeline & Dynamic System Prompts | Open | E-1.1, E-1.2, E-1.3 |

## Initiative Details

### I-1: Sovereign-14B SFT Pipeline & Dynamic System Prompts

**Strategic goal:** Train a specialized Sovereign-14B orchestrator model on
LUMI-G that dynamically generates prompt-specific system prompts for planner,
judge, and expert LLMs, replacing static template prompts with task-adapted
instructions.

**Epics:**

| ID | Name | Status | Depends on |
|---|---|---|---|
| E-1.1 | Dynamic System Prompt Generation | Open | none |
| E-1.2 | SFT Dataset Generation with Full Template JSON | Open | E-1.1 (partial) |
| E-1.3 | LUMI-G Training Run & ONNX Export | Open | E-1.2 |

**Source:** `AGENT_LASTENHEFT.md` TASK-7 (pending, unassigned).

## Dependency Map

```
E-1.1 (system prompt generator in dynamic_router.py)
  └── E-1.2 (dataset_generator.py produces full template JSON)
        └── E-1.3 (LUMI-G SLURM training + ONNX export → sovereign_router.onnx)
```

## Roadmap

1. E-1.1: Implement system prompt generator → verify via in-container E2E
2. E-1.2: Extend dataset generation → generate new dataset (665+ prompts)
3. E-1.3: Renew LUMI-G SSH cert → submit SLURM job → copy ONNX back → rebuild

## Stories Index

*(Populate when epic-level refinement is complete)*

## Implementation Tasks Index

*(Populate when story-level refinement is complete)*
