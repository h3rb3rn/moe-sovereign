# TASK-45 Quality-atomare Persistenz

Level: Implementation Task
Status: Done 2026-08-01 (Codex CLI)

Parent Story: S-2.1.1 Verifiable Precision Execution (`story.md`)

## Objective

Wiederverwendbare Antwort-, Wissens-, Episode- und Learning-Artefakte erst
nach erfolgreichem finalen Quality Gate idempotent persistieren.

## Scope

- `graph/synthesis.py`, `main.py`, Cache-/Memory-/Kafka-/Episode-/Learning-
  Services und passende Tests
- Neuer `response_commit`-Knoten nach Quality Pass
- Idempotenter Commit-Key aus Request-, Response-, Contract- und Evidence-Hash
- HITL-Approve/Reject/Resume-Semantik für den Commit

## Out Of Scope

- Verzögern betrieblicher Audit-, Fehler- und Task-Ledger-Ereignisse
- Änderung fachlicher Cache-TTLs ohne Messdaten
- Neue deterministische Tools

## Code / Document Anchors

- Current pre-gate writes: `graph/synthesis.py`
- Graph terminal routing: `main.py`
- Quality gate: `services/quality_gate.py`
- HITL route/state: `routes/gates.py`, `services/hitl_gate.py`

## Implementation Notes

- Alle semantisch wiederverwendbaren Writes aus Merger-/Pre-Gate-Pfaden in
  einen expliziten Commit-Knoten verschieben.
- Graph-Routing: `quality pass -> response_commit -> END`; `blocked|pending ->
  END` ohne semantischen Commit. HITL-Approve committed exakt einmal den
  freigegebenen gebundenen Response-Hash.
- Operational Audit/Task-Ereignisse bleiben sofort sichtbar und werden nicht
  als semantischer Cache missverstanden.
- Partial failure im Commit einzeln erfassen. Keine halbfertige Transaktion
  als vollständig markieren; sichere Teilwrites müssen idempotent retrybar
  sein.
- Typisierte Cache-Keys enthalten Contract-/Katalogversion und normalisierten
  Input; ungebundene/degradierte Antworten sind nicht cachebar.

## Acceptance

- Quality-blocked, HITL-pending/rejected, abgebrochene und ungebundene
  Antworten erzeugen keine wiederverwendbaren semantischen Writes.
- Approve/Resume/Retry erzeugt höchstens einen logischen Commit.
- Audit und Task-Ledger bleiben auch bei Block/Commit-Fehler vollständig.
- Erfolgreiche Nicht-Precision-Antworten behalten ihre beabsichtigte
  Persistenz nach dem Gate.

## Proof

- Syntax: `python3 -m compileall -q graph services routes main.py`
- Unit: `python3 -m pytest tests/test_quality_gate.py tests/test_hitl_gate.py tests/test_cache_bypass.py tests/test_cascade_lifecycle.py -q`
- Failure injection: jeden Commit-Sink einzeln fehlschlagen und Retry/
  Idempotenz prüfen
- Regression: `python3 -m pytest tests/ -q`
- Rebuild: `docker compose build langgraph-app && docker compose up -d --no-deps --force-recreate langgraph-app`
- E2E: Pass, Block, Pending, Reject, Approve und doppelten Resume gegen echte
  Cache-/Memory-Sinks prüfen.

## Failure / Stop Conditions

- Stop, wenn ein Blockpfad semantischen Zustand schreibt oder Audit verliert.
- Stop, wenn HITL-Resume denselben Response mehrfach committed.
- Stop if `pytest tests/` introduces a new failure.
- Stop if rebuild fails — do not leave service in broken state.

## Cleanup

- Erfolgreich migrierte Pre-Gate-Write-Zweige löschen und veraltete Cache-
  Kommentare/Dokumentation korrigieren.
- Update `AGENT_LASTENHEFT.md` Status + Resolution notes.
- Update `docs/ai-memory/07-current-status-and-next-work.md`.

## Refinement Check

- Authority model check: Quality Gate wird alleiniger Commit-Entscheider.
- Related backlog check: TASK-44 vorher abschließen; HITL/E-2.2 mitprüfen.
- Code-contract check: Alle Sink-Callsites per Quellcodesuche inventarisieren.
- Refinement result: Separater Rollback-fähiger Graph-Umbau.

## Source Material

- Integration plan: `integration-plan.md`
- Quality gate documentation: `docs/system/pipeline.md`

## Resolution

- `quality_gate -> response_commit -> END` ist der einzige Graphpfad für
  wiederverwendbare Antwort-, Knowledge-, Episode- und Learning-Writes.
  `blocked` und `pending` enden vor dem Commit; Reject schreibt nicht, und
  HITL-Approve verwendet den beim Gate eingefrorenen Response-/Evidence-
  Vertrag.
- Der Commit-Key bindet Request-, Response-, Contract-, Binding- und
  Evidence-Hash. Ein Valkey-Journal protokolliert jeden Sink einzeln; Resume
  überspringt erfolgreiche Sinks und wiederholt ausschließlich fehlgeschlagene
  Sinks. Ungebundene oder nachträglich veränderte Precision-Antworten werden
  vor dem ersten Sink blockiert.
- Chroma, L0, Response-Metadaten, Routing-Telemetrie, Episode, Kafka-Ingest,
  Self-Correction, Retrieval-Attribution, Routing-/Policy-Learning und
  Self-Evaluation werden erst nach dem Pass ausgelöst. Operative Request-
  Audit- und Task-Ledger-Ereignisse bleiben während der Ausführung sichtbar.
- Precision-Response-Caching bleibt im Deployment auf `bypass`: typisierte
  Keys sind implementiert und vertragsspezifisch, der aktuelle Reader kann
  jedoch noch keinen vollständigen Evidence-Umschlag rekonstruieren und
  revalidieren. Eine vorzeitige `typed`-Aktivierung wäre daher nicht
  end-to-end wirksam.
- Unit-/Failure-Injection decken Pass, Block, Pending, Reject, Approve,
  Hash-Manipulation, Partial Retry und doppelten Commit ab. Vollständige
  Regression: **797 passed in 4,25 s**; Compileall, Compose-Config und
  Diff-Check sind grün.
- Live-Proof `chatcmpl-5de9de32-2a34-428f-acda-ee2f0098b44a` lieferte fünf
  gebundene Fakten in 2,543 s mit null Modell-Tokens. Der Trace zeigt
  `precision_bind=bound`, `quality_gate=passed` und
  `response_commit=complete`; alle zehn realen Sinks wurden `done` journaled.
  Image `sha256:4abd1ef59f145cf0641c3624dcdf1b948f57f176870b9413e250e4c5f3b94262`
  läuft healthy mit RestartCount 0. Der temporäre `horndev`-Key wurde im
  `finally`-Pfad widerrufen, invalidiert und archiviert.
