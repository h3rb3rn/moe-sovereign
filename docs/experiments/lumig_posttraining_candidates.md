# LUMI-G Nachtraining-Kandidaten

**Status:** in progress
**Quelle:** isoliertes Knowledgebase-Wirksamkeitsexperiment
(`docs/experiments/graphrag_efficacy_ringbuffer.md`), `sci-sysprog-01-lockfree-ringbuffer`

## Zweck

Diese Liste sammelt Befunde aus dem laufenden GraphRAG-Wirksamkeitsexperiment,
die sich **nicht** durch weiteren Wissensimport in den Knowledgebase-Graphen
lösen lassen — d.h. Fälle, in denen ein Fakt nachweislich im Prompt sichtbar
war (verifiziert via `ai_io_audit_log`) und trotzdem falsch angewendet wurde.
Das unterscheidet einen echten **Wissens-Gap** (löst sich durch Import) von
einer **Anwendungsgrenze** (löst sich nur durch Training/Finetuning des
Modells selbst).

Aufnahmekriterium: ein Fakt/Fähigkeitsbereich gilt erst als Kandidat, wenn er
**mindestens zweimal unabhängig** trotz bestätigt sichtbarem Wissen verletzt
wurde. Einmalige Vorkommnisse werden zunächst als Wissensimport-Kandidat
behandelt (siehe `graphrag_efficacy_ringbuffer.md`).

## Kandidaten

### 1. Acquire-Release Memory-Ordering-Reasoning (bestätigt, hohe Konfidenz)

**Muster:** Das Modell platziert atomare Stores/Loads mit der falschen
Ordering-Stärke relativ zur eigentlichen Datenveröffentlichung — typischerweise
zu schwach (relaxed statt release) beim Publizieren, oder fehlend (kein
acquire) beim Konsumieren — obwohl der exakte Fakt ("producer must write data
before the release store that publishes it", `curated_set:
systems_programming_v2_producer_write_order`, seit Runde 2 im Graphen)
nachweislich im Prompt vorhanden war.

**Beobachtungen (unabhängig, mit Wissen nachweislich sichtbar):**
- Lauf 3 (vor GraphRAG-Retrieval-Fix; Sichtbarkeit zu diesem Zeitpunkt nicht
  einzeln verifiziert, aber Fakt bereits importiert): "multiple producers
  read and store tail without CAS"
- Lauf 6 / 17. Versuch (nach dem Retrieval-Fix, Sichtbarkeit bestätigt):
  "the push path publishes the slot after a relaxed CAS, allowing the
  consumer to observe an advanced tail before the payload is written" +
  "lacks an acquire load of tail in the consumer"
- Lauf 7 / 19. Versuch (nach dem Retrieval-Fix, Sichtbarkeit bestätigt,
  identischer Wissensstand wie Versuch 17 plus Runde 7): "producers write
  the payload after the releasing CAS, so the release does not order the
  data write and consumers can read uninitialized/None slots" — **fünfte
  unabhängige Beobachtung**, Score/Judge exakt identisch zu Versuch 17
  (5.8/3.0) trotz zwei zusätzlicher, unabhängiger Wissensrunden dazwischen.
  Score-Plateau bei gleichbleibender Ordering-Verletzung ist ein starkes
  Signal, dass weitere periphere Wissensimporte den Gesamt-Score nicht mehr
  spürbar heben werden, solange diese eine Kernfähigkeit nicht adressiert
  wird (siehe Trainingsempfehlung unten und die separate Compiler/
  Sanitizer-in-the-Loop-Pro/Contra-Analyse im Session-Protokoll).

**Einordnung:** Memory-Ordering-Reasoning für Lock-free-Datenstrukturen gilt
auch für erfahrene Systemprogrammierer als eine der schwierigsten Teilgebiete
nebenläufiger Programmierung. Die Wiederholung trotz explizit im Kontext
vorhandenem Fakt spricht für eine echte Modell-Grenze, nicht für einen
Wissens-Gap.

**Trainingsempfehlung:** Gezielte SFT/DPO-Beispiele mit Fokus auf
Producer/Consumer-Ordering-Paare (release-Store erst nach vollständigem
Schreiben der Payload; korrespondierender acquire-Load vor jedem Lesen einer
über eine atomare Variable veröffentlichten Struktur), idealerweise mit
einem Compiler/Sanitizer-verifizierten Reward-Signal (z.B. ThreadSanitizer /
Loom-Modellchecking für Rust) statt reinem LLM-Judge-Feedback.

**Update nach Einführung von `rust_compile_check` (Phase 1, reines
Typ-/Borrow-Checking, siehe agent_status/claude-code.md
FEATURE-rust-compile-check-precision-tool):** Läufe 21 und 22 bestätigen
die Ordering-Verletzung ein 6. und 7. Mal unabhängig — und zeigen zusätzlich
**strukturell**, warum Phase 1 dieses Problem nicht lösen kann: eine Data
Race durch falsche Memory-Ordering ist kein Compile-Fehler (das Programm
kompiliert einwandfrei), sondern ein Laufzeit-/Nebenläufigkeits-Defekt, der
nur durch tatsächliche Ausführung unter einem Concurrency-Checker (Miri,
ThreadSanitizer, Loom) erkennbar wäre — explizit Phase 2, bisher nicht
umgesetzt. Das ist damit der klarste Einzelbeleg in dieser gesamten
Session dafür, dass für dieses spezifische Fähigkeitsdefizit entweder (a)
Phase 2 des Compiler-in-the-Loop-Features nötig ist, oder (b) Finetuning —
reines Wissens-Hinzufügen im Graphen UND reine Compile-Checks reichen beide
nachweislich nicht aus.

## Noch nicht bestätigte Kandidaten (erst 1 Beobachtung, weiter beobachten)

### 5. Falsches Argumentschema bei `decimal_finance` (1 Beobachtung)

**Muster:** Der Planner erkennt korrekt, dass `precision_tools`/`decimal_finance`
für eine exakte Rechenaufgabe nötig ist, ruft das Tool aber mit erfundenen,
plausibel klingenden Argumentnamen auf (`"precision": "high", "operations":
["multiply", "add", "power"]`) statt der tatsächlich geforderten
(`operation`, `operands`, `currency`, `scale`, `rounding`).

**Beobachtung:** großer Scientific-Benchmark, Runde 1,
`sci-precision-02-ast-financial-arithmetic`, Bedingung `ablation_no_graphrag`,
2026-08-24 22:58 UTC+2. Der automatische Contract-Reparatur-Retry
(`missing_mcp_args`) hat die Lücke korrekt erkannt, aber die
Korrektur-Antwort des Planners war selbst kein valides JSON mehr
(`PlannerContractError` → HTTP 500).

**Einordnung:** anders als die Themenfabrikation — hier hat der Planner das
richtige Tool und die richtige Kategorie erkannt, nur das konkrete
Argumentschema nicht. Möglicher (noch nicht umgesetzter, nicht ohne
Rücksprache angegangener) Ansatz: ein konkretes `decimal_finance`-Beispiel
direkt neben dem bestehenden `CHAINED CALCULATIONS`-Beispiel im
Planner-Prompt (`graph/planner.py`) ergänzen, das die exakten Feldnamen
zeigt.

**Kontext — Task-7-Runde-1-Gesamtbild:** Alle 4 Bedingungen dieser Runde
sind gescheitert, GAP 3 (`$task_result`-Verkettung) wurde dadurch **nicht
erreicht** — keine einzige Bedingung kam so weit, überhaupt eine zweite,
verkettete `decimal_finance`-Task aufzurufen:
- `compound_ai`: komplette Themenfabrikation ("memory.md priority rules")
- `compound_ai_debate`: Det=0.0 trotz Judge 8.5 — vermutlich thematisch
  plausibel klingende, aber falsche/unvollständige Antwort ohne
  `decimal_finance`
- `ablation_no_graphrag`: `decimal_finance` erkannt, aber falsches
  Argumentschema (dieser Kandidat) → 500
- `native_baseline`: 1,2/10, Det=0.0 (erwartbar — kein Tool-Zugriff, exakte
  Mehrjahres-Rechnung ohne Werkzeug)

GAP 3 bleibt damit nach Runde 1 weiterhin unbewiesen im echten
Pipeline-Kontext. Weitere Runden (2-5) bieten neue Gelegenheiten.

*(wird laufend aktualisiert, sobald sich ein Muster wiederholt)*

**Nachtrag nach Fix-Deploy, zweiter Runde-1-Durchlauf (2026-08-25,
10:11-10:52 CEST):** Nachdem die drei Session-Fixes (Few-Shot-Filter,
Judge/Experten-Reload, `$task_result`-Verkettung) tatsächlich deployed
waren (vorher versehentlich nie live, siehe `agent_status/claude-code.md`
Eintrag `~05:50Z`), liefen alle 4 Bedingungen für Task 6 erneut — diesmal
ohne Themenfabrikation, aber GAP 3 wurde wieder nicht demonstriert, aus
einem dritten, neuen Grund:

- `compound_ai` (Score 3.0, Det=0.0): Planner wählte Kategorie
  `reasoning` statt `precision_tools` — der Experte soll die gesamte
  Rechnung selbst in Prosa durchführen, kein MCP-Tool-Zugriff.
- `compound_ai_debate` (Score 3.6, Det=0.0): kein eigener Planner-Aufruf —
  `Planner cache hit (Valkey) — skipping LLM` übernahm 1:1 denselben
  `reasoning`-Plan von `compound_ai`. **Neuer Befund:** der Planner-Cache
  ist offenbar promptbasiert und conditionsübergreifend geteilt (nicht pro
  Template/Bedingung isoliert) — sobald eine Bedingung eine
  Kategorie-Entscheidung trifft, erben andere Bedingungen desselben
  Tasks/Runde denselben (ggf. suboptimalen) Plan, ohne selbst eine
  Chance auf `precision_tools`-Routing zu bekommen.
- `ablation_no_graphrag` (Score 3.3, Det=0.0): **kein Cache-Hit**, eigener
  Planner-Lauf, wählte diesmal korrekt `category: "precision_tools"` und
  dispatchte tatsächlich ein MCP-Tool (`calculate`,
  `840 * 8760 / 1000 = 7358.4`) — exakt der erwartete `annual_mwh`-Wert,
  live per Container-Log verifiziert (`MCP: ... 840 * 8760 / 1000 =
  7358.4`). **Aber:** der Plan enthielt nur diese EINE atomare
  Berechnung, keine Verkettung der restlichen Schritte (Tarifeskalation,
  Jahreskosten, kumulierte Summe, CO2) über `$task_result`. Trust-Score
  bewertete das trotzdem als "100% deterministic task coverage" (0.870,
  PROCEED) und übersprang sogar den Thinking-Schritt
  ("complete deterministic evidence and <=1 non-precision task") — die
  fehlenden ~9 weiteren Rechenschritte wurden der Merger-LLM zur freien
  Prosa-Schätzung überlassen, was den Det=0.0 erklärt.
- `native_baseline` (Score 1.2, Det=0.0): unverändert aus Vorlauf
  übernommen (kein Planner/Pipeline, von keinem Fix betroffen).

**Einordnung:** Die `$task_result`-Infrastruktur selbst ist nachweislich
erreichbar und funktionsfähig (der `calculate`-Dispatch beweist das) — das
eigentliche verbleibende Problem ist, dass der 4B-Planner mehrstufige
numerische Aufgaben systematisch UNTER-dekomponiert (1 Task statt der
nötigen ~10 verketteten) und die Trust-Score-Bewertung diese
Unter-Deckung nicht erkennt, solange die wenigen geplanten Tasks selbst
erfolgreich ausgeführt wurden. Das ist ein neuer, eigenständiger
Kandidat für Nachtraining (Planner-Zerlegungstiefe bei mehrstufigen
Rechenaufgaben) UND ein möglicher kleiner Infra-Punkt (Trust-Score-
Deterministic-Coverage-Metrik prüft nur "sind die geplanten Tasks
erledigt", nicht "deckt der Plan die tatsächlich im Prompt geforderten
Werte ab") — letzterer nicht in dieser Session umgesetzt, da außerhalb
des ursprünglich vereinbarten GAP-3-Scopes und ohne Rücksprache.

## Explizit ausgeschlossen (als Wissens-Gap identifiziert und importiert, kein Finetuning-Kandidat)

- Rust `thread::spawn` `'static`-Bound (Runde 4) — nach Import nicht mehr
  verletzt (Lauf 6/17. Versuch)
- `UnsafeCell` für `&self`-Mutation (Runde 4) — nach Import nicht mehr in der
  Grundform verletzt (Lauf 6/17. Versuch), aber siehe neue, tiefere
  Anwendung unten
- Rust `usize`-Vorzeichenlosigkeit (Runde 4)
- `thread::spawn`-Move-Semantik + `Arc` (Runde 5)
- `JoinHandle::join()`-Rückgabetyp (Runde 5)
- Drop-Trait für teilinitialisierte Custom-Container (Runde 6) — Import
  erfolgt, Wirksamkeit noch nicht getestet (Lauf 7 läuft)
- `UnsafeCell`-Aliasing-Regeln (&T vs. &mut) (Runde 7) — Import erfolgt,
  Wirksamkeit noch nicht getestet
- `unsafe impl Sync`-Korrektheitsanforderungen (Runde 7) — Import erfolgt,
  Wirksamkeit noch nicht getestet

## Separater, unabhängiger Befund (kein Wissens-Gap, kein reines Ordering-Problem)

### 2. Planner-Task-Fabrikation (bestätigt, hohe Konfidenz)

Der Planner (`moe-sovereign-student:4b`, LUMI-G-distilliert) erfindet
wiederholt thematisch fremde Zusatz-Tasks, die nachweislich nicht im
Eingabe-Prompt vorkommen (verifiziert durch Volltextsuche im jeweils
mehrere zehn KB großen Planner-Prompt selbst):

- "ping" → Task über "DNS, HTTP, gRPC, GraphQL discovery and routing"
- "What is 2+2?" (Kontext: Scientific-Benchmark-Template) → Task über
  "DNS, HTTP, gRPC, REST API calls"
- Reiner Rust-Ringbuffer-Prompt (48 KB Kontext) → zusätzliche, komplett
  unabhängige Tasks zu Firewall-Regeln und OAuth/API-Key-Auth-Kaskade
- **Neu, höherer Schweregrad (großer Scientific-Benchmark, Runde 1,
  `sci-precision-02-ast-financial-arithmetic`, Bedingung `compound_ai`,
  2026-08-24 11:16 UTC+2):** erstmals eine **vollständige Themenersetzung**
  statt einer zusätzlichen Task neben der echten. Der Prompt (Rechenaufgabe:
  Energiekosten + Tarifeskalation über 3 Jahre + CO2-Emissionen) wurde
  komplett ignoriert; der Plan bestand ausschließlich aus erfundenen,
  wechselseitig unabhängigen Themen ("Datacenter PUE/PCE Compliance Audit",
  "Security Audit/Risk Assessment", "GDPR/CCPA/PIPEDA Legal Compliance",
  "Data Governance Inventory") — kein einziges Element hatte Bezug zum
  echten Input. Kein Cache-Hit (verifiziert: echter LLM-Aufruf, kein
  `Planner cache hit`-Log). Konsequenz: `decimal_finance`/die neue
  `$task_result`-Verkettung (GAP 3) wurde durch diesen Lauf nicht erreicht.

**Auffälligkeit:** Alle bisherigen Fabrikationen drehen sich um Netzwerk-/
Security-/Compliance-Themen — kein Zufallsmuster, deutet auf eine
Trainingsdaten-Verzerrung der Distillation hin. Die neueste Beobachtung
zeigt zusätzlich, dass der Schweregrad von "zusätzliche Fake-Task" bis zu
"komplette Themenersetzung, echter Prompt bleibt unbearbeitet" reicht.

**Root-Cause-Untersuchung, ein Teilfaktor gefunden und gefixt
(2026-08-24):** Ein echter, verifizierter Code-Bug wurde identifiziert und
behoben — `get_few_shot_context()` (`self_correction.py`) injizierte
ungefiltert die wörtlichen Fehltexte früherer Self-Correction-Einträge aus
ALLEN Experten-Kategorien in jeden Planner-Prompt (siehe
`agent_status/claude-code.md`, Fix `0d0f72e9`,
`fix/few-shot-context-topic-contamination`). Direkt nachgewiesen: die
"Apex-Central"/"Directive 2024-B"-Fabrikationen lagen als abrufbare
Few-Shot-Einträge im Store. Nach dem Fix liefert `get_few_shot_context()`
für den betroffenen Prompt nachweislich keinen Kontext mehr.

**Aber:** ein Live-Replay NACH diesem Fix produzierte weiterhin eine
komplette Themenersetzung (diesmal: "grep print()-Aufrufe /
karpathy-compliance"). Drei weitere Live-Injektionsquellen wurden für
diesen Fall explizit ausgeschlossen (`moe:planner_success` leer,
`semantic_router_node` kein Treffer, `get_active_advice()` liefert 0
Regeln). Die verbleibende Fabrikation ist keinem im Code auffindbaren
Retrieval-Mechanismus zuzuordnen. **Einordnung bleibt daher bestehen:**
primär Trainingsdaten-/Distillations-Artefakt des 4B-Planners, der
Few-Shot-Fix reduziert einen realen, aber nicht den alleinigen
Kontaminationsvektor.

**Trainingsempfehlung:** Stärkere Grounding-Constraint im Planner-Training
(z.B. explizites Negativbeispiel-Training gegen Tasks ohne lexikalischen/
semantischen Bezug zum tatsächlichen Nutzer-Input), oder ein Reward-Signal,
das Tasks ohne Rückbezug zum Input bestraft.

**Weitere bestätigte Instanz, neuer Schweregrad (Runde 1, Task 9
`sci-governance-01-technical-sovereignty`, Bedingung `compound_ai`,
2026-08-25 01:38 CEST):** Prompt war ein Hospital-Compound-AI-Architektur-
Audit (Data Protection by Design). Planner erfand stattdessen eine einzelne
Task über "DHS Tier 3 Small Entity RCE verification claims... session_9b...
Diga/Feedzup/DNS log gaps" — Begriffe ohne jeden Bezug zum Prompt oder zum
Datensatz (Volltextsuche negativ). Passt zum bekannten Netzwerk-/Security-/
Compliance-Bias. Neu: die fabrizierte Task war so unbrauchbar, dass der
Experten-Knoten **kein einziges Ergebnis** produzierte (kein `expert_call`-
Log zwischen `[NODE] EXPERTS` und `[NODE] MERGER`, Trust-Score sofort 0.0,
HARD-BLOCK "No expert results and no retrieval context"). 2 Self-Critique-
Runden hoben den Score nur auf 0.1 (weiterhin < 0.3-Schwelle); der
anschließende Critic-Node korrigierte den Text und versuchte den
trust_verdict-Upgrade auf `PROCEED_WITH_ASSUMPTION` (siehe
`graph/synthesis.py` Zeile ~2387-2390). Der unabhängige
`incomplete_plan_tasks()`-Check in `evaluate_quality_gate()`
(`services/quality_gate.py` Zeile ~209, läuft VOR dem trust_verdict-Check)
blockte trotzdem korrekt (`quality_gate | blocked`, verifiziert via
Redis-Trace `moe:active:{chat_id}:trace`) — die ursprüngliche Plan-Task
task-1 wurde nie real ausgeführt, das zählt unabhängig vom
Hallucination-Check-Upgrade. **Kein Infra-Bug:** das Fail-Closed-Verhalten
(Critic-Upgrade kann eine nie ausgeführte Plan-Task nicht nachträglich als
"erledigt" umdeklarieren) ist korrekt und AGENTS.md-konform. Der
Benchmark-Harness verwirft dieses Ergebnis bereits zurecht aus dem
Checkpoint (`_result_is_valid()`: `total_tokens == 0` → invalid, wird bei
Resume neu versucht). Keine Code-Änderung vorgenommen — bestätigt nur die
bestehende Einordnung als Trainingsdaten-Artefakt, diesmal mit sichtbarem
Kaskadeneffekt bis zum kompletten Experten-Ausfall.

### 4. Planner produziert fehlerhaft verschachteltes/escaptes JSON bei komplexer struktureller Persistierung (bestätigt, 2 unabhängige Beobachtungen)

**Muster:** Bei der Anweisung, eine nicht-triviale, mehrteilige Struktur
(mehrere Entitäten + Kanten, oder eine mehrteilige Regel-/Direktiven-Hierarchie)
als Task zu persistieren, produziert `moe-sovereign-student:4b` über 3
Versuche hinweg kein valides Task-Array — stattdessen tief
verschachteltes/mehrfach escaptes JSON, das an `CONTRACT PLAN VALID: False`
scheitert (`PlannerContractError`, HTTP 500 an den Client).

**Beobachtungen (unabhängig, beide großer Scientific-Benchmark, Runde 1):**
- `sci-graphrag-01-topology-cascade` Turn 1 (fiktive Microservice-Topologie
  mit 4 Entitäten + Abhängigkeiten), Bedingung `compound_ai`, 2026-08-23
  20:54 UTC+2.
- `sci-graphrag-02-paraconsistent-reconciliation` (mehrteilige
  "Sovereign Executive Amendment"-Direktivenhierarchie mit
  Wirksamkeits-/Geltungsbereichs-Feldern), Bedingung `compound_ai_debate`,
  2026-08-23 23:18 UTC+2. Anderer Task, anderer Inhalt, identisches
  Fehlerbild.

**Einordnung:** mechanisch verschieden von Kandidat 2
(Planner-Task-Fabrikation, thematische Erfindung) und vom bereits
behobenen SCHEMA_OUTPUT-Bug (leeres `[]` bei langem Prompt) — hier wird
nicht-leeres, aber syntaktisch ungültiges JSON erzeugt, spezifisch
getriggert durch strukturelle Komplexität der zu persistierenden Daten
(mehrere verschachtelte Entitäten/Regeln in einer Anweisung). Bisher nur
bei `compounding_knowledge`-Tasks beobachtet — auffällig, aber mit n=2
noch nicht als kategoriespezifisch gesichert. Kein Wissens-Gap: der
gesamte Inhalt steht bereits vollständig im Prompt selbst; ein Import in
den Knowledge Graph wäre Data-Leakage des Testfalls, kein legitimer
Wissensimport.

**Trainingsempfehlung:** SFT-Beispiele mit mehrteiligen, strukturell
komplexen Persistierungs-Anweisungen (mehrere Entitäten/Regeln in einem
Turn), Ziel: valides, flach kodiertes JSON-Task-Array statt
verschachtelter/escapter String-Rekursion.

## Verwandter, aber separat zu behandelnder Befund

### 3. Judge-Format-Compliance (bestätigt, hohe Konfidenz, aber Prompt-Engineering-Fläche zuerst ausgeschöpft)

Der Judge (`sovereign-judge:27b`) verletzt gelegentlich das vorgeschriebene
Antwortformat (entweder bare `CONFIRMED` oder direkte Korrektur ohne
Präambel) und liefert stattdessen eine Deliberations-/Meta-Kommentar-Antwort.
6 unabhängig beobachtete Variantion dieses Musters in dieser Session, über
mehrere verschiedene Prompt-Stellen hinweg (Merger-Critic, Hallucination-
Check-Critic). Bisher ausschließlich reaktiv per Regex abgefangen
(`_CRITIC_PREAMBLE_RE`), nie strukturell behoben — jede Instanz kostet eine
volle Judge-Generierung (mehrere Minuten) ohne Nutzen.

**Trainingsempfehlung:** SFT/DPO-Pass mit explizitem Format-Compliance-Reward
für den Judge, spezifisch für Critic-artige Prompts mit dem
CONFIRMED/Direct-Correction-Kontrakt.
