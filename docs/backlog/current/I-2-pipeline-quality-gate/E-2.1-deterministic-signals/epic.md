# E-2.1 Deterministische Pipeline-Signale

Level: Epic
Status: Open

Parent Initiative: I-2 Pipeline Quality Gate Stack (`../initiative.md`)

## Epic Goal

Vier deterministische, LLM-freie Checks mit &lt;10ms Overhead an den
kritischen Übergangspunkten der Pipeline — alle fail-open, alle strukturiert
auditierbar.

## Initiative Alignment

Schließt die unmittelbarsten Qualitätslücken mit geringstem Aufwand. Bildet
die Pflicht-Vorstufe für E-2.2 (Trust-Score braucht strukturierte
Boundary-Violations als Input).

## Users / Consumers

- Judge-Node: bekommt vollständige Expert-Outputs statt stiller Partial-Outputs
- Constitution-Enforcement: Decision-Log schreibt jedes Block-Event mit Rationale
- Operator-Dashboard: offene vs. aufgelöste Cascades unterscheidbar

## Capability / Proof Package

1. **Boundary Contracts** — YAML-Deklaration der Pflichtfelder pro Stage-Grenze.
   Deterministischer Check vor Expert-Dispatch und vor Judge-Übergabe.
   Verletzung → `SPEC_GAP`-Cascade, kein teurer Expert-Aufruf.

2. **Decision Log** — Append-only PostgreSQL-Tabelle `decision_log` mit
   Pflichtfeld `rationale`. Schreibt jede Judge-Übersteuerung, jeden
   Constitution-Block+Re-Route, jeden DoR-Fail mit Begründung.

3. **Deterministischer Scope Guard** — Pattern-Match auf `domain_hints` im
   Planner-Task-Payload. Expert-Zugriff auf nicht deklarierte Domain →
   sofortiger `SPEC_GAP`-Cascade, &lt;10ms, kein LLM-Urteil.

4. **Cascade Event Resolution Tracking** — `resolve(event_id, resolved_by)`
   in `services/cascade.py`. PostgreSQL-Spalten `resolved_at`,
   `resolved_by_event_id`. `list(only_open=True)` als neue Abfrage.

## Scope

- `services/boundary_contracts.py` (neu)
- `configs/boundary-contracts.yaml` (neu, Pflichtfelder pro Stage)
- `services/decision_log.py` (neu)
- PostgreSQL-Tabelle `decision_log`
- `services/scope_guard.py` (neu)
- Erweiterung `services/cascade.py` um `resolve()`
- PostgreSQL-Spalten `resolved_at`, `resolved_by_event_id` in Cascade-Tabelle
- Integration als LangGraph-PreNodes / PostNodes

## Out Of Scope

- Trust-Score-Berechnung (→ E-2.2)
- Human-in-the-Loop Gate (→ E-2.2)
- Kontext-Persistenz (→ E-2.3)

## Dependencies / Preconditions

- `services/cascade.py` bereits vorhanden (✓)
- PostgreSQL via `langgraph-checkpoint-postgres` verfügbar (✓)
- `configs/sovereign-constitution.yaml` bereits vorhanden (✓)

## Current Status

Nicht begonnen. Boundary Contracts und Decision Log sind unabhängig
startbar. Scope Guard benötigt finale Planner-Payload-Struktur als Referenz.

## Known Facts / Evidence

- `graph/planner.py`: Task-Payload ist frei-strukturiertes Dict ohne Schema
- `graph/synthesis.py`: Judge trifft Entscheidungen ohne Log-Eintrag
- `services/cascade.py`: kein `resolve()`-Aufruf, kein Resolution-State

## Success Semantics

- Boundary-Verletzung zwischen Planner→Expert produziert SPEC_GAP-Event in Kafka `moe.audit`
- `decision_log`-Tabelle enthält Eintrag mit `rationale` bei jeder Judge-Übersteuerung
- Expert-Dispatch auf nicht deklarierter Domain produziert SPEC_GAP, kein Expert-Aufruf
- `cascade.list(only_open=True)` liefert nur noch nicht-aufgelöste Events

## Failure Semantics

- Expert wird trotz fehlendem Pflichtfeld im Planner-Output dispatched
- Judge-Übersteuerung ohne `decision_log`-Eintrag
- Cascade-Event verbleibt dauerhaft im `open`-Status ohne Auflösungsmöglichkeit

## Downstream Contract

Schafft strukturierte Boundary-Violation-Events als Input für Trust-Score (E-2.2).
Decision Log bildet Audit-Grundlage für E-2.5 (Multi-Tenant Compliance).

## Quality Constraints

- Alle Checks deterministisch, kein LLM-Call
- Alle Checks fail-open: Exception in Check → Log + Weiter, kein Pipeline-Block
- Boundary-Contract-Check: &lt;10ms
- Scope-Guard-Check: &lt;10ms

## Risks / Assumptions

- Planner-Payload-Struktur muss stabil genug sein um Pflichtfelder zu deklarieren
- PostgreSQL-Schema-Migration für `decision_log` und Cascade-Resolution-Spalten

## Refinement Check

- Authority model check: rein lokal, kein Egress, deterministische Checks
- Related backlog check: keine Überschneidung mit I-1 (SFT)
- Code-contract check: AgentState-Pattern aus cascade.py als Vorlage
- Refinement result: bereit zur Story-Decomposition

## Exit Criteria

- `pytest tests/ -q` grün nach Implementierung
- Boundary-Contract-Violation produziert nachweislich SPEC_GAP in Kafka
- `decision_log`-Tabelle existiert und enthält Test-Einträge

## Story Breakdown

*(bei Sprint-Start zu füllen)*

## Source Material

- Wiki `boundary-contracts.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/boundary-contracts.md`
- Wiki `decision-log.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/decision-log.md`
- Wiki `haertung-loop-v6.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/haertung-loop-v6.md`
- Wiki `cascade-events.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/cascade-events.md`
