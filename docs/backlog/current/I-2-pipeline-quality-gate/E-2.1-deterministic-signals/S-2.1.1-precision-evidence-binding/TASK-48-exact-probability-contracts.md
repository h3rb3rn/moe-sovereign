# TASK-48 Exakte Wahrscheinlichkeitsrechnung

Level: Implementation Task
Status: Completed (2026-08-02)

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

Begrenzte diskrete Wahrscheinlichkeits- und Kombinatorikaufgaben als exakte
rationale Fakten und explizite Decimal-Projektion berechnen.

## Scope

- MCP-Vertrag `exact_probability` mit `Fraction`-/Integer-Kern
- Begrenzte Operationen für rationale Ereignisse, Kombinationen,
  Permutationen und Binomialwahrscheinlichkeit
- Explizite Decimal-Scale/Rundung nur als Darstellung des exakten Ergebnisses
- Intent-Extractor, Renderer, Unit-/Property-/E2E-Tests

## Out Of Scope

- Freiform-Modellierung unklarer Zufallsexperimente
- Monte-Carlo-Simulation als Ersatz für exakte unterstützte Operationen
- Kontinuierliche Verteilungen oder statistische Inferenz in Phase P0

## Code / Document Anchors

- MCP implementation/registry: `mcp_server/server.py`
- Precision contracts: `services/pipeline/contracts.py`
- Typed result/binding from TASK-43/44

## Implementation Notes

- Ergebnis enthält gekürzten Bruch, optionalen Decimalwert, Operation und
  Parameter; Decimaldarstellung verändert den rationalen Fakt nicht.
- `n`, `k`, Nenner, Bitlänge und geschätzte Rechenkosten vor Ausführung
  begrenzen; keine ungebundene Fakultäts-/Exponentiation.
- Ungültige Wahrscheinlichkeiten außerhalb `[0,1]`, negative Counts und
  widersprüchliche Parameter typisiert ablehnen.
- Nur eindeutig extrahierbare Formulierungen im Enforce-Korpus aufnehmen.

## Acceptance

- Exakte Resultate stimmen in Property-Tests mit unabhängigen rationalen
  Identitäten überein und bleiben über Wiederholungen bitgleich.
- Grenz-/Kostenlimits greifen vor teurer Berechnung.
- Ein unklar modellierter Sachtext bleibt außerhalb der Enforce-Allowlist.
- Finale Bruch- und Decimaldarstellung ist evidence-bound.

## Proof

- Syntax: `python3 -m compileall -q mcp_server services`
- Unit/property: 0/1, Kürzung, Binomialidentitäten, `n<k`, große Grenzwerte,
  ungültige Nenner und Kostenlimit
- Contract: `/tools` und `/invoke` positive/negative Matrix
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `sudo docker compose build mcp-precision langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate mcp-precision langgraph-orchestrator`
- E2E: Reine und gemischte Probability-Prompts über Live-MoE-API.

## Failure / Stop Conditions

- Stop bei ungebundener Laufzeit/Speichernutzung oder Float als Wahrheitswert.
- Stop, wenn mehrdeutiger Text automatisch in ein Zufallsmodell übersetzt wird.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Keine generischen Statistikpfade ersetzen, bevor deren Semantik bewiesen ist.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Exakter rationaler Kern ist die Ergebnisautorität.
- Related backlog check: Baut auf TASK-43/44/45 auf.
- Code-contract check: Kostenlimit vor jeder kombinatorischen Operation.
- Refinement result: Eigenständig im Shadow-Modus ausrollbar.

## Source Material

- Integration plan: `integration-plan.md`
- Offloading evaluation: `docs/system/toolstack/deterministic_offloading_evaluation_2026-07-31.md`

## Resolution

- `exact_probability` implementiert Bruchkürzung, Kombination, Permutation
  und Binomialwahrscheinlichkeit mit `Fraction`/Integer als Autorität.
  Dezimalwerte entstehen nur bei gemeinsam explizit angegebener Scale und
  Rundung.
- `n<=4096`, Ergebnis-Bitlänge und eine konservative Binomial-Kostenschätzung
  werden vor teurer Berechnung geprüft. Ungültige Nenner, `k>n`, Werte
  außerhalb `[0,1]`, negative Counts und einseitige Decimal-Policy scheitern
  fail closed.
- Der Cross-Facade-Proof lieferte bitgleich `15/128` und `0.117188` mit null
  Modell-Tokens. Derselbe Bruch blieb im gemischten Expert-/Critic-Pfad
  unverändert gebunden.
