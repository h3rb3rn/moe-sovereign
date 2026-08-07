# MoE Sovereign — Gesamtbewertung von Deployment und Codebasis

## Dokumentstatus

| Feld | Wert |
|---|---|
| Bewertungsstand | 31.07.2026 |
| Letzte Live-Nachprüfung | 31.07.2026, 02:50 CEST |
| Bewerteter Kern | `moe-infra`, `langgraph-orchestrator`, MCP, Wissens- und Persistenzdienste |
| Ergänzend betrachtet | LLM-Backends, Monitoring, Enterprise- und Airflow-Nebenstack |
| Live-Image | `sha256:5f3e0eeda248b8743df3ebed5950125f57d0c9852e71d0ddab720bfa022e3040` |
| Git-Stand | `29a1b368` auf `fix/codex-responses-template-routing`; Remote-Upstream nicht mehr vorhanden |
| Gesamturteil | **Gelb — Kern und geprüfter Mixed-Expert-Pfad wirksam; breite Planner-, Betriebs- und Release-Reife noch offen** |

Diese Bewertung ist eine technische Momentaufnahme. Laufzeitwerte wie
Speicherbelegung, Modellresidenz und Containerzustände können sich ändern.
Die fachlichen GAPs wurden dagegen durch Codeprüfung, Tests, persistierte
Audits und reproduzierte End-to-End-Aufrufe bestätigt.

## Kurzfassung

MoE Sovereign besitzt inzwischen einen belastbaren Plattformkern:
Orchestrator, Persistenz, GraphRAG, MCP, Authentisierung, Monitoring und
Maintenance starten zuverlässig. Die vollständige Testsuite ist grün, die
Python-Abhängigkeiten sind exakt gesperrt und die zentralen Dateien im
laufenden Image stimmen mit der Arbeitskopie überein.

Der in TASK-37 reproduzierte kritische Mixed-Expert-Pfad ist nach TASK-38
end-to-end wirksam: Planner-Verträge scheitern geschlossen, drei
Precision-Tasks werden tatsächlich über MCP ausgeführt, der Code-Experte
läuft, Quality Gate und Usage-Audit schließen terminal ab und fünf
aufeinanderfolgende Template-Antworten bestanden alle Ground-Truth-Prüfungen.
Ein echter Kaltlauf blieb mit 157,472 Sekunden unter dem normalen
300-Sekunden-Budget; vier Warmläufe benötigten 40,788 bis 50,576 Sekunden.

Die Plattform ist damit deutlich weiter als die Baseline, aber nicht
allgemein „fertig“. Der dedizierte Planner liefert für den Prüf-Prompt noch
keinen brauchbaren Plan; eine bewusst enge deterministische Recovery
übernimmt. Der Planner-Prompt bleibt mit 13.432 Tokens groß, der erfolgreiche
Pfad startet trotz positivem Trust noch Self-Critique und einen zweiten
Merger, und die Single-GPU-Optimierung verwendet dasselbe Qwen3.6-Modell für
Expert und Merger. Native Modellaufrufe bleiben fachlich unzuverlässig: In
drei finalen Wiederholungen war der Wochentag jedes Mal falsch.

Die Plattform ist daher:

- für Infrastruktur-, Integrations- und kontrollierte einfache API-Nutzung
  betriebsbereit;
- für native Modellnutzung nur mit anwendungsseitiger Qualitätskontrolle
  geeignet;
- für den konkret validierten Mixed-Precision-/Code-Workflow bedingt
  freigabefähig;
- für breite, unbekannte Expert-Template-Aufgaben weiterhin nur als
  Beta/Canary freizugeben, bis der Planner ohne enge Recovery zuverlässig
  ausführbare Verträge erzeugt.

## Prüfgrundlage

### Live- und Deployment-Prüfungen

- `langgraph-orchestrator`: running, healthy, `RestartCount=0`.
- `/health`: `status=ok`.
- `/ready`: alle sechs Prüfungen positiv:
  - Orchestration Graph;
  - Valkey;
  - User-Datenbank;
  - Neo4j;
  - MCP Precision;
  - Chroma.
- Zentrale Host-/Container-Dateien wurden per SHA-256 verglichen und waren
  identisch:
  - `main.py`;
  - `config.py`;
  - `graph/planner.py`;
  - `graph/router_nodes.py`;
  - `graph/synthesis.py`;
  - `services/trivial_fast_path.py`;
  - `services/trust_score.py`;
  - `requirements.lock.txt`.
- Vollständiger Testlauf: **714 Tests bestanden in 4,00 Sekunden**.
- `pip check`: keine defekten Python-Abhängigkeiten.
- MCP Precision: healthy, **58 Werkzeuge** verfügbar.
- Prometheus: ready.
- Grafana: Datenbankstatus `ok`.
- OpenSearch: Clusterstatus `green`, 100 % aktive Shards, keine
  unzugewiesenen Shards.
- SessionMesh: API-Status `ok`.
- N04-/RGTX-Ollama-Endpunkte sind erreichbar. Zum Prüfzeitpunkt war auf den
  geprüften Ports kein Modell resident.

### End-to-End-Nachweise

1. Ein konservativer einfacher `moe-auto`-Probeaufruf antwortete nach den
   TASK-36-Korrekturen mit HTTP 200 und exakt `OK` in 10,27 Sekunden. Die
   Baseline war zuvor nach 300,108 Sekunden in den Timeout gelaufen.
2. Die finale native Qwen3.6-Matrix lieferte 3/3 HTTP 200 und valides JSON,
   aber 0/3 vollständig korrekte Antworten: `weekday_de` war dreimal
   falsch. Der Kaltlauf benötigte 139,749 Sekunden, die zwei Warmläufe
   3,610 und 3,555 Sekunden.
3. Das private horndev-Expert-Template lieferte nach den TASK-38-Fixes einen
   erfolgreichen Kaltlauf in 157,472 Sekunden und vier Warmläufe in
   40,788–50,576 Sekunden. Alle 5/5 Antworten waren HTTP 200,
   schemaexakt und bestanden 7/7 Ground-Truth-Checks.
4. Im finalen Template-Lauf wurden Planner, `gcd_lcm`, `unit_convert`,
   `day_of_week`, Code-Reviewer, GraphRAG, Merger, Self-Critique,
   zweiter Merger und Quality Gate tatsächlich erreicht. Die API-Usage
   `19.888/1.589` Prompt-/Completion-Tokens entsprach exakt der Summe der
   persistierten Stage-Audits.

### Grenzen der Bewertung

- Es wurde kein Lasttest mit vielen gleichzeitigen Mandanten durchgeführt.
- Die Testsuite ist sehr schnell und deckt Verträge und Integrationslogik gut
  ab, ersetzt aber keine wiederholten kalten GPU-End-to-End-Läufe. Für das
  reparierte Template liegt erst ein erfolgreicher unabhängiger Kaltlauf,
  nicht die geplante Dreierserie, vor.
- Externe Dienste wurden nur soweit bewertet, wie sie aus dem lokalen
  Deployment und den konfigurierten Netzen erreichbar waren.
- Rechtliche Zertifizierung, Datenschutzprüfung und Penetrationstest sind
  nicht Bestandteil dieser technischen Bewertung.

## Statusmatrix

| Bereich | Status | Bewertung |
|---|---|---|
| Kerncontainer und Readiness | Grün | Stabil, keine Neustarts, alle kritischen Checks positiv |
| Python-Code und Abhängigkeiten | Grün | 714 Tests grün, vollständiger Lock-Satz, `pip check` grün |
| Einfacher `moe-auto`-Fast-Path | Grün | Realer E2E-Aufruf erfolgreich und deutlich schneller als Baseline |
| Native Modell-API | Gelb | Schnell und verfügbar, fachliche Fehler trotz Selbstverifikation |
| Geprüftes Mixed-Expert-Template | Grün/Gelb | 5/5 fachlich korrekt; ein Kaltlauf und vier Warmläufe unter 300 s, breite Generalisierung noch offen |
| MCP-Server | Grün | Healthy und 58 Tools verfügbar |
| Planner-zu-MCP-Ausführung | Grün/Gelb | Taskverlust geschlossen; enger deterministischer Recovery-Pfad wirksam, Planner selbst liefert beim Prüf-Prompt noch `{}` |
| GraphRAG | Gelb/Grün | Technisch aktiv; taskabhängiger Trust korrigiert, konkrete Relevanz des 325-Zeichen-Kontexts unbewiesen |
| Cache | Grün | `no_cache=true` überspringt L0 und L1; Knowledge-/GraphRAG-Caches sind davon getrennt |
| Trust/Self-Correction | Gelb/Grün | Task-/evidenzabhängig und budgetiert; positiver Trust startet noch unnötig Self-Critique/zweiten Merger |
| Guard | Gelb | Auditiert und SLO-begrenzt; kalter Guard fällt bewusst offen aus |
| Usage-/AI-I/O-Audit | Grün | Native und orchestrierte Pfade auditiert; finale API-Usage stimmt exakt mit Stage-Audits überein |
| Wissens-/Maintenance-Pfade | Gelb/Grün | HABE erfolgreich; Graph-Decay-Telemetrie unvollständig |
| Monitoring | Grün | Prometheus, Grafana, cAdvisor und Exporter verfügbar |
| Enterprise-Integrationen | Gelb | Marquez/lakeFS erreichbar; NiFi-TLS-Verifikation defekt |
| Repository-/Releasezustand | Rot | 170 Worktree-Einträge, kein Upstream, kein freigegebener Commit zum Live-Image |
| Ressourcenreserve | Gelb | Genügend RAM verfügbar, aber Swap voll und OpenSearch am Limit |
| Airflow-Nebenstack | Rot | Flower und Kernprozesse mit tausenden Restarts; Flower weiterhin in permanenter Restart-Schleife |

## Was gut funktioniert

### 1. Kern-Deployment und Startverhalten

- Der Orchestrator startet vollständig, initialisiert seine Datenbanken,
  Kafka, Skill Registry, Checkpointing, GraphRAG und MCP-Verbindung.
- Die Readiness-Prüfung unterscheidet kritische und optionale Subsysteme und
  meldet den tatsächlichen Zustand statt nur einen HTTP-Prozessstatus.
- Valkey, Postgres, Neo4j und Chroma sind end-to-end erreichbar.
- Beim letzten Live-Check lief der Orchestrator seit ungefähr 23 Stunden
  ohne Containerneustart.
- Der Orchestrator benötigt mit ungefähr 486 MiB von 6 GiB nur einen kleinen
  Teil seines Containerlimits.

### 2. Codequalität und reproduzierbarer Python-Unterbau

- Die vollständige Testsuite mit 714 Tests läuft grün.
- Der exakte `requirements.lock.txt`-Satz verhindert eine unkontrollierte
  Neuauflösung von Laufzeitabhängigkeiten.
- Das Python-Basisimage ist digest-gepinnt.
- `pip check` bestätigt einen konsistenten installierten Abhängigkeitsgraph.
- Ein Runtime-Entry-Point-Manifest und Reachability-Tests schützen gegen
  erneute Drift zwischen dynamischen Einstiegspunkten und importierbarem
  Code.
- Die in TASK-35 inventarisierten statisch nicht belegten Kandidaten wurden
  in TASK-36 entweder entfernt, verdrahtet oder als bewusste Framework-,
  HTTP-, CLI- beziehungsweise Betreiberverträge erfasst.

### 3. Einfacher Fast-Path

- Eine gemeinsame konservative Eligibility-Regel begrenzt den
  Planner-/Judge-Fast-Path.
- Rechen-, Rechts-, Recherche-, Aktualitäts-, Datei-, Bild-, Tool-,
  Mehrturn- und Expertenaufgaben werden nicht vorschnell als trivial
  eingestuft.
- Nur ein explizit markierter, konfliktfreier Einzelexpertenpfad darf teure
  Stufen überspringen.
- Quality Gate und Constitution Enforcement bleiben Teil dieses Pfads.
- Der reale Probeaufruf belegt die Wirksamkeit: 10,27 Sekunden statt eines
  300-Sekunden-Timeouts.

### 4. Sicherheit und Mandantentrennung

- Private Expert-Templates werden anhand der Benutzer-/Template-ID
  autorisiert.
- Ein API-Key eines anderen Benutzers konnte das private horndev-Template
  nicht aufrufen.
- Der für den Benchmark ausdrücklich freigegebene temporäre horndev-Key
  wurde ausschließlich im Prozessspeicher gehalten und nach jedem Lauf in
  einem `finally`-Block widerrufen sowie aus Valkey invalidiert. Sämtliche
  temporären TASK-38-Keys sind inaktiv; kein Cacheeintrag blieb zurück.
- Erforderliche HITL-Gates scheitern geschlossen.
- Guard-Providerfehler und Cancellation werden auditiert.
- Active-Request-Cleanup und terminale AI-I/O-Audits funktionieren
  grundsätzlich auch bei Timeout.

### 5. Werkzeuge, Wissen und Persistenz

- Der MCP-Server stellt 58 Werkzeuge für Mathematik, Datum, Einheiten,
  Code-/Dateizugriff, Graph, Recherche und weitere Präzisionsaufgaben bereit.
- GraphRAG initialisierte zuletzt 16.824 Entitäten und 17.778 Beziehungen.
- Der Semantic Router pflegt seine Prototypen in Chroma.
- Checkpoints werden in Postgres persistiert.
- HABE-Rebuilds lesen ungefähr 15.900 Graphtripel und schreiben erfolgreich
  einen aktualisierten holografischen Vektor.
- Kafka Producer und Consumer verbinden sich und übernehmen ihre
  Topic-Partitionen.

### 6. Reparierter Mixed-Expert-Pfad

- Boundary-Verträge sind ein kritischer Readiness-Bestandteil.
- Jeder geplante Task erhält eine stabile ID und einen terminalen
  Ledgerstatus; das Quality Gate blockiert unvollständige Pläne.
- Drei geplante Precision-Tasks wurden im finalen Lauf tatsächlich an
  `gcd_lcm`, `unit_convert` und `day_of_week` dispatcht und korrekt
  ausgeführt.
- Eine absolute monotone Deadline begrenzt alle Modell-, Tool-, Retry- und
  Synthesestufen gemeinsam.
- Expert und Judge liefern mit `think:false` öffentliche Antworten innerhalb
  ihres Tokenbudgets. Thinking-only- oder leere Expert-Antworten gelten als
  Fehler.
- Die finale Stagefolge endete mit `quality_gate passed`; Active Request,
  API-Key und Auditdatensätze waren danach terminal bereinigt.

### 7. Monitoring und Betrieb

- Prometheus, Grafana, cAdvisor, Node Exporter und Dozzle laufen.
- OpenSearch ist fachlich grün und hat keine unzugewiesenen Shards.
- Marquez und lakeFS sind aus dem Orchestrator erreichbar.
- Garage Object Storage und der Maintenance-Container sind healthy.
- Das Dateisystem ist mit 54 % Belegung nicht knapp; ungefähr 215 GiB waren
  beim Messlauf frei.

## Bestätigte GAPs

### GAP-01 — Komplexe Expert-Templates liefern kein Ergebnis

- **Priorität:** P0 / kritisch
- **Status:** für den geprüften Mixed-Precision-/Code-Pfad geschlossen;
  breite Template-Generalisation offen
- **Nachweis:** Ein erfolgreicher Kaltlauf in 157,472 Sekunden und vier
  Warmläufe in 40,788–50,576 Sekunden; 5/5 HTTP 200 und 7/7 Checks.
- **Restrisiko:** Es wurden noch keine drei voneinander unabhängigen
  Kaltläufe und keine repräsentative Matrix freier unbekannter Aufgaben
  abgenommen.

### GAP-02 — Precision-Tasks können still verschwinden

- **Priorität:** P0 / kritisch
- **Status:** geschlossen
- **Nachweis:** Schema-Validierung, genau ein begrenzter Repair,
  deterministische enge Precision-Normalisierung, terminales Task-Ledger
  und blockierendes Quality Gate sind verdrahtet. Der finale Trace belegt
  `gcd_lcm`, `unit_convert` und `day_of_week` jeweils `started/done`.
- **Fail-closed:** Unbekannte, mehrdeutige, lückenhaft nummerierte oder
  über dem Laufzeitmaximum liegende Pläne werden nicht still gekürzt oder
  als generische Aufgabe ausgeführt.

### GAP-03 — Kein durchgängiges Restbudget pro Request

- **Priorität:** P0 / kritisch
- **Status:** geschlossen
- **Nachweis:** Eine absolute monotone Deadline wird von API-Preprocessing
  bis Synthese durchgereicht. Planner/Fallback, Expert, MCP, GraphRAG,
  Recherche, Thinking, Judge, Self-Critique, Retries und Retry-Sleeps
  verwenden ausschließlich das verbleibende Budget.

### GAP-04 — Korrekter interner Kandidat wird nicht gerettet

- **Priorität:** P0 / kritisch
- **Status:** geschlossen
- **Nachweis:** Bei zu kleiner Merger-Restzeit kann ein vollständiger,
  nicht medizinischer/rechtlicher und Constitution-konformer
  Executor-Kandidat transparent als degradiert zurückgegeben werden.
  Unvollständige Pläne sind ausdrücklich nicht rettbar.

### GAP-05 — Trust-Score ist nicht ausreichend aufgabentypabhängig

- **Priorität:** P0 / hoch
- **Status:** funktional geschlossen, Effizienz-Follow-up offen
- **Nachweis:** MCP-Präzisionsprovenienz, Taskabdeckung und Creative-Fit
  werden aufgabentypabhängig bewertet; irrelevanter Graphkontext zählt
  nicht pauschal als Quelle.
- **Restoptimierung:** Der finale positive `PROCEED`-Lauf startete dennoch
  Self-Critique und einen zweiten Merger. Dieser Pfad ist budgetiert und
  korrekt, aber mit drei Judge-Aufrufen unnötig teuer.

### GAP-06 — Native Modellantworten sind nicht verlässlich validiert

- **Priorität:** P0 / hoch
- **Status:** offen
- **Befund:** Im finalen Deployment lieferte das native Qwen3.6 3/3
  HTTP 200 und schemaexaktes JSON, aber in allen drei Läufen einen falschen
  Wochentag. Die übrigen sechs automatisierten Checks bestanden.
- **Auswirkung:** Eine schnelle und formal gültige Antwort darf nicht mit
  fachlicher Zuverlässigkeit gleichgesetzt werden.

### GAP-07 — Client-Tokenbudget begrenzt interne Stufen nicht

- **Priorität:** P1 / hoch
- **Status:** geschlossen
- **Nachweis:** Das Clientlimit wird in Expert-/Judge-Stufen begrenzt
  weitergereicht. `think:false` verhindert, dass verstecktes Reasoning das
  gesamte Limit vor dem öffentlichen Inhalt verbraucht; leere
  Thinking-only-Ergebnisse gelten als Fehler.

### GAP-08 — Überdimensionierte Kontexte und Planner-Prompts

- **Priorität:** P1 / hoch
- **Status:** teilweise geschlossen
- **Nachweis:** Template-Planner und Merger wurden auf 32k gesetzt;
  Expert-Kontexte werden adaptiv gewählt und der Planner läuft auf einer
  separaten RGTX-Ressource.
- **Rest-GAP:** Der kurze Benchmark erzeugt weiterhin 13.432
  Planner-Eingabetokens. Das dedizierte Modell antwortet beim Prüf-Prompt
  mit `{}`; die enge Recovery, nicht der Planner selbst, erzeugt den Plan.

### GAP-09 — `no_cache=true` überspringt L1 nicht vollständig

- **Priorität:** P1 / mittel
- **Status:** geschlossen
- **Nachweis:** `no_cache=true` kehrt vor L0- und L1-Antwortcache zurück.
  GraphRAG-/Knowledge-Caches bleiben davon getrennte Retrieval-Pfade.

### GAP-10 — GraphRAG-Relevanz und Provenienz sind inkonsistent

- **Priorität:** P1 / hoch
- **Status:** teilweise geschlossen
- **Nachweis:** Graphkontext ist keine pauschale Trust-Quelle mehr; der
  Trust-Score ist task- und evidenzabhängig.
- **Rest-GAP:** Der finale Prompt lud weiterhin 325 Zeichen GraphRAG aus
  dem Knowledge-Cache. Ein messbarer Nutzen für diese Aufgabe wurde nicht
  belegt; eine Relevanzschwelle vor der Synthese bleibt sinnvoll.

### GAP-11 — Expertendiversität ist nur Policy-Diversität

- **Priorität:** P1 / mittel
- **Status:** offen und jetzt klar ausgewiesen
- **Befund:** Alle acht konfigurierten Experten des geprüften Templates
  verwenden dasselbe `qwen3.6:35b@N04-RTX`; nur Systemprompts unterscheiden
  die Rollen.
- **Auswirkung:** Rollenbezeichnungen können echte Modellvielfalt
  suggerieren, obwohl Laufzeit-, Fehler- und Wissensprofil identisch sind.
  Für das Single-GPU-Template nutzt auch der Merger bewusst dasselbe
  Qwen3.6-Modell; unabhängige Judge-Modelldiversität wurde zugunsten des
  300-Sekunden-SLO aufgegeben.

### GAP-12 — Planner-/Judge-Prompts sind gegenüber der Konfiguration veraltet

- **Priorität:** P1 / hoch
- **Status:** offen/erweitert
- **Befund:** Prompts nennen für Mathematik noch
  `phi4:14b@N04-RGTX` und für Creative Writing
  `qwen3.6:35b-spec@N04-RTX`, tatsächlich wird das normale Qwen3.6-Modell
  genutzt.
- **Auswirkung:** Der Planner trifft Entscheidungen auf Basis nicht mehr
  zutreffender Rollen-/Modellannahmen.
- **Zusatzbefund:** Der neue `qwen3-planner:q4km` liefert beim
  Abnahmeprompt reproduzierbar `{}` beziehungsweise leere `subtasks`.
  Die deterministische Recovery deckt nur explizit unterstützte,
  nummerierte Verträge ab; das Modell-/Prompt-Verhältnis muss für freie
  Aufgaben korrigiert oder neu evaluiert werden.

### GAP-13 — Keine dauerhaft warme LLM-Kapazität

- **Priorität:** P1 / hoch
- **Status:** teilweise geschlossen
- **Befund:** Planner wurde auf N04-RGTX getrennt und ist warm
  wiederverwendbar. Expert und Merger teilen auf N04-RTX dasselbe Modell,
  wodurch der zweite 35B-Swap entfällt.
- **Auswirkung:** Jeder größere Request kann Modellladezeit verursachen.
  Der finale native Kaltlauf benötigte 139,749 Sekunden, der
  Template-Kaltlauf 157,472 Sekunden. Eine garantierte Warmhaltung oder
  dedizierte unabhängige Judge-Kapazität fehlt weiterhin.

### GAP-14 — Kalter Guard fällt bewusst offen aus

- **Priorität:** P1 / Security-Trade-off
- **Status:** implementierte Betriebsentscheidung, Restrisiko offen
- **Befund:** Ist `llama-guard3:8b` nicht resident, wird kein kalter
  Modellstart ausgelöst; der Request läuft mit auditiertem
  `fail_open_not_warm` weiter.
- **Nutzen:** Der Guard blockiert nicht mehr das gesamte 300-Sekunden-SLO.
- **Risiko:** Modellbasierte Guard-Prüfung ist in diesem Zustand nicht
  wirksam. Erforderliche HITL-Gates bleiben davon getrennt fail-closed.

### GAP-15 — Modellbasierte Complexity-Klassifikation fehlt

- **Priorität:** P1 / mittel
- **Status:** offen
- **Befund:** `transformers` ist nicht installiert; der Orchestrator fällt
  auf eine Heuristik zurück.
- **Auswirkung:** Die Funktion arbeitet, aber nicht mit der dokumentierten
  beziehungsweise erwartbaren Modellklassifikation. Grenzfälle können
  falsche Stufenbudgets und Routingentscheidungen erhalten.

### GAP-16 — Timeout-Telemetrie verliert reale Nutzung

- **Priorität:** P1 / hoch
- **Status:** geschlossen
- **Nachweis:** Native Latenz und AI-I/O-Audit sind vorhanden.
  Timeout-/Fehlerpfade aggregieren terminale Stage-Audits und einen
  Request-Snapshot. Im finalen Erfolgsfall entsprach die API-Usage
  `19.888/1.589` exakt Planner+Expert+Judge-Audit.

### GAP-17 — Graph-Decay-Telemetrie ist nicht vollständig initialisiert

- **Priorität:** P2 / mittel
- **Status:** offen, derzeit Dry-Run
- **Befund:** Maintenance-Abfragen warnen, dass `hit_count`, `miss_count`
  und `last_hit` auf den betrachteten Graphknoten nicht existieren.
- **Auswirkung:** Der Dry-Run meldet null Kandidaten, kann aber ohne
  konsistente Retrieval-Telemetrie keine belastbare Decay-Entscheidung
  vorbereiten.

### GAP-18 — NiFi ist aus dem Orchestrator nicht vertrauenswürdig erreichbar

- **Priorität:** P1 / mittel
- **Status:** offen
- **Befund:** TLS-Verifikation scheitert am selbstsignierten
  NiFi-Zertifikat.
- **Auswirkung:** Der Enterprise-Stack wird nur als 2/3 erreichbar
  bewertet. NiFi-abhängige Datenflüsse sind aus diesem Pfad nicht
  betriebsbereit.

### GAP-19 — Airflow Flower befindet sich in einer Restart-Schleife

- **Priorität:** P1 / hoch für den Nebenstack
- **Status:** offen, live bestätigt
- **Befund:** Am 31.07.2026 wurden für Flower **12.839 Neustarts** gezählt.
  Worker, Scheduler und Webserver lagen ebenfalls bei ungefähr
  **5.627–5.640 Restarts** und befanden sich beim Check wieder in
  `health: starting`. Der
  Container ruft `airflow celery` auf; dieses Kommando ist in der
  installierten Airflow-CLI nicht verfügbar.
- **Auswirkung:** Flower-Monitoring ist nicht verfügbar und erzeugt
  permanente Restart-Last. Der Fehler liegt außerhalb des
  `moe-infra`-Kern-Compose, gehört aber zum betrachteten Gesamtsystem.

### GAP-20 — Repository ist nicht releasefähig

- **Priorität:** P0 / kritisch für Deployment-Governance
- **Status:** offen
- **Befund:** 118 versionierte Änderungen, 52 unversionierte Einträge und
  eine enthaltene Löschung; insgesamt **170 Worktree-Einträge**. Der
  Branch besitzt keinen Upstream.
- **Auswirkung:** Das laufende Image ist für die geprüften Kerndateien
  kohärent, aber der Gesamtstand kann nicht zuverlässig aus einem
  freigegebenen Remote-Commit rekonstruiert, geprüft oder zurückgerollt
  werden.
- **Hinweis:** Die Änderungen stammen aus mehreren Arbeitssträngen und
  dürfen nicht pauschal verworfen werden.

### GAP-21 — Ressourcenreserve einzelner Dienste ist knapp

- **Priorität:** P1 / mittel
- **Status:** offen, beobachten
- **Befund:** OpenSearch belegt ungefähr 1.010 MiB von 1 GiB
  beziehungsweise 98,67 % seines Containerlimits. Der Cluster ist noch
  `green`.
- **Hostzustand:** Etwa 11 GiB RAM waren verfügbar, der 974-MiB-Swap war
  jedoch vollständig belegt.
- **Auswirkung:** Kein aktueller Ausfall, aber erhöhtes OOM- und
  Latenzrisiko bei Lastspitzen.

### GAP-22 — Healthchecks decken nicht alle Dienste fachlich ab

- **Priorität:** P2 / mittel
- **Status:** offen
- **Befund:** `moe-admin` ist über den Root-Pfad erreichbar und leitet mit
  HTTP 303 weiter, besitzt aber keinen `/health`-Endpunkt. Mehrere
  Nebencontainer melden nur „running“ statt eines fachlichen
  Healthchecks.
- **Auswirkung:** Docker- und externe Überwachung können Prozessleben mit
  tatsächlicher Funktionsfähigkeit verwechseln.

### GAP-23 — Optionale Authentik-Konfiguration ist unvollständig

- **Priorität:** P2 / niedrig, solange das Profil deaktiviert bleibt
- **Status:** offen
- **Befund:** Compose meldet fehlende Authentik-Postgres-, Secret-,
  Tag- und Portvariablen.
- **Auswirkung:** Kein Kernfehler bei deaktiviertem Profil. Eine spätere
  Aktivierung ohne vollständige Konfiguration würde jedoch fehlschlagen
  beziehungsweise unsicher starten.

### GAP-24 — Veraltete Integrationsabhängigkeit

- **Priorität:** P2 / mittel
- **Status:** offen
- **Befund:** `langchain-community` meldet beim Orchestratorstart eine
  Deprecation und verweist auf eigenständige Integrationspakete.
- **Auswirkung:** Kein aktueller Ausfall, aber zukünftiges
  Kompatibilitäts- und Wartungsrisiko.

### GAP-25 — Gleichnamige Templates erschweren Betrieb und Freigabe

- **Priorität:** P2 / mittel
- **Status:** offen
- **Befund:** Das geprüfte Template existiert gleichnamig für mehrere
  Benutzer sowie als nahezu leeres Admin-Template. Die technische
  Autorisierung per ID arbeitet korrekt.
- **Auswirkung:** Anzeige, Benchmarking und Support können versehentlich das
  falsche Template meinen. Ein explizites Sharing-/Grant-Modell fehlt.

### GAP-26 — Testsuite beweist keine LLM-SLO-Wirksamkeit

- **Priorität:** P1 / hoch
- **Status:** teilweise geschlossen
- **Nachweis:** Ein versionierter E2E-Harness prüft identischen Prompt,
  Schema, sieben Ground-Truth-Kriterien, temporären Key-Lifecycle und
  Native-/Template-Ziele. Ein Kalt- und vier Warmläufe des Templates
  bestanden.
- **Rest-GAP:** GPU-E2E ist noch kein verpflichtender CI-/Release-Gate;
  drei unabhängige Kaltläufe, Parallel-/Mandantenlast, Modell-Swapping und
  breitere Promptklassen fehlen.

## Nicht als GAP zu wertende, im Benchmark nicht verwendete Funktionen

Nicht jede konfigurierte Funktion muss bei jedem Prompt laufen. Im
TASK-37-Prompt waren folgende Nichtausführungen sachlich vertretbar:

- Web Research, weil keine aktuelle Webinformation benötigt wurde;
- Agent-Tool-Erweiterungen, weil der Client keine `tools` mitsendete;
- Creative-, Long-Context- und Technical-Support-Experten, weil der Prompt
  diese Rollen nicht benötigte;
- tatsächliche Guard-Modellinferenz aufgrund der bewusst gewählten
  Warm-only-Policy.

Ein echtes GAP liegt dagegen vor, wenn eine vom Planner ausdrücklich
eingeplante Teilaufgabe von keinem Executor übernommen wird oder eine
notwendige Qualitätsstufe nur wegen verbrauchtem Budget nicht erreicht wird.

## Optimierungs- und Behebungsplan

Legende: **erledigt** = implementiert und mindestens durch Tests belegt;
**wirksam validiert** = zusätzlich im realen E2E nachgewiesen;
**offen/teilweise** = verbleibender Arbeitsumfang.

### P0 — Korrektheit und Auslieferbarkeit

1. **Precision-Task-Vertrag härten — erledigt und wirksam validiert**
   - `mcp_tool` und `mcp_args` im Planner-Schema verbindlich machen.
   - Planner-Ausgaben vor Graphübergabe vollständig validieren.
   - Genau einen begrenzten Repair-/Replan-Versuch erlauben.
   - Danach expliziten strukturierten Fehler oder bewussten
     Expertenfallback ausführen.
   - Niemals einen Task still entfernen.

2. **Eine gemeinsame monotone Deadline einführen — erledigt**
   - Absolute Request-Deadline im Zustand speichern.
   - Restbudget an jede Modell- und Toolstufe übergeben.
   - Native- und Fallback-Planner teilen sich ein Budget.
   - SDK-Retries dürfen die Requestdeadline nicht verlängern.
   - Folgestufen nur starten, wenn eine definierte Mindestrestzeit
     vorhanden ist.

3. **Korrekte Kandidaten retten — erledigt**
   - Thinking und Merger möglichst in einem Judge-Aufruf konsolidieren.
   - Bereits validierten Kandidaten bei knappem Budget als klar
     gekennzeichnetes degradiertes Ergebnis ausliefern.
   - Keine zweite unbudgetierte Judge-/Self-Critique-Schleife starten.

4. **Trust-Score nach Aufgabentyp differenzieren — erledigt**
   - Deterministische MCP-Ergebnisse als strukturierte Provenienz werten.
   - Rechenbeweise algorithmisch prüfen.
   - Kreative Ausgaben nicht wegen fehlender Tatsachenquellen blockieren.
   - `BLOCK`, Direktantwort und Self-Critique als widerspruchsfreie
     Zustandsmaschine modellieren.

5. **Releasezustand sichern — offen**
   - 170 Worktree-Einträge nach Herkunft und Zweck inventarisieren.
   - Fremde Änderungen nicht überschreiben.
   - Kohärente Changesets committen, Remote-Branch neu anlegen und CI
     ausführen.
   - Image, Commit und Konfiguration eindeutig miteinander verknüpfen.

### P1 — Laufzeit, Ressourcen und Qualität

6. **Dynamische Kontextgrößen — teilweise erledigt**
   - Kurze Requests mit 16k/32k planen.
   - 262k nur bei belegtem Long-Context-Bedarf einsetzen.

7. **Interne Tokenbudgets propagieren — erledigt und wirksam validiert**
   - Client- und Templatebudget auf Planner, Expert, Judge und
     Self-Critique aufteilen.
   - Harte maximale Zwischenantwortgrößen definieren.

8. **Planner verkleinern und warm halten — teilweise erledigt**
   - Kleines dediziertes Planner-Modell verwenden.
   - Planner ist auf N04-RGTX entkoppelt; Judge und Expert teilen für das
     Single-GPU-Template bewusst dasselbe warme Modell.
   - Warmhaltung an reale Last und VRAM-Grenzen koppeln.

9. **Cache und Retrieval entlasten — Cache erledigt, Retrieval offen**
   - Bei `no_cache=true` vor jeder L1-Abfrage zurückkehren.
   - GraphRAG nur oberhalb einer Relevanz- und Provenienzschwelle
     einspeisen.
   - Irrelevanter Kontext darf `aux_context` nicht setzen.

10. **Prompt-/Konfigurationsdrift entfernen — offen**
    - Modellnamen aus der tatsächlichen Konfiguration generieren.
    - Veraltete Expertentabellen aus Planner-/Judge-Prompts entfernen.
    - Policy-Diversität klar von Modelldiversität unterscheiden.

11. **Timeout-Telemetrie aggregieren — erledigt und wirksam validiert**
    - Abgeschlossene Stage-Audits in die finale Usage übernehmen.
    - Reale Tokens, Latenzen, Complexity, Cynefin, Trust und
      Expert-Domains auch bei Timeout persistieren.
    - Native Aufrufe ebenfalls mit Latenz und AI-I/O-Audit erfassen.

12. **Native Antworten deterministisch prüfen — offen**
    - Verfügbare MCP-Werkzeuge für Mathematik, Datum und Einheiten wirklich
      ausführen.
    - Behauptete Prüfnachweise gegen Toolergebnisse validieren.

13. **Betriebsfehler beheben — offen**
    - Airflow-Flower-Kommando an die installierte Airflow-Version anpassen.
    - NiFi-CA beziehungsweise Zertifikatskette vertrauenswürdig
      konfigurieren.
    - OpenSearch-Heap und Containerlimit aufeinander abstimmen.
    - Ursache des vollständig belegten Swap prüfen.

### P2 — Wartbarkeit und Betriebsreife

14. Admin- und Nebenservices mit fachlichen Healthchecks ausstatten.
15. Authentik-Profil vollständig konfigurieren oder ausdrücklich als nicht
    installiert dokumentieren.
16. `langchain-community` durch gepflegte Einzelintegrationen ersetzen.
17. Graph-Retrieval-Telemetrie initialisieren, bevor Graph Decay von Dry-Run
    auf Schreibbetrieb umgestellt wird.
18. Benutzerübergreifende Template-Anzeigen eindeutig machen und explizite
    Grants einführen.
19. Mutable `latest`-Tags in betriebsrelevanten Nebenservices durch geprüfte
    Versionen beziehungsweise Digests ersetzen.

## Abnahmestand für die nächste Produktionsfreigabe

### Expert-Template-Funktion

- **Teilweise erfüllt:** identischer TASK-37-Prompt ein Mal kalt und vier
  Mal warm erfolgreich; drei unabhängige Kaltläufe fehlen.
- **Erfüllt:** alle fachlichen Felder und alle sieben automatisierten
  Checks korrekt.
- **Erfüllt:** gültiges, exakt sechsfeldriges JSON.
- **Erfüllt:** jede geplante Precision-Aufgabe besitzt ein ausgeführtes
  MCP-Werkzeug.
- **Erfüllt:** kein still verlorener Task; terminales Ledger und Quality
  Gate.
- **Erfüllt:** alle Judge-Aufrufe teilen dieselbe absolute Deadline.
- **Erfüllt für die Fünferprobe:** höchste gemessene Template-Latenz
  157,472 Sekunden und damit unter 300 Sekunden. Eine belastbare
  statistische P95-Aussage benötigt mehr unabhängige Kaltläufe.

### Fehlerpfade

- **Erfüllt durch Contract-Tests:** ungültiger Planner-Plan wird eng
  repariert oder explizit abgelehnt.
- **Erfüllt durch Ledger-/Quality-Gate-Tests:** MCP-Ausfall kann nicht
  still als erledigt verschwinden.
- **Erfüllt durch Deadline-/Candidate-Tests:** knappe Deadline rettet nur
  einen zulässigen vollständigen Kandidaten, sonst eindeutiger Fehler.
- **Erfüllt im E2E:** Active Requests, temporäre Keys und Audits waren nach
  Erfolg beziehungsweise Timeout terminal bereinigt.

### Telemetrie

- **Erfüllt:** finale API-Usage entspricht exakt der Summe der
  Stage-Audits.
- **Erfüllt in Code/Tests:** Timeout-/Fehlerpfade aggregieren Usage und
  Request-Snapshot.
- **Erfüllt:** Native und orchestrierte Pfade besitzen Latenz- und
  AI-I/O-Daten.

### Deployment und Release

- Sauberer oder vollständig erklärter Working Tree.
- Remote-Branch und reproduzierbarer Commit vorhanden.
- CI mit vollständigen Tests, Compile-/Importprüfung, `pip check`,
  Compose-Validierung und Diff-Check grün.
- Neu gebautes Image eindeutig dem Commit zugeordnet.
- `/ready` vollständig positiv.
- Relevante Container ohne Restart-Schleifen.
- Rollback auf die vorherige freigegebene Version praktisch getestet.

## Freigabeempfehlung

| Nutzungsart | Empfehlung |
|---|---|
| Health-, Monitoring- und Adminbetrieb | Freigabefähig mit ergänztem Admin-Healthcheck |
| MCP-Werkzeuge direkt | Freigabefähig unter bestehenden Auth-/Toolverträgen |
| Einfacher konservativer `moe-auto`-Fast-Path | Freigabefähig mit laufendem Qualitätsmonitoring |
| Native Modell-API | Bedingt freigabefähig; keine ungeprüfte Faktengarantie |
| Validierter Mixed-Precision-/Code-Workflow | Bedingt freigabefähig mit Monitoring; Kaltstart bleibt hoch |
| Breite unbekannte Expert-Templates | Beta/Canary, bis Planner ohne enge Recovery zuverlässig arbeitet |
| NiFi-abhängige Enterprise-Flows | Nicht freigeben, bis TLS-Vertrauen hergestellt ist |
| Airflow-Flower-Monitoring | Nicht betriebsfähig, bis Restart-Ursache behoben ist |
| Release aus aktuellem Git-Zustand | Nicht freigeben, bis Worktree und Remote-Branch konsolidiert sind |

## Referenzen

- `AGENT_LASTENHEFT.md`, TASK-35: End-to-End-Vollständigkeit und
  Definition-vs.-Nutzung-Audit.
- `AGENT_LASTENHEFT.md`, TASK-36: umgesetzte Restbaustellen und
  Wirksamkeitsnachweis.
- `AGENT_LASTENHEFT.md`, TASK-37: nativer Qwen3.6-/horndev-Template-
  Vergleich.
- `AGENT_LASTENHEFT.md`, TASK-38: priorisierter Remediation-Plan.
- `agent_status/codex-cli.md`: Ausführungs- und Abnahmenachweise.
- `docs/system/status.md`: automatisch aktualisierter Container- und
  Wissensstatus.
