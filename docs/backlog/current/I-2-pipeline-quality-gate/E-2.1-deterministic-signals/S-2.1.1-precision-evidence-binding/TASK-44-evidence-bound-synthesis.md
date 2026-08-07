# TASK-44 Evidenzgebundene Synthese und Direktantwort

Level: Implementation Task
Status: Done 2026-08-01

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

Reine Präzisionsanfragen ohne LLM-Synthese deterministisch ausgeben und in
gemischten Antworten jeden verpflichtenden Fakt nach der letzten LLM-
Mutation unveränderlich an typisierte MCP-Evidenz binden.

## Scope

- `main.py`, `graph/synthesis.py`, neue Precision-Render-/Binding-Module,
  `services/quality_gate.py`, `pipeline/state.py`
- Direktroute für vollständig durch Precision-Verträge abgedeckte Requests
- Strukturierte Fact-Slots für Mixed-Pläne und Post-Critic-Binding
- Auditnachweis der tatsächlich übersprungenen Modellknoten

## Out Of Scope

- Verschieben bestehender Persistenzschreibvorgänge
- Neue MCP-Fachtools
- Freiform-Reparatur eines fehlenden Toolwerts durch ein LLM

## Code / Document Anchors

- Graph construction: `main.py`
- Merger, critic and final gate: `graph/synthesis.py`
- Quality enforcement: `services/quality_gate.py`
- Typed evidence from TASK-43: `graph/tool_nodes.py`, `pipeline/state.py`

## Implementation Notes

- Direktroute nur, wenn Preflight den gesamten Nutzerauftrag vollständig und
  eindeutig abdeckt; Auth, Deadline, Access-Kind, Ledger und Audit bleiben
  aktiv.
- Deterministischer Renderer arbeitet ausschließlich auf typisierten Fakten
  und versionierten Locale-Templates. Keine semantische LLM-Nachbearbeitung.
- Mixed-Synthese referenziert opaque Fact-IDs/Slots. Nach Conflict/Critic wird
  ein eigener `precision_bind`-Knoten ausgeführt, der Werte aus Evidence
  einsetzt und Vollständigkeit prüft.
- Ein fehlender/duplizierter/kontextfalsch verwendeter Slot scheitert fail
  closed oder nutzt höchstens eine strukturierte, wertfreie Layout-Reparatur;
  Fakten dürfen nicht vom Reparaturmodell erzeugt werden.
- Quality Gate prüft Binding-Status nach dem letzten mutierenden Knoten.

## Acceptance

- Reine Precision-Anfragen erzeugen null Planner-/Expert-/Judge-/Merger-/
  Critic-Modellaufrufe und weiterhin vollständige Ledger/Evidence.
- Absichtliche Merger-/Critic-Manipulation kann Wert, Einheit, Datum, Locale
  oder Zuordnung eines Toolfakts nicht verändern.
- Mixed-Antworten bleiben sprachlich synthetisierbar, aber jeder Pflichtfakt
  ist exakt an Evidence gebunden.
- Alle API-Fassaden liefern dieselbe gebundene Semantik.

## Proof

- Syntax: `python3 -m compileall -q graph services pipeline main.py`
- Unit: `python3 -m pytest tests/test_quality_gate.py tests/test_pipeline_contracts.py tests/test_mcp_validation.py -q`
- Adversarial: Stub-Merger/-Critic mit veränderten, entfernten, duplizierten
  und kontextvertauschten Fakten
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `sudo docker compose build langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate langgraph-orchestrator`
- E2E: Audit eines reinen Requests auf null Modellcalls sowie gemischten
  Request mit mindestens einem Experten- und einem Precision-Task prüfen.

## Failure / Stop Conditions

- Stop, wenn die Direktroute einen nicht vollständig abgedeckten Auftrag
  verschluckt oder Auth/Deadline/Access-Kind umgeht.
- Stop, wenn freie String-Ersetzung nicht eindeutig zwischen gleichen Werten
  unterschiedlicher Bedeutung unterscheidet.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Alte Freitext-Injektion „exact, authoritative“ für migrierte Evidenz
  entfernen; nicht migrierte Tools klar als ungebunden markieren.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Finale Fakten kommen nur aus TASK-43-Evidence.
- Related backlog check: Sequenziell vor TASK-45 wegen Graph-Dateiüberlappung.
- Code-contract check: Alle mutierenden Knoten müssen vor Binding liegen.
- Refinement result: Aktivierung erst nach R1-Proof.

## Source Material

- Integration plan: `integration-plan.md`
- Pipeline documentation: `docs/system/pipeline.md`

## Resolution Notes

- A strict preflight classifier now selects the direct route only when the
  complete request is covered by a supported precision contract. The route
  executes MCP, deterministic locale rendering, binding and the final gate;
  it bypasses Planner, Expert, Thinking, Merger, Conflict and Critic nodes.
- Mixed plans receive opaque, value-free fact slots after workers finish.
  Merger and Critic must preserve each marker exactly once, in order and alone
  on a line. Only the post-Critic binder can replace it with a statement
  rendered from typed, current-iteration evidence. Missing, duplicate,
  changed, swapped, unknown or context-wrapped markers fail closed.
- Mandatory plain-text precision turns override a Claude-Code profile's
  `native`/reasoning mode, while real client tool and `tool_result` turns retain
  their existing contract. Chat Completions, Responses and Messages therefore
  use the same precision graph semantics.
- Adversarial tests cover removed, duplicated, changed, swapped and
  context-wrapped slots plus direct-response mutation. Full regression passed:
  **787 tests in 4.59 seconds**.
- Live direct proof on all three API facades returned the identical German GCD
  sentence with 0 model tokens and the stage sequence
  `preflight→MCP→renderer→bind→quality`. A live mixed request executed one
  `qwen3.6:35b` code-review expert and `gcd_lcm`; the isolated slot bound after
  Critic and then correctly entered the independent HITL pending gate.
- Active orchestrator image:
  `sha256:19f01440fcea67c0d35a976414a883012e63154d0caa1a20ad2ec7c0b7ae9656`,
  healthy with RestartCount 0. All temporary `horndev` keys were revoked and
  archived; temporary traces and the test-only pending gate were removed.
