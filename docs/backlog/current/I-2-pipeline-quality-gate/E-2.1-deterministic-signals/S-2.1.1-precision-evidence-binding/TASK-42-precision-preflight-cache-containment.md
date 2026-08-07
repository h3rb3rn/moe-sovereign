# TASK-42 Precision Preflight und Cache-Containment

Level: Implementation Task
Status: Done

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

Verpflichtende Precision-Intents vor jedem Antwortcache erkennen und bis zu
Task-Ledger und MCP-Evidenz fail closed nachverfolgen, sodass kein Cache-,
Planner- oder Retry-Pfad TASK-41 umgehen kann.

## Scope

- `pipeline/state.py`, `graph/router_nodes.py`, `graph/planner.py`,
  `graph/tool_nodes.py`, `services/quality_gate.py`, `main.py`
- Planner-/Response-Cache-Fingerprint und fokussierte Contract-, Cache-,
  Worker- und Quality-Gate-Tests
- Konfigurierbarer Legacy-Cache-Bypass für verpflichtende Precision-Intents

## Out Of Scope

- Neuer MCP-Ergebnisumschlag, direkte deterministische Ausgabe und neue Tools
- Verschieben der Learning-Persistenz
- Breitere Intent-Allowlist als die drei in TASK-41 bewiesenen Verträge

## Code / Document Anchors

- Response cache routing: `graph/router_nodes.py`
- Planner handoff/guard: `graph/planner.py`, `services/pipeline/contracts.py`
- MCP retry/evidence: `graph/tool_nodes.py`
- Final enforcement: `services/quality_gate.py`, `graph/synthesis.py`

## Implementation Notes

- `precision_preflight` direkt nach Guard und vor L0/L1 einfügen; er schreibt
  immutable Sollintents, Sollargumente, Katalog- und Contract-Hash in State.
- Für Pflichtintents Legacy-L0/L1-Antwortcache sperren. Ein späterer
  typisierter Cache ist nicht Teil dieses Tasks.
- Planner muss den Snapshot übernehmen und darf Intent nicht neu, abweichend
  erkennen. Planner-Cache-Key über vollständigen kanonischen Katalogvertrag
  invalidieren.
- Für Pflichtverträge keine Judge-generierte Argumentänderung erlauben.
  Retry nur mit identischem normalisiertem Input; Drift typisiert blockieren.
- Quality Gate prüft je Sollintent genau eine passende geplante Aufgabe, ein
  terminal erfolgreiches Task-Ereignis und MCP-Evidenz mit identischen
  Tool-/Argument-/Contract-Hashes.
- Fehlercodes mindestens: `precision_evidence_missing`,
  `precision_evidence_mismatch`, `precision_contract_changed`.

## Acceptance

- Ein absichtlich falscher L0-/L1-Cache kann keinen der drei TASK-41-Intents
  als Erfolg beantworten.
- Katalog-/Schemawechsel und Judge-Argumentdrift werden fail closed erkannt.
- Nicht-Precision-Anfragen behalten den bestehenden Cache-Pfad.
- Alle drei unterstützten Intents besitzen eine lückenlose
  Intent→Task→Evidence-Korrelation.

## Proof

- Syntax: `python3 -m compileall -q pipeline graph services main.py`
- Unit: `python3 -m pytest tests/test_pipeline_contracts.py tests/test_cache_bypass.py tests/test_mcp_validation.py tests/test_quality_gate.py -q`
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `sudo docker compose build langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate langgraph-orchestrator`
- E2E: Im Container falschen Cache, richtigen Plan, General-Downgrade,
  Argumentdrift und Katalogreload gegen die drei aktiven Verträge prüfen.

## Failure / Stop Conditions

- Stop, wenn ein verpflichtender Intent noch vor Preflight als Cache-Erfolg
  endet oder ein nicht-Precision-Cache regressiert.
- Stop, wenn Task-/Evidence-Korrelation nicht ohne Prompt-/Secret-Logging
  möglich ist.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Obsolete Precision-Erkennung in nachgelagerten Cache-/Planner-Zweigen
  entfernen, sobald alle Aufrufer den Snapshot nutzen.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Ableitung aus verifiziertem Cache-/Planner-/Worker-Pfad.
- Related backlog check: Baut auf TASK-41 auf; kein neuer TASK-38-Scope.
- Code-contract check: Alle Cache-Routen und `_prepare_handoff_plan`-Aufrufer erneut prüfen.
- Refinement result: Erstes, eigenständig rollbackbares P0-Release.

## Source Material

- Integration plan: `integration-plan.md`
- TASK-41 record: `AGENT_LASTENHEFT.md`
