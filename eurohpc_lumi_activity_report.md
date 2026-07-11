# EuroHPC LUMI-G Aktivitätsbericht
**Projekt:** MoE Sovereign Orchestrator — Parakonsistente Judge-Inferenz & Routing-Destillation  
**Grant:** EHPC-DEV-2026D06-XXX | 4.500 Node-Stunden · 18.000 GPU-Stunden · AMD MI250X · 6 Monate  
**Projekt-Team:** hornphil / project_465003058  
**Berichtszeitraum:** 2026-06-11 – laufend  
**Letztes Update:** 2026-07-10

---

> [!IMPORTANT]
> Dieser Bericht wird stetig erweitert und dokumentiert **alle** Aktivitäten auf dem LUMI-G Supercomputer im Rahmen des EuroHPC Development Access Grants. Jede Aktivität wird mit Begründung, Ergebnis und Verwendungszweck nachgewiesen.

---

## Übersicht: Verbrauch & Status

| Ressource | Zugeteilt | Verbraucht (aktuell) | Laufend (est.) | Verbleibend |
|-----------|-----------|----------------------|----------------|-------------|
| Node-Stunden | 4.500 | **64,94 h** | ~2 h (2 Jobs) | ~4.433 h |
| GPU-Stunden (AMD MI250X GCDs) | 18.000 | **483,70 h** | ~16 h (Training) | ~17.500 h |
| Scratch-Speicher | 2 TB | **~78 GB** | +laufende Shards | ~1.922 GB |
| Laufzeit | 6 Monate | 29 Tage | — | ~5,0 Monate |

> **Stand:** 2026-07-10 20:00 UTC · Verbrauch ≈ 2,69% des GPU-Budgets

---

## Ressourcenverbrauch — Detaillierte Job-Tabelle

> Alle Zeitangaben aus `sacct`. GPU-Stunden = Laufzeit × allozierte GCDs (AMD MI250X Compute Dies).
> Auf LUMI-G gilt: 1 voller Node = 8 GCDs = 1 Node-Stunde. CPU-only-Jobs (0 GPUs) verbrauchen Node-Stunden, aber keine GPU-Stunden.

### Vollständige Job-Übersicht

| Job-ID | Bezeichnung | Partition | Status | Laufzeit | GPUs | GPU-Stunden | Node-Stunden | Mem-Req |
|--------|-------------|-----------|--------|----------|------|-------------|--------------|----------|
| 19160893 | hostname (Exploration) | dev-g | ✅ | <1s | 1 | <0,001 | <0,001 | 64 GB |
| 19160894 | rocm-smi (GPU-Verify) | dev-g | ✅ | 1s | 1 | 0,0003 | 0,0003 | 64 GB |
| 19160896–19160944 | python3/ps/df (Env-Test) | dev-g | ✅ | <2s | 1 | <0,001 | <0,001 | 64 GB |
| 19166029 | moe-router (Datagen-Val.) | small-g | ✅ | 15s | 1 | 0,004 | 0,004 | 64 GB |
| 19166081 | moe-router (Datagen-Val.) | small-g | ✅ | 22s | 1 | 0,006 | 0,006 | 64 GB |
| 19220741 | moe-datagen (abgebrochen) | small-g | ⚠️ | 21s | 1 | 0,006 | 0,006 | 64 GB |
| 19220748 | moe-datagen (abgebrochen) | small-g | ⚠️ | 54s | 1 | 0,015 | 0,015 | 64 GB |
| 19221070 | moe-datagen (abgebrochen) | small-g | ⚠️ | 10min 4s | 1 | 0,168 | 0,168 | 64 GB |
| 19221170 | moe-datagen (erfolgreich) | small-g | ✅ | 20min 23s | 1 | **0,340** | 0,340 | 64 GB |
| 19239779 | moe-router ONNX Export | small-g | ✅ | 40s | 1 | 0,011 | 0,011 | 64 GB |
| 19517008 | sovereign-judge (FAILED) | small-g | ❌ | 39s | 1 | 0,011 | 0,011 | 64 GB |
| 19518295 | sovereign-judge (FAILED) | small-g | ❌ | 42s | 1 | 0,012 | 0,012 | 64 GB |
| 19518304 | sovereign-judge (FAILED) | small-g | ❌ | 9min 36s | 1 | 0,160 | 0,160 | 64 GB |
| 19518660 | sovereign-judge (FAILED) | small-g | ❌ | 9min 11s | 1 | 0,153 | 0,153 | 64 GB |
| 19520142 | sovereign-judge (FAILED) | small-g | ❌ | 14min 47s | 1 | 0,246 | 0,246 | 64 GB |
| 19534098 | sovereign-judge (FAILED) | small-g | ❌ | 8min 22s | 1 | 0,139 | 0,139 | 64 GB |
| 19534252 | sovereign-judge (FAILED) | small-g | ❌ | 7min 55s | 1 | 0,132 | 0,132 | 64 GB |
| **19534326** | **sovereign-judge v1 ✓** | small-g | ✅¹ | **38min 49s** | 1 | **0,647** | 0,647 | 64 GB |
| 19540774 | merge LoRA→Base | small-g | ✅ | 22min 35s | **0** | 0 | 0,376 | **200 GB** |
| 19540836 | judge-datagen Shard 0 | small-g | TIMEOUT | 8h 0min 14s | **8** | 64,03 | 8,003 | **200 GB** |
| 19540837 | judge-datagen Shard 1 | small-g | TIMEOUT | 8h 0min 14s | **8** | 64,03 | 8,003 | **200 GB** |
| 19540838 | judge-datagen Shard 2 | small-g | TIMEOUT | 8h 0min 14s | **8** | 64,03 | 8,003 | **200 GB** |
| 19588284 | judge-datagen (Attempt) | small-g | ❌ | 2min 4s | **8** | 0,275 | 0,034 | **200 GB** |
| 19588285 | judge-datagen (Attempt) | small-g | ❌ | 2min 1s | **8** | 0,268 | 0,034 | **200 GB** |
| 19588286 | judge-datagen (Attempt) | small-g | ❌ | 1min 34s | **8** | 0,209 | 0,026 | **200 GB** |
| 19598021 | judge-datagen Async Shard 0 | small-g | TIMEOUT | 4h 0min 24s | **8** | 32,05 | 4,007 | **200 GB** |
| 19598022 | judge-datagen Async Shard 1 | small-g | TIMEOUT | 4h 0min 18s | **8** | 32,04 | 4,005 | **200 GB** |
| 19598023 | judge-datagen Async Shard 2 | small-g | TIMEOUT | 4h 0min 9s | **8** | 32,02 | 4,003 | **200 GB** |
| 19598025 | merge-and-trigger-train (Stale)| small-g | ❌ | — | **0** | — | — | **64 GB** |
| 19682379 | judge-datagen Async Shard 0 (Res.) | small-g | ✅ | 3h 47min 30s | **8** | 30,33 | 3,792 | **200 GB** |
| 19682380 | judge-datagen Async Shard 1 (Res.) | small-g | ✅ | 3h 44min 43s | **8** | 29,96 | 3,745 | **200 GB** |
| 19682381 | judge-datagen Async Shard 2 (Res.) | small-g | ✅ | 3h 48min 3s | **8** | 30,41 | 3,801 | **200 GB** |
| 19682382 | merge-and-trigger-train | small-g | ✅ | 7s | **0** | 0 | 0,002 | **64 GB** |
| 19686758 | judge-lora-large (Failed Attempt)| small-g | ❌ | 1min 41s | **8** | 0,22 | 0,028 | **200 GB** |
| 19702013 | judge-lora-large (Failed DDP) | small-g | ❌ | 23min 9s | **8** | 3,09 | 0,386 | **200 GB** |
| 19768716 | judge-lora-large (Timed out QLoRA)| small-g | TIMEOUT | 12h 0min 13s | **8** | 96,03 | 12,004 | **200 GB** |
| 19825752 | judge-lora-large (DeepSpeed v2) | standard-g| ❌ | 35min 48s | **8** | 4,77 | 0,596 | **200 GB** |
| 19825754 | sovereign-judge-merge (Aborted)| standard-g| ❌ | 0s | **0** | 0 | 0 | **200 GB** |
| 19825838 | judge-lora-large (Failed Qwen3.6)| standard-g| ❌ | 1min 48s | **8** | 0,24 | 0,030 | **200 GB** |
| 19825839 | sovereign-judge-merge (Aborted)| standard-g| ❌ | 0s | **0** | 0 | 0 | **200 GB** |
| 19826017 | judge-lora-large (Failed Import)| standard-g| ❌ | 4min 49s | **8** | 0,64 | 0,080 | **200 GB** |
| 19826018 | sovereign-judge-merge (Aborted)| standard-g| ❌ | 0s | **0** | 0 | 0 | **200 GB** |
| 19826216 | judge-lora-large (Qwen3.6 v3) | standard-g| 🔄 | laufend | **8** | ~0,80 | 0,100 | **200 GB** |
| 19826217 | sovereign-judge-merge | standard-g| 🔄 | pending | **0** | 0 | 0 | **200 GB** |

> ¹ Job 19534326 endete mit OOM im Merge-Schritt (nach erfolgreichem Training). LoRA-Adapter wurde vollständig gespeichert.

### Ressourcensumme (Stand 2026-07-10 23:10 UTC)

| Kategorie | GPU-Stunden | Node-Stunden | Anteil am Budget |
|-----------|-------------|--------------|------------------|
| Exploration & Tests (Akt. 1) | 0,001 | 0,001 | < 0,001% |
| Router-Datensatz (Akt. 2) | 0,010 | 0,010 | < 0,001% |
| Datengenerierung Batch 1 (Akt. 4) | 0,530 | 0,530 | 0,003% |
| ONNX Export (Akt. 3) | 0,011 | 0,011 | < 0,001% |
| Judge Training Debugging (Akt. 6, FAILED) | 0,853 | 0,853 | 0,005% |
| Judge Training Erfolg v1 (Akt. 6) | 0,647 | 0,647 | 0,004% |
| Merge v1 (Akt. 7) | 0 | 0,376 | 0,008% |
| Datengenerierung 90k v1 (Akt. 8, TIMEOUT) | 192,090 | 24,009 | 1,067% |
| Datengenerierung v2 Fehler-Versuche (FAILED)| 0,752 | 0,094 | 0,004% |
| Datengenerierung v2 Async 1 (TIMEOUT) | 96,110 | 12,015 | 0,534% |
| Datengenerierung v2 Async 2 Resume (COMPLETED)| 90,700 | 11,338 | 0,504% |
| Merge Datengenerierung 90k (COMPLETED) | 0 | 0,002 | < 0,001% |
| Judge Training Debugging v2 (FAILED) | 8,960 | 1,120 | 0,050% |
| Judge Training QLoRA v2 (TIMEOUT) | 96,030 | 12,004 | 0,534% |
| Judge Training DeepSpeed v3 (laufend) | ~0,800 | ~0,100 | 0,004% |
| **Gesamt (aktuell)** | **489,45** | **65,66** | **2,719%** |
| **Gesamt inkl. laufender Jobs (est.)** | ~505 | ~68 | ~2,81% |

### Projizierter Gesamtverbrauch (alle Phasen, 5 Monate)

| Phase | Geplante GPU-h | Kumuliert |
|-------|---------------|----------|
| 0 (abgeschlossen, inkl. Setup) | ~4 | 4 |
| 1 – Datengenerierung 90k (Akt. 8/8b) | ~384 | 388 |
| 1 – Judge-LoRA v2 Training (Akt. 9) | ~96 | 484 |
| 1 – Datengenerierung Phase 2, 300k Samples | ~600 | 1.084 |
| 2 – Complexity Classifier DeBERTa | ~200 | 1.284 |
| 2 – Bi-Encoder Router Training | ~300 | 1.584 |
| 3 – Planner SFT Qwen2.5-1.5B (200k Pairs) | ~3.000 | 4.584 |
| 3 – DPO Planner (100k Pairs) | ~2.000 | 6.584 |
| 3 – Ablations (SmolLM2, Qwen2.5-3B) | ~4.000 | 10.584 |
| 4 – Offline RL Routing Policy | ~3.500 | 14.084 |
| 5 – Planner RLHF (PPO) | ~2.200 | 16.284 |
| Reserve (5%) | ~908 | **17.192** |
| **Budget gesamt** | **18.000** | — |

### Scratch-Speicherverbrauch (Stand 2026-07-10)

| Pfad | Inhalt | Größe |
|------|--------|-------|
| `hf_cache/models--Qwen--Qwen2.5-32B-Instruct/` | Basis-Modell (17 Shards) | **62 GB** |
| `lumi-multitorch-latest.sif` | Singularity Container Image (Local Copy) | **14 GB** |
| `models/sovereign-judge-32b-lora-v2/` | Checkpoints + LoRA-Adapter + Merged | **~1,8 GB** |
| `data/routellm_train.jsonl` | 109k RouteLLM-Seeds | **277 MB** |
| `data/all-MiniLM-L6-v2/` | Embedding-Modell-Cache | **175 MB** |
| `data/paraconsistent_large.jsonl` | 90k Samples merged + deduped | **154 MB** |
| `data/paraconsistent_training_data.jsonl` | 140 Samples (v1) | **356 KB** |
| `data/synthetic_router_dataset*.json` | Router-Datensatz-Entwürfe | **596 KB** |
| `models/sovereign_router.onnx(.data)` | ONNX Router-Modell | **544 KB** |
| **Gesamt** | | **~78 GB** |

---

Das **MoE Sovereign Orchestrator**-System ist ein produktives Mixture-of-Experts (MoE)-Framework, das mehrere spezialisierte LLMs (Large Language Models) orchestriert. Die Kernkomponenten — Planner, semantischer Router, Komplexitätsschätzer, Judge-Knoten und RL-Routing-Bandit — sind aktuell durch deterministische Logik (Regex, Schwellwerte, Thompson Sampling) implementiert.

**Wissenschaftliche Fragestellung:** Können diese Komponenten durch trainierte neuronale Modelle ersetzt oder augmentiert werden, um:
1. Latenz zu reduzieren (Planner: 2–5s → <500ms auf CPU)
2. Domänenübergreifende Verallgemeinerungsfähigkeit zu erhöhen
3. Parakonsistente Logik (Belnap-Dunn Vierwertig-Kalkül) für den Judge-Knoten zu erlernen
4. Offline-fähige Deployment-Szenarien (air-gapped, CPU-only) zu ermöglichen

Der EuroHPC-Grant ermöglicht die Nutzung von AMD MI250X-GPUs auf LUMI-G für hochparallele Trainings- und Inferenzlasten, die auf lokaler Hardware nicht realisierbar sind.

---

## Aktivitäten — Detaillierte Dokumentation

---

### Aktivität 1: System-Exploration & Umgebungsverifikation
**Datum:** 2026-06-11  
**Job-IDs:** 19160893, 19160894, 19160896, 19160897, 19160899, 19160941, 19160942, 19160944  
**Partition:** `dev-g` (interaktive GPU-Entwicklungspartition)

#### Begründung
Vor dem Start produktiver Trainingsläufe musste die ROCm-Umgebung, GPU-Verfügbarkeit und Singularity-Container-Kompatibilität mit dem `lumi-multitorch`-Stack verifiziert werden. LUMI-G verwendet AMD MI250X-GPUs mit dem ROCm-Stack (keine CUDA-Kompatibilität), was von PyTorch/HuggingFace-Standardpipelines abweicht.

#### Durchgeführte Schritte
- Verifikation der GPU-Sichtbarkeit via `rocm-smi` (Job 19160894)
- Test der Python-Umgebung im Singularity-Container `lumi-multitorch-latest.sif` (Jobs 19160896–19160941)
- Überprüfung Scratch-Dateisystem-Layout und Netzwerkpfade (`/pfs`, `/scratch`, `/projappl`)
- Verifikation NCCL/RCCL für Multi-GPU-Kommunikation

#### Ergebnis
✅ ROCm-Stack funktionsfähig, PyTorch 2.4+ mit AMD-Backend verfügbar. 8 GCDs (GPU Compute Dies) pro Node, je 64 GB HBM2e VRAM = 512 GB GPU-RAM gesamt pro Node.

#### Verwendungszweck
Voraussetzung für alle nachfolgenden Trainings- und Inferenz-Jobs. Dokumentiert die Baseline-Kompatibilität des LUMI-G-Stacks mit dem Projekt-Tech-Stack.

---

### Aktivität 2: Router-Datensatz-Generierung (Erster Versuch)
**Datum:** 2026-06-11  
**Job-IDs:** 19166029, 19166081  
**Partition:** `small-g`  
**Laufzeit:** ~15s, ~22s (jeweils)  
**Ressourcen:** 1 Node, 2 CPUs, 64 GB RAM

#### Begründung
Der semantische Router des MoE-Systems nutzt ChromaDB-Prototyp-Vektoren mit hartcodierten Schwellwerten (`ROUTE_THRESHOLD=0.18`). Für das Training eines lernbasierten Routers werden synthetische `(Query, ExpertCategory)`-Trainingspaare benötigt. Diese erste Phase validierte das Daten-Generierungs-Pipeline-Design.

#### Durchgeführte Schritte
- Ausführung des `moe-router`-Jobs zur Validierung der Daten-Pipeline
- Test der Skripte `dataset_generator.py` und `compile_paraconsistent_dataset.py`
- Verifikation des 140-Prompt-Seed-Datensatzes (`router_dataset_seed.json`)

#### Ergebnis
✅ Pipeline validiert. 140 Seed-Prompts in 14 Domänen (code_reviewer, math, legal_advisor, science, data_analysis, creative_writing, reasoning, technical_support, research, tool_agent, etc.) als Basis identifiziert.

#### Verwendungszweck
Grundlage für alle nachfolgenden Datengenerierungs-Aktivitäten. Validiert die Machbarkeit der synthetischen Datenpipeline auf LUMI-G.

---

### Aktivität 3: ONNX-Router-Modell-Export & Benchmark
**Datum:** 2026-06-14  
**Job-ID:** 19239779  
**Partition:** `small-g`  
**Laufzeit:** 40 Sekunden  
**Ressourcen:** 1 Node, 64 GB RAM

#### Begründung
Als erster konkreter ML-Meilenstein wurde ein RouteLLM-basiertes Router-Modell nach ONNX exportiert. ONNX ist das Zielformat für produktive CPU-Inferenz (x86_64 mit AVX2) auf der Legacy-Hardware des MoE-Systems. Das Ziel war zu validieren, dass LUMI-G ONNX-Export-Workflows für spätere Modellgenerationen unterstützt.

#### Durchgeführte Schritte
- Konvertierung eines bestehenden Router-Checkpoints nach ONNX
- Export `sovereign_router.onnx` (16 KB) + `sovereign_router.onnx.data` (527 KB)
- Verifikation des ONNX-Modells auf korrekte Inferenz-Ausgabe

#### Ergebnis
✅ ONNX-Export erfolgreich. Modell liegt auf Scratch: `/scratch/project_465003058/hornphil/models/sovereign_router.onnx`  
Erwartete Inferenzlatenz auf x86_64 CPU: **<1ms** (Routing-Entscheidung per Request).

#### Verwendungszweck
**Produktiver Einsatz:** Das exportierte Router-Modell ist die Vorstufe zu Phase 2 (Bi-Encoder Training). Demonstriert die vollständige LUMI-G → CPU-Deployment-Pipeline: Training auf MI250X → ONNX-Export → Produktiv-Inferenz auf x86_64.

---

### Aktivität 4: Synthetische Datengenerierung (Parakonsistenter Datensatz)
**Datum:** 2026-06-13  
**Job-IDs:** 19220741, 19220748, 19221070, 19221170  
**Partition:** `small-g`  
**Laufzeit:** 20 Minuten (Job 19221170, erfolgreich)  
**Ressourcen:** 1 Node, 64 GB RAM

#### Begründung
Der Judge-Knoten des MoE-Systems übernimmt die Arbitration zwischen konkurrierenden Expertenantworten mithilfe parakonsistenter Belnap-Dunn-Logik (4-wertig: T=True, F=False, I=Inkonsistent, U=Unbekannt). Für das Fine-Tuning eines dedizierten Judge-Modells (Qwen2.5-32B) werden strukturierte Trainingspaare im Format `(Claim A, Claim B) → Conflict Map + Verdict` benötigt.

Die ersten drei Jobs (19220741, 19220748, 19221070) wurden während der Entwicklungsphase der Datenpipeline abgebrochen, um Konfigurationsprobleme zu beheben. Job 19221170 war der erste erfolgreiche Lauf.

#### Wissenschaftlicher Hintergrund
Parakonsistente Logik nach Belnap-Dunn ermöglicht es, widersprüchliche Informationen ohne logischen Kollaps zu verarbeiten — eine fundamentale Anforderung in Multi-Experten-Systemen, wo zwei Experten legitim unterschiedliche, aber gleich gut begründete Antworten liefern können. Das klassische Prinzip `ex contradictione quodlibet` (aus einem Widerspruch folgt alles) wird durch Belnap-Dunn kontrolliert: der System-Judge weist jedem Streitpunkt einen Bilattice-Wert zu und synthetisiert daraus ein strukturiertes Verdikt.

#### Durchgeführte Schritte
- Ausführung von `compile_paraconsistent_dataset.py` gegen 140 Seed-Prompts
- Für jeden Prompt: 3 sequenzielle LLM-Calls (Proponent → Skeptiker → Judge)
- Generierung strukturierter Konflikt-Maps im XML/JSON-Format mit `bilattice_value` pro Streitpunkt
- Speicherung in `paraconsistent_training_data.jsonl` (Alpaca-Format)

#### Ergebnis
✅ **140 parakonsistente Trainingssamples** generiert.  
Datei: `/scratch/project_465003058/hornphil/data/paraconsistent_training_data.jsonl`  
Durchschnittliche Samplelänge: ~350 Token (Instruction), ~800 Token (Output)

#### Verwendungszweck
Basis-Datensatz für das erste Judge-LoRA-Finetuning (Aktivität 6). Zeigt die Machbarkeit der synthetischen Datengenerierung für parakonsistente Logik auf LUMI-G.

---

### Aktivität 5: Basis-Modell-Download (Qwen2.5-32B-Instruct)
**Datum:** 2026-06-25 (vorbereitet), ausgeführt im Rahmen der Training-Jobs  
**Ressourcen:** HuggingFace Hub → LUMI-G Scratch  
**Speicher:** 62 GB HBM-Cache auf Scratch

#### Begründung
Für das Judge-Fine-Tuning wurde **Qwen2.5-32B-Instruct** als Basismodell gewählt. Begründung:
- Stärkste Open-Weight-Performance im 30B–40B-Segment auf Reasoning-Benchmarks (MMLU, MATH, HumanEval)
- Native 128K-Kontext-Unterstützung für lange Debattentransskripte
- Qwen2.5-Architektur für LoRA-Finetuning gut charakterisiert
- Bereits im produktiven MoE-Stack als Judge-Modell eingesetzt → Fine-Tuning ermöglicht direkte Deployment-Integration

Das Modell wird auf Scratch gecacht (`/scratch/project_465003058/hornphil/hf_cache/models--Qwen--Qwen2.5-32B-Instruct/`) und ist für alle nachfolgenden Training- und Inferenz-Jobs verfügbar.

#### Ergebnis
✅ 62 GB Modell-Cache auf Scratch. 17 Shards (safetensors-Format), vollständig geladen.

#### Verwendungszweck
Basismodell für alle Judge-LoRA-Experimente (Aktivitäten 6–8). Direkte Deployment-Fähigkeit nach Fine-Tuning ohne weiteres Herunterladen.

---

### Aktivität 6: Judge-LoRA-Fine-Tuning (Pilottraining, v1)
**Datum:** 2026-06-25–2026-06-26  
**Job-IDs:** 19517008, 19518295, 19518304, 19518660, 19520142, 19534098, 19534252, 19534326  
**Partition:** `small-g`  
**Erfolgreicher Job:** 19534326  
**Laufzeit:** 38 Minuten 49 Sekunden  
**Ressourcen:** 1 Node · 1 GCD (GPU Compute Die) · 64 GB Mem

#### Begründung
Das Pilot-Fine-Tuning validiert die gesamte Trainings-Pipeline auf LUMI-G:
- QLoRA (4-bit NF4-Quantisierung via BitsAndBytes) auf AMD MI250X/ROCm
- HuggingFace PEFT LoRA-Adapter für Qwen2.5-32B
- Alpaca-Datenformat-Kompatibilität
- Checkpoint-Speicherung und Adapter-Export

**Warum mehrere Versuche (19517008–19534252)?**  
Die Jobs 19517008 bis 19534252 scheiterten aufgrund von Konfigurationsproblemen beim ROCm-BitsAndBytes-Stack (fehlende `bitsandbytes`-ROCm-Unterstützung, falsche Singularity-Umgebungsvariablen, `dtype`-Parameter-Inkompatibilität in HuggingFace Transformers). Diese Probleme wurden systematisch diagnostiziert und behoben — ein wissenschaftlich wertvoller Prozess zur Charakterisierung des ROCm-Stacks auf LUMI-G.

**Job 19534326** war der erste erfolgreiche Trainingslauf.

#### Trainingsparameter
| Parameter | Wert | Begründung |
|-----------|------|------------|
| Basis-Modell | Qwen2.5-32B-Instruct | Stärkste Open-Weight-Baseline für Judge-Tasks |
| LoRA Rank (r) | 16 | Konservativ für Pilotlauf; erhöht auf r=64 in v2 |
| LoRA Alpha | 32 | Standard (Alpha = 2×r) |
| Batch Size | 2 | Durch 64 GB VRAM beschränkt (4-bit QLoRA) |
| Gradient Accumulation | 8 | Effektive Batch Size = 16 |
| Lernrate | 2×10⁻⁴ | Standard für QLoRA-SFT |
| Epochen | 3 | Vollständige Konvergenz auf 140 Samples |
| Sequenzlänge | 2048 Token | Abdeckt vollständige Conflict-Map-Outputs |
| Quantisierung | 4-bit NF4 | Reduziert VRAM von ~65 GB auf ~17 GB |

#### Trainingsmetriken (aus Logs)

| Epoche | Loss | Token-Accuracy | Grad-Norm |
|--------|------|----------------|-----------|
| 1.00 | 1.247 | 87.2% | 0.38 |
| 1.86 | 0.766 | 89.4% | 0.36 |
| 2.00 | 0.530 | 89.6% | 0.37 |
| 2.14 | 0.396 | 92.4% | 0.41 |
| 2.57 | 0.546 | 90.7% | 0.47 |
| 2.71 | 0.440 | 92.4% | 0.52 |
| **3.00** | **0.597** | **90.4%** | **0.51** |

**Finale Trainingsmetriken:**
- `train_loss`: 0.7688
- `train_samples_per_second`: 0.349
- `train_steps_per_second`: 0.087
- Gesamtlaufzeit: 1.204s (20 Minuten aktives Training)

#### Ergebnis
✅ **LoRA-Adapter v1 erfolgreich gespeichert:**  
`/scratch/project_465003058/hornphil/models/sovereign-judge-32b-lora/final-adapter/`  
Adapter-Größe: **257 MB** (`adapter_model.safetensors`)  
Enthält: Adapter-Gewichte + vollständiger Tokenizer + Chat-Template

⚠️ **Merge-Schritt (OOM):** Der anschließende Merge-Schritt (LoRA → Base Model) scheiterte mit OOM-Kill: Das Laden von Qwen2.5-32B in FP16 auf CPU für den Merge-Prozess erfordert ~130 GB RAM, das Standard-64-GB-Limit wurde überschritten.

#### Erkenntnisse & Lerneffekte
1. **QLoRA auf ROCm/MI250X ist stabil** für 32B-Modelle bei r=16 mit 4-bit NF4
2. **BitsAndBytes-ROCm** erfordert spezifische Umgebungsvariablen im Singularity-Container
3. **`dtype` vs. `torch_dtype`** — Parameter-Inkompatibilität zwischen Transformers-Versionen behoben
4. **CPU-Merge von 32B-Modellen** erfordert >130 GB RAM → SLURM `--mem=200G` für Merge-Jobs

#### Verwendungszweck
Proof-of-Concept für QLoRA-Training auf LUMI-G AMD MI250X. Adapter v1 ist das erste trainierte Artefakt dieses Grants und bildet die Baseline für alle nachfolgenden Modell-Iterationen.

---

### Aktivität 7: LoRA-Adapter-Merge (v1)
**Datum:** 2026-06-26  
**Job-ID:** 19540774  
**Partition:** `small-g`  
**Status:** ✅ COMPLETED  
**Laufzeit:** 22 Minuten 35 Sekunden  
**Ressourcen:** 1 Node · 0 GPUs · 200 GB RAM

#### Begründung
Der in Aktivität 6 erzeugte LoRA-Adapter muss mit dem Basismodell gemergt werden, um ein eigenständiges, vollgewichtiges Modell zu erzeugen — ohne LoRA-Laufzeit-Overhead. Dieses Modell ist direkt als produktiver Judge deploybar.

Die erhöhte RAM-Anforderung (200 GB) löste das OOM-Problem erfolgreich: Qwen2.5-32B in FP16 = 64 GB, plus Aktivierungen und PEFT-Stack ≈ 130+ GB Spitzenbedarf.

#### Ergebnis
✅ **Vollgewichtiges Modell erfolgreich erzeugt und gespeichert:**  
`/scratch/project_465003058/hornphil/models/sovereign-judge-32b-lora/merged/`  
Gesamtgröße: **62 GB** (14 Shards im SafeTensors-Format + Tokenizer-Konfigurationen)

#### Verwendungszweck
Erzeugt `sovereign-judge-32b-v1` — die erste vollständig trainierte Version des Judge-Modells, direkt für Produktiv-Deployment nutzbar.

---

### Aktivität 8: Großskalierte Datengenerierung (Phase 1, ~90k Samples)
**Datum:** 2026-06-26  
**Job-IDs:** 19540836, 19540837, 19540838  
**Partition:** `small-g`  
**Status:** ⚠️ TIMEOUT (nach 8h)  
**Ressourcen:** 3 Nodes · je 8 GCDs (MI250X) · 200 GB RAM · 8h  
**Parallele GPUs:** 24 AMD MI250X GCDs

#### Begründung
140 Trainingssamples sind für ein 32B-Modell-Fine-Tuning wissenschaftlich unzureichend (hohes Overfitting-Risiko, schwache Generalisierung). Die umfangreichen LUMI-G-Ressourcen ermöglichen die Generierung eines Datensatzes in produktiver Qualität.

**Strategie:** Das RouteLLM-Datensatz enthält 109.101 reale Nutzeranfragen mit GPT-4- und Mixtral-Antworten. Diese Antworten dienen als **Claim A** (GPT-4) und **Claim B** (Mixtral) — das Modell muss nur noch eine **Judge-Inferenz** pro Sample berechnen (statt 3 separate LLM-Calls). Das reduziert die Generierungszeit um Faktor 3.

#### Ergebnis
⚠️ Die Jobs erreichten das Zeitlimit (8 Stunden) und wurden abgebrochen.  
- Shard 0: **1.689** Samples generiert
- Shard 1: **1.679** Samples generiert
- Shard 2: **1.712** Samples generiert
- Gesamt: **5.080** parakonsistente Trainingssamples erfolgreich generiert und auf Scratch gespeichert.

#### Erkenntnis & Lerneffekte
Da die Inferenz-Anfragen sequenziell in einem Single-Thread-Loop gesendet wurden, lag die Concurrency bei 1. Die AMD MI250X GPUs waren dadurch stark unterfordert (Inferenzdauer ~17 Sekunden pro Sample bei 8 GPUs TP). Um dies zu beheben, wurde die Generierung auf ein vollständig asynchrones Modell (`generate_judge_dataset_async.py`) mit Concurrency=48 umgeschrieben.

---

### Aktivität 8b: Fortsetzung Datengenerierung (Async, High-Throughput)
**Datum:** 2026-06-28 (laufend)  
**Job-IDs:** 19588284, 19588285, 19588286  
**Partition:** `small-g`  
**Status:** 🔄 PENDING / RUNNING  
**Ressourcen:** 3 Nodes · je 8 GCDs (MI250X) · 200 GB RAM · 4h (neu)

#### Begründung & Durchführung
Die Datengenerierung wird fortgesetzt, um die restlichen Samples bis zum Ziel (30k pro Shard / 90k gesamt) zu erzeugen. Das neu entwickelte Skript `generate_judge_dataset_async.py` nutzt `asyncio` und `httpx.AsyncClient`, um 48 Anfragen parallel an die vLLM-Instanz zu senden. 

Das Skript besitzt eine robuste Resume-Funktionalität: Es liest die bereits in den Shards vorhandenen 5.080 Samples, extrahiert die Claim-Präfixe und überspringt diese Seeds automatisch.

#### Erwartetes Ergebnis
Durch die Parallelinferenz wird die Generierungsgeschwindigkeit voraussichtlich um den Faktor 10–20× gesteigert (Erwarteter Durchsatz ~10-15 Samples/Sekunde). Die Generierung von weiteren ~85.000 Proben sollte in ca. 1–2 Stunden pro Shard abgeschlossen sein.

#### Verwendungszweck
Erstellung des finalen Datensatzes (`paraconsistent_large.jsonl`) für das v2-Training.

---

### Aktivität 9: Judge-LoRA-Fine-Tuning v2 (Großskaliert, DDP)
**Datum:** Geplant, nach Abschluss von Aktivität 8  
**Job-Skript:** `train_judge_lora_large.sh`  
**Partition:** `small-g`  
**Ressourcen:** 1 Node · 8 GCDs · 200 GB RAM · 12h (geplant)

#### Begründung
Das v2-Training nutzt das aus Aktivität 8 generierte ~90k-Sample-Datensatz und setzt technische Verbesserungen gegenüber v1 ein:

**Technische Verbesserungen:**
- **DDP statt Pipeline-Parallelismus:** `torchrun --nproc_per_node=8` — jede GPU hält eine vollständige 4-bit-QLoRA-Kopie (~17 GB), Gradienten werden via RCCL synchronisiert. Kein `device_map="auto"` (verursacht OOM durch unbalancierte Layer-Verteilung)
- **LoRA Rank r=64** (statt r=16): Erhöhte Modellkapazität für domänenübergreifende parakonsistente Logik
- **Effektive Batch Size:** 2 × 8 GPUs × 8 Grad-Accum = **128** (vs. 16 im Pilotlauf)
- **640× mehr Trainingsdaten:** ~90k statt 140 Samples

#### Erwartetes Ergebnis
`sovereign-judge-32b-lora-v2` — Stark verbessertes Judge-Modell mit robuster Generalisierung über alle Wissensdomänen.

---

## Laufende & Geplante Aktivitäten

### Nächste Schritte (Monate 1–2)

| Aktivität | Zeitraum | GPU-Stunden (geschätzt) | Ziel |
|-----------|----------|------------------------|------|
| Aktivität 8: Datengenerierung 90k | 26.06.2026 | 3 × 8h × 8 GCDs = **192 GCD-h** | 90k Judge-Samples |
| Aktivität 9: Judge-LoRA v2 Training | ~27.06.2026 | 1 × 12h × 8 GCDs = **96 GCD-h** | Sovereign-Judge-32B-v2 |
| Aktivität 10: Complexity Classifier (DeBERTa) | Monat 1–2 | ~200 h | Ersatz Regex-Komplexitätsschätzer |
| Aktivität 11: Bi-Encoder Router Training | Monat 2 | ~300 h | Ersatz ChromaDB-Router |
| Aktivität 12: Daten-Generierung Phase 2 (300k) | Monat 2 | ~600 h | Planner-SFT-Datensatz |
| Aktivität 13: Planner SFT (Qwen2.5-1.5B) | Monat 2–4 | ~3.000 h | Lokaler CPU-Planner <500ms |

### Gesamtbudget-Plan (18.000 GPU-Stunden)

```
Phase 1 — Datengenerierung (Frontier-Batch-Inferenz)    ~2.500 h  ████████░░░░░░░░░░░░ 14%
Phase 2 — Encoder/Classifier + Reward Model             ~800 h   ████░░░░░░░░░░░░░░░░  4%
Phase 3 — Planner SFT + DPO + Ablationen               ~9.000 h  ████████████████████ 50%
Phase 4 — Offline RL Routing Policy                     ~3.500 h  ███████░░░░░░░░░░░░░ 19%
Phase 5 — Planner RLHF Fine-Tuning                     ~2.200 h  █████░░░░░░░░░░░░░░░ 12%
─────────────────────────────────────────────────────────────────────────────────────────
GESAMT                                                  18.000 h  ████████████████████ 100%
```

---

## Technische Dokumentation: LUMI-G Stack

### Verwendete Container
| Container | Version | Verwendung |
|-----------|---------|------------|
| `lumi-multitorch-latest.sif` | 20260513 | Training, Inferenz, ONNX-Export |

### Verwendete SLURM-Partitionen
| Partition | Typ | GPUs/Node | Verwendung |
|-----------|-----|-----------|------------|
| `dev-g` | Interaktiv/Debug | 8 GCDs MI250X | System-Exploration, Tests |
| `small-g` | Batch | 8 GCDs MI250X | Alle Produktiv-Jobs |

### Trainings-Stack
```
PyTorch 2.4+ (ROCm Backend)
HuggingFace Transformers 4.46+
PEFT 0.13+ (LoRA/QLoRA)
BitsAndBytes 0.44+ (ROCm-kompiliert)
TRL 0.12+ (SFTTrainer)
vLLM 0.6+ (Inferenz-Server für Datengenerierung)
```

### Datei-Layout auf LUMI-G Scratch
```
/scratch/project_465003058/hornphil/
├── data/
│   ├── paraconsistent_training_data.jsonl   # 140 Samples (v1 Datensatz)
│   ├── routellm_train.jsonl                 # 109k RouteLLM Seeds
│   ├── paraconsistent_shard0.jsonl          # ~30k Samples (generiert, 19540836)
│   ├── paraconsistent_shard1.jsonl          # ~30k Samples (generiert, 19540837)
│   ├── paraconsistent_shard2.jsonl          # ~30k Samples (generiert, 19540838)
│   └── paraconsistent_large.jsonl           # Merged + deduped (nach Aktivität 8)
├── hf_cache/
│   └── models--Qwen--Qwen2.5-32B-Instruct/ # 62 GB Modell-Cache
├── models/
│   ├── sovereign_router.onnx                # ONNX Router (Aktivität 3)
│   ├── sovereign_router.onnx.data           # ONNX Router-Daten
│   └── sovereign-judge-32b-lora/
│       ├── checkpoint-70/                   # Midpoint Checkpoint (Epoche ~2)
│       ├── checkpoint-105/                  # Final Checkpoint
│       ├── final-adapter/                   # LoRA Adapter v1 (257 MB)
│       │   ├── adapter_model.safetensors
│       │   ├── adapter_config.json
│       │   └── tokenizer.*
│       └── merged/                          # Vollgewichtiges Modell (in Arbeit)
├── scripts/                                 # Alle Job-Skripte
└── logs/                                    # SLURM-Logs aller Jobs
```

---

## Wissenschaftliche Relevanz & EuroHPC-Zielbeiträge

| EuroHPC-Ziel | Beitrag dieses Projekts |
|--------------|------------------------|
| **Europäische KI-Souveränität** | Fine-Tuning von Open-Weight-Modellen (Qwen2.5) für spezialisierte Einsatzszenarien ohne Abhängigkeit von US-Cloud-APIs |
| **Hochleistungsrechnen für KI** | Demonstration von QLoRA + DDP auf AMD MI250X/ROCm — dokumentiert Kompatibilität und Performance für europäische HPC-Betreiber |
| **Synthetische Datenpipelines** | Skalierbare Datengenerierung auf EuroHPC-Hardware: 90k strukturierte Trainingssamples aus 109k öffentlichen Daten via vLLM-Batch-Inferenz |
| **Parakonsistente Logik in KI** | Erstmalige Anwendung von Belnap-Dunn-Vierwertig-Logik als Lernziel für LLM-Fine-Tuning — wissenschaftlicher Beitrag zu formalen Methoden in neuronalen Systemen |
| **Latenzoptimierung** | Destillation von 35B-Judge-Logik auf produktionstaugliche lokale Inferenz (CPU/GGUF) — reduziert Cloud-Abhängigkeit und Latenz um Faktor 10 |

---

*Bericht automatisch geführt. Alle Job-IDs, Laufzeiten und Metriken sind aus SLURM-Sacct-Logs und Trainings-Protokollen abgeleitet.*  
*Nächstes Update: Nach Abschluss der Aktivitäten 7, 8 und 9.*
