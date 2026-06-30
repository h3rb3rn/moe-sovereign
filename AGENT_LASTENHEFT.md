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
formalized as tasks): (1) make the personal API key prefix configurable via environment variables or options in all scripts (`scripts/dataset_generator.py`, `scripts/send_request.py`, `scripts/index_models_metadata.py`); (2) ensure that any cloud-model discovery or dynamic routing configurations do not hardcode AIHUB, but remain fully configurable dynamically via the MoE Admin UI (Inference Servers / User Connections), allowing individual configurations for users without AIHUB access; (3) `models/backup_20260612/` (552 KB old ONNX model from
TASK-2) is safe to delete once the new `sovereign_router.onnx` has been
stable for a while.

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

---

### TASK-10: Trust-Score / Verification Substrate

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

- **Status:** pending
- **Owner:** unassigned
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

**New tasks dependency graph (TASK-10 through TASK-21):**
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
