# TASK-49 Strukturierte Validierung

Level: Implementation Task
Status: Completed (2026-08-02)

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

JSON-, YAML-, XML- und CSV-Eingaben sicher parsen und gegen explizite,
netzwerkfreie Verträge validieren, statt Struktur oder Gültigkeit vom LLM
raten zu lassen.

## Scope

- MCP-Vertrag `structured_validate` mit formatspezifischen Operationen
- Sichere Parser, JSON-Schema-Validierung und strukturierte Fehlerpositionen
- Payload-, Tiefe-, Entity-, Zeilen-/Spalten- und Laufzeitgrenzen
- Intent-Extractor, Renderer, Unit-/Fuzz-/E2E-Tests

## Out Of Scope

- Ausführen eingebetteter Inhalte, Makros, Formeln oder Tags
- Remote Schema-/DTD-/XInclude-Auflösung
- Automatische Reparatur oder semantische Migration invalider Nutzdaten

## Code / Document Anchors

- MCP implementation/registry: `mcp_server/server.py`
- MCP lock: `mcp_server/requirements.lock.txt`
- Contract validation: TASK-43 modules and `graph/tool_nodes.py`

## Implementation Notes

- JSON streng parsen; Schema-Refs ausschließlich aus erlaubtem lokalen
  Registry-Snapshot. Keine Netzwerkauflösung.
- YAML nur Safe Loader, unbekannte/ausführbare Tags ablehnen und Alias-/Depth-
  Limits setzen.
- XML DTD, externe Entities, XInclude und Entity Expansion deaktivieren;
  sichere Bibliothek exakt locken, falls Standardbibliothek nicht genügt.
- CSV benötigt explizite oder sicher erkannte Dialektgrenzen und meldet
  Formelpräfixe als Datenrisiko, führt sie aber niemals aus.
- Ergebnis liefert `valid`, Format, Schema-/Payload-Hash und begrenzte
  strukturierte Fehler; niemals vollständige sensible Payload in Telemetrie.

## Acceptance

- XXE/Billion-Laughs, YAML-Tag-/Alias-Angriffe, tiefe JSON-Strukturen,
  remote Refs und übergroße CSVs werden vor gefährlicher Verarbeitung
  blockiert.
- Valide Dokumente und präzise Fehlerpositionen sind reproduzierbar.
- Das Tool validiert nur; es behauptet keine fachliche Wahrheit des Inhalts.
- Finale Validierungsaussage ist an Result-Hash und Contract gebunden.

## Proof

- Syntax: `python3 -m compileall -q mcp_server services`
- Unit/fuzz: sichere Parser- und Größenlimitmatrix für alle vier Formate
- Dependency: `pip check` und Prüfung exakt gelockter Parserabhängigkeiten
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `sudo docker compose build mcp-precision langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate mcp-precision langgraph-orchestrator`
- E2E: Valide und bösartige Payload je Format über authentifizierte Live-API.

## Failure / Stop Conditions

- Stop bei Netzwerkzugriff, DTD/Entity-Auswertung, unsafe YAML oder
  ungebundener Payload-/Tiefe-/Laufzeit.
- Stop, wenn rohe sensible Payloads in Metrics/Audit gelangen.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Unsichere/duplizierte Parserpfade nach Migration löschen und Locks dokumentieren.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Validierung bestätigt Struktur, nicht Semantik.
- Related backlog check: Baut auf TASK-43/44/45 auf.
- Code-contract check: Parserabhängigkeiten und Container-Limits gemeinsam prüfen.
- Refinement result: Eigenständig im Shadow-Modus ausrollbar.

## Source Material

- Integration plan: `integration-plan.md`
- Offloading evaluation: `docs/system/toolstack/deterministic_offloading_evaluation_2026-07-31.md`

## Resolution

- `structured_validate` parst JSON, Safe-YAML, defused XML und CSV innerhalb
  fester Payload-, Schema-, Tiefe-, Knoten-, Zeilen-, Spalten- und Feldlimits.
  JSON Schema Draft 2020-12 wird netzwerkfrei ohne `$ref` validiert; YAML-
  Alias/Anchor/Tags sowie XML-DTD, Entities und XInclude werden abgewiesen.
  CSV benötigt einen expliziten Dialekt und meldet Formelpräfixe nur als
  Datenrisiko.
- Das Ergebnis enthält ausschließlich Format, Validität, begrenzte
  Diagnostik, Warnungen und Payload-/Schema-Hashes. Ein im ersten Proof
  gefundener Leak über `input_normalized`, Statusstream und Tool-Log wurde
  durch eine vertragliche SHA-256-Redaktionspolicy geschlossen; Live-Evidence
  enthielt den Test-Payload danach nicht mehr.
- Valides JSON passierte alle drei API-Fassaden bitgleich und ohne
  Modell-Tokens. XXE, YAML-Aliase, Remote-Schema-Refs, tiefe Payloads und
  CSV-Grenz-/Formelfälle sind im fokussierten Korpus abgedeckt.
