# TASK-46 Deterministische Zeit- und Zeitzonenverträge

Level: Implementation Task
Status: Completed (2026-08-01)

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

Explizite Zeit-, Datums-, Zeitzonen- und DST-Fragen über versionierte
`time_facts`-/`timezone_convert`-Verträge reproduzierbar beantworten.

## Scope

- Neue MCP-Tools, FastMCP-/REST-Registry, Beschreibungen und Router-Defaults
- IANA-Zeitzonen, UTC-Offset, DST-Fold/-Gap und expliziter Zeitbezug
- Gepinnte Zeitzonendaten im MCP-Dependency-Lock
- Typisierte Intent-Extraktoren, Verträge, Renderer und Tests DE/EN

## Out Of Scope

- Business-/Feiertagskalender und mutable Öffnungszeiten
- Implizites „jetzt“ ohne explizit dokumentierte Clock-Quelle und `as_of`
- Standort→Zeitzone-Raten ohne autoritative Geodatenquelle

## Code / Document Anchors

- MCP tools/registry: `mcp_server/server.py`
- Dependency lock: `mcp_server/requirements.lock.txt`
- Precision intent contracts: `services/pipeline/contracts.py`
- Tool docs: `docs/system/toolstack/mcp_tools.md`

## Implementation Notes

- Eingaben verwenden ISO-8601 und IANA-Zonen. Naive lokale Zeiten in einem
  DST-Fold benötigen explizites `fold`; nicht existente Gap-Zeiten scheitern.
- Clock-gebundene Antworten tragen Quelle, UTC-Instant und `as_of`; rein
  umgerechnete explizite Instants sind input-only reproduzierbar.
- Keine Plattform-Locale-Abhängigkeit; Namen/Formatierung über bewiesene
  Locale-Tabellen/Renderer.
- Größen-, Jahres- und Laufzeitgrenzen im Vertrag definieren.
- Erst Shadow-Telemetrie, dann Intent-Enforce nach Negativkorpus.

## Acceptance

- UTC↔IANA, Offsetwechsel, Schaltjahr, ISO-Woche, Fold und Gap sind exakt und
  zwischen Host/Container reproduzierbar.
- Mehrdeutige, ungültige oder implizit clock-gebundene Eingaben erzeugen
  typisierte Rückfragen/Fehler statt geratenem Wert.
- Contract-/tzdata-Version und `as_of` sind in Evidence sichtbar.

## Proof

- Syntax: `python3 -m compileall -q mcp_server services`
- Unit/property: fokussierte Zeitzonen- und Intent-Tests über DST-Grenzen
- Contract: `/tools`-Schema sowie positive/negative `/invoke`-Matrix
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `sudo docker compose build mcp-precision langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate mcp-precision langgraph-orchestrator`
- E2E: Je ein reiner und gemischter Zeitrequest über alle API-Fassaden.

## Failure / Stop Conditions

- Stop, wenn Host-TZ/Locale oder ungepinnte tzdata das Ergebnis verändern.
- Stop bei stiller Fold-Auswahl oder Normalisierung einer nicht existenten Zeit.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Überlappende untypisierte Zeit-Hilfszweige erst nach Migrationsproof entfernen.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Mutable Clockwerte werden source-/`as_of`-gebunden.
- Related backlog check: Baut auf TASK-43/44/45 auf.
- Code-contract check: Calendar-Facts-Kompatibilität und Router-Drift prüfen.
- Refinement result: Eigenständig im Shadow-Modus ausrollbar.

## Source Material

- Integration plan: `integration-plan.md`
- Offloading evaluation: `docs/system/toolstack/deterministic_offloading_evaluation_2026-07-31.md`

## Resolution

- `time_facts` und `timezone_convert` sind als vollständige, versionierte
  MCP-Verträge mit gepinntem `tzdata==2026.3`, IANA-Zonen, ISO-Instants,
  Fold-/Gap-Fehlern, `as_of`, typisierter Evidence und DE/EN-Renderern aktiv.
- Die Implementierung deckt reine Direct Responses und nummerierte Mixed-
  Requests ab. Dabei wurden drei reale Integrationslücken geschlossen:
  generische Merger duplizierten Zeitwerte, der Anthropic-Agentplan ließ
  Precision-Aufgaben aus, und die Anthropic-Fassade ignorierte das explizit
  angeforderte private Template beziehungsweise gab Quality-Blocks leer als
  HTTP 200 zurück.
- Reine Zeitabfragen bestanden Chat, Responses und Anthropic bitgleich mit
  null Modell-Tokens. Der finale gemischte Anthropic-Proof
  `msg_ba9443376ced4b9d96cbb19f` passierte MCP, Expert, isolierten Critic,
  Binding, Quality Gate und sechs Commit-Senken.
- Der abschließende TASK-50-Korpus bestätigte den Zeitvertrag erneut über alle
  drei Fassaden. Temporäre horndev-Schlüssel wurden jeweils widerrufen,
  invalidiert und archiviert.
