# I-2 Pipeline Quality Gate Stack

Level: Initiative
Status: Open

Parent: Current Backlog Decomposition (`../../current/current.md`)

## Strategic Outcome

Deterministisch messbare Antwortqualität, vollständige Compliance-Auditierbarkeit
und strukturelle Resilienz in der Planner→Experts→Judge-Pipeline — ohne den
Fokus auf lokale, souveräne Inferenz zu verschieben.

## Value / Reason

Die vier in feat/sovereign-typed-cascades-dor-constitution eingeführten
Mechanismen (Cascade Types, DoR, Constitution, Retry Budget) bilden die
Basis-Schicht. Diese Initiative schließt die verbleibenden Qualitätslücken:

- Der Judge hat kein quantitatives Qualitätsverdikt (kein Trust-Score).
- Grenzwerte zwischen Pipeline-Stufen sind undeklariert (kein Boundary Contract).
- Laufzeit-Entscheidungen sind nicht begründet dokumentiert (kein Decision Log).
- Expert-Crashes führen zu vollständigem Neustart (kein Checkpointing).
- Kontext geht über Session-Grenzen verloren (kein Handover).
- Multi-Tenant-Betrieb ist nicht datenisoliert.

## Risk Reduced

- Antworten mit 0 validierten Quellen werden als valide durchgereicht.
- Judge-Übersteuerungen sind im Audit-Trail nicht begründet.
- Stiller garbage-in/garbage-out an Phasengrenzen bleibt unentdeckt.
- Crash eines Expert-Nodes führt zu vollständigem Neustart laufender Traversals.
- Cross-Tenant-Datenlecks bei Multi-Kunden-Betrieb.

## Users / Stakeholders

- Nutzer der OpenAI-kompatiblen API (indirekt: verbesserte Antwortqualität)
- Betreiber (Observability, Compliance-Audit)
- Downstream: Judge-Node, Expert-Nodes, Constitution-Enforcement

## Current Seam

Judge entscheidet ohne messbare Schwellen. Planner→Expert-Übergabe ist ein
frei-strukturiertes Dict ohne deklarierte Pflichtfelder. Kafka-Events zeigen
WHAT, nie WHY. Expert-Crash = Vollneustart.

## Scope

- Quantitativer Trust-Score nach jedem Expert-Durchlauf
- Self-Critique Iteration Loop (max. 2 Runden bei borderline Score)
- Human-in-the-Loop Gate für kritische Antworten
- Boundary Contracts an Planner→Expert und Expert→Judge
- Decision Log mit Rationale-Pflicht
- Deterministischer Scope Guard vor Expert-Dispatch
- Cascade Event Resolution Tracking (open/resolved)
- Handover / Context-Preservation über Session-Grenzen
- Resumable Task Checkpointing (Mid-Expert Crash Recovery)
- Artefakt-Registry mit SHA-256 Provenance
- Cynefin-basierte Complexity Classification mit adaptivem Autonomie-Level
- Multi-Tenant Data Isolation (Fail-Closed)

## Out Of Scope

- Änderungen am Modell-Routing oder Expert-Auswahl-Logik
- Neue Expert-Typen oder Domain-Erweiterungen
- Infrastruktur-Änderungen (K3s, Longhorn, Netzwerk)
- SFT-Training oder ONNX-Export (→ I-1)

## Success Measures

- Trust-Score wird nach jedem Expert-Durchlauf berechnet und in AgentState gesetzt
- `block`-Verdict stoppt den Loop deterministisch ohne LLM-Fallback
- `decision_log`-Tabelle enthält Rationale für jede Judge-Übersteuerung
- Boundary-Contract-Violation produziert SPEC_GAP-Cascade, kein Expert-Aufruf
- `pytest tests/ -q` bleibt grün nach jedem Epic

## Dependencies / Preconditions

- feat/sovereign-typed-cascades-dor-constitution gemergt (✓ bereits auf main)
- PostgreSQL-Verbindung via `langgraph-checkpoint-postgres` (bereits vorhanden)
- Valkey/Redis-Verbindung (bereits vorhanden)
- Kafka `moe.audit` Topic (bereits vorhanden)

## Roadmap / Priority Notes

Empfohlene Reihenfolge nach Aufwand/Nutzen:

1. **E-2.1** (gering, sofort): Boundary Contracts + Decision Log + Scope Guard + Cascade Resolution
2. **E-2.2** (mittel): Trust-Score + Self-Critique + Human-in-the-Loop Gate
3. **E-2.3** (mittel): Handover + Task-Checkpointing + Artefakt-Registry
4. **E-2.4** (mittel): Cynefin Complexity Classification
5. **E-2.5** (hoch): Multi-Tenant Data Isolation

## Refinement Check

- Authority model check: lokal, kein externer Egress, kein Modell-Swap
- Related backlog check: I-1 (SFT) ist unabhängig — keine Blockierung
- Code-contract check: bestehende AgentState TypedDict-Erweiterung als Pattern
- Refinement result: bereit für Epic-Decomposition

## Epics

- E-2.1 Deterministische Pipeline-Signale: `I-2-pipeline-quality-gate/E-2.1-deterministic-signals/epic.md`
- E-2.2 Trust-Score & Selbstkorrektur-Loop: `I-2-pipeline-quality-gate/E-2.2-trust-score-self-critique/epic.md`
- E-2.3 Kontext-Kontinuität & Crash-Resilienz: `I-2-pipeline-quality-gate/E-2.3-context-resilience/epic.md`
- E-2.4 Adaptive Komplexitätssteuerung: `I-2-pipeline-quality-gate/E-2.4-complexity-classification/epic.md`
- E-2.5 Multi-Tenant Data Isolation: `I-2-pipeline-quality-gate/E-2.5-multi-tenant/epic.md`

## Source Material

- adSCAILE Wiki (lokal): `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/`
- Relevante Wiki-Seiten: `re-loop-trust-substrate.md`, `boundary-contracts.md`, `decision-log.md`,
  `human-in-the-loop.md`, `haertung-loop-v6.md`, `cascade-events.md`, `handover.md`,
  `resilience-pack.md`, `knowledge-base.md`, `re-loop-interaktionsmodi.md`, `mandantenfaehigkeit.md`
