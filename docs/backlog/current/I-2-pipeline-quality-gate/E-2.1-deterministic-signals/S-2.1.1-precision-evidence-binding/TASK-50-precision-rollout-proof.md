# TASK-50 Telemetrie, Benchmark und stufenweiser Enforce-Rollout

Level: Implementation Task
Status: Completed (2026-08-02)

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

Die Precision-Plattform mit niedrig-kardinaler Telemetrie, festem
adversarialen Benchmark und überprüfbarem Shadow→Enforce-Rollout über alle
API-Pfade produktionsnah abnehmen.

## Scope

- Precision-Metriken, Auditkorrelation und zentrale Rollout-Konfiguration
- Versionierter Positiv-/Negativ-/Störfallkorpus für TASK-42 bis TASK-49
- Cold-/Warm-Benchmark, Native-vs-Orchestrated-Vergleich und API-E2E
- Docker-Build/Recreate, Readiness, Image-/Hash-/Restart-Proof und Rollbacktest
- Dokumentations-, Lastenheft-, Status- und SessionMesh-Abschluss

## Out Of Scope

- Marketingclaim ohne veröffentlichte Methodik und Ergebnisartefakt
- Aktivierung weiterer P1-Verträge
- Commit, Push, PR, Publish oder externe Produktion ohne Autorisierung

## Code / Document Anchors

- Metrics/observability: `main.py`, `services/tracking.py`, bestehende metric modules
- API facades: `services/pipeline/chat.py`, `services/pipeline/responses.py`, template routing
- Benchmark source/results: neues versioniertes Test-/Resultartefakt unter `tests/` und `docs/system/toolstack/`
- Integration criteria: `integration-plan.md`

## Implementation Notes

- Metriken mindestens für Intent erkannt, Route, Cache-Bypass, Input-/Output-
  Schemafehler, Contract-Drift, Binding, LLM-Escape und Commit. Labels nur aus
  kleiner Contract-/Status-Allowlist; keine Prompts/Rohargumente.
- Neue Verträge zuerst Shadow: False Positives/Negatives und Argumenttreue
  messen. Enforce nur einzeln nach grünem Task-Proof und dokumentiertem Gate.
- Benchmark fixiert Promptkorpus, Contract-/Image-/Template-/Modellversion,
  API-Key-Rolle, Datum, Stichprobe, Cold-/Warm-Bedingung, Timeout und Grenzen.
- Native und orchestrierte Ausführung mit identischem fachlichem Prompt
  vergleichen; Tool-/Modellcalls, exakte Fakten, Fehler, Latenz und Cache-
  Zustand getrennt berichten.
- Rollback von Direct Response, Structured Required und Typed Cache sowie zum
  letzten verifizierten Image praktisch testen.

## Acceptance

- Alle messbaren Gesamtkriterien aus `integration-plan.md` sind mit
  reproduzierbaren Artefakten belegt.
- Null LLM-only-Escape und null Fact-Mutation im fest versionierten
  verpflichtenden Korpus; Negativkorpus blockiert keine nicht unterstützten
  Aufgaben durch erfundene Argumente.
- Chat-, Template- und Responses-Fassade bestehen Cold/Warm und Fehlerpfade.
- Container sind healthy, RestartCount 0, `/ready` grün; Rollback ist bewiesen.
- Benchmarkclaims bleiben auf Methodik, Version und beobachtete Ergebnisse
  begrenzt.

## Proof

- Syntax: `python3 -m compileall -q .`
- Unit/regression: `python3 -m pytest tests/ -q`
- Governance: `python3 scripts/check_governance.py`
- Docs: `python3 -m mkdocs build --strict`
- Dependencies/config: `docker compose config` und `pip check` in betroffenen Images
- Rebuild: `sudo docker compose build mcp-precision langgraph-orchestrator && sudo docker compose up -d --no-deps --force-recreate mcp-precision langgraph-orchestrator`
- E2E: versioniertes Benchmarkskript gegen native und orchestrierte API mit
  dreifachem Standardtimeout für Cold Starts; Resultat als datiertes Artefakt.

## Failure / Stop Conditions

- Stop bei LLM-only-Escape, Faktmutation, Preflight-Bypass, Pre-Gate-Commit,
  Secret-Leak oder hoch-kardinaler Metrik.
- Stop, wenn Benchmarkmethodik oder verglichene Versionen nicht identisch
  nachvollziehbar sind.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Nach stabilem Enforce obsolete Legacy-Zweige/Flags entfernen und P1-GAPs
  separat refinieren.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Benchmark ist Evidence, nicht Website-/Roadmap-Claim.
- Related backlog check: Abschlussgate für TASK-42 bis TASK-49 und TASK-38-API-Pfade.
- Code-contract check: Alle Fassaden und tatsächlichen Modell-/Toolaufrufe auditieren.
- Refinement result: Letztes Release; keine Enforce-Freigabe ohne Gesamtproof.

## Source Material

- Integration plan: `integration-plan.md`
- Benchmark API/task history: `AGENT_LASTENHEFT.md`
- System assessment: `docs/system/systembewertung_2026-07-30.md`

## Resolution

- `PRECISION_CONTRACT_MODE=shadow|enforce`,
  `PRECISION_DIRECT_RESPONSE_ENABLED`, `MCP_STRUCTURED_RESULT_REQUIRED` und
  `PRECISION_CACHE_POLICY=bypass|typed` bilden eine zentrale Rollout- und
  Rollback-Grenze. Der aktive Zustand ist `enforce/true/true/bypass`.
- `moe_precision_events_total` misst Intent, Route, Cache-Bypass, Schema,
  Drift, Tool, Binding, Quality/Escape und Commit ausschließlich mit
  allowlisteten Contract-/Stage-/Outcome-/Mode-Labels. Prompts, Argumente,
  Credentials und Hash-Rohmaterial sind keine Labels.
- Der versionierte Korpus `moe-precision-v1` und
  `scripts/benchmark_precision_rollout.py` prüfen reine Cross-Facade-, Mixed-
  und Native-vs-Orchestrated-Pfade mit 900 Sekunden Timeout und ephemerem
  horndev-Schlüssel. Der erste Mixed-Lauf deckte eine halbe AdviceTaker-
  Implementierung auf: `calculate` wurde ohne Pflichtargument injiziert. Die
  Rule Engine injiziert MCP-Aufgaben nun nur noch mit vollständig bewiesenen
  Argumenten; der Wiederholungslauf bestand 13/13 Fälle.
- Native Qwen (beobachteter Kaltstart, vorher `/api/ps=[]`) benötigte 151,918 s,
  warm 21,323 s und je 880 Tokens. Der evidence-bound Direct-Pfad benötigte
  0,174/0,163 s und null Modell-Tokens. Das ist ein n=1-Systemtest, kein
  allgemeiner Leistungsclaim. Der Mixed-Pfad benötigte 201,555 s und 15.499
  Tokens.
- Flag-Rollback (`shadow/false/false/bypass`) lief healthy/restart-0. Der
  praktische Image-Rollback auf
  `sha256:8c90f1e3654c525ad3f41fffb237acaa3d37322c2e866ee521e14239315d54c8`
  lief ebenfalls healthy/restart-0; anschließend wurde das finale Image
  `sha256:4320ca67eaaeaf5168d4c4c251427f99305e04bfbdbf1e60e3b4368f2e8d402f`
  erfolgreich wiederhergestellt.
- Abschließend: 908 Tests, Governance 27/9, MkDocs strict, Compose-Config,
  fokussierter Diff-Check und `pip check` in beiden Images grün. MCP-Image
  `sha256:7e28eeab4a5b05e56eb713cfbab834a6c9dc4ebfea9ae3594eb0f46c77c5564a`
  und Orchestrator laufen healthy mit RestartCount 0; alle temporären
  Benchmark-Schlüssel sind widerrufen und archiviert.
