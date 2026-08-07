# S-2.1.1 Verifiable Precision Execution

Level: Story
Status: Open

Parent Epic: E-2.1 Deterministic Pipeline Signals (`../epic.md`)

## Behavior Outcome

Als MoE-API kann ich eine eindeutig erkannte Präzisionsanfrage nur dann als
erfolgreich ausgeben, wenn ein aktiver, versionierter MCP-Vertrag mit den aus
dem Nutzertext extrahierten Argumenten ausgeführt wurde, sein typisiertes
Ergebnis validiert ist und die finale Antwort genau an diese Evidenz gebunden
bleibt.

## Value / Reason

Der vorhandene Precision-Intent-Guard schützt den Planner-Handoff, aber noch
nicht den vollständigen Lebenszyklus von Cache-Lookup bis Antwortausgabe und
Learning-Persistenz. Diese Story schließt die verbleibenden Umgehungs- und
Mutationspfade, bevor weitere deterministische Tools aktiviert werden.

## Current Seam

- L0-/L1-Antwortcaches liegen vor dem Planner und können den Guard umgehen.
- Der MCP-Worker prüft nur Pflichtfelder und generische Fehlerstrings; ein
  Judge darf nach einem Toolfehler Argumente verändern.
- MCP-Ergebnisse werden als gekürzter Freitext an Merger und Critic gegeben.
  Ein korrektes Toolergebnis ist dadurch nicht an die finale Aussage gebunden.
- Ergebnis-, Cache- und Learning-Persistenz erfolgt teilweise vor dem finalen
  Quality Gate.
- Der Planner-Cache-Fingerprint bildet nicht den vollständigen Toolvertrag ab.

## Preconditions

- TASK-41 bleibt mit seinem engen, adversarial getesteten Intent-Guard grün.
- Der aktive MCP-Katalog ist vollständig discoverbar und enthält nur
  aktivierte Tools.
- Vor jedem Runtime-Eingriff werden Branch, Dirty Worktree, aktive Leases,
  laufende Images, Requests und Readiness erneut lokal geprüft.
- Änderungen an `graph/synthesis.py` und `main.py` werden wegen gemeinsamer
  Graph-Topologie sequenziell und nicht parallel umgesetzt.

## Acceptance Criteria

- Kein verpflichtender Precision-Intent kann über Cache, Planner, Retry,
  Merger, Critic oder API-Ausgabe ohne passende validierte Evidenz erfolgreich
  werden.
- Reine, vollständig deterministische Anfragen benötigen keinen Modellaufruf;
  gemischte Antworten können evidenzgebundene Fakten nicht verändern.
- Nur final qualitätsfreigegebene Antworten werden in wiederverwendbare
  Antwortcaches, semantisches Gedächtnis oder Learning-Pfade geschrieben.
- Eingabe- und Ausgabeschemas, Vertragsversion, Kataloghash, normalisierte
  Argumente und Ergebnis-Hash sind im Audit nachvollziehbar.
- Neue P0-Tools für Zeit/Zeitzonen, Decimal-Finanzmathematik, exakte
  Wahrscheinlichkeit und strukturierte Validierung durchlaufen denselben
  Vertrag, Shadow-Modus und Enforce-Gate.
- Die Live-Abnahme beweist die Invarianten für native Chat-, Template- und
  Responses-kompatible API-Pfade bei warmem und kaltem Start.

## Proof Boundary

Die Story gilt erst als abgeschlossen, wenn Unit-/Property-/Contract-Tests,
ein In-Container-E2E mit absichtlich falschem Cache, Schema-/Katalogwechsel
und Merger-/Critic-Manipulation sowie ein Live-MoE-API-Benchmark auf der
recreateten Instanz grün sind.

## Non-Goals

- Keine Behauptung, dass mutable Außenweltfakten durch MCP automatisch
  deterministisch werden.
- Keine autonome Modellgewichtsänderung oder LUMI-G-Distillation.
- Keine breiten Regex-Intentklassen ohne typisierte, adversarial getestete
  Extraktoren.
- Keine Rechts-, Steuer- oder Finanzberatung aus unversionierten Regeln.

## Dependencies

- TASK-41 Fail-closed Precision-Intent-Guard.
- Aktiver MCP-Registry-/Invoke-Pfad und Quality Gate.
- TASK-43 ist Voraussetzung für TASK-44 sowie TASK-46 bis TASK-49.
- TASK-44 und TASK-45 werden wegen überlappender Graph-Dateien sequenziell
  umgesetzt; TASK-50 folgt nach allen Funktionspaketen.

## Refinement Check

- Authority model check: Working code/config bleibt Wahrheitsquelle; die Story
  erweitert E-2.1 und ändert keine Initiative- oder Epic-Semantik.
- Related backlog check: TASK-38/41, E-2.2 sowie Cache-/Quality-Gate-Pfade
  wurden auf Überschneidungen geprüft; TASK-9 ist fachlich getrennt.
- Code-contract check: Cache, Planner, MCP-Worker, Synthesis, Critic, Quality
  Gate, API-Ausgabe und Persistenz wurden lokal bis zum Endpunkt verfolgt.
- Refinement result: Ausführbar als TASK-42 bis TASK-50; Reihenfolge und
  Rollback-Gates sind im Integrationsplan festgelegt.

## Implementation Tasks

- TASK-42 Precision Preflight und Cache-Containment: `TASK-42-precision-preflight-cache-containment.md`
- TASK-43 Versionierte MCP-Verträge und typisierte Evidenz: `TASK-43-versioned-mcp-contracts.md`
- TASK-44 Evidenzgebundene Synthese und Direktantwort: `TASK-44-evidence-bound-synthesis.md`
- TASK-45 Quality-atomare Persistenz: `TASK-45-quality-atomic-persistence.md`
- TASK-46 Deterministische Zeit- und Zeitzonenverträge: `TASK-46-time-timezone-contracts.md`
- TASK-47 Decimal-Finanzmathematik: `TASK-47-decimal-finance-contracts.md`
- TASK-48 Exakte Wahrscheinlichkeitsrechnung: `TASK-48-exact-probability-contracts.md`
- TASK-49 Strukturierte Validierung: `TASK-49-structured-validation-contracts.md`
- TASK-50 Telemetrie, Benchmark und stufenweiser Enforce-Rollout: `TASK-50-precision-rollout-proof.md`

## Source Material

- Deterministic offloading evaluation: `docs/system/toolstack/deterministic_offloading_evaluation_2026-07-31.md`
- MCP tool documentation: `docs/system/toolstack/mcp_tools.md`
- Pipeline architecture: `docs/system/pipeline.md`
- Detailed integration plan: `integration-plan.md`
- Implementation record: `AGENT_LASTENHEFT.md`
