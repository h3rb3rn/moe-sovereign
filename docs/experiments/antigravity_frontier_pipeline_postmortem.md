# Postmortem: Antigravity CLI "Frontier Teacher Pipeline" — geplant, nie ausgeführt

**Status:** research / postmortem (AGENTS.md §9: dies beschreibt einen vergangenen
Zustand, keine aktuelle oder validierte Produktionsaussage)
**Datum der Untersuchung:** 2026-09-01/02
**Methode:** Cross-Referenzierung von Antigravity-CLI-Sitzungsdatenbanken
(`~/.gemini/antigravity-cli/conversations/*.db`, `~/.gemini/antigravity-cli/brain/*/*.md`)
gegen die tatsächlich auf LUMI-G-Scratch vorhandenen SLURM-Skripte und
Trainingsdateien, durchgeführt in einer Claude-Code-Session auf explizite
Nutzeranfrage ("gehe durch die agy Sessions durch").

## Zusammenfassung

Antigravity CLI (Gemini 3.6 Flash, Agent-Owner mehrerer `AGENT_LASTENHEFT.md`-
Tasks) hat am 15./16.08.2026 einen vollständigen, professionell wirkenden
Trainingsplan für alle 10 MoE-Sovereign-Modelle (Planner, 8 Experten, Judge)
erstellt — mit konkreten 2026er-Frontier-Modellen als Teacher pro Rolle,
GPU-Stunden-Budget und SLURM-Gantt-Diagramm. **Dieser Plan wurde nachweislich
nie ausgeführt.** Die tatsächlich für das reale Training verwendeten
Datensätze stammen stattdessen aus einem separaten, LLM-freien
Template-Generator mit katastrophal geringer Diversität (siehe
`docs/experiments/lumig_posttraining_candidates.md` und die Live-Messung
unten). Es gibt keine Dokumentation eines expliziten Entscheids, den
Frontier-Plan zu verwerfen — der Ersatz geschah offenbar stillschweigend.

## Der geplante Zustand: "LUMI-G Masterplan 2026: Next-Gen Frontier Teacher Pipeline"

Quelle: `~/.gemini/antigravity-cli/brain/47a63447-e16d-4d76-bc0c-4ba473beaf86/lumi_g_frontier_masterplan_2026.md`
(geschrieben 2026-08-15 23:55, Antigravity-Sitzungs-DB
`47a63447-e16d-4d76-bc0c-4ba473beaf86.db`, 6.550 Treffer für "LUMI-G" in
dieser einen Sitzung).

Vorgesehene Teacher-Modell-Zuordnung pro Komponente:

| Rolle | Primärer Teacher | Sekundär (DPO) | Ziel-Samples |
|---|---|---|---|
| Meta-Planner | `meta-llama/Llama-3.1-405B` | `THUDM/GLM-5.2` | 100.000 |
| expert-coder | `deepseek-ai/DeepSeek-Coder-V2` (236B) | `DeepSeek-V3` | 150.000 |
| expert-precision | `Qwen/Qwen2.5-Math-72B` | `Nemotron-70B` | 120.000 |
| expert-graphrag | `moonshotai/Kimi-k3` (2M Kontext) | `Llama-3.1-405B` | 120.000 |
| expert-governance | `mistralai/Mistral-Large-2407` (123B) | `Llama-3.1-405B` | 100.000 |
| expert-research | `moonshotai/Kimi-k3` | `Nemotron-70B` | 100.000 |
| expert-security | `deepseek-ai/DeepSeek-V3` | `Mistral-Large` | 100.000 |
| expert-datainfra | `deepseek-ai/DeepSeek-Coder-V2` (236B) | `Qwen2.5-72B` | 100.000 |
| expert-omni | `meta-llama/Llama-3.1-405B` | `Nemotron-70B` | 110.000 |
| sovereign-judge | `Llama-3.1-405B` + `Nemotron-70B` | Z3 SMT Solver | 100.000 |

Vorgesehener 4-Stufen-SLURM-Ablauf (Gantt-Diagramm im Originaldokument):
Stufe 1 Synthese (2 Nodes, 14h, 224 GPU-h) → Stufe 2 Nemotron-3-Ultra-DPO-
Scoring (1 Node, 4h, 32 GPU-h) → Stufe 3 paralleles 4-Node-Training (6h,
192 GPU-h) → Stufe 4 CPU-Merge/GGUF-Export (1,5h). Gesamt: ~449 GPU-h von
18.000 GPU-h Grant-Budget (~2,5 %).

Der Plan ist inhaltlich fundiert und intern konsistent (korrekte
Modellgrößen, plausible Rollen-zu-Domäne-Zuordnung, realistisches
GPU-Stunden-Budget) — es handelt sich nicht um eine offensichtlich
unseriöse Fantasie, sondern um einen durchdachten, aber folgenlosen Plan.

## Der tatsächliche Zustand: Template-Generator ohne LLM

Live in dieser Session verifiziert (siehe `docs/experiments/lumig_posttraining_candidates.md`
für den vollen Befund): Die Datei, die tatsächlich `slurm/lumig_expert_ensemble_pipeline.slurm`
für Planner und alle 8 Experten füttert (`dataset_expert_{role}_*.jsonl` auf
`/scratch/project_465003058/hornphil/datasets/`), stammt aus
`scripts/generate_expert_ensemble_datasets.py` — einem reinen
Python-Skript ohne jeden Modell-/API-Aufruf (`random.choice()` über 3-5
hartkodierte Themen pro Rolle, `random.seed(42)`).

Gemessene Diversität (vollständige Dateien, 2026-09-01/02):

| Datei | Zeilen | Echte einzigartige Instruktionen (90 % Nicht-Anchor-Anteil) |
|---|---|---|
| `dataset_expert_coder_150k.jsonl` | 150.000 | 3 |
| `dataset_expert_planner_100k.jsonl` | 100.000 | 4 |
| `dataset_expert_{datainfra,governance,graphrag,judge,research,security}_*.jsonl` | je 100-120k | 1 (jeweils) |
| `dataset_expert_omni_120k.jsonl` | 120.000 | 1 |
| `dataset_expert_precision_120k.jsonl` | 120.000 | ~108.000 (divers nur durch randomisierte IP/VLSM-Zahlenwerte, nicht durch ein LLM) |

Keines der im Frontier-Plan genannten Modelle (DeepSeek-Coder-V2, Kimi-K3,
Qwen2.5-Math-72B, Mistral-Large-2407, Nemotron-70B, Llama-3.1-405B) taucht
in irgendeinem tatsächlich vorhandenen SLURM-Skript, Python-Skript oder
Trainingsdatensatz auf diesem Cluster auf.

Konkretes Beispiel (Planner, alle 4 Instruktionen inhaltlich geprüft):
vier unterschiedliche Themen-Prompts ("Distributed Raft Consensus
Cluster", "DSGVO-Compliant GraphRAG Store", "VLSM Partitioning Engine",
"High-Throughput eBPF Telemetry") erhalten **alle** identisch denselben
3-Task-DAG als Zielantwort (graphrag→coder→…), unabhängig vom Thema.

## Kausaler Zusammenhang mit den Scientific-Benchmark-Befunden

Diese Messung root-causet Kandidat 2 (Planner-Task-Fabrikation,
`docs/experiments/lumig_posttraining_candidates.md`) direkt und mechanisch:
Der Planner wurde nicht "kreativ halluzinierend" trainiert, sondern auf nur
4 auswendig gelernte (Prompt, Plan)-Paare, deren Zielantwort systematisch
unabhängig vom tatsächlichen Prompt-Thema ist. Die im Benchmark beobachtete
Tendenz zu Netzwerk-/Security-/Compliance-Themen ist plausibel auf die
konkreten 3-5 hartkodierten Themen-Pools in `generate_expert_ensemble_datasets.py`
zurückzuführen, nicht auf eine intrinsische Modellgrenze des 4B-Architektur.
Dieselbe Erklärung deckt vermutlich weite Teile der übrigen 7 Experten ab
(je nur 1 einzigartiges Trainingsbeispiel), auch wenn deren inhaltliche
Prüfung im Detail noch aussteht.

## Verwandtes Fail-Muster im selben Projekt

`slurm/lumig_job1_dataset_gen.slurm` zeigt dieselbe Fail-Signatur in
kleinerem Maßstab: Das Skript lädt reale HuggingFace-Modellgewichte
(`deepseek-ai/DeepSeek-V4-Flash`) herunter, "generiert" dann aber nur
hartkodierte Platzhalter-Records (`'prompt': f'Synthetic CoT reasoning
prompt {i}'`, `'smt_proof': 'SAT'`) ohne einen einzigen Modellaufruf. Beide
Fälle teilen dasselbe Muster: ein Artefakt, das äußerlich (Dateiname,
Umfang, referenzierte Infrastruktur) wie eine echte, funktionierende
LLM-Generierungspipeline aussieht, es aber nicht ist — ohne jede
Kennzeichnung als Stub/Platzhalter im Code selbst.

## Offene Fragen (nicht in dieser Session geklärt)

- Wurde der Frontier-Plan bewusst verworfen (z. B. wegen Kosten/API-Zugriff
  für Kimi-K3/Mistral-Large/GLM-5.2) und die Entscheidung nirgends
  festgehalten, oder ist der Ersatz durch den Template-Generator ein reiner
  Implementierungs-Bug (Antigravity hat geplant, aber ein anderer Agent oder
  ein späterer Lauf hat versehentlich den einfacheren Generator verdrahtet)?
- Wurden Stufe 2-4 des Frontier-Plans (DPO-Scoring, Paralleltraining,
  GGUF-Export) jemals in irgendeiner Form angestoßen, auch nur teilweise?
- Sind die übrigen 7 "1-Template"-Experten-Korpora inhaltlich ebenso
  irrelevant/themenfremd wie das geprüfte Planner-Beispiel? (Noch nicht
  einzeln verifiziert, siehe "Alle 10 Korpora inhaltlich prüfen" als
  offene Option aus der vorherigen Diskussion.)

## Referenzen

- Antigravity-Artefakt: `~/.gemini/antigravity-cli/brain/47a63447-e16d-4d76-bc0c-4ba473beaf86/lumi_g_frontier_masterplan_2026.md`
- Antigravity-Sitzungs-DBs mit Kimi-K3-Erwähnung: `47a63447-e16d-4d76-bc0c-4ba473beaf86.db`, `38a528ea-1372-4011-93ef-2d753a22a104.db`
- Tatsächlicher Generator: `scripts/generate_expert_ensemble_datasets.py`
- Verwandter Fund: `slurm/lumig_job1_dataset_gen.slurm`
- Vollständige Kandidatenliste: `docs/experiments/lumig_posttraining_candidates.md`
