# Current Status and Next Work — MoE Sovereign (moe-infra)

Purpose: compact current-state file for context restore.

## Current Status (2026-07-02)

All active tasks are tracked as follows:

| Task | Summary | Status |
|---|---|---|
| TASK-1 | PRE-FLIGHT ctx-resolution mismatch (Bug B) — `resolve_requested_ctx()` | ✅ done |
| TASK-2 | LUMI-G SSH cert renewal + router model training | ✅ done |
| TASK-3 | IMoE end-to-end verification + walkthrough report | ✅ done |
| TASK-4 | ChromaDB semantic template cache never hitting (Bug C) | ✅ done |
| TASK-5 | `dynamic_template_feedback_log` insert path dead (Bug D) | ✅ done |
| TASK-6 | Hardcoded infra/secrets in `dynamic_router.py` | ✅ done |
| TASK-7 | Dynamic system prompts / Sovereign-14B SFT pipeline | ✅ done |
| TASK-8 | HABE (Holographic Ambient Background Engine) + GUI | ✅ done |
| TASK-9 | Large-Scale Dataset Gen & Judge QLoRA v2 (8-GPU DDP) | 🔄 in_progress |

Authoritative task details and resolution notes:
`../../AGENT_LASTENHEFT.md` Section 3.

## Next Work

1. **TASK-9: Large-Scale Dataset Generation & Judge Model Training (v2)** (highest priority active item)
   - Complete 90k samples generation using the async generator `generate_judge_dataset_async.py` (concurrency 48) on LUMI-G.
   - Run the chained trigger job `merge_shards_and_train.sh` (Job `19682382`) to merge/deduplicate/trigger DDP training.
   - Run the 8-GPU QLoRA DDP training on the dense **granite4.1:30b** base.
   - Merge the final adapter and quantize the model to 4-bit (AWQ/GGUF) for deployment on `N04-RTX`.

2. **Phase 3: Planner Model Distillation (Granite-3B CPU-focused SLM)**
   - Synthesize 200k planner pairs using teacher models.
   - Train a fast local JSON planner model (**granite4.1:3b**) to run on MoE-Sovereign VM CPU.

3. **Follow-ups from TASK-6**:
   - Make personal API key prefix configurable via env vars in `scripts/dataset_generator.py`, `scripts/send_request.py`, `scripts/index_models_metadata.py`.
   - Cloud-model discovery must not assume a hardcoded AIHUB account — configure fully via Admin UI.

4. **Model cleanup**: `models/backup_20260612/` (552 KB old ONNX) is safe to delete.

## Recent Decisions

- **2026-06-12**: `resolve_requested_ctx()` added to `context_budget.py` (TASK-1).
- **2026-06-12**: ChromaDB document text aligned with query text in `dynamic_router.py` (TASK-4).
- **2026-06-12**: `CLOUD_ENDPOINT`/`CLOUD_TOKEN` moved to env vars (TASK-6).
- **2026-06-16**: HABE VSA module (`services/vsa_background.py`) deployed (TASK-8).
- **2026-06-22**: Dynamic system prompts generated dynamically in `dynamic_router.py` and dataset (TASK-7).
- **2026-06-26**: Merged Judge v1 model (FP16 Qwen2.5-32B) successfully merged on CPU (Job 19540774).
- **2026-06-28**: Concurrency increased to 48 in `generate_judge_dataset_async.py` using `asyncio` and `httpx` to maximize vLLM throughput (10x-20x speedup).
- **2026-06-28**: Fixed deduplication key truncation bug in `merge_shards_and_train.sh` to prevent loss of unique samples.
- **2026-06-28**: Set up automated SLURM job chaining (`--dependency=afterok:JOB_IDS`) for seamless pipeline execution.
- **2026-06-29**: Copied singularity container image to local project scratch (14 GB) and updated scripts to use this copy, resolving compute node mount issues (`/pfs/lustref1` unavailable on nodes).
- **2026-06-29**: The async generator jobs (19598021-23) ran successfully but timed out after 4 hours, generating 50,276 samples (exhibiting ~11,300 samples/hour throughput, an 18x speedup over v1).
- **2026-07-02**: Resubmitted the generator shards as resume runs (Jobs 19682379-81) to complete the remaining ~40k samples, along with the chained merge/train trigger (Job 19682382).

## Do Not Forget

- VRAM rule: llama3:70b-class models ≤60 GB context budget.
- `local_only=True` must exclude all `CLOUD_ENDPOINT` models — verify in `_get_cluster_state()`.
- No hardcoded server names, IPs, or model names in source.
- All code, comments, and docstrings must be in English.
- UI strings go through the translation system (`t(request, 'key')`).
- After Python changes: rebuild + restart affected service.
- GitOps: always use a feature branch and PR — never push directly to main.
