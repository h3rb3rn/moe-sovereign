# TASK-43 Versionierte MCP-Verträge und typisierte Evidenz

Level: Implementation Task
Status: Done 2026-08-01

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

MCP-Discovery und Invoke um vollständige, versionierte Ein-/Ausgabeverträge
und einen kompatiblen strukturierten Ergebnisumschlag erweitern, den der
Orchestrator als unveränderliche Evidenz validiert und speichert.

## Scope

- `mcp_server/server.py`, MCP-Vertragsmodule und Dependency-Lock
- Orchestrator-Katalogloader in `main.py`, `graph/tool_nodes.py`,
  `pipeline/state.py`
- Input-/Output-JSON-Schema-Validierung, Contract-/Katalog-/Result-Hashes
- Migration der TASK-41-Verträge `gcd_lcm`, `unit_convert`, `calendar_facts`

## Out Of Scope

- Neue fachliche Tools und Intentklassen
- Deterministischer Renderer, gemischte Fact-Slots und Post-Quality-Commit
- Entfernen des Legacy-`result`-Felds in `/invoke`

## Code / Document Anchors

- MCP discovery/invoke: `mcp_server/server.py`
- Runtime catalog loading: `main.py`
- Tool execution/evidence: `graph/tool_nodes.py`
- Tool contracts documentation: `docs/system/toolstack/mcp_tools.md`

## Implementation Notes

- MCP ist Autorität für ausführbare Vertragsmetadaten. Lokale Intent-
  Extraktoren referenzieren Contract-IDs, duplizieren aber keine Outputschema-
  Wahrheit.
- `/tools` um Contract-ID/-Version, vollständiges Input-/Output-Schema,
  Determinismus- und Source-Klasse, Limits, Retry-/Cache-Policy sowie
  kanonischen Hash erweitern.
- Vor Invoke vollständiges JSON Schema validieren: Typen, Enums, Formate,
  Grenzen, Required und `additionalProperties` entsprechend Vertrag.
- `/invoke` behält `result` und ergänzt `structured_result` mit normalisiertem
  Input, typisierten Fakten, Source/`as_of`, Warnungen und Result-Hash.
- Tool-Output ebenfalls gegen Output-Schema validieren. Fehlerantworten
  besitzen einen eigenen typisierten Error-Umschlag und können nie Evidence-
  Status `completed` erzeugen.
- Evidence nicht semantisch kürzen; getrennte begrenzte Prompt-/UI-Projektion
  verwenden. Größe und sensible Felder per Vertrag beschränken.

## Acceptance

- Die drei migrierten Tools liefern alten Clients weiterhin `result`, neue
  Clients erhalten zusätzlich schema-valide strukturierte Fakten.
- Jeder negative Input-/Output-Schemafall scheitert mit stabilem Fehlercode.
- Katalog-, Contract-, Input- und Result-Hash stimmen zwischen Discovery,
  Invoke, Task-Ereignis und Evidence überein.
- Ein Reload ersetzt Vertragsmetadaten atomar; In-Flight-Requests erkennen
  Snapshot-Drift.

## Proof

- Syntax: `python3 -m compileall -q mcp_server graph pipeline main.py`
- Unit: `python3 -m pytest tests/test_mcp_validation.py tests/test_mcp_access_kind.py tests/test_pipeline_contracts.py -q`
- Contract: direkte `/tools`- und `/invoke`-Negativmatrix für alle drei Tools
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `sudo docker compose build mcp-precision langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate mcp-precision langgraph-orchestrator`
- E2E: Live-Discovery und Invoke auf Legacy- und Structured-Feld prüfen; Image-
  IDs, Hashes, Readiness und RestartCount dokumentieren.

## Failure / Stop Conditions

- Stop, wenn ein Output trotz Schemafehler als erfolgreiche Evidence gilt.
- Stop, wenn eine Contract-Version nicht reproduzierbar gehasht oder atomar
  geladen werden kann.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Primitive Required-only-Sondervalidierung nach vollständiger Migration
  entfernen; Legacy-`result` erst in einem später angekündigten API-Break.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: MCP-Katalog bleibt Autorität für aktive Tools.
- Related backlog check: Voraussetzung für TASK-44 und TASK-46 bis TASK-49.
- Code-contract check: REST- und FastMCP-Registry müssen dieselben Metadaten tragen.
- Refinement result: Separates kompatibles Plattform-Release.

## Source Material

- Integration plan: `integration-plan.md`
- MCP documentation: `docs/system/toolstack/mcp_tools.md`

## Resolution Notes

- MCP discovery now publishes full input/output schemas, semantic contract
  identity, canonical hash, determinism/source model, normalization, retry,
  cache and size policies for `calendar_facts`, `gcd_lcm` and `unit_convert`.
- `/invoke` remains compatible through `result` and adds schema-validated
  typed facts, normalized input, runtime source/version, warnings and a result
  hash. Stable input-, execution-, result- and output-error codes never create
  completed evidence.
- The orchestrator atomically replaces the validated live catalogue, freezes
  its contract metadata in request preflight, validates structured results and
  stores full bounded evidence. The final gate detects contract, input, facts,
  source and result-hash manipulation.
- Focused proof: 118 tests. Full regression: 772 passed in 4.24 seconds.
  Compileall, governance 27/9, MkDocs strict, Compose config and both runtime
  `pip check` runs passed.
- Live MCP negative matrix covered missing, type, enum, range, unknown-field,
  empty-unit and incompatible-dimension cases. Discovery/invoke hashes matched
  for all three contracts. In-container GCD evidence passed with 23; changing
  it to 29 was blocked as `precision_evidence_mismatch`.
- Deployed images: MCP
  `sha256:f2e172d23b0c745c85c3d6bf37495ae2d95d9d403c42ad5508708c59a637676c`
  and orchestrator
  `sha256:919022098988d3fd198af20c8b6b8c5a5a7912c9bb7bbfce5a606506541a6956`;
  both healthy with RestartCount 0, and 59/59 tools loaded.
