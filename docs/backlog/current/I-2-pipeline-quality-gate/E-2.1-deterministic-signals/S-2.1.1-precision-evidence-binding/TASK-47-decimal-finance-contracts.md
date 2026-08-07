# TASK-47 Decimal-Finanzmathematik

Level: Implementation Task
Status: Completed (2026-08-02)

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

Reine Finanzarithmetik mit expliziter Decimal-Scale, Währung und Rundung ohne
Binary-Float oder unversionierte Rechts-/Steuerannahmen ausführen.

## Scope

- MCP-Vertrag `decimal_finance` mit begrenzter Operations-Allowlist
- Decimal-String-Eingaben, Währungscode, Scale und Rundungsmodus
- Typisierte Resultate für Summen, Differenz, Produkt/Quotient, Prozentsatz
  und klar definierte Zins-/Ratenoperationen
- Intent-Extractor, Renderer, Unit-/Property-/E2E-Tests

## Out Of Scope

- Steuer-, Gebühren-, Bilanzierungs- oder Rechtsregeln ohne versionierte Quelle
- Wechselkurse, Marktpreise oder andere mutable Fakten
- Implizite Währungs-/Locale-/Rundungsannahmen

## Code / Document Anchors

- MCP implementation/registry: `mcp_server/server.py`
- Precision contracts: `services/pipeline/contracts.py`
- Contract metadata/evidence from TASK-43

## Implementation Notes

- Zahlen ausschließlich als kanonische Dezimalstrings annehmen; Float-Input
  im Vertrag ablehnen.
- Rundung explizit aus erlaubter Decimal-Mode-Enum wählen. Scale und Ergebnis-
  Quantisierung getrennt dokumentieren.
- Division durch null, Overflow/Exponent-, Präzisions- und Iterationsgrenzen
  typisiert behandeln.
- Jurisdiktionsabhängige Regeln sind eigene source-versioned Verträge und
  dürfen nicht in dieses input-only Tool einsickern.
- Shadow-Korpus enthält DE/EN-Formate und absichtlich mehrdeutige Angaben.

## Acceptance

- Property-Tests stimmen mit Python `Decimal` unter explizitem Context überein.
- Kein unterstützter Pfad konstruiert zwischenzeitlich einen Binary-Float.
- Fehlende Währung, Scale oder Rundung wird bei semantisch notwendiger Angabe
  nicht geraten.
- Finale Ausgabe ist exakt an Decimal-Evidence gebunden.

## Proof

- Syntax: `python3 -m compileall -q mcp_server services`
- Unit/property: Decimal-Grenzwerte, Rundungsmodi, negative/große Werte,
  Division null und Locale-Negativfälle
- Contract: `/tools` und `/invoke` positive/negative Matrix
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `sudo docker compose build mcp-precision langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate mcp-precision langgraph-orchestrator`
- E2E: Reine und gemischte Finanzarithmetik über Live-MoE-API.

## Failure / Stop Conditions

- Stop bei implizitem Float, Rundungsmodus, Währung oder Jurisdiktion.
- Stop, wenn ein Finance-Intent ohne vollständige Parameter enforce wird.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Überlappende generische Arithmetic-Intents erst nach Proof migrieren.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Input-only-Mathematik strikt von mutablen Regeln getrennt.
- Related backlog check: Baut auf TASK-43/44/45 auf.
- Code-contract check: Decimal-Kontext wird pro Request lokal gesetzt.
- Refinement result: Eigenständig im Shadow-Modus ausrollbar.

## Source Material

- Integration plan: `integration-plan.md`
- Offloading evaluation: `docs/system/toolstack/deterministic_offloading_evaluation_2026-07-31.md`

## Resolution

- `decimal_finance` akzeptiert ausschließlich begrenzte kanonische
  Dezimalstrings sowie explizite ISO-Währung, Scale und einen von sieben
  Rundungsmodi. Addieren, Subtrahieren, Multiplizieren, Dividieren,
  Prozentsatz sowie klar definierter einfacher/zusammengesetzter Zins laufen
  in einem lokalen 128-stelligen `Decimal`-Kontext; Float-Inputs scheitern am
  Inputschema.
- Division durch null, ungültige Exponenten, nicht-ganzzahlige Zinsperioden,
  Magnitude-/Iterationsgrenzen und fehlende Policy-Felder liefern typisierte
  Fehler. Steuer-, Wechselkurs-, Gebühren- und Jurisdiktionsannahmen bleiben
  ausdrücklich außerhalb des Vertrags.
- DE/EN-Extractor und Renderer sind evidence-bound. Reine API-Requests waren
  über Chat, Responses und Anthropic identisch (`22.80 EUR`, `half_up`) und
  verbrauchten null Modell-Tokens; der Mixed-Proof band denselben Fakt nach
  dem Expert-/Critic-Pfad unverändert.
