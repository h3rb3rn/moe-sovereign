# Status Log — agy (Google Antigravity CLI)

Append-only. Newest entry at the bottom. Never delete prior entries — they
are the resumability record for this protocol (see `AGENT_LASTENHEFT.md`
Section 0).

Each entry format:

```
## <UTC timestamp> — <TASK-ID> — <state>
Plan / progress:
- ...
Pre-conditions verified:
- ...
Notes:
- ...
```

`<state>` is one of: `starting`, `in_progress`, `checkpoint`, `blocked`, `done`, `aborted`.

---

## 2026-06-12T09:05Z — VRAM-CTX-CLAMP — done

Plan / progress:
- Context window overflow fix: JUDGE_NUM_CTX war 98304, gesenkt auf 32768 (laut .env Kommentar-Budget).
- `context_budget.py`: DB-Metadaten überschreiben jetzt `/api/ps` Abfragen in `get_model_ctx_async()`.
  llama3.3-70b-ctx4k:latest lädt nun mit ctx=4096 statt 98304.
- `services/inference.py`: `_judge_model_kw()` / `_planner_model_kw()` klemmen num_ctx auf safe-limit.
- `graph/expert.py`: Expert-Context-Pinning respektiert DB-basierten Safe-Limit.
- `services/dynamic_router.py`: Kompilierte Templates enthalten jetzt planner_num_ctx / judge_num_ctx.
- `main.py`: CC-Tool Warmup + Keepalive-Loop klemmen auf Modell-Limit.
- `.env`: POLICY_LOG_PATH=/app/logs/policy_training.jsonl hinzugefügt (Fix für Bug A in Container).
- Container rebuilt + restarted.
Pre-conditions verified:
- N04-RTX (60GB VRAM), N11-M10 (32GB VRAM) — beide Nodes erreichbar.
- langgraph-orchestrator container healthy nach Rebuild.
- CC tool warmup bestätigt: qwen3.6:35b num_ctx=32768 (log: "CC tool model warmup done").
- VRAM N04-RTX nach Warmup: qwen3.6:35b 22.9GB@ctx=32768 (vorher 29GB@ctx=98304).
- llama3.3-70b-ctx4k:latest bei E2E Test: 43.0GB@ctx=4096 (vorher 53.6GB@ctx=98304). -10GB gespart!
- E2E Test (moe-auto, Quicksort): HTTP 200, vollständige Antwort, kein OOM-Fehler.
Notes:
- synthesis: PRE-FLIGHT merger overflow ctx=4096 tritt auf wenn llama3.3-70b-ctx4k als Judge
  selektiert wird (Dynamic Router). Das ist ein TRUE POSITIVE.
  Laut TASK-1 Resolution notes (Claude Code): Bug B ist behoben, resolve_requested_ctx() aktiv.
- Redis ctx cache keys für llama3.3-70b-ctx4k gelöscht (stale 98304 Wert).
- TASK-1 bis TASK-6 laut AGENT_LASTENHEFT.md alle done (Claude Code, 2026-06-12).

## 2026-06-12T21:31Z — REVIEW — done

Plan / progress:
- Lastenheft (AGENT_LASTENHEFT.md) vollständig gelesen und analysiert.
- Alle Tasks TASK-1 bis TASK-6: done.
- Verbleibende offene Punkte sind Follow-ups (kein TASK-Status, Admin-Entscheidung erforderlich):
  1. Persönlicher API Key moe-sk-940e228... noch in scripts/ hardcoded (3 Dateien).
  2. System-API-Key braucht AIHUB-Verbindung für cloud-model discovery (Admin UI Aktion).
  3. models/backup_20260612/ (altes ONNX, 552KB) nach Stabilitätsphase löschen.
Notes:
- Nächster sinnvoller Schritt: Nutzer entscheidet ob die 3 Script-Dateien ebenfalls bereinigt werden
  sollen (analog TASK-6), oder ob das als low-priority Follow-up bleibt.


## 2026-06-25T07:38Z — TASK-LUMI-JUDGE — in_progress

Plan / progress:
- LUMI-G LoRA judge training für paraconsistenten Sovereign-Judge (32B QLoRA).
- Implementiert: J-MoE Debate Flow (graph/expert.py), Belnap-Dunn Arbitration (graph/synthesis.py),
  Dataset Compiler (scripts/compile_paraconsistent_dataset.py).
- Erstellt: scripts/train_judge_lora.py (TRL SFTTrainer + LoRA, QLoRA 4-bit), 
  scripts/train_judge_lora.sh (SLURM, 8 GCDs, 4h, small-g partition).
- Dataset (3 dry-run samples) + alle Scripts nach LUMI-G hochgeladen.
- SLURM Job **19517008** submitted, Status: PD (pending) → small-g partition.
Pre-conditions verified:
- SSH zu lumi-g: OK (Cert erneuert durch User, 2026-06-25).
- lumi-multitorch-latest.sif: vorhanden unter /appl/local/laifs/containers/.
- Qwen2.5-32B-Instruct: vollständig gecacht (17 Safetensor-Shards, 62 GB HF cache).
- Dataset paraconsistent_training_data.jsonl: 3 Samples auf LUMI hochgeladen.
Notes:
- Dataset ist ein Dry-Run-Placeholder (3 Mock-Samples). Ein echter Datensatz
  sollte über scripts/compile_paraconsistent_dataset.py (MoE API, live LLM calls)
  generiert und erneut hochgeladen werden.
- Job läuft mit Qwen2.5-32B-Instruct (statt geplanter 7B) — weil 32B bereits gecacht,
  7B nicht. Batch size=1 für 32B QLoRA.
- Nach Job-Abschluss: sovereign_router.onnx.data Analogie — merged Model nach
  /opt/deployment/moe-sovereign/moe-infra/models/sovereign-judge-32b/ kopieren.

---

## 2026-06-28T20:45:00Z — TASK-9 — in_progress

Plan / progress:
- Merge v1 completed successfully on CPU (22 mins, Job 19540774), yielding the full 62 GB merged FP16 Qwen2.5-32B model on scratch.
- The initial 3-node datagen run (`19540836-38`) timed out after 8h, but generated 5,080 paraconsistent logic samples.
- Rewrote the generator to run concurrently using `asyncio` and `httpx.AsyncClient` with a concurrency limit of 48 (`generate_judge_dataset_async.py`).
- Fixed a critical deduplication key truncation bug in `merge_shards_and_train.sh` that would have otherwise caused massive data loss (retaining only 9 unique lines due to 120-char instruction slicing).
- Resubmitted the three shards (`19588284-86`) under `small-g` using the new async script.
- Chained `merge_shards_and_train.sh` as a dependent CPU SLURM batch job (`19588422` with `--dependency=afterok:19588284:19588285:19588286`) to automatically merge, deduplicate, and kick off 8-GPU DDP training (`train_judge_lora_large.sh`) once datagen finishes.
- Verified that all scripts are properly synchronized to LUMI-G.

Pre-conditions verified:
- LUMI-G connectivity is healthy.
- Deterministic random seed 42 in place to ensure sharding consistency.
- No other agent is editing `merge_shards_and_train.sh` or the training scripts.

Notes:
- Post-training action: The resulting 62 GB merged model will be quantized to 4-bit (AWQ/GGUF) to fit within a ~20 GB VRAM envelope for deployment on N04-RTX.

---

## 2026-06-29T08:57:00Z — TASK-9 — in_progress

Plan / progress:
- The initial async run attempts (Jobs 19588284-86) failed immediately.
- Diagnostics revealed that the compute nodes on LUMI-G do not have `/pfs/lustref1` mounted. Consequently, all references to the shared container `/appl/local/laifs/containers/lumi-multitorch-latest.sif` and the `laifs` modules failed since they are symlinked to `/pfs/lustref1`.
- Solution: Copied the 14 GB Singularity container image directly to our local project scratch directory (`/scratch/project_465003058/hornphil/lumi-multitorch-latest.sif`) which is hosted on the fully accessible `/pfs/lustrep4` filesystem.
- Modified `generate_judge_dataset.sh`, `train_judge_lora_large.sh`, and `merge_judge_lora.sh` to use the local scratch-based container path and comment out the broken module load calls.
- Verified via interactive `srun` that Singularity successfully executes inside the scratch-based container copy on compute nodes and correctly detects PyTorch and AMD GPUs (`torch.cuda.is_available() == True`).
- Cancelled the old stale dependency job (Job 19588422).
- Resubmitted the three generator shards (Jobs 19598021, 19598022, 19598023).
- Resubmitted the merge-and-trigger job (Job 19598025) with `--dependency=afterok:19598021:19598022:19598023` to start DDP training automatically once datagen completes.
- Updated `PreView/index.html` and `eurohpc_lumi_activity_report.md` with the new job states, quotas, and resubmissions.

---

## 2026-07-02T20:40:00Z — TASK-9 — in_progress

Plan / progress:
- The async generator runs (Jobs 19598021-23) ran successfully on LUMI-G but reached their 4-hour time limit and timed out.
- Verification showed they generated a total of **50,276 valid paraconsistent samples** (Shard 0: 16,843, Shard 1: 16,792, Shard 2: 16,641) out of the 90,000 target.
- Measured throughput is ~11,300 samples/hour (~3.1 samples/second across all shards), representing a successful **18x speedup** over the single-threaded implementation.
- Copied data is completely safe; no data loss occurred.
- Action: Cancelled the old stale dependency job (Job 19598025).
- Action: Resubmitted the three shards as resume jobs (Jobs 19682379, 19682380, 19682381) to complete the remaining ~40,000 samples. The generator script automatically detects the already generated files and resumes from their respective offsets.
- Action: Resubmitted the merge-and-trigger job (Job 19682382) with `--dependency=afterok:19682379:19682380:19682381`.
- Updated `PreView/index.html` and `eurohpc_lumi_activity_report.md` to reflect the updated metrics, consumption, and pending queue state.
