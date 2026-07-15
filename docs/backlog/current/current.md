# Current Backlog Decomposition — MoE Sovereign (moe-infra)

Purpose: single entry point for all active work.

## Active Initiatives

| ID | Name | Status | Epics |
|---|---|---|---|
| I-1 | Sovereign-14B SFT Pipeline & Dynamic System Prompts | Open | E-1.1, E-1.2, E-1.3 |
| I-2 | Pipeline Quality Gate Stack | Open | E-2.1, E-2.2, E-2.3, E-2.4, E-2.5 |

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

---

### I-2: Pipeline Quality Gate Stack

**Strategic goal:** Deterministisch messbare Antwortqualität, vollständige
Compliance-Auditierbarkeit und strukturelle Resilienz in der
Planner→Experts→Judge-Pipeline. Baut auf feat/sovereign-typed-cascades-dor-constitution auf.

**Epics:**

| ID | Name | Status | Depends on |
|---|---|---|---|
| E-2.1 | Deterministische Pipeline-Signale | Open | none |
| E-2.2 | Trust-Score & Selbstkorrektur-Loop | Open | E-2.1 |
| E-2.3 | Kontext-Kontinuität & Crash-Resilienz | Open | E-2.1 |
| E-2.4 | Adaptive Komplexitätssteuerung (Cynefin) | Open | E-2.2 |
| E-2.5 | Multi-Tenant Data Isolation | Open | E-2.1 |

**Source:** adSCAILE Wiki-Analyse (2026-06-30), lokal unter
`/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/`.

## Dependency Map

```
E-1.1 (system prompt generator in dynamic_router.py)
  └── E-1.2 (dataset_generator.py produces full template JSON)
        └── E-1.3 (LUMI-G SLURM training + ONNX export → sovereign_router.onnx)

E-2.1 (Boundary Contracts, Decision Log, Scope Guard, Cascade Resolution)
  ├── E-2.2 (Trust-Score, Self-Critique, Human-Gate)
  │     └── E-2.4 (Cynefin Complexity Classification)
  ├── E-2.3 (Handover, Task Checkpointing, Artefakt-Registry)
  └── E-2.5 (Multi-Tenant Isolation)
```

## Roadmap

**I-1:**
1. E-1.1: Implement system prompt generator → verify via in-container E2E
2. E-1.2: Extend dataset generation → generate new dataset (665+ prompts)
3. E-1.3: Renew LUMI-G SSH cert → submit SLURM job → copy ONNX back → rebuild

**I-2 (empfohlene Reihenfolge nach Aufwand/Nutzen):**
1. E-2.1: Boundary Contracts + Decision Log + Scope Guard + Cascade Resolution (gering, sofort)
2. E-2.2: Trust-Score + Self-Critique + Human-Gate (mittel)
3. E-2.3: Handover + Checkpointing + Artefakt-Registry (mittel, parallel zu E-2.2 möglich)
4. E-2.4: Cynefin Complexity Classification (mittel, nach E-2.2)
5. E-2.5: Multi-Tenant Isolation (hoch, letzter Schritt)

## Stories Index

*(Populate when epic-level refinement is complete)*

## Implementation Tasks Index

*(Populate when story-level refinement is complete)*
