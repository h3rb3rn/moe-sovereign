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

*(wird laufend aktualisiert, sobald sich ein Muster wiederholt)*

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

**Auffälligkeit:** Alle drei Fabrikationen drehen sich um Netzwerk-/
Security-Themen — kein Zufallsmuster, deutet auf eine Trainingsdaten-
Verzerrung der Distillation hin.

**Trainingsempfehlung:** Stärkere Grounding-Constraint im Planner-Training
(z.B. explizites Negativbeispiel-Training gegen Tasks ohne lexikalischen/
semantischen Bezug zum tatsächlichen Nutzer-Input), oder ein Reward-Signal,
das Tasks ohne Rückbezug zum Input bestraft.

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
