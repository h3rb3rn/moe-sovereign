# Lehrer-Modell-Verifikation für das LUMI-G-Vollnachtraining

**Status:** in progress (Phase 0/1 abgeschlossen und real verifiziert, Phase 2
teilweise — siehe Lastenheft-Punkt unten)
**Zeitraum:** 2026-09-03 bis 2026-09-08
**Zugehöriger Plan:** `~/.claude/plans/zazzy-beaming-koala.md` (lokal, nicht im
Repo — dieses Dokument ist die dauerhafte, repo-gebundene Aufzeichnung der
Methodik, Ergebnisse und Probleme)

## Zweck dieses Dokuments

Vollständiges Nachtraining aller 10 MoE-Sovereign-Modelle (Planner, 8
Experten, Judge) mit echten, verifizierten Trainingsdaten — als Reaktion auf
den Antigravity-Frontier-Pipeline-Vorfall (`antigravity_frontier_pipeline_postmortem.md`),
bei dem ein nie ausgeführter Plan und fabrizierte Trainingsdaten über Monate
unentdeckt blieben. Die durchgehende Leitlinie dieser Arbeit: **kein Schritt
gilt als erledigt, weil ein Prozess mit Exit-Code 0 endete oder eine Zeilenzahl
plausibel aussah** — jeder Schritt braucht echte, inhaltliche Verifikation.
Dieses Dokument hält fest, wie diese Verifikation für die Lehrer-Modell-Auswahl
konkret aussah, was dabei gefunden wurde, und welche Probleme das aufgedeckt
hat.

---

## Teil 1: Verifikationsmethodik

### 1.1 LUMI-G: Drei-Filter-Kompatibilitätsprüfung

LUMI-G nutzt einen fest gepinnten vLLM-Build (`0.20.1+lumi.aif.gfx90a`,
ROCm/AMD MI250X). Ein Modell ist dort nur nutzbar, wenn es **alle drei**
folgenden Filter besteht — geprüft ausschließlich über Metadaten
(`HfApi().model_info()` + `config.json`-Download), **nie** durch Herunterladen
der eigentlichen Gewichte:

1. **Architektur-Registrierung**: Die `architectures`-Angabe aus `config.json`
   muss in `ModelRegistry.get_supported_archs()` (direkt per SSH +
   `singularity exec` Python-Einzeiler geprüft, 344 registrierte Klassen,
   keine GPU/kein Download nötig) enthalten sein.
2. **Quantisierungs-Kernel**: Auch bei registrierter Architektur kann eine
   spezifische Quant-Methode auf ROCm scheitern — unabhängig von Filter 1.
   Bestätigt live: `deepseek_v4_fp8` schlägt beim Engine-Init fehl
   (`"deepseek_v4_fp8 quantization is currently not supported in rocm"`,
   Job 21676155), obwohl `DeepseekV4ForCausalLM` registriert ist.
3. **Größe vs. Node-HBM2e** (~512GB/Node): unquantisierte Gewichtsgröße muss
   in dieses Budget passen, abzüglich KV-Cache/Aktivierungen.

### 1.2 LUMI-G: Der vierte, empirisch entdeckte Faktor — Checkpoint-Größe vs. Node-RAM

Erst durch reale Smoke-Tests entdeckt, nicht aus der Dokumentation ableitbar:
Wenn ein Checkpoint >90% des verfügbaren Node-**RAM** (nicht VRAM, ~448GB)
belegt, schaltet vLLMs Lustre-Auto-Prefetch ab
(`"Checkpoint size (X GiB) exceeds 90% of available RAM (Y GiB). Skipping
auto-prefetch."`) — danach werden Shards einzeln von Lustre gelesen
(~100-228s/Shard statt ~1-1,2s/Shard). Kein OOM, keine Fehlermeldung, die
danach aussieht wie ein Ladeproblem — nur eine 2-3h-lange, stille Verlangsamung,
die einen 2h-Job scheinbar grundlos ins Timeout laufen lässt (Jobs 21701731,
21701732). **Konsequenz:** jeder Job mit einem Checkpoint nahe der RAM-Grenze
braucht ≥4h Walltime allein fürs Laden, unabhängig von der eigentlichen
Generierungsdauer.

### 1.3 OpenRouter: Lizenz-Verifikation ausschließlich am Original

Frontier-Modelle (Claude/GPT/Gemini über Claude Code/Codex/Antigravity) wurden
geprüft und **verworfen**: alle drei Anbieter verbieten aktuell (Original-ToS
direkt eingesehen, nicht Sekundärquellen), Modell-Outputs zum Trainieren
anderer Modelle zu verwenden:

- **Anthropic** (Claude Help Center, direkt gefetcht): *"We prohibit customers
  from using our services to train or develop AI models without our written
  permission"* — explizit ausgeschlossen: "general purpose chatbots",
  "models designed for open-ended text generation", "using Outputs as
  training targets for models".
- **OpenAI** (Services Agreement PDF, `v.010126`, per PDF-Extraktion direkt
  gelesen, §3.3(e)): *"except for a Permitted Exception, use Output to
  develop artificial intelligence models that compete with OpenAI's products
  and services"* — die "Permitted Exception" ist eng auf
  Klassifikatoren/Embeddings (nicht verteilt) und OpenAIs eigene
  Fine-Tuning-Services beschränkt.
- **Google** (Gemini API Additional Terms of Service): *"You may not use the
  Services to develop models that compete with the Services"*.

Diese Prüfung wurde real durchgesetzt bestätigt: Anthropic beschuldigte im
Februar 2026 öffentlich Moonshot AI (Kimi), massenhaft Konten genutzt zu haben,
um Claude-Konversationen zum Training eigener Modelle zu generieren — kein
theoretisches Risiko.

**Als Konsequenz** wurde für jedes tatsächlich in Betracht gezogene
OpenRouter-Modell die **Original-Lizenzdatei direkt** (HuggingFace `LICENSE`,
nicht Blog-Zusammenfassungen) eingesehen, bevor es empfohlen wurde:

| Modell | Lizenzquelle direkt geprüft | Ergebnis |
|---|---|---|
| Kimi K3 | HF `LICENSE` | Eigene Lizenz, breite Kommerzrechte, $20M-Umsatzschwelle, keine Distillation-Einschränkung |
| GLM-5.3 | HF `LICENSE` | Eigene Lizenz, $10Mrd-Umsatzschwelle, keine Distillation-Einschränkung |
| DeepSeek-V4-Pro | Sekundärquelle (MIT bestätigt über mehrere Quellen) | MIT — sauberste Lizenz |
| Mistral Large 3 (2512) | Sekundärquelle (Apache 2.0 bestätigt) | Apache 2.0 |
| Mistral-Large-Instruct-2411 (LUMI-G) | Mistral AI Research License (MRL), Originaltext | **Nur "Research Purposes"** — nicht direkt/indirekt mit kommerziellen Aktivitäten verbunden. Für MoE-Sovereign als privates Forschungsprojekt vermutlich unkritisch, aber nicht abschließend geprüft — Nutzer muss selbst einordnen. |
| Qwen3.8-Max | HF `LICENSE` + Sekundärquellen | **Ausgeschlossen**: Alibaba-Terms beschränken Nutzung von "Model Studio"-Outputs zum Training konkurrierender Produkte; ≥24,3% der eigenen Trainingsdaten aus nicht-kommerziellen CC-BY-NC-Quellen (Lizenz-Kontamination) |
| Grok 4.5 | Recherche zu Verfügbarkeit | **Ausgeschlossen**: kein Open-Weight-Release (nur API), xAI zählt zu den Anbietern mit Anti-Distillation-Klauseln — gleiche Kategorie wie Claude/GPT/Gemini |
| MiniMax M3 | HF `LICENSE` | Unkritisch (Community License, $20M-Schwelle), aber Benchmark-Score zu niedrig für Priorisierung |
| NVIDIA Nemotron 3 Nano | NVIDIA Open Model License | Sehr sauber, aber Benchmark-Score (9) zu schwach — "Nano"-Klasse |

### 1.4 OpenRouter: Benchmark-Verifikation an einer einzelnen, konsistenten Quelle

Wichtige Selbstkorrektur während der Arbeit: es gibt **kein offizielles
Ranking** — verschiedene Leaderboards (LMArena, BenchLM, Artificial Analysis)
nutzen unterschiedliche Methodik und kommen zu abweichenden Platzierungen.
Erste Modellauswahl beruhte auf Sekundärquellen-Snippets aus verschiedenen
Leaderboards — auf Nutzernachfrage korrigiert: alle Kandidaten wurden erneut
im **selben Snapshot** von Artificial Analysis' Intelligence Index verglichen
(direkter Fetch, nicht Suchergebnis-Zusammenfassung), um Äpfel mit Äpfeln zu
vergleichen:

| Modell | Intelligence Index (AA, Snapshot 2026-09-08) |
|---|---|
| Qwen3.8-Max | 45* (Daten unvollständig markiert; ohnehin lizenzrechtlich ausgeschlossen) |
| Kimi K3 | 44 |
| GLM-5.3 | 44 |
| DeepSeek V4 Pro | 36 |
| MiniMax M3 | 30 |
| NVIDIA Nemotron 3 Nano | 9 |

### 1.5 Die wichtigste Verifikationsdisziplin: reale Inhalte lesen, nicht nur Zeilenzahlen zählen

Der mit Abstand wertvollste Teil der Methodik in dieser Arbeit: **jedes
"PASSED" wurde durch Lesen echter Stichproben gegengeprüft**, nicht nur durch
die vom Skript selbst gemeldete Erfolgszahl. Das deckte mehrere reale Bugs auf,
die eine reine Zeilenzahl-Prüfung durchgelassen hätte (siehe Teil 3).

### 1.6 Reasoning-Effort-Kontrolle als kritischer, zunächst übersehener Parameter

Erste Diagnose ("2026er-Reasoning-Modelle sind für Bulk-Generierung
grundsätzlich unzuverlässig") war **falsch** und wurde durch weitere
Verifikation korrigiert: Kimi K3, GLM-5.3 und DeepSeek-V4-Pro unterstützen
alle OpenRouters standardisierten `reasoning`-Parameter (bestätigt via
`GET /api/v1/models` → `supported_parameters`). Ungebremstes Reasoning
(Standardeinstellung) führte zu vollständigem Scheitern; `reasoning.effort:
low` behob es für Kimi K3 und GLM-5.3 vollständig (siehe Teil 2), aber
**nicht** zuverlässig für DeepSeek-V4-Pro (siehe Teil 3.3) — die
Normalisierung von OpenRouters generischem Parameter auf den jeweiligen
Provider-eigenen Mechanismus ist nicht garantiert einheitlich.

---

## Teil 2: Ergebnisse — reale Smoke-Tests

### 2.1 LUMI-G-Lehrer (Grounding-Modus, `--mode grounding`)

| Modell | Ergebnis | Anmerkung |
|---|---|---|
| Qwen3.5-35B-A3B | Verifiziert (Grounding + Loom nach Fix) | Ursprünglicher Distillations-Lehrer |
| Qwen3-Next-80B-A3B-Instruct | 24/24 | Tier A, Reasoning-Cluster |
| GLM-4.5-Air | 24/24 (nach Parser-Fix, vorher 3/24) | Tier A, Code/Governance-Cluster |
| Mistral-Large-Instruct-2411 | 16/16 | Tier B, lizenzrechtlich eingeschränkt (MRL) |
| Qwen3-235B-A22B-Instruct-2507 | 14/16 | 7/8 Kategorien perfekt, `security`-Kategorie konsequent verweigert |
| DeepSeek-Coder-V2-Instruct | 14/16 | Gleiches `security`-Verweigerungsmuster |

### 2.2 OpenRouter-Lehrer (`role_sft`-Modus, mit `reasoning_effort=low` wo zutreffend)

| Modell | Rolle getestet | Ergebnis | Kosten/Beispiel |
|---|---|---|---|
| Mistral Large 3 (2512) | coder | 3/3 sauber, KEIN Reasoning-Tuning nötig | $0,003 |
| Kimi K3 | coder | 0/3 (ohne Reasoning-Kontrolle) → **3/3** (mit `--reasoning-effort low`) | $0,026 |
| GLM-5.3 | research | 0/6 (zwei Läufe ohne Kontrolle) → **3/3** (mit `--reasoning-effort low`) | $0,007 |
| DeepSeek-V4-Pro | research | 3/3, 3/3 (ohne Parameter) | $0,006-0,011 |
| DeepSeek-V4-Pro | coder | 3/3 (1× mit Reasoning-Resten kontaminiert) → mit `effort=low`: **0/3** → mit `effort=none`: 2/3 → ganz weggelassen: 2/3 | Schwankend, kein zuverlässiger Fix gefunden |

---

## Teil 3: Identifizierte Probleme

### 3.1 Infrastruktur-Bugs (LUMI-G)

- **Stage-3-GGUF-Export** (`singularity: command not found`, 9/10 historische
  Trainingsjobs betroffen): PATH-Abhängigkeit zwischen Stages entfernt,
  `SINGULARITY_BIN` einmal aufgelöst und explizit durchgereicht. Real
  verifiziert (Job 21698556, echte GGUF-Dateien geprüft).
- **Home-Quota** (`/users/hornphil` zu klein für Triton/Torch-Caches):
  bereits vor dieser Session gefixt, Muster in allen neuen Skripten
  übernommen.

### 3.2 Parser-/Format-Bugs (gefunden durch echte Inhaltsprüfung, nicht Zeilenzahl)

Chronologisch, jeweils mit Regressionstest abgesichert:

1. **GLM-4.5-Air-JSON-Escaping**: gieriger `\[.*\]`-Regex spannte über
   Reasoning-Text mit eigenen Klammerzeichen hinweg — 7/8 Grounding-Kategorien
   auf 0 zerstört trotz realer Generierung. Fix: klammertiefen-bewusster
   Scanner (`_find_bracket_balanced_arrays`).
2. **Derselbe Bug bei `--mode loom`** (seit Wochen unentdeckt, fälschlich auf
   `--max-tokens` zurückgeführt): mehrzeiliger Rust-Code lässt sich generell
   nicht zuverlässig als JSON-String escapen — **Formatwechsel** auf
   Trennzeichen-basiertes Schema (`===FIELD===\n<Inhalt>\n===END===`) statt
   JSON, kein Escaping mehr nötig.
3. **Mindestlängen-Garbage** (`` `, ` `` als "erfolgreich" geparstes Ergebnis):
   Parser prüfte nur "nicht leer", nicht Plausibilität. Fix:
   `_MIN_SOURCE_LEN`/`_MIN_RESPONSE_LEN`-Guards.
4. **Duplikat-Marker-Kontamination** (DeepSeek-V4-Pro schrieb einen
   Platzhalter-Fehlversuch, dachte laut nach, setzte neu an — Parser griff
   den ersten statt den letzten Versuch): Fix auf rückwärtssuchende
   Markerzuordnung umgestellt.
5. **Template-Leak, nicht zuverlässig automatisch erkennbar** (Kimi K3 schrieb
   `` `, then request, then ` `` als "Anfrage" — wörtliche Wiederholung der
   Formatanweisung statt echtem Inhalt): bewusst **kein** Heuristik-Fix
   gebaut, da ein Wortzahl-/Längentest sowohl diesen Müll als auch legitime
   kurze Beispiele ("What is 2+2?") gleichermaßen erfasst hätte. Verbleibt
   ein Restrisiko, das nur durch die ohnehin vorgesehene manuelle
   Stichprobenprüfung (5 Beispiele/Rolle) abgefangen wird.

### 3.3 Modell-Verhalten (kein Code-Bug, echte Eigenschaft der Modelle)

- **`security`-Kategorie-Verweigerung**: Qwen3-235B-A22B und
  DeepSeek-Coder-V2 verweigern beide konsequent (0/2) den generischen
  Grounding-Meta-Prompt für die Sicherheits-Kategorie — vermutlich
  Content-Moderation. Noch zu prüfen: ob das echte `security`-Rollen-Prompt
  dasselbe zeigt.
- **Reasoning-Token-Erschöpfung**: GLM-5.3 und Kimi K3 verbrauchten ohne
  explizite Reasoning-Kontrolle ihr gesamtes `max_tokens`-Budget fürs interne
  Denken (`content: null` in der API-Antwort, trotz realer Abrechnung) — mit
  `reasoning.effort=low` vollständig behoben für beide.
- **DeepSeek-V4-Pro-Reasoning-Kontrolle unzuverlässig**: weder
  `reasoning.effort` (OpenRouter-Standard) noch DeepSeeks eigenes
  `{"thinking":{"type":"disabled"}}` unterdrückten das Reasoning zuverlässig;
  Erfolgsquote schwankte zwischen Läufen (2/3 bis 3/3) unabhängig vom
  Parameter — kein sauberer Fix gefunden, echte Lauf-zu-Lauf-Varianz.

### 3.4 Der zentrale Scope-Gap: generischer `role_sft`-Ansatz trifft nicht die eigentlich identifizierten Schwächen

Beim erneuten, kritischen Abgleich mit den realen Benchmark-Funden
(`lumig_posttraining_candidates.md`) und der Planner/Judge-Produktionslogik
(`graph/planner.py`) zeigte sich: der generische `_ROLE_SFT_GENERATION_TEMPLATE`
("erfinde eine realistische Anfrage + Antwort in Prosa") erzeugt zwar
brauchbare, aber **an den eigentlich identifizierten Schwächen vorbeigehende**
Trainingsdaten:

- **Planner** (betrifft 3 von 5 dokumentierten Kandidaten): der reale
  Planner-Output ist ein strukturiertes JSON-Task-Array mit exaktem
  MCP-Tool-Schema (`id`/`task`/`category`/`mcp_tool`/`mcp_args`),
  `$task_result`-Verkettung für mehrstufige Berechnungen, Skill-Katalog-
  Invocation — keines davon wird vom generischen Prosa-Template trainiert.
  Betroffen: Kandidat 5 (falsches `decimal_finance`-Argumentschema),
  Kandidat 4 (verschachteltes/escaptes JSON bei Multi-Entity-Persistierung),
  neuer Fund (systematische Unter-Dekomposition bei mehrstufigen
  Rechenaufgaben).
- **Judge**: Kandidat 3 (Format-Compliance) verlangt einen sehr spezifischen
  Kontrakt (`_CRITIC_PREAMBLE_RE` in `graph/synthesis.py`) — bares
  `CONFIRMED` oder direkte Korrektur, ohne jede Präambel. Das generische
  "bewerte in Prosa"-Template trainiert dieses Kontraktverhalten nicht.
- **Coder**: Kandidat 1 (Memory-Ordering) hat bereits eine dedizierte,
  Loom-verifizierte Pipeline (`generate_loom_seed_examples.py` +
  `curate_coder_expert_dataset.py`), die aber nicht mit den neuen generischen
  `role_sft`-Daten zusammengeführt ist — Risiko, dass das eigentliche
  Kernproblem im finalen Datensatz verwässert wird.
- **Long-Term-Memory**: kein fehlender Experte — geprüft: 3-Tier-System
  (Hot=Kontext, Warm=ChromaDB `memory_retrieval.py`, Cold=Neo4j/GraphRAG).
  Tier 3 ist bereits der bestehende `graphrag`-Experte. Tier 1/2 sind
  automatische Pipeline-Mechanismen ohne LLM-Beteiligung.
- **Die übrigen 7 Experten**: generischer Ansatz bestätigt korrekt — MCP-
  Tool-Aufrufe werden ausschließlich vom Planner entschieden und
  deterministisch von der Pipeline ausgeführt, die Experten selbst
  bekommen nur Prosa-Aufgaben.

**Noch offen (nicht umgesetzt):** planner-spezifischer Generierungsmodus,
judge-spezifischer Modus, Loom-Merge-Workflow — siehe Lastenheft-Punkt am
Ende dieses Dokuments.

---

## Teil 4: Infrastruktur — `rust-loom-sandbox`-Ressourcen

Reale Messung (nicht Dokumentation): Leerlauf 47MB RAM/0,14% CPU; unter
echter Last (ein reales `loom-check`) **~316MB RAM (41% des 768MB-Limits),
37% CPU, 3,2s Dauer** für einen einfachen Testfall. Serialisiert
(`asyncio.Semaphore(1)`) wegen RAM-Headroom auf dem moe-infra-Host — der
Host selbst hat aktuell sehr wenig Puffer (35GB RAM gesamt, 839MB sofort
frei, Swap nahezu voll, 6-8 weitere Produktionsdienste bereits resident).

**Entscheidung:** Batch-Verifikation von Lehrer-generierten Loom-Kandidaten
auf eine separate VM auslagern statt den angespannten moe-infra-Host mit
parallelen Sandbox-Instanzen weiter zu belasten.

**Umgesetzt und real verifiziert (2026-09-08):** Bereitgestellte VM
(`vm-lumi-g-netcup`, SSH-Alias) wich von der ursprünglichen Ankündigung ab
(real: Debian 12 bookworm, 4 Kerne, 7,8GB RAM statt der angekündigten
Debian 13/8 Kerne/16GB) — laut Nutzer bewusst eine zum Oktober gekündigte,
sonst ungenutzte "monitoring"-VM, vor dem Einrichten per `ps`/`ss`/
`systemctl` verifiziert als tatsächlich frei (nur `atop` als Alt-Dienst,
keine Konflikte, Boot-Zeit desselben Tages).

- `services/rust_loom_sandbox/` (Dockerfile, app.py, scaffold) per `scp`
  übertragen, Image dort gebaut (`cargo build --release` mit vendorierter
  `loom`-Abhängigkeit, danach netzwerklos lauffähig).
- Neue `docker-compose.loom-sandbox-remote.yml` (3 Instanzen statt 1, an
  4 echten Kernen — 1 Kern Puffer für OS/Docker-Daemon). **Erste Version
  nutzte `deploy.resources.limits` — von `docker compose up` ohne Swarm-Mode
  nicht durchgesetzt** (`CpuQuota=0` trotz `cpus: '1'`); auf die klassischen
  Top-Level-Schlüssel (`cpus:`, `mem_limit:`, `pids_limit:`) umgestellt,
  danach korrekt angewendet (`NanoCpus=1000000000` bestätigt).
- Ports nur auf `127.0.0.1` gebunden (kein Internet-Exposure) — die
  Verifikations-Läufe laufen direkt auf der VM selbst, nicht über einen
  Tunnel von außen.
- **Realer Parallel-Test**: 3 gleichzeitige `/loom-check`-Anfragen an alle
  3 Instanzen, alle `compiles:true, passed:true`. RAM danach: 7,2GB frei,
  kein Swap-Verbrauch (vs. dem knappen moe-infra-Host) — bestätigt sicheren
  Spielraum für die geplante Nutzung.
- **Zweite, ebenfalls gekündigte VM** (`vm-lumi-g-netcup-02`, Debian 13
  trixie, 4 Kerne, 7,8GB RAM) vom Nutzer bereitgestellt, Docker CE fehlte
  noch (nur `containerd.io` aus dem bereits konfigurierten Docker-Apt-Repo
  vorhanden) — nachinstalliert.
  **Unerwarteter Zwischenfall:** das Starten des Docker-Daemons hat wegen
  `restart: always`-Policies automatisch **23 Container einer zweiten,
  fast vollständigen MoE-Sovereign-Stack-Instanz** hochgefahren (Neo4j,
  Postgres, Kafka, ChromaDB, Ollama, Open-WebUI, Grafana, MinIO,
  mcp-precision u.a., real mit Daten unter `/opt/moe-sovereign`) — die VM
  war entgegen der Ankündigung nicht leer. Auf Nutzeranweisung
  ("Alle Container abreisen und das System säubern") vollständig bereinigt:
  alle 23 Container gestoppt/entfernt, `docker system prune -a --volumes`
  (58,56GB freigegeben), 5 vom automatischen Prune übersehene Volumes
  manuell nachentfernt (`libre-api-main_*`, `moe-sovereign_caddy_*`,
  `moe-sovereign_moe_storage_data`) — Enddiskstand: 34GB statt zuvor 111GB
  belegt, keine Container/Volumes/Images mehr vorhanden.
  Danach identisches Setup wie VM1: Image gebaut, 3 Instanzen gestartet,
  realer 3-fach-Paralleltest bestanden (`compiles:true, passed:true`
  auf allen 3 Ports), 7,0GB RAM frei danach.

**Gesamtkapazität nach beiden VMs: 6 parallele `rust-loom-sandbox`-Instanzen
über zwei unabhängige Maschinen**, beide real end-to-end verifiziert (nicht
nur Health-Check, sondern echte `/loom-check`-Aufrufe mit korrektem
Ergebnis).

---

## Teil 5: Planner- und Judge-spezifische Modi (2026-09-09) — real verifiziert

**Planner-Modus:** `_PLANNER_TRAINING_SYSTEM_PROMPT` (kondensierte, statische
Regeln aus `graph/planner.py`: exaktes MCP-Schema, `$task_result`-Verkettung,
Wissensspeicher-Prosa-Muster, VLSM-vs-subnet_calc) + 8 rotierende
Muster-Fokusse (`_PLANNER_PATTERN_FOCUS`) für gezielte Diversität +
`_validate_planner_task_array()` (echte strukturelle JSON-Validierung, kein
Längen-Ersatz). Reale Stichprobe (Mistral Large 3, 7/8 erfolgreich)
bestätigte alle Zielmuster: Einheiten-Umrechnung, korrektes VLSM,
Wissensspeicherung als flache Prosa-Bestätigung (Kandidat 4 direkt
getroffen), BGB-Paragraphen-Recherche, `$task_result`-Verkettung mit
`"id"`-Feld, einfache Einzelaufgabe, Research-vor-Code-Muster.

**Judge-Modus:** `_JUDGE_CRITIC_TRAINING_SYSTEM_PROMPT` + 4 Muster-Fokusse
(CONFIRMED bei Code/Prosa, Korrektur bei Code/Fakten) +
`_critic_response_is_noncompliant()` — spiegelt exakt die reale
Produktionsprüfung `_critic_is_noncompliant_confirmation()` aus
`graph/synthesis.py`. Reale Stichprobe (8/8 erfolgreich) bestätigte
korrekte CONFIRMED-Erkennung UND korrekte präambelfreie Direktkorrekturen
(z.B. Frage "Hauptstadt von Australien?", falsche Antwort "Sydney" im
Check → Judge korrigiert korrekt zu "Canberra", ohne Präambel).

**Gefundener und gefixter Bug:** `scripts/generate_role_sft_openrouter.py`
hat eine eigene Generierungsschleife, getrennt von
`generate_diverse_training_seeds.py`s `run_role_sft_mode` — bei der
Ersteinführung der Planner-/Judge-Logik nicht mitaktualisiert. Ein erster
Testlauf (`--role planner` über OpenRouter) meldete "8/8 erfolgreich",
lieferte aber ausschließlich generische Prosa zum selben Thema
(MoE-Video-Transcoding) statt JSON-Task-Arrays — der neue Code lief nie,
weil das Skript weiterhin den alten generischen Pfad nutzte. Erst durch
Lesen der echten Inhalte (nicht nur der Erfolgszahl) entdeckt und behoben
(Musterrotation + korrekte Parser/Templates in beide Skripte verdrahtet).

## Teil 6: Loom-Merge-Workflow (Kandidat 1) — erste echte End-to-End-Ausführung (2026-09-09)

Die 3-stufige Pipeline (`--mode loom` generieren → `generate_loom_seed_examples.py
--llm-scenarios-file` sandbox-verifizieren → `curate_coder_expert_dataset.py`
kuratieren) existierte bereits aus früherer Arbeit — hier zum ersten Mal
echt end-to-end mit den gefixten Komponenten ausgeführt.

**GLM-4.5-Air auf LUMI-G (SLURM-Job 21805142, 20 Rohversuche):** nur
6/20 geparst (Reasoning-Trace `<think>` frisst Token-Budget, keine
Reasoning-Kontrolle bei direkten vLLM-Aufrufen verfügbar). **Reale
Sandbox-Verifikation: 0 von 6 Paaren brauchbar** — entweder kompiliert der
Code nicht, oder der "Fix" behebt das Problem tatsächlich nicht. Kuration
bestätigt: 0 finale Trainingsbeispiele. Auf Nutzerentscheidung hin auf
Kimi K3 umgestellt.

**Kimi K3 über OpenRouter** (dafür `generate_role_sft_openrouter.py` um
einen `--mode loom`-Zweig erweitert, wiederverwendet dieselben
`_LOOM_GENERATION_PROMPT`/`parse_loom_output`-Bausteine): 10/10 Rohversuche
geparst, $0.175 für 10 Beispiele.

**Bei der Sandbox-Verifikation zwei reale Infrastruktur-Bugs gefunden und
gefixt:**
1. **Ein einzelner pathologischer Kandidat legt die ganze Instanz lahm.**
   Ein Kimi-K3-Kandidat ("ticket_lock_payload_handoff", vermutlich eine
   CAS-Retry-Schleife ohne sauberes Loom-Yield) ließ die Sandbox-Instanz
   hart hängen bleiben — jede nachfolgende Anfrage (auch für andere,
   unbeteiligte Kandidaten) schlug fehl, bis die Instanz neugestartet
   wurde. `generate_loom_seed_examples.py` brach vorher beim ersten Fehler
   komplett ab; jetzt wird ein einzelner Sandbox-Fehler abgefangen und
   protokolliert, die Verifikation der restlichen Kandidaten läuft weiter.
2. **tmpfs-Berechtigungsfehler nach Container-Neustart.**
   `PermissionError: [Errno 13] Permission denied: '/build/...'` — das
   `/build`-tmpfs-Mount (`exec,nosuid,size=1024m`) hatte nach
   `docker restart` falsche Berechtigungen für den Nicht-root-Container-
   User, wodurch **jede** Anfrage mit 500/Verbindungsabbruch scheiterte,
   auch bereits mehrfach bestätigt gute Kandidaten. Fix:
   `uid=1003,gid=0,mode=1770` explizit in den tmpfs-Optionen ergänzt
   (`docker-compose.loom-sandbox-remote.yml`), Container neu erstellt
   (nicht nur neugestartet). **Wichtig:** die identische tmpfs-Zeile ohne
   explizite `uid`/`gid` existiert auch in der Produktions-`docker-
   compose.yml` (`rust-loom-sandbox`-Service) — dieselbe latente Schwäche
   dort vermutlich vorhanden, nur bisher nie durch einen Neustart
   ausgelöst; nicht in dieser Session gefixt (Produktionsänderung
   außerhalb des Scopes).

**Reales Endergebnis nach beiden Fixes** (9 von 10 Kimi-K3-Kandidaten
verifiziert, der bekannte Störer ausgeschlossen): **6 von 9 Paaren zeigen
das korrekte Muster** (broken scheitert, fixed besteht) — 3 zeigen korrekt
erkannt, dass der Fix das Problem nicht behebt. Kuration:
**6 finale, sandbox-verifizierte ChatML-Trainingsbeispiele** für den
`coder`-Experten (echte Acquire/Release-Korrekturen bestätigt, 2,7-3,8KB
pro Beispiel). Der Kandidat "ticket_gate_payload_publish" bestand die
Sandbox-Prüfung **dreimal unabhängig identisch** (broken=False,
fixed=True) über verschiedene Instanzen hinweg.

## Offene Punkte (Lastenheft für die Fortsetzung)

1. ~~Planner-spezifischer `role_sft`-Modus~~ — **erledigt, siehe Teil 5**.
2. ~~Judge-spezifischer Modus~~ — **erledigt, siehe Teil 5**.
3. ~~Loom-Merge-Workflow~~ — **erledigt, siehe Teil 6** (erste echte Charge:
   6 verifizierte Beispiele; Skalierung auf mehr Rohversuche noch offen).
4. ~~Netcup-VM(s) einrichten~~ — **erledigt, siehe Teil 4** (2 VMs, 6 parallele
   Sandbox-Instanzen).
5. Produktions-`docker-compose.yml`s `rust-loom-sandbox`-tmpfs-Mount hat
   dieselbe fehlende `uid`/`gid`-Angabe wie der in Teil 6 gefundene Fehler
   in `docker-compose.loom-sandbox-remote.yml` — sollte dort ebenfalls
   ergänzt werden, aber nicht ohne separate Rücksprache (Produktionsdienst).
6. Merge-Skript: alle Quellen (LUMI-G-direkt, hochgeladene OpenRouter-Daten,
   Loom-verifizierte Beispiele) zu einer Datei pro Rolle auf LUMI-G-Scratch
   zusammenführen, VOR Beginn von Phase 3 (Training) — kein sequenzielles
   Nachtrainieren, um katastrophales Vergessen zu vermeiden.
7. `security`-Rollen-Prompt real gegen Qwen3-235B/DeepSeek-Coder-V2 testen,
   um zu klären, ob die Verweigerung auch dort auftritt.
8. Mistral-Large-Instruct-2411s MRL-Lizenzstatus für MoE-Sovereign als
   Ganzes abschließend einordnen (Forschungsprojekt vs. jede kommerzielle
   Berührung) — vom Nutzer selbst zu entscheiden, nicht rein technisch
   lösbar.
9. Skalierung des Loom-Kandidaten-Volumens (aktuell nur 1 kleine Charge
   getestet) — Kimi K3 hat sich als deutlich zuverlässiger als GLM-4.5-Air
   erwiesen (6/9 vs. 0/6 sandbox-verifiziert), sollte für die Vollskalierung
   priorisiert werden.
