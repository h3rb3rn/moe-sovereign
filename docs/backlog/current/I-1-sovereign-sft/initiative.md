# I-1 Sovereign SFT and Dynamic Prompts

Owner: Platform Engineering / LUMI training owner
Version: 1.0
Last verified: 2026-07-30
Level: Initiative
Status: Partial

## Strategic outcome

Produce a versioned, evaluated controller artifact trained from reproducible
prompt-to-template examples. The controller may generate task-specific
planner, judge, and expert policies without becoming a monolithic replacement
for downstream experts.

## Verified current state

- Dynamic prompt-specific planner/judge/expert prompt generation is
  implemented and contract-tested (historical TASK-7).
- Dataset-generation code can emit complete template JSON.
- LUMI-G training work is active under TASK-9, but TASK-9 targets the
  paraconsistent judge dataset/model and is not proof of a deployed
  Sovereign controller.

## Remaining outcomes

1. Version and validate the controller training dataset, including schema,
   provenance, split, deduplication, and leakage checks.
2. Run reproducible controller training with pinned source, environment, and
   hyperparameters.
3. Evaluate routing/template quality against a dated baseline.
4. Package and deploy the selected artifact with source-to-model provenance,
   rollback, and cold/warm operational evidence.

## Status boundaries

- E-1.1 Dynamic system-prompt generation: **Implemented**.
- E-1.2 Controller dataset proof: **Partial**.
- E-1.3 Trained/evaluated controller deployment: **Planned**.

Do not describe LUMI-G distillation, a trained Sovereign controller, or
dynamic template generation as deployed unless the corresponding artifact and
environment have current validation evidence.
