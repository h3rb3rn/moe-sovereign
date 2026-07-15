# E-2.2 Trust-Score & Selbstkorrektur-Loop

Level: Epic
Status: Open

Parent Initiative: I-2 Pipeline Quality Gate Stack (`../initiative.md`)

## Epic Goal

Quantitatives, deterministisches Qualitätsverdikt nach jedem Expert-Durchlauf
mit automatischer Selbstkorrektur bei borderline Ergebnissen und Human-Gate
bei regulatorisch kritischen Antworten.

## Initiative Alignment

Schließt die wichtigste Qualitätslücke: der Judge entscheidet heute ohne
messbare Schwellen. Trust-Score + Self-Critique ersetzen binäres
COMPLETE/NEEDS_MORE_INFO durch graduelles, begründetes Verdikt.

## Users / Consumers

- Judge-Node: empfängt quantitativen Score statt implizitem Qualitätsurteil
- Expert-Nodes: bekommen bei `proceed-with-assumption` strukturiertes Gap-Feedback
- Operator: Human-Gate-Endpoint für Approval bei blockierten Antworten

## Capability / Proof Package

1. **Trust-Score** (0.0–1.0) aus gewichteten Faktoren:
   - `source_count`: Anzahl zitierter Neo4j-Knoten
   - `conflict_count`: Widersprüche zwischen Expert-Antworten
   - `cross_references_resolved`: Abdeckung der Planner-Teilfragen
   - `source_hashes_valid`: Integrität der ChromaDB-Retrieval-Hashes (Hard-Block)
   Drei Buckets: `proceed` (≥0.65), `proceed-with-assumption` (0.30–0.65), `block` (&lt;0.30).
   Hard-Block auf invalide Hashes unabhängig vom Score.

2. **Self-Critique Iteration Loop**: Bei `proceed-with-assumption` → max. 2
   Korrekturläufe mit explizitem Gap-Feedback an Experts. Nach Limit-Erschöpfung
   oder `block` → Eskalation. LangGraph: Conditional Edge Judge → Experts mit
   `critique_iterations`-Check im State.

3. **Human-in-the-Loop Gate**: Bei `proceed-with-assumption` + kritischer
   Kategorie (Constitution-`warn`-Treffer) → Antwort eingefroren, Gate-State
   in Valkey, `POST /gates/{id}/approve` setzt Pipeline fort.
   Kein Polling im laufenden Thread — State-basierter Wakeup.

## Scope

- `services/trust_score.py` (neu) — Score-Berechnung + Hard-Block-Logik
- `configs/trust-score-weights.yaml` (neu) — Gewichte und Schwellen konfigurierbar
- Erweiterung `pipeline/state.py` um `trust_score`, `trust_verdict`, `critique_iterations`
- LangGraph: neue Conditional Edge Judge → Experts
- `services/gate.py` (neu) — Valkey-backed Gate-State
- `routes/gates.py` (neu) — `POST /gates/{id}/approve`, `GET /gates/{id}`
- Integration in `graph/synthesis.py` nach Expert-Completion

## Out Of Scope

- Änderung der Expert-Auswahl-Logik oder des Routings
- Neuer Modell-Typ oder Inference-Backend
- Kontext-Persistenz (→ E-2.3)

## Dependencies / Preconditions

- E-2.1 abgeschlossen (Boundary-Contract-Violations als Cascade-Input)
- ChromaDB-Retrieval mit Hash-Information verfügbar (prüfen)
- Valkey/Redis-Verbindung vorhanden (✓)
- Constitution `warn`-Events in AgentState propagiert (✓)

## Current Status

Nicht begonnen. Trust-Score-Gewichte müssen initial mit Dummy-Werten
kalibriert und dann empirisch angepasst werden.

## Known Facts / Evidence

- `graph/synthesis.py`: Expert-Outputs in `agentic_history` ohne Qualitätsmaß
- `pipeline/state.py`: kein `trust_score`-Feld vorhanden
- ChromaDB-Retrieval-Ergebnisse enthalten keine Hash-Felder (muss ergänzt werden)

## Success Semantics

- Nach Expert-Completion enthält AgentState `trust_score` (float) und `trust_verdict` (str)
- `block`-Verdict stoppt Loop deterministisch, Kafka `moe.audit` erhält Block-Event
- Bei `proceed-with-assumption`: zweiter Expert-Durchlauf mit Gap-Feedback startet automatisch
- `GET /gates/{id}` gibt `{"status": "waiting"}` wenn Antwort eingefroren
- `POST /gates/{id}/approve` setzt Pipeline innerhalb von &lt;500ms fort

## Failure Semantics

- Trust-Score-Berechnung wirft Exception → fail-open, Score = None, proceed
- Gate-Valkey-Ausfall → fail-open, Antwort wird gesendet ohne Gate
- Self-Critique-Loop überschreitet `critique_iterations` → Eskalation, kein Infinite-Loop

## Downstream Contract

Trust-Score in AgentState ist Input für E-2.4 (Cynefin-Klassifikation nutzt
Score als Komplexitätssignal). Gate-Mechanismus ist Basis für E-2.5
(Multi-Tenant-Approval-Workflows).

## Quality Constraints

- Trust-Score-Berechnung: deterministisch, kein LLM-Call, &lt;50ms
- Fail-open: alle Ausnahmen in Score-Handler gecatcht, Pipeline nie geblockt durch Exception
- Gate-State-TTL: 24h (danach automatisch expired, Antwort verworfen)
- Max. Critique-Iterations: 2 (konfigurierbar in Constitution YAML)

## Risks / Assumptions

- ChromaDB gibt keine Hash-Informationen zurück → Hard-Block nicht implementierbar ohne ChromaDB-API-Erweiterung
- Trust-Score-Gewichte initial unkalibriert → starten mit konservativen Defaults, empirisch verfeinern
- Gate-Endpoint muss authentifiziert sein (bestehende Auth-Middleware wiederverwendbar)

## Refinement Check

- Authority model check: rein lokal, Gate-Endpoint über bestehende Auth-Middleware
- Related backlog check: benötigt E-2.1 als Vorbedingung
- Code-contract check: AgentState-Erweiterungsmuster aus cascade.py bereits etabliert
- Refinement result: bereit nach E-2.1-Abschluss

## Exit Criteria

- `pytest tests/ -q` grün
- Trust-Score in AgentState nachweislich gesetzt nach Expert-Completion
- `block`-Verdict nachweislich in Kafka `moe.audit`
- Gate-Approve-Flow E2E-getestet

## Story Breakdown

*(bei Sprint-Start zu füllen)*

## Source Material

- Wiki `re-loop-trust-substrate.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/re-loop-trust-substrate.md`
- Wiki `human-in-the-loop.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/human-in-the-loop.md`
- Wiki `gate-workflow.md`: `/opt/deployment/adSCAILE_Framework/adSCAILE_shared_tools/adscaile_wiki-main/wiki/gate-workflow.md`
