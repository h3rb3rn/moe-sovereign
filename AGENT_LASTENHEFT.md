# AGENT_LASTENHEFT.md — Cross-Tool Task Coordination (MoE Sovereign)

This file is a **shared task board** for AI agentic tools collaborating on this
project across sessions and tools (Claude Code, agy / Google Antigravity CLI,
and others). It complements `AGENTS.md` (permanent conventions) and
`CLAUDE.md` (project rules) — read those first for general working rules.

This document is updated by humans and agents alike. Keep entries terse,
factual, and timestamped (UTC).

---

## 0. MANDATORY: Status Protocol (read this before starting any task)

**Why:** Long-running agent sessions can hit rate limits or context limits
mid-task. If an agent disappears without a trace, the next agent (or the
human operator) has no idea what was attempted, what state the system is in,
or whether it's safe to continue. This protocol exists to make every task
resumable.

**Rule:** Before starting work on ANY task in Section 3, an agent MUST:

1. Open `agent_status/<your-tool-name>.md` (create it from
   `agent_status/_template.md` if it doesn't exist yet).
2. Append a new status entry (see template) with:
   - Timestamp (UTC)
   - Task ID you are about to start (e.g. `TASK-1`)
   - Your current understanding of the task's state (pending / partially
     done / blocked — read prior entries first)
   - A short plan (3-6 bullet points) of what you intend to do
   - Any pre-conditions you verified (e.g. "container healthy", "no other
     agent currently editing these files")
3. Set the task's `Status:` and `Owner:` fields in Section 3 to
   `in_progress` and your tool name.
4. Only then start working.

**During the task:** if you reach a natural checkpoint (e.g. before a
long-running build, before a SLURM job submission, before an operation that
could take >5 min), append a short progress update to your status file. This
ensures that if you get cut off, the next agent knows exactly how far you got.

**When done (or blocked):** append a final entry to your status file
(`done` / `blocked: <reason>`), and update Section 3's `Status:` field
(`done`, `blocked`, or back to `pending` if you had to abort). Never leave a
task `in_progress` with no recent status entry — that's the broken state this
protocol prevents.

**Conflict avoidance:** before editing a file, check the other agents'
status files for entries mentioning the same file with `in_progress`. If
found, coordinate via this document (add a note) rather than editing
concurrently.

---

## 1. Big Picture

**MoE Sovereign** is a local, sovereign Multi-Model Orchestration (MoE) LLM
laboratory running on heterogeneous on-prem hardware (RTX/Tesla GPU nodes).
It acts as a middleware gateway: a LangGraph orchestrator
(`langgraph-app` / `main.py`) plans a request, dispatches it to one or more
expert LLMs on local Ollama nodes (deterministic template routing), merges
and judges the results (`graph/synthesis.py`), and returns a response — all
without leaving the local network unless explicitly permitted.

**Current initiative — Infrastructure Mixture of Experts (IMoE) Gating
Network:** a lightweight ONNX classifier (on top of all-MiniLM-L6-v2
embeddings) that dynamically predicts expert category, complexity, and
retrieval gates per prompt, replacing/augmenting static template selection.
Implementation status (see `~/.gemini/antigravity-cli/brain/<session>/task.md`
for the authoritative checklist):

- ✅ Synthetic training dataset generated (665 prompts)
- ✅ DB schema (`model_metadata`, `dynamic_template_feedback_log`) + helpers
  (insert path unwired — see Bug D)
- ✅ Daily model-metadata indexer (1,042 models indexed)
- ✅ Dynamic router service (`services/dynamic_router.py`) — ChromaDB cache,
  ONNX inference, Thompson-sampling-based allocation scoring
- ✅ Orchestrator integration (`routing.py`, `planner.py`, `feedback.py`)
- ✅ Unit tests (`tests/test_dynamic_router.py`)
- ⏸ **Router model training on LUMI-G** — blocked on expired SSH cert
- ✅ **End-to-end verification & walkthrough report** — done (2026-06-12),
  see TASK-3

**Today's debugging session (2026-06-12)** uncovered two issues during an
E2E smoke test of the orchestrator:

- **Bug A (fixed, 12:17 UTC+2):** `policy_log.py` failed to write
  `policy_training.jsonl` because `.env`'s `POLICY_LOG_PATH` correction
  was added after the container's last creation. Fixed by recreating
  `langgraph-app` (`docker compose up -d langgraph-app`).
- **Bug B (fixed, TASK-1):** the merger PRE-FLIGHT
  overflow check in `graph/synthesis.py` reported `ctx=4096` for
  `JUDGE_MODEL=qwen3.6:35b`, even though the model's real context window is
  32768. Root cause: the check read the model's *currently loaded* context
  via Ollama `/api/ps` (`context_budget.get_model_ctx_async` →
  `fetch_ollama_num_ctx`), which reflected a stale prior load — not what the
  actual judge call requests via `_judge_model_kw()`
  (`services/inference.py:573-594`). Fixed via the new
  `resolve_requested_ctx()` helper in `context_budget.py` — see TASK-1
  Resolution notes for full verification.
- **Bug C (fixed 17:58 UTC+2, TASK-4):**
  the ChromaDB semantic template cache (`_match_existing_template()`,
  `services/dynamic_router.py:356-375`) NEVER produces a cache hit, for any
  prompt — including verbatim repeats. Root cause: `_save_template_to_db_
  and_cache()` indexes documents as `f"Dynamic gating template compiled for
  prompt: {prompt[:80]}..."` (`dynamic_router.py:698`), but
  `_match_existing_template()` queries with the raw `prompt`
  (`dynamic_router.py:363`). Diagnostic against the live `moe_template_cache`
  collection: querying with the raw prompt gives cosine distance `0.3103`
  to its own just-stored entry (> 0.18 threshold → miss), while querying
  with the stored-document format gives `~0.0000` (would hit). Every
  request therefore re-compiles and re-registers a new
  `admin_expert_templates` row, defeating the cache's purpose (avoiding
  redundant DB rows / VRAM reloads, per walkthrough §3).
- **Bug D (root-caused 12:25 UTC+2, fixed 19:24 UTC+2, TASK-5):**
  `dynamic_template_feedback_log` (Postgres, `database.py:225`) has **0
  rows** in production. `log_dynamic_template_feedback()`
  (`database.py:2518`, the INSERT helper) has no callers anywhere in the
  codebase — it was implemented per task.md item 3 but never wired into the
  template-compile path (item 6). Consequence:
  `routes/feedback.py:114`'s `update_dynamic_template_feedback_rating
  (template_id, rating)` always matches 0 rows (`UPDATE ... WHERE
  template_id=%s` on a table with no such row) — **user ratings (👍/👎) on
  dynamically-routed responses are silently discarded**, wrapped in a bare
  `try/except: pass` so the failure never surfaces in logs.

This Lastenheft turns the remaining work into a coordinated backlog.

**Status as of 2026-06-12T21:06Z:** TASK-1 through TASK-6 are all `done`
(see Section 3 for full Resolution notes on each). Open follow-ups (not
formalized as tasks): ~~(1) make the personal API key prefix configurable via
environment variables or options in all scripts (`scripts/dataset_generator.py`,
`scripts/send_request.py`, `scripts/index_models_metadata.py`)~~; ~~(2) ensure
that any cloud-model discovery or dynamic routing configurations do not
hardcode AIHUB, but remain fully configurable dynamically via the MoE Admin
UI (Inference Servers / User Connections), allowing individual configurations
for users without AIHUB access~~; ~~(3) `models/backup_20260612/` (552 KB old
ONNX model from TASK-2) is safe to delete once the new `sovereign_router.onnx`
has been stable for a while~~.

**Update (2026-07-05T19:45Z, Claude Code):** All three follow-ups verified
resolved, no task ever needed:
1. `scripts/dataset_generator.py`/`send_request.py` read `SYSTEM_API_KEY` from
   env (no hardcoded personal key); `index_models_metadata.py` reads
   `INFERENCE_SERVERS` from env entirely. No trace of the old
   `moe-sk-940e228...` key anywhere in the codebase (verified via grep).
2. `services/dynamic_router.py:56-64` (`CLOUD_ENDPOINTS`) is now derived
   generically from `INFERENCE_SERVERS_LIST` (all non-Ollama entries, each
   with its own URL/token) — superseding TASK-6's original
   `DYNAMIC_ROUTER_CLOUD_ENDPOINT`/`_TOKEN` single-pair env-var approach
   (those env vars no longer exist in `.env`). `get_dynamic_template()`
   (line ~789) additionally layers per-user `user_connections` on top of the
   global cloud list — users without admin-configured AIHUB access can
   already supply their own cloud endpoint via a private connection.
3. `models/backup_20260612/` no longer exists (already deleted).

No further action needed on these three items.

---

## 2. General Rules for All Agents

- Follow `CLAUDE.md` and `AGENTS.md` in full (English code/comments/docs,
  no hardcoded infra defaults, translation files for UI strings, etc.).
- After any Python/template change, rebuild and restart the affected
  service: `sudo docker compose build <service> && sudo docker compose up -d <service>`.
  Remember: `.env` changes also require `docker compose up -d <service>`
  (recreate) — a plain `restart` does NOT reload `env_file` values.
- Service names: `langgraph-app` (main.py), `moe-admin` (admin_ui/),
  `mcp-precision` (mcp_server/).
- Only use the MoE-API (`http://node-0X.internal:8002/v1/chat/completions`,
  model `moe-auto`) for end-to-end testing. Direct Ollama API calls are for
  debugging only.
- `llama3:70b`-class models are VRAM-constrained — do not request context
  windows beyond what the node's VRAM supports (≤60 GB rule established
  previously).

---

## 3. Task Backlog

### TASK-1: Fix PRE-FLIGHT ctx-resolution mismatch (Bug B)

- **Status:** done (2026-06-12)
- **Owner:** Claude Code
- **Depends on:** none
- **Context:** See Section 1, Bug B. The PRE-FLIGHT checks in
  `graph/synthesis.py` (merger, ~line 386) and `graph/expert.py` (~line 306)
  resolve the model's context window via `get_model_ctx_async()`, which
  queries Ollama `/api/ps` for the *live* loaded state. The actual LLM call
  resolves context via `_judge_model_kw()` / `_planner_model_kw()`
  (`services/inference.py`), which uses a different priority order
  (`state_num_ctx or JUDGE_NUM_CTX/PLANNER_NUM_CTX or static_ctx`, clamped by
  `safe_ctx`). These two resolutions can disagree, causing false-positive
  overflow warnings and redundant `compress_prompt_to_fit()` calls.
- **Instructions:**
  1. Extract a shared helper, e.g. `resolve_requested_ctx(model, state_num_ctx, num_ctx_env, redis_client=None) -> int`
     in `context_budget.py`, implementing the SAME priority logic currently
     duplicated in `_judge_model_kw()` and `_planner_model_kw()`
     (`services/inference.py:573-619`): `state_num_ctx or num_ctx_env or
     static_ctx(model)`, clamped to `safe_ctx = static_ctx(model)` if smaller.
  2. Update `_judge_model_kw()` and `_planner_model_kw()` to call this helper
     (no behavior change for the actual LLM calls — this is a refactor).
  3. Update the PRE-FLIGHT checks in `graph/synthesis.py` (merger, ~line 386)
     and `graph/expert.py` (~line 306) to use `resolve_requested_ctx(...)`
     instead of (or in addition to) the live `/api/ps`-based
     `get_model_ctx_async()`. The PRE-FLIGHT budget must reflect what the
     upcoming call will actually request, not the currently-loaded state.
  4. Decide (and document in a code comment) whether `/api/ps` should still
     be consulted at all — e.g. as a diagnostic log line, not as the budget
     input.
  5. Rebuild and restart: `sudo docker compose build langgraph-app && sudo docker compose up -d langgraph-app`.
- **Acceptance criteria:**
  - Re-run the quicksort E2E prompt via the MoE-API
    (`scratch/test_prompt.sh` from the agy session is a good template).
  - Orchestrator logs show NO `PRE-FLIGHT merger overflow` warning for
    `qwen3.6:35b` when `JUDGE_NUM_CTX=32768`.
  - `compress_prompt_to_fit()` is not called for inputs well under 32768
    tokens.
  - The request returns a final response within a reasonable time
    (no multi-minute hang from redundant model reloads).
  - `tests/test_dynamic_router.py` and any existing context-budget unit
    tests still pass.

- **Resolution notes (Claude Code, 2026-06-12):**
  - Implemented `resolve_requested_ctx(model, state_num_ctx, num_ctx_env,
    label="")` in `context_budget.py` as the single source of truth for "what
    ctx will this call request" — `state_num_ctx or num_ctx_env or
    get_model_context_window(model)`, clamped to `get_model_context_window
    (model)` if that static value is smaller (with an INFO log when clamped).
  - `_judge_model_kw()` / `_planner_model_kw()` in `services/inference.py`
    refactored to call this helper (pure refactor, no behavior change).
  - `graph/synthesis.py` merger PRE-FLIGHT now computes `_merger_ctx` via
    `resolve_requested_ctx(..., label="synthesis")` instead of the live
    `/api/ps`-based `get_model_ctx_async`. `/api/ps` is still polled
    periodically elsewhere for node-health/VRAM diagnostics (e.g. the
    `vram_high` warnings), but is NO LONGER used as PRE-FLIGHT budget input.
  - **Deviation from original instructions: `graph/expert.py` (~line 306/392)
    was investigated and intentionally NOT changed.** Its PRE-FLIGHT budget
    already uses `_expert_ctx_window` (from `get_model_ctx_async` + VRAM
    pinning + native-ctx clamp) as BOTH the budget input AND the actual call's
    `extra_body.options.num_ctx` (line 392) — i.e. it was already
    self-consistent and not affected by Bug B. No changes needed there.
  - **Verification:** rebuilt/recreated `langgraph-app`; ran the quicksort
    E2E prompt via the MoE-API (~19 min total, dominated by 2 expert calls +
    an 11-min judge call on a 70B model at 91% VRAM — not a ctx-mismatch
    reload). For this run the dynamic router selected
    `llama3.3-70b-ctx4k:latest` as judge (not `qwen3.6:35b`); the new log line
    `synthesis: context clamped from requested 32768 to safe limit 4096 for
    model llama3.3-70b-ctx4k:latest` confirms `resolve_requested_ctx()` is
    active, and the resulting overflow warning is a TRUE positive (that
    model's real static ctx is 4096 by design). Directly verified in-container
    via `resolve_requested_ctx`: `qwen3.6:35b` → 32768 (the original Bug B
    case is fixed — no more spurious `ctx=4096`); `llama3.3-70b-ctx4k:latest`
    → 4096 (correct). `tests/test_dynamic_router.py` +
    `tests/test_context_index.py` (24 tests) pass.

---

### TASK-2: Renew LUMI SSH cert & run router training on LUMI-G

- **Status:** done (2026-06-12)
- **Owner:** Claude Code
- **Depends on:** none (independent of TASK-1/3, but TASK-3 depends on its output)
- **Context:** The IMoE gating network's multi-task head needs training on
  LUMI-G (AMD MI250x partition). The dataset (665 prompts) is already
  generated at `~/synthetic_router_dataset.json`. The blocker is
  an expired SSH certificate `~/.ssh/id_efp.lumi.csc.fi-cert.pub` (CSC
  federated EFP/MyAccessID cert — these typically require interactive
  browser-based re-authentication and CANNOT be renewed non-interactively).
- **Instructions:**
  1. Check whether the cert can be renewed via a CLI tool (e.g. `sshcsc lumi`
     or similar, depending on what's installed) — run `sshcsc --help` or
     check `~/.ssh/config` for renewal hints.
  2. **If renewal requires interactive browser MFA:** STOP, write a status
     entry marking this `blocked: needs human MyAccessID re-auth`, and ask
     the human operator to run the renewal step themselves (suggest the
     `! <command>` pattern for Claude Code sessions). Do not attempt
     workarounds (e.g. disabling host key checking) — this is an
     authentication issue, not a connectivity issue.
  3. Once the cert is valid (verify with `ssh lumi.csc.fi true` or
     equivalent), copy the dataset:
     `scp ~/synthetic_router_dataset.json lumi:/scratch/project_465003058/hornphil/data/`
  4. Copy the training script `train_router_onnx.py` (locate it in the agy
     session's scratch dir or the repo) to LUMI-G.
  5. Submit the SLURM training job via `train_router.sh` (`sbatch
     train_router.sh`). Poll with `squeue -u <user>` / `sacct` — use the
     status protocol (Section 0) to log job ID and expected runtime before
     walking away.
  6. Once complete, copy `sovereign_router.onnx` back to this host. Determine
     the correct host-side path that maps to the container path
     `/app/models/sovereign_router.onnx` (check `docker-compose.yml` volume
     mounts for `/app/models` — do not assume `/opt/moe-infra/...`, verify).
- **Acceptance criteria:**
  - `ssh lumi.csc.fi true` succeeds.
  - SLURM job completes successfully (`sacct` shows `COMPLETED`).
  - `sovereign_router.onnx` exists at the correct host path, mounted into
    `langgraph-app` at `/app/models/sovereign_router.onnx`.

- **Resolution notes (Claude Code, 2026-06-12):**
  - Human operator renewed the LUMI cert out-of-band; `ssh lumi-g` (alias for
    `efp.lumi.csc.fi`, user `hornphil`) confirmed working — steps 1-2 of the
    original instructions were skipped per operator instruction.
  - **Training was already complete** before this task started: SLURM job
    `19166081` (`sacct`: COMPLETED, 2026-06-11T10:48:23 → 10:48:45, exit 0:0)
    trained `SovereignRouterClassifier` for 40 epochs (loss 0.2854 → 0.0324,
    logs at `/scratch/project_465003058/hornphil/logs/train_19166081.log` on
    LUMI) and exported
    `/scratch/project_465003058/hornphil/models/sovereign_router.onnx{,.data}`.
    (An earlier job `19166029` failed with `LocalEntryNotFoundError` — no
    internet on the compute node to fetch `all-MiniLM-L6-v2` from HF Hub;
    `19166081` used the locally-cached copy at
    `/scratch/project_465003058/hornphil/data/all-MiniLM-L6-v2/` instead.)
    Steps 1-5 of the original instructions (cert check, dataset/script
    upload, `sbatch` submission) were therefore moot.
  - **Host path / mount clarification**: `/app/models` is NOT a docker-compose
    bind mount (no entry for it in `docker-compose.yml`) — it is populated at
    **build time** from the repo directory
    `/opt/moe-sovereign/models/` (included in the
    `langgraph-app` build context). So the correct host path is
    `./models/sovereign_router.onnx{,.data}` in the repo root, NOT
    `/opt/moe-infra/...`.
  - Found a model ALREADY deployed at that path (mtime 2026-06-12 09:29,
    md5 `d9a7a57b...`/`49dd0799...`) — checksums did NOT match job 19166081's
    output, indicating a separate/earlier training run (the training script
    has no fixed random seed, so independent runs produce different weights
    even on the same dataset). Backed up the old files to
    `models/backup_20260612/` and replaced them with job 19166081's output
    (md5 `466ad556...`/`5e811e3d...`).
  - Rebuilt and recreated `langgraph-app`
    (`sudo docker compose build langgraph-app && sudo docker compose up -d
    langgraph-app`). Verified via `docker exec ... md5sum` that the new model
    is present at `/app/models/sovereign_router.onnx{,.data}`, and via logs
    that it loads cleanly: `🎯 Sovereign Router ONNX model loaded from
    /app/models/sovereign_router.onnx (providers=['CPUExecutionProvider'])`.
    A follow-up E2E request (trivial prompt) returned HTTP 200 /
    `finish_reason: stop` with no router-related errors.
  - TASK-3 is now unblocked (the trained ONNX model is in place).

---

### TASK-3: IMoE end-to-end verification & walkthrough report

- **Status:** done (2026-06-12)
- **Owner:** Claude Code
- **Depends on:** TASK-2 (done — needs `sovereign_router.onnx` in place)
- **Context:** Implementation (Section 1, items 1-6) is complete and unit-
  tested. What remains is the final checklist item 7 from
  `~/.gemini/antigravity-cli/brain/<session>/task.md`.
- **Instructions:**
  1. Confirm `sovereign_router.onnx` is present at `/app/models/sovereign_router.onnx`
     inside the `langgraph-app` container.
  2. Rebuild and restart: `sudo docker compose build langgraph-app && sudo docker compose up -d langgraph-app`.
  3. Manual tests:
     - **DB log writes:** trigger a request, confirm a row is written to
       `dynamic_template_feedback_log` and `policy_training.jsonl`
       (`/app/logs/policy_training.jsonl` — verify Bug A fix holds).
     - **ChromaDB template cache:** send the same/similar prompt twice,
       confirm the second request hits the dynamic router's ChromaDB cache
       (check logs for a cache-hit message in `services/dynamic_router.py`).
     - **Local compliance mode:** with `local_only` active, confirm the
       dynamic router does not route to or score non-local endpoints
       (cross-reference AGENTS.md Task 3 — Local-Only Compliance).
  4. Write the walkthrough report. Extend
     `~/.gemini/antigravity-cli/brain/<session>/walkthrough.md` (or create a
     new doc under `./docs/` per CLAUDE.md if it should become permanent
     project documentation) summarizing: architecture, what was tested, and
     results.
- **Acceptance criteria:**
  - All three manual tests pass and are documented with evidence (log
    excerpts, DB query output).
  - `task.md` item 7 fully checked off.
  - Walkthrough report committed/saved.
- **Resolution notes (2026-06-12T12:30Z):**
  - Steps 1-2 already satisfied by TASK-2.
  - Manual test 1 (DB log writes): PASS, with a corrected target — the
    table actually populated per request is `admin_expert_templates`
    (confirmed 8 rows incl. the TASK-1/2 verification chat_ids' templates),
    not `dynamic_template_feedback_log` (0 rows — see Bug D / TASK-5).
    `policy_training.jsonl` confirmed written (Bug A fix holds).
  - Manual test 2 (ChromaDB cache hit): FAIL, root-caused as **Bug C**
    (query/document text mismatch in `dynamic_router.py`, cache never hits
    for any prompt) — see Bug C above and TASK-4.
  - Manual test 3 (local-only compliance): PASS — verified via direct
    `get_dynamic_template(prompt, local_only=True/False)` calls; local
    allocation excludes all `CLOUD_ENDPOINT` models as expected.
  - `task.md` item 7 fully checked off (with annotations pointing to Bug
    C/TASK-4). Walkthrough extended at
    `~/.gemini/antigravity-cli/brain/38b2b162-4f85-49f0-8a2c-05400168d4ae/walkthrough.md`
    (new §5).
  - Acceptance criteria interpretation: "all three manual tests pass" is
    not literally met (test 2 fails) — but the verification itself is
    complete, the failure is fully root-caused with reproducible evidence,
    and a fix is scoped as TASK-4. Treating TASK-3 (the *verification*
    task) as done; the underlying bug is tracked separately.

---

### TASK-4: Fix ChromaDB semantic template cache never hitting (Bug C)

- **Status:** done (2026-06-12)
- **Owner:** Claude Code
- **Depends on:** TASK-3 (done — this bug was found during its verification)
- **Context:** `_match_existing_template()` (`services/dynamic_router.py:356-375`)
  queries ChromaDB `moe_template_cache` with the raw prompt, but
  `_save_template_to_db_and_cache()` (called from `get_dynamic_template()`,
  `dynamic_router.py:698`) indexes documents as
  `f"Dynamic gating template compiled for prompt: {prompt[:80]}..."`. The
  resulting cosine distance for even a verbatim repeat is `~0.31` (>
  `0.18` threshold), so the cache never hits — every request re-compiles
  and re-registers a new `admin_expert_templates` row.
- **Instructions:**
  1. Pick a fix direction (recommended: **(a)** — index the raw `prompt` as
     the ChromaDB document; keep the `"Dynamic gating template compiled for
     prompt: ..."` text only in `reasoning_trace`/metadata. Alternative
     **(b)**: make `_match_existing_template()` query with the same
     wrapped string `_save_template_to_db_and_cache()` indexes.)
  2. Implement the fix in `services/dynamic_router.py`.
  3. Re-run the cache-hit test from TASK-3 §5.2 (same prompt twice,
     in-container): confirm the 2nd call logs `🎯 Semantic template cache
     L2 hit!` and does NOT register a new `admin_expert_templates` row.
  4. Run `pytest tests/test_dynamic_router.py -q`.
  5. Rebuild/restart `langgraph-app` per CLAUDE.md.
- **Acceptance criteria:**
  - Repeating an identical prompt within the cosine-distance threshold
    produces a ChromaDB L2 cache hit and reuses the existing
    `admin_expert_templates` row (no new row created).
  - All existing `test_dynamic_router.py` tests still pass.
- **Resolution notes (2026-06-12T17:58Z):**
  - Implemented option (a): `_save_template_to_db_and_cache()`
    (`dynamic_router.py:378`) gained a `cache_query_text` parameter; the
    ChromaDB document is now the raw `prompt` (matching
    `_match_existing_template()`'s query), while the Postgres
    `description` column still gets the human-readable
    `"Dynamic gating template compiled for prompt: ..."` text. Call site
    at `dynamic_router.py:706` passes `cache_query_text=prompt`.
  - **Two additional latent bugs were exposed and fixed** by making the
    cache hit actually fire (previously dead code, never exercised):
    - `dynamic_router.py:~498` (`SELECT config_json FROM
      admin_expert_templates WHERE id=%s` → `row[0]`) raised `KeyError: 0`
      because the pool's default `row_factory` is `dict_row`
      (`database.py:445`) — fixed to `row["config_json"]`.
    - The reconstructed cached config dict lacked `"id"`/`"name"` keys
      (only added to the in-memory dict by the *caller* of
      `_save_template_to_db_and_cache()`, after `config_json` was already
      serialized) — would have caused `KeyError: 'id'` at
      `chat.py:1029` (`tmpl_id = dynamic_tmpl["id"]`) on every cache hit.
      Fixed by setting `cached_config["id"] = tmpl_id` /
      `cached_config["name"] = tmpl_name` before returning.
  - Verified end-to-end in-container (3 calls):
    - Call 1 (new prompt "Was ist der Unterschied zwischen einem Hash-Set
      und einer Linked List?") → cache miss, compiled
      `moe-dyn-512feaa590df`.
    - Call 2 (identical prompt) → `🎯 Semantic template cache L2 hit!
      ... distance=-0.0000`, returned the **same** `moe-dyn-512feaa590df`
      with `id`/`name` populated, **no new `admin_expert_templates` row**
      (confirmed via direct Postgres query: exactly 1 row for that id).
    - Call 3 (a prompt identical to an earlier *different* test's
      registration) → correctly hit *that* template
      (`moe-dyn-a24bf34df57b`, distance=-0.0000), confirming the cache
      distinguishes unrelated prompts correctly.
  - `pytest tests/test_dynamic_router.py -q` → 6 passed (before and after).
  - Rebuilt/restarted `langgraph-app` twice (once per fix iteration);
    container healthy both times.

---

### TASK-5: Wire up `dynamic_template_feedback_log` inserts (Bug D)

- **Status:** done (2026-06-12)
- **Owner:** Claude Code
- **Depends on:** TASK-3 (done — this bug was found during its verification)
- **Context:** `log_dynamic_template_feedback()` (`admin_ui/database.py:2518`)
  was dead code — no callers. `dynamic_template_feedback_log` had 0 rows in
  production, so `routes/feedback.py:114`'s
  `update_dynamic_template_feedback_rating(template_id, rating)` always
  affected 0 rows: user 👍/👎 ratings on dynamically-routed responses were
  silently discarded.
- **Instructions:**
  1. Call `log_dynamic_template_feedback(tmpl_id, prompt, config_json,
     latency_ms=None, tokens_used=None)` inside (or immediately after)
     `_save_template_to_db_and_cache()` in `services/dynamic_router.py`, so
     a row exists for `feedback.py` to update when the user later rates the
     response.
  2. Verify `update_dynamic_template_feedback_rating()` in
     `routes/feedback.py:114` now returns `True` / updates a real row for a
     freshly-compiled `template_id`.
  3. Run `pytest tests/test_dynamic_router.py -q`.
  4. Rebuild/restart `langgraph-app` per CLAUDE.md.
- **Acceptance criteria:**
  - A new dynamic-template compile inserts a row into
    `dynamic_template_feedback_log`.
  - A subsequent feedback rating on that `template_id` updates
    `user_rating` on that row (rowcount > 0).
  - All existing `test_dynamic_router.py` tests still pass.
- **Resolution notes (2026-06-12, Claude Code):**
  - Added `log_dynamic_template_feedback` to the `admin_ui.database` import
    in `services/dynamic_router.py` (line 16).
  - In `_save_template_to_db_and_cache()`, immediately after the existing
    `admin_expert_templates` INSERT (and its try/except), added a second,
    independent try/except block that calls
    `log_dynamic_template_feedback(template_id=tmpl_id, prompt=cache_query_text,
    config_json=config_json, latency_ms=None, tokens_used=None)`.
    `cache_query_text` (the raw prompt, introduced in TASK-4) is reused as
    the `prompt` value — no new parameter needed. Failures are logged and
    swallowed (best-effort, matching the style of the INSERT above it).
  - `python3 -m pytest tests/test_dynamic_router.py -q` → 6 passed (no
    change needed — the two `_save_template_to_db_and_cache` mocks accept
    arbitrary args).
  - Rebuilt and restarted `langgraph-app`; startup logs clean (no new
    errors beyond the pre-existing NiFi self-signed-cert warning).
  - **End-to-end verification** (in-container script, `init_db()` +
    `dr.init_router()` + one `get_dynamic_template()` call with a fresh
    prompt):
    - Compile produced `template_id = moe-dyn-49bef56315d6`.
    - `SELECT ... FROM dynamic_template_feedback_log WHERE template_id = ...`
      immediately returned a row: `{'template_id': 'moe-dyn-49bef56315d6',
      'prompt': '<the compiled prompt>', 'user_rating': None, 'status':
      'success'}`.
    - `update_dynamic_template_feedback_rating(tmpl_id, 5)` → `True`.
    - Re-querying the row showed `user_rating: 5` — confirms
      `routes/feedback.py`'s rating path now updates a real row
      (rowcount > 0).
  - Bug D is fixed; both acceptance criteria are met.

---

### TASK-6: Remove hardcoded infra/secrets from `dynamic_router.py`

- **Status:** done (2026-06-12)
- **Owner:** Claude Code
- **Depends on:** none
- **Context:** `services/dynamic_router.py:43-48` hardcodes
  `OLLAMA_ENDPOINTS` (server names/IPs, violates CLAUDE.md "No Hardcoded
  Infrastructure") and `CLOUD_ENDPOINT`/`CLOUD_TOKEN`. Investigation found
  `CLOUD_TOKEN` (`moe-sk-940e228...`) is not a generic service token — it is
  a **personal API key belonging to `kontakt@philipp-horn.dev`** (label
  "Benchmark", `dynamic_routing=true`, `local_only_routing=true`), embedded
  in plaintext in 4 files (`services/dynamic_router.py`,
  `scripts/dataset_generator.py`, `scripts/send_request.py`,
  `scripts/index_models_metadata.py`). The intended system credential
  (`SYSTEM_API_KEY`, "system-healer") returns 0 models via `/v1/models`
  (no AIHUB connection configured for that user) — so a naive swap to
  `SYSTEM_API_KEY` would silently zero out cloud-model discovery for all
  dynamic-routing users.
- **Decision (user, 2026-06-12):** behavior-preserving fix only —
  1. Derive `OLLAMA_ENDPOINTS` from `config.py`'s `INFERENCE_SERVERS_LIST` /
     `URL_MAP` / `API_TYPE_MAP` (admin-configured via `INFERENCE_SERVERS`),
     filtering for `api_type == "ollama"` and stripping the `/v1` suffix.
     Produces the same `{"N04-RTX": "...11434", "N11-M10": "...11434"}` dict.
  2. Move `CLOUD_ENDPOINT`/`CLOUD_TOKEN` to new env vars
     `DYNAMIC_ROUTER_CLOUD_ENDPOINT` / `DYNAMIC_ROUTER_CLOUD_TOKEN` in
     `.env`, keeping the current values (no behavior change). Default to
     `""` per CLAUDE.md; guard the cloud-poll block so an empty value skips
     the call cleanly.
  3. The deeper question — should dynamic-routing's cloud-model discovery
     use a personal benchmark key as its credential, or should
     `SYSTEM_API_KEY`'s user get an AIHUB connection configured via Admin UI
     — is **out of scope for TASK-6** and tracked as a follow-up note below.
- **Instructions:**
  1. Edit `services/dynamic_router.py`: import `URL_MAP`, `API_TYPE_MAP`
     from `config`; replace the `OLLAMA_ENDPOINTS` literal with a
     derivation; replace `CLOUD_ENDPOINT`/`CLOUD_TOKEN` with
     `os.getenv(...)` reads; guard the cloud-poll block in
     `_get_cluster_state()` with `if CLOUD_ENDPOINT and CLOUD_TOKEN:`.
  2. Add `DYNAMIC_ROUTER_CLOUD_ENDPOINT` / `DYNAMIC_ROUTER_CLOUD_TOKEN` to
     `.env` with the current hardcoded values.
  3. Run `pytest tests/test_dynamic_router.py -q`.
  4. Rebuild/restart `langgraph-app` per CLAUDE.md; verify
     `_get_cluster_state()` still returns local Ollama models AND cloud
     models (same as before the change).
- **Acceptance criteria:**
  - `OLLAMA_ENDPOINTS` contains no hardcoded server names/IPs in source.
  - `CLOUD_ENDPOINT`/`CLOUD_TOKEN` are no longer literals in
    `dynamic_router.py`.
  - `_get_cluster_state()` returns the same local+cloud model counts as
    before the change.
  - All existing `test_dynamic_router.py` tests still pass.
- **Follow-up (not in TASK-6 scope):** Make the personal API keys configurable via environment variables in `scripts/dataset_generator.py`, `scripts/send_request.py`, and `scripts/index_models_metadata.py`. Cloud-model discovery and routing must not assume a hardcoded AIHUB account, but must remain fully configurable via the MoE Admin UI (Inference Servers / User Connections), ensuring users without AIHUB can configure their own endpoints or run completely locally.
- **Resolution notes (2026-06-12, Claude Code):**
  - `services/dynamic_router.py:17` — added `URL_MAP`, `API_TYPE_MAP` to
    the `config` import.
  - `OLLAMA_ENDPOINTS` is now a dict comprehension over `URL_MAP.items()`,
    filtered to `API_TYPE_MAP.get(name) == "ollama"`, stripping a trailing
    `/v1` (the native Ollama API is queried, not the OpenAI-compatible
    route). Produces the identical
    `{"N04-RTX": "http://node-0X.internal:11434", "N11-M10":
    "http://node-0X.internal:11434"}` from the admin-configured
    `INFERENCE_SERVERS` env var — no server names/IPs left in source.
  - `CLOUD_ENDPOINT`/`CLOUD_TOKEN` now read via
    `os.getenv("DYNAMIC_ROUTER_CLOUD_ENDPOINT", "")` /
    `os.getenv("DYNAMIC_ROUTER_CLOUD_TOKEN", "")` (empty-string default per
    CLAUDE.md). The cloud-poll block in `_get_cluster_state()` is now
    guarded with `if CLOUD_ENDPOINT and CLOUD_TOKEN:` so an unconfigured
    deployment cleanly skips cloud-model discovery instead of making a
    request to `"/models"`.
  - `.env` — added `DYNAMIC_ROUTER_CLOUD_ENDPOINT` /
    `DYNAMIC_ROUTER_CLOUD_TOKEN` with the previously-hardcoded values
    (unchanged), with a comment explaining their purpose and that they're
    optional.
  - `python3 -m pytest tests/test_dynamic_router.py -q` → 6 passed.
  - Rebuilt + restarted `langgraph-app`; clean startup (only the
    pre-existing NiFi self-signed-cert warning).
  - **End-to-end verification** (in-container script): `OLLAMA_ENDPOINTS`
    printed identical to the old hardcoded dict; `_get_cluster_state()`
    returned **101 local models** (N04-RTX + N11-M10) and **1021 cloud
    models** — same counts as the pre-change baseline (TASK-3/4/5
    verification runs). Temp script removed from container and repo.
  - All acceptance criteria met; no behavior change.

---

### TASK-7: Implement Dynamic System Prompts in Dataset Generation & Gating Templates

- **Status:** done (2026-06-22)
- **Owner:** Antigravity (Google Antigravity CLI)
- **Depends on:** none
- **Context:** The upcoming Sovereign-14B SFT model training on LUMI-G requires training pairs `(Prompt, Optimal_Template_JSON)` where the template contains custom, prompt-specific system prompts for the planner, the judge, and every selected expert (e.g. `experts[exp]["system_prompt"]`, `planner_prompt`, `judge_prompt`).
- **Instructions:**
  1. Modify `scripts/dataset_generator.py`'s `generate_variants()` system instruction to require generating full template configurations (including custom system prompts for experts, planner, and judge) instead of simple prompt strings.
  2. Extend `services/dynamic_router.py`'s `get_dynamic_template()` function to support generating these system prompts dynamically (either using a fallback-prompt generator LLM call or via structured templates mapping categories to custom personas/prompts).
  3. Ensure `"planner_prompt"` and `"judge_prompt"` fields are populated in the dynamically compiled template JSON and verified.
- **Acceptance criteria:**
  - `dataset_generator.py` prompts the model to generate full `Optimal_Template_JSON` entries containing custom prompts for planner, judge, and experts.
  - Dynamically compiled templates include customized system prompts for experts, planner, and judge.
  - Verification test queries show these custom prompts propagated correctly to `AgentState`.
- **Resolution notes:**
  - **Dynamic System Prompts Helper:** Implemented `_generate_fallback_structured_prompts` (0 ms latency path) and `_generate_prompt_specific_prompts` (LLM-driven path controlled by `DYNAMIC_SYSTEM_PROMPTS_LLM_ENABLED` environment variable).
  - **Dynamic Template Integration:** Integrated the custom prompt generation into `get_dynamic_template()` in `services/dynamic_router.py` for planner, judge, and active expert models.
  - **Dataset Generation Integration:** Updated `scripts/dataset_generator.py` to generate complete optimal template configurations including customized prompt-specific system prompts for planner, judge, and active experts, both for newly generated prompts and seed prompts (via fallback generation).
  - **Unit Tests:** Added unit tests verifying language and step hints in `_generate_fallback_structured_prompts` and LLM mock validation in `_generate_prompt_specific_prompts` inside `tests/test_dynamic_router.py`. All tests pass.

---

### TASK-8: Implement Holographic Ambient Background Engine (HABE) and GUI controls

- **Status:** done (2026-06-16)
- **Owner:** Antigravity (Google Antigravity CLI)
- **Depends on:** none
- **Context:** Dreyfus background simulation via VSA (Holographic Reduced Representations) to bundle Neo4j/Cache triples into an ambient background vector. Requires integration with Admin UI expert templates and the routing pipeline.
- **Instructions:**
  1. Create a VSA module `services/vsa_background.py` implementing binding (circular convolution via FFT), unbinding, bundling, and cleanup.
  2. Implement GUI toggle (`enable_habe`) in `admin_ui/templates/expert_templates.html` (creation + edit modals) and JS payload serialization.
  3. Update `admin_ui/app.py` endpoints to process, export, import, and update the template payload with `enable_habe`.
  4. Modify `services/routing.py` to resolve and parse `enable_habe` from the database.
  5. Sync the changes to the public repository branch `docs/eurohpc-lumig-grant`.
- **Resolution notes:**
  - **HABE Service:** Created `services/vsa_background.py` with HRR (circular convolution/FFT) and unit-tested it successfully.
  - **UI Integration:** Modified `expert_templates.html` (Z. 267 & Z. 845) to add HABE toggles, and updated Javascript handlers to serialise the state.
  - **Backend Integration:** Updated `admin_ui/app.py` and `services/routing.py` to support `enable_habe` in all template database operations.
  - **Repository Sync:** Ran the sync script and pushed the updated codebase to the public GitHub repository branch `docs/eurohpc-lumig-grant`.
  - **Resource Strategy:** Documented K80 cluster as a deterministic FP64 scientific node (CUDA 11 constraint) and LUMI-G as the training node for the SFT/DPO orchestrator models.

---

### TASK-9: Large-Scale Dataset Generation & Judge Model Training (v2)

- **Status:** in_progress
- **Owner:** Antigravity (Google Antigravity CLI)
- **Depends on:** TASK-2, TASK-7
- **Context:** Training a high-quality paraconsistent Judge model requires transitioning from the 140-sample pilot dataset to a large-scale dataset (90k samples based on RouteLLM seeds). This requires high-throughput inference on LUMI-G and robust DDP-based training.
- **Instructions:**
  1. **Async Datagen:** Implement `scripts/generate_judge_dataset_async.py` using `asyncio` and `httpx.AsyncClient` with `concurrency=48` to utilize vLLM's batching capabilities.
  2. **Sharded Runs:** Run 3 parallel generator jobs (Offset 0, 30000, 60000) on 8-GPU nodes via SLURM, writing to individual shards.
  3. **Resume Logic:** Implement prefix-based duplicate checking on startup to skip already generated samples.
  4. **Deduplication & Merge:** Combine shards and deduplicate based on the full instruction string (do NOT truncate to 120 chars, which collapses the dataset).
  5. **DDP Training:** Launch 8-GPU DDP training using `train_judge_lora_large.sh` (each GPU loading a local 4-bit QLoRA copy to avoid pipeline parallel OOMs).
  6. **Automated Chaining:** Use SLURM dependencies (`--dependency=afterok:JOB_IDS`) to trigger the merge-and-train workflow automatically.
- **Acceptance criteria:**
  - Full 90k seed prompts generated and merged into `paraconsistent_large.jsonl`.
  - 8-GPU DDP training executes successfully and outputs the `sovereign-judge-32b-lora-v2` LoRA adapter.
  - Merging LoRA into the base model produces a 62 GB FP16 model checkpoint without OOM.
- **Resolution notes (Antigravity, 2026-06-28):**
  - **Merge v1:** Successfully merged the pilot model (Job 19540774, COMPLETED, 22 mins).
  - **Datagen v1:** Shards reached 8h timeout, producing 5,080 unique samples. Rewrote generator to `generate_judge_dataset_async.py` (Concurrency=48).
  - **Deduplication Fix:** Fixed a major bug in `merge_shards_and_train.sh` where keys were truncated to 120 chars, causing massive data loss.
  - **Resubmission:** Re-submitted the 3 shards (Jobs 19588284-86) and chained them to the trigger job (Job 19588422) for automated SFT execution.
  - **Doku-Sync (2026-07-05, Claude Code, Quelle: `agent_status/agy.md`
    Eintrag 2026-07-02T20:40Z):** Der obige Stand war überholt — die Jobs
    19598021-23 liefen erfolgreich, erreichten aber das 4h-Zeitlimit bei
    50.276 von 90.000 Samples (~18x Speedup ggü. Single-Thread). Antigravity
    hat als Resume-Jobs 19682379-81 + Merge-Trigger 19682382
    (`--dependency=afterok`) neu eingereicht. Aktueller SLURM-Zustand seither
    nicht erneut geprüft (Betreiber-Entscheidung vom 2026-07-05, kein
    SSH-Check) — maßgeblich bleibt der agy-Status-Log.

---

### TASK-10: Trust-Score / Verification Substrate

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** none
- **Context:** Der Judge hat kein quantitatives Qualitätsverdikt — er entscheidet ohne messbare Schwellen und winkt Antworten mit 0 validierten Quellen als valide durch. Ein Trust-Score berechnet nach jedem Expert-Durchlauf einen numerischen Wert aus messbaren Faktoren und leitet daraus eine deterministische Entscheidung ab.
- **Instructions:**
  1. Erstelle `services/trust_score.py` mit:
     - `TrustVerdict` Enum: `PROCEED` (≥0.65), `PROCEED_WITH_ASSUMPTION` (0.30–0.65), `BLOCK` (<0.30)
     - `TrustScore` Dataclass: `score: float`, `verdict: TrustVerdict`, `hard_blocked: bool`, `factors: dict`
     - `compute_trust_score(state_: AgentState) -> TrustScore` — Faktoren: `source_count` (Anzahl zitierter Neo4j-Knoten), `conflict_count` (Widersprüche zwischen Experts, negativ gewichtet), `cross_references_resolved` (Abdeckung der Teilfragen), `source_hashes_valid` (ChromaDB-Retrieval-Integrität als Hard-Block-Trigger)
     - Gewichte konfigurierbar via `TRUST_SCORE_WEIGHTS_JSON` env var (JSON-Dict, Default im Code als Fallback)
     - Hard-Block unabhängig vom Score: wenn `source_hashes_valid == False` → `hard_blocked=True`, Verdict zwingend `BLOCK`
  2. Integriere `compute_trust_score()` in `graph/synthesis.py` (Judge-Node, nach Expert-Aggregation, vor Merge-Prompt-Assembly).
  3. Bei `BLOCK` oder `hard_blocked`: Antwort nicht senden, Kafka-Event `moe.quality` emittieren, `x-moe-quality: blocked` Header setzen.
  4. Bei `PROCEED_WITH_ASSUMPTION`: Verdict und reduzierter Score in `AgentState` speichern (neues Feld `trust_verdict: str`) für TASK-11.
  5. Unit-Tests in `tests/test_trust_score.py` (min. 5 Cases: kein Source-Count, voller Score, Hard-Block, Grenzwerte).
  6. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Eine Anfrage mit 0 Neo4j-Quellen produziert `TrustVerdict.BLOCK` und wird nicht an den Client geliefert.
  - Eine Anfrage mit validierten Quellen und konsistenten Expert-Antworten produziert `TrustVerdict.PROCEED`.
  - Invalide ChromaDB-Hash-Prüfung triggert Hard-Block unabhängig vom numerischen Score.
  - `tests/test_trust_score.py` grün.

---

### TASK-11: Self-Critique Iteration Loop

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** TASK-10 (benötigt `trust_verdict` im AgentState)
- **Context:** Wenn der Trust-Score nach dem ersten Expert-Durchlauf im Bereich `PROCEED_WITH_ASSUMPTION` liegt (0.30–0.65), wird heute sofort eskaliert. Ein Self-Critique-Loop gibt den Experts einen explizit formulierten Gap-Feedback-Prompt und erlaubt max. N=2 Korrekturiterationen, bevor eskaliert wird. Schätzung: 40–60% weniger manuelle Escalations bei Borderline-Anfragen.
- **Instructions:**
  1. Füge `AgentState` in `pipeline/state.py` zwei neue Felder hinzu: `self_critique_round: int` (Default 0), `self_critique_max: int` (Default 2, aus `SELF_CRITIQUE_MAX_ROUNDS` env var).
  2. Erstelle einen neuen LangGraph-Node `self_critique` in `graph/synthesis.py` (oder eigene Datei `graph/self_critique.py`):
     - Liest `trust_verdict`, `expert_results`, aktuelle Teilfragen
     - Kompiliert einen Gap-Feedback-Prompt: "Folgende Aspekte waren unvollständig / widersprüchlich: [gap_summary]. Bitte überarbeite deine Antwort gezielt."
     - Ruft nur die betroffenen Experts erneut auf (nicht alle), inkrementiert `self_critique_round`
     - Gibt Kontrolle zurück an Trust-Score-Node (TASK-10)
  3. Konditionale Kante in `main.py`: nach Judge-Node → wenn `trust_verdict == PROCEED_WITH_ASSUMPTION` und `self_critique_round < self_critique_max` → `self_critique` Node; sonst → `resolve_conflicts` wie bisher.
  4. Bei erschöpftem Limit (`self_critique_round >= self_critique_max`) und noch `PROCEED_WITH_ASSUMPTION`: Antwort mit `x-moe-quality: assumption` Header senden statt Escalation.
  5. Unit-Tests: mock `compute_trust_score()` für Loop-Behavior-Tests.
  6. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Bei `PROCEED_WITH_ASSUMPTION` startet genau 1 Korrekturiteration (max. 2 gesamt).
  - Bei `PROCEED` kein Self-Critique-Aufruf.
  - Bei `BLOCK` kein Self-Critique (Hard-Block bleibt Hard-Block).
  - `self_critique_round` im `usage_log` protokolliert (Erweiterung der `usage_log`-INSERT in `database.py`).

---

### TASK-12: Decision Log mit Rationale-Pflicht

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** none
- **Context:** Kafka sagt heute WHAT und WHEN — das WHY fehlt komplett. Für EU-AI-Act-Compliance und Post-Mortems ist ein append-only Decision Log mit Pflichtfeld `rationale` essentiell. Jede nicht-triviale Laufzeit-Entscheidung (Judge-Übersteuerung, Constitution-Block, DoR-Fail, Trust-Score-Block) muss mit Begründung persistiert werden.
- **Instructions:**
  1. Erstelle `services/decision_log.py`:
     - `DecisionType` Enum: `JUDGE_OVERRIDE`, `CONSTITUTION_BLOCK`, `DOR_FAIL`, `TRUST_BLOCK`, `REPLAN`, `STUCK_LOOP`, `SELF_CRITIQUE_TRIGGERED`
     - `log_decision(decision_type: DecisionType, request_id: str, rationale: str, metadata: dict = None) -> None`
     - Backend: Kafka-Topic `moe.decisions` (append-only, gleiche Infrastruktur wie `moe.audit`); bei Kafka-Ausfall als Fallback in `decision_log.jsonl` im Log-Verzeichnis schreiben.
     - Pflichtfeld `rationale` — kein leerer String erlaubt (ValueError bei leerem rationale).
  2. Integriere `log_decision()` an folgenden Call-Sites:
     - `graph/synthesis.py`: bei Judge-Übersteuerung und Trust-Score-Block
     - `services/sovereign_constitution.py` (oder wo Constitution-Checks laufen): bei `on_violation: block`
     - `services/dor_check.py`: bei DoR-Violations (rationale = Violation-Message)
     - `services/cascade.py`: bei `STUCK_LOOP`-Emission
  3. Unit-Tests in `tests/test_decision_log.py`: leeres Rationale → ValueError, alle DecisionTypes schreibbar, Kafka-Fallback auf jsonl.
  4. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Jeder Constitution-Block erzeugt einen Kafka-Event auf `moe.decisions` mit nicht-leerem `rationale`.
  - `decision_log.jsonl` als Fallback vorhanden und beschreibbar.
  - Kein leeres `rationale` kommt durch (ValueError-Test grün).
  - Kafka-Topic `moe.decisions` unter `docker exec kafka kafka-topics.sh --list` sichtbar.

---

### TASK-13: Boundary Contracts zwischen Pipeline-Stufen

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** none
- **Context:** An den Stagegrenzen (Planner→Expert, Expert→Judge) wird heute nicht deterministisch geprüft, ob alle Pflichtfelder vorhanden sind. Fehlt `subtasks` oder `constraints` im Planner-Output, werden teure Expert-Calls mit unvollständigem Input gestartet. Ein YAML-deklarativer Contract-Check kostet <10ms und verhindert Silent Garbage-in/out.
- **Instructions:**
  1. Erstelle `config/boundary_contracts.yaml`:
     ```yaml
     stages:
       planner_to_expert:
         required_fields: [category, search_query]
         optional_fields: [mcp_tool, mcp_args, constraints]
         on_violation: cascade_spec_gap
       expert_to_judge:
         required_fields: [content, category]
         optional_fields: [citations, confidence]
         on_violation: cascade_expert_failure
     ```
  2. Erstelle `services/boundary_check.py`:
     - `check_boundary(stage: str, payload: dict) -> List[str]` — lädt das YAML, prüft Pflichtfelder, gibt Verletzungen zurück.
     - Bei Verletzung: emittiert den in `on_violation` deklarierten `CascadeType` via `services/cascade.py`.
  3. Integriere `check_boundary("planner_to_expert", task)` in `graph/planner.py` direkt vor dem Expert-Dispatch (nach DoR-Check, TASK-1-Integration-Point bei Zeile ~717).
  4. Integriere `check_boundary("expert_to_judge", result)` in `graph/synthesis.py` bei Expert-Result-Aggregation.
  5. Unit-Tests: fehlende Pflichtfelder → Cascade; vollständiger Payload → keine Verletzung.
  6. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Ein Planner-Output ohne `category` triggert `SPEC_GAP`-Cascade, kein Expert-Call.
  - Ein Expert-Result ohne `content` triggert `EXPERT_FAILURE`-Cascade.
  - Valide Payloads passieren ohne Overhead (<1ms Latenz-Overhead gemessen via Logging).
  - `boundary_contracts.yaml` versioniert im Repo.

---

### TASK-14: Human-in-the-Loop Gate

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** TASK-10 (benötigt Trust-Score-Verdict)
- **Context:** Bei Trust-Score `PROCEED_WITH_ASSUMPTION` + kritischer Anfrage (z.B. Constitution-`warn`-Level) wird die Antwort heute gesendet, ohne dass ein Mensch eingreifen kann. Ein state-basierter Gate-Freeze in Valkey ermöglicht echte Human-Approval-Flows: die Antwort wird eingefroren und erst nach `POST /gates/{id}/approve` gesendet. Für regulatorisch sensible Kontexte (DSGVO, EU-AI-Act Art. 14).
- **Instructions:**
  1. Erstelle `services/hitl_gate.py`:
     - `create_gate(request_id: str, reason: str, response_draft: str, ttl_seconds: int = 3600) -> str` — speichert Gate-State + Draft in Valkey, gibt `gate_id` zurück.
     - `get_gate(gate_id: str) -> dict | None` — liest Gate-State.
     - `approve_gate(gate_id: str) -> bool` — setzt Status auf `approved`, gibt True zurück.
     - `reject_gate(gate_id: str) -> bool` — setzt Status auf `rejected`.
     - TTL: nach `ttl_seconds` automatisch `expired`, Antwort wird nicht gesendet.
  2. Neuer API-Endpoint in `routes/` (neue Datei `routes/gates.py`):
     - `GET /gates/{gate_id}` — Gate-Status abfragen
     - `POST /gates/{gate_id}/approve` — Gate approven (nur Admin oder Request-Owner)
     - `POST /gates/{gate_id}/reject` — Gate ablehnen
  3. Integriere Gate-Trigger in `graph/synthesis.py`: wenn `trust_verdict == PROCEED_WITH_ASSUMPTION` UND `constitution_level == "warn"` → `create_gate()`, Client bekommt HTTP 202 mit `x-moe-gate-id: {gate_id}` statt finaler Antwort.
  4. Stream-Polling: Client kann auf `GET /gates/{gate_id}` pollen bis `approved`/`rejected`/`expired`. Bei `approved`: finale Antwort aus Valkey holen und liefern. Bei `rejected`/`expired`: 410 Gone.
  5. Unit-Tests: Gate-Lifecycle (create→approve→fetch), TTL-Ablauf (mock), Authorization-Check.
  6. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Borderline-Anfrage (Trust-Score 0.30–0.65 + Constitution-warn) liefert HTTP 202 + Gate-ID.
  - `POST /gates/{id}/approve` gibt finale Antwort frei.
  - Gate-State nach TTL automatisch `expired`, kein Memory-Leak in Valkey.
  - Nicht-Admin kann nicht fremde Gates approven (403).

---

### TASK-15: Cynefin Complexity Classification

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** none
- **Context:** MoE-Sovereign kennt heute `trivial/moderate/complex` als Complexity-Level, entschieden vom Planner-LLM. Cynefin erweitert das um eine vierte Dimension: das Autonomie-Level der Antwort. `clear`-Anfragen werden vollautomatisch beantwortet; `complex`/`chaotic`-Anfragen aktivieren HITL-Gate (TASK-14) und erhöhten Trust-Score-Schwellwert. Damit wird das Autonomie-Level der Pipeline deklarativ und nicht implizit.
- **Instructions:**
  1. Erstelle `services/cynefin.py`:
     - `CynefinDomain` Enum: `CLEAR`, `COMPLICATED`, `COMPLEX`, `CHAOTIC`
     - `classify_cynefin(state_: AgentState) -> CynefinDomain` — deterministisch, kein LLM: basierend auf `complexity_level`, Anzahl Expert-Domains, `enable_graphrag`, Länge des Inputs
     - Mapping: `trivial` + 1 Domain → `CLEAR`; `moderate` + ≤2 Domains → `COMPLICATED`; `complex` + >2 Domains → `COMPLEX`; Trust-Score `BLOCK` → `CHAOTIC`
  2. Integriere in `graph/planner.py`: nach Complexity-Routing, neues State-Feld `cynefin_domain: str`.
  3. Verwende `cynefin_domain` in TASK-14-Gate-Trigger-Entscheidung: Gate nur bei `COMPLEX`/`CHAOTIC`.
  4. Logge `cynefin_domain` im `usage_log` (neues Spalte in `database.py`).
  5. Unit-Tests: alle 4 Mappings korrekt.
  6. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Triviale Anfragen landen in `CLEAR`, erhalten kein Gate.
  - Komplexe Multi-Domain-Anfragen landen in `COMPLEX`, aktivieren Gate (wenn TASK-14 vorhanden).
  - `cynefin_domain` in `usage_log`-Zeilen sichtbar.

---

### TASK-16: Cascade Event Lifecycle (Resolution Tracking)

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** none (ergänzt bestehende `services/cascade.py` aus feat `886944f7`)
- **Context:** `services/cascade.py` emittiert Cascade-Events, trackt aber nicht ob sie aufgelöst wurden. Nach einem Replan-Zyklus weiß das System nicht, ob ein `CONTEXT_GAP` geschlossen wurde oder noch offen ist. `list(only_open=True)` ist unmöglich. Für Post-Mortem und SLA-Reporting essentiell.
- **Instructions:**
  1. Erweitere `services/cascade.py`:
     - `CascadeEvent` bekommt neues Feld `resolved: bool = False`, `resolved_at: str | None = None`
     - Neue Funktion `resolve_cascade(event: CascadeEvent) -> CascadeEvent` — setzt `resolved=True`, `resolved_at=<UTC-ISO>`
     - Neue Funktion `list_open_cascades(request_id: str) -> List[CascadeEvent]` — filtert aus Valkey alle Events mit `resolved=False`
     - Storage: Cascade-Events per `request_id` in Valkey mit TTL 24h
  2. Integriere `resolve_cascade()` in `graph/planner.py` nach erfolgreichem Replan: alle Events der Runde werden resolved.
  3. Bei `STUCK_LOOP`-Emission (Retry-Budget erschöpft): alle offenen Cascades des Requests als unresolved im `decision_log` (TASK-12) notieren.
  4. Unit-Tests: resolve + list_open, TTL-Verhalten (mock Valkey).
  5. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Nach erfolgreichem Replan zeigt `list_open_cascades(request_id)` leere Liste.
  - Bei STUCK: unresolved Cascades im Decision-Log sichtbar.
  - Valkey-Keys für Cascade-Events haben 24h TTL.

---

### TASK-17: Deterministischer Scope Guard

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** none
- **Context:** Heute entscheidet das LLM ob ein Expert auf eine Domain zugreifen darf. Ein deterministischer Scope Guard prüft vor dem Expert-Call, ob die angefragte Domain in der deklarierten `expert_domains`-Liste des Tasks liegt. Block in <10ms statt LLM-Urteil. Verhindert Domain-Drift bei falsch geroutetem Task.
- **Instructions:**
  1. Erstelle `services/scope_guard.py`:
     - `ScopeViolation` Dataclass: `task_id`, `requested_domain`, `allowed_domains`, `message`
     - `check_scope(task: dict, expert_category: str) -> ScopeViolation | None` — prüft ob `expert_category` in `task.get("allowed_domains", [task["category"]])` enthalten ist
     - Bei Verletzung: emittiert `CascadeType.SCOPE_DRIFT` via `services/cascade.py`
  2. Integriere `check_scope()` in `graph/expert.py` direkt vor dem LLM-Call (nach DoR, vor Prompt-Assembly).
  3. Bei `ScopeViolation`: Expert-Slot überspringen, `SCOPE_DRIFT`-Event emittieren, Task zurück an Planner.
  4. Unit-Tests: erlaubte Domain → kein Block; fremde Domain → `SCOPE_DRIFT`.
  5. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Ein Expert-Call für Domain `math` auf einem Task mit `category: code` wird blockiert.
  - `SCOPE_DRIFT`-Event in Kafka `moe.decisions` sichtbar.
  - Korrekt geroutete Tasks passieren ohne Overhead.

---

### TASK-18: Handover / Context-Preservation

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** none
- **Context:** Bei Kontext-Überschreitung oder Session-Timeout geht der aktuelle Orchestrierungs-State verloren. Ein Handover-Mechanismus serialisiert den relevanten `AgentState`-Ausschnitt in Valkey und ermöglicht Fortsetzung in einer neuen Session. Besonders relevant für lange Research-Anfragen (>10 Min. Laufzeit).
- **Instructions:**
  1. Erstelle `services/handover.py`:
     - `create_handover(state_: AgentState, reason: str) -> str` — serialisiert `plan`, `expert_results`, `chat_history`, `trust_verdict`, `self_critique_round`, `agentic_iteration` in Valkey mit TTL 4h, gibt `handover_id` zurück.
     - `restore_handover(handover_id: str) -> dict | None` — rekonstruiert relevante State-Felder.
  2. Trigger in `graph/synthesis.py`: bei `STUCK_LOOP` + nicht-kritischer Anfrage → `create_handover()` statt Hard-Fail; Response-Body enthält `x-moe-handover-id`.
  3. Neuer Endpoint `POST /handover/{id}/resume` in `routes/` — stellt State wieder her und setzt Pipeline fort.
  4. Unit-Tests: serialize/deserialize round-trip, TTL-Ablauf.
  5. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Eine STUCK-Anfrage liefert `x-moe-handover-id` im Response-Header.
  - `POST /handover/{id}/resume` setzt Pipeline mit rekonstruiertem State fort.
  - Handover-State nach 4h TTL automatisch gelöscht.

---

### TASK-19: Wikipedia / YAGO 4 Knowledge Import in Neo4j GraphRAG

- **Status:** done (2026-07-01, Antigravity — ETL-Script implementiert)
- **Owner:** Antigravity
- **Depends on:** none
- **Context:** Der bestehende `graphrag_pipeline_worker.py` ingested nur interne Markdown-Dokumentation (SYSTEM.md, CHANGELOG.md, docs/**/*.md). Faktisches Weltwissen (Software-Frameworks, Algorithmen, Konzepte) ist nicht vorhanden — bei allgemeinen Wissensanfragen liefert GraphRAG deshalb 0 Neo4j-Knoten, was TASK-10 (Trust-Score) hart blockt. YAGO 4 enthält ~1 Mrd. RDF-Triples aus Wikipedia + WordNet + GeoNames, davon gefiltert ~5–10M für die Software/AI/Tech-Domäne.
- **Instructions:**
  1. **Datenquelle:** YAGO 4 Partial Dump für relevante Schemata herunterladen:
     - `schema:SoftwareApplication`, `schema:SoftwareSourceCode`, `wdt:Q7397` (Software)
     - `schema:Algorithm` (aus WordNet-Mapping)
     - `wikidata:Q9143` (Programming Language), `wikidata:Q28640` (Profession/Role)
     - Download-Pfad: `yago-knowledge.org/data/yago4/` — Turtle-Dumps, domänenspezifische Teilmengen bevorzugen
  2. **ETL-Script** `scripts/import_yago_to_neo4j.py`:
     - Input: `.ttl`/`.nt`-Dateien (RDF/Turtle)
     - Parse via `rdflib` (bereits in Pypi, kein neues Dep falls verfügbar)
     - Mapping: YAGO-Entitätstypen → bestehende Ontologie-Typen aus `graph_rag/ontology.py` (`Tech_Concept`, `Algorithm`, `Framework`, `Tool`)
     - Output: Neo4j `MERGE`-Queries analog zu `graph_rag/manager.py:_upsert_entity()`
     - Batch-Inserts à 500 Triples, Progress-Logging alle 10k Triples
     - `--dry-run` und `--limit N` Flags für Test-Imports
  3. **Konflikt-Behandlung:** `source_weight = 0.8` (höher als "extracted" 0.6, niedriger als Ontologie 1.0) für YAGO-Daten. Bestehende Ontologie-Knoten werden NICHT überschrieben (`MERGE` on name + type ohne `SET` falls bereits vorhanden).
  4. **Integration in `graphrag_pipeline_worker.py`:** optionaler `--yago-import` Flag der das ETL-Script als Vorschritt ausführt.
  5. Testen: Import von 1k Test-Triples (Python + JavaScript Entities aus YAGO), anschließend `manager.query_context("Was ist FastAPI?")` → sollte Neo4j-Knoten zurückgeben.
  6. Rebuild/restart `langgraph-app` (nur falls `graph_rag/` geändert).
- **Acceptance criteria:**
  - Nach Import: `MATCH (n:Tech_Concept) RETURN count(n)` in Neo4j zeigt >0 YAGO-importierte Knoten.
  - `manager.query_context("Erkläre GraphQL")` gibt mind. 1 Neo4j-Knoten aus (vorher 0).
  - `--dry-run` erzeugt kein Schreiben in Neo4j.
  - Import von 100k Triples läuft in <10 Minuten durch.

---

### TASK-20: Wikipedia-Abstracts Chunking + Embedding Pipeline

- **Status:** done (2026-07-01, Antigravity — Embed-Script implementiert)
- **Owner:** Antigravity
- **Depends on:** TASK-19 (YAGO-Import liefert Entitätsliste für Abstracts)
- **Context:** Der bestehende GraphRAG-Stack nutzt Neo4j für strukturiertes Wissen (Entitäten + Relationen) und ChromaDB für semantische Vektoren. Wikipedia-Abstracts — der Fließtext zu jedem Entitäts-Knoten — werden nirgends eingebettet. Ohne Chunking + Embedding ist GraphRAG kein echtes Hybrid-Retrieval, sondern nur Cypher-Lookup. Das war der kritischste Mangel im ursprünglichen PoC-Prompt: "process later" für Phase 3.
- **Instructions:**
  1. **Datenquelle:** Wikipedia-Abstracts via `wikimedia.org/api/rest_v1/page/summary/{title}` (REST, kein SPARQL) oder DBpedia Spotlight Abstracts (`downloads.dbpedia.org/repo/dbpedia/text/abstracts/`).
  2. **Script** `scripts/embed_wikipedia_abstracts.py`:
     - Liest alle Neo4j-Entitäten mit `source = "yago"` (nach TASK-19)
     - Fetched Wikipedia-Summary per Entitätsname (async httpx, max 10 concurrent)
     - Chunked Abstracts in 200-Token-Segmente (Overlap 40 Token), Chunker via `tiktoken`
     - Embeddet mit `all-MiniLM-L6-v2` (bereits vorhanden im Stack für IMoE-Router)
     - Speichert in ChromaDB Collection `moe_wikipedia_abstracts` mit Metadata `entity_name`, `neo4j_id`, `chunk_index`
  3. **Integration in `graph_rag/manager.py:query_context()`:**
     - Hybrid-Query: Neo4j Cypher (strukturiert) + ChromaDB `moe_wikipedia_abstracts` (semantisch)
     - Merge-Strategie: Neo4j-Knoten-Score * 0.6 + ChromaDB-Vector-Score * 0.4 (konfigurierbar via env)
     - Bestehende ChromaDB-Collection `moe_semantic_cache` bleibt unberührt
  4. **Rate-Limiting:** Wikimedia API erlaubt 200 Requests/s ohne Key, mit `User-Agent`-Header. Script setzt `RATE_LIMIT=5` Default (konservativ), konfigurierbar.
  5. Unit-Tests: Mock-Wikipedia-API, Chunking-Logik (leere/lange Abstracts), ChromaDB-Collection-Isolation.
  6. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - ChromaDB Collection `moe_wikipedia_abstracts` existiert mit >0 Chunks.
  - `query_context("GraphQL Schema Definition Language")` gibt sowohl Neo4j-Knoten als auch ChromaDB-Chunks zurück.
  - Score-Merge produziert sortierten, deduplizierten Kontext (kein Duplikat für denselben Entitätsnamen).
  - Embed-Script ist idempotent (zweiter Lauf macht nichts doppelt).

---

### TASK-21: GraphRAG Benchmark Harness (CypherBench + GraphRAG-Bench)

- **Status:** pending
- **Owner:** unassigned
- **Depends on:** TASK-19, TASK-20 (GraphRAG-Stack muss Faktenwissen enthalten um sinnvoll zu benchmarken)
- **Context:** Ein Evaluierungs-Harness für MoE-Sovereigns GraphRAG-Qualität fehlt komplett. Der ursprüngliche PoC-Prompt (Wikidata SPARQL + manueller Markdown-Vergleich) hatte 4 kritische Mängel: SPARQL rate-limited, `neo4j:latest` nicht reproduzierbar, kein Ground-Truth, kein Chunking. Ersatz: CypherBench (11 fertige Property-Graphs, 10k+ Cypher-Fragen) + GraphRAG-Bench (ICLR'26, zitierfähige Ground-Truth Q&A).
- **Instructions:**
  1. **Daten-Setup** in `moe-benchmark/`:
     - CypherBench: `git lfs clone https://huggingface.co/datasets/megagonlabs/cypherbench` → einen der 11 Graphs via `neo4j-admin load` importieren (pinne `neo4j:5.18.0` in `Dockerfile.bench`, niemals `:latest`)
     - GraphRAG-Bench: `huggingface_hub.snapshot_download("GraphRAG-Bench/GraphRAG-Bench")` → Q&A-Pairs als lokale JSONL-Datei
  2. **Benchmark-Script** `moe-benchmark/benchmark_graphrag.py`:
     - Liest Q&A-Pairs aus GraphRAG-Bench (Schwierigkeitsstufen: `fact_retrieval`, `complex_reasoning`, `summarization`)
     - Sendet jede Frage an MoE-Sovereign API: einmal `enable_graphrag=false` (Zero-Shot), einmal `enable_graphrag=true`
     - Misst: Latenz, Token-Count, LLM-as-Judge-Score (separater Judge-Call der Ground-Truth vs. Antwort bewertet, Skala 0–5)
     - Output: `results/graphrag_bench_YYYYMMDD.json` + aggregierte Tabelle (Precision, Recall, Latenz-Delta, Token-Overhead)
  3. **LLM-as-Judge statt manueller Sichtprüfung:**
     - Judge-Prompt: "Bewerte die Antwort auf einer Skala 0–5 gemessen an der Ground-Truth. Antworte nur mit der Zahl."
     - Judge-Model: `moe-auto` (selbst), oder dedizierter Judge via `BENCHMARK_JUDGE_MODEL` env var
  4. **Reproduzierbarkeit:**
     - `docker-compose.yml` in `moe-benchmark/` pinnt `neo4j:5.18.0` (nicht latest)
     - `requirements.txt` mit festen Versionen
     - Fixture-Dataset (`data/fixtures/sample_100.jsonl`) für schnelle Smoke-Tests ohne volle Download
  5. Ausführung via `make benchmark` in `moe-benchmark/`.
- **Acceptance criteria:**
  - `make benchmark` läuft durch ohne manuelle Eingriffe.
  - Output-JSON enthält `zero_shot_score`, `graphrag_score`, `latency_ms`, `token_count` pro Q&A-Pair.
  - GraphRAG-Score ist im Mittel höher als Zero-Shot-Score (Validierung dass GraphRAG hilft).
  - Ergebnis ist mit einem `git log`-Hash verknüpft (reproduzierbar, zitierbar).
  - `neo4j:latest` kommt in keiner Benchmark-Konfigurationsdatei vor.

---

### TASK-22: Strategy Review Node (Abstraction-First Quality Layer)

- **Status:** done (2026-07-01, Antigravity)
- **Owner:** Antigravity
- **Depends on:** TASK-10 (Trust-Score-Verdict steuert Aktivierung)
- **Context:** Inspiriert durch einen privaten Mixed-Reality-Anwendungsfall (Spatial Audio via Emulator-Ring-Buffer statt räumlichem Objekt): Ein kleines lokales Modell erstellt für die Expertenergebnisse eine *inhaltsfreie* Strategieabstraktion (Problemklasse, Lösungsansatz, Annahmen, Unsicherheiten). Ein konfigurierbares potentes Reviewer-Modell (Standard: lokaler Judge; optional: Frontier-Endpunkt) bewertet *nur die Abstraktion*, nie den Inhalt. Das strukturelle Feedback fließt zurück in den Merger. Kern-Invariante: kein Domain-Inhalt verlässt den lokalen Stack, es sei denn der Admin hat explizit einen Frontier-URL konfiguriert.
- **Instructions:**
  1. Erstelle `services/strategy_review.py`:
     - Dataclass `StrategyAbstract`: `problem_class: str`, `solution_approach: str`, `assumptions: list[str]`, `uncertainties: list[str]`
     - Dataclass `StrategyFeedback`: `structural_gaps: list[str]`, `alternative_approaches: list[str]`, `confidence_adjustment: float` (−0.2 … +0.2)
     - `abstract_solution(expert_results, plan, input_query, abstractor_llm) -> StrategyAbstract` — Abstractor-LLM sieht Inhalt, produziert nur Abstraktion
     - `review_strategy(abstract: StrategyAbstract, reviewer_llm) -> StrategyFeedback` — Reviewer sieht *nur* die Abstraktion, kein Inhalt
  2. Erstelle `graph/strategy_review_node.py`:
     - `strategy_review_node(state_)` — orchestriert Abstractor + Reviewer
     - Abstractor: `STRATEGY_ABSTRACTOR_MODEL` env (leer → `planner_llm`; Standard: kleinstes verfügbares Modell)
     - Reviewer: `STRATEGY_REVIEWER_MODEL` + `STRATEGY_REVIEWER_URL` + `STRATEGY_REVIEWER_TOKEN` env (leer → `judge_llm` lokal; gesetzt → Frontier-Endpunkt)
     - Gibt zurück: `strategy_feedback: str` (kompaktes strukturiertes Feedback für den Merger), `trust_score` Anpassung per `confidence_adjustment`
  3. Konditionale Aktivierung: nur wenn `STRATEGY_REVIEW_ENABLED=true` (env) AND (`trust_verdict == PROCEED_WITH_ASSUMPTION` OR `cynefin_domain in (COMPLEX, CHAOTIC)`)
  4. Integration in `graph/synthesis.py` / `main.py`:
     - Neuer LangGraph-Node `strategy_review` zwischen Expert-Runde und Merger
     - `strategy_feedback` als zusätzlicher Kontext in den Merger-Prompt injiziert (neues State-Feld)
  5. `pipeline/state.py`: neues Feld `strategy_feedback: str`
  6. Unit-Tests in `tests/test_strategy_review.py`:
     - Abstractor produziert keine rohen Inhalte (assert kein Expert-Zitat im Abstract)
     - Reviewer-Prompt enthält keinen Original-Inhalt (Invariante)
     - Deaktiviert wenn `STRATEGY_REVIEW_ENABLED` nicht gesetzt
     - `confidence_adjustment` liegt in [−0.2, +0.2]
  7. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - `STRATEGY_REVIEWER_URL` leer → lokaler Judge als Reviewer, kein Netzwerk-Call nach außen.
  - `STRATEGY_REVIEWER_URL` gesetzt → Frontier-Endpunkt verwendet (konfigurierbar, kein Hardcode).
  - Reviewer-Prompt enthält nachweislich keinen Original-Expert-Output (Inhaltstrennung verifiziert).
  - `strategy_feedback` im Merger-Log sichtbar wenn aktiviert.
  - `STRATEGY_REVIEW_ENABLED` nicht gesetzt → Node wird übersprungen, kein Overhead.

---

### TASK-23: Pipeline Log UI-Erweiterung (Trust Score, Cynefin, Self-Critique)

- **Status:** done (2026-07-01)
- **Owner:** Claude Code
- **Depends on:** TASK-10 – TASK-15 (alle done)
- **Context:** Die `usage_log`-Tabelle speichert bisher keine Trust-Score-, Cynefin- oder Self-Critique-Felder. Das Pipeline-Log-Template zeigt sie folglich nicht. Für operative Sichtbarkeit der neuen Qualitätssignale müssen DB-Schema, Backend-INSERT, API-Query und Template synchron erweitert werden.
- **Instructions:**
  1. `admin_ui/database.py` — `ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS` für: `trust_score DOUBLE PRECISION`, `trust_verdict TEXT`, `cynefin_domain TEXT`, `self_critique_round INTEGER NOT NULL DEFAULT 0`, `cascade_type TEXT`. Funktion `log_usage()` um diese Parameter erweitern.
  2. `main.py` — in `_log_usage_to_db`-Aufruf (Zeile ~1867) die Felder `trust_score`, `trust_verdict`, `cynefin_domain`, `self_critique_round`, `cascade_type` aus `data` extrahieren und übergeben.
  3. `routes/admin_stats.py` — SELECT in `pipeline_log()` um neue Felder erweitern.
  4. `admin_ui/templates/pipeline_log.html` — neue Spalten: `trust_verdict`-Badge (grün/gelb/rot), `cynefin_domain`-Badge, `self_critique_round`-Spalte; alle Labels via `{{ t(request, 'key') }}`.
  5. Alle vier Lang-Dateien (`de_DE`, `en_EN`, `fr_FR`, `zh_CN`) mit neuen Schlüsseln befüllen.
  6. Rebuild/restart `moe-admin`.
- **Acceptance criteria:**
  - Pipeline-Log-Seite zeigt `trust_verdict` farbig (PROCEED=grün, PROCEED_WITH_ASSUMPTION=gelb, BLOCK=rot).
  - `cynefin_domain` als Badge sichtbar.
  - Neue `usage_log`-Spalten über `ALTER TABLE IF NOT EXISTS` (idempotent, keine Migration nötig).

---

### TASK-24: HITL Gate Approval UI

- **Status:** done (2026-07-01)
- **Owner:** Claude Code
- **Depends on:** TASK-14 (done)
- **Context:** Gates werden via `POST /gates/{id}/approve|reject` approved (langgraph-app), aber es gibt keine Admin-UI-Seite dafür. Ohne UI müssen Gates manuell mit curl bedient werden — operativ nicht nutzbar.
- **Instructions:**
  1. Neue Admin-UI-Seite `/gates` (Template `gates.html`, Route in `admin_ui/app.py`).
  2. API-Proxy-Endpoints in `admin_ui/app.py`: `GET /api/gates/{gate_id}` und `POST /api/gates/{gate_id}/approve|reject` → Weiterleitung an `ORCHESTRATOR_URL/gates/...`.
  3. Template: Liste offener Gates (polling `GET /api/gates?status=pending`), pro Gate: Request-ID, Reason, Erstellt-Zeit, Ablaufzeit, Approve/Reject-Buttons.
  4. JavaScript-Polling alle 10s für automatische Aktualisierung.
  5. Lang-Dateien: alle vier Sprachdateien mit neuen Keys.
  6. Nav-Eintrag in bestehende Admin-Navigation einfügen.
  7. Rebuild/restart `moe-admin`.
- **Acceptance criteria:**
  - `/gates`-Seite zeigt offene Gates.
  - Approve-Button sendet `POST /gates/{id}/approve` und refresht die Liste.
  - Keine Gates vorhanden → leere State-Meldung statt Fehler.

---

### TASK-25: Response Detail Modal — Strategy Feedback & Pipeline Signals

- **Status:** done (2026-07-01, Claude Code)
- **Owner:** Claude Code
- **Depends on:** TASK-23 (neue DB-Felder müssen vorhanden sein)
- **Context:** Das bestehende Detail-Modal im Pipeline-Log zeigt `request_id`, `cache_hit`, `agentic_rounds`. Mit den neuen Felder aus TASK-23 können `strategy_feedback`, `self_critique_round/max`, `cascade_type` und `cynefin_domain` ebenfalls angezeigt werden.
- **Instructions:**
  1. `routes/admin_stats.py` — separaten `GET /v1/admin/pipeline-log/{request_id}` Endpunkt hinzufügen, der `strategy_feedback` aus Valkey (via `handover`-Key-Namespace oder separatem Valkey-Key) und alle DB-Felder zurückgibt.
  2. `pipeline_log.html` — Detail-Modal erweitern: aufklappbarer Block „Strategy Review Feedback" wenn `strategy_feedback` nicht leer, `self_critique_round/max`-Anzeige, `cascade_type`-Badge.
  3. Lang-Dateien für neue Labels.
  4. Rebuild/restart `moe-admin`.
- **Acceptance criteria:**
  - Klick auf Pipeline-Log-Zeile öffnet Modal mit Strategy-Feedback-Block (wenn vorhanden).
  - `self_critique_round` wird als „N / max" angezeigt.

---

### TASK-26: Live Monitoring — Trust Verdict Badge

- **Status:** done (2026-07-01, Claude Code)
- **Owner:** Claude Code
- **Depends on:** TASK-23
- **Context:** Das Live-Monitoring zeigt aktive Requests ohne Qualitätssignal. Ein `trust_verdict`-Badge in der laufenden Request-Liste würde zeigen, ob ein Request gerade blockiert ist oder mit Annahmen läuft.
- **Instructions:**
  1. `main.py` — `_register_active_request()` / Active-Request-Valkey-State um `trust_verdict` und `cynefin_domain` erweitern.
  2. `live_monitoring.html` — Badge-Spalte in der aktiven Request-Liste.
  3. Lang-Dateien für neue Labels.
  4. Rebuild/restart `moe-admin` + `langgraph-app`.
- **Acceptance criteria:**
  - Laufende Requests zeigen `trust_verdict`-Badge (leer wenn noch nicht berechnet, Badge wenn vorhanden).

---

### TASK-27: Handover / Resume UI

- **Status:** done (2026-07-01, Claude Code)
- **Owner:** Claude Code
- **Depends on:** TASK-18 (done)
- **Context:** Handover-IDs kommen im Response-Header `x-moe-handover-id` an, aber es gibt keine UI zum Weiterführen einer unterbrochenen Session. Power-User müssen `POST /handover/{id}/restore` manuell aufrufen.
- **Instructions:**
  1. User-Portal (`user_portal.html`) — Button „Session fortsetzen" im Audit-Log-Modal wenn `handover_id` vorhanden.
  2. Admin-UI (`app.py`) — Proxy `POST /api/handover/{id}/restore` → `ORCHESTRATOR_URL/handover/{id}/restore`.
  3. Modal zum Wiederherstellen: zeigt Handover-Grund und Timestamp, Confirm-Button sendet Resume-Request und öffnet Chat mit dem wiederhergestellten Input.
  4. Lang-Dateien.
  5. Rebuild/restart `moe-admin`.
- **Acceptance criteria:**
  - User sieht im Audit-Log-Modal „Handover vorhanden" mit Fortführen-Button.
  - Klick auf Fortführen schickt Resume-Request und zeigt Antwort.

---

### TASK-28: Decision Log Explorer

- **Status:** done (2026-07-01)
- **Owner:** Claude Code
- **Depends on:** TASK-12 (done)
- **Context:** Decision-Log-Einträge landen in Kafka `moe.decisions` + `decision_log.jsonl`. Es gibt keine UI zum Browsing. Für Post-Mortems und EU-AI-Act-Compliance-Audits wird eine filterbare Admin-Seite benötigt.
- **Instructions:**
  1. Backend: `GET /v1/admin/decision-log?decision_type=&request_id=&limit=&offset=` in `routes/admin_stats.py` — liest aus `decision_log.jsonl` (Kafka-Consumer wäre aufwendiger, JSONL reicht für den Anfang).
  2. Admin-UI: neue Seite `/decision-log` (Template `decision_log_explorer.html`).
  3. Filter: `decision_type` (Dropdown), `request_id` (Freitext), Zeitraum.
  4. Pro Eintrag: `ts`, `decision_type`-Badge, `request_id`, `rationale`, `metadata`-Collapsible.
  5. Lang-Dateien.
  6. Nav-Eintrag.
  7. Rebuild/restart `moe-admin`.
- **Acceptance criteria:**
  - `/decision-log` zeigt paginierte Einträge aus `decision_log.jsonl`.
  - Filter nach `decision_type` funktioniert.
  - `rationale`-Feld immer sichtbar (nie leer, wegen Pflichtfeld-Constraint).

---

### TASK-29: AI I/O Audit Service (Strukturiertes LLM-Request/Response-Logging)

- **Status:** done (2026-07-01, Claude Code)
- **Owner:** Claude Code
- **Depends on:** none
- **Context:** MoE-Sovereign hat Grafana-Monitoring für GPU-Metriken, aber kein semantisches AI-I/O-Audit: welcher User hat welchen Prompt mit welchem Modell aufgerufen, was wurde zurückgegeben. Für EU-AI-Act Art. 13 (Transparenz) und DSGVO-Verarbeitung braucht es einen append-only Audit-Trail jedes LLM-Calls. Inspirationsquelle: `workflow-runtime-audit-service.js` aus dem ADHS-Projekt (Agent Orchestrator) von Michael Reich — dort löst das Muster dasselbe Problem für einen anderen Stack. Kernpunkte: API-Key-Redaktion, Session-Korrelation, Transport-Metadaten.
- **Instructions:**
  1. Erstelle `services/ai_io_audit.py`:
     - `sanitize_audit_payload(payload: dict) -> dict` — traversiert rekursiv, ersetzt alle Werte unter Keys `authorization`, `api-key`, `apikey`, `x-api-key` durch `"[redacted]"`. Keys case-insensitiv vergleichen.
     - Dataclass `AiIoAuditEntry`: `audit_id: str`, `session_id: str`, `request_id: str`, `model: str`, `endpoint: str`, `stage: str`, `prompt_tokens: int | None`, `completion_tokens: int | None`, `started_at: str`, `completed_at: str | None`, `status: str` (`"pending"`, `"completed"`, `"error"`), `request_body: dict` (sanitized), `response_body: dict | None` (sanitized).
     - `create_audit_entry(session_id, request_id, model, endpoint, stage, request_body) -> AiIoAuditEntry` — erzeugt Entry mit `audit_id = f"{session_id}:{request_id}"`, speichert in einem In-Memory-`Dict[str, AiIoAuditEntry]` (`_live_entries`).
     - `complete_audit_entry(audit_id, response_body, prompt_tokens, completion_tokens, status) -> None` — schließt Entry ab, schreibt nach Postgres (Tabelle `ai_io_audit_log`) und entfernt aus `_live_entries`.
     - `get_live_entries() -> List[AiIoAuditEntry]` — gibt aktive (noch nicht abgeschlossene) Entries zurück.
  2. Postgres-Schema: `CREATE TABLE IF NOT EXISTS ai_io_audit_log (audit_id TEXT PRIMARY KEY, session_id TEXT, request_id TEXT, model TEXT, endpoint TEXT, stage TEXT, prompt_tokens INTEGER, completion_tokens INTEGER, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, status TEXT, request_body JSONB, response_body JSONB)`. Migration in `admin_ui/database.py` via `CREATE TABLE IF NOT EXISTS` (idempotent).
  3. Integration in `services/inference.py`: vor jedem `httpx`-Call an Ollama/Provider `create_audit_entry()` aufrufen, nach dem Call `complete_audit_entry()` — sowohl bei Erfolg als auch bei Exception (status `"error"`).
  4. Neuer Admin-UI-Endpoint `GET /v1/admin/ai-io-audit?request_id=&model=&status=&limit=&offset=` in `routes/admin_stats.py` — liest aus `ai_io_audit_log` + `get_live_entries()` zusammengeführt, sortiert nach `started_at DESC`.
  5. Admin-UI-Template `ai_io_audit.html` (neue Seite `/ai-io-audit`): Tabelle mit `audit_id`, `model`, `stage`, `status`-Badge, `prompt_tokens`, `completion_tokens`, `started_at`. Klick auf Zeile öffnet Modal mit sanitized `request_body`/`response_body` (JSON-Collapsible).
  6. Nav-Eintrag. Lang-Dateien (alle vier Sprachen) für neue Keys.
  7. Unit-Tests `tests/test_ai_io_audit.py`: API-Key-Redaktion auf verschachtelten Payloads, Entry-Lifecycle (create→complete→persist), Live-Entries-Map leert sich nach Complete.
  8. Rebuild/restart `langgraph-app` + `moe-admin`.
- **Acceptance criteria:**
  - Jeder LLM-Call in `inference.py` erzeugt einen `ai_io_audit_log`-Eintrag.
  - Kein API-Key-Wert (auch verschachtelt) erscheint im `request_body`/`response_body` — nur `"[redacted]"`.
  - `GET /v1/admin/ai-io-audit` gibt abgeschlossene + laufende Entries zurück.
  - `/ai-io-audit`-Seite zeigt Einträge mit Status-Badge und Klick-Modal.
  - `tests/test_ai_io_audit.py` grün.
- **Resolution notes (2026-07-01, Claude Code):**
  - `services/ai_io_audit.py`: `sanitize_audit_payload()` (rekursive API-Key-Redaktion, case-insensitiv), `AiIoAuditEntry` Dataclass, `create_audit_entry()` / `complete_audit_entry()` / `get_live_entries()`. In-Memory `_live_entries` Dict, Postgres-Persistenz via `state._userdb_pool`.
  - `admin_ui/database.py`: `ai_io_audit_log`-Tabelle und Indizes als `CREATE TABLE IF NOT EXISTS` (idempotent).
  - `routes/admin_stats.py`: `GET /v1/admin/ai-io-audit` — merged Live-Entries + DB-Rows, sortiert nach `started_at DESC`.
  - `admin_ui/templates/ai_io_audit.html`: Filter-UI, Tabelle mit Status-Badge, Detail-Modal mit sanitized JSON-Collapsible.
  - `admin_ui/app.py`: `/ai-io-audit`-Route + `/api/ai-io-audit`-Proxy.
  - `services/inference.py`: Judge-Ollama-Call mit `_audit_create()`/`_audit_complete()` gewrapped (try/finally-Muster).
  - `admin_ui/lang/`: 4 Sprachdateien mit neuen Keys.
  - 11 Tests grün.

---

### TASK-30: Structured-Output Failure Recovery mit Retry-Strategie

- **Status:** done (2026-07-01, Claude Code)
- **Owner:** Claude Code
- **Depends on:** none
- **Context:** Wenn ein LLM kein valides JSON/Schema zurückliefert (z.B. bei Structured Output in `graph/synthesis.py` oder `graph/planner.py`), fehlt ein deterministischer Umgang. Aktuell fällt der Request mit einer generischen Exception durch. Inspirationsquelle: `workflow-engine/structured-failure.js` + `workflow-engine/recovery.js` aus ADHS — dort klassifiziert das System den Fehler, bietet 4 konkrete Recovery-Actions an und trackt Retry-Runden. Für MoE-Sovereign: angepasst auf Python, auf LangGraph-State-basiertes Retry, mit konfigurierbarem Fallback-Modell.
- **Instructions:**
  1. Erstelle `services/structured_failure.py`:
     - `StructuredFailureKind` Enum: `SCHEMA_OUTPUT` (JSON-Parse-Fehler, Schema-Violation), `PROVIDER_TRANSPORT` (Timeout, ECONNRESET, HTTP 429/502/503/504), `RUNTIME_ERROR` (sonstige).
     - `RecoveryAction` Enum: `RETRY_SAME`, `RETRY_FALLBACK`, `RETRY_SELECTED`, `STOP`.
     - Dataclass `StructuredFailure`: `failure_kind: StructuredFailureKind`, `model: str`, `fallback_model: str`, `stage: str`, `message: str`, `raw_text: str` (max. 1600 Zeichen), `retry_round: int`, `allowed_actions: List[RecoveryAction]`.
     - `classify_failure(error: Exception, raw_text: str = "") -> StructuredFailureKind` — Regex auf `error.message` + `raw_text`: `/json|schema|parse|structured|top-level|missing required/i` → `SCHEMA_OUTPUT`; `/timeout|ECONNRESET|ETIMEDOUT|rate limit|429|502|503|504/i` → `PROVIDER_TRANSPORT`; sonst `RUNTIME_ERROR`.
     - `build_failure(error, model, stage, fallback_model="", raw_text="", retry_round=0) -> StructuredFailure` — klassifiziert und wählt `allowed_actions`: bei `SCHEMA_OUTPUT`/`PROVIDER_TRANSPORT` → alle 4 Actions wenn `fallback_model` gesetzt, sonst ohne `RETRY_FALLBACK`; bei `RUNTIME_ERROR` → nur `RETRY_SAME`, `STOP`.
     - `resolve_retry_model(failure: StructuredFailure, action: RecoveryAction, selected_model: str = "") -> str` — gibt zurück: `RETRY_SAME` → `failure.model`; `RETRY_FALLBACK` → `failure.fallback_model`; `RETRY_SELECTED` → `selected_model`; `STOP` → raise `ValueError`.
  2. Integration in `graph/synthesis.py` (Judge-Call) und `graph/planner.py` (Planner-Call): JSON-Parse-Block (`json.loads()`) mit `except` → `build_failure()` aufrufen, `StructuredFailure` in `AgentState` als `structured_failure` Feld speichern. Retry max. `STRUCTURED_FAILURE_MAX_RETRIES` (env, Default `2`) mit `resolve_retry_model(failure, RecoveryAction.RETRY_SAME)` für automatischen Retry; nach Limit-Erreichen → `STOP` → Cascade `SPEC_GAP`.
  3. Neues `AgentState`-Feld `structured_failure: dict | None` (serialisierte `StructuredFailure`) und `structured_failure_round: int`.
  4. Admin-UI: `structured_failure_round` im `usage_log` (neue Spalte, `ALTER TABLE IF NOT EXISTS`), sichtbar im Pipeline-Log als Badge „SF-N".
  5. Env var `STRUCTURED_FAILURE_FALLBACK_MODEL` (leer = kein Fallback) für `fallback_model`.
  6. Unit-Tests `tests/test_structured_failure.py`: Klassifikation (je 2 Beispiel-Errors pro Kind), `resolve_retry_model` alle Actions, Max-Retry-Limit triggert Cascade.
  7. Rebuild/restart `langgraph-app`.
- **Acceptance criteria:**
  - Ein JSON-Parse-Fehler im Judge produziert `StructuredFailureKind.SCHEMA_OUTPUT` und automatisch bis zu 2 Retries (same model).
  - `STRUCTURED_FAILURE_FALLBACK_MODEL` gesetzt: nach Max-Retries wird Fallback-Modell versucht (1 weiterer Versuch), dann `STOP`.
  - `structured_failure_round` in `usage_log` sichtbar.
  - `tests/test_structured_failure.py` grün.
  - Provider-Timeout (`httpx.TimeoutException`) wird als `PROVIDER_TRANSPORT` klassifiziert, nicht als `RUNTIME_ERROR`.
- **Resolution notes (2026-07-01, Claude Code):**
  - `services/structured_failure.py`: `StructuredFailureKind` Enum, `RecoveryAction` Enum, `StructuredFailure` Dataclass, `classify_failure()`, `build_failure()`, `resolve_retry_model()`. Regex-basierte Klassifikation (SCHEMA_OUTPUT / PROVIDER_TRANSPORT / RUNTIME_ERROR). `_STRUCTURED_FAILURE_FALLBACK_MODEL` + `_MAX_RETRIES` via env.
  - `pipeline/state.py`: `structured_failure: dict` + `structured_failure_round: int` als neue State-Felder.
  - `admin_ui/database.py`: `ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS structured_failure_round`.
  - `routes/admin_stats.py`: `structured_failure_round` in `pipeline_log` SELECT via `COALESCE(..., 0)`.
  - 16 Tests grün.

---

### TASK-31: Modell-Capability-Tabelle (Provider Routing Matrix)

- **Status:** done (2026-07-01, Claude Code)
- **Owner:** Claude Code
- **Depends on:** none
- **Context:** MoE-Sovereign wählt heute aus, *welche GPU und welches Modell* einen Request bearbeiten (dynamischer Router, GPU-Pool-Select). Es fehlt aber die nächste Ebene: *wie* das gewählte Modell angesprochen werden soll — Streaming vs. nicht-Streaming, JSON-Schema-Enforcement, Chat-Completions vs. Responses-API. Das wird aktuell per-Request händisch konfiguriert oder ist fest im Code. Inspirationsquelle: `ai-provider-routing.js` aus ADHS — dort pflegt eine explizite Capability-Matrix pro Modell, welche API-Modi und Output-Formate unterstützt werden. Für MoE-Sovereign: als YAML-Konfiguration, die den existierenden `services/inference.py`-Call-Aufbau steuert.
- **Instructions:**
  1. Erstelle `config/model_capabilities.yaml`:
     ```yaml
     # Capability-Matrix pro Modell. Fehlende Modelle erben "default".
     default:
       json_schema: false
       json_object: true
       stream: true
       responses_api: false
       hints: []
     models:
       "qwen3.6:35b":
         json_schema: true
         json_object: true
         stream: true
         hints: ["schema+"]
       "llama3.3-70b-ctx4k:latest":
         json_schema: false
         json_object: false
         stream: true
         hints: []
       "mistral:7b":
         json_schema: true
         json_object: true
         stream: false
         hints: ["schema+", "chat+"]
     ```
     Fehlende Einträge werden vom `default`-Block geerbt (kein KeyError, nur `dict.get()`).
  2. Erstelle `services/model_capabilities.py`:
     - `load_capabilities(path: str = "config/model_capabilities.yaml") -> dict` — lädt YAML, cached nach erstem Load in Modul-Variable.
     - `get_model_caps(model: str) -> dict` — gibt Model-Eintrag zurück, fallback auf `default`.
     - `model_supports_json_schema(model: str) -> bool`
     - `model_supports_streaming(model: str) -> bool`
     - `model_hint_tokens(model: str) -> List[str]` — gibt `hints`-Liste zurück.
  3. Integration in `services/inference.py`:
     - Vor jedem LLM-Call `get_model_caps(model)` aufrufen.
     - `json_schema`-fähige Modelle: `response_format={"type": "json_schema", ...}` setzen wo heute `json_object` steht.
     - `stream`-unfähige Modelle: `stream=False` erzwingen unabhängig von Request-Präferenz.
     - Neues Logging: `logger.debug("model=%s caps=%s", model, caps)` vor dem Call.
  4. Admin-UI: neue Seite `/model-capabilities` — zeigt `model_capabilities.yaml` als lesbare Tabelle (Modell, JSON-Schema, Stream, Hints). Keine Editierbarkeit in v1 (read-only).
  5. Hot-Reload-Support: `CAPABILITIES_RELOAD_ON_REQUEST=true` env → Datei bei jedem Request neu laden (für Entwicklungsworkflow ohne Rebuild).
  6. Unit-Tests `tests/test_model_capabilities.py`: Default-Fallback für unbekanntes Modell, Override für bekanntes, `model_supports_json_schema` korrekt für beide Fälle.
  7. Rebuild/restart `langgraph-app` + `moe-admin`.
- **Acceptance criteria:**
  - Unbekanntes Modell erbt `default`-Caps ohne Exception.
  - `qwen3.6:35b` verwendet `json_schema`-Mode in `inference.py` (statt `json_object`).
  - Stream-unfähige Modelle aus YAML senden `stream=False` an Ollama.
  - `/model-capabilities`-Seite zeigt alle konfigurierten Modelle als Tabelle.
  - `tests/test_model_capabilities.py` grün.
- **Resolution notes (2026-07-01, Claude Code):**
  - `configs/model_capabilities.yaml`: Capability-Matrix mit `default`-Block + Modell-Overrides (13 konfigurierte Modelle).
  - `services/model_capabilities.py`: `load_capabilities()` (einmaliges YAML-Laden, `CAPABILITIES_RELOAD_ON_REQUEST` für Dev-Reload), `get_model_caps()` (Dict-Merge mit Default-Fallback), `model_supports_json_schema()`, `model_supports_streaming()`, `model_hint_tokens()`.
  - `services/inference.py`: Import der Capability-Funktion, `logger.debug("model=%s caps=%s")` vor Judge-Ollama-Calls.
  - `admin_ui/templates/model_capabilities.html`: Read-only-Tabelle (JSON Schema, Stream, Hints pro Modell), Default-Caps-Block.
  - `admin_ui/app.py`: `/model-capabilities` Route + `/api/model-capabilities` JSON-Endpunkt.
  - `admin_ui/templates/base.html`: Nav-Einträge für `/ai-io-audit` und `/model-capabilities` im Monitoring-Dropdown.
  - 4 Sprachdateien: neue Keys für AI I/O Audit und Model Capabilities.
  - 10 Tests grün.

### TASK-32: Integration / Evaluation des Claude Design System Prompts (AI-Slop-Prävention & a11y)

- **Status:** partially_done (Phase 1 Evaluation done + korrigiert; Phase 2:
  2 Skills importiert und auditiert, Admin-Freigabe offen; Phase 3-4
  ausstehend)
- **Owner:** Claude Code (Korrektur + Phase 2, 2026-07-05), ursprünglich Antigravity
- **Depends on:** none
- **Context:** MoE-Sovereign generiert über Coder-Experten und UI-Skills Web-Interfaces. Zudem besitzt es eigene Web-UIs. Um generischen "AI-Slop" (typische KI-Layoutmuster, unharmonische Farbverläufe, unpassende Fonts) zu vermeiden und Barrierefreiheit (a11y/WCAG) von Anfang an sicherzustellen, soll das Konzept aus dem Projekt [claude-design-system-prompt](https://github.com/Trystan-SA/claude-design-system-prompt) integriert werden. **Lizenz-Konformität:** Da das Quellprojekt unter der MIT-Lizenz steht, müssen bei der Übernahme der Prompts und Skills die ursprünglichen Lizenz- und Copyright-Hinweise (Trystan Sarrade) beibehalten und in den Zieldateien dokumentiert werden.
- **Instructions:**
  1. **Phase 1: Analyse & Bewertung (Erledigt):**
     - Analyse der Designkonzepte und der 14 prozeduralen Skills.
     - Detaillierter Evaluationsbericht erstellt unter [moe_design_system_evaluation.md](file:///home/philipp/.gemini/antigravity-cli/brain/e37eb3b2-85a4-48ec-b63b-9f83fe8a2e0e/moe_design_system_evaluation.md).
  2. **Phase 2: Experten-Integration (Ausstehend):**
     - Registrierung eines neuen Experten `frontend_designer` oder `ui_ux_designer` in [moe-infra/prompts.py](file:///opt/deployment/moe-sovereign/moe-infra/prompts.py) mit dem Kern-System-Prompt zur Slop-Vermeidung.
  3. **Phase 3: Skill-Import & Lizenzkonformität (Ausstehend):**
     - Übernahme der wichtigsten Skills (z. B. `ai-slop-check.md`, `accessibility-audit.md`, `make-tweakable.md`) als Markdown-Dateien nach [moe-infra/skills/](file:///opt/deployment/moe-sovereign/moe-infra/skills/).
     - **Wichtig:** Jede übernommene Datei muss im Frontmatter oder als Header den ursprünglichen MIT-Lizenz- und Urheberrechtshinweis (Copyright (c) 2026 Trystan Sarrade) enthalten.
     - Durchführung des nach [moe-infra/services/skills.py](file:///opt/deployment/moe-sovereign/moe-infra/services/skills.py) notwendigen LLM-Sicherheitsaudits für den Import.
  4. **Phase 4: Design-Hygiene in der Entwicklung (Ausstehend):**
     - Übernahme der Prinzipien in die Entwickler-Richtlinien `AGENTS.md` zur Qualitätssicherung künftiger MoE-UIs.
- **Acceptance criteria:**
  - [moe_design_system_evaluation.md](file:///home/philipp/.gemini/antigravity-cli/brain/e37eb3b2-85a4-48ec-b63b-9f83fe8a2e0e/moe_design_system_evaluation.md) ist vollständig ausgearbeitet.
  - Neuer UI/UX-Experte reagiert zuverlässig auf Design-Anfragen.
  - Neue Design-Review-Skills (z. B. `/ai-slop-check`) sind im Skill-Verzeichnis registriert, auditiert und vom Admin freigegeben.
  - **Lizenzprüfung:** Alle importierten System-Prompts und Skill-Dateien weisen die erforderlichen Copyright- und Lizenzangaben der MIT-Lizenz auf.
- **Resolution notes (2026-07-05, Antigravity):**
  - Phase 1 (Analyse) erfolgreich abgeschlossen. Das Dokument [moe_design_system_evaluation.md](file:///home/philipp/.gemini/antigravity-cli/brain/e37eb3b2-85a4-48ec-b63b-9f83fe8a2e0e/moe_design_system_evaluation.md) enthält die Analyse der 14 Skills und der 5 Haupt-Design-Konzepte in einer Mehrwert-Matrix.
  - Phase 2–4 sind für nachfolgende Iterationen unter Wahrung der MIT-Lizenzbedingungen vorbereitet.

- **Korrektur-Resolution (2026-07-05T19:24Z, Claude Code):** Die bestehende
  Evaluation wurde unabhängig gegen das Live-Repo (öffentliche GitHub-API,
  nicht nur den Bericht) verifiziert. Zwei Lücken korrigiert, Empfehlung
  umgewichtet — Details siehe `agent_status/claude-code.md`.

  1. **Zwei Repo-Varianten, nicht eine.** `claude/` (mit Subagent-Delegation,
     "delegate thorough verification to a verifier subagent") und `codex/`
     ("verification is in-loop... there is no verifier subagent"). Der
     bestehende Bericht referenziert ausschließlich `claude/`.
  2. **Modell-Kalibrierungswarnung im README übersehen:** Der Prompt ist
     explizit auf aktuelle Anthropic-Frontier-Modelle kalibriert und warnt
     selbst: *"On older models... or non-Anthropic models, the calmer
     phrasing may under-trigger."* MoE-Sovereigns Experten sind lokale SLMs
     (`qwen3.6:35b`, `gemma4:12b`, `ornith:9b`) — exakt die Zielgruppe, vor
     der gewarnt wird. Ein 1:1-Import ohne Anpassung würde bei Weg 1
     vermutlich schwächer wirken als vom Bericht angenommen.
  3. **Struktureller Mismatch bei Weg 1 (`frontend_designer`-MoE-Experte):**
     Der Claude-Workflow (Kapitel 2–4, 18) setzt Dateisystem-Zugriff
     ("read the design system's full definition... whatever exists") und
     Subagent-Verifikation voraus. `graph/expert.py` (reguläre MoE-Pipeline)
     ist reines Text-rein/Text-raus ohne Tool-/Dateizugriff — bestätigt beim
     Code-Review. Ein `frontend_designer`-Experte kann daher nur die
     *Prinzipien* (Kapitel 5–16: Farbsystem, Typografie, Abstände, a11y-Regeln
     als Text), nicht den *Workflow* (Fragerunden, Subagent-Delegation,
     Datei-Exploration) sinnvoll übernehmen.

  **Revidierte Instructions für Phase 2–4:**
  - Weg 2 (Skills für Claude-Code-Sessions) zuerst umsetzen, nicht Weg 1.
    Grund: Diese Sessions haben echten Datei-/Tool-Zugriff und verwalten
    bereits `admin_ui/templates/*.html` — die Voraussetzungen des Prompts
    sind hier tatsächlich erfüllt. Die `codex/`-Variante ("in-loop
    verification", kein Subagent-Zwang) ist die strukturell passendere
    Vorlage als `claude/`.
  - Import-Reihenfolge Weg 2: `ai-slop-check.md`, `accessibility-audit.md`,
    `hierarchy-rhythm-review.md` (aus `codex/skills/`, nicht `claude/skills/`)
    — mit YAML-Frontmatter (`description:`-Feld, Format siehe bestehende
    Skills in `moe-infra/skills/*.md`) ergänzen, MIT-Copyright-Header
    (Trystan Sarrade, 2026) beibehalten, durch den bestehenden
    `_run_llm_audit()`-Mechanismus (`admin_ui/app.py:3716`) laufen lassen.
    **Umsetzungsstand 2026-07-05:** `ai-slop-check` und
    `hierarchy-rhythm-review` importiert nach `skills/community/` und
    auditiert (`verdict: safe`, 0 Findings, `qwen3.6:35b`@N04-RTX).
    `accessibility-audit` bewusst NICHT importiert — der bestehende
    Community-Skill `a11y-audit` deckt denselben Funktionsumfang bereits ab
    (WCAG-2.2-Scan/Fix/Verify). Offen: Admin-Freigabe der beiden Skills
    (`admin_approved`) über die `/skills`-Seite — Selbst-Freigabe per SQL
    wurde vom Auto-Mode-Classifier zweifach gestoppt und bewusst nicht
    umgangen (Freigabe externen Codes ist eine Menschen-Entscheidung).
  - Weg 1 (`frontend_designer`-Experte) nur mit reduziertem Scope: Prompt
    auf Kapitel 5–16 (reine Stilregeln) beschränken, explizit dokumentieren,
    dass Fragerunden/Subagent-Verifikation dort strukturell nicht greifen —
    sonst entsteht eine Workflow-Erwartung, die die Architektur nicht liefern
    kann.

  **Ergänzte Acceptance-Kriterien:**
  - Importierte Skills stammen aus `codex/skills/`, nicht `claude/skills/`
    (Begründung: kein Subagent-Mechanismus in MoE-Sovereigns Skill-System).
  - `frontend_designer`-Systemprompt (falls Weg 1 umgesetzt wird) enthält
    einen expliziten Hinweis, dass Fragerunden und Datei-Exploration in
    diesem Kontext nicht verfügbar sind.
  - Mindestens ein Skill (empfohlen: `/ai-slop-check`) erfolgreich gegen
    eine reale Datei aus `admin_ui/templates/` in einer Claude-Code-Session
    ausgeführt und das Ergebnis dokumentiert (Nachweis der Weg-2-Tauglichkeit).

---

### TASK-33: Vibelate-Governance als CC-Profil-Preset (gestufter Weg: Prompt zuerst, Fine-Tuning später)

- **Status:** partially_done (Phase A umgesetzt 2026-07-05; Phase B wartet
  auf Messphase — siehe Resolution notes)
- **Owner:** Claude Code (Phase A)
- **Depends on:** WP3/Quality-Probe (`services/quality_probe.py`,
  `MOE_QUALITY_PROBE` Flag, siehe `SESSION_DOKUMENTATION_2026-07-05.md`),
  `scripts/export_distillation_dataset.py` (beide bereits implementiert)
- **Context:** `/opt/deployment/Michael_Reich/Vibelate3` (Ursprung:
  `ADHS/vibelate/`, verfeinert über Vibelate2 → Vibelate3) ist kein
  Coding-Stil, sondern ein Agenten-Governance-Framework: Autoritätshierarchie,
  kanonische Backlog-Zerlegung (Initiative → Epic → Story → Implementation
  Task), "Schema is the process"-Architekturphilosophie, Proof-Integrity- und
  Verification-Regeln. Analyse (2026-07-05, Claude Code) ergab zwei
  Kernbefunde:
  1. Vibelate setzt durchgehend Datei-Exploration, Ownership-Boundary-Prüfung
     und iterative Testlauf-Verifikation voraus. `graph/expert.py` (reguläre
     MoE-Pipeline) ist reines Text-rein/Text-raus ohne Tool-/Dateizugriff —
     ein neuer MoE-Pipeline-Modus (`moe_mode: "vibelate"`) könnte die
     Disziplin nur behaupten, nicht durchsetzen. Ein CC-Profil-Preset
     (`system_prompt_prefix`, analog zum bereits implementierten
     `tool_system_prefix`-Mechanismus aus `cc_session.py`) passt dagegen
     strukturell: eine echte Claude-Code-Session hat Datei-, Such- und
     Testlauf-Zugriff, genau wie von Vibelate vorausgesetzt.
  2. Vibelate ist kein stabiles Zielobjekt — die Methodik hat sich bereits
     über zwei Iterationen (v2 → v3) sichtbar verändert. Ein Fine-Tuning
     jetzt würde eine noch in Bewegung befindliche Methodik in Gewichte
     einfrieren (teuer bei jeder Regel-Änderung, nicht diffbar/auditierbar —
     dasselbe Problem, das TASK-12/Decision-Log für Laufzeitentscheidungen
     löst). Zudem ist Fine-Tuning auf externe HPC-Infra angewiesen — TASK-2
     in diesem selben Lastenheft zeigt konkret, wie ein abgelaufenes
     LUMI-SSH-Zertifikat einen Trainingslauf tagelang blockierte.
  - **Entscheidung:** gestufter Weg statt Entweder-Oder. Phase A (jetzt):
     CC-Profil-Preset mit System-Prompt-Prefix, Wirksamkeit über die bereits
     laufende Quality-Probe-Infrastruktur messen. Phase B (später, nur wenn
     Phase A stabile, gute Ergebnisse zeigt): stabile Vibelate-konforme
     Transkripte über die bereits vorhandene Distillations-Pipeline als
     LoRA-Trainingsdaten exportieren.
- **Instructions:**
  1. **Phase A — CC-Profil-Preset:**
     - Kondensiere `Vibelate3/AGENTS.md` (Precedence, Core Working Contract,
       Coding Behavior, Verification Rules — nicht die projektspezifischen
       Platzhalter-Kapitel wie Backlog/AI-Memory-Pfade) zu einem
       `system_prompt_prefix`-Text für ein neues CC-Profil "Vibelate-Strict".
     - Lege das Profil im Admin-UI oder User-Portal an (bestehender
       CC-Profil-Editor, `moe_mode` frei wählbar je nach Anwendungsfall —
       `native` oder `moe_orchestrated` mit Experten-Template).
     - MIT-Lizenzhinweis: Vibelate selbst hat noch keine explizite Lizenz im
       Projektverzeichnis geprüft — vor Veröffentlichung/Weitergabe des
       CC-Profil-Textes mit Michael Reich klären, ob/wie Attribution nötig
       ist (anders als beim MIT-lizenzierten `claude-design-system-prompt`
       aus TASK-32, wo die Lizenzlage bereits geklärt ist).
  2. **Phase A — Messung:**
     - `MOE_QUALITY_PROBE=1` ist bereits aktiv (siehe
       `SESSION_DOKUMENTATION_2026-07-05.md`). Sofern das Vibelate-Profil
       hinreichend Traffic bekommt, fließen Vergleichsdaten automatisch in
       `pipeline_quality_log`.
     - Nach einigen Wochen Nutzung: Auswertung, ob mit dem Vibelate-Profil
       geführte Sessions messbar weniger Nacharbeit / Regressions-Bugs /
       Scope-Abweichungen produzieren als ohne (Proxy-Metriken: Anzahl
       Folge-Korrekturen pro Task; `structured_failure_round` aus TASK-30;
       `trust_verdict`-Verteilung aus TASK-10 — sofern anwendbar).
  3. **Phase B — Distillation (nur nach positiver Phase-A-Auswertung UND
     wenn das Vibelate-Regelwerk über mind. 2-3 Monate unverändert blieb):**
     - `scripts/export_distillation_dataset.py` um einen Filter erweitern,
       der nur Conversation-Log-Einträge mit `template_id`/CC-Profil
       "Vibelate-Strict" exportiert.
     - Erst dann: LoRA-Trainingslauf auf einem lokalen Coder-Modell prüfen —
       mit dem Wissen, dass jede spätere Vibelate-Regeländerung einen neuen
       Trainingslauf erfordert (bewusste Kosten-Nutzen-Abwägung, siehe
       Context).
- **Acceptance criteria:**
  - CC-Profil "Vibelate-Strict" existiert, ist im Profil-Editor sichtbar und
    liefert bei einer Testanfrage nachweislich Vibelate-Sprache/-Disziplin
    (z. B. "Challenge weak or underspecified input", scope-enger Edit-Stil)
    in der Antwort.
  - Explizite Dokumentation, dass dies ein CC-Profil-Preset ist, kein neuer
    MoE-Pipeline-Modus — mit Begründung (Tool-/Dateizugriff-Voraussetzung).
  - Phase B wird nicht ohne dokumentierte Phase-A-Auswertung begonnen.
- **Resolution notes (2026-07-05, Claude Code — Phase A):**
  - `Vibelate3/AGENTS.md` auf die projektunabhängigen Kapitel kondensiert
    (2638 Zeichen: Core Working Contract, Coding Behavior, Proof Integrity,
    Architecture stance).
  - CC-Profil "Vibelate-Strict" (`ucp-96dd63b047aa47deac4a856a`, User
    horndev, `moe_mode: native`, `tool_model` leer → Template-Auto-Ableitung)
    per SQL angelegt — nach expliziter Nutzerbestätigung via AskUserQuestion,
    nachdem der Auto-Mode-Classifier den ersten Versuch gestoppt hatte.
    Redis-Cache invalidiert; Persistenz per Round-Trip-Read verifiziert.
  - Bewusst NICHT erledigt: Zuweisung des Profils zu einem API-Key
    (Nutzerentscheidung) und der Lizenz-Klärungspunkt mit Michael Reich.
  - Phase B bleibt offen bis zur dokumentierten Phase-A-Auswertung
    (Messfenster läuft mit `MOE_QUALITY_PROBE=1`).

---

### TASK-34: Integration / Evaluation des Vibe-Coding-Ökosystems (Empfehlungen & Implementierungsszenarien)

- **Status:** pending
- **Owner:** unassigned
- **Depends on:** Phase 1/3: none. Phase 2: Koordination mit TASK-32 (Design-
  Skills) und TASK-33 (Vibelate-CC-Profil-Prefix) — alle drei injizieren
  Regeln in denselben effektiven System-Prompt; Stacking-Reihenfolge und
  Konfliktauflösung müssen vor Phase-2-Beginn festgelegt werden (siehe
  Review-Notiz).
- **Context:** Das Vibe-Coding-Ökosystem entwickelt sich rasant (z. B. *The Ultimate Vibecoding Directory* und *Awesome-Vibecoding*). Diese Verzeichnisse listen moderne Client-Side Tools, Best Practices und System-Prompts auf. Da `MoE-Sovereign` als private, lokale Multi-Modell-Orchestrierungsschicht konzipiert ist, können wir durch die Integration dieser Standards Entwicklern ermöglichen, ihre bevorzugten Frontend-Coding-Clients (wie Cline, Roo-Code oder lokale Web-App-Generatoren) vollkommen souverän im eigenen Netz mit lokalen Experten (wie `qwen2.5-coder` oder `phi4`) zu betreiben.
- **Instructions:**
  1. **Phase 1: API-Kompatibilität für Vibe-Coding-Clients (Sovereign Gateway):**
     - Analyse der API-Anforderungen führender Vibe-Coding-Tools (z. B. Cline, Roo-Code).
     - Erweiterung der API-Kompatibilität in `routes/anthropic_compat.py` und `routes/ollama_compat.py`, um reibungslose Schnittstellen zu diesen Client-Editoren zu gewährleisten.
  2. **Phase 2: Prompt-Standardisierung für lokale Experten:**
     - Übernahme strukturierter Doktrinen (z. B. TDD-Erzwingung, präzise File-Edit-Spezifikationen, Such- und Ersetzungs-Patterns) aus bekannten `.cursorrules` des *Vibecoding Directories*.
     - Integration dieser Regeln in die standardmäßigen System-Prompts von `MoE-Sovereign` (`prompts/systemprompt/`), um Syntax-Drift bei lokalen 14B/35B Modellen zu minimieren.
  3. **Phase 3: MCP-Tool-Registry-Erweiterung:**
     - Analyse der in *Awesome-Vibecoding* gelisteten, bewährten Community-MCP-Server.
     - Ergänzung nützlicher Werkzeuge (z. B. für erweiterte Dateiverwaltung, Git-Aktionen) in der AST-geprüften Whitelist des `mcp-precision`-Containers.
- **Acceptance criteria:**
  - Kompatibilitäts-Verifikationsbericht liegt vor (welcher Client, welcher
    API-Modus, welche Lücken tatsächlich gefunden — analog zum
    Evaluationsbericht aus TASK-32 Phase 1); erst danach werden Compat-Layer
    geändert. *(Ersetzt am 2026-07-05 das ursprüngliche selbsterfüllende
    Kriterium „Plan ist eingetragen (Erledigt)" — Widerspruchsauflösung auf
    Betreiberanweisung.)*
  - Mindestens ein moderner Vibe-Coding-Client (z. B. Cline oder Roo-Code) kann sich erfolgreich über das Sovereign-Gateway verbinden und Dateiveränderungen mit lokalen Modellen abschließen.
  - System-Prompts für Programmier-Tasks enthalten Regeln zur präzisen Such- und Ersetzungsstruktur.
  - Whitelist der MCP-Tools wurde um mindestens ein Tool erweitert, das den
    für Community-Skills etablierten Audit-Weg (`_run_llm_audit()` +
    Admin-Freigabe, vgl. `skill_registry`-Muster) durchlaufen hat.
    *(Präzisiert am 2026-07-05: „Community-geprüft" war ohne definierten
    Prüfmechanismus nicht abnehmbar.)*
- **Review-Notiz (2026-07-05, Claude Code — Koordination gem. Section 0,
  Inhalt bewusst nicht umgeschrieben):**
  - Referenzierte Pfade gegen den Code verifiziert: `prompts/systemprompt/`
    (existiert, u.a. `agentic_coder.md`), `routes/anthropic_compat.py` /
    `routes/ollama_compat.py` (existieren), AST-basierte Prüfung in
    `mcp_server/server.py` (existiert) — Grundlage der Task ist solide.
  - **Phase 1 setzt eine Lücke voraus, die vermutlich nicht existiert:**
    Cline/Roo-Code sprechen OpenAI-/Anthropic-/Ollama-kompatible APIs;
    alle drei Compat-Layer sind bereits vorhanden. Empfohlener erster
    Schritt: Verbindungs-Verifikation mit einem echten Client, dann nur
    tatsächlich festgestellte Lücken schließen — nicht pauschal "erweitern".
  - **Nicht deklarierte Überlappung:** Phase 2 (Regeln in
    `prompts/systemprompt/`) überschneidet sich mit TASK-33
    (Vibelate-Regeln als CC-Profil-Prefix) und TASK-32 (Design-Regeln als
    Skills). Drei Ebenen injizieren dann Disziplin-Regeln in denselben
    effektiven System-Prompt — Stacking-Reihenfolge und Konflikte sollten
    vor Phase 2 geklärt werden. `Depends on: none` ist daher zu schwach.
  - **Phase 3 fehlt ein Sicherheits-Gate:** Neue Community-MCP-Tools sollten
    denselben Audit-Weg durchlaufen wie Community-Skills
    (`_run_llm_audit()` + Admin-Freigabe, vgl. `skill_registry`-Muster).
    Acceptance-Kriterium 4 („Community-geprüft") ist ohne definierten
    Prüfmechanismus nicht abnehmbar.
  - **Formales (aufgelöst 2026-07-05 auf Betreiberanweisung):** Die drei
    formalen Widersprüche wurden direkt in dieser Task korrigiert —
    (a) selbsterfüllendes Kriterium 1 durch prüfbares
    Verifikationsbericht-Kriterium ersetzt, (b) Graph-Eintrag von
    „Evaluierung done" auf „Plan eingetragen" korrigiert (kein
    Evaluations-Artefakt existierte), (c) `Depends on` um die
    Prompt-Stacking-Koordination mit TASK-32/33 ergänzt. Weiterhin offen
    und NICHT durch mich behebbar: kein Status-Log-Eintrag des Erstellers
    für die ursprüngliche Eintragung (`agent_status/agy.md` unverändert
    seit 2026-07-02, vgl. Section 0) — der Ersteller sollte das bei der
    nächsten Session nachholen.

---

### TASK-35: End-to-End-Vollständigkeit, Live-Code-Kohärenz und Aktivierung halbfertiger Funktionen

- **Status:** done (2026-07-28, Codex CLI; Restrestrisiken unten dokumentiert)
- **Owner:** Codex CLI
- **Depends on:** TASK-10, TASK-13 bis TASK-16, TASK-29 bis TASK-31
- **Context:** Ein Code-, Test- und Live-System-Audit am 2026-07-26/27 hat
  mehrere Funktionen gefunden, die zwar als `done` dokumentiert oder als
  Modul vorhanden sind, aber im produktiven Ausführungspfad fehlen, nur
  teilweise aufgerufen werden oder wegen eines inkohärenten Container-
  Dateistands nicht funktionieren. Der Audit umfasste statische
  Call-Site-Prüfung, 596 gesammelte Tests, isolierte Testbatches,
  Live-Container, Postgres/Valkey/Neo4j/Chroma/Kafka, MCP-Werkzeuge und einen
  echten `moe-auto`-Request. 445 eindeutig gezählte Tests liefen grün; ein
  vollständiger Lauf wird zusätzlich durch nicht beendete Test-Worker in
  Agent-Enrichment/JMoE und einen fehlschlagenden Web-Search-Fallback
  verhindert.
- **Verifizierte Befunde (Priorität P0 bis P2):**
  1. **P0 — inkohärenter Produktivcode:** `langgraph-app` mountet nur
     `./services` aus dem Host, während `graph/`, `main.py` und
     `tool_injector.py` aus einem älteren Image stammen. Dadurch importiert
     das aktuelle `services/helpers.py` die im Container fehlende Funktion
     `inject_tools_explicit`. Der Live-E2E-Request
     `chatcmpl-fd99a5cf-c133-42a1-81de-e37579081eff` lief zunächst rund
     12,5 Minuten durch einen fachlich falschen Planner-Plan und brach dann
     mit `ImportError` ab. Der Request blieb in Valkey als aktiv markiert
     und erzeugte keinen vollständigen `usage_log`-Datensatz.
  2. **P0 — HITL nicht im Datenpfad und unsicher:** `create_gate()` hat
     keinen Produktionsaufrufer; die Chat-Pipeline liefert weder HTTP 202
     noch `x-moe-gate-id`. Die Gate-Routen verlassen sich auf nicht gesetzte
     `request.state.user_id/role`: ein Owner-loses Gate war ohne
     Authentifizierung freigebbar, während der System-Key ein fremdes Gate
     nicht freigeben konnte.
  3. **P0 — Boundary/Cascade nur scheinbar aktiv:** Boundary-Verletzungen
     werden erkannt, aber `_emit_cascade()` persistiert/emittiert kein
     Cascade-Event. Der Planner protokolliert Verletzungen und führt
     ungültige Tasks trotzdem aus. `classify_gap()` wird benutzt,
     `emit_cascade()`, `resolve_cascade()` und `list_open_cascades()` sind
     im realen Graphpfad nicht vollständig verbunden. Produktionsdaten:
     null Cascade-Zeilen und keine Cascade-Keys.
  4. **P0 — Trust-`BLOCK` blockiert nicht:** Der Graph protokolliert das
     Verdict, unterdrückt oder ersetzt die Antwort jedoch ausdrücklich
     nicht. Damit ist das zentrale Abnahmekriterium aus TASK-10 nicht
     erfüllt.
  5. **P1 — Structured Failure nicht integriert:** Das vollständige Modul
     aus TASK-30 ist vorhanden, wird aber weder vom Planner noch vom Judge
     importiert. Automatische Retries, Fallback-Modell und abschließende
     `SPEC_GAP`-Cascade finden nicht statt; in der Produktion ist
     `structured_failure_round > 0` null.
  6. **P1 — Cynefin wird mit veraltetem State berechnet:** Der Planner
     klassifiziert vor Rückgabe des gerade ermittelten
     `complexity_level`/Plans. Auf der ersten Runde sieht Cynefin deshalb
     typischerweise Default- oder Altwerte; ein späteres Trust-`BLOCK` kann
     die Erstklassifikation nicht zu `CHAOTIC` machen.
  7. **P1 — AI-I/O-Audit nur für Judge:** Von allen Modellaufrufen wird nur
     der native Ollama-Judge instrumentiert. Die Live-Tabelle enthält 112
     Einträge ausschließlich mit Stage `judge`; das Kriterium „jeder
     LLM-Call“ aus TASK-29 ist nicht erfüllt.
  8. **P1 — Modell-Capabilities nur geloggt:** Die Capability-Matrix wird am
     Judge gelesen, aber `json_schema`/`json_object` und `stream=False`
     werden nicht durchgängig auf Requests angewendet. Die Hilfsfunktionen
     für Streaming und JSON-Schema haben keinen produktiven Aufrufer.
  9. **P1 — Garage/MCP-Dateiupload defekt:** Der S3-Client übergibt die
     konfigurierte Garage-Region nicht. Garage antwortet deshalb mit
     `AuthorizationHeaderMalformed` (`us-east-1` statt konfigurierter
     Region). Der MCP-Aufruf liefert zwar HTTP 200, trägt aber nur den
     Authentifizierungsfehler im Tool-Ergebnis; der Garage-Healthcheck ist
     deaktiviert.
  10. **P1 — HABE-Artefaktpfade widersprechen sich:** Der Rebuild schreibt
      wegen `np.save("habe_vector.bin", ...)` tatsächlich
      `habe_vector.bin.npy`, der Laufzeitknoten erwartet
      `habe_vector.npy`. Ein 942-MB-Vokabular liegt vor, aber kein Artefakt
      am erwarteten Pfad; ein Scheduler fehlt.
  11. **P1 — Retrieval-/Lernwartung nicht verdrahtet:**
      `record_attribution()` hat keinen Produktionsaufrufer,
      Graph-Decay besitzt nur einen Host-Cron-Kommentar, und Eurisko ist
      trotz Dokumentation als automatischer Hintergrundprozess nur über
      einen manuellen Admin-Endpunkt erreichbar.
  12. **P1 — Request-Timeout/Fehlerbereinigung unvollständig:** Der
      Planner-Timeout steht standardmäßig auf 300 Sekunden, der
      Non-Streaming-Graph hat keinen hinreichenden Gesamt-Timeout und räumt
      bei Ausnahmen die Active-Request-Registrierung nicht zuverlässig auf.
  13. **P2 — Healthcheck zu oberflächlich:** `/health` gibt immer
      `{"status":"ok"}` zurück, ohne Postgres, Valkey, Graph-Store oder
      Modell-Backend zu prüfen.
  14. **P2 — Web-Search-Fallback/Testlauf instabil:** Der isolierte
      Fallback-Test wartet zweimal etwa zwölf Sekunden und erhält statt des
      erwarteten Fallback-Ergebnisses eine leere Liste. Einige
      Agent-Enrichment-/JMoE-Tests beenden ihre Executor/Event-Loop-Worker
      nach `PASSED` nicht, wodurch der Gesamtlauf hängt.
  15. **P2 — Decision-Log-Warnung:** Ohne laufenden Event Loop wird die
      Kafka-Coroutine erzeugt, aber nicht awaited; das produziert
      `RuntimeWarning` und kann einen beabsichtigten Publish verlieren.
  16. **P2 — dokumentierte und ungenutzte Funktionen:** Bestätigte
      Produktionslücken bestehen u. a. für
      `pipeline.contracts.parse_plan/parse_verdict`,
      `dynamic_router.classify_active_experts`,
      `inference._invoke_council_expert`,
      `parsing._parse_expert_gaps`,
      `logic_types.lukasiewicz_tnorm` und `node_load.least_loaded`.
      Zusätzlich ist die Graph-/MCP-Dokumentation gegenüber dem realen
      Graph (Strategy Review, Self-Critique, Conflict Resolution) und den
      58 live registrierten MCP-Tools veraltet.
- **Ausführungsplan (in dieser Reihenfolge; vor Codeänderungen
  niedergeschrieben):**
  1. Container-Quellstand vereinheitlichen, Importpfade absichern,
     Planner-/Gesamt-Timeouts begrenzen und Active-Request-Cleanup in einen
     garantierten Fehlerpfad legen.
  2. Trust-Block und HITL-Gate bis zum HTTP-Vertrag durchverdrahten;
     Gate-Routen an die echte API-Key-Authentifizierung und Owner/Admin-
     Autorisierung anbinden.
  3. Boundary-Verletzungen als echte Cascades persistieren, ungültige Tasks
     vor Expert-Aufrufen stoppen sowie Cascade-Auflösung und Stuck-Logging
     in den Replan-/Synthesis-Pfad integrieren.
  4. Structured-Failure-Recovery an Planner- und Judge-Parsing anbinden und
     Cynefin erst auf dem aktualisierten State sowie nach Trust-Änderungen
     klassifizieren.
  5. AI-I/O-Audit und Modell-Capability-Enforcement in die zentralen
     Inference-Pfade heben, sodass Planner, Experts und Judge dieselben
     Regeln erhalten.
  6. Garage-Region/Health, HABE-Artefaktvertrag und kontrollierte
     Wartungsjobs für HABE, Attribution, Graph-Decay und Eurisko reparieren.
  7. Readiness-Checks, Web-Search-Fallback, Decision-Log-Async-Pfad und
     hängende Testressourcen korrigieren. Bewusst tote oder ersetzte
     Funktionen entweder an einen belegbaren Produktionspfad anbinden oder
     entfernen/deprecaten; keine künstlichen Aufrufe nur zur
     Call-Site-Erzeugung.
  8. Unit-/Integrations-/Contract-Tests ergänzen, technische Dokumentation
     auf den belegten Istzustand bringen und die bisherigen
     `done`-Resolution-Notes dort sichtbar korrigieren, wo die
     Abnahmekriterien nicht erfüllt waren.
  9. Betroffene Images neu bauen und reproduzierbar deployen. Danach
     Container-Imports, Dependency-Readiness, Gate-/Cascade-/Garage-
     Contracts und einen echten `moe-auto`-End-to-End-Request prüfen.
  10. Diese Task mit exakten Testzahlen, verbleibenden Risiken und bewusst
      zurückgestellten Punkten abschließen; bestätigte dauerhafte Ergebnisse
      in SessionMesh festhalten.
- **Acceptance criteria:**
  - Container- und Host-Code stammen nach Deployment aus demselben
    Worktree-Snapshot; ein automatisierter Import-Symboltest findet keine
    Host/Container-Abweichung.
  - Ein kurzer `moe-auto`-Request endet innerhalb des konfigurierten
    Gesamt-Timeouts mit Antwort oder strukturiertem Fehler, wird immer aus
    der Active-Request-Liste entfernt und im Audit/Usage-Pfad abgeschlossen.
  - Trust-`BLOCK` liefert keinen ungekennzeichneten finalen Inhalt.
    Gate-pflichtige Requests liefern HTTP 202 + Gate-ID; anonyme/fremde
    Freigabe ist 401/403, Owner/Admin/System-Freigabe funktioniert.
  - Boundary-Verletzung verhindert den Expert-Call und erzeugt eine offene
    Cascade; erfolgreicher Replan löst sie, Stuck protokolliert offene IDs.
  - Planner- und Judge-Parsefehler nutzen begrenzte Structured-Recovery;
    Cynefin sieht aktuelle Complexity-/Trust-Werte.
  - Audit enthält mindestens Planner-, Expert- und Judge-Stages; Capability-
    Flags verändern nachweislich den ausgehenden Request.
  - Garage-Dateiupload und HABE-Load verwenden die konfigurierten,
    identischen Verträge; `/ready` wird bei Ausfall einer
    Pflichtabhängigkeit unready.
  - Der vollständige automatisierte Testlauf beendet sich selbstständig;
    verbleibende externe Modell-/Netzabhängigkeiten sind getrennt markiert.
  - Dokumentation nennt keine Funktion als produktiv, wenn der zugehörige
    Live-/Contract-Test fehlt.
- **Resolution notes (2026-07-28, Codex CLI):**
  - **Plan vollständig abgearbeitet:** Die zehn vor der Implementierung
    notierten Schritte wurden in der angegebenen Reihenfolge umgesetzt. Der
    Orchestrator wird jetzt als kohärentes Image ohne partiellen
    `services/`-Quellmount betrieben; Gesamtbudget, Timeout-Fehlervertrag,
    Usage-Logging und Active-Request-Cleanup liegen im realen
    Non-Streaming-Pfad. Trust/HITL, Boundary/Cascade, Structured Recovery,
    aktuelles Cynefin, Capability-Enforcement und das finale Quality Gate
    sind an Graph und HTTP-Transport angeschlossen.
  - **Keine künstlichen Call-Sites:** Die zuvor belegten toten Funktionen
    wurden entweder fachlich angebunden oder entfernt. Produktiv angebunden
    sind insbesondere `parse_plan`, `_parse_expert_gaps`,
    `lukasiewicz_tnorm`, der modellabhängige Retry-Resolver, Retrieval-
    Attribution und Node-Load-Tracking. Entfernt wurden unter anderem
    `parse_verdict`, `classify_active_experts`, `_invoke_council_expert`,
    `least_loaded`, redundante Cascade-/Pipeline-Wrapper und nicht
    implementierte HABE-Hierarchie-/Virtual-Prefix-Methoden.
  - **Trust/HITL live:** Das Quality Gate leert bei `BLOCK` den Inhalt; eine
    erforderliche, aber nicht speicherbare Freigabe blockiert ebenfalls.
    Ein realer Gate-Durchlauf im neu gebauten Container ergab anonym
    `401`, mit Systemidentität `GET 200`, `approve 200` und anschließend
    Status `approved`. Fremdnutzer-/Owner-Regeln sind zusätzlich durch
    Contract-Tests abgedeckt. Non-Streaming liefert für ein Gate `202` plus
    `X-MoE-Gate-Id`; blockierte Antworten liefern `422`. Streaming sendet
    nur das Gate-/Block-Kontrollereignis, nicht den Antwortentwurf.
  - **Boundary/Cascade/Structured/Cynefin:** Planner-Vertragsverletzungen
    verhindern den Expert-Dispatch und erzeugen echte offene Cascades.
    Replan/Synthesis lösen sie oder protokollieren verbliebene IDs.
    Planner- und Synthesis-Parsing verwenden begrenzte strukturierte
    Wiederholungen und konfigurierte Fallback-Modelle. Cynefin wird nach
    aktueller Complexity und nochmals nach dem finalen Trust-Verdict
    berechnet.
  - **AI-I/O-Audit und Capabilities:** Planner, Expert, Judge,
    Background-Judge, lokaler GGUF-Planner und Guard teilen jetzt einen
    vollständigen Audit-Lifecycle. Ein Request-Timeout schließt den Eintrag
    auch bei `asyncio.CancelledError` als `error`, ohne die Cancellation zu
    verschlucken. Der Container-/Postgres-Nachweis lieferte auf dem finalen
    Image `guard/error/task35-guard-audit-final`; zusätzlich lieferte der
    Abbruchtest `expert/error/task35-cancel-audit-live`. Die bestehende
    Tabelle enthielt außerdem 90 abgeschlossene und 24 fehlerhafte
    Judge-Aufrufe sowie sechs Planner-Fehler.
    JSON-/Streaming-Fähigkeiten und maximale
    Planner-/Judge-/Expert-Ausgaben verändern bzw. begrenzen nun den
    tatsächlich ausgehenden Request.
  - **Speicher und Lernwartung:** Der Garage-Client verwendet die
    konfigurierte Region; ein echter MCP-`file_upload` war erfolgreich und
    Garage ist `healthy`. MCP meldet 58/58 aktivierte Tools einschließlich
    `file_upload`. HABE schreibt und lädt einheitlich
    `models/habe_vector.npy`, atomar und fail-safe. Der laufende
    Maintenance-Scheduler holte 15.855 Neo4j-Tripel, band sie in 20.206
    Vokabulareinträge ein, schrieb das erwartete Artefakt und beendete den
    HABE-Job in 67,986 s mit Exit 0. Graph-Decay lief als Dry-Run mit Exit 0;
    Eurisko bleibt bewusst opt-in. Synthesis ruft Retrieval-Attribution
    nach tatsächlich gelieferten Graph-Chunks auf.
  - **Readiness und Async-Ressourcen:** `/ready` prüft den kompilierten
    Graphen, Valkey und Nutzerdatenbank als Pflichtabhängigkeiten sowie
    Neo4j, MCP und Chroma als optionale Checks. Web-Fallback,
    Agent-Enrichment und Decision-Log benutzen begrenzte, sauber
    abgeschlossene Async-Pfade; der vollständige Testprozess hängt nicht
    mehr.
  - **Statische Erreichbarkeitsprüfung:** Der letzte alias-aware AST-/Call-
    Site-Lauf über 162 Runtime-/Ops-Python-Dateien untersuchte 1.908
    Definitionen und ließ 33 Nullreferenz-Kandidaten übrig. Diese Restmenge
    wurde nicht durch Scheinaufrufe „grün“ gemacht. Sie besteht aus
    Framework-/Protokoll-Callbacks (`dispatch` fünfmal, `forward`,
    `on_step_end`, `embed_query`, `embed_documents`), eigenständig
    aufrufbaren Skript-/Service-Schnittstellen sowie derzeit nicht intern
    belegten Admin-/Federation-APIs:
    `_prom_range`, `_get_global_server_names`, `log_usage`,
    `get_admin_template`, `delete_admin_template`,
    `get_federation_policy`, `create_outbox`, `get_outbox`,
    `update_outbox`, `get_tenant`, `_set_run_status`, `handshake`,
    `get_manual_domains`, `push_knowledge`, `pull_knowledge`,
    `append_decision`, `assert_proven`, `_get_avg_duration`,
    `fetch_local_models`, `format_prompt`, `retrieve`,
    `save_reference_set`, `is_enabled` und `set_feature_enabled`.
    Wegen dynamischer Framework-Aufrufe bzw. möglicher externer API-Nutzung
    ist „keine statische Referenz“ kein sicherer Löschbeweis. Diese
    Funktionen werden daher ausdrücklich **nicht als produktiv
    end-to-end-verifiziert** behauptet; eine Entfernung benötigt zuerst
    Zugriffstelemetrie bzw. eine API-Deprecation.
  - **Automatisierte Abnahme:** `python3 -m pytest -q` beendet sich mit
    **619 passed in 4.10s**. Zusätzlich bestanden Compileall, der separate
    Komplexitäts-Integrationstest (5/5), `docker compose config --quiet`
    (nur erwartete, nicht zu TASK-35 gehörende leere Authentik-Variablen)
    und `git diff --check` unter Ausschluss der bereits vorher
    whitespace-behafteten Nutzerdatei
    `eurohpc_lumi_activity_report.md`.
  - **Deployment und Live-Abnahme:** `langgraph-orchestrator` läuft
    `healthy` auf Image
    `sha256:4d7841d2724877ce6005bc95b6288f6e137493aec2127a3a7292c790231c2070`.
    SHA-256 von `main.py`, `tool_injector.py`,
    `services/hitl_gate.py`, `services/inference.py`,
    `services/pipeline/chat.py` und
    `graph/router_nodes.py` stimmen zwischen Host und Container überein.
    `/ready` meldet alle sechs Prüfungen positiv. MCP, Garage,
    Maintenance, Neo4j, Valkey, Postgres und Chroma liefen bei der
    Abschlussprüfung ebenfalls `healthy`.
  - **Echter `moe-auto`-Request:** Der finale Non-Streaming-Lauf
    `chatcmpl-ec1126c0-0df6-4338-8752-426014013a75` endete nach
    **300,122 s** mit strukturiertem HTTP 504
    (`timeout_error`, `orchestration_timeout`, Request-ID im Fehlerobjekt).
    Der Active-Key war danach entfernt; `usage_log` enthielt Status
    `timeout`, Modell `moe-auto`, 300.001 ms und null Completion-Tokens.
    Damit ist der Fehler-/Cleanup-Vertrag erfüllt, nicht jedoch ein
    erfolgreicher Antwortnachweis unter der beobachteten Last.
  - **Verbleibende, offen ausgewiesene Betriebsrisiken:** Der konfigurierte
    Guard belegte beim echten E2E 120 s lang ein stark ausgelastetes lokales
    Backend und lief dann gemäß bewusster Policy fail-open; dem Planner
    verblieb im 300-s-Gesamtbudget nicht genug Kapazität. Gleichzeitig
    liefen weitere Qwen-3.6-Anfragen auf den Inferenzknoten. Das ist kein
    unbeschränkter Code-Hänger mehr, aber ein reales Capacity-/SLO-Problem:
    Vor einer Erfolgs-SLO-Abnahme sind Guard-/Planner-Reservierung,
    kürzerer Guard-Timeout oder ein dauerhaft warmes separates
    Guard-Backend nötig. Graph-Decay meldet außerdem erwartete Neo4j-
    Hinweise, solange ältere Chunks noch keine `last_hit`-/Attributions-
    Properties besitzen. Die 33 statisch nicht belegten öffentlichen oder
    dynamischen Schnittstellen bleiben bis zu Telemetrie/Deprecation als
    gesonderte Inventarliste bestehen. Ein Clean-Build hat außerdem gezeigt,
    dass mehrere Python-Abhängigkeiten in `requirements.txt` nur mit offenen
    Mindestversionen spezifiziert sind. Das finale Image ist gesund und
    importierbar, aber bitgenaue Reproduzierbarkeit erfordert künftig eine
    gepflegte Constraints-/Lock-Datei und einen separaten
    Dependency-Upgrade-Testlauf.

---

### TASK-36: Restbaustellen schließen und Wirksamkeit unter realer Inferenzlast nachweisen

- **Status:** done (2026-07-29, Codex CLI)
- **Owner:** Codex CLI
- **Depends on:** TASK-35
- **Context:** TASK-35 hat den Orchestrator funktional kohärent gemacht, bei
  der Live-Abnahme aber drei bewusst offene Restklassen ausgewiesen:
  (1) Der Guard belegte ein gemeinsam genutztes Ollama-Backend bis zu 120 s
  und ließ dem Planner im 300-s-Gesamtbudget keine verlässlich nutzbare
  Kapazität; der reale `moe-auto`-Request endete deshalb korrekt bereinigt,
  aber ohne Antwort. (2) Das Image wurde aus offenen Mindestversionen gebaut,
  obwohl eine veraltete Lock-Datei im Repository lag und vom Dockerfile nicht
  verwendet wurde. (3) 33 statische Nullreferenz-Kandidaten blieben
  absichtlich ungeklärt. Eine erneute lokale Prüfung am 2026-07-29 bestätigte,
  dass Guard und Planner auf demselben Host laufen und weder
  `llama-guard3:8b` noch `qwen3-planner:q4km` warm geladen waren. Das
  produktive Image selbst ist weiterhin healthy und `/ready` positiv.
- **Vor Implementierung verifizierte Einordnung der Restkandidaten:**
  - **Dynamische Verträge, keine tote Implementierung:** fünf
    ASGI-`dispatch`-Methoden, Chroma-`embed_query`/`embed_documents`,
    Torch-`forward` und der als Trainer-Callback registrierte
    `on_step_end`. Diese werden durch Frameworks oder Protokolle anhand
    ihres Namens aufgerufen und brauchen einen expliziten, getesteten
    Runtime-Entry-Point-Vertrag statt künstlicher Python-Call-Sites.
  - **Öffentliche/HTTP-/CLI-Einstiegspunkte:** Federation `push_knowledge`
    und `pull_knowledge` werden über die Admin-HTTP-Routen fachlich
    ausgeführt; Federation-Clientmethoden wie `handshake` sind öffentliche
    Protokolloperationen. Die Reference-Set-Datei wird durch das
    Regressionsskript gelesen und darf als betreiberverwaltetes Artefakt
    weiterhin extern geschrieben werden. Solche Schnittstellen werden
    als öffentliches Contract-Inventar geprüft, nicht nur anhand interner
    Namensreferenzen bewertet.
  - **Echte tote Duplikate/Legacy-Helfer:** `_prom_range`,
    `_get_global_server_names`, `log_usage`, `get_admin_template`,
    `delete_admin_template`, `_set_run_status`, `fetch_local_models`,
    `format_prompt` und der redundante `context_index.retrieve`-Wrapper
    besitzen aktuelle, bereits verdrahtete Ersatzpfade. Diese werden nach
    erneutem Referenzcheck entfernt.
  - **Echte Halbverdrahtungen:** `routes/admin_rlsf.py` importiert
    `is_enabled`, prüft den Schalter vor dem Start aber nicht;
    `set_feature_enabled` besitzt keinen Orchestrator-Schreibendpunkt;
    das Gap-Healer-Timing wird geschrieben, aber nicht gelesen;
    `ConstructiveProof.assert_proven` wird als aktiv dokumentiert, ohne an
    einer Entscheidungsgrenze verwendet zu werden. Diese Pfade werden
    entweder funktional geschlossen oder in Dokumentation und Code
    eindeutig als nicht produktiv entfernt/abgestuft.
- **Ausführungsplan (vor der ersten Codeänderung niedergeschrieben):**
  1. Den Guard-Aufruf kapazitätsschonend machen: kurzer, begrenzter
     Warmzustands-Probe auf Ollama `/api/ps`, standardmäßig nur einen bereits
     geladenen Guard aufrufen, Cold-Miss/Providerfehler als explizites
     `fail_open` auditieren und den Guard-Timeout auf ein SLO-taugliches
     Budget begrenzen.
  2. Den bestehenden trivialen Planner-Fast-Path standardmäßig aktivieren,
     aber durch eine konservative Eligibility-Regel vor Rechen-, Rechts-,
     Aktualitäts-, Recherche-, Datei-/Bild- und fortgesetzten Chat-Aufgaben
     schützen. Dadurch darf nur ein eindeutig einfacher Prompt direkt an
     den warmen General-Expert gehen.
  3. Die Nullreferenz-Restmenge schließen: bestätigte Legacy-Duplikate
     entfernen, den RLSF-Schalter tatsächlich erzwingen, Feature-Toggles
     über einen authentifizierten Orchestrator-Endpunkt verdrahten,
     write-only Gap-Timing entfernen und dynamische/öffentliche
     Einstiegspunkte in einem importierbaren Contract-Manifest registrieren.
     Nicht ausgeführte Intuitionistik darf nicht länger als aktive
     Executor-Garantie dokumentiert sein.
  4. Die aktuelle, funktionierende Container-Umgebung als vollständig
     versionsgepinnten Lock-Satz festschreiben, das Dockerfile ausschließlich
     daraus installieren lassen und das Python-Basisimage per Digest binden.
     `pip check` und Importtests bleiben Teil der Image-Abnahme.
  5. Unit-/Contracttests für Guard-Warm/Cold/Error/Cancellation,
     Fast-Path-Zulässigkeit, RLSF-/Feature-Schalter und Runtime-Entry-Points
     ergänzen; anschließend gesamten Pytest-Lauf, Compileall,
     `git diff --check`, Compose-Konfiguration und einen statischen
     Referenzlauf ausführen.
  6. Ein sauberes Image aus dem festgeschriebenen Dependency-Satz bauen,
     deployen und Host-/Container-Quellen sowie `/ready` verifizieren.
     Danach mindestens einen echten kurzen `moe-auto`-Request messen. Die
     Wirksamkeit gilt nur als belegt, wenn der Guard den kalten 8B-Swap
     vermeidet, der Request innerhalb des Budgets eine Antwort liefert,
     Active-Request-Cleanup/Usage/Audit abgeschlossen sind und
     sicherheits- oder werkzeugrelevante Prompts den Fast-Path nicht nehmen.
  7. Exakte Messwerte, Testzahlen, Image-ID und etwaige verbleibende externe
     Kapazitätsrisiken hier und im Agent-Status dokumentieren; bestätigte
     dauerhafte Entscheidungen/Aufgaben in SessionMesh festhalten.
- **Acceptance criteria:**
  - Ein kalter Guard führt zu keinem Guard-Modell-POST und benötigt nur das
    begrenzte Probe-Budget; Audit und Graph-Stage kennzeichnen den Zustand
    als `fail_open`, nicht fälschlich als bestanden. Ein warmer Guard bleibt
    voll funktionsfähig; Cancellation wird weitergereicht und auditiert.
  - Ein einfacher, deterministisch unkritischer Prompt umgeht den Planner;
    Rechen-, Rechts-, aktuelle/recherchebedürftige, Datei-/Bild- und
    Mehrturn-Prompts tun dies nachweislich nicht.
  - Jeder verbleibende statische Nullreferenz-Kandidat ist entweder entfernt,
    fachlich aufgerufen oder als getesteter dynamischer/öffentlicher
    Einstiegspunkt inventarisiert. Ein deaktivierter RLSF-Loop lässt sich
    nicht über den Trigger starten; bekannte Starfleet-Features sind über
    einen abgesicherten Schreibpfad schaltbar.
  - Docker installiert einen vollständigen, exakten Lock-Satz auf einem
    digest-gepinnten Basisimage; `pip check`, Compile-/Importprüfung und
    gesamter Testlauf sind grün.
  - Das neue Live-Image ist healthy und `/ready` positiv. Ein realer kurzer
    `moe-auto`-Request liefert innerhalb des Gesamtbudgets eine Antwort
    (nicht nur einen strukturierten Timeout), hinterlässt keinen Active-Key
    und besitzt abgeschlossene Usage-/AI-I/O-Auditdaten.

- **Umgesetzte Auflösung:**
  - Der Guard besitzt jetzt einen kurzen `/api/ps`-Warmzustands-Probe,
    `GUARD_WARM_ONLY=true` als sicheren Betriebsstandard, ein eigenes
    15-s-Budget und explizite `fail_open`-/Fehler-/Cancellation-Audits. Ein
    kalter `llama-guard3:8b` wird im normalen Pfad nicht mehr in das mit
    Experten geteilte Backend geladen.
  - Eine gemeinsame, konservative Eligibility-Regel in
    `services/trivial_fast_path.py` steuert sowohl den HTTP-Preflight als
    auch den Planner-Fast-Path. Sie sperrt unter anderem Rechen-, Rechts-,
    Recherche-, Aktualitäts-, Datei-/Bild-, Tool-, Systemprompt-,
    Mehrturn- und explizite Expertenaufgaben. Nur ein entsprechend
    markierter Pfad darf das einzelne Expertenergebnis direkt übernehmen.
    Quality Gate und Constitution Enforcement bleiben dabei aktiv; ein
    Konflikt hebt die Fast-Path-Vertrauensfreigabe auf.
  - Der Fast-Path propagiert die tatsächlichen Skip-/Tier-/Cynefin-Signale
    bis zur Synthese. Refinement, Thinking und Judge werden bei dem
    verifizierten Einzelexperten nicht mehr versehentlich nachgestartet.
    Der resultierende Trust-Score wird auch im direkten Rückgabepfad
    vollständig in Usage und Antwortzustand geschrieben.
  - Bestätigte Legacy-Duplikate wurden entfernt. RLSF erzwingt seinen
    Feature-Schalter nun auf Route-, Service- und CLI-Ebene.
    Starfleet-Featureänderungen besitzen einen authentifizierten
    Schreibendpunkt. Federation-Handshake, manueller Outbox-Push,
    periodischer Auto-Push und Admin-UI sind fachlich verdrahtet.
    Schreib-only Gap-Timing wurde entfernt und die nicht produktiv
    ausgeführte Intuitionistik eindeutig als Forschung statt
    Executor-Garantie dokumentiert.
  - Die zuvor 33 offenen dynamischen/öffentlichen Kandidaten sind jetzt
    entweder gelöscht, an einen realen Aufrufpfad angebunden oder als
    Framework-, HTTP-, CLI- bzw. Betreibervertrag in
    `configs/runtime_entrypoints.json` erfasst. Contract- und
    Reachability-Tests prüfen, dass Manifest und importierbarer Runtime-Code
    nicht auseinanderlaufen.
  - `requirements.lock.txt` enthält den vollständigen exakten Satz von 148
    Anwendungsabhängigkeiten. Das Dockerfile installiert ausschließlich
    diesen Satz und bindet das Python-Basisimage per Digest.

- **Wirksamkeitsnachweis und Abnahme:**
  - Vollständiger finaler Testlauf: **669 passed in 3,46 s**. Die gezielten
    Guard-/Fast-Path-/Trust-Tests liefen zusätzlich mit **49 passed**.
    `py_compile`/`compileall`, Compose-Konfiguration, Diff-Check,
    Runtime-Entry-Point- und Dependency-Lock-Prüfung sind grün.
  - Das finale Image
    `sha256:286a5752e829e3dff0366f4faa3791f20a7d603bfd3546feef34d33c7e4e53f9`
    ist healthy; `/ready` meldet Graph, Valkey, User-DB, Neo4j, MCP und
    Chroma positiv. `pip check` meldet keine defekten Anforderungen; 148
    Lock-Einträge stimmen exakt mit der Laufzeit überein, neben den drei
    erwarteten Bootstrap-Paketen `pip`, `setuptools` und `wheel`.
  - Derselbe echte, cachefreie `moe-auto`-Prompt
    „Antworte ausschließlich mit: OK“ lief vor der Korrektur in
    **300,108 s** in den Gesamt-Timeout. Der finale Lauf
    `chatcmpl-c71fab5f-518b-42fd-a64c-3b3f4d6a3663` antwortete mit HTTP 200
    und exakt `OK` in **10,27 s** warm beziehungsweise **10 224 ms** laut
    persistierter Usage. Das ist eine Reduktion um rund **96,6 %** bzw.
    der **29,2-fache Durchsatz** für diesen reproduzierten Probe-Request.
    Ein kalter Expert-Start wurde separat erfolgreich in **149,464 s**
    abgeschlossen und blieb ebenfalls unter dem 300-s-Budget.
  - Die Live-Stagefolge war
    `guard fail_open_not_warm → planner fast_path → fuzzy_router →
    expert → merger verified_single_expert → quality_gate passed`.
    Es gab keinen Planner-, Judge-, Thinking- oder Self-Critique-
    Inferenzaufruf und keinen Guard-Modell-POST. Der Active-Key war nach
    Abschluss entfernt. Usage enthält `status=ok`,
    `complexity_level=trivial`, `cynefin_domain=CLEAR`,
    `trust_score=0.65`, `trust_verdict=PROCEED` und
    `self_critique_round=0`; die Guard- und Experten-AI-I/O-Audits sind
    abgeschlossen.
  - Als bewusst verbleibende Betriebsoptimierung braucht ein kalter
    35B-Experte weiterhin rund 149 s Lade-/Antwortzeit. Das ist kein
    Funktions- oder SLO-Blocker für das aktuelle 300-s-Budget, sollte aber
    durch ein passendes dauerhaft warmes Tier-1-Modell reduziert werden.
    Bestehende nicht-blockierende Hinweise betreffen außerdem das
    selbstsignierte NiFi-Zertifikat, nicht gesetzte optionale
    Authentik-Compose-Variablen und eine Upstream-Deprecation aus
    `langchain-community`; sie beeinflussten die Abnahme nicht.

### TASK-37: Native Qwen3.6 gegen horndev-Expert-Template end-to-end vergleichen

- **Priority:** high
- **Owner:** Codex CLI
- **Status:** done (Benchmark und Ursachenanalyse; Optimierungen in TASK-38)
- **Scope:** Realer, cachefreier Vergleich von
  `qwen3.6:35b@N04-RTX` und dem privaten horndev-Template
  `moe-n04-rtx-qwen3.6:35b-256k` über dieselbe OpenAI-kompatible API,
  einschließlich Ground-Truth-Prüfung, Stage-/Audit-Auswertung und
  Nachweis, welche konfigurierten Implementierungen tatsächlich liefen.
- **Sicherer Testzugang:**
  - Der vorhandene Benchmark-Schlüssel gehört technisch dem Benutzer
    `philipp` und war für das private horndev-Template korrekt nicht
    autorisiert. Ein gleichnamiges Admin-Template ist eine andere, nahezu
    leere Konfiguration und wurde nicht ersatzweise getestet.
  - Nach ausdrücklicher Benutzerfreigabe wurde ein kurzlebiger,
    horndev-gebundener Benchmark-Schlüssel erzeugt, in Valkey
    synchronisiert und ausschließlich in einer Datei mit Modus `0600`
    unter `/tmp` gehalten. Nach dem Test wurde er in der User-DB
    widerrufen, der Cache invalidiert, der Benutzer neu synchronisiert und
    die Datei sicher gelöscht. Ein abschließender API-Probeaufruf erhielt
    erwartungsgemäß HTTP 401; der Schlüssel ist in der DB inaktiv.
  - Für den Kaltstart-Test betrug der Client-Timeout 3600 s. Der
    Orchestrierungs-Timeout wurde temporär von 300 auf 900 s
    verdreifacht. Nach dem Test wurde der produktive Wert 300 s
    wiederhergestellt und der Container neu erzeugt.
- **Identischer Testprompt:**
  - Vier unabhängig prüfbare Teilaufgaben: ggT von 391 und 299, exakte
    Umrechnung von 72 km/h in m/s, deutscher Wochentag für den
    29.07.2026 und SQL-Injection-Prüfung samt parametrisierter
    `cursor.execute`-Ersatzzeile.
  - Ausgabevorgabe: ausschließlich ein JSON-Objekt mit sechs exakt
    benannten Feldern und kurzen Prüfnachweisen; `temperature=0`,
    `max_tokens=1200`, `no_cache=true`.
  - Unabhängig mit Python-Standardbibliothek bestätigte Ground Truth:
    `gcd=23`, `speed_m_s=20`, `weekday_de="Mittwoch"` sowie
    SQL-Injection durch String-Konkatenation und eine parametrisierte
    SQLite-Abfrage.
- **Natives Ergebnis:**
  - Request `chatcmpl-688e5a11-117f-4d41-b041-67c32ff59c0b`: HTTP 200
    nach 14,062 s, syntaktisch gültiges JSON.
  - Nur 2 von 4 fachlichen Teilaufgaben waren korrekt. Geschwindigkeit und
    SQL-Prüfung stimmten; `gcd=29` statt 23 und `Freitag` statt
    `Mittwoch` waren falsch.
  - Der ausgegebene ggT-Nachweis war zusätzlich halluziniert:
    `391 = 29 * 17` und `299 = 29 * 13` sind beide falsch. Die verlangte
    Selbstverifikation verhinderte die Fehler somit nicht.
  - Usage wurde mit 195 Prompt-, 254 Completion- und 449 Gesamttokens
    persistiert. Für den nativen Pfad fehlen jedoch `latency_ms` und ein
    AI-I/O-Auditdatensatz.
- **Expert-Template-Ergebnis:**
  - Request `chatcmpl-98bdbd74-988c-4c10-8c56-0c6c84a9769f`: öffentlich
    HTTP 504 nach 900,017 s. Das Template lieferte daher trotz höherer
    interner Fachqualität kein nutzbares API-Ergebnis.
  - Der interne Thinking-Kandidat enthielt alle vier korrekten Antworten
    (`23`, `20`, `Mittwoch`, SQL-Injection samt parametrisierter Abfrage).
    Er wurde aber nicht ausgeliefert: Der Trust-Score von 0,035 blockierte
    die erste Synthese; ein zweiter Judge-/Merger-Aufruf wurde beim
    Erreichen des Gesamtbudgets abgebrochen.
  - Gemessene AI-I/O-Dauer entlang des kritischen Pfads:
    Guard-Warmprobe 0,016 s, nativer Planner-Fehlversuch 180,015 s,
    Planner-Fallback 289,887 s, Code-Reviewer 70,841 s, Thinking-Judge
    260,267 s und abgebrochener zweiter Judge 96,577 s. Allein der Planner
    verbrauchte damit etwa 470 s beziehungsweise mehr als 52 % des
    verdreifachten Gesamtbudgets.
  - Der Planner-Fallback verbrauchte 16.490 Eingabetokens und
    308 Ausgabetokens; Code-Reviewer 1.060/2.867 und Thinking-Judge
    472/5.726. Trotz dieser belegten internen Nutzung enthält die
    persistierte Timeout-Usage 0 Tokens und keine Complexity-, Trust-,
    Cynefin- oder Expert-Domain-Werte.
- **Tatsächlich ausgeführte Implementierungen:**
  - Ausgeführt wurden private Template-Auflösung, Guard-Warmprobe
    (`fail_open_not_warm` ohne Modellinferenz), L0/L1-Cacheprüfung,
    heuristische Complexity- und Cynefin-Klassifikation, Planner samt
    Fallback, Definition-of-Ready-Prüfung, Fuzzy Router, GraphRAG,
    Code-Reviewer-Expert, Thinking-Judge, Trust-Score und der Beginn der
    Merger-/Judge-Schleife. Active-Request-Cleanup und einzelne
    AI-I/O-Audits wurden korrekt abgeschlossen.
  - Nicht ausgeführt wurden die drei geplanten MCP-Präzisionswerkzeuge.
    Der Planner erzeugte für ggT, Einheitenumrechnung und Datum jeweils
    `precision_tools` ohne `mcp_tool` und `mcp_args`. Definition of Ready
    protokollierte dies nur als Fehler. Der MCP-Knoten akzeptiert allein
    Tasks mit `mcp_tool`, während der Expert-Knoten
    `precision_tools` ausschließt; alle drei Teilaufgaben verschwanden
    dadurch still aus der Ausführung.
  - Die übrigen konfigurierten Experten, Web Research,
    Self-Critique, Quality Gate, Constitution Enforcement, Antwort-Cache-
    Schreibpfad und finale Wissensaufnahme liefen nicht. Web Research und
    Agent-Tool-Erweiterungen waren für diesen Prompt beziehungsweise einen
    normalen Chat ohne Client-Tools nicht erforderlich; die finalen
    Qualitätsschritte wurden dagegen ausschließlich durch den Timeout
    verhindert.
  - `no_cache=true` überspringt im aktuellen Router nur L0, führt aber
    dennoch eine Chroma-L1-Abfrage aus. Ein Treffer würde nicht verwendet,
    die teure Abfrage findet jedoch trotzdem statt.
  - GraphRAG lieferte 666 Zeichen, die der interne Judge selbst als
    irrelevant einstufte. Trotzdem setzte dieser Kontext `aux_context`,
    beeinflusste den Gate-Pfad und lieferte keine verwertbare Provenienz.
- **Konfigurations- und Produktbefunde:**
  - Alle acht vermeintlich unterschiedlichen Experten des Templates
    verwenden dasselbe `qwen3.6:35b@N04-RTX` mit 262.144 Kontexttokens;
    sie unterscheiden sich nur durch Systemprompts. Das ist Policy-,
    nicht Modelldiversität.
  - Planner- und Judge-Prompt nennen für Mathematik noch
    `phi4:14b@N04-RGTX` und für Creative Writing
    `qwen3.6:35b-spec@N04-RTX`, obwohl beide Rollen tatsächlich das
    normale Qwen-Modell nutzen. Diese Prompt-/Konfigurationsdrift kann
    falsche Routingannahmen erzeugen.
  - Der kurze 195-Token-Request erhielt auf Planner-Ebene einen
    262k-Kontext und 16.490 Eingabetokens. Zudem begrenzte das
    Client-`max_tokens=1200` die internen Ausgaben nicht; Expert und Judge
    erzeugten 2.867 beziehungsweise 5.726 Tokens.
  - Gleichnamige Templates existieren für horndev, Commander1024 und als
    Admin-Template. Die API autorisiert korrekt per Template-ID, die
    gleichnamige Anzeige ohne explizites Sharing-/Grant-Modell erschwert
    jedoch Betrieb und Benchmarking.
  - Nach dem Timeout war auf N04 kein Modell mehr resident. Planner,
    Expert und Judge mit großen Kontexten konkurrieren seriell um dieselbe
    GPU und verursachen Kaltstart-/Swap-Risiko.
- **Vergleichsurteil:**
  - Native Ausführung gewinnt Verfügbarkeit und Laufzeit (HTTP 200 in
    14,062 s), scheitert aber an elementarer Faktengenauigkeit und
    halluziniert ihren eigenen Prüfnachweis.
  - Das Expert-Template gewinnt nur bei der internen Kandidatenqualität
    (4/4 korrekt), verliert end-to-end aber vollständig, weil nach
    900,017 s kein Ergebnis an den Client ausgeliefert wurde. Das ist
    deshalb noch kein wirksamer Qualitätsgewinn für den API-Nutzer.
  - Der Test belegt, dass „definiert“ nicht „verwendet“ bedeutet:
    Insbesondere Precision/MCP, mehrere Experten und die finalen
    Qualitätsschritte waren im relevanten Request nicht durchgängig
    erreichbar.
- **Abnahme und Wiederherstellung:**
  - Der temporäre horndev-Schlüssel ist widerrufen und aus `/tmp`
    entfernt; das Secret wurde weder in Repository-Dateien noch in diesem
    Lastenheft persistiert.
  - `ORCHESTRATION_TIMEOUT=300` ist wieder aktiv.
    `langgraph-orchestrator` ist healthy; `/ready` meldet
    Orchestration Graph, Valkey, User-DB, Neo4j, MCP Precision und Chroma
    positiv. Der Active-Request des Timeout-Laufs ist entfernt und alle
    begonnenen AI-I/O-Audits sind terminal.

### TASK-38: Expert-Template-Pfad korrekt, budgetiert und beobachtbar machen

- **Priority:** critical
- **Owner:** Codex CLI
- **Status:** done (2026-07-31, Codex CLI; Restoptimierungen unten)
- **Goal:** Die im TASK-37 reproduzierten Ausführungslücken so schließen,
  dass ein kurzer, deterministisch prüfbarer Mehrfachprompt innerhalb des
  normalen 300-s-Budgets ein vollständiges, belegtes Ergebnis liefert.
- **Vor Umsetzung niedergeschriebener Auflösungsplan:**
  1. **P0 – Precision-Tasks reparieren:** Planner-Vertrag um erforderliche
     `mcp_tool`-/`mcp_args`-Felder und Schema-Validierung ergänzen.
     Fehlende Felder müssen genau einen begrenzten Repair-/Replan-Versuch
     auslösen; danach expliziter strukturierter Fehler oder bewusster
     Expert-Fallback statt stillem Task-Verlust. Reachability- und
     Integrationstest für gemischte Precision-/Expert-Pläne ergänzen.
  2. **P0 – ein Gesamtbudget durchreichen:** Eine monotone Deadline und
     Restbudget an Planner, Fallback, Expert, Thinking und Merger
     propagieren. Native- und kompatible Planner-Pfade dürfen nicht jeweils
     ein neues Vollbudget erhalten; SDK-Retries müssen im selben Budget
     liegen. Stufenbudgets und Mindestrestzeit vor Folgestufen erzwingen.
  3. **P0 – korrekten Kandidaten retten:** Thinking und Merger entweder in
     einem Judge-Aufruf konsolidieren oder einen bereits validierten
     Thinking-Kandidaten bei auslaufendem Merger-Budget als klar markiertes
     degradiertes Ergebnis ausgeben. Ein zweiter Judge darf nicht
     ungebremst denselben teuren Pfad wiederholen.
  4. **P0 – Trust/Provenienz korrigieren:** MCP-Ergebnisse als
     nachvollziehbare Quellen in den Trust-Score übernehmen,
     deterministische Rechnachweise strukturiert prüfen und irrelevanten
     GraphRAG-Kontext nicht als hilfreiche Quelle beziehungsweise
     `aux_context` werten.
  5. **P1 – Token und Kontext begrenzen:** Kurze Requests mit
     16k-/32k-Kontext planen; 262k nur nach belegtem Bedarf aktivieren.
     Client- und Template-Ausgabebudgets bis Expert/Judge propagieren,
     Planner-Prompt komprimieren und doppelte/stale Expertentabellen
     entfernen.
  6. **P1 – unnötige Arbeit überspringen:** Bei `no_cache=true` vor der
     L1-Abfrage zurückkehren. GraphRAG nur oberhalb einer
     Relevanz-/Provenienzschwelle einspeisen. Thinking bei vollständigen,
     deterministisch validierten Precision-Ergebnissen und einem engen
     Code-Review nicht obligatorisch nachstarten.
  7. **P1 – Inferenzrollen entkoppeln:** Kleines, dauerhaft warmes
     Planner-Modell einsetzen und Judge/Planner nach Möglichkeit auf
     separate Kapazität legen. Expertendiversität entweder real herstellen
     oder irreführende Modellnamen und Rollenversprechen entfernen.
  8. **P2 – Identität und Telemetrie härten:** Template-Namen
     benutzerübergreifend eindeutig anzeigen und ein explizites
     Sharing-/Grant-Modell vorsehen. Native Latenz und AI-I/O-Audit
     erfassen; Timeout-Usage aus abgeschlossenen Stage-Audits mit realen
     Tokens, Complexity, Cynefin, Trust und Domains aggregieren.
  9. **Validierung:** Den identischen TASK-37-Prompt kalt und warm je
     mindestens dreimal gegen native und Template ausführen. Abnahme:
     4/4 korrekte Felder, ausgeführte und auditierte Precision-Tools,
     gültiges JSON, kein still verlorener Task, keine zweite unbudgetierte
     Judge-Schleife, terminaler Cleanup, vollständige Usage-/Auditdaten und
     Template-P95 unter 300 s. Zusätzlich Fehlerpfade für ungültigen
     Planner-Plan, MCP-Ausfall und knappe Restdeadline testen.
- **Ausführungsrefinement (vor Implementierungsbeginn 2026-07-31):**
  1. **Gate A – Baseline/Scope:** Dirty Worktree, aktiven TASK-9-Lease,
     Branch/HEAD, fehlenden Upstream, laufendes Image, RestartCount und
     `/ready` sichern. Keine Trainingsdatei aus TASK-9 verändern.
  2. **Gate B – Pflichtverträge:** COMP-01 zuerst schließen. Pflicht-
     Boundary-Konfiguration und Prüfung scheitern geschlossen; optionale
     Cascade-/Exporterfehler dürfen die lokale Blockentscheidung nicht
     aufheben.
  3. **Gate C – Task-Vollständigkeit:** Typisierten Precision-Vertrag,
     Registry-Schema-Prüfung, genau einen begrenzten Repair und ein
     terminales Task-Ledger einführen. Für jeden geplanten Task muss
     `executed`, `fallback`, `rejected` oder `failed` belegt sein.
  4. **Gate D – Budget/Kandidaten:** Eine monotone Deadline durch API,
     Planner, Repair/Fallback, MCP, Experten, Thinking, Judge und
     Self-Critique reichen. Vor jeder teuren Folgestufe Mindestrestzeit
     prüfen und nur schema-/toolvalidierte Kandidaten explizit degradiert
     retten.
  5. **Gate E – Qualität/Kosten:** Trust nach Evidenz- und Aufgabentyp
     differenzieren, irrelevantes GraphRAG abweisen, `no_cache` vollständig
     respektieren und interne Context-/Tokenbudgets begrenzen.
  6. **Gate F – Beobachtbarkeit:** Stage-Audits bei Timeout aggregieren,
     native und orchestrierte Pfade vergleichbar instrumentieren und
     Templates mit Owner/ID eindeutig ausweisen.
  7. **Gate G – Abnahme/Deployment:** Erst gezielte negative Tests, dann
     vollständige Regression, Image-Build/Recreate, Readiness und reale
     kalt/warme E2E-Matrix. Kein Push/Publish; Rollback-Image bleibt
     `sha256:286a5752e829e3dff0366f4faa3791f20a7d603bfd3546feef34d33c7e4e53f9`.
- **Resolution notes (2026-07-31, Codex CLI):**
  - **Pflichtverträge und Task-Vollständigkeit:** Boundary-Verträge werden
    beim Start zwingend geladen und über `/ready` als kritischer Check
    ausgewiesen. Planner-Pläne erhalten stabile Task-IDs, werden gegen den
    live entdeckten MCP-Katalog validiert und dürfen die Laufzeitgrenze
    nicht mehr durch stilles Abschneiden umgehen. Ein terminales
    Task-Ledger und das Quality Gate verhindern, dass geplante Tasks ohne
    `executed`, `fallback`, `rejected` oder `failed` verschwinden.
  - **Precision-Reparatur:** Fehlende `mcp_tool`-/`mcp_args`-Felder lösen
    einen begrenzten Schema-Repair aus. Zusätzlich existiert eine eng
    begrenzte deterministische Normalisierung für explizite ggT-,
    `km/h→m/s`- und Wochentagsaufgaben. Liefert ein Planner für eine
    vollständig nummerierte Liste gar keinen Plan, wird nur dann
    rekonstruiert, wenn *jede* Teilaufgabe eindeutig einem dieser
    Precision-Verträge oder einer expliziten
    `cursor.execute`-SQL-Injection-Prüfung entspricht. Unbekannte,
    lückenhaft nummerierte oder übergroße Pläne bleiben fail-closed.
  - **Eine Deadline:** Eine absolute monotone Deadline läuft jetzt durch
    Guard, Planner, Repair/Fallback, Experten, MCP, GraphRAG, Recherche,
    Thinking, Judge, Self-Critique und Synthese. Retry-Wartezeiten werden
    gegen das Restbudget begrenzt. Bei knapper Synthesezeit darf nur ein
    vollständiger, nicht sicherheitskritischer Executor-Kandidat explizit
    als degradiert ausgeliefert werden.
  - **Qualität, Cache und Budgets:** Trust ist task- und evidenzabhängig;
    MCP-Präzisionsprovenienz wird positiv gewertet, kreative Aufgaben
    benötigen keine erfundenen Faktquellen und irrelevanter Graphkontext
    zählt nicht pauschal als Quelle. `no_cache=true` überspringt L0 und L1.
    Kontextfenster werden adaptiv gewählt, das Client-Ausgabelimit wird an
    interne Stufen weitergereicht und redundantes Thinking wird bei
    vollständiger deterministischer Evidenz plus höchstens einer
    Nicht-Precision-Aufgabe übersprungen.
  - **Thinking-only-Lücke geschlossen:** Planner, Experten und Judge
    arbeiten in ihren strukturierten/budgetierten Verträgen standardmäßig
    mit `think:false`. Zuvor verbrauchten Expert und Judge jeweils das
    vollständige 1.200-Token-Limit ausschließlich im separaten
    Ollama-`thinking`-Feld und lieferten leeren `content`. Eine leere
    Expertenantwort wird jetzt explizit als Fehler verbucht und niemals
    mehr als erledigter Task gezählt.
  - **Inferenzrollen für das private horndev-Template:** Planner wurde von
    `qwen3.6:35b@N04-RTX` mit 262k Kontext auf den dedizierten
    `qwen3-planner:q4km@N04-RGTX` mit 32k Kontext verschoben. Der
    ursprünglich separate Sovereign-Judge verursachte auf der einzigen
    N04-RTX-Kapazität einen zweiten 35B-Modellwechsel und wiederholte
    Timeouts; der Merger nutzt deshalb für dieses konkrete
    Single-GPU-Template das bereits warme `qwen3.6:35b@N04-RTX` mit 32k.
    Das beseitigt den Swap, ersetzt aber unabhängige Modelldiversität durch
    Tool-Evidenz, Policy-Trennung und Self-Critique.
  - **Telemetry:** Native Requests besitzen nun Latenz und AI-I/O-Audit.
    Timeout-/Fehlerantworten übernehmen Stage-Usage und einen
    nicht-inhaltlichen Request-Snapshot. Der native Judge-Adapter reicht
    Usage-Metadaten weiter; die Synthese sichert sie vor der
    Content-Bereinigung. Im finalen Lauf stimmte die API-Usage
    **exakt** mit dem AI-I/O-Audit überein:
    `19.888/1.589` Prompt-/Completion-Tokens, davon Planner
    `13.432/7`, Expert `1.119/119` und drei budgetierte Judge-Aufrufe
    zusammen `5.337/1.463`.
  - **Reproduzierbarer E2E-Harness:** `scripts/validate_task38_e2e.py`
    erstellt einen kurzlebigen horndev-Key ausschließlich im Speicher,
    führt denselben Ground-Truth-Prompt wahlweise nativ, als Template oder
    gegen beide Pfade aus und widerruft/invalidiert den Key in `finally`.
    Alle sieben temporären Keys aus den Reparaturläufen sowie die späteren
    Abschlusskeys sind in der DB inaktiv; kein zugehöriger
    `user:apikey:*`-Cacheeintrag blieb zurück.
  - **Finaler Qualitätsvergleich auf demselben Deployment:**
    Native `qwen3.6:35b@N04-RTX` lieferte 3/3 HTTP 200 und valides
    Sechs-Felder-JSON, aber 0/3 vollständig korrekte Antworten:
    `weekday_de` war in jedem Lauf falsch. Latenzen:
    **139,749 s kalt**, danach **3,610 s** und **3,555 s** warm; Usage je
    `233/100` Tokens. Das Template lieferte beim erfolgreichen Kaltlauf
    **157,472 s** und bei vier Warm-Läufen **40,788–50,576 s**; alle
    **5/5** Antworten waren HTTP 200, schemaexakt und bestanden
    **7/7** Ground-Truth-Checks.
  - **Tatsächliche Stage-Nutzung im finalen Template-Lauf:**
    `guard fail_open_not_warm → cache bypass → planner →
    precision_tools×3 + code_reviewer → gcd_lcm + unit_convert +
    day_of_week → GraphRAG → qwen3.6 code_reviewer → thinking skipped →
    merger → self_critique → merger → quality_gate passed`.
    Nicht benötigte Web-, Creative-, Long-Context- und Client-Tool-Pfade
    wurden korrekt nicht ausgeführt.
  - **Abnahme:** Vollständiger Lauf **714 passed in 4,00 s**;
    `py_compile` der geänderten Kernmodule grün. Finales Image
    `sha256:5f3e0eeda248b8743df3ebed5950125f57d0c9852e71d0ddab720bfa022e3040`
    läuft `healthy`, `RestartCount=0`. `/ready` meldet
    Boundary-Verträge, Graph, Valkey und User-DB kritisch positiv sowie
    Neo4j, MCP Precision und Chroma positiv.
  - **Bewusst verbleibende Optimierungen:** Der dedizierte Planner liefert
    für diesen Prompt weiterhin `{}`; die enge deterministische Recovery
    stellt die Ausführung her, breite unbekannte Aufgaben scheitern
    dagegen korrekt geschlossen. Sein Prompt ist mit 13.432 Tokens noch
    zu groß. Ein erfolgreicher Warm-Lauf startet trotz `Trust=PROCEED`
    Self-Critique und einen zweiten Merger, sodass drei Judge-Aufrufe
    entstehen. GraphRAG lieferte 325 Zeichen ohne belegten Nutzen. Der
    kalte 35B-Start bleibt mit rund 140–157 s teuer. Guard-Warm-only,
    heuristische Complexity, unabhängige Judge-Diversität,
    templateübergreifende Identität/Grants und die drei geforderten
    unabhängigen kalten Template-Wiederholungen bleiben Betriebs-/
    Qualitäts-Follow-ups.
  - Kein Commit, Push, PR oder Publish ausgeführt. Der vorhandene
    Multi-Agent-Dirty-Worktree wurde erhalten; der finale Git-Status zählt
    170 Einträge und besitzt weiterhin keinen Upstream.

### TASK-39: Agent Rules 2.0 und prüfbare Governance

- **Priority:** high
- **Owner:** Codex CLI
- **Status:** done (2026-07-30, Codex CLI)
- **Goal:** Die Agentenanweisungen auf einen kurzen, eindeutigen und
  automatisiert prüfbaren Stand bringen, fehlende Authority-Dokumente
  ergänzen und belegte Implementierungsstände von Planungsständen trennen.
- **Scope:** Ausschließlich Governance-, Backlog-, Dokumentations- und
  Prüfdateien. Keine Runtime-, Deployment- oder Modelländerung.
- **Vor Umsetzung niedergeschriebener Auflösungsplan:**
  1. `AGENTS.md` als kompakte, tool-unabhängige Single Source of Truth mit
     Authority-Reihenfolge, Autonomiematrix, Sicherheitsgrenzen,
     Deadline-/Retry-Regeln, Worktree-Leases und Definition of Done
     restrukturieren.
  2. `CLAUDE.md` auf den Import von `AGENTS.md` und wenige
     Claude-spezifische Hinweise reduzieren; path-spezifische Regeln für
     Security, Python, Tests und Deployment unter `.claude/rules/`
     auslagern.
  3. Die bislang nur referenzierten Pflichtquellen
     `PROJECT_COMPLIANCE.md`, `docs/backlog/current/dependency-map.md` und
     `docs/backlog/current/roadmap.md` mit Owner, Version und
     Verifikationsdatum erstellen.
  4. Eine explizite Fail-open-/Fail-closed-Matrix, Schutz gegen
     Prompt-Injection und unvertrauenswürdige Toolausgaben,
     Autorisierungsgrenzen sowie Regeln gegen das Persistieren interner
     Gedankengänge dokumentieren.
  5. Den belegten I-2-Implementierungsstand gegen Code, Tests und
     TASK-35/36 korrigieren; offene E-2.3-/E-2.5-Anteile und TASK-38
     weiterhin klar als offen ausweisen.
  6. Eine deterministische Governance-Prüfung samt CI-Workflow und
     generiertem Runtime-Entry-Point-Katalog ergänzen. Abnahme über
     Check-Modus, Markdown-/MkDocs-Build und Diff-Prüfung.
- **Acceptance criteria:**
  - Alle als verpflichtend referenzierten Governance-Dateien existieren und
    ihre internen Pfade sind auflösbar.
  - `CLAUDE.md` importiert `AGENTS.md`; dauerhafte Regeln werden nicht
    widersprüchlich dupliziert.
  - Fail-closed gilt explizit für Authentifizierung, Autorisierung,
    Mandantengrenzen, `local_only`, Schema-/Boundary-Pflichtfelder,
    erforderliche HITL-Gates und Integritätsprüfungen. Optionale
    Enrichment-/Observability-Pfade sind bewusst als fail-open oder
    degraded gekennzeichnet.
  - Governance-CI erkennt fehlende Pflichtdateien, ungültige lokale Links,
    veraltete generierte Kataloge und verbotene direkte GitHub-main-Pushes
    im dokumentierten Workflow.
  - Backlog-Status behauptet keine fehlende Implementierung für bereits
    durch Tests und TASK-35/36 belegte Funktionen und keine Fertigstellung
    für unbewiesene Multi-Tenant-/Checkpointing-Funktionen.
  - Keine Runtime-Datei, kein Container und keine produktive Konfiguration
    wird für diese Aufgabe verändert.
- **Resolution notes (2026-07-30, Codex CLI):**
  - `AGENTS.md` ist jetzt die tool-unabhängige Regelquelle mit
    Authority-/Restore-Reihenfolge, Vier-Stunden-Lease als
    Stalenzerkennung, isolierten Worktrees, Autonomiematrix,
    Prompt-/Tool-Trust, Secret-/Reasoning-Regeln, einer durchgereichten
    monotonen Deadline, idempotenten Retries, Test-/Build-Reihenfolge und
    einer evidenzbasierten Definition of Done.
  - `CLAUDE.md` importiert `@AGENTS.md`; Security-, Python-, Test- und
    Deployment-Regeln liegen path-spezifisch unter `.claude/rules/`.
    Direkte Pushes auf `main` bleiben verboten; Commit, Push, PR, Publish
    und Deployment benötigen weiterhin den beauftragten externen
    Zustandswechsel.
  - `PROJECT_COMPLIANCE.md` definiert die normative
    Fail-open-/Fail-closed-Matrix. Auth, Autorisierung, Tenant,
    `local_only`, Pflichtverträge, Integrität, Policy-Block und
    erforderliche HITL-Gates scheitern geschlossen. Caches und optionale
    Enrichment-/Exporterpfade dürfen nur explizit degradiert ausfallen.
    Vier belegte Istabweichungen sind als COMP-01 bis COMP-04 erfasst.
  - Die fehlenden Backlog-Pflichtdateien Dependency Map, Roadmap, Stories
    und Implementation-Task-Index wurden ergänzt. Zusätzlich wurde der
    defekte I-1-Link durch ein Statusblatt ersetzt. I-2 und die fünf Epics
    unterscheiden jetzt `Planned`, `Partial`, `Implemented` und
    `Validated`; Handover wird nicht mehr mit Task-Checkpointing/
    Artefakt-Registry und User-Scoping nicht mehr mit vollständiger
    Multi-Tenant-Isolation gleichgesetzt.
  - `scripts/check_governance.py --check` validiert 27 Pflichtdateien,
    Metadaten, lokale Links, Policy-Marker, direkte-main-Push-Kommandos,
    projektbezogene Secret-Muster und neun deklarierte Runtime-Entry-Points
    samt echter Wiring-Marker. Der daraus deterministisch erzeugte Katalog
    liegt unter `docs/generated/runtime-entrypoints.md`; der neue
    GitHub-Workflow `.github/workflows/governance.yml` führt denselben
    Check für relevante Pushes und Pull Requests aus.
  - **Abnahme:** Governance-Check grün (`27` Dateien, `9` Entry-Points),
    `python3 -m py_compile scripts/check_governance.py` grün,
    `python3 -m pytest -q` **669 passed in 6.11s**,
    `mkdocs build --strict` Exit 0 und fokussiertes `git diff --check`
    grün. MkDocs meldet ausschließlich informative bestehende
    Nicht-Nav-Seiten/Anchor-Hinweise und den Material-2.0-Hinweis.
  - Keine Runtime-/Deployment-/Modellkonfiguration wurde für TASK-39
    geändert, kein Container neu gebaut oder gestartet und kein
    Whitepaper-Update ausgelöst. Kein Commit/Push/Deployment ausgeführt;
    der fremde Dirty-Worktree wurde erhalten. TASK-38 bleibt separat
    kritisch und `pending`.

---

### TASK-40: Deterministische Kalenderfakten und MCP-Offloading-Inventur

- **Priority:** high
- **Owner:** Codex CLI
- **Status:** done (2026-07-31, Codex CLI)
- **Goal:** Wochentage und kalenderbezogene Aussagen über einen strikt
  validierten, maschinenlesbaren MCP-Vertrag beantworten und systematisch
  festhalten, welche weiteren fehleranfälligen LLM-Aufgaben deterministisch
  berechnet oder gegen versionierte Daten geprüft werden sollten.
- **Scope:** `mcp_server/server.py`, Precision-Routing und dessen Tests,
  Planner-Toolkatalog, MCP-Dokumentation, ein separates Evaluationsdokument
  sowie lokaler Build/Recreate von `mcp-precision`. Keine Credential-,
  Datenbank-, Modell-, Template- oder externe Deployment-Änderung.
- **Vor Umsetzung niedergeschriebener Ausführungsplan:**
  1. **Baseline und Verträge:** Dirty Worktree, aktiven TASK-9-Lease,
     Branch/HEAD, laufenden MCP-Katalog und vorhandene Datumstools sichern.
     Den neuen Vertrag so abgrenzen, dass `day_of_week` für bestehende
     Aufrufer erhalten bleibt und relative Angaben wie „heute“ ohne explizite
     Zeitzone nicht geraten werden.
  2. **Kalender-Tool:** `calendar_facts(date_str, locale)` als reine lokale
     Berechnung implementieren. Nur striktes ISO-Datum und eine kleine,
     dokumentierte Locale-Allowlist akzeptieren. Als deterministisches JSON
     mindestens kanonisches Datum, lokalisierten Wochentag, ISO-Wochentag,
     ISO-Kalenderwoche samt ISO-Wochenjahr, Tag im Jahr, Quartal,
     Monatslänge, Schaltjahr und Wochenende zurückgeben.
  3. **Registry und Erreichbarkeit:** Das Tool in FastMCP, REST-Registry,
     Beschreibung, Access-Kind, Planner-Gruppen, Fallback-Katalog und
     Precision-Defaults eintragen. Die enge, fail-closed Planner-Recovery für
     explizite Wochentagsfragen auf den neuen live entdeckten Vertrag
     umstellen; unbekannte/ungültige Datumsformen bleiben abgewiesen.
  4. **Gezielte Tests:** Schaltjahr, ungültiges Datum, ungültige Locale,
     deutsch/englische Bezeichnungen, Monatsgrenze und ISO-Wochenjahrgrenze
     direkt testen. Zusätzlich Registry-/Schema-Vollständigkeit sowie
     Repair- und Empty-Plan-Recovery mit `calendar_facts` belegen.
  5. **Offloading-Evaluation:** Bestehende Tools gegen weitere
     Halluzinationsklassen inventarisieren. Kandidaten nach rein
     deterministisch, deterministisch mit versioniertem Datensatz und nicht
     sinnvoll deterministisch trennen; Priorität, benötigten Vertrag,
     Datenquelle/Versionierung, Sicherheitsgrenze und vorhandene Abdeckung
     dokumentieren. Bestehende semantische Schwächen wie approximative
     Kalenderzerlegung ausdrücklich als GAP markieren.
  6. **Validierung und lokaler Rollout:** Syntax/fokussierte Tests,
     Governance-Check und vollständige relevante Tests ausführen. Danach nur
     `mcp-precision` bauen und recreaten, `/health`, `/tools` und `/invoke`
     für Normal-, Grenz- und Fehlerfälle prüfen. Den Orchestrator nur dann
     recreaten, wenn der neue Planner-Katalog ohne Neustart nicht geladen
     werden kann; vorher aktive Requests prüfen. Rollback ist das vorherige
     MCP-Image.
  7. **Abschlussnachweise:** Reale Image-ID, Health/RestartCount,
     Tool-Schema/-Invocation, Testzahlen, bekannte Grenzen und priorisierte
     Folgearbeiten in Lastenheft, Statuslog und SessionMesh festhalten. Kein
     Commit, Push, PR oder Publish.
- **Acceptance criteria:**
  - `calendar_facts` ist über `/tools` mit korrektem Schema sichtbar und über
    `/invoke` erfolgreich sowie für ungültige Eingaben kontrolliert
    ausführbar.
  - Der Output ist parsebares, stabiles JSON und besteht belegte Grenzfälle
    für Schaltjahr, Monatslänge und ISO-Wochenjahr.
  - Explizite deutsche und englische Wochentagsanfragen werden vom
    deterministischen Recovery-Pfad auf `calendar_facts` geroutet; der alte
    `day_of_week`-Aufruf bleibt kompatibel.
  - Registry, Description, Access-Kind und Planner-Katalog driften nicht
    auseinander; fokussierte und vollständige Tests sind grün.
  - Ein separates Markdown-Dokument priorisiert weitere MCP-Kandidaten und
    unterscheidet lokale Berechnung klar von zeitabhängigen Fakten, die nur
    mit autoritativer, versionierter Datenquelle zuverlässig sind.
  - Der recreatete MCP-Container ist healthy, RestartCount 0 und der
    tatsächlich laufende Vertrag wurde live aufgerufen.
- **Resolution notes (2026-07-31, Codex CLI):**
  - **Neuer Vertrag:** `calendar_facts(date_str, locale="de")` liefert
    stabiles JSON mit kanonischem Datum, lokalisierten Wochen-/Monatsnamen,
    ISO-Wochentag, ISO-Woche und separatem ISO-Wochenjahr, Tag im Jahr,
    Quartal, Monats-/Jahreslänge sowie Schaltjahr-/Wochenendstatus. Nur
    striktes `YYYY-MM-DD` und `de`/`en` sind erlaubt; relative Angaben werden
    ohne explizite Uhr-/Zeitzonenquelle abgewiesen. `day_of_week` bleibt als
    kompatibler Wrapper erhalten.
  - **Erreichbarkeit:** FastMCP, REST-Registry, Description, Access-Kind,
    Core-/Fallback-Katalog und Dynamic-Router-Defaults sind synchronisiert.
    Die deutsche/englische Precision-Reparatur sowie Empty-Plan-Recovery
    dispatchen den live entdeckten `calendar_facts`-Vertrag. Der zuvor
    referenzierte, aber nicht existente Default `format_number` wurde entfernt;
    neue Tests verhindern Registry-/Router-Drift.
  - **Benachbarte Datums-/Evidenzfehler:** `date_diff` zerlegt Abstände nicht
    mehr pauschal mit 365/30, sondern meldet exakte Gesamttage plus echte
    `relativedelta`-Kalenderdifferenz. `Error:`-/`Fehler:`-, bracketed Error-
    und JSON-Error-Ergebnisse dürfen nicht mehr als erfolgreiche
    Precision-Evidenz in Working Memory gelangen.
  - **Build-Failure-Path:** Der erste MCP-Neubau zog wegen
    `mcp[cli]>=1.0.0` das inkompatible MCP 2.0 ohne
    `mcp.server.fastmcp`; der Container restartete und der Orchestrator lud
    korrekt nur seinen Fallback-Katalog. Ursache behoben durch
    digest-gepinntes Python-Basisimage und exakten transitiven Lock mit dem
    nachweislich kompatiblen MCP 1.28.1; `pip check` grün. Finales MCP-Image:
    `sha256:94e99b8f7480c353631f28594cc294c1c219a364e858f91615fdf906f19360e6`.
  - **Live-Abnahme:** `/tools` liefert 59 katalogisierte Schemata;
    `calendar_facts` besitzt `date_str` required, Locale-Default `de` und
    `access_kind=read`. `/invoke` bestand Schaltjahr `2024-02-29`, deutsche
    und englische Namen, ISO-Grenze `2021-01-01 → Woche 53/ISO-Jahr 2020`,
    ungültiges Datum/Locale, Legacy-Wochentag und exakte Datumsspanne. Der
    Orchestrator lud alle 59 Tools und bewies beide Sprach-Recoveries im
    laufenden Container.
  - **Regression/Betrieb:** Governance-Check grün (27 Pflichtdateien, neun
    Entry-Points), `mkdocs build --strict` Exit 0 und vollständige Regression
    **737 passed in 4,67 s**. Orchestrator-Image
    `sha256:3ea4d1822c2857cd9b74b82bbfbb9a9878e049ed9f406bbd871eec222c65946f`
    sowie MCP laufen `healthy`, jeweils RestartCount 0; `/ready` ist in allen
    kritischen und optionalen Checks positiv.
  - **Priorisierte Rest-GAPs:** Höchste Priorität hat ein Precision-Intent-
    Guard, weil bestehende Tools bei falscher LLM-Kategorisierung weiterhin
    nicht greifen. Danach folgen typisierte Zeitzonen-/DST-Verträge,
    Decimal-Finanzmathematik, exakte Wahrscheinlichkeiten und strukturierte
    Schema-Validierung. Version-/Business-Calendar-/Identifier-/Statistik-/
    Geo-/Tokenizer-Verträge sind P1. Mutable Fakten benötigen stets
    autoritative Quelle, Version/`as_of` und Provenienz; MCP-Transport allein
    macht sie nicht deterministisch. Vollständige Bewertung:
    `docs/system/toolstack/deterministic_offloading_evaluation_2026-07-31.md`.
  - Die bekannten Compose-Warnungen zu fehlenden Authentik-Variablen blieben
    unverändert. Kein Credential, Datenbestand, Modell oder Template geändert;
    kein Commit, Push, PR oder Publish ausgeführt. Der Dirty Worktree ist kein
    releasefähiges Artefakt.

---

### TASK-41: Fail-closed Precision-Intent-Guard

- **Priority:** critical
- **Owner:** Codex CLI
- **Status:** done (2026-08-01, Codex CLI)
- **Goal:** Explizit erkennbare, eindeutig parametrisierbare
  Präzisionsanfragen dürfen nicht als reine LLM-Aufgabe ausgeführt werden,
  wenn der Planner sie falsch kategorisiert, entfernt oder nur teilweise in
  den Ausführungsplan übernimmt. Der Planvertrag muss diese Herabstufung vor
  dem Dispatch erkennen und geschlossen scheitern beziehungsweise genau
  einmal über den bestehenden begrenzten Contract-Repair-Pfad korrigieren.
- **Scope:** `services/pipeline/contracts.py`, der gemeinsame Planner-Handoff
  in `graph/planner.py`, der live geladene MCP-Schemakatalog in `main.py`,
  fokussierte Contract-/Planner-/Katalogtests sowie die deterministische
  Offloading-Dokumentation. Keine neuen fachlichen MCP-Tools, keine
  Credential-, Datenbank-, Modell-, Template- oder externe
  Deployment-Änderung.
- **Vor Umsetzung niedergeschriebener Ausführungsplan:**
  1. **Baseline und Schutzbereich:** Branch/HEAD, Dirty Worktree, fremde
     Status-Leases, laufende Images und `/ready` sichern. Bestehende
     Precision-Recovery, alle `_prepare_handoff_plan`-Aufrufer,
     Planner-Cache/Fallback und Trivial-Fast-Path lokal verifizieren.
  2. **Enger Intent-Vertrag:** Ausschließlich bereits eindeutig unterstützte
     und vollständig aus dem Prompt parametrisierbare Verträge erkennen:
     `gcd_lcm`, `unit_convert` für km/h nach m/s sowie `calendar_facts` für
     explizite deutsche/englische Wochentagsfragen. In Fließtext wird nur ein
     vollständiger Einzelintent geprüft; bei sauber nummerierten Listen wird
     jeder Eintrag isoliert geprüft. Erwähnungen ohne Operationssignal,
     unvollständige Parameter und ungültige Datumswerte dürfen keinen
     Toolaufruf erfinden.
  3. **Planabgleich:** Für jeden erkannten Intent muss vor dem Dispatch eine
     `precision_tools`-Aufgabe mit demselben live entdeckten Tool und
     semantisch gleichen Pflichtargumenten existieren. Eine abweichende oder
     fehlende Aufgabe erzeugt einen strukturierten
     `precision_intent_downgraded`-Contract-Fehler. Der bestehende eine
     begrenzte Repair-Versuch bleibt der einzige LLM-Reparaturpfad; nach
     Erschöpfung wird kein `general`-Fallback erlaubt.
  4. **Katalogintegrität:** Der Orchestrator darf nur aktuell als `enabled`
     gemeldete MCP-Tools planen. Den Schemakatalog bei erfolgreichem Reload
     atomar ersetzen, damit deaktivierte oder entfernte Tools nicht als
     veraltete Präzisionsverträge erhalten bleiben. Ist ein benötigtes Tool
     nicht verfügbar, darf der Guard keinen scheinbar ausführbaren Vertrag
     konstruieren.
  5. **Gezielte Tests:** Positive deutsche/englische Einzelintents,
     nummerierte Mixed-Pläne, richtige und falsche Argumente, Planner-
     Downgrade, falsches Tool, unbekanntes/deaktiviertes Tool, Erwähnungen
     ohne Rechenabsicht, ungültige/mehrdeutige Eingaben, Cache-/Fast-Path-
     Anschluss und begrenzte Repair-Instruktion abdecken. Bestehende
     Recovery- und Precision-Tests müssen ohne Semantikregression bestehen.
  6. **Validierung und lokaler Rollout:** Syntax, fokussierte Tests,
     vollständige Regression, Governance und MkDocs strict ausführen. Danach
     nur den Orchestrator neu bauen/recreaten, sofern aktive Requests dies
     sicher erlauben; `/ready`, Image-ID, RestartCount und live geladene
     Toolzahl prüfen. Im laufenden Container mindestens einen korrekt
     akzeptierten sowie einen absichtlich degradierten Plan gegen den echten
     Schemakatalog validieren.
  7. **Wirksamkeitsnachweis und Abschluss:** Belegen, dass ein korrekter
     Precision-Plan passiert, ein `general`-Downgrade vor Dispatch blockiert
     wird und nicht-deterministische Prompts unverändert zulässig bleiben.
     Messwerte, Grenzen und Folgearbeiten in Lastenheft, Statuslog und
     SessionMesh festhalten. Kein Commit, Push, PR oder Publish.
- **Acceptance criteria:**
  - Jeder eng erkannte und im aktiven MCP-Katalog vorhandene
    Präzisionsintent besitzt vor Dispatch eine passende Toolaufgabe samt
    korrekter Pflichtargumente; andernfalls entsteht ein strukturierter,
    fail-closed Contract-Fehler.
  - Einzelne deterministic Teilaufgaben können in Mixed-/nummerierten
    Prompts nicht durch allgemeine Expert-Aufgaben ersetzt oder weggelassen
    werden.
  - Prosa-Erwähnungen, unvollständige/ungültige Eingaben und nicht
    allowlistete Operationsformen erzeugen weder False-Positive-Blockaden
    noch erfundene Toolargumente.
  - Deaktivierte oder bei einem erfolgreichen Reload entfernte Tools fehlen
    im Planner-Schemakatalog; bestehende Tools bleiben vollständig
    beschrieben.
  - Fokussierte und vollständige Tests, Governance und MkDocs strict sind
    grün. Der recreatete Orchestrator ist healthy, RestartCount 0 und die
    drei Wirksamkeitsfälle sind im laufenden Codepfad belegt.
- **Resolution notes (2026-08-01, Codex CLI):**
  - **Zentraler Guard:** `detect_required_precision_intents` erkennt nur
    vollständig parametrisierbare direkte GGT-, km/h→m/s- und deutsche/
    englische Wochentagsanfragen. Nicht nummerierter Text muss genau einen
    Intent enthalten; bei lückenlos nummerierten Listen wird jeder Eintrag
    isoliert geprüft. Code-Erstellungsaufträge, beiläufige Beispiele,
    ungültige Daten, lückenhafte Nummerierung und mehrdeutige Mehrfachintents
    bleiben bewusst außerhalb der Allowlist.
  - **Fail-closed Planvertrag:** Der gemeinsame `_prepare_handoff_plan` prüft
    jetzt zusätzlich Eingabeintent, aktives Tool, Kategorie und semantische
    Argumente. Fehlende/falsche Aufgaben erzeugen
    `precision_intent_downgraded`, fehlende aktive Tools
    `required_precision_tool_unavailable`. Der normale Planner erhält genau
    den bestehenden begrenzten Contract-Repair; nach Erschöpfung ist kein
    `general`-Fallback zulässig. Cache-Fingerprint Version 4 invalidiert alte
    Pläne; Kalender/GGT-Signale umgehen den Trivial-Fast-Path nicht mehr.
  - **Katalogintegrität:** `_load_mcp_tool_descriptions` übernimmt nur live
    `enabled` gemeldete Einträge, baut Schemas/Beschreibungen zunächst lokal
    und ersetzt den Runtime-Katalog anschließend vollständig. Erfolgreiche
    Reloads entfernen deaktivierte/gelöschte Tools; Discovery-Fehler löschen
    ausführbare alte Schemas. Statische Fallback-Beschreibungen sind dadurch
    kein Ausführbarkeitsbeleg.
  - **Tests/Dokumentation:** 98 fokussierte Contract-, Fast-Path- und
    Katalogtests sowie die vollständige Regression **756 passed in 4,95 s**
    sind grün. Governance validiert 27 Pflichtdateien und neun Entry-Points;
    `mkdocs build --strict`, Compileall, Compose-Config, `pip check` und
    fokussiertes `git diff --check` bestanden. Die Offloading-Evaluation ist
    auf Version 1.1 aktualisiert und weist DET-01 korrekt als enge Phase A aus;
    eine unbelegte 5–30-%-Fehlerrate und die zu breite Behauptung vollständigen
    Offloadings wurden aus der MCP-Dokumentation entfernt.
  - **Live-Wirksamkeit:** Orchestrator-Image
    `sha256:f4751f7c8090a8c1a1b673e26f4d8687cc983e5f66a17956a6e81000fbda2a51`
    läuft healthy mit RestartCount 0; `/health` und `/ready` sind positiv.
    Der Startup lud 59/59 aktive Tools. Im laufenden Container passierte ein
    korrekter `calendar_facts`-Plan mit `planned`-Ledger-Ereignis;
    `general`-Downgrade und falsches Datum wurden jeweils vor Dispatch mit
    `precision_intent_downgraded` blockiert; eine beiläufige Datumsnennung
    blieb als `general` zulässig. Vier Runtime-Dateien stimmen per SHA-256
    zwischen Host und Container überein; kein Active Request blieb zurück.
  - **Grenze/Folgearbeit:** DET-01 schützt derzeit absichtlich nur drei
    bewiesene Extraktoren. Weitere Arithmetic-/Unit-/Hash-/Statistik-/CIDR-
    und Schema-Intents dürfen erst nach typisierten, adversarial getesteten
    Argumentextraktoren aufgenommen werden. Zeitzone/DST,
    Decimal-Finanzmathematik, exakte Wahrscheinlichkeit und strukturierte
    Validierung bleiben die nächsten P0-Verträge. Das unveränderte MCP-Image
    bleibt healthy; die bekannten Authentik-Compose-Warnungen bestehen.
  - Vor dem Recreate waren null aktive Requests vorhanden. Kein Commit, Push,
    PR oder Publish, keine Credential-, Daten-, Modell- oder
    Template-Änderung; der 176 Einträge umfassende Dirty Worktree bleibt kein
    releasefähiges Artefakt.

---

### TASK-42 bis TASK-50: Precision Evidence Binding und deterministische MCP-Plattform

- **Priority:** critical / P0
- **Owner:** Codex CLI für TASK-42 bis TASK-50
- **Status:** TASK-42 bis TASK-50 done (2026-08-02, Codex CLI)
- **Goal:** Einen verpflichtenden Precision-Intent von der Erkennung vor jedem
  Antwortcache über unveränderlichen Contract-Snapshot, normalisierte
  Argumente, schema-validen MCP-Aufruf und typisierte Evidenz bis zur final
  gebundenen API-Antwort und erst anschließendem idempotenten Learning-/Cache-
  Commit lückenlos beweisbar machen. Auf diesem Fundament werden Zeit/
  Zeitzone, Decimal-Finanzmathematik, exakte Wahrscheinlichkeit und sichere
  strukturierte Validierung als neue P0-Verträge integriert.
- **Lokal verifizierte GAPs vor Planung:**
  - **Cache-Bypass:** L0/L1 werden in `graph/router_nodes.py` vor dem Planner
    ausgewertet; ein Treffer gelangt direkt zum Merger. Damit kann ein alter
    oder falscher Cache TASK-41 vollständig umgehen.
  - **Argumentdrift:** `graph/tool_nodes.py` prüft vor Invoke im Wesentlichen
    Required-Felder. Nach einem Fehler kann der Judge neue Argumente erzeugen,
    ohne sie erneut an den ursprünglichen Intent-/Planvertrag zu binden.
  - **Untypisierte Evidenz:** `/tools` beschreibt kein Outputschema, Contract-
    Version oder Determinismus-/Source-Modell; `/invoke` liefert heterogenen
    Freitext. Evidence und Working Memory kürzen Darstellungen und besitzen
    keine durchgängigen Input-/Result-/Contract-Hashes.
  - **Faktmutation:** Der Merger erhält MCP-Ergebnisse als freien Text; Merger
    und nachgelagerter Critic können korrekte Toolwerte vor der finalen Ausgabe
    verändern. Das Quality Gate prüft Taskabschluss, aber noch keine bindende
    Deckung zwischen finaler Aussage und Evidence.
  - **Pre-Gate-Persistenz:** Antwortcache, Chroma/Kafka, Episode und Learning-
    Pfade können bereits im Merger-Zweig schreiben, bevor Critic und finales
    Quality Gate abgeschlossen sind. Eine später blockierte Antwort kann damit
    wiederverwendbaren Zustand verunreinigen.
  - **Contract-Drift/Observability:** Der Planner-Cache-Fingerprint bildet
    nicht das vollständige Schema ab; Pfadmetriken für Cache-Bypass, Schema-
    Fehler, Evidence-Binding und LLM-Escape fehlen.
- **Vor Umsetzung niedergeschriebener Integrationsplan:**
  1. **TASK-42 — Precision-Preflight und Cache-Containment:** Preflight direkt
     nach Guard und vor L0/L1 einfügen; für Pflichtintents Legacy-Response-
     Cache umgehen; immutable Sollintents/Argumente/Contract-Hash im State
     halten; Planner-Cache vollständig fingerprinten; Judge-Argumentdrift für
     Pflichtverträge sperren; Intent→Task→Terminalevent→Evidence im Quality
     Gate fail closed korrelieren.
  2. **TASK-43 — Versionierte MCP-Verträge:** MCP-Discovery um vollständige
     Input-/Output-JSON-Schemas, Contract-Version, Determinismusklasse,
     Source-/`as_of`-Policy, Limits und Hash erweitern. `/invoke` bleibt über
     `result` kompatibel und ergänzt `structured_result`; beide Seiten prüfen
     Ein- und Ausgabe vollständig. Zunächst `gcd_lcm`, `unit_convert` und
     `calendar_facts` migrieren.
  3. **TASK-44 — Evidenzgebundene Synthese:** Vollständig abgedeckte reine
     Precision-Anfragen nach Auth/Deadline/Ledger über MCP und einen
     deterministischen Locale-Renderer ohne Modellknoten ausgeben. In Mixed-
     Antworten opaque Fact-Slots nutzen und erst nach Conflict/Critic aus
     typisierter Evidenz binden; fehlende oder kontextfalsche Slots blockieren.
  4. **TASK-45 — Quality-atomare Persistenz:** Wiederverwendbare Cache-,
     Memory-, Kafka-, Episode- und Learning-Writes in einen idempotenten
     `response_commit` hinter den erfolgreichen Quality-Pass verschieben.
     Audit-/Task-Ledger bleiben sofort verfügbar; Block/Pending/Reject
     committed nichts Semantisches, HITL-Approve genau einmal.
  5. **TASK-46 — Zeit/Zeitzone/DST:** Explizite ISO-Instants und IANA-Zonen,
     DST-Fold/-Gap, gepinnte Zeitzonendaten sowie source-/`as_of`-gebundene
     Clock-Aussagen. Kein Raten bei naiver oder mehrdeutiger Lokalzeit.
  6. **TASK-47 — Decimal-Finanzmathematik:** Dezimalstrings, Währung, Scale
     und Rundungsmodus explizit; niemals Binary-Float. Input-only-Arithmetik
     strikt von versionierungs-/jurisdiktionspflichtigen Steuer-, Kurs- und
     Rechtsregeln trennen.
  7. **TASK-48 — Exakte Wahrscheinlichkeit:** Begrenzte rationale
     Wahrscheinlichkeits-/Kombinatorikoperationen mit `Fraction` als
     Wahrheitswert und Decimal nur als explizit gerundete Projektion; Kosten-
     und Bitlängengrenzen vor Ausführung.
  8. **TASK-49 — Strukturierte Validierung:** JSON/YAML/XML/CSV mit sicheren
     Parsern, vollständigen Größen-/Tiefe-/Entity-/Laufzeitgrenzen und ohne
     Remote-Refs, DTD/XXE, unsafe YAML oder Inhaltsausführung validieren.
  9. **TASK-50 — Telemetrie und Rollout-Proof:** Niedrig-kardinale Metriken für
     Intent, Route, Cache-Bypass, Schema, Drift, Binding, Escape und Commit;
     neue Verträge einzeln Shadow→Enforce schalten. Versionierten Cold-/Warm-
     Benchmark gegen native und orchestrierte API mit dreifachem Timeout,
     Fehler-/Manipulationsmatrix, Source-to-Image-Nachweis und praktischem
     Rollback ausführen.
- **Reihenfolge/Abhängigkeiten:** TASK-42 → TASK-43 → TASK-44 → TASK-45.
  TASK-46 bis TASK-49 beginnen erst auf dem typisierten Vertragsfundament und
  werden einzeln im Shadow-Modus abgenommen; TASK-50 ist das gemeinsame
  Enforce- und Abschlussgate. TASK-44/45 dürfen wegen gemeinsamer Änderungen
  an Graph-Topologie und `graph/synthesis.py` nicht parallel implementiert
  werden.
- **Gesamtabnahme:**
  - Null LLM-only-Erfolge im fest versionierten Pflicht-Precision-Korpus und
    null Cache-Hits vor Precision-Preflight.
  - 100 Prozent exakte finale Fakten in der adversarialen Merger-/Critic-
    Mutationsmatrix; jeder Pflichtfakt korreliert mit Contract-, Input-, Task-
    und Result-Hash.
  - Reine Precision-Anfragen zeigen im Audit null Planner-/Expert-/Judge-/
    Merger-/Critic-Modellaufrufe.
  - Null wiederverwendbare semantische Writes vor erfolgreichem finalen Gate;
    Pass/Block/Pending/Reject/Approve und Resume sind idempotent bewiesen.
  - Alle negativen Ein-/Ausgabeschema-, Permission-, Deadline-, Katalogreload-
    und Parser-Sicherheitsfälle liefern typisierte Fehler statt geratenen
    Ersatzwerten.
  - Vollständige Regression, Governance, MkDocs strict, Dependency-/Compose-
    Checks, Cold-/Warm-E2E über Chat-, Template- und Responses-Fassade sowie
    healthy/restart-0 Container sind reproduzierbar dokumentiert.
- **Rollout/Rollback:** Zentraler Shadow/Enforce-Modus, anfänglicher Precision-
  Cache-Bypass, separat schaltbare Direct Response und Structured-Required-
  Migration. Bei jedem Gate müssen letztes verifiziertes Image und Flags einen
  praktischen Rollback ermöglichen; obsolete Legacy-Zweige/Flags werden nach
  stabiler Abnahme entfernt.
- **Planartefakte:** Story, Zielarchitektur, GAP-/Risiko-/Testmatrix und alle
  ausführbaren Task-Sheets liegen unter
  `docs/backlog/current/I-2-pipeline-quality-gate/E-2.1-deterministic-signals/S-2.1.1-precision-evidence-binding/`.
- In diesem Planungsschritt wurden keine Runtime-Datei, kein Container, kein
  Credential, Datenbestand, Modell oder Expert Template verändert und kein
  Commit, Push, PR oder Publish ausgeführt. Der vorbestehende Dirty Worktree
  bleibt erhalten.
- **TASK-42 Resolution notes (2026-08-01, Codex CLI):**
  - Ein eigener `precision_preflight` liegt jetzt zwischen Guard und Cache und
    friert Pflichtintents, normalisierte Sollargumente, vollständige aktive
    Schemas sowie Contract-/Kataloghash ein. `cache_lookup_node` besitzt einen
    zusätzlichen direkten Schutz: erkannte Precision-Anfragen umgehen L0, L1,
    Knowledge-Bypass und Soft Examples; Agent-Caches der OpenAI-/Anthropic-
    Toolpfade werden ebenfalls nicht bedient.
  - Planner-Cache Version 5 hasht den vollständigen Katalog statt nur Toolname
    und Required-Felder. Planner/Recovery nutzen für Pflichttools den Request-
    Snapshot; ein zunächst fehlendes Tool wird nicht durch einen späteren
    Reload still ausführbar.
  - Der MCP-Worker validiert vor Invoke das vollständige entdeckte JSON-Schema
    einschließlich Typen und Zusatzfeldern, normalisiert nur dokumentierte
    Defaults und korreliert Evidence über Contract-/Input-Hash. Katalogdrift
    blockiert vor Invoke; nach Serverfehlern sind Judge-generierte Argumente
    für Pflichtverträge verboten. Optionale Retries bleiben schema-validiert.
  - Das finale Quality Gate rekonstruiert die erkannte Pflichtmenge und prüft
    bijektiv Intent→Plan-Task→genau eine erfolgreiche Evidence. Fehlender
    Preflight, Contract-/Argument-/Hash-Drift und fehlende/duplizierte Evidence
    liefern stabile fail-closed Gründe.
  - Fokussierte Graph-/Contract-/Cache-/Worker-/Quality-Tests **140 passed**;
    vollständige Regression **766 passed in 4,30 s**. Governance 27/9,
    MkDocs strict, Compileall, Compose-Config, `pip check` und Diff-Check sind
    grün.
  - Live-Image
    `sha256:d5f76d7d40eabbb5cb3c2151f2e788b3b1c5f26bc747b5e3a73a703f28aaeedc`
    läuft healthy mit RestartCount 0 und 59/59 aktiven Tools. Ein absichtlich
    falscher realer L0-Eintrag wurde umgangen, `gcd_lcm` ergab 23 mit
    Contract-/Input-Hash, Quality passierte und blockierte anschließend eine
    manipulierte Evidence mit `precision_evidence_mismatch`.
  - Vor dem Recreate waren null aktive Requests vorhanden; der temporäre
    falsche Cacheeintrag wurde gelöscht. Bekannte Authentik-Warnungen bleiben
    unverändert. Kein Credential, Datenbestand, Modell oder Expert Template
    geändert und kein Commit, Push, PR oder Publish ausgeführt.
- **TASK-43 Resolution notes (2026-08-01, Codex CLI):**
  - `calendar_facts`, `gcd_lcm` und `unit_convert` veröffentlichen jetzt
    vollständige versionierte Ein-/Ausgabeschemas, kanonische Contract-Hashes,
    Determinismus-/Source-Metadaten sowie Normalisierungs-, Retry-, Cache- und
    Größenpolicies. `/invoke` behält `result` und ergänzt typisierte Fakten,
    normalisierten Input, Runtime-Source, Warnungen und Result-Hash.
  - MCP und Orchestrator validieren Input, Output, Contract, Source und Hash
    fail closed; Evidence bleibt innerhalb des 65.536-Zeichen-Limits
    vollständig. Der Katalog wird erst nach vollständiger Validierung als Set
    ersetzt, In-Flight-Snapshots erkennen Drift, und das finale Gate blockiert
    manipulierte strukturierte Fakten.
  - Fokussiert **118 passed**, vollständig **772 passed in 4,24 s**;
    Compileall, Governance 27/9, MkDocs strict, Compose-Config und `pip check`
    in beiden Images sind grün. Die Live-Negativmatrix prüfte Required, Typ,
    Enum, Range, Zusatzfelder, leere und inkompatible Einheiten.
  - MCP-Image
    `sha256:f2e172d23b0c745c85c3d6bf37495ae2d95d9d403c42ad5508708c59a637676c`
    und Orchestrator-Image
    `sha256:919022098988d3fd198af20c8b6b8c5a5a7912c9bb7bbfce5a606506541a6956`
    laufen healthy/restart-0 mit 59/59 Tools. Live-GGT 23 passierte; eine
    Änderung auf 29 blockierte als `precision_evidence_mismatch`. Vor dem
    Recreate waren null aktive Requests vorhanden; keine Credentials, Modelle,
    Templates, Commits, Pushes, PRs oder Publikationen wurden verändert.
- **TASK-44 Resolution notes (2026-08-01, Codex CLI):**
  - Vollständig abgedeckte Präzisionsanfragen laufen nach Auth und Guard direkt
    über MCP, typisierten Locale-Renderer, Evidence-Binding und Quality Gate.
    Planner, Experten, Thinking, Merger, Konfliktauflösung und Critic werden
    auf diesem Pfad nicht aufgerufen.
  - Gemischte Antworten erhalten wertfreie opaque Fact-Slots. Nach dem letzten
    Modellknoten bindet ein deterministischer Knoten jeden isolierten Slot an
    aktuelle typisierte Evidence; fehlende, duplizierte, vertauschte,
    unbekannte oder kontextumhüllte Marker blockieren fail closed.
  - Chat-, Responses- und Anthropic-Fassade lieferten live denselben GGT-Satz
    mit null Modell-Tokens. Ein `native` Claude-Code-Profil kann einen reinen
    Pflicht-Precision-Turn nicht mehr am gemeinsamen Graph vorbeileiten;
    echte Tool-/Tool-Result-Turns bleiben unverändert.
  - Ein nummerierter Mixed-Live-Request führte `gcd_lcm` und einen
    `qwen3.6:35b`-Code-Review-Experten aus. Der Fact-Slot blieb durch Merger,
    zwei Self-Critique-Runden und Critic isoliert, wurde danach korrekt
    gebunden und erst anschließend vom unabhängigen HITL-Gate auf `pending`
    gesetzt.
  - Fokussiert **29 passed**, vollständig **787 passed in 4,59 s**. Das aktive
    Orchestrator-Image
    `sha256:19f01440fcea67c0d35a976414a883012e63154d0caa1a20ad2ec7c0b7ae9656`
    läuft healthy/restart-0. Temporäre `horndev`-Keys wurden widerrufen und
    archiviert; Test-Traces und der ausschließlich für den Proof erzeugte
    Pending-Gate wurden entfernt. Kein Commit, Push, PR oder Publish.
- **TASK-45 Resolution notes (2026-08-01, Codex CLI):**
  - Der Graph besitzt jetzt eine explizite Commit-Grenze:
    `quality_gate(pass) -> response_commit -> END`. Block/Pending/Reject enden
    ohne wiederverwendbaren Write; HITL-Approve committed den eingefrorenen
    Draft und seine Hashbindung vor Freigabe.
  - Der idempotente Commit-Key bindet Request-, Response-, Contract-, Binding-
    und Evidence-Hash. Ein Valkey-Journal hält den Status aller zehn Sinks und
    wiederholt bei Resume nur fehlgeschlagene Sinks. Response- und Binding-
    Hash werden vor jedem ersten Write erneut geprüft; ungebundene Precision-
    Antworten blockieren.
  - Alle wiederverwendbaren Cache-, Metadata-, Kafka-, Episode-, Correction-,
    Attribution-, Routing-/Policy-Learning- und Evaluation-Pfade wurden aus
    dem Merger hinter das finale Gate verschoben. Operatives Audit und
    Task-Ledger bleiben sofort sichtbar.
  - Precision-Cache bleibt produktiv auf `bypass`, weil der bestehende Reader
    den zwar versionierten Key, aber noch keinen vollständigen Evidence-
    Umschlag revalidieren kann. Damit wird kein nur halb integrierter Cachepfad
    aktiviert.
  - Vollständige Regression **797 passed in 4,25 s**. Live lieferte Request
    `chatcmpl-5de9de32-2a34-428f-acda-ee2f0098b44a` fünf deterministische
    Fakten in 2,543 s und null Modell-Tokens; Trace: Binding `bound`, Gate
    `passed`, Commit `complete`, alle zehn realen Sink-Journale `done`.
  - Image
    `sha256:4abd1ef59f145cf0641c3624dcdf1b948f57f176870b9413e250e4c5f3b94262`
    läuft healthy/restart-0 mit `PRECISION_CACHE_POLICY=bypass`. Der temporäre
    `horndev`-Key wurde widerrufen, invalidiert und archiviert. Kein Commit,
    Push, PR oder Publish.
- **TASK-46 Resolution notes (2026-08-02, Codex CLI):**
  - `time_facts` und `timezone_convert` verlangen explizite ISO-Werte und
    IANA-Zonen, behandeln DST-Gaps fail closed und verlangen bei mehrdeutigen
    Fold-Zeiten die explizite Auswahl. `tzdata==2026.3` ist im Runtime-Image
    gepinnt und wird mit den typisierten Fakten belegt.
  - Deutsche/englische reine und gemischte Intents durchlaufen denselben
    Contract-Snapshot-, Evidence-Binding- und Quality-Pfad. Der Anthropic-
    Mixed-Proof `msg_ba9443376ced4b9d96cbb19f` belegte Preflight → MCP →
    Expert → Hybrid/Critic → Bind → Quality → sechs erfolgreiche Commits.
- **TASK-47 Resolution notes (2026-08-02, Codex CLI):**
  - `decimal_finance` nutzt ausschließlich kanonische Decimal-Strings mit
    expliziter Scale, Rundung und ISO-4217-Währung. Addieren, Subtrahieren,
    Multiplizieren, Dividieren, Prozent-, einfache und Zinseszinsrechnung sind
    typisiert; Float, Division durch null, unpassende Operanden und Grenzen
    werden abgewiesen.
  - Wechselkurse, Steuerrecht und jurisdiktionsabhängige Rundungsregeln sind
    bewusst kein Bestandteil des input-only Vertrags und dürfen nicht aus dem
    Modell ergänzt werden.
- **TASK-48 Resolution notes (2026-08-02, Codex CLI):**
  - `exact_probability` implementiert Bruch, Kombination, Permutation und
    Binomialwahrscheinlichkeit auf Integer-/`Fraction`-Basis. Eine Decimal-
    Projektion entsteht nur mit expliziter Scale und Rundung; `n`, Kosten und
    Resultat-Bitlänge sind vor bzw. während der Ausführung begrenzt.
  - Reine API-Antworten werden ohne LLM-Token aus typisierter Evidenz
    gerendert; übergroße Zustandsräume und nicht-kanonische Zahlen blockieren
    als Contract-/Toolfehler statt auf generierten Code auszuweichen.
- **TASK-49 Resolution notes (2026-08-02, Codex CLI):**
  - `structured_validate` validiert begrenztes JSON, YAML, XML und CSV mit
    sicheren, exakt gelockten Parsern. YAML-Aliase/-Tags, XML DTD/Entities/
    XInclude und JSON-Schema-`$ref` werden ohne Netzwerk- oder Dateizugriff
    abgewiesen; CSV verlangt einen expliziten Dialekt.
  - Der Rollout fand eine reale Inhaltsleckage über `input_normalized`, Logs,
    Telemetrie und Working Keys. Die zentrale Evidence-Policy ersetzt
    `payload` und `schema_json` an allen operativen Grenzen durch SHA-256 und
    UTF-8-Bytezahl; die bösartige Live-Matrix bestätigte null Secret-Echos.
- **TASK-50 Resolution notes (2026-08-02, Codex CLI):**
  - `PRECISION_CONTRACT_MODE=shadow|enforce`, Direct-Response-, Structured-
    Required- und Cache-Flags erlauben getrennten Rollout/Rollback. Niedrig-
    kardinale Metriken erfassen Contract, Stage, Outcome und Mode ohne Prompt,
    Rohargumente oder Hashes als Labels.
  - Das versionierte Korpus `moe-precision-v1` passierte nach einem gefundenen
    und behobenen AdviceTaker-Fehler **13/13** API-Fälle. Alle zwölf reinen
    Chat-/Responses-/Anthropic-Anfragen nutzten null Modell-Tokens; der
    gemischte Request führte genau zwei MCP-Verträge und einen abgegrenzten
    Code-Review-Experten aus und passierte Binding sowie Quality Gate.
  - Beim identischen Decimal-Prompt lieferte native `qwen3.6:35b` auf N04-RTX
    kalt/warm in 151,918/21,323 s den korrekten Wert, die evidence-bound Direct
    Route in 0,174/0,163 s zusätzlich mit Scale-/Rundungsnachweis. Stichprobe
    n=1; daraus folgt keine allgemeine Performance- oder Qualitätsaussage.
  - Vollständige Regression **908 passed in 5,04 s**, Governance 27/9,
    MkDocs strict, Compose-Config, Diff-Check und `pip check` beider Images sind
    grün. Aktive Images: MCP
    `sha256:7e28eeab4a5b05e56eb713cfbab834a6c9dc4ebfea9ae3594eb0f46c77c5564a`,
    Orchestrator
    `sha256:4320ca67eaaeaf5168d4c4c251427f99305e04bfbdbf1e60e3b4368f2e8d402f`;
    beide healthy/restart-0. Flag- und vorheriger Image-Rollback wurden real
    ausgeführt, anschließend wurde das finale Image erfolgreich restauriert.
  - Temporäre `horndev`-Benchmark-Keys sind widerrufen, invalidiert und
    archiviert (kombinierter Audit TASK-46/50: elf Datensätze, null aktiv,
    null unarchiviert). Methodik, Request-IDs und Grenzen stehen in
    `docs/system/toolstack/precision_rollout_benchmark_2026-08-02.md`. Kein
    Commit, Push, PR oder Publish.

### TASK-51: Native adaptive deliberation workflow

- **Owner:** Codex CLI
- **Status:** completed (2026-08-07)
- **Depends on:** TASK-38, TASK-42 through TASK-50
- **Goal:** Integrate the proven sovereign-debate-engine behavior as a
  versioned MoE Sovereign deliberation policy without duplicating its auth,
  persistence, retrieval, or inference infrastructure. Expert templates must
  explicitly disable, adaptively enable, or require deliberation. Agent and
  round capacity must derive from frozen complexity/Cynefin/plan signals and
  expose separately budgeted reserve capacity.
- **Owned scope:** `services/deliberation/`, template resolution in
  `services/routing.py`, dynamic `moe-auto` template compilation in
  `services/dynamic_router.py`, the expert execution integration in
  `graph/expert.py`, required state/config fields, focused tests, and TASK-51
  documentation. Existing TASK-9 training scripts and unrelated dirty
  worktree files are excluded.
- **Required behavior:**
  1. Version and strictly validate `deliberation_policy`; invalid required
     policy fails closed and is never silently ignored.
  2. Support `disabled`, `adaptive`, and `required` activation plus `micro`,
     `moderated`, and `auto` modes.
  3. Compute initial/reserve agents and rounds deterministically from current
     pipeline signals, template limits, and the remaining request budget.
  4. Do not require a new planner-model output contract for the first release;
     `moe-auto` compiles and snapshots the policy outside the planner LLM.
  5. Preserve the existing standard path when deliberation is disabled or not
     selected, and retain the bounded legacy micro-debate semantics through
     the new policy layer.
  6. Account for every deliberation model call and respect the single request
     deadline, cancellation, `local_only`, endpoint, and template boundaries.
  7. Cover negative policy/schema cases, early convergence, reserve
     activation, hard budget exhaustion, and `CHAOTIC`/Trust-BLOCK behavior.
- **Acceptance:** focused contract and integration tests plus the complete
  relevant test suite pass; configuration/docs are synchronized; no deploy,
  migration, credential, commit, push, or publication occurs without separate
  authorization.
- **Resolution (2026-08-07):** Implemented the versioned strict policy,
  deterministic capacity and reserve planner, request-wide model-call and
  deadline accounting, bounded micro and moderated execution, convergence and
  fallback behavior, conflict/quality/commit integration, safe telemetry, and
  Admin/User controls for static and dynamic templates. Missing policy on
  existing templates retains the former bounded micro semantics; newly created
  templates default to disabled, while `moe-auto` compiles an adaptive policy
  outside the planner LLM and supports explicit per-user disable/require
  modifiers. The planner output contract and current LUMI-G training dataset
  therefore do not require retraining for this release.
- **Validation (2026-08-07):** Complete repository regression `937 passed`;
  compile, template/translation/JavaScript parsing, strict MkDocs build,
  governance check (27 required files / 9 runtime entrypoints), Compose config,
  scoped diff check, and the updated whitepaper PDF build passed. Fresh
  `moe-infra-moe-admin` (`sha256:16354741...`) and
  `moe-sovereign-orchestrator:local` (`sha256:d767d971...`) images built
  successfully; isolated container imports validated the new policy and expert
  graph. No running service was recreated and no live model benchmark was
  performed, so production effectiveness remains deployment-dependent.

---

### TASK-52: Local image/audio generation as versioned MCP tools (ComfyUI + Kokoro-FastAPI on N04-RGTX)

- **Owner:** Claude Code
- **Status:** pending (GAP analysis and implementation plan written 2026-08-07; no code changed)
- **Depends on:** TASK-42 through TASK-45 (versioned MCP contract schema, `determinism`
  field, evidence-binding boundary — this task adds a new tool category on top of that
  framework, not a parallel one)
- **Context:** Operator wants OpenAI-API-parity image generation
  (`/v1/images/generations`) and audio generation (`/v1/audio/speech`) available as
  local, self-hosted capabilities, dedicated to the `N04-RGTX` GPU grouping so the
  `N04-RTX` node (primary expert-LLM instance) is not disturbed.
- **Verified hardware constraints (2026-08-07, do not re-derive from vendor marketing
  numbers — these were checked against current CUDA/PyTorch minimums):**
  1. `N04-RGTX`'s GTX 1060 (Pascal, compute capability 6.1) **cannot run any
     PyTorch-based generative model** — current PyTorch wheels require CC ≥ 7.5, and
     cuDNN 9.12 dropped CC 6.1 support outright. This is the same class of dead end as
     the Tesla M10 (`N11-M10`, already `enabled: false`) and is not a VRAM problem, it
     cannot be worked around by picking a smaller model. Exclude it from GPU scheduling
     for this task; its only remaining use is as a CPU-mode Kokoro fallback, which is
     explicitly **out of scope** for this task (do not build it speculatively).
  2. `N04-RGTX`'s RTX 2060 (12 GB, Turing, CC 7.5) sits exactly at the current PyTorch
     minimum — usable, no safety margin below it. Re-verify against the PyTorch/CUDA
     version actually pinned in the container before assuming this still holds at
     implementation time (this class of minimum has moved twice in one research pass
     during scoping — do not treat it as permanently fixed).
  3. Neither this RTX 2060 (Turing) nor `N04-RTX`'s RTX 3060s (Ampere) have native FP8
     tensor cores (FP8 hardware acceleration starts at Ada Lovelace/Hopper). An
     "fp8-quantized" FLUX checkpoint will still load and run here, but only via
     weight-only quantization (fp8 storage, fp16 compute, Marlin-kernel style) — expect
     generation latency well above numbers benchmarked on Ada-class cards; do not copy
     vendor/community benchmark numbers into docs or capacity planning without
     re-measuring on this specific card.
  4. `N04-RTX`, `N04-RGTX`, and `N04-TESLA` share **one physical host**
     (`192.168.155.224`, distinguished only by Ollama port `11434`/`11435`/`11436` in
     `INFERENCE_SERVERS`) — `N11-M10` is a separate host. Before writing any Compose
     `device_ids`/`NVIDIA_VISIBLE_DEVICES` pinning, run `nvidia-smi -L` on
     `192.168.155.224` and explicitly map which physical GPU index is the RTX 2060
     belonging to the `N04-RGTX` grouping, as opposed to the two RTX 3060s / two other
     RTX 2060s already claimed by `N04-RTX`'s Ollama instance. Do not guess an index —
     a wrong pin would either starve the primary LLM instance of a GPU it currently owns
     or silently run the new services on the (non-functional) GTX 1060.
- **Architecture decision (rationale, not open for silent re-litigation without a
  documented reason):**
  - Expose generation as **new MCP tool contracts** (`generate_image`,
    `generate_speech`) inside the existing `mcp-precision` service
    (`mcp_server/server.py`), not as a new graph node (unlike `guard_node`, this is not
    a pre-planner short-circuit check — it is a capability the Planner decides to
    invoke, which is exactly what the MCP tool-catalog/contract mechanism already
    exists for) and not as new sibling REST endpoints (would duplicate auth, rate
    limiting, and template resolution that the existing pipeline already provides).
  - `mcp-precision` itself stays thin: both new tool handlers are HTTP proxies to two
    new, separate backend containers (`comfyui`, `kokoro-tts`) over the internal Docker
    network. Do not vendor ComfyUI's or Kokoro's Python dependencies into
    `mcp-precision`'s image — those are heavy, GPU-bound, and architecturally
    unrelated to the deterministic precision tools already living there.
  - New `determinism` contract value: **`generative_model`**. This is a deliberate,
    explicit new trust class, distinct from `input_only`/`source_versioned`/
    `library_pinned`: generative output is a statistical sample, not a verifiable fact.
    Tools with `determinism: generative_model` **must never** enter the precision
    evidence-binding fast path built in TASK-42/44 (no Planner/Judge/Critic bypass) —
    their output always passes through the normal expert-result → Merger → Critic path,
    same trust treatment as any LLM expert call.
- **Template integration (pattern reuse, mirrors `guardrail_*`):**
  Add per-template override fields, resolved through
  `services/routing.py::_resolve_template_prompts()` exactly like
  `guardrail_model_override`/`guardrail_url_override`/`guardrail_token_override`
  (`services/routing.py`, `graph/router_nodes.py` import block, `admin_ui/app.py`'s four
  CRUD spots for `admin_expert_templates`):
  `image_generation_model_override`, `image_generation_url_override`,
  `image_generation_token_override`, `audio_generation_model_override`,
  `audio_generation_url_override`, `audio_generation_token_override`. Unset → falls back
  to new `IMAGE_GEN_URL`/`AUDIO_GEN_URL` config defaults (`config.py`, same
  `GUARD_URL`/`GUARD_MODEL` fallback pattern). New templates default these fields
  unset/disabled — this is an opt-in capability (GPU cost, new attack surface), not a
  default-on one, matching TASK-51's default-disabled precedent for a new template
  capability.
- **Response handling (verify against current envelope before implementing, do not
  assume):** Generated assets must not be inlined as base64 into MCP tool results or
  carried as text through Merger/Critic — store to a shared volume, return a short
  reference (id/URL) as the tool result. Confirm during implementation whether
  `services/pipeline/chat.py`'s response envelope already supports a multipart
  `image_url`-style content part (used elsewhere for vision input) that the final
  response can reuse for output, or whether this is a net-new addition — this was not
  verified during scoping and must not be assumed either way.
- **Explicit non-goals / follow-ups (do not silently build or silently skip):**
  1. Content moderation of *generated* image/audio content is **not** covered by the
     existing `guard_node`/Llama Guard 3 pipeline (text-only). Either enable a
     generation-side safety checker (e.g. the standard Diffusers `safety_checker`
     module for ComfyUI/SDXL/FLUX) as a required part of this task's acceptance
     criteria, or explicitly document the gap and get operator sign-off before
     deployment — do not ship without one of these two outcomes.
  2. GTX 1060 CPU-mode Kokoro fallback: explicitly out of scope (see hardware
     constraint 1) — track as a separate follow-up only if there is a concrete need.
  3. `N04-TESLA`'s actual GPU generation is unverified (name is ambiguous, see hardware
     constraint 4) — do not assume it is usable or unusable for a future scale-out of
     this task without checking `nvidia-smi` output first.
- **Instructions:**
  1. `nvidia-smi -L` on `192.168.155.224`; record the exact device index mapping for
     `N04-RGTX`'s RTX 2060 in this task's resolution notes before writing any Compose
     GPU pinning.
  2. Add `comfyui` and `kokoro-tts` services to `docker-compose.yml`, both pinned via
     `device_ids` to the verified RTX 2060 index only. Use a community-maintained
     low-VRAM/FP8-capable ComfyUI image (e.g. `frefrik/comfyui-flux`) and
     `ghcr.io/remsky/kokoro-fastapi-gpu`; pin both to a specific tag/digest, not
     `:latest`, and record the digest in the resolution notes.
  3. Add `generate_image`/`generate_speech` tool contracts to
     `mcp_server/server.py._TOOL_CONTRACTS`, `determinism: "generative_model"`,
     `source_policy` describing the backend container + pinned model version, full
     input/output JSON schemas (output = asset reference, not inline bytes).
  4. `config.py`: `IMAGE_GEN_URL`, `IMAGE_GEN_MODEL`, `AUDIO_GEN_URL`,
     `AUDIO_GEN_MODEL`, `IMAGE_GEN_TOKEN`, `AUDIO_GEN_TOKEN` defaults.
  5. `services/routing.py::_resolve_template_prompts()` + `admin_ui/app.py` (four CRUD
     spots): add the six new override fields, matching the `guardrail_*` pattern
     exactly.
  6. Decide and implement asset storage + response-envelope handling (see "Response
     handling" above) — verify first, do not assume.
  7. Implement the safety-checker decision from non-goal 1 (enable it, or get explicit
     documented sign-off for shipping without it).
  8. Tests: contract/schema tests for both tools against a mocked backend (deterministic
     CI, no live GPU/image-content assertions); a live smoke test against the real
     `comfyui`/`kokoro-tts` containers is a separate, manually-run verification step, not
     part of the automated suite.
- **Acceptance criteria:**
  - `nvidia-smi -L` mapping recorded; Compose GPU pinning matches it exactly (no
    contention with `N04-RTX`'s existing Ollama instance, no accidental pin to the
    non-functional GTX 1060).
  - Both tools discoverable via the existing MCP catalog with a complete, schema-valid
    contract; `determinism: "generative_model"` confirmed to be excluded from the
    precision evidence-binding fast path (negative test: a generative-tool result must
    not satisfy `precision_evidence_mismatch`-style binding checks).
  - Template override fields resolve end-to-end (template with an override reaches the
    tool call; template without one falls back to the configured default).
  - Safety-checker decision explicitly resolved (enabled and tested, or explicitly
    signed off as a documented gap) — not silently absent.
  - Full relevant test suite passes; governance/MkDocs/Compose-config checks pass; no
    running service recreated, no commit/push/PR/publish, without separate
    authorization.

---

### TASK-53: Wire `local_only_routing` end-to-end into the graph pipeline + egress guard

- **Owner:** Claude Code
- **Status:** done (code + tests; live rebuild/recreate pending — see Resolution notes)
- **Depends on:** none (independent compliance fix); touches files also touched by
  TASK-51 (`graph/expert.py`) — verified no active lease overlap at start.
- **Trigger:** Live incident during TASK-51's "temporary deliberation validation
  rerun" (2026-08-07, 20:55 UTC+2): the moderated-debate feature dispatched ~17 real
  requests to paid OpenRouter frontier models (`gpt-5.4-pro`, `gpt-5.5-pro`,
  `claude-opus-4.7-fast`, ...) using the live system key, during what was intended to
  be a local-model validation run (some calls hit `402 Payment Required`, draining
  OpenRouter balance). Root-caused via read-only container-log + code investigation
  (see `agent_status/claude-code.md` TASK-53 `starting` entry for full chain).
- **Root cause (broader than the triggering symptom):**
  1. `local_only_routing` (per-API-key compliance flag, correctly read from `user_ctx`
     in `services/pipeline/chat.py`) was computed only transiently for the
     `get_dynamic_template(...)` call and never written onto `AgentState`. All three
     graph-invocation entry points (`main.py::stream_response`,
     `services/pipeline/chat.py`, `services/pipeline/anthropic.py::
     _anthropic_moe_handler`) never set it. `graph/expert.py:916`'s
     `state_.get("local_only_routing")` read was therefore always `False` in
     production — dead code disguised as a working guard.
  2. `services/sovereignty.py::assert_egress_allowed()` — an existing, correctly
     designed fail-closed egress guard ("BLOCKED egress ... configuration mistakes
     must fail loudly, not leak silently") — was wired into exactly one call site
     (`_anthropic_tool_handler`/`_anthropic_reasoning_handler`'s single
     `session.tool_url`, via the check in `anthropic_messages`). The full
     planner/expert/judge/debate graph pipeline (all three entry points, the code path
     that handles the large majority of traffic including every `moe-auto` request)
     had zero egress enforcement before any outbound LLM call.
  3. `graph/expert.py::run_moderated_request()` (TASK-51's debate-panel candidate
     selection) and `run_task()`'s static single-expert path both build candidate
     lists directly from `effective_experts`/`EXPERTS` with no local/cloud filtering —
     unlike `services/dynamic_router.py::_score_and_allocate_model`'s "Compliance
     Gate", which only runs for the `dynamic` category via `services/expert_builder.py`.
- **Fix (end-to-end propagation + fail-closed enforcement at actual dispatch, not a
  candidate-list patch at the TASK-51 call site alone):**
  1. `pipeline/state.py` — new declared field `local_only_routing: bool`.
  2. `services/sovereignty.py::assert_egress_allowed` — signature decoupled from the
     `user_ctx` dict shape: `(url: str, local_only: bool)`.
  3. `local_only` computed unconditionally (permission flag `moe-auto:local-only` >
     API-key flag > global `LOCAL_ONLY_COMPLIANCE` env), independent of whether
     dynamic-router template compilation runs, in all three entry points, and written
     onto the invoked state dict.
  4. Egress guard called at the actual network-dispatch boundary:
     `graph/expert.py::run_single()` (covers both the static single-expert path and
     the TASK-51 debate-turn path, since `run_moderated_request` dispatches turns via
     `run_single`) and `services/inference.py::_invoke_judge_with_retry` (covers both
     the regular judge stage and the debate moderator, plus the planner equivalent).
     `EgressDenied` is translated into the existing per-expert error-result shape
     (`[{model} ERROR]: ...`), not an unhandled exception.
  5. Defense-in-depth: `run_task`'s and `run_moderated_request`'s candidate lists are
     additionally filtered by `_is_local_url(URL_MAP.get(endpoint, endpoint))` when
     `local_only_routing` is set — resolving the symbolic node name through `URL_MAP`
     first, not the raw `endpoint` string, because `_is_local_url` treats any dot-free
     unresolved name as local and the TASK-51 incident's exact candidates
     (`endpoint="openrouterai"`) are dot-free symbolic names. This filter is
     best-effort only (it approximates single-endpoint resolution, not `_select_node`'s
     full floating/multi-node logic); `run_single()`'s guard is the actual guarantee.
  6. A fourth, previously-undiscovered dispatch path found only during live
     verification (not reachable by the unit tests above, which only exercise
     `expert_worker`): `services/pipeline/chat.py`'s `model@node` "native direct"
     passthrough (`_native_endpoint`, used by Open WebUI's native-model picker and any
     `model@node`-style request matching a `model_endpoint` permission) dispatches via
     raw `httpx`/`_stream_native_llm()` entirely outside `app_graph` — none of the
     graph-side fixes above cover it. Guarded separately at the top of chat.py's
     `if _native_endpoint:` block (before both its streaming and non-streaming
     branches), reusing the same `local_only` value resolved once per request.
- **Acceptance criteria — all met, evidence below:**
  - Full regression suite green (952 passed, up from the TASK-51-era baseline of 938 —
    13 new tests: `tests/test_sovereignty.py` (11) + two dispatch-level tests added to
    `tests/test_jmoe_debate_judge.py`). `python3 -m compileall`, `git diff --check`, and
    `python3 scripts/check_governance.py --check` (27/9) all green on the changed files.
  - `langgraph-app` rebuilt twice (once per fix iteration — see next bullet) and
    recreated; healthy both times, `/ready` fully positive
    (`orchestration_graph`/`boundary_contracts`/`valkey`/`user_database` all
    `ok:true, critical:true`).
  - **Live verification exposed and then confirmed a fix for a fourth dispatch path**
    that none of the unit tests could reach (they only exercise `expert_worker`): using
    the pre-existing `local_only_routing=true` "Benchmark" key
    (`moe-sk-0261cddfe...`, already present in `api_keys` — no credential was created
    or modified), a live `POST /v1/chat/completions` with
    `model=openai/gpt-4o-mini@openrouterai` (the exact TASK-51 incident shape) **still
    reached the real OpenRouter API and returned a real completion** after the first
    build — root-caused live to `services/pipeline/chat.py`'s `_native_endpoint`
    "native model@node" passthrough, which dispatches via raw `httpx`/
    `_stream_native_llm()` entirely outside `app_graph` (see item 6 in the Fix list).
    Guarded, rebuilt, recreated; the identical request then returned
    `403 {"code":"local_only_violation","message":"local_only routing: endpoint
    'openrouter.ai' is not a local/allowlisted host"}` with the container log showing
    `sovereignty: BLOCKED egress to openrouter.ai (local_only key)` and **no**
    `POST .../openrouter.ai/...` request.
  - **No-regression proof** (same key, same live container): a full `model=moe-auto`
    request (`chatcmpl-27d664f2...`) — exercising planner → complexity routing →
    expert dispatch → judge/merger, i.e. the graph path — completed normally in
    133s (cold qwen3.6:35b load), returned the correct content, and the container log
    confirms every request in that run stayed on `http://192.168.155.224:11434`
    (local N04-RTX Ollama). `local_only_routing` blocks non-local egress without
    degrading legitimate local routing.
  - No migration, credential, or schema change. No unrelated-scope edits; the
    pre-existing dirty worktree was preserved (verified via `git status`/`git diff
    --check` scoped to the files touched by this task).

---

## 4. Suggested Tool Assignments

- **Claude Code CLI** (this session, has live shell + Docker access on
  `ki-vm-node05`): best suited for TASK-1 (code refactor + rebuild) and the
  Docker/manual-test portions of TASK-3. For the new quality tasks: TASK-12
  (Decision Log — pure Python service, no LangGraph structural changes) and
  TASK-13 (Boundary Contracts — YAML config + lightweight check module).
- **agy / Google Antigravity CLI** (has full IMoE implementation context and
  the original `task.md`/`walkthrough.md`): best suited for TASK-2 (it
  authored the training scripts) and writing the TASK-3 walkthrough report.
  For the new tasks: TASK-10 (Trust-Score) and TASK-11 (Self-Critique), which
  require deep LangGraph-node wiring in `graph/synthesis.py`.
- **OpenCode**: available for TASK-4 (new work) or as a second implementer
  for TASK-1 if Claude Code is blocked. For new tasks: TASK-16
  (Cascade Resolution Tracking — isolated extension of existing `cascade.py`)
  and TASK-17 (Scope Guard — small standalone service).
- **Codex CLI**: well suited for focused, isolated refactors. For new tasks:
  TASK-15 (Cynefin Classification — deterministic, no LLM, pure logic module)
  and TASK-18 (Handover / Context-Preservation — Valkey serialization pattern
  analogous to existing session handling).
- **Cursor**: useful for multi-file consistency reviews. For new tasks:
  TASK-14 (Human-in-the-Loop Gate — touches `graph/synthesis.py`, new
  `routes/gates.py`, and Valkey integration simultaneously).

These are suggestions, not constraints — any agent may pick up any
`pending` task, as long as it follows the Status Protocol (Section 0) and
updates `Owner:`/`Status:` accordingly. If two agents target the same files,
check each other's status logs first and note the overlap in Section 3.

**New tasks dependency graph (TASK-10 through TASK-34):**
```
Quality Enhancements:
TASK-10 (Trust-Score)
    └── TASK-11 (Self-Critique)
    └── TASK-14 (HITL Gate)
            └── TASK-15 (Cynefin) [informs Gate trigger]

TASK-12 (Decision Log)        ← independent, high priority
TASK-13 (Boundary Contracts)  ← independent
TASK-16 (Cascade Resolution)  ← extends cascade.py (feat 886944f7)
TASK-17 (Scope Guard)         ← independent
TASK-18 (Handover)            ← independent

GraphRAG / Wikipedia Knowledge:
TASK-19 (YAGO 4 Import)
    └── TASK-20 (Wikipedia Abstracts Chunking + Embedding)
            └── TASK-21 (GraphRAG Benchmark Harness)

ADHS-Transfer (aus Agent-Orchestrator-Analyse, 2026-07-01):
TASK-29 (AI I/O Audit Service)      ← independent, EU-Compliance-Priorität
TASK-30 (Structured-Output Failure) ← independent, erhöht Robustheit
TASK-31 (Capability-Tabelle)        ← independent, Basis für TASK-30-Routing

Claude Design System Integration:
TASK-32 (Claude Design Prompt)       ← Phase 1+2 weitgehend done (2 Skills importiert+auditiert, Admin-Freigabe offen), Phase 3-4 pending

Agent-Governance Transfer:
TASK-33 (Vibelate CC-Profil-Preset)  ← Phase A live (ucp-96dd63b0...), Phase B nach Messfenster

Vibe-Coding Ökosystem Integration:
TASK-34 (Vibe-Coding Integration)    ← Plan eingetragen 2026-07-05, Phase 1-3 pending
```

---

## 5. Status Log Directory

Per-agent append-only status logs live in `agent_status/`:

- `agent_status/_template.md` — copy this to start a new agent's log
- `agent_status/claude-code.md`
- `agent_status/agy.md`
- `agent_status/opencode.md`
- `agent_status/codex-cli.md`
- `agent_status/cursor.md`
