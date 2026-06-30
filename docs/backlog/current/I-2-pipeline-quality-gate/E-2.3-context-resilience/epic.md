# E-2.3 Kontext-Kontinuität & Crash-Resilienz

Level: Epic
Status: Open

Parent Initiative: I-2 Pipeline Quality Gate Stack (`../initiative.md`)

## Epic Goal

Expert-Crashes führen zu Resume am letzten Checkpoint statt Vollneustart.
Multi-Turn-Konversationen verlieren keinen validierten Kontext über
Session-Grenzen. Jeder Expert-Output ist mit SHA-256-Provenance rückverfolgbar.

## Capability / Proof Package

1. **Handover / Context-Preservation** — PostgreSQL-Tabelle `handovers` mit
   scope_type (`session`|`task`), scope_id (`conversation_id`), TTL 24h.
   Felder: `completed_work`, `remaining_work`, `decisions`, `next_action`.
   `PreCompact`-Hook schreibt Snapshot, `PostCompact`-Hook injiziert in
   nächsten Turn. Verhindert Cold-Start nach LangGraph-Compaction.

2. **Resumable Task Checkpointing** — Append-only Checkpoint-Log pro
   Expert-Task in Valkey (Key: `moe:checkpoint:{task_id}`, TTL 2h).
   Expert persistiert Zwischenstand (z.B. bereits traversierte Neo4j-Node-IDs).
   Nach Crash: `claim_next` liefert `last_checkpoint`, Expert setzt dort fort.
   Idempotency-Key auf `task.complete` verhindert doppelte Kafka-Events.

3. **Artefakt-Registry mit SHA-256 Provenance** — PostgreSQL-Tabelle
   `artifacts` mit Pointer + SHA-256 + `produced_by_task_id` + `phase_id` +
   `supersedes`-Kette. Inhalt bleibt in ChromaDB/Neo4j; Registry hält nur
   Pointer. `artifact.latest(phase_id)` deterministisch beantwortbar.
   Jede Artefakt-Mutation produziert Audit-Eintrag.

## Scope

- `services/handover.py` (neu)
- PostgreSQL-Tabelle `handovers`
- `services/task_checkpoint.py` (neu)
- Valkey-Keys `moe:checkpoint:{task_id}`
- `services/artifact_registry.py` (neu)
- PostgreSQL-Tabelle `artifacts`
- Erweiterung Expert-Nodes um Checkpoint-Calls
- Erweiterung `pipeline/state.py` um `handover_id`, `artifact_ids`

## Out Of Scope

- Trust-Score oder Gate-Mechanismus (→ E-2.2)
- Multi-Tenant-Trennung der Artefakte (→ E-2.5)

## Dependencies / Preconditions

- E-2.1 abgeschlossen (Decision Log als Artefakt-Mutations-Audit-Ergänzung)
- PostgreSQL via langgraph-checkpoint-postgres verfügbar (✓)
- Valkey/Redis-Verbindung vorhanden (✓)

## Success Semantics

- Expert-Crash → Resume nach &lt;2s am letzten Checkpoint ohne Neustart der GraphRAG-Traversal
- `handovers`-Tabelle enthält Snapshot nach LangGraph-Compaction
- `artifacts`-Tabelle enthält SHA-256-Eintrag für jeden Expert-Output
- `artifact.latest(phase_id)` liefert deterministisch aktuellsten Stand

## Failure Semantics

- Checkpoint-Valkey-Ausfall → fail-open, Expert startet von 0 (kein Block)
- Handover-PostgreSQL-Ausfall → fail-open, nächster Turn ohne Kontext
- Artefakt-Registry-Ausfall → fail-open, Expert-Output nicht registriert aber geliefert

## Quality Constraints

- Alle drei Mechanismen fail-open
- Checkpoint-Payload capped bei 256KB
- Handover-TTL: 24h (konfigurierbar)
- Checkpoint-TTL: 2h (konfigurierbar)

## Exit Criteria

- `pytest tests/ -q` grün
- Simulierter Expert-Crash → nachweislicher Resume am Checkpoint
- Artefakt-Tabelle enthält SHA-256-Einträge nach Expert-Completion

## Source Material

- Wiki `handover.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/handover.md`
- Wiki `resilience-pack.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/resilience-pack.md`
- Wiki `knowledge-base.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/knowledge-base.md`
