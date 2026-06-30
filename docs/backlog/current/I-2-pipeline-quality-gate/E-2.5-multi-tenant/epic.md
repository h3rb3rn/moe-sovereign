# E-2.5 Multi-Tenant Data Isolation (Fail-Closed)

Level: Epic
Status: Open

Parent Initiative: I-2 Pipeline Quality Gate Stack (`../initiative.md`)

## Epic Goal

Vollständige Datenisolation zwischen Kunden auf ChromaDB-Collection- und
Neo4j-Label-Ebene. Fehlende `tenant_id` im Context → sofortige Exception,
nie silent Fallback auf Default-Namespace.

## Capability / Proof Package

- `tenant_id` wird in der API-Auth-Middleware aus JWT extrahiert und als
  Context-Variable durch alle Services propagiert.
- ChromaDB: Collection-Name = `{tenant_id}_{collection_name}`
- Neo4j: Node-Labels enthalten `tenant_id`-Präfix oder dediziertes Sub-Graph-Label
- Fehlende `tenant_id` → `RuntimeError` (Fail-Closed), kein globaler Namespace-Fallback
- Cross-Tenant-Check: Expert-Node prüft ob angefragte Collection/Label zur
  eigenen `tenant_id` gehört. Verstoß → sofortige Ablehnung + Audit-Log-Eintrag.
- Control Plane (Admin-API) getrennt von Data Plane (Inference-API)

## Scope

- Middleware-Erweiterung: `tenant_id`-Extraktion aus JWT
- `services/tenant.py` (neu) — Tenant-Context-Propagation, Fail-Closed-Guard
- ChromaDB-Client-Wrapper: Collection-Name-Scoping
- Neo4j-Query-Wrapper: Label-Scoping
- Expert-Nodes: Tenant-Guard vor jedem DB-Zugriff
- Audit-Log-Eintrag bei Cross-Tenant-Versuch

## Out Of Scope

- Separate PostgreSQL-Schemata pro Tenant (Scope: Collection/Label-Isolation reicht für PoC)
- Billing oder Quota-Enforcement

## Dependencies / Preconditions

- E-2.1 abgeschlossen (Decision Log als Audit-Basis für Cross-Tenant-Versuche)
- Bestehende JWT-Auth-Middleware vorhanden (✓ `services/auth.py`)

## Success Semantics

- Expert-Query auf fremde ChromaDB-Collection → RuntimeError + Audit-Eintrag
- Request ohne `tenant_id` im JWT → 401 an API-Grenze, kein Pipeline-Eintritt
- Zwei Tenants schreiben parallel → keine Collection-Überschneidung nachweisbar

## Failure Semantics

- Fail-Closed: jede fehlende `tenant_id` bricht die Anfrage mit 4xx ab

## Quality Constraints

- Fail-Closed ist nicht verhandelbar — kein Silent-Fallback
- Tenant-ID-Propagation durch alle Layers ohne Ausnahme

## Risks / Assumptions

- Neo4j-Label-Strategie muss mit bestehendem GraphRAG-Schema kompatibel sein
- Bestehende Single-Tenant-Daten müssen migriert werden (Default-Tenant-ID)

## Exit Criteria

- `pytest tests/ -q` grün mit Multi-Tenant-Fixtures
- Cross-Tenant-Query wirft nachweislich RuntimeError
- Request ohne tenant_id schlägt an API-Grenze fehl

## Source Material

- Wiki `mandantenfaehigkeit.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/mandantenfaehigkeit.md`
