# Current Dependency Map

Owner: Platform Engineering
Version: 1.1
Last verified: 2026-08-01

This map describes delivery/proof dependencies, not only code imports.

```mermaid
flowchart TD
  G[TASK-39: Agent Rules 2.0] --> C[Compliance and governance checks]
  C --> T38[TASK-38: bounded expert-template path]
  T38 --> P[Complex-path production proof]

  E21[E-2.1 deterministic signals] --> E22[E-2.2 trust and correction]
  E21 --> E23[E-2.3 context resilience]
  E21 --> E25[E-2.5 tenant isolation]
  E22 --> E24[E-2.4 complexity and autonomy]

  C1[COMP-01 fail-closed required boundaries] --> E21
  T41[TASK-41: narrow precision intent guard] --> T42[TASK-42: preflight and cache containment]
  T42 --> T43[TASK-43: versioned MCP contracts]
  T43 --> T44[TASK-44: evidence-bound synthesis]
  T44 --> T45[TASK-45: quality-atomic persistence]
  T45 --> T46[TASK-46: time and timezone]
  T45 --> T47[TASK-47: decimal finance]
  T45 --> T48[TASK-48: exact probability]
  T45 --> T49[TASK-49: structured validation]
  T46 --> T50[TASK-50: rollout proof]
  T47 --> T50
  T48 --> T50
  T49 --> T50
  T50 --> E21
  P --> E22
  CP[Task checkpoints and idempotency] --> E23
  AR[Artifact registry and provenance] --> E23
  TI[Identity and storage namespace proof] --> E25

  I11[I-1 dynamic prompts] --> I12[I-1 dataset proof]
  I12 --> I13[I-1 trained controller artifact]
```

## Gates

| Consumer | Required predecessor evidence |
|---|---|
| Complex expert-template production readiness | TASK-38 cold/warm E2E, no silent task loss, one propagated deadline, complete usage/audit |
| E-2.1 completion | Required boundary configuration/check errors fail closed; emission/export failures may degrade independently; S-2.1.1 proves precision intent→contract→evidence→output→commit |
| E-2.2 completion | Precision/MCP provenance contributes correctly to trust and a valid candidate reaches the API within budget |
| E-2.3 completion | Idempotent checkpoint resume and SHA-256 artifact lineage, not handover alone |
| E-2.4 completion | Caller-visible supervised confirmation before expensive dispatch |
| E-2.5 completion | Negative cross-tenant tests across API, PostgreSQL, Valkey, ChromaDB, Neo4j, memory, and tools |
| I-1 completion | Versioned dataset, reproducible training, evaluated artifact, deployment/rollback evidence |

The normative failure semantics are defined in
`../../../PROJECT_COMPLIANCE.md`.
