# Current Status and Next Work — MoE Sovereign (moe-infra)

Purpose: compact current-state file for context restore.

## Current Status (2026-06-17)

All tasks from the 2026-06-12 debugging session are resolved:

| Task | Summary | Status |
|---|---|---|
| TASK-1 | PRE-FLIGHT ctx-resolution mismatch (Bug B) — `resolve_requested_ctx()` | ✅ done |
| TASK-2 | LUMI-G SSH cert renewal + router model training | ✅ done |
| TASK-3 | IMoE end-to-end verification + walkthrough report | ✅ done |
| TASK-4 | ChromaDB semantic template cache never hitting (Bug C) | ✅ done |
| TASK-5 | `dynamic_template_feedback_log` insert path dead (Bug D) | ✅ done |
| TASK-6 | Hardcoded infra/secrets in `dynamic_router.py` | ✅ done |
| TASK-7 | Dynamic system prompts / Sovereign-14B SFT pipeline | ⏸ pending |
| TASK-8 | HABE (Holographic Ambient Background Engine) + GUI | ✅ done |

Authoritative task details and resolution notes:
`../../AGENT_LASTENHEFT.md` Section 3.

## Next Work

1. **TASK-7: Dynamic System Prompts** (highest priority open item)
   - Modify `scripts/dataset_generator.py` to produce full template JSON
     with custom system prompts for planner, judge, and each expert.
   - Extend `services/dynamic_router.py:get_dynamic_template()` to generate
     prompt-specific system prompts dynamically.
   - Depends on: none (independent of all resolved tasks).
   - See: `docs/backlog/current/current.md` for structured decomposition.

2. **Follow-ups from TASK-6** (not formalized as tasks):
   - Make personal API key prefix configurable via env vars in
     `scripts/dataset_generator.py`, `scripts/send_request.py`,
     `scripts/index_models_metadata.py`.
   - Cloud-model discovery must not assume a hardcoded AIHUB account —
     configure fully via Admin UI (Inference Servers / User Connections).

3. **Model cleanup**: `models/backup_20260612/` (552 KB old ONNX) is safe
   to delete once `sovereign_router.onnx` has been stable for a while.

## Recent Decisions

- **2026-06-12**: `resolve_requested_ctx()` added to `context_budget.py` as
  single source of truth for context window resolution (TASK-1). `/api/ps`
  no longer used as PRE-FLIGHT budget input.
- **2026-06-12**: ChromaDB document text aligned with query text in
  `dynamic_router.py` (TASK-4). Cache now hits on identical prompts.
- **2026-06-12**: `CLOUD_ENDPOINT`/`CLOUD_TOKEN` moved to env vars
  `DYNAMIC_ROUTER_CLOUD_ENDPOINT`/`DYNAMIC_ROUTER_CLOUD_TOKEN` (TASK-6).
- **2026-06-16**: HABE VSA module (`services/vsa_background.py`) deployed,
  `enable_habe` toggle in Admin UI and routing pipeline (TASK-8).

## Do Not Forget

- VRAM rule: llama3:70b-class models ≤60 GB context budget.
- `local_only=True` must exclude all `CLOUD_ENDPOINT` models — verify in
  `_get_cluster_state()` on any routing change.
- No hardcoded server names, IPs, or model names in source.
- All code, comments, and docstrings must be in English.
- UI strings go through the translation system (`t(request, 'key')`).
- After Python changes: rebuild + restart affected service (not plain restart).
- GitOps: always use a feature branch and PR — never push directly to main.
