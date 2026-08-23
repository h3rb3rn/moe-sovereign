# Knowledgebase-Wirksamkeitsnachweis: sci-sysprog-01-lockfree-ringbuffer

**Status:** in progress
**Started:** 2026-08-20

## These

Wenn ein SLM vollständig das notwendige Wissen (Strategien, Abhängigkeiten,
Grundlagen, Konventionen) zum Lösen einer Aufgabe zur Verfügung hat, ist es in
der Lage, die Aufgabe zu 100% zu erledigen.

## Methodik

Isolierter, iterativer Lauf ausschließlich auf `compound_ai` (GraphRAG aktiv)
für die Aufgabe `sci-sysprog-01-lockfree-ringbuffer`. Kein Vergleich gegen
andere Conditions in diesem Experiment — es geht nicht um "hilft GraphRAG
allgemein", sondern um die enger gefasste Frage "reicht vollständiges,
korrektes Fachwissen aus, damit das SLM die Aufgabe fehlerfrei löst".

Pro Lauf:
1. Benchmark auf genau dieser Aufgabe/Condition starten, überwachen.
2. Bei Infra-/Script-Fehlern: analysieren, fixen, neu starten (siehe
   [[feedback_benchmark_infra_error_policy]] — zählt nicht als Ergebnis).
3. Bei fehlerfreiem Durchlauf: Judge-Begründung auswerten, fehlendes
   Wissen X identifizieren.
4. Externe, autoritative Quelle(n) zu X recherchieren, **allgemein**
   formulierten Fakt (kein bug-spezifischer Punkt-Fix) mit Quellenangabe in
   `graph_rag/curated/systems_programming_reference.cypher` ergänzen und
   importieren.
5. Nächster Lauf.

**Abbruchkriterien:**
- **Erfolg:** `judge_verdict == "EXCELLENT"` und `judge_score >= 9.5`.
- **Kein-Cheat-Grenze erreicht:** Der Judge kritisiert in einer Runde exakt
  das, was im Graphen bereits als abrufbarer Fakt vorliegt (verifiziert über
  denselben Retrieval-Pfad wie beim Import) — dann ist es kein Wissens-Gap
  mehr, sondern ein Anwendungsproblem des Modells. An diesem Punkt wird
  gestoppt und so berichtet, nicht weiter mit neuem Wissen versucht zu
  kompensieren.

**Reproduktion:** Jeder Cypher-Import ist einzeln in
`graph_rag/curated/systems_programming_reference.cypher` dokumentiert
(Rundenmarkierung, Quellen, `curated_set`-Tag pro Runde für gezieltes
Rollback). Jeder Lauf wird mit
`MOE_BENCHMARK_TASK_IDS=sci-sysprog-01-lockfree-ringbuffer
MOE_BENCHMARK_CONDITIONS=compound_ai MOE_BENCHMARK_NUM_ROUNDS=1
python3 benchmarks/run_scientific_benchmark.py --fresh` ausgeführt.

## Lauf-Protokoll

### Lauf 1 (vor Beginn dieses isolierten Experiments, aus der vollen Suite übernommen)

- Wissensstand: `systems_programming_v1` (9 allgemeine Fakten zu Lock-free-
  Concurrency + eBPF/XDP, importiert unabhängig von einem einzelnen
  Fehlerbefund — siehe Cypher-Datei, Sektion `ROUND 1`/`v1`).
- Ergebnis: Score 4.9/10, Det 10.0, Judge 1.5, 1372 Tokens.
- Judge-Befund: *"writes the buffer after the release CAS, allowing a
  consumer to observe an advanced tail and read an unwritten slot... also
  fails to provide the required cross-thread safety guarantees and has
  underflow risk in the fullness check."* — Padding/Teststruktur laut Judge
  vorhanden ("despite superficial padding and test structure"), aber
  Producer schreibt Daten NACH statt VOR dem Release-Store.
- Identifizierter Wissens-Gap: Producer-seitiges Gegenstück zu
  "consumer read-before-release ordering" (war in v1 nur für die
  Consumer-Seite abgedeckt) fehlte.
- Import: `producer write-before-release ordering`
  (`curated_set: systems_programming_v2_producer_write_order`), Quellen:
  moodycamel.com "A Fast Lock-Free Queue for C++", cppreference.com
  acquire/release-Semantik.

### Lauf 2

- Wissensstand: + `producer write-before-release ordering`
  (`systems_programming_v2_producer_write_order`).
- Ergebnis: Score 5.8/10, Det 10.0, Judge 3.0, 2043 Tokens.
- Judge-Befund: *"pop can return None when a claimed slot is not yet marked
  full, causing the test consumer to exit early and lose messages, and
  producer/consumer can race on buffer/slot state because head advancement is
  relaxed and not properly synchronized with slot reuse. Although it meets
  padding and power-of-two requirements, the claimed lock-free MPSC
  correctness and zero-loss test are not satisfied."*
- Fortschritt: Padding/Power-of-Two laut Judge jetzt korrekt — die
  vorherigen zwei Imports (False-Sharing-Padding, Producer-write-before-
  release) wirken sichtbar. Neuer Fehlertyp: (a) `relaxed` statt
  `release`/`acquire` bei der Head-Index-Fortschreibung, (b) Consumer gibt
  bei einem reservierten-aber-noch-nicht-vollständig-geschriebenen Slot
  vorzeitig auf (`None`) statt zu warten/erneut zu prüfen.
- Identifizierter Wissens-Gap: korrekte Memory-Ordering für Head/Tail-Index-
  Updates (warum `relaxed` hier unzureichend ist) + korrektes
  Busy-Wait/Retry-Verhalten beim Konsumieren eines reservierten Slots statt
  early-exit.

### Lauf 3

- Import: `relaxed ordering scope for queue indices` + `consumer spin-wait on
  claimed-but-unpublished slot`
  (`curated_set: systems_programming_v3_relaxed_scope_and_spin_wait`),
  Quellen: davekilian.com "Making Sense of Acquire-Release Semantics",
  cppreference.com `std::memory_order`, book-of-gehn.github.io "Lock-Free
  Queue Part II".
- **Erster Durchlauf ungültig (Infra-Bug, zählt nicht als Ergebnis):** Score
  3.0/10 (Det 6.0, Judge 1.0) — aber `final_response` bestand komplett aus
  Critic-Meta-Kommentar ("The answer contains a critical technical error in
  its reasoning regarding memory orderings...") statt einer echten
  korrigierten Antwort. Gleicher Bug-Typ wie der bereits gefixte
  Critic-Node-Bug, nur eine dritte, bisher nicht abgedeckte Variante: Judge
  beginnt mit der laut Prompt explizit verbotenen Präambel ("The answer
  contains...") und liefert nie die geforderte direkte Korrektur — mein
  bisheriger Guard prüfte nur (a) trailing bare CONFIRMED und (b)
  vollständiges Verschwinden von Code-Markern, was hier nicht griff, weil
  die Kritik Code-Fragmente zitierte. Fix: `_CRITIC_PREAMBLE_RE` erkennt
  jetzt zusätzlich verbotene Präambel-Anfänge. Gegen 6 Fälle verifiziert
  (inkl. exaktem Lauf-3-Text und dem ursprünglichen Session-Bug von ganz zu
  Beginn — beide korrekt erkannt, echte Korrekturen bleiben unangetastet).
  Container neu gebaut (`sha256:4b8adf67...`), neu erstellt, `healthy`.
  Wissensstand unverändert (kein neuer Import nötig), Lauf 3 wird wiederholt.

### Lauf 3 (Wiederholung nach Critic-Preamble-Fix)

- **Zweiter Durchlauf ebenfalls ungültig (Infra-Bug):** Score 4.6/10 (Det 10.0,
  Judge 1.0), `final_response` erneut komplett Critic-Meta-Kommentar: *"The
  provided answer contains a critical logical flaw in the unit test..."*.
  Vierte sprachliche Variante desselben Präambel-Musters — mein Regex prüfte
  nur exakt "the answer"/"the response" am Satzanfang, "the **provided**
  answer" hat ein Zwischenwort und rutschte durch. Watchdog-mtime-Fix hat
  diesmal korrekt funktioniert (kein Fehlalarm mehr, sauberer Abschluss
  erkannt) — das Monitoring-Problem ist behoben, nur die Erkennungsregel war
  noch zu eng.
- Fix: `_CRITIC_PREAMBLE_RE` erlaubt jetzt optionale Zwischenwörter
  ("provided"/"given") vor answer/response/implementation/code, plus eine
  zweite Alternative für "critical/unsupported/incorrect flaw/error/claim"
  am Satzanfang. Gegen 9 Fälle verifiziert (alle 3 bisher beobachteten realen
  Varianten + 2 hypothetische + 4 echte Korrekturen, die nicht fälschlich
  greifen dürfen). Container neu gebaut (`sha256:d00a7d2b...`), healthy.
- Wissensstand unverändert (kein neuer Import — wieder ein Pipeline-Bug,
  kein Wissens-Gap). Lauf 3 wird ein drittes Mal wiederholt.

### Lauf 3 (zweite Wiederholung, nach zweitem Critic-Preamble-Fix)

- **Dritter Durchlauf: kein Pipeline-Bug, sondern ein legitimer, aber für
  unser Experiment nicht auswertbarer Fall.** Score 3.0/10 (Det 0.0, Judge
  exakt 5.0 = UNSCORED_FALLBACK-Muster), `total_tokens=0`. Ursache: Planner
  erzeugte zwei fast identische, redundante `systems_programming`-Tasks; zwei
  Experten lieferten widersprüchliche Implementierungen; Trust-Score fiel
  über 3 Merger-Durchläufe (0.310→0.295→0.278), blieb `BLOCK`;
  Quality-Gate hat die gesamte Antwort korrekt und absichtlich zurückgehalten
  (`trust_score_block`, `services/quality_gate.py:253` — beabsichtigtes
  Fail-closed-Verhalten, kein Bug). Kein Inhalt zu bewerten.
- **Architektur-Erweiterung statt weiterem Wissensimport:** Auf Vorschlag des
  Users wurde stattdessen der `merger_node`-Refinement-Loop erweitert, sodass
  der Judge Experten-Konflikte in JEDER Kategorie arbitrieren und die
  Korrektur an den Experten zurückspielen kann (vorher: nur
  `medical_consult`/`legal_advisor`, und nur protokolliert statt angewendet).
  Details: `agent_status/claude-code.md`, FEATURE-merger-conflict-
  arbitration-refine. Container neu gebaut
  (`sha256:7a22744681d59decdd691bb65073f2a4736176ce9e3d6e8a1940c4ca4817240f`),
  deployed. Da das Duplikat-Task-Muster stochastisch ist, dient dieser
  nächste Lauf gleichzeitig als Live-Integrationstest für die neue Funktion.
- Wissensstand unverändert (kein neuer Import — diesmal weder Pipeline-Bug
  noch Wissens-Gap, sondern eine Architektur-Lücke).
- **Vierter Durchlauf: kein Duplikat-Task-Konflikt mehr** (Planner hat sauber
  geplant), stattdessen ein kleinerer Konflikt in Pseudo-Kategorie "judge"
  (Self-Critique-Runde 1 vs. 2 widersprechen sich) — neue Arbitrierung hat
  korrekt versucht einzugreifen, konnte aber nichts tun (`EXPERTS["judge"]`
  existiert nicht, `_refine_expert_response` liefert `None`), Fallback zu
  `resolve_conflicts_node` griff unverändert korrekt (keine Regression).
  Notiert als kleine spätere Nachbesserung (Pseudo-Kategorie "judge" aus
  Konflikt-Trigger ausschließen), kein Blocker.
- **Aber wieder kein auswertbares Ergebnis:** Score 7.0/10, `judge_verdict =
  UNSCORED_FALLBACK` — der externe Scoring-Judge (nicht die Pipeline)
  scheiterte alle 3 Versuche mit ausführlicher Denk-Vorrede ("We need answer
  user's request... Need analyze deeply"), lief dabei jedes Mal ins
  `num_predict=4096`-Limit, kam nie zur JSON-Antwort (~15 Min verschwendet).
  `final_response` selbst ist echt (3750 Tokens, `turn.ok=True`, sieht nach
  vollständiger Implementierung aus) — nur die Bewertung fehlt.
- Fix: `num_predict` in `judge_evaluation()` (Scoring-Judge, nicht
  Pipeline-Judge) von 4096 auf 8192 erhöht. Reiner Script-Fix, kein
  Container-Rebuild nötig. Vor dem Fix geprüft: Produktions-Judge-Calls
  (`MAX_JUDGE_TOKENS=32768`) waren nie betroffen — reine Unterdimensionierung
  im separaten Benchmark-Skript, kein Symptom-Fix mit Produktions-Lücke.
- **Erstes valides Ergebnis nach dem num_predict-Fix:** Score 4.6/10, Det
  10.0, Judge 1.0, `judge_verdict=FAIL` (echt, kein Fallback mehr!).
  Judge-Befund, zwei unabhängige Probleme:
  1. *"multiple producers read and store tail without CAS"* — das ist
     **exakt der seit Runde 1 im Graphen verfügbare Fakt**
     ("compare-and-swap retry loop"). Nähert sich dem Abbruchkriterium
     (Anwendungsproblem statt Wissens-Gap).
  2. *"the Rust code also cannot compile"* — Mutation von `Vec<Option<T>>`
     durch `&self`, non-`'static`-Referenzen in gespawnten Threads,
     `usize` mit `-1` initialisiert. Das ist ein **neuer, unabhängiger
     Wissens-Gap** (Rust-Sprachmechanik, nicht Lock-free-Algorithmus-Design)
     — noch nicht im Graphen abgedeckt.
- Import: `Rust thread::spawn requires 'static bound`,
  `Rust interior mutability requires UnsafeCell for &self mutation`,
  `Rust usize is unsigned, no negative literal`
  (`curated_set: systems_programming_v4_rust_compile_correctness`), Quellen:
  doc.rust-lang.org (std::thread::spawn, Reference: Interior Mutability,
  std::cell::UnsafeCell, std::primitive::usize), RFC 3151 Scoped Threads.

### Lauf 4

- **Fünfter Durchlauf: neue, unabhängige Fehlerklasse — Repetitions-Kollaps
  in der Merger-Synthese selbst.** Score 3.0/10, `total_tokens=0`, HTTP 422
  `unclosed_code_block`. Über Postgres `ai_io_audit_log` (Request-Body pro
  Judge-Call abgeglichen) nachgewiesen: die MERGER-Synthese selbst (nicht
  Critic) erzeugte eine 100.216 Zeichen lange Antwort, die normal beginnt
  ("Here is the synthesized implementation..."), dann aber in eine
  Wiederholungsschleife kollabiert (`// I will output the SPSC code.`,
  Dutzende Male), mitten im Code-Block abbrechend. Der bereits deployte
  Critic-Guard hat korrekt erkannt, dass die *Critic*-Antwort nicht konform
  war, und die vorherige (Merger-)Antwort bewahrt — aber diese war selbst
  schon kaputt. Quality-Gate hat zu Recht blockiert.
- Fix (zwei Teile, gemeinsam umgesetzt):
  1. `repeat_penalty=1.3`/`repeat_last_n=256` für den Merger-Synthese-Call
     (Ollama-Sampling-Parameter, unterdrückt Wiederholungsschleifen direkt
     an der Quelle) — nur für diesen Call-Site, alle anderen Judge-Aufrufe
     unverändert.
  2. Merger-Retry-Loop nutzt jetzt die bereits vorhandene
     `verify_response_plausibility()`-Prüfung (dieselbe, die Quality-Gate am
     Ende nutzt) direkt nach jedem Syntheseversuch — bei Erkennung (leer,
     zu kurz, unclosed Code-Block) wird ein weiterer Versuch ausgelöst statt
     die kaputte Antwort durchzureichen.
  Verifiziert gegen den exakten aufgezeichneten Repetitions-Text (korrekt als
  nicht plausibel erkannt) und eine saubere Antwort (unverändert akzeptiert).
  Container neu gebaut
  (`sha256:6591d3474c71e9035a290bf3f35ec04112dc5cd904b4d7bf0f5f369f06590d57),
  deployed.
- **Erster Wiederholungsversuch ungültig (Verfahrensfehler, kein Modell-/
  Wissensergebnis):** Ursprünglicher Prozess hing >40 Min, Watchdog hat
  korrekt erkannt und neu gestartet — aber der Watchdog-Neustart läuft per
  Design **ohne** `--fresh` (Resume-Modus, gedacht für echte lange
  Mehr-Task-Läufe). Da die Checkpoint-Datei noch Lauf 3's Eintrag enthielt
  (mein `--fresh` beim ursprünglichen Start hatte nur den In-Memory-Zustand
  zurückgesetzt, nie die Datei überschrieben, bevor der Prozess hing), hat
  der Neustart Lauf 3's Ergebnis wortidentisch als `[RESUMED]` übernommen —
  kein neuer Generierungslauf, keine Aussage über die beiden neuen Fixes.
  Korrektur: Checkpoint-Datei vor jedem gezielten Einzel-Wiederholungslauf
  jetzt explizit gelöscht (nicht nur `--fresh`); für diese isolierten Läufe
  läuft ab sofort kein Watchdog mehr mit (dessen Resume-Verhalten passt
  nicht zum Anwendungsfall), manuelle Überwachung stattdessen.
- Status: läuft (siebter Versuch, ohne Watchdog).
- **Siebter Versuch ungültig (Infra-Bug):** Score 4.9, `judge_verdict=FAIL`
  (echt), aber `final_response` durch den 5. bislang ungefixten
  Critic-Präambel-Varianten kontaminiert (`'The provided "ANSWER TO CHECK"
  is severely corrupted...'`, quotet den Prompt-Header wörtlich statt eines
  einfachen Nomens). Fix: `_CRITIC_PREAMBLE_RE` um optionales Anführungszeichen
  und "to check"-Suffix erweitert (`agent_status/claude-code.md`,
  FIX-critic-preamble-fifth-variant). Wissensstand unverändert.
- **Achter Versuch ungültig (Infra-Bug, vor der eigentlichen Aufgabe):**
  Preflight-Check des Skripts selbst schlug fehl (HTTP 422). Ursache: die
  triviale Preflight-Probe `"ping"` liess den Planner reproduzierbar eine
  komplett themenfremde Aufgabe halluzinieren ("Characterize DNS, HTTP,
  gRPC... routing"), die vorhersehbar an Trust-Score/Quality-Gate scheiterte.
  Fix: Preflight-Probe auf eine echte Frage umgestellt, `quality_blocked`
  als Soft-Pass gewertet. Reiner Skript-Fix, kein Wissens-Gap.
- **Neunter Versuch ungültig (Infra-Bug):** Score 6.2, `judge_verdict=FAIL`
  (echt, korrekt erkannt: "devolves into incoherent word salad"), aber die
  Merger-Synthese selbst (Versuch 2/3, bestand die damalige Plausibilitäts-
  Prüfung) driftete in eine mehrere tausend Wörter lange, grammatikalisch
  flüssige aber inhaltsleere Assoziationskette ohne einen einzigen
  Code-Block, obwohl die Aufgabe explizit Rust/C++-Code verlangte. Bestätigt
  die zuvor offene Sorge: `repeat_penalty=1.3` (aus dem Repetitions-Collapse-
  Fix) verhindert wörtliche Wiederholung, aber nicht thematisches Abdriften.
  User-Entscheidung: Plausibilitäts-Check erweitern statt `repeat_penalty`
  zurückzunehmen. Fix: `verify_response_plausibility()` erkennt jetzt
  `missing_required_code`, wenn die Aufgabe explizit eine Implementierung in
  einer benannten Sprache verlangt und die Antwort keinen Code-Block enthält
  (`agent_status/claude-code.md`, FIX-plausibility-missing-required-code).
  Wissensstand unverändert (dritter Infra-Bug in Folge, kein Wissens-Gap).
- Status: läuft (zehnter Versuch).
- **Zehnter Versuch ungültig, aber korrekt blockiert:** Score 3.0
  (`deterministic_score=0.0`, `total_tokens=0`), `final_response=""`, HTTP 422
  `plausibility_failed:missing_required_code`. Der neue Check hat wie
  vorgesehen gegriffen und verhindert, dass Garbage durchrutscht -- aber
  auch alle 3 Merger-Retry-Versuche degenerierten identisch (kein einziger
  Code-Block über 76 Minuten Rechenzeit). Deutet stark darauf hin, dass
  `repeat_penalty=1.3` selbst die Ursache ist, nicht nur ein Erkennungs-Gap.
- **Elfter Versuch abgebrochen (Verfahrensentscheidung während des Laufs):**
  Gleicher Merger-Call driftete erneut in Wort-Salat, diesmal auf über
  22.000 Tokens wachsend (Richtung 32k-Limit) nach 30+ Minuten ohne
  Terminierung -- live via Ollama-Timing-Log und GPU-Auslastung bestätigt
  (kein Stillstand, echte Generierung). Vierte Reproduktion desselben Musters
  in Folge. User-Entscheidung: jetzt abbrechen, `repeat_penalty` reduzieren
  statt einen weiteren ~40-75-min-Lauf abzuwarten.
  Fix: `repeat_penalty` 1.3 → 1.15 (`agent_status/claude-code.md`,
  FIX-reduce-merger-repeat-penalty). Wissensstand unverändert (vierter
  Infra-Bug in Folge, kein Wissens-Gap; kumulativ ~2,5 Std. GPU-Zeit ohne
  wissenschaftlichen Erkenntnisgewinn für diese Experiment-Frage).
- Status: läuft (zwölfter Versuch).
- **Zwölfter Versuch: erstes valides, unkontaminiertes Ergebnis in Lauf 4.**
  Score 5.2/10, Det 10.0, Judge 2.0, `judge_verdict=FAIL` (echt). 18.349
  Zeichen echte Antwort, sauber geschlossener Code-Block, keine
  Wort-Salat-Drift mehr — `repeat_penalty=1.15` hat das Problem gelöst.
  (Das gespeicherte `final_response`-Feld selbst war auf 1000 Zeichen für
  die Zusammenfassung gekürzt, `turns[0].response` enthält die volle
  Antwort.)
  Judge-Befund, zwei Kategorien:
  1. *"release CAS before writing the payload... consumer reads
     uninitialized memory"* — Umkehrung des seit Runde 2 im Graphen
     vorhandenen Fakts ("producer write-before-release ordering"). Zweite
     Wiederholung desselben Wissensbereichs, nähert sich dem
     Abbruchkriterium für dieses Teilgebiet (Anwendungsproblem, kein
     Wissens-Gap).
  2. *"test does not compile due to moved-value and join-return-type
     errors"* — neuer, unabhängiger Rust-Gap, nicht von Runde 4 abgedeckt.
- Import: `Rust thread::spawn closures need move + Arc for shared
  ownership`, `JoinHandle::join() returns a Result that must be handled`
  (`curated_set: systems_programming_v5_rust_move_and_join`), Quellen:
  doc.rust-lang.org (The Book Kapitel 16.1, std::thread::JoinHandle::join,
  std::sync::Arc).
- Status: läuft (dreizehnter Versuch).
- **Dreizehnter Versuch ungültig (echter, bisher unentdeckter Infra-Bug,
  nicht kosmetisch):** Score 3.0, `final_response=""`, HTTP 422
  `trust_score_block`. Planner erzeugte erneut zwei redundante
  `systems_programming`-Tasks (wie in Lauf 3), Trust-Score fiel auf BLOCK
  (0.272). Log zeigte danach aber: alle 4 Konflikte von
  `resolve_conflicts_node` als non-kritisch verworfen, UND der
  Hallucination-Check-Critic hat die eine echte unsupported claim korrekt
  gefunden und korrigiert — protokolliert (irreführend) als "Trust-Score
  stayed PROCEED_WITH_ASSUMPTION". Trotzdem wurde die korrigierte Antwort
  verworfen. Ursache: `critic_node`'s Hallucination-Check-Pfad hat
  `state_["trust_verdict"]` nie tatsächlich aktualisiert — die Quality-Gate
  las weiterhin den veralteten `BLOCK`-Wert einer früheren Merger-Runde.
  Echter, produktionsrelevanter Bug (kein Test-Artefakt). Fix:
  Hallucination-Check kann jetzt einen veralteten `BLOCK` auf
  `PROCEED_WITH_ASSUMPTION` anheben, wenn die Prüfung selbst die Antwort
  bestätigt oder korrigiert (`agent_status/claude-code.md`,
  FIX-critic-hallucination-check-unblocks-stale-trust). Wissensstand
  unverändert (fünfter Infra-Bug in Folge, kein Wissens-Gap).
- Status: läuft (vierzehnter Versuch).
- **Fünfzehnter bis zwanzigster Versuch:** siehe FIX-Einträge in
  `agent_status/claude-code.md` (Hallucination-Check-Log-Fix, Regression
  Lauf 5.6-Score-Varianz, GraphRAG-Fix-Nachfolgetests) sowie die separate
  Feature-Entwicklung `rust_compile_check` (Compiler-in-the-Loop, siehe
  Pro/Contra- und Implementierungsplan-Diskussion im Session-Protokoll).
  Host-Wartung dazwischen: separater `moe-codex`-Compose-Stack (25
  Container) auf User-Anweisung gestoppt, um Platz für den neuen
  Sandbox-Service zu schaffen (1,2 GiB → 8,2 GiB frei).
- **Einundzwanzigster Versuch: erster Lauf mit `rust_compile_check` live in
  der Pipeline.** Score 5.5/10, Judge 2.5/10, `judge_verdict=FAIL` (echt).
  Laufzeit 60 Minuten (vs. ~25-30 Min. zuvor) — der Merger hat über mehrere
  Self-Critique-Runden hinweg gegen echte `rustc`-Diagnosen retried, exakt
  wie vorgesehen (kein Bug, erwarteter Mehraufwand bei schwierigen
  Code-Aufgaben). Alle beobachteten Compiler-Fehler waren echt und
  unterschiedlich (unclosed delimiter, moved-value, Trait-Bound-Fehler,
  Borrow-Checker-Verstöße) — kein Fehlalarm.
  Judge-Befund, ein neuer Gap: *"dereferencing UnsafeCell without unsafe
  blocks"* — Modell erreicht `UnsafeCell::get()` korrekt (Runde 4/7-Wissen
  angewendet), vergisst aber, dass der zurückgegebene Rohzeiger weiterhin
  einen `unsafe`-Block zum Dereferenzieren braucht (syntaktische
  Voraussetzung, unterscheidet sich von der bereits importierten
  Aliasing-Regel).
- Import: `Raw pointer dereference requires an unsafe block`
  (`curated_set: systems_programming_v9_unsafe_block_dereference`), Quelle:
  doc.rust-lang.org (std::cell::UnsafeCell).
- Status: läuft (zweiundzwanzigster Versuch).
- **Zweiundzwanzigster Versuch: Score 5.2/10, Judge 2.0/10.** Ordering-
  Verletzung jetzt zum 6./7. Mal unabhängig bestätigt trotz sichtbarem
  Wissen und aktivem Compiler-Check — strukturell erwartbar, da eine Data
  Race durch falsche Memory-Ordering kein Compile-Fehler ist und daher von
  Phase 1 (reines Typ-/Borrow-Checking) prinzipiell nicht erkannt werden
  kann. Details und Trainingsempfehlung in
  `docs/experiments/lumig_posttraining_candidates.md`.

## Abschluss der isolierten Experiment-Phase (2026-08-23)

Nach 22 Versuchen, 9 kuratierten Wissensrunden (22 Fakten,
`graph_rag/curated/systems_programming_reference.cypher`) und 9+ behobenen,
dauerhaften Infrastruktur-Bugs (siehe `agent_status/claude-code.md`) wird
die isolierte Phase hier abgeschlossen. Zusammenfassung:

**Zur Kernthese:** Teilweise bestätigt, mit einer wichtigen Einschränkung.
Wissensimport hat nachweislich geholfen (Score-Trend nach dem
GraphRAG-Retrieval-Fix: 5.2→5.5→5.8, vorher wiederholt niedriger durch den
Retrieval-Cap-Bug), aber die Aufgabe hat mehr unabhängige Fehlerquellen als
jede Wissensrunde einzeln abdeckt, und mindestens eine Kernfähigkeit
(Acquire-Release-Memory-Ordering-Reasoning) erwies sich über 6-7 unabhängige
Beobachtungen hinweg als echte Anwendungsgrenze, nicht als Wissens-Gap.

**Wichtigster Infra-Befund:** Der GraphRAG-Retrieval-Cap-Bug (nur 6 von
zeitweise 22 Fakten pro Anfrage sichtbar, in willkürlicher Reihenfolge) hat
einen Großteil der früheren Läufe kontaminiert — jede Schlussfolgerung aus
Läufen vor diesem Fix muss mit Vorbehalt gelesen werden.

**Neues Werkzeug:** `rust_compile_check` (Compiler-in-the-Loop, Phase 1) ist
implementiert, verifiziert und deployed — reduziert Rauschen durch
LLM-Selbsteinschätzung für syntaktische/typbezogene Fehler, kann aber
Nebenläufigkeits-Logikfehler (die dominante verbleibende Fehlerquelle)
prinzipbedingt nicht erkennen.

**Nächste Schritte (nicht in dieser Session):** LUMI-G-Nachtraining-Liste
(`lumig_posttraining_candidates.md`) als Grundlage für einen Trainings-Pass;
Entscheidung über Phase 2 (Miri/ThreadSanitizer) des Compiler-Checks.
- **Vierzehnter Versuch: Block korrekt (kein Bug), aber 6. Beobachtung
  desselben Judge-Non-Compliance-Musters** an einer neuen Stelle
  (Hallucination-Check-Critic selbst). Kein Wissens-Gap, keine neue
  Erkenntnis zur These.
- **Fünfzehnter Versuch: zweiter sauberer, valider Lauf.** Score 5.2/10,
  Det 10.0, Judge 2.0, `judge_verdict=FAIL` (echt). Judge-Befund: zwei der
  drei Kritikpunkte sind exakte Wiederholungen bereits importierter Fakten
  (Runde 4: `UnsafeCell`, `'static`-Bound). **User-Frage: ist das
  Anwendungsgrenze (Finetuning-Thema) oder Infrastruktur?**
  Root-Cause-Analyse anhand aufgezeichneter Prompts (`ai_io_audit_log`,
  alle 14 LLM-Calls dieses Laufs): **"UnsafeCell" und "'static" erscheinen
  in KEINEM einzigen Prompt** — auch nicht im finalen, 35.942 Zeichen
  langen Critic-Prompt, der explizit den vollen GraphRAG-Kontext enthalten
  sollte. Das Modell hat das Wissen nie gesehen, nicht ignoriert.
  **Root Cause gefunden und verifiziert:** `graph_rag/manager.py`s
  Retrieval-Query kappte hart bei den ersten 3 Suchbegriffen, 1 Entity-
  Treffer pro Begriff und den ersten 6 (von inzwischen 22 tatsächlich am
  Hub-Knoten hängenden) Fakten — in Neo4js interner, nicht
  relevanzsortierter Reihenfolge. Damit ist die zuvor gezogene
  "Anwendungsgrenze statt Wissens-Gap"-Schlussfolgerung **nicht haltbar**:
  sie beruhte auf Läufen, bei denen ein Großteil des importierten Wissens
  strukturell gar nicht ankommen konnte.
  Fix: Caps deutlich erweitert (Begriffe 3→6, Entities/Begriff 1→2,
  direkte Fakten 6→25) plus echtes Relevanz-Ranking beim Rendern (statt
  blinder Positions-Kappung auf die ersten 4) (`agent_status/claude-code.md`,
  FIX-graphrag-retrieval-relevance-cap). Verifiziert: Kontext wuchs von
  konstant 684 auf 1812 Zeichen, beide zuvor unsichtbaren Runde-4-Fakten
  jetzt enthalten.
  Separater, unabhängiger Befund (nicht behoben): Planner hat aus dem reinen
  Ringbuffer-Prompt zwei themenfremde Security-Tasks erfunden (Firewall,
  Auth-Kaskade) — verifiziert nicht im 48-KB-Planner-Prompt vorhanden, also
  echte Fabrikation, kein Kontext-Leck. Vermutlich Trainingsdaten-Artefakt
  des distillierten Planners, nicht diese Sitzung behoben.
- Status: läuft (sechzehnter Versuch, mit GraphRAG-Retrieval-Fix).
- **Sechzehnter Versuch: dritter valider Lauf, Fix bestätigt wirksam.**
  Score 5.5/10, Det 10.0, Judge 2.5, `judge_verdict=FAIL` (echt). **Die
  beiden zuvor wiederholt verletzten Runde-4-Fakten (`UnsafeCell`,
  `'static`-Bound) werden diesmal NICHT mehr bemängelt** — starkes Signal,
  dass der Retrieval-Fix tatsächlich wirkt. Judge-Befund stattdessen:
  1. *"full check and tail.fetch_add are not atomic"* — Verfeinerung von
     Runde 1 (CAS-Domäne).
  2. *"fails false-sharing requirement by only aligning the struct"* —
     Verfeinerung von Runde 1 (Padding muss zwischen Feldern liegen, nicht
     nur am Struct).
  3. *"Drop logic ignores the current head and can double-drop reused
     slots"* — **neuer, unabhängiger Gap**: korrekte `Drop`-Implementierung
     für teilinitialisierte Custom-Container.
- Import: `Custom container Drop must only drop occupied slots`
  (`curated_set: systems_programming_v6_manual_drop_occupied_slots`),
  Quelle: doc.rust-lang.org The Rustonomicon (Implementing Vec,
  Deallocating).
- Status: läuft (siebzehnter Versuch).
- **Siebzehnter Versuch: vierter valider Lauf, Trend hält an.** Score 5.8/10
  (5.2→5.5→5.8), Det 10.0, Judge 3.0 (2.0→2.5→3.0), `judge_verdict=FAIL`
  (echt). Modell hat `UnsafeCell` diesmal tatsächlich verwendet (Runde-4-
  Grundform nicht mehr verletzt), aber unsound eingesetzt. Judge-Befund:
  1. *"concurrent &mut access to the same UnsafeCell<...> violates Rust
     aliasing rules"* — neuer, tieferer Gap (UnsafeCell erlaubt nur &T-
     Umgehung, nie &mut-Exklusivität).
  2. *"push path publishes the slot after a relaxed CAS... lacks an acquire
     load"* — **dritte/vierte unabhängige Wiederholung** der Runde-2/3-
     Ordering-Domäne trotz bestätigt sichtbarem Wissen. Als ersten
     bestätigten LUMI-G-Nachtraining-Kandidaten dokumentiert (siehe
     `docs/experiments/lumig_posttraining_candidates.md`).
  3. *"unsound/invalid Sync ... constructs"* — neuer, unabhängiger Gap.
- Import: `UnsafeCell exempts only &T, never &mut, from aliasing rules`,
  `unsafe impl Sync requires actual synchronization, not just intent`
  (`curated_set: systems_programming_v7_unsafecell_aliasing_and_sync`),
  Quellen: doc.rust-lang.org (The Reference: Behavior Considered Undefined;
  The Rustonomicon: Send and Sync).
- Neu ab jetzt: parallel zur Wissens-Import-Schleife wird eine LUMI-G-
  Nachtraining-Kandidatenliste geführt
  (`docs/experiments/lumig_posttraining_candidates.md`) — Aufnahmekriterium:
  ein Fakt/Fähigkeitsbereich muss mindestens zweimal unabhängig trotz
  bestätigt sichtbarem Wissen verletzt worden sein.
- Status: läuft (achtzehnter Versuch).
- **Achtzehnter Versuch ungültig (transientes Planner-Sampling-Pech, kein
  Bug):** HTTP 500 `orchestration_failed` nach nur 14.8s. Planner scheiterte
  3x in Folge an strukturiertem Output (1. Versuch: Kategorie-Liste statt
  Tasks zurückgegeben; 2./3. Versuch: valides Task-JSON, aber
  `"topic": "lock_free_data_structures` ohne schließendes Anführungszeichen
  — ungültiges JSON). Bestehender 3-Versuch-Retry-Mechanismus hat korrekt
  erkannt und sauber mit klarem Fehler abgebrochen, kein Hänger, keine
  Korruption — als Architektur-Verhalten korrekt, reines Sampling-Pech.
  Kein Fix nötig, direkt neu gestartet.
- Status: läuft (neunzehnter Versuch).
- **Neunzehnter Versuch: Score-Plateau, 5. Bestätigung der Anwendungsgrenze.**
  Score 5.8/10, Judge 3.0 — **exakt identisch** zu Versuch 17, trotz zwei
  zusätzlicher Wissensrunden dazwischen. Judge-Befund:
  1. *"producers write the payload after the releasing CAS... consumers can
     read uninitialized/None slots"* — **fünfte unabhängige Beobachtung**
     der Runde-2/3-Ordering-Domäne trotz bestätigt sichtbarem Wissen. Nicht
     erneut importiert (kein Wissens-Gap mehr, siehe
     `lumig_posttraining_candidates.md`). Score-Plateau bei gleichbleibender
     Verletzung ist starkes Signal: weitere periphere Imports werden den
     Score vermutlich nicht mehr spürbar heben, solange diese eine
     Kernfähigkeit nicht adressiert wird.
  2. *"per-producer sequence validator initialized to u32::MAX instead of
     -1, causing immediate assertion failure"* — neuer, unabhängiger Gap:
     Sentinel-Wert passt nicht zur Vergleichsoperation (Monotonie-Check
     braucht einen "kleiner als jeder echte Wert"-Sentinel, MAX ist das
     Gegenteil). Modell hat vermutlich die Runde-4-Regel "MAX statt
     negativem Literal" auf einen Kontext übertragen, wo sie nicht passt.
- Import: `Sentinel value must match its comparison operation`
  (`curated_set: systems_programming_v8_sentinel_value_selection`), Quelle:
  Wikipedia (Sentinel value — Semipredicate-Problem, Option-Type-
  Alternative).
- Status: läuft (zwanzigster Versuch).
