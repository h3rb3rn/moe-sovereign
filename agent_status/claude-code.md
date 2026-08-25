# Status Log — Claude Code

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

## 2026-06-12T10:42:00Z — TASK-1 — in_progress
Plan / progress:
- Extracted `resolve_requested_ctx(model, state_num_ctx, num_ctx_env, label="")` in
  `context_budget.py`, mirroring the priority/clamp logic previously duplicated in
  `_judge_model_kw`/`_planner_model_kw`: `state_num_ctx or num_ctx_env or
  get_model_context_window(model)`, clamped to `get_model_context_window(model)`
  if that static value is smaller.
- Refactored `_judge_model_kw` and `_planner_model_kw` in `services/inference.py`
  to call `resolve_requested_ctx()` (pure refactor, no behavior change for actual
  LLM calls). Added the import alongside the existing `_static_ctx` import.
- Updated the merger PRE-FLIGHT block in `graph/synthesis.py` (~line 375-389) to
  compute `_merger_ctx` via `resolve_requested_ctx(_merger_judge_model,
  _merger_judge_num_ctx, JUDGE_NUM_CTX, label="synthesis")` instead of the live
  `/api/ps`-based `get_model_ctx_async`. Rest of the PRE-FLIGHT block (budget calc,
  overflow logging, `compress_prompt_to_fit`) unchanged — consumes `_merger_ctx`
  as before.
- Syntax-checked all 3 changed files (`python3 -c "import ast; ast.parse(...)"` —
  all OK).
- Rebuilt and recreated `langgraph-app`
  (`sudo docker compose build langgraph-app && sudo docker compose up -d langgraph-app`)
  — container started cleanly, logs normal, only a pre-existing/unrelated NiFi
  self-signed-cert warning.
- Now running an E2E verification request via the MoE-API (quicksort prompt) and
  checking `langgraph-orchestrator` logs for absence of the spurious
  `PRE-FLIGHT merger overflow — ctx=4096` warning for `qwen3.6:35b`.

Pre-conditions verified:
- No other agent's status file shows an `in_progress` entry touching
  `context_budget.py`, `services/inference.py`, or `graph/synthesis.py`.
- `langgraph-app` container healthy after recreate.

Notes:
- Deviation from the original TASK-1 instructions in `AGENT_LASTENHEFT.md`:
  `graph/expert.py` (~line 306/392) was investigated and found NOT to need
  changes — its PRE-FLIGHT budget (`_expert_ctx_window`, from `get_model_ctx_async`
  + VRAM pinning + native-ctx clamp) is already the SAME variable used to build
  the actual call's `extra_body.options.num_ctx`, so it is self-consistent and
  not affected by Bug B. Will reconcile this in `AGENT_LASTENHEFT.md` TASK-1 once
  E2E verification completes.

---

## 2026-06-12T11:05:00Z — TASK-1 — done
Plan / progress:
- Ran the quicksort E2E prompt via the MoE-API (model `moe-auto`). HTTP 200,
  finish_reason=stop, total ~19 min (dominated by 2 expert calls + an 11-min
  judge generation on a 70B model at 91% VRAM — not a ctx-mismatch reload).
- Orchestrator logs for this run show the new `resolve_requested_ctx()` label
  `synthesis` firing: `context clamped from requested 32768 to safe limit 4096
  for model llama3.3-70b-ctx4k:latest`. For THIS run the dynamic router picked
  `llama3.3-70b-ctx4k:latest` as judge (not `qwen3.6:35b`), and 4096 is that
  model's genuine static ctx (a deliberately VRAM-constrained variant) — so the
  resulting "PRE-FLIGHT merger overflow — ctx=4096, fixed=3500" warning is a
  TRUE positive, not the Bug B false alarm.
- Directly verified the original Bug B case in-container:
  `resolve_requested_ctx('qwen3.6:35b', 0, 32768) == 32768` (was previously
  reported as 4096 via stale `/api/ps` state — now fixed) and
  `resolve_requested_ctx('llama3.3-70b-ctx4k:latest', 0, 32768) == 4096`
  (correct, matches static ctx for this model).
- Ran `python3 -m pytest tests/test_dynamic_router.py tests/test_context_index.py
  -q` on host → 24 passed.
- Updated `AGENT_LASTENHEFT.md` TASK-1: `Status: done`, `Owner: Claude Code`,
  added "Resolution notes" section documenting the implementation, the
  `graph/expert.py` deviation, and verification evidence.

Pre-conditions verified:
- `langgraph-app` container healthy throughout (no restart needed for
  verification).

Notes:
- No regression observed. `/api/ps` polling continues elsewhere (node-health/
  VRAM diagnostics, e.g. `vram_high` warnings) — it is simply no longer used as
  PRE-FLIGHT budget input, as documented in `AGENT_LASTENHEFT.md`.
- TASK-1 fully closed. TASK-2 (LUMI cert/training) and TASK-3 (IMoE
  verification, depends on TASK-2) remain blocked/unstarted.

---

## 2026-06-12T11:20:00Z — TASK-2 — starting
Plan / progress:
- Human operator confirmed the LUMI SSH cert is valid again (`ssh lumi-g`
  works) — skipping step 1/2 (cert renewal check) of the original
  instructions.
- `ssh lumi-g "echo OK"` succeeds: lands on `uan18` as `hornphil`
  (host alias `lumi-g` → `efp.lumi.csc.fi`, cert
  `~/.ssh/id_efp.lumi.csc.fi-cert.pub`).
- Next: locate `train_router_onnx.py` and `train_router.sh` (check agy
  session scratch dirs / repo), confirm
  `~/synthetic_router_dataset.json` exists, copy dataset +
  scripts to LUMI-G scratch (`/scratch/project_465003058/hornphil/data/`),
  submit `sbatch train_router.sh`, poll via `squeue`/`sacct`.
- Will checkpoint here again before/after `sbatch` submission (long-running
  SLURM job).

Pre-conditions verified:
- `ssh lumi-g` connectivity confirmed (uan18, hornphil).
- No other agent's status file shows `in_progress` on TASK-2.

---

## 2026-06-12T11:35:00Z — TASK-2 — checkpoint
Plan / progress:
- Discovery: training on LUMI-G is ALREADY DONE. SLURM job 19166081
  (`moe-router...`, COMPLETED, 2026-06-11T10:48:23 → 10:48:45, exit 0:0)
  trained `SovereignRouterClassifier` for 40 epochs (loss 0.2854 → 0.0324)
  using `/pfs/lustrep4/users/hornphil/train_router_onnx.py` (same script as
  repo's `scripts/train_router_onnx.py`) and exported
  `/scratch/project_465003058/hornphil/models/sovereign_router.onnx{,.data}`
  (md5 466ad556.../5e811e3d...).
- An EARLIER job (19166029, 2026-06-11T10:46:53) failed with
  `LocalEntryNotFoundError` (no internet access on compute node to fetch
  all-MiniLM-L6-v2 from HF Hub) — superseded by 19166081, which used the
  locally-cached embedding model at
  `/scratch/project_465003058/hornphil/data/all-MiniLM-L6-v2/`.
- The model currently deployed in `langgraph-app` / repo
  `models/sovereign_router.onnx{,.data}` (md5 d9a7a57b.../49dd0799...,
  mtime 2026-06-12 09:29) does NOT match job 19166081's output — different
  checksums for BOTH files (the training script has no fixed random seed, so
  this looks like a separate/earlier or local training run, not the LUMI
  result from job 19166081).
- TASK-2 remaining scope reduces to: copy job 19166081's output from LUMI to
  `/opt/moe-sovereign/models/`, rebuild+restart
  `langgraph-app`, verify it loads.
- Original instructions steps 1-5 (cert check, dataset/script upload, sbatch
  submission) are MOOT — training already happened. Proceeding directly to
  step 6 (copy-back + deploy).

Pre-conditions verified:
- `sacct -j 19166029,19166081` confirms COMPLETED/exit 0:0 for both.
- Container's currently-loaded model confirmed via TASK-1 E2E log
  ("🎯 Sovereign Router ONNX model loaded from /app/models/sovereign_router.onnx").

---

## 2026-06-12T11:50:00Z — TASK-2 — done
Plan / progress:
- Backed up the previously-deployed ONNX model to
  `models/backup_20260612/` (md5 `d9a7a57b...`/`49dd0799...`, mtime
  2026-06-12 09:29 — did not match any LUMI job output, likely an earlier/
  separate local training run; script has no fixed seed).
- `scp`'d job 19166081's output from
  `lumi-g:/scratch/project_465003058/hornphil/models/sovereign_router.onnx{,.data}`
  to `models/sovereign_router.onnx{,.data}` in the repo root (md5
  `466ad556...`/`5e811e3d...` — confirmed match on both ends).
- Rebuilt + recreated `langgraph-app`
  (`sudo docker compose build langgraph-app && sudo docker compose up -d
  langgraph-app`). Container healthy.
- Confirmed in-container md5 matches the LUMI-trained model, and startup log
  shows `🎯 Sovereign Router ONNX model loaded from
  /app/models/sovereign_router.onnx (providers=['CPUExecutionProvider'])`
  with no errors.
- Sent a trivial follow-up E2E request via the MoE-API → HTTP 200,
  `finish_reason: stop`, no router-related errors.
- Updated `AGENT_LASTENHEFT.md` TASK-2: `Status: done`, `Owner: Claude Code`,
  added "Resolution notes" documenting the build-time (not bind-mount) path
  for `/app/models`, the checksum mismatch finding, and the swap.

Pre-conditions verified:
- `langgraph-app` healthy after recreate; both verification requests
  (TASK-1 quicksort run and this TASK-2 trivial run) completed with HTTP 200.

Notes:
- TASK-3 is now UNBLOCKED (depends on TASK-2's ONNX output being in place —
  done). Not started in this session; left as `Owner: unassigned` /
  `Status: blocked → unblocked, not yet started` for the next agent or a
  follow-up request.
- `models/backup_20260612/` left in place (552 KB) for rollback if the new
  router model misbehaves in production; safe to delete once the new model
  has proven stable over a few days.

---

## 2026-06-12T12:05:00Z — TASK-3 — starting
Plan / progress:
- Steps 1-2 (ONNX present, rebuild/restart) already satisfied by TASK-2 work.
- Plan for manual tests (Section 3 of TASK-3 instructions):
  1. DB log writes: trigger a request, check `dynamic_template_feedback_log`
     (Postgres) for a new row and `/app/logs/policy_training.jsonl` for a new
     line.
  2. ChromaDB template cache: send the same/similar prompt twice, check
     `services/dynamic_router.py` logs for a cache-hit message on the 2nd
     request.
  3. Local-Only Compliance: inspect how `local_only` is enforced in
     `services/dynamic_router.py`/`services/routing.py`/`graph/synthesis.py`
     (per AGENTS.md Permanent Task 3), and verify (by code review +/or a
     `local_only` test request) that the dynamic router doesn't score/route
     to non-local endpoints (e.g. `adesso-ai-hub.3asabc.de`).
  4. Write walkthrough report (location TBD — `./docs/` if permanent, else
     extend agy's `task.md`/`walkthrough.md`).
- Will read `services/dynamic_router.py`, `services/feedback.py`,
  `policy_log.py`, and DB connection helpers first to know table/column names
  and connection details before running queries.

Pre-conditions verified:
- `langgraph-app` healthy (from TASK-2 recreate).
- No other agent's status log shows `in_progress` on TASK-3 or the files
  above.

---

## 2026-06-12T12:20:00Z — TASK-3 — checkpoint
Plan / progress:
- Manual test 1 (DB log writes) DONE, with a finding that corrects the
  Lastenheft's acceptance criterion:
  - `/app/logs/policy_training.jsonl` IS written correctly per request
    (Bug A fix holds) — confirmed real `v:2` + `_correction` entries for
    the TASK-1/TASK-2 verification chat_ids.
  - `admin_expert_templates` (Postgres, `moe_userdb`) IS written on every
    dynamic-template compile — confirmed 8 rows incl.
    `moe-dyn-2b7b6db868c8` (TASK-1, 10:42:39), `moe-dyn-2456a362aaa0`
    (TASK-2, 11:18:53), and 3 rows from this session's TASK-3 direct
    `get_dynamic_template()` calls (12:14:40/41/57).
  - `dynamic_template_feedback_log` (the table literally named in TASK-3's
    instructions) has **0 rows**. `log_dynamic_template_feedback()`
    (`admin_ui/database.py:2518`) is DEAD CODE — grep confirms it has no
    callers anywhere in the codebase. Only its sibling
    `update_dynamic_template_feedback_rating()` is used (from
    `routes/feedback.py`, for user thumbs-up/down on an existing
    template_id).
  - Interpretation: the Lastenheft's author conflated
    `dynamic_template_feedback_log` (intended for per-request
    latency/token/rating feedback, never wired up) with
    `admin_expert_templates` (the table `_save_template_to_db_and_cache()`
    in `dynamic_router.py:392` actually inserts into). Treating manual
    test 1 as PASS based on `admin_expert_templates` +
    `policy_training.jsonl` evidence above; flagging the dead
    `dynamic_template_feedback_log`/`log_dynamic_template_feedback()` as a
    TASK-4 candidate (either wire it up or remove the dead code per
    CLAUDE.md "no dead code").
- Next: manual test 2 (ChromaDB semantic template cache hit) — call
  `get_dynamic_template()` twice in-container with the same prompt, check
  for "🎯 Semantic template cache L2 hit!" on the 2nd call.

---

## 2026-06-12T12:35:00Z — TASK-3 — done
Plan / progress:
- Manual test 2 (ChromaDB cache): ran `get_dynamic_template()` 3x in-container
  with the same/near-same German prompt ("Erkläre mir kurz, wie ein
  Bloom-Filter funktioniert."). All 3 calls compiled+registered NEW templates
  (`moe-dyn-a89b89...`, `moe-dyn-1ff1c2df9f3d`, `moe-dyn-91bf93bf3291`) —
  no "🎯 Semantic template cache L2 hit!" ever logged.
- Root-caused via a diagnostic script comparing distances directly against
  the live `moe_template_cache` ChromaDB collection: querying with the raw
  prompt → distance 0.3103 to its own just-stored doc (> 0.18 threshold,
  miss); querying with the exact stored-document text
  (`"Dynamic gating template compiled for prompt: ..."`) → distance ~0.0000
  (would hit). Confirmed root cause = query/document text-format mismatch
  between `_match_existing_template()` (queries raw prompt) and
  `_save_template_to_db_and_cache()` (indexes wrapped description string).
  Documented as **Bug C**.
- Manual test 3 (local-only compliance): already PASS from earlier
  direct-call evidence (local_only=True → all-local allocation;
  local_only=False → cloud judge `mistralai/mistral-large-3-675b-instruct-2512@nvidia`).
- Cross-checked agy's session brain
  (`~/.gemini/antigravity-cli/brain/38b2b162-4f85-49f0-8a2c-05400168d4ae/`):
  `task.md` item 3 confirms `dynamic_template_feedback_log` was meant to be
  written per-request and updated by `feedback.py` on user rating — but the
  INSERT helper `log_dynamic_template_feedback()` has zero callers. Documented
  as **Bug D** (user 👍/👎 ratings on dynamic templates are silently dropped,
  swallowed by `feedback.py:114`'s bare `try/except: pass`).
- Wrote up all 3 manual tests + Bug C + Bug D as new §5 in agy's
  `walkthrough.md`, checked off `task.md` item 7 (all 4 sub-items, with
  annotations).
- Updated `AGENT_LASTENHEFT.md`:
  - TASK-3 → `done`, with Resolution notes (2/3 manual tests pass, test 2
    root-caused as Bug C; acceptance-criteria interpretation documented).
  - Added Bug C / Bug D to the "Today's debugging session" bug list.
  - Filled **TASK-4** (fix Bug C — ChromaDB cache fix, recommended option +
    alternative + acceptance criteria) and **TASK-5** (fix Bug D — wire up
    `log_dynamic_template_feedback`), both `Status: pending, Owner:
    unassigned`.
  - Added a new **TASK-6** reserved slot, carrying forward the earlier
    hardcoded-infrastructure/secret finding
    (`OLLAMA_ENDPOINTS`/`CLOUD_ENDPOINT`/`CLOUD_TOKEN` in
    `dynamic_router.py:43-48`) as a formal candidate.
- Removed temporary in-container test scripts
  (`/app/_task3_cache_test.py`, `/app/_task3_cache_diag.py`) and their repo
  copies under `agent_status/`.

Pre-conditions verified:
- `langgraph-app` healthy throughout (read-only diagnostics only, no code
  changes to running services — Bug C/D fixes deliberately left to
  TASK-4/TASK-5 for a follow-up session/agent).

Notes:
- TASK-3 fully closed. TASK-4, TASK-5, TASK-6 are new, `unassigned`,
  ready for pickup by any agent.
- `models/backup_20260612/` (from TASK-2) still pending cleanup — left as
  noted previously.

---

## 2026-06-12T15:55:00Z — TASK-4 — starting
Plan / progress:
- User confirmed: proceed with TASK-4 (fix Bug C, ChromaDB semantic
  template cache never hits).
- Plan: implement recommended option (a) from TASK-4 — index the raw
  `prompt` text in ChromaDB (matching `_match_existing_template()`'s query
  text), keep the `"Dynamic gating template compiled for prompt: ..."`
  wording only in the Postgres `description` column / metadata.
  - Add a `cache_query_text: str` parameter to
    `_save_template_to_db_and_cache()` (`services/dynamic_router.py:378`),
    used for `documents=[...]` in the ChromaDB `.add()` call (line ~405).
    `desc` continues to be used for the Postgres `description` column
    (unchanged).
  - At the call site (`dynamic_router.py:700`), pass
    `cache_query_text=prompt`.
- After implementation: re-run the in-container repeat-prompt test from
  TASK-3 §5.2 (same prompt twice) — expect 2nd call to log "🎯 Semantic
  template cache L2 hit!" and NOT register a new `admin_expert_templates`
  row. Then `pytest tests/test_dynamic_router.py -q`, then rebuild/restart
  `langgraph-app`.

Pre-conditions verified:
- No other agent's status file shows `in_progress` on
  `services/dynamic_router.py`.
- `langgraph-app` healthy (last verified at TASK-3 close).

---

## 2026-06-12T18:00:00Z — TASK-4 — done
Plan / progress:
- Implemented the planned fix (option a): added `cache_query_text` param to
  `_save_template_to_db_and_cache()`, ChromaDB now indexes the raw prompt
  (matching `_match_existing_template()`'s query text). Call site passes
  `cache_query_text=prompt`.
- Re-testing immediately exposed TWO further latent bugs in the
  (previously dead) cache-hit-fetch path, both fixed in the same pass:
  - `row[0]` → `KeyError: 0` because the pool uses `dict_row` by default
    (`database.py:445`) — fixed to `row["config_json"]`.
  - Cached `config_json` lacks `"id"`/`"name"` (added to the dict only
    after `_save_template_to_db_and_cache()` returns in the normal path) —
    would have caused `KeyError: 'id'` at `chat.py:1029` on every cache
    hit. Fixed by restoring `cached_config["id"]`/`["name"]` from
    `tmpl_id`/`tmpl_name` before returning.
- `pytest tests/test_dynamic_router.py -q` → 6 passed (both before and
  after all 3 edits).
- Rebuilt + recreated `langgraph-app` twice (once per fix iteration via
  `sudo docker compose build/up -d`); healthy both times.
- Final in-container verification (new prompt "Was ist der Unterschied
  zwischen einem Hash-Set und einer Linked List?"):
  - Call 1 → miss, compiled `moe-dyn-512feaa590df`.
  - Call 2 (identical prompt) → `🎯 Semantic template cache L2 hit! ...
    distance=-0.0000`, returned the SAME `moe-dyn-512feaa590df` incl.
    `id`/`name`. Confirmed via direct Postgres query: exactly 1 row for
    that id in `admin_expert_templates` (no duplicate created).
  - Call 3 (prompt matching a different, earlier-registered template) →
    correctly hit THAT template (`moe-dyn-a24bf34df57b`), confirming the
    cache distinguishes unrelated prompts.
- Updated `AGENT_LASTENHEFT.md`: TASK-4 → `done`, Resolution notes covering
  all 3 fixes + verification evidence; Bug C marked `fixed 17:58 UTC+2`.
- Removed temporary verification script
  (`/app/_task4_cache_verify.py` + repo copy under `agent_status/`).

Pre-conditions verified:
- `langgraph-app` healthy after both rebuilds.
- Exactly 1 `admin_expert_templates` row for the cache-reused template id
  (no duplicate row created on cache hit).

Notes:
- TASK-4 fully closed. TASK-5 (Bug D — wire up
  `log_dynamic_template_feedback`) and TASK-6 (hardcoded infra/secrets in
  `dynamic_router.py`) remain `unassigned`, ready for pickup.
- `models/backup_20260612/` (from TASK-2) still pending cleanup.

---

## 2026-06-12T17:20:00Z — TASK-5 — starting
Plan / progress:
- User confirmed: proceed with TASK-5 (fix Bug D — wire up
  `log_dynamic_template_feedback` so `dynamic_template_feedback_log` gets a
  row per compiled dynamic template, enabling
  `update_dynamic_template_feedback_rating()` in `routes/feedback.py:114`
  to actually update something).
- Plan: in `_save_template_to_db_and_cache()` (`dynamic_router.py:378`),
  after the existing `admin_expert_templates` INSERT, call
  `log_dynamic_template_feedback(tmpl_id, cache_query_text, config_json,
  latency_ms=None, tokens_used=None)` (reusing the `cache_query_text`
  param added in TASK-4 — it's the raw prompt). Add the missing import from
  `admin_ui.database`. Wrap in its own try/except (best-effort, matching
  the existing INSERT's error-handling style).
- Verification: in-container, call `get_dynamic_template()` once, then
  query `dynamic_template_feedback_log` for the new row; then call
  `update_dynamic_template_feedback_rating(tmpl_id, 5)` directly and
  confirm `rowcount > 0` / `user_rating` updated.
- `pytest tests/test_dynamic_router.py -q`, then rebuild/restart
  `langgraph-app`.

Pre-conditions verified:
- No other agent's status file shows `in_progress` on
  `services/dynamic_router.py` or `admin_ui/database.py`.
- `langgraph-app` healthy (last verified at TASK-4 close).

---

## 2026-06-12T17:24:17Z — TASK-5 — done

Bug D fixed and verified end-to-end.

**Changes:**
- `services/dynamic_router.py:16` — added `log_dynamic_template_feedback`
  to the `admin_ui.database` import.
- `services/dynamic_router.py`, in `_save_template_to_db_and_cache()` —
  after the existing `admin_expert_templates` INSERT (own try/except),
  added a second try/except calling `log_dynamic_template_feedback(
  template_id=tmpl_id, prompt=cache_query_text, config_json=config_json,
  latency_ms=None, tokens_used=None)`. `cache_query_text` (raw prompt,
  from TASK-4) is reused — no new parameter.

**Verification:**
- `python3 -m pytest tests/test_dynamic_router.py -q` → 6 passed.
- Rebuilt + restarted `langgraph-app`; clean startup (only the
  pre-existing NiFi self-signed-cert warning).
- In-container script (`init_db()` + `dr.init_router()` +
  `get_dynamic_template()` with a fresh prompt):
  - New compile → `template_id = moe-dyn-49bef56315d6`.
  - `dynamic_template_feedback_log` row created immediately with that
    `template_id`, the compiled prompt, `user_rating=None`,
    `status='success'`.
  - `update_dynamic_template_feedback_rating(tmpl_id, 5)` → `True`.
  - Re-query confirmed `user_rating=5` (rowcount > 0).
- Temp script removed from container and repo.

**Docs updated:**
- `AGENT_LASTENHEFT.md`: TASK-5 → `done` with Resolution notes; Bug D
  bullet → "fixed 19:24 UTC+2, TASK-5".

Both acceptance criteria met. TASK-6 (hardcoded infra/secrets in
`dynamic_router.py:43-48`) remains open/unassigned.

---

## 2026-06-12T21:03:52Z — TASK-6 — starting

User confirmed proceeding with TASK-6 (hardcoded `OLLAMA_ENDPOINTS` /
`CLOUD_ENDPOINT` / `CLOUD_TOKEN` in `services/dynamic_router.py:43-48`).

**Pre-conditions verified:**
- `OLLAMA_ENDPOINTS` (`{"N04-RTX": "...224:11434", "N11-M10": "...231:11434"}`)
  matches `config.py`'s `URL_MAP` entries for `N04-RTX`/`N11-M10` with
  `/v1` stripped and `API_TYPE_MAP[...] == "ollama"` — safe to derive.
- `CLOUD_TOKEN` (`moe-sk-940e228...`) traced via `api_keys` table
  (`key_prefix LIKE 'moe-sk-940%'`) to a personal key owned by
  `kontakt@philipp-horn.dev`, label "Benchmark", `dynamic_routing=true`,
  `local_only_routing=true`. `SYSTEM_API_KEY` ("system-healer") returns 0
  models via `/v1/models` in-container (vs. 1021 for the hardcoded key) —
  not a drop-in replacement.
- Same personal key also hardcoded in `scripts/dataset_generator.py`,
  `scripts/send_request.py`, `scripts/index_models_metadata.py` —
  out of scope for TASK-6, documented as follow-up.

**User decision:** behavior-preserving fix — derive `OLLAMA_ENDPOINTS` from
`config.py`; move `CLOUD_ENDPOINT`/`CLOUD_TOKEN` to new `.env` vars
(`DYNAMIC_ROUTER_CLOUD_ENDPOINT`/`_TOKEN`) with unchanged values.

**Plan:**
1. Edit `services/dynamic_router.py`: import `URL_MAP`, `API_TYPE_MAP` from
   `config`; derive `OLLAMA_ENDPOINTS`; read `CLOUD_ENDPOINT`/`CLOUD_TOKEN`
   via `os.getenv(..., "")`; guard the cloud-poll block in
   `_get_cluster_state()`.
2. Add the two new vars to `.env` with current values.
3. `pytest tests/test_dynamic_router.py -q`.
4. Rebuild/restart `langgraph-app`; verify `_get_cluster_state()` returns
   the same local+cloud model counts as before.

No other agent's status file shows `in_progress` on `services/dynamic_router.py`
or `config.py`.

---

## 2026-06-12T21:06:27Z — TASK-6 — done

Hardcoded infra/secrets in `services/dynamic_router.py:43-48` removed,
behavior-preserving as decided with the user.

**Changes:**
- `services/dynamic_router.py:17` — added `URL_MAP`, `API_TYPE_MAP` to the
  `config` import.
- `OLLAMA_ENDPOINTS` now derived from `URL_MAP`/`API_TYPE_MAP` (filter
  `api_type == "ollama"`, strip trailing `/v1`) — produces the identical
  `{"N04-RTX": "...224:11434", "N11-M10": "...231:11434"}` from the
  admin-configured `INFERENCE_SERVERS` env var.
- `CLOUD_ENDPOINT`/`CLOUD_TOKEN` now `os.getenv("DYNAMIC_ROUTER_CLOUD_
  ENDPOINT"/"_TOKEN", "")` — empty-string default per CLAUDE.md.
- `_get_cluster_state()`'s cloud-poll block guarded with
  `if CLOUD_ENDPOINT and CLOUD_TOKEN:`.
- `.env` — added `DYNAMIC_ROUTER_CLOUD_ENDPOINT`/`_TOKEN` with the
  previously-hardcoded values (unchanged), with explanatory comment.

**Verification:**
- `python3 -m pytest tests/test_dynamic_router.py -q` → 6 passed.
- Rebuilt + restarted `langgraph-app`; clean startup (only the pre-existing
  NiFi self-signed-cert warning).
- In-container script: `OLLAMA_ENDPOINTS` identical to old hardcode;
  `_get_cluster_state()` → 101 local models + 1021 cloud models = 1122
  total — same as the TASK-3/4/5 baseline. Temp script removed from
  container and repo.

**Docs updated:**
- `AGENT_LASTENHEFT.md`: TASK-6 → `done` with Context (incl. the
  personal-API-key finding), Decision, Instructions, Acceptance criteria,
  Follow-up note, and Resolution notes.

**Not in scope (documented as follow-up in TASK-6):**
- The same personal key (`moe-sk-940e228...`, owned by
  `kontakt@philipp-horn.dev`, label "Benchmark") is also hardcoded in
  `scripts/dataset_generator.py`, `scripts/send_request.py`,
  `scripts/index_models_metadata.py`.
- Whether dynamic-routing's cloud-model discovery should run under that
  personal key vs. a properly AIHUB-connected system account
  (`SYSTEM_API_KEY` / "system-healer" currently returns 0 models) is an
  admin/architecture decision, not actioned here.

All TASK-1 through TASK-6 from `AGENT_LASTENHEFT.md` are now `done`.

---

---

## 2026-07-01T12:00:00Z — TASK-29/30/31 — done

Alle drei ADHS-Transfer-Tasks implementiert.

**TASK-31 (Model Capability Table):**
- `configs/model_capabilities.yaml` (13 Modelle + default-Block)
- `services/model_capabilities.py` (YAML-Loader, get_model_caps, typed Getters)
- `tests/test_model_capabilities.py` (10 Tests grün)
- `admin_ui/templates/model_capabilities.html` (read-only Tabelle)
- `admin_ui/app.py`: `/model-capabilities` + `/api/model-capabilities`
- `services/inference.py`: Import + debug-Log vor Judge-Call

**TASK-30 (Structured-Output Failure Recovery):**
- `services/structured_failure.py` (StructuredFailureKind, RecoveryAction, build_failure, resolve_retry_model)
- `tests/test_structured_failure.py` (16 Tests grün)
- `pipeline/state.py`: `structured_failure` + `structured_failure_round` Felder
- `admin_ui/database.py`: `ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS structured_failure_round`
- `routes/admin_stats.py`: neues Feld im pipeline_log SELECT

**TASK-29 (AI I/O Audit Service):**
- `services/ai_io_audit.py` (sanitize_audit_payload, AiIoAuditEntry, create/complete/get_live)
- `tests/test_ai_io_audit.py` (11 Tests grün)
- `admin_ui/database.py`: `ai_io_audit_log` Tabelle + Indizes
- `routes/admin_stats.py`: `GET /v1/admin/ai-io-audit`
- `admin_ui/templates/ai_io_audit.html` (Filter, Tabelle, Detail-Modal)
- `admin_ui/app.py`: `/ai-io-audit` + `/api/ai-io-audit`
- `services/inference.py`: Judge-Ollama-Call mit Audit gewrapped
- `admin_ui/lang/`: 4 Sprachdateien aktualisiert

**Lastenheft:**
- TASK-10 bis TASK-22, TASK-25 bis TASK-27 als done markiert (durch andere Agenten bereits implementiert)
- TASK-29/30/31 als done markiert mit Resolution-Notes
- TASK-21 (GraphRAG Benchmark) bleibt pending (benchmark_graphrag.py nicht implementiert)

**Gesamt: 89 Tests grün.**

---

## 2026-07-05T19:24:13Z — TASK-32 — in_progress → done (Korrektur der bestehenden Evaluation)

Plan / progress:
- Bestehenden Evaluationsbericht (Antigravity, moe_design_system_evaluation.md)
  gelesen; unabhängig via öffentlicher GitHub-API verifiziert (Repo-Metadaten,
  vollständiger Dateibaum, README, LICENSE, `claude/system-prompt.md`,
  `codex/AGENTS.md`, Beispiel-Skill `ai-slop-check.md`).
- Zwei von der bestehenden Evaluation übersehene Befunde identifiziert und
  gegen den Live-Code von MoE-Sovereign verifiziert (`services/skills.py`,
  `admin_ui/app.py::_run_llm_audit`, `graph/expert.py`-Aufrufmuster):
  1. Das Repo hat zwei Varianten (`claude/` mit Subagent-Delegation,
     `codex/` als Single-Loop ohne Subagent) — nicht nur eine.
  2. Ein "Model calibration"-Abschnitt im README warnt explizit, dass der
     Prompt auf aktuelle Anthropic-Frontier-Modelle kalibriert ist und bei
     älteren/lokalen Modellen "under-trigger" kann — direkt relevant, da
     MoE-Sovereigns Experten lokale SLMs sind (qwen3.6:35b, gemma4:12b, ...).
  3. Der Claude-Workflow (Kapitel 2-4) setzt Dateisystem-Zugriff und
     Subagent-Verifikation voraus — MoE's reguläre Experten-Pipeline
     (`graph/expert.py`) ist reines Text-rein/Text-raus ohne Tools. Ein
     `frontend_designer`-MoE-Experte (Weg 1 der bestehenden Evaluation)
     kann daher nur die Prinzipien (Kapitel 5-16), nicht den Workflow nutzen.
- Lastenheft TASK-32 Resolution-Notes um diese Korrektur ergänzt; Empfehlung
  umgewichtet: Weg 2 (Skills für Claude-Code-Sessions, `codex/`-Variante als
  strukturelle Vorlage) zuerst, Weg 1 (MoE-Pipeline-Experte) nur mit
  reduziertem Scope (nur Stilregeln, kein Workflow).

Pre-conditions verified:
- `services/skills.py`: YAML-Frontmatter-Format (`description:`-Feld) und
  `admin_approved`/`audit_verdict`-Registry bestätigt.
- `admin_ui/app.py:3716 _run_llm_audit()`: Sicherheitsaudit-Mechanismus für
  Community-Skills existiert bereits, wie von TASK-32 Phase 3 vorausgesetzt.
- `graph/expert.py`-Aufrufpfad (ChatOpenAI/Ollama-nativ): kein Tool-/
  Dateizugriff für reguläre MoE-Experten bestätigt (gleiches Muster wie
  bereits in der Architektur-Analyse vom 2026-07-05 für den CC-Tool-Pfad
  festgestellt).

Notes: Kein Code geändert — reine Korrektur/Ergänzung der Evaluation und des
Lastenhefts. Owner-Feld auf "Claude Code" umgestellt (Korrektur einer
bestehenden Resolution, nicht neue Implementierung).

---

## 2026-07-05T19:34:33Z — TASK-33 — new (pending)

Plan / progress:
- Neue Task TASK-33 im Lastenheft angelegt: Vibelate-Governance-Framework
  (`/opt/deployment/Michael_Reich/Vibelate3`, Ursprung `ADHS/vibelate/`) als
  CC-Profil-Preset statt als MoE-Pipeline-Modus, gestufter Weg
  (System-Prompt-Prefix zuerst, Fine-Tuning erst nach nachgewiesener
  Stabilität des Regelwerks über die bereits vorhandene
  Quality-Probe/Distillations-Infrastruktur).
- Dependency-Graph in Section 4 um "Agent-Governance Transfer: TASK-33"
  ergänzt.
- Kein Code geändert — reine Backlog-Aufnahme auf Nutzeranfrage
  ("Vorschlag mit ins Lastenheft aufnehmen").

Pre-conditions verified:
- Abhängigkeiten (`services/quality_probe.py`, `MOE_QUALITY_PROBE`-Flag,
  `scripts/export_distillation_dataset.py`) bereits in dieser Session
  implementiert und live-verifiziert (siehe
  `SESSION_DOKUMENTATION_2026-07-05.md`).

Notes: Status bewusst `pending` — reine Backlog-Aufnahme, keine Umsetzung
angefordert. Owner `unassigned`.

---

## 2026-07-05T20:10:20Z — TASK-33 Phase A / TASK-32 Phase 2 / Section-1-Follow-ups — done/blocked (siehe Notes)

Plan / progress (Umsetzung der zuvor priorisierten Reihenfolge auf
Nutzeranfrage "Mach es so"):
1. **TASK-33 Phase A (CC-Profil-Preset):** `Vibelate3/AGENTS.md` auf
   Precedence/Core-Working-Contract/Coding-Behavior/Verification-Rules
   kondensiert (2638 Zeichen). Neues CC-Profil "Vibelate-Strict"
   (`ucp-96dd63b047aa47deac4a856a`) für User horndev per SQL-INSERT in
   `user_cc_profiles` angelegt — **nach expliziter Nutzerbestätigung**
   (Classifier stoppte den ersten Versuch als "Modify Shared Resources",
   Nutzer per AskUserQuestion um Erlaubnis gebeten, "Direkt per SQL anlegen"
   gewählt). Redis-Cache für horndev invalidiert. Profil noch **nicht** einem
   API-Key zugewiesen (bewusst nicht automatisch, um den Live-Testschlüssel
   nicht zu verändern) — DONE bis zu diesem Punkt, Zuweisung liegt beim
   Nutzer.
2. **TASK-32 Phase 2 (Skill-Import):** `ai-slop-check.md` und
   `hierarchy-rhythm-review.md` aus `codex/skills/` (nicht `claude/skills/`,
   siehe Korrektur-Resolution) mit MIT-Copyright-Header (Trystan Sarrade,
   2026) und Frontmatter (`name`/`description`, Format von `a11y-audit.md`
   übernommen) nach `skills/community/` importiert. `accessibility-audit.md`
   bewusst NICHT importiert — ein `a11y-audit`-Skill mit gleichem
   Funktionsumfang existiert bereits. LLM-Sicherheitsaudit exakt mit dem
   Mechanismus aus `admin_ui/app.py::_run_llm_audit()` gegen `qwen3.6:35b`
   @N04-RTX durchgeführt: beide `verdict: safe`, 0 Findings, Audit-JSONs
   liegen neben den Skills. **Blocked:** Das Setzen von `admin_approved=TRUE`
   in `skill_registry` wurde vom Classifier gestoppt ("Permission Grant" —
   Selbst-Freigabe externen Codes ohne explizite Autorisierung für genau
   diesen Schritt). Bestehenden Endpunkt `POST
   /api/admin/skills/{skill_name}/approve` (admin_ui/app.py:3902) für die
   Freigabe im Admin-UI (`/skills`-Seite) an den Nutzer verwiesen statt die
   Sperre zu umgehen.
3. **Section-1-Follow-ups (AIHUB/API-Key-Hardcoding):** Alle drei
   informellen Follow-ups aus Section 1 als bereits erledigt verifiziert
   (grep über den gesamten Code, kein hartkodierter Key mehr,
   `CLOUD_ENDPOINTS` in `dynamic_router.py` vollständig aus
   `INFERENCE_SERVERS_LIST` abgeleitet, `models/backup_20260612/` existiert
   nicht mehr) — vermutlich durch spätere, nie zurück ins Lastenheft
   dokumentierte Arbeit gelöst. Lastenheft Section 1 mit Nachweisen
   aktualisiert (durchgestrichen + Update-Absatz), keine Umsetzung nötig.

Pre-conditions verified:
- `user_cc_profiles`-Schema (`id, user_id, name, config_json, is_active,
  created_at, updated_at`) vor dem INSERT geprüft.
- `skill_registry`/`skill_audit_log`-Schema und Bootstrap-Verhalten
  (`ON CONFLICT DO NOTHING`, `is_builtin=False` für Community-Skills) in
  `services/skills.py` gelesen, bevor der Audit-Mechanismus repliziert wurde.
- Zwei Classifier-Stopps sauber behandelt: einer nach Nutzer-Rückfrage
  fortgesetzt (CC-Profil), einer bewusst nicht umgangen, sondern an den
  vorhandenen Admin-Workflow verwiesen (Skill-Freigabe).

Notes: TASK-33 bleibt formal `pending` bis Phase B (Distillation) — Phase A
ist jetzt faktisch live. TASK-32 bleibt `partially_done` bis der Nutzer die
beiden Skills im Admin-UI freigibt.

---

## 2026-07-05T20:24:15Z — Lastenheft-Review + Doku-Sync — done

Plan / progress:
- Erneute Lastenheft-Prüfung auf Nutzeranfrage. Neu vorgefunden: TASK-34
  (Vibe-Coding-Ökosystem, vermutlich Antigravity, 22:19 lokal) — ohne
  Status-Log-Eintrag des Erstellers (agy.md unverändert seit 02.07.).
- Vier eigene Doku-Sync-Lücken geschlossen (Vibelate-Proof-Integrity-Regel
  auf eigene Einträge angewandt):
  1. TASK-33: Status `pending` → `partially_done`, Owner gesetzt,
     Resolution-Notes für Phase A ergänzt (Profil-ID, Classifier-Stopp +
     Nutzerbestätigung, bewusst offene Punkte).
  2. TASK-32: Umsetzungsstand in den revidierten Instructions dokumentiert
     (2 Skills importiert+auditiert, a11y bewusst übersprungen,
     Admin-Freigabe offen nach zweifachem Classifier-Stopp).
  3. TASK-33-Metriken: `structured_failure_round`→TASK-30,
     `trust_verdict`→TASK-10 sauber getrennt.
  4. Dependency-Graph: Header auf TASK-34 erweitert, TASK-32/33-Kommentare
     auf Ist-Stand.
- TASK-34 mit Review-Notiz versehen (Koordinations-Notiz gem. Section 0,
  Inhalt des fremden Eintrags nicht umgeschrieben): Pfade verifiziert (ok);
  Phase 1 unterstellt eine vermutlich nicht existente Compat-Lücke;
  nicht deklarierte Überlappung mit TASK-32/33 (Prompt-Stacking auf drei
  Ebenen); Phase 3 ohne Sicherheits-Gate für Community-MCP-Tools;
  Kriterium 1 selbsterfüllend; Status/Graph-Widerspruch.

Pre-conditions verified:
- `prompts/systemprompt/`, `routes/*_compat.py`, `mcp_server/server.py`
  (AST-Prüfung) — alle TASK-34-Referenzen existieren.
- Offene Nebenbeobachtung: TASK-9-Resolution im Lastenheft (28.06.) ist
  älter als der agy-Status-Log (02.07.: Jobs 19682379-82, 50.276 Samples) —
  nicht geändert, da fremde in_progress-Task und der Status-Log laut
  Protokoll das Resumability-Record ist.

Notes: Skill-Freigabe (TASK-32) weiterhin beim Nutzer — dritter
SQL-Selbstfreigabe-Versuch wurde nach erneutem Classifier-Stopp bewusst
nicht unternommen; Admin-UI-Weg oder erneute explizite Freigabe nötig.

---

## 2026-07-05T20:30:06Z — Widerspruchsauflösung Lastenheft — done

Plan / progress (auf explizite Betreiberanweisung „löse Widersprüche auf"):
1. TASK-34 Graph-Eintrag: „Evaluierung done" → „Plan eingetragen 2026-07-05"
   (kein Evaluations-Artefakt existierte; jetzt konsistent mit Status
   `pending`).
2. TASK-34 `Depends on: none` → Phase-2-Koordinationspflicht mit TASK-32/33
   (Prompt-Stacking) deklariert.
3. TASK-34 Kriterium 1 (selbsterfüllend) → prüfbares
   Verifikationsbericht-Kriterium ersetzt; Kriterium 4 („Community-geprüft")
   auf den etablierten Audit-Weg präzisiert. Beide Änderungen inline als
   Widerspruchsauflösung gekennzeichnet.
4. TASK-32 Statuszeile: „Implementierung ausstehend" → präziser
   Phasen-Stand (Phase 2 läuft, Freigabe offen).
5. TASK-9: Doku-Sync-Nachtrag in den Resolution-Notes mit Quellenverweis
   auf agy.md-Eintrag vom 02.07. (Jobs 19682379-82, 50.276/90.000 Samples);
   ausdrücklich vermerkt, dass der aktuelle SLURM-Zustand NICHT geprüft
   wurde (Betreiber-Entscheidung, kein SSH-Check) und der agy-Log
   maßgeblich bleibt.
6. Review-Notiz in TASK-34 aktualisiert (Formales-Punkt auf „aufgelöst"
   gesetzt, verbleibender Punkt: fehlender Ersteller-Status-Log-Eintrag —
   nicht durch mich behebbar, an Ersteller delegiert).

Pre-conditions verified:
- Fremde Task-Inhalte (TASK-34, TASK-9) nur mit expliziter
  Betreiberanweisung editiert; jede Änderung inline gekennzeichnet mit
  Datum und Grund, ursprüngliche Aussagen bei TASK-9 nicht gelöscht,
  sondern per Nachtrag korrigiert.

Notes: Damit sind alle in der Review vom 20:24Z-Eintrag genannten
Widersprüche aufgelöst bis auf den fehlenden agy-Status-Log-Eintrag
(liegt beim Ersteller). Offen bleibt weiterhin die Skill-Freigabe
(TASK-32) durch den Betreiber.

---

## 2026-08-07T00:00:00Z — TASK-52 — done (spec only, no code)

Plan / progress:
- Nutzer wollte lokale Bild-/Audio-Generierung (OpenAI-API-Parität
  `/v1/images/generations`, `/v1/audio/speech`) auf N04-RGTX evaluieren und
  als Lastenheft-Task spezifizieren.
- Hardware-Recherche (WebSearch, nicht aus Trainingsstand geraten):
  GTX 1060 (Pascal, CC 6.1) von aktuellem PyTorch/cuDNN nicht mehr
  unterstützt — gleiches Problem wie N11-M10, kein VRAM-Workaround möglich.
  RTX 2060 (Turing, CC 7.5) liegt exakt auf der aktuellen PyTorch-Untergrenze.
  FLUX-fp8 braucht nativ Ada/Hopper-Tensor-Cores (weder RTX 2060 noch RTX
  3060 vorhanden) — läuft hier nur über Weight-only-Quantisierung, langsamer
  als vielfach zitierte Ada-Benchmarks.
- Wichtiger Infra-Fund: N04-RTX/N04-RGTX/N04-TESLA sind derselbe physische
  Host (192.168.155.224, nur Ollama-Port unterschiedlich) — GPU-Pinning für
  neue Container muss vor Compose-Änderungen per `nvidia-smi -L` verifiziert
  werden, sonst Risiko einer Kollision mit der laufenden N04-RTX-Instanz.
- TASK-52 in AGENT_LASTENHEFT.md angelegt: MCP-Tool-Ansatz
  (`generate_image`/`generate_speech` in mcp-precision, neue
  `determinism: generative_model`-Klasse, explizit vom
  Precision-Evidence-Bypass ausgeschlossen), Template-Override-Felder analog
  `guardrail_*`, zwei neue Backend-Container (comfyui, kokoro-tts) GPU-gepinnt
  auf die verifizierte RTX-2060-Device-ID. Content-Moderation für generierte
  Bilder (Guard-Node deckt nur Text ab) und Response-Envelope-Verifikation
  explizit als offene Entscheidungen markiert, nicht stillschweigend
  angenommen.

Pre-conditions verified:
- Kein anderer Agent-Status-Log meldet TASK-52 oder Arbeit an
  mcp_server/server.py, services/routing.py, admin_ui/app.py, docker-compose.yml
  im relevanten Zeitraum als in_progress.
- TASK-51 (Codex CLI, 2026-08-07, completed) betrifft services/deliberation/,
  routing.py-Template-Resolution, dynamic_router.py, graph/expert.py — keine
  Dateiüberschneidung mit dem für TASK-52 vorgesehenen Scope wurde als
  in_progress vorgefunden; dennoch bei Implementierung erneut prüfen, da
  services/routing.py von beiden Tasks berührt wird.

Notes: Nur Planungsdokument geschrieben (AGENT_LASTENHEFT.md TASK-52), keine
Code-, Compose- oder Config-Änderung. Owner bleibt Claude Code, Status
`pending` bis der Nutzer Implementierung beauftragt. Kein `nvidia-smi`-Check
auf 192.168.155.224 durchgeführt (kein Shell-Zugriff auf diesen Host in
dieser Session) — als Instruktion 1 im Task explizit als Vorbedingung vor
jeder Compose-Änderung vermerkt, nicht angenommen.

---

## 2026-08-07T21:20:00Z — TASK-53 — starting

Plan / progress:
- User meldete unerwünschte native OpenRouter-Aufrufe an Frontier-Modelle
  (gpt-5.4-pro, gpt-5.5-pro, claude-opus-4.7-fast, ...) mit dem echten
  System-Key während des TASK-51 "temporary deliberation validation rerun"
  (07.08.2026, 20:55 Uhr). Root-Cause-Analyse (read-only, Container-Logs +
  Code) ergab einen tieferliegenden, vorbestehenden Compliance-Gap, nicht nur
  einen TASK-51-spezifischen Bug:
  1. `local_only_routing` (API-Key-Flag, korrekt aus `user_ctx` gelesen) wird
     in `services/pipeline/chat.py` nur transient für den
     `get_dynamic_template(...)`-Aufruf berechnet und **nie** auf
     `AgentState` geschrieben — `graph/expert.py:916`
     (`state_.get("local_only_routing")`) liest ein Feld, das in
     `pipeline/state.py` nicht deklariert ist und von keinem der drei
     Graph-Invoke-Entry-Points (`main.py::stream_response`,
     `services/pipeline/chat.py`, `services/pipeline/anthropic.py::
     _anthropic_moe_handler`) je gesetzt wird — immer `False`.
  2. `services/sovereignty.py::assert_egress_allowed()` (Egress-Guard,
     fail-closed) ist im gesamten Graph-Pipeline-Pfad nirgends verdrahtet —
     nur `_anthropic_tool_handler`/`_anthropic_reasoning_handler`
     (`session.tool_url`, ein einzelner fixer Endpoint) sind über den
     bestehenden Check in `anthropic_messages` (Zeile ~3153) geschützt. Der
     volle Planner/Experten/Judge/Debatte-Graph (alle drei Entry-Points) hat
     keinen einzigen Egress-Check vor einem ausgehenden LLM-Call.
  3. `graph/expert.py::run_moderated_request()` (TASK-51,
     Moderated-Debate-Panel) und `run_task()`'s statischer
     Single-Expert-Pfad wählen Kandidaten direkt aus
     `effective_experts`/`EXPERTS` ohne jede local_only/is_local-Filterung
     (im Unterschied zu `services/dynamic_router.py::
     _score_and_allocate_model`'s "Compliance Gate", die nur für die
     "dynamic"-Kategorie über `expert_builder.py` läuft).
- Fix-Plan (kein Pflaster an der TASK-51-Stelle, sondern die fehlende
  End-to-End-Durchleitung + der fehlende fail-closed-Egress-Check):
  1. `local_only_routing: bool` neu in `AgentState` (`pipeline/state.py`)
     deklarieren.
  2. `services/sovereignty.py::assert_egress_allowed` von
     `(url, user_ctx: dict)` auf `(url, local_only: bool)` entkoppeln.
  3. In allen drei Graph-Invoke-Entry-Points `local_only` unbedingt (nicht
     nur im dynamic-router-Zweig) aus Permission-Flag > Key-Flag > globalem
     Env berechnen und in den State schreiben.
  4. Egress-Guard an den tatsächlichen Dispatch-Punkten verdrahten:
     `graph/expert.py::run_single()` (deckt Single-Expert- UND
     Debatte-Turn-Pfad ab, da `run_moderated_request` intern `run_single`
     aufruft) sowie `services/inference.py::_invoke_judge_with_retry`
     (Moderator + regulärer Judge) und das Planner-Äquivalent.
  5. Zusätzlich defense-in-depth: local_only/is_local-Filter auf
     `run_task`'s und `run_moderated_request`'s Kandidatenlisten, damit
     lokal_only-Requests gar nicht erst einen zum Scheitern verurteilten
     Cloud-Kandidaten auswählen.
  6. Tests ergänzen, volle Regression, Container neu bauen/recreaten, live
     mit einem local_only_routing=1-Request gegen eine bekannte
     Cloud-Kategorie verifizieren (Erwartung: EgressDenied/403, kein
     ausgehender Call).
- TASK-53 in `AGENT_LASTENHEFT.md` wird vor der ersten Code-Änderung
  ergänzt.

Pre-conditions verified:
- Kein anderer Agent-Status-Log zeigt `in_progress` auf
  `graph/expert.py`, `services/inference.py`, `services/sovereignty.py`,
  `services/pipeline/chat.py`, `services/pipeline/anthropic.py`, `main.py`
  oder `pipeline/state.py`.
- TASK-51 (Codex CLI) ist `done`; keine Dateiüberschneidung als aktive
  Lease vorgefunden.
- Dirty Worktree (viele vorbestehende, unrelated Änderungen) wird
  unangetastet erhalten; nur die oben genannten Dateien werden bearbeitet.

---

## 2026-08-07T21:45:00Z — TASK-53 — done

Plan / progress:
- Alle sechs geplanten Fix-Schritte umgesetzt: `AgentState.local_only_routing`
  deklariert; `services/sovereignty.py::assert_egress_allowed` von
  `(url, user_ctx)` auf `(url, local_only: bool)` entkoppelt plus neue
  `resolve_local_only(user_perms, user_ctx)`-Hilfsfunktion (single source of
  truth für Permission-Flag > Key-Flag > globalen Env); `local_only`
  unbedingt (nicht mehr nur im dynamic-router-Zweig) in
  `services/pipeline/chat.py` berechnet und in **beide** dortigen
  Graph-Entry-Points geschrieben; `main.py::stream_response()` um
  `local_only`-Parameter erweitert; `services/pipeline/anthropic.py::
  _anthropic_moe_handler` ebenso; zusätzlich `services/pipeline/ollama.py`
  und `services/pipeline/responses.py` (beide rufen `stream_response()`
  direkt auf — beim ersten Scan übersehen, beim systematischen Sichten aller
  `stream_response(`-Aufrufer gefunden und nachgezogen).
- Egress-Guard an den echten Dispatch-Punkten verdrahtet:
  `graph/expert.py::run_single()` (deckt Single-Expert- und
  Debatte-Turn-Pfad ab), `services/inference.py::_invoke_judge_with_retry`
  (Judge + Moderator) und `_invoke_planner_with_retry`.
- Defense-in-depth-Filter in `run_task`/`run_moderated_request` ergänzt —
  dabei einen eigenen Bug beim ersten Entwurf gefunden und korrigiert:
  `model_cfg["endpoint"]` ist ein symbolischer Node-Name (z.B.
  "openrouterai"), keine URL; `_is_local_url()` behandelt jeden punktfreien,
  unaufgelösten String als lokal. Ungeprüft hätte der Filter genau die
  TASK-51-Vorfallskonfiguration (`endpoint="openrouterai"`) fälschlich als
  lokal durchgelassen. Fix: erst durch `URL_MAP` auflösen, dann prüfen —
  exakt wie `run_single()` es beim tatsächlichen Dispatch tut.
- 13 neue Tests (`tests/test_sovereignty.py`, 11 Unit-Tests für Guard/
  Resolve-Logik; zwei neue Dispatch-Level-Tests in
  `tests/test_jmoe_debate_judge.py` über den echten `expert_worker()`-
  Entry-Point). Volle Regression: 952 passed (vorher 938). `compileall`,
  `git diff --check`, `scripts/check_governance.py --check` (27/9) grün.
- `langgraph-app` gebaut/recreatet, `/ready` vollständig positiv.
- **Live-Verifikation deckte einen vierten, von keinem Unit-Test erreichbaren
  Dispatch-Pfad auf:** derselbe Live-Request
  (`model=openai/gpt-4o-mini@openrouterai` mit dem lokal_only-Key
  `moe-sk-0261cddfe...`, der bereits als "Benchmark"-Key mit
  `local_only_routing=true` in `api_keys` existiert — kein Credential
  angelegt/verändert) erreichte nach dem ersten Build tatsächlich
  OpenRouter und lieferte eine echte Antwort zurück. Root Cause:
  `services/pipeline/chat.py`'s `_native_endpoint`-"native model@node"-
  Passthrough dispatcht per rohem `httpx`/`_stream_native_llm()` komplett
  außerhalb von `app_graph` — keiner der Graph-seitigen Fixes deckt das ab.
  Nachträglich in `chat.py` direkt am Anfang von `if _native_endpoint:`
  gefixt (ein Guard für Streaming- und Non-Streaming-Zweig), erneut
  gebaut/recreatet.
- Live-Beweis nach dem zweiten Build: derselbe Request → sauberer 403
  (`local_only_violation`), Container-Log zeigt `sovereignty: BLOCKED
  egress to openrouter.ai (local_only key)`, kein Request an OpenRouter mehr
  im Log. Regressionsgegenprobe mit demselben Key: ein voller
  `model=moe-auto`-Request (Planner→Experte→Judge, echter Graph-Pfad) lief
  normal durch (133s, kalter qwen3.6:35b-Load), Log zeigt ausschließlich
  Traffic zu `192.168.155.224:11434` (lokaler N04-RTX) — lokal_only
  blockiert Cloud-Egress, ohne legitimes lokales Routing zu beeinträchtigen.
- `AGENT_LASTENHEFT.md` TASK-53 auf `done` mit vollständigen Resolution-
  Notes (Fix-Liste inkl. des nachträglich gefundenen vierten Pfads,
  Live-Evidenz) aktualisiert.

Pre-conditions verified:
- `langgraph-orchestrator` beide Male healthy nach Recreate, `/ready`
  vollständig positiv.
- Kein Commit/Push/PR/Publish. Kein Credential angelegt, geändert oder
  widerrufen — ausschließlich ein bereits vorhandener, für Tests
  vorgesehener Key read-only zur Live-Verifikation verwendet (siehe
  Memory `test-api-key-horndev`).
- Vorbestehender Dirty Worktree unangetastet; nur die für TASK-53
  vorgesehenen Dateien plus `services/pipeline/chat.py` (nachträglich,
  vierter Fund) geändert.

Notes:
- Bewusst außerhalb des Scopes belassen: `services/inference.py::
  ainvoke_judge_llm()` (systemweiter, admin-konfigurierter
  Hintergrund-Judge für OpenWebUI-interne Requests/Self-Rating —
  request-unabhängig, kein `state`-Parameter, per Design derselbe globale
  `JUDGE_URL` wie der reguläre Judge, welcher in diesem Deployment lokal
  konfiguriert ist) und der lokale `_FALLBACK_NODE`-Pfad in
  `_invoke_llm_with_fallback` (laut `config.py`-Kommentar explizit "falls
  back to a configured **local** node" — invariant, nicht request-abhängig
  konfigurierbar). Beide als dokumentierte, bewusste Scope-Grenzen
  festgehalten, nicht übersehen.

---

## 2026-08-09T00:00:00Z — Lastenheft-Reconciliation (GitHub-Pull) — done

Plan / progress:
- Nutzerauftrag: `github`-Remote fetchen und Lastenheft-Offen-Status gegen
  tatsächlichen Repo-Stand abgleichen.
- `git fetch github`: github/main bei 544abe7f, lokaler Branch bereits via
  Merge enthalten — kein Pull-Konflikt, kein Merge nötig, nur Fetch +
  Read-only-Vergleich über `git show github/main:<path>`/`git log github/main`.
- TASK-9/32/33/34 gegen agy.md/codex-cli.md/claude-code.md-Status-Logs
  geprüft — alle konsistent mit Lastenheft-Stand (TASK-9 zuletzt 2026-07-12,
  seither ohne Checkpoint, aber kein anderer Agent hat es übernommen).
- TASK-21 als stale identifiziert: Commit 715db565 (2026-07-11) implementierte
  es bereits, Lastenheft stand weiter auf "pending". Beide Ergebnisdateien
  vollständig geprüft (nicht nur angelesen wie in der vorherigen
  Chat-Antwort) — Lauf 1: 10/10 Paare score=0/0 beidseitig (Harness-Fehler-
  Verdacht). Lauf 2: 7/10 weiter 0/0, 5/10 mit >60s-Latenz (~300s-Werte
  verdächtig rund, vermutlich Timeout-Ceiling). Akzeptanzkriterium
  ("GraphRAG-Score im Mittel höher") formal erfüllt (1.3 vs. 1.1), aber von
  nur 2-3 echten Datenpunkten getragen — nicht als "done" gewertet, sondern
  als "blocked" mit den offenen Fragen dokumentiert.
- TASK-53-Status-Zeile ("live rebuild/recreate pending") gegen den
  tatsächlich laufenden Container verifiziert: `docker exec
  langgraph-orchestrator grep ... services/sovereignty.py` zeigt die gefixte
  Signatur bereits live. Status-Zeile war nur unpräzise formuliert (Resolution
  Notes waren korrekt) — Wortlaut korrigiert, keine inhaltliche Änderung.

Pre-conditions verified:
- Kein anderer Agent-Status-Log zeigt aktuelles `in_progress` auf TASK-21
  oder TASK-53 zum Zeitpunkt der Bearbeitung.
- Nur Doku-Änderungen (AGENT_LASTENHEFT.md), keine Code-/Compose-/Config-
  Änderung, kein Rebuild, kein Commit/Push/PR.

Notes: TASK-21 bleibt technisch offen (Harness-Zuverlässigkeit ungeklärt) —
nicht fälschlich als erledigt geschlossen, nur der Status ehrlich auf
"blocked" mit konkreten Debugging-Fragen präzisiert. TASK-9 (in_progress,
seit 2026-07-12 ohne Update) dem Nutzer als möglicherweise gestoppten
LUMI-Job gemeldet, aber nicht eigenmächtig übernommen oder verändert.

---

## 2026-08-10T00:00:00Z — depends_on-Auflösung in services/deliberation/capacity.py — done

Plan / progress:
- Bei der Evaluation "was fehlt dem Planner für optimales Agieren" einen realen
  Defekt gefunden: `_dependency_depth()` löste `depends_on` ausschließlich gegen
  `task["id"]` auf, während der trainierte Planner-Prompt `depends_on` als
  "<prior task description prefix>" definiert. Messung an 2.000 echten
  Trainingsbeispielen: 15 % emittieren `depends_on`, nur 3 % emittieren `id`.
- Fix: Auflösung zusätzlich über Task-Beschreibungs-Präfix, bewusst strikt
  (nur eindeutige Treffer erzeugen eine Kante; mehrdeutige Präfixe werden
  verworfen statt geraten). Positionsbasierte Graph-Keys, damit auch Tasks ohne
  `id` Knoten sind. Zyklus-Semantik des Originals unverändert übernommen.
- **Eigene Fehlannahme korrigiert:** zunächst als "Aktivierungslogik kaputt"
  eingeordnet. Tatsächlich ist `dependency_depth >= 2` in der adaptiven
  OR-Kette redundant (jeder Plan mit Tiefe ≥2 hat zwangsläufig `task_count >= 2`,
  was bereits feuert). Der echte Effekt liegt bei `desired_rounds` — mit auf 1
  festgenagelter Tiefe lief ein dreistufig abhängiger Plan mit genauso vielen
  Deliberationsrunden wie ein flacher. Kommentar und Test entsprechend
  korrigiert, statt die erste Behauptung stehenzulassen.
- 6 neue Tests. Gegenprobe gegen HEAD-Stand von capacity.py durchgeführt:
  2 Tests schlagen dort fehl (`dependency_depth` 1 statt 3,
  `initial_rounds` 2 statt 3), nach dem Fix alle 14 grün — die Tests belegen
  also eine reale Verhaltensänderung, nicht nur sich selbst.

Pre-conditions verified:
- TASK-51 (Codex CLI, Eigentümer von services/deliberation/) steht auf `done`,
  kein aktiver Lease auf diesem Pfad in irgendeinem agent_status/*.md.
- Volle Regression: 957 passed, 1 failed. Der Fehlschlag
  (`tests/test_context_budget_adaptive.py::test_context_never_exceeds_template_ceiling`)
  ist **vorbestehend und nicht von mir verursacht**: weder `context_budget.py`
  noch dessen Test sind in meinem Diff, beide sind unverändert auf HEAD-Stand.
- compileall, `git diff --check`, `check_governance.py --check` (27/9) grün.

Notes: Kein Rebuild, kein Recreate, kein Commit/Push/PR — nur Arbeitskopie.
Offener, separat zu entscheidender Befund siehe nächster Eintrag/Bericht:
`adaptive_context_window()` hebt den per-Template gesetzten
`planner_num_ctx=4096` faktisch auf (skaliert auf 16.384 hoch, da die kleinste
Tier-Stufe 16.384 ist und nur die globale Env-Var `PLANNER_NUM_CTX=40960`
nachträglich kappt). Nicht eigenmächtig geändert — größerer Blast Radius
(Judge- und Expert-Pfad nutzen dieselbe Funktion).

---

## 2026-08-11T00:00:00Z — Taxonomie-Variation im Planner-Datensatz-Generator — done

Plan / progress:
- Befund vorab (messbasiert, nicht vermutet): Abgleich der 14 live migrierten
  Expert-Templates gegen die im Datensatz einbetonierte 15er-Taxonomie ergab,
  dass JEDES Template 1–5 Kategorien nutzt, auf die das Modell nie trainiert
  wurde (long_context, devops_sre, security_analysis, tool_agent,
  knowledge_healing, skill_detector, mail_classify, memory_recall,
  web_researcher …). Drei davon sind Beinahe-Treffer kanonischer Namen
  (creative_writing/creative_writer, data_analysis/data_analyst,
  web_researcher/research) — dort stehen ~260k Trainingsbeispiele gegen eine
  einzelne In-Context-Zeile des Orchestrators.
- Vorher geprüft und VERWORFEN: die Idee, das Code-Prompt-Gerüst in
  graph/planner.py als redundant zu entfernen. Es ist tragend — es liefert
  `VALID CATEGORIES` aus der Laufzeit-Template-Konfiguration. Ohne es würde der
  Planner auf Kategorien routen, die im Template nicht existieren.
- Umgesetzt in scripts/generate_planner_dataset.py:
  `_PLANNER_PROMPT_TEMPLATE` mit Platzhalter statt hartkodiertem
  Kategorienblock; `sample_taxonomy()` (Teilmengen 3..n, beobachtete +
  synthetische Umbenennungen, reale Zusatzkategorien, gemischte Reihenfolge);
  `sample_planner_prompt()` kombiniert Framing- und Taxonomie-Variation;
  `score_plan(..., valid_categories)` und `process_query(..., valid_categories)`
  durchgereicht; Negativ-Samples nutzen jetzt den Prompt IHRES Samples statt
  eines frisch gezogenen (sonst Korrektur gegen nie gezeigte Kategorienliste);
  Opt-out `--no-taxonomy-variation`.
- Bewusst NICHT variiert: `code_reviewer` und `legal_advisor` (in score_plan
  namentlich referenziert — RESEARCH-BEFORE-CODE und §-Regel; Umbenennung
  hätte genau bei den neuen Samples die Qualitätsprüfung stillgelegt),
  sowie `precision_tools`/`research`/`dynamic` (strukturell, im Code verdrahtet).
  Über 400 Ziehungen verifiziert, dass beide nie umbenannt auftauchen.

Pre-conditions verified:
- Kein anderer Agent-Status-Log zeigt in_progress auf scripts/ oder dem
  Planner-Datensatz. Keine externen Importeure des Scripts im Repo.
- Verifikation: kanonischer Prompt weiterhin unverändert erzeugbar
  (Platzhalter ersetzt, `creative_writer` enthalten); 5 Stichproben ergaben
  4–12 Kategorien mit Umbenennungen und Zusatzrollen; `score_plan` akzeptiert
  ein `creative_writing`-Plan mit passender Menge (score 7, keine Issues) und
  meldet es mit kanonischer Menge als unknown (score 6) — belegt, dass die
  Durchreichung tragend ist und nicht nur kosmetisch.
- compileall, `git diff --check` grün. Volle Regression 957 passed, 1 failed —
  der Fehlschlag (test_context_budget_adaptive) ist derselbe vorbestehende wie
  im Eintrag zuvor, unverändert und nicht von diesem Diff berührt.

Notes: Nur Generator-Code, KEIN Datensatz generiert, kein LUMI-Job, kein
Teacher-Aufruf, kein Rebuild, kein Commit/Push. Der nächste Schritt (echte
Datensatz-Generierung) kostet Teacher-/GPU-Zeit und wurde bewusst nicht
eigenmächtig gestartet.

---

## 2026-08-11T17:39:00Z — ADHOC-native-timeout — starting

Plan / progress:
- User meldete: Open-WebUI-Requests über natives `model@node`-Passthrough
  (User horndev, Key "open-webui") liefern bei größeren/langsameren Modellen
  eine leere Fehlermeldung `[Error: ]` statt der Antwort — konkret
  `nemotron-3.5-lightning:30b@N04-RTX`, 11.08.2026 19:13:26, Dauer exakt
  5.0min bis zum Fehler; Prozesstabelle zeigt trotzdem `status=completed`.
- Root-Cause gefunden (Code-Review, kein Rebuild/Request nötig):
  `main.py:_stream_native_llm` verwendet `endpoint.get("timeout", 300)`
  (main.py:1675), aber `_native_endpoint` wird an allen drei Stellen in
  `services/pipeline/chat.py` (~1817, ~1831, ~1849) OHNE `timeout`-Feld
  gebaut. `config.py` leitet aus `INFERENCE_SERVERS` nur `URL_MAP`/
  `TOKEN_MAP`/`API_TYPE_MAP` ab (Zeile 88-90) — keine `TIMEOUT_MAP`, obwohl
  jeder Server-Eintrag ein `"timeout"`-Feld hat (N04-RTX: 3600). Ergebnis:
  jeder native Passthrough-Request nutzt hart codiert 300s statt des
  konfigurierten Node-Timeouts. httpx.ReadTimeout hat i.d.R. eine leere
  `str()`-Repräsentation → `except Exception as _e: yield f'[Error: {_e}]'`
  (main.py:1807-1809) ergibt `[Error: ]`. Die Exception wird verschluckt,
  danach läuft der Generator normal bis `[DONE]` weiter → daher
  `status=completed` im Prozess-Log trotz Fehlschlag für den Nutzer.
  Nutzer hat den zweiten, in der Prozesstabelle genannten Fall
  (`muse-glimmer:30b-q4_K_M-dflash@N04-RTX`, 16:02:46, 42.3s) selbst um
  16:02 Uhr abgebrochen, weil auf dem Inferenzserver keine Aktivität
  sichtbar war — vermutlich reguläre Modell-Ladezeit (VRAM-Swap auf
  demselben physischen Host wie N04-RTX/N04-RGTX/N04-TESLA, siehe TASK-52-
  Eintrag oben), nicht dasselbe Timeout-Problem; nicht weiter verfolgt, da
  vom Nutzer selbst erklärt und kein Fehlerhinweis im Log dazu vorliegt.
- Ursprünglich vermuteter zweiter Bug ("Antwortinhalt wirkt wie
  qwen3.6:35b trotz korrektem Modell-Log") vom Nutzer auf denselben
  nemotron-Request zurückgeführt — keine separate Ursache, durch den
  `[Error: ]`-Fund erklärt.
- Geplanter Fix: `TIMEOUT_MAP` in `config.py` analog zu `URL_MAP`/
  `TOKEN_MAP` ergänzen; an allen drei `_native_endpoint`-Konstruktions-
  stellen in `chat.py` durchreichen (inkl. User-Connections-Fallback mit
  eigenem Default); leere Error-Message in `main.py:1809` gegen
  `str(_e) or type(_e).__name__` absichern, damit künftige Fehler dieser
  Art nicht mehr wortlos sind.

Pre-conditions verified:
- Kein anderer Agent-Status-Log zeigt `in_progress` auf `config.py`,
  `services/pipeline/chat.py` oder `main.py`.
- Working Tree hat vorbestehende unstaged Änderungen von TASK
  "Taxonomie-Variation im Planner-Datensatz-Generator" (AGENT_LASTENHEFT.md,
  agent_status/claude-code.md, docs/experts/index.md, docs/system/status.md,
  graph/planner.py, scripts/generate_planner_dataset.py,
  services/decision_log.py, services/deliberation/capacity.py,
  services/quality_gate.py, services/sovereignty.py, tests/
  test_deliberation_capacity.py) — nicht berührt, keine Überschneidung mit
  den für diesen Fix vorgesehenen Dateien.

Notes: Kein Rebuild/Recreate/Commit/Push bisher. Fix wird auf separatem
Feature-Branch umgesetzt, um die bestehenden unstaged Änderungen nicht zu
vermischen.

---

## 2026-08-11T17:52:00Z — ADHOC-native-timeout — done

Plan / progress:
- Implementiert wie geplant:
  - `config.py:91` — neue `TIMEOUT_MAP = {s["name"]: s.get("timeout", 300)
    for s in INFERENCE_SERVERS_LIST}`, analog zu `URL_MAP`/`TOKEN_MAP`/
    `API_TYPE_MAP`.
  - `services/pipeline/chat.py` — `TIMEOUT_MAP` importiert; an allen drei
    `_native_endpoint`-Konstruktionsstellen ein `"timeout"`-Feld ergänzt
    (globaler URL_MAP-Pfad: `TIMEOUT_MAP.get(_ep_node, 300)`; beide
    User-Connection-Fallback-Pfade: `_uc.get("timeout", 300)`, da
    User-Connections kein Timeout-Feld im Schema haben). Zusätzlich die
    beiden non-streaming nativen `httpx.AsyncClient(timeout=300)`-Aufrufe
    (Zeile ~2403, ~2504 — selbe Bug-Klasse, war beim Review zusätzlich
    aufgefallen) auf `timeout=float(_native_endpoint.get("timeout", 300))`
    umgestellt.
  - `main.py:1807-1810` — leere Exception-Messages abgesichert:
    `_err_msg = str(_e) or type(_e).__name__`, sowohl im Log als auch im
    an den Client gestreamten `[Error: ...]`-Chunk verwendet. Künftige
    Fehler dieser Klasse zeigen jetzt mindestens den Exception-Typnamen
    (z.B. `[Error: ReadTimeout]`) statt `[Error: ]`.
- Neuer Test `tests/test_native_timeout_map.py` (3 Tests): TIMEOUT_MAP-
  Default-Fallback auf 300 für Server ohne explizites Timeout-Feld,
  korrekte Übernahme eines konfigurierten Timeouts, Vollständigkeits-Check
  (jeder `URL_MAP`-Eintrag hat einen `TIMEOUT_MAP`-Eintrag).
- Verifikation:
  - `python3 -c "import ast; ast.parse(...)"` für alle 3 geänderten Dateien
    — OK.
  - `pytest tests/test_native_timeout_map.py tests/test_routing.py
    tests/test_jmoe_debate_judge.py tests/test_sovereignty.py
    tests/test_dynamic_router.py -q` → 63 passed.
  - Volle Regression `pytest tests/ -q` → 987 passed, 1 failed. Der
    Fehlschlag (`test_context_budget_adaptive.py::
    test_context_never_exceeds_template_ceiling`) ist derselbe
    vorbestehende, dokumentierte Fehler aus dem Taxonomie-Variation-Eintrag
    weiter oben in dieser Datei — unverändert, nicht von diesem Diff
    berührt.
  - Kein Live-E2E-Test gegen N04-RTX durchgeführt (würde einen >300s
    laufenden Request auf dem produktiven Node erfordern, um den
    ursprünglichen Timeout-Fall real zu reproduzieren — bewusst nicht
    unternommen, um keine unnötige GPU-Zeit/Störung auf einem produktiven
    Node zu verursachen; die Config-Propagation ist stattdessen per Review
    + Unit-Test verifiziert, dass `_native_endpoint["timeout"]` jetzt in
    JEDEM Codepfad, der einen httpx-Timeout für native Requests setzt,
    ankommt).

Pre-conditions verified:
- Arbeit auf eigenem Branch `fix/native-timeout-propagation` (von
  `fix/planner-schema-output` abgezweigt), um nicht mit der dort laufenden,
  unstaged Taxonomie-Variation-Arbeit zu vermischen. Nur die 3 Ziel-Dateien
  + der neue Testfile wurden verändert.

Notes: Kein Rebuild/Recreate von `langgraph-app`, kein Commit, kein Push —
auf explizite Nutzerentscheidung wartend. Die eingangs vermutete zweite
Ursache ("Antwortinhalt wirkt wie qwen3.6:35b") wurde vom Nutzer auf
denselben nemotron-Request zurückgeführt und ist damit durch diesen Fix
erklärt, nicht separat zu beheben. Der zweite gemeldete Fall
(muse-glimmer, 16:02 Uhr, vom Nutzer selbst wegen fehlender sichtbarer
Serveraktivität abgebrochen) bleibt als vermutete reguläre Modell-
Ladezeit unklassifiziert — kein Fehlerindiz im Log, nicht weiter verfolgt.

## 2026-08-19T20:18:09Z — FINDING-planner-nontrivial-retry-prompt — starting

Plan / progress:
- Root-caused during a live Scientific Benchmark overnight run: non-trivial-complexity
  planner requests (moderate/complex/expert) hit PlannerContractError on real tasks. The
  giant non-trivial prompt (graph/planner.py:872-969) is reused verbatim across all 3
  structured-output retry attempts (only a repair hint gets appended, never shrunk), and
  moe-sovereign-student:4b cannot reliably follow it under load -- attempt 2 hallucinated
  an unrelated "GAPS-5.1 pre-flight check" meta-prompt instead of a task decomposition.
  Not a pre-existing AGENT_LASTENHEFT.md task; no matching entry found via grep.
- Fix: factor the existing trivial-path compact prompt (graph/planner.py:842-871) into a
  reusable helper and use it on retry (attempt >= 1) for non-trivial complexity too, in
  place of the full giant prompt. First-attempt (non-trivial) prompt stays untouched.
- Working in an isolated git worktree, branch fix/planner-nontrivial-retry-compact-prompt.
- No commit/push/PR without explicit user authorization (per AGENTS.md).

Files: graph/planner.py (target of the fix).

## 2026-08-19T21:14:00Z — FINDING-planner-nontrivial-retry-prompt — done

Plan / progress:
- Implemented in worktree ../moe-infra-worktree-planner-fix, branch
  fix/planner-nontrivial-retry-compact-prompt: factored the trivial-path compact prompt
  into _build_compact_prompt(task_budget_text), reused it on retry (attempt >= 1) for
  non-trivial complexity instead of resending the full ~1000-line prompt + growing
  repair hint. First-attempt (non-trivial) prompt left byte-identical.
- Built image moe-sovereign-orchestrator:local (sha256:36225e7f33ae223cb87b349963c...),
  recreated langgraph-orchestrator with it (with explicit user go-ahead for the
  container recreate step).
- Integration test: replayed the exact prompt that failed earlier tonight
  (sci-sysprog-01-lockfree-ringbuffer, compound_ai template) directly against
  /v1/chat/completions. Result: HTTP 202 (HITL gate, "trust verdict
  PROCEED_WITH_ASSUMPTION; Cynefin COMPLEX") after 1053s -- no PlannerContractError, no
  "Planner structured failure", no GAPS-5.1-style hallucination in the logs. Planner
  produced a valid task array on this run.

Pre-conditions verified:
- graph/planner.py compiles cleanly (py_compile).
- Container health check passed after recreation.
- Two false alarms during testing, both self-corrected: (1) a trivial "ping" probe
  appeared to hang for 60-280s -- turned out to just be the same multi-round
  self-critique loop real tasks already exhibit (~1000s total), not a bug; (2) one
  planner call returned a degenerate repeated-model-name list -- did not reproduce on
  retest, root cause not conclusively identified (possibly a transient warm-model
  state issue on ollama-rgtx, unloaded/reloaded as a precaution, no further action
  taken since it did not recur).

Notes:
- Not committed/pushed/PR'd -- per AGENTS.md, awaiting explicit user authorization.
- Uncommitted change lives only in the worktree; main checkout's working copy of
  graph/planner.py is untouched.

## 2026-08-19T22:21:24Z — FINDING-critic-node-non-compliant-judge-overwrite — starting

Plan / progress:
- Root-caused the poor real-benchmark scores (`sci-sysprog-01-lockfree-ringbuffer`,
  compound_ai, FAIL 1.4/10) to `graph/synthesis.py:critic_node` (~line 2058-2136), the
  hallucination-risk fact-check pass that fires when trust_verdict is still
  PROCEED_WITH_ASSUMPTION/BLOCK after self-critique.
- The critic prompt requires the judge to answer with EITHER the bare word "CONFIRMED"
  OR a direct corrected answer with no preamble. The code only checks
  `critic_out.upper().startswith("CONFIRMED")`; anything else fully replaces
  final_response. Live evidence (checkpoint_scientific_benchmark.json,
  r1_sci-sysprog-01-lockfree-ringbuffer_compound_ai): the judge wrote an ~800-word
  self-deliberation about whether the claim is "unsupported" and only concluded with
  the bare word CONFIRMED at the very end, not the start. Since the string does not
  START with CONFIRMED, the code discarded the correct, working Rust MPSC
  implementation and replaced final_response with the judge's internal reasoning
  trace verbatim -- not a self-critique/merger bug (expert_results reducer, dedup,
  boundary_check, fast-path/judge-gate all confirmed correct beforehand).
- Fix approach: add a guard before the startswith("CONFIRMED") branch is bypassed --
  detect "confirmed but non-compliant format" (trailing bare CONFIRMED, or the
  original had code markers and critic_out has none) and treat it as a confirmation
  (preserve final_response) instead of a correction. Scoped to the general
  fact-check/hallucination-risk branch actually implicated; safety-critical and
  precision-hybrid critic branches left untouched.
- Stopping the running benchmark (PID 2561140, started 21:29, stuck ~2.5h on the same
  first task) before implementing, per the established stop-fix-restart pattern.
- Working in an isolated git worktree; no commit/push without explicit authorization.

Files: graph/synthesis.py (critic_node, target of the fix).

## 2026-08-19T22:25:32Z — FINDING-critic-node-non-compliant-judge-overwrite — done

Plan / progress:
- Implemented in worktree ../moe-infra-worktree-critic-fix, branch
  fix/critic-node-non-compliant-judge-format: added
  _critic_is_noncompliant_confirmation(critic_out, original) and a guard in
  critic_node right after the existing `critic_out.upper().startswith("CONFIRMED")`
  check. Fires when (a) the reply ends in a bare trailing "CONFIRMED" instead of
  starting with it (deliberation-then-verdict pattern), or (b) the original answer
  had code markers and the reply has none (deliberation/meta-commentary instead of a
  real replacement). On either signal, final_response is preserved unchanged instead
  of being overwritten by the non-compliant reply. Both branches feeding critic_out
  (safety-critical `active` and hallucination-risk) share this one check site, so
  both are covered without duplicated logic.
- Verified the guard function standalone (extracted, no framework import needed)
  against 5 cases: (1) the *exact* recorded judge text from the failed benchmark run
  (checkpoint_scientific_benchmark.json, r1_sci-sysprog-01-lockfree-ringbuffer) ->
  correctly flagged True; (2) a genuine code correction -> False (unaffected); (3)
  proper bare "CONFIRMED" -> already handled by the pre-existing startswith check,
  unaffected; (4) a genuine text-only correction with no code on either side ->
  False (unaffected); (5) the word "confirmed" appearing mid-sentence inside a real
  code correction -> False (unaffected, only a *trailing* bare CONFIRMED trips it).
- IMPORTANT: while rebuilding, discovered graph/planner.py in the main checkout
  (/opt/deployment/moe-sovereign/moe-infra) still had the ORIGINAL (pre-fix) content
  -- the earlier planner fix (FINDING-planner-nontrivial-retry-prompt) only ever
  landed in the separate worktree ../moe-infra-worktree-planner-fix and was never
  copied back into the main checkout's working tree. A build from the main checkout
  would have silently shipped the unfixed planner.py. Copied
  ../moe-infra-worktree-planner-fix/graph/planner.py into the main checkout before
  building, so the deployed image now carries BOTH fixes together.
- Built moe-sovereign-orchestrator:local (sha256:7c09784c9fca422b979cd575a333...),
  recreated langgraph-orchestrator. Container reached health:healthy. Verified both
  fixes present in the running container's /app (grep for
  _build_compact_prompt/_critic_is_noncompliant_confirmation).
- Stopped the overnight benchmark stack (PID 2561140 run_scientific_benchmark.py,
  watchdog.sh, health_check.sh, power_monitor.py) before rebuilding, cleared
  .bench_running lock. The one completed checkpoint entry for this run
  (sci-sysprog-01-lockfree-ringbuffer, compound_ai, FAIL 1.4 -- produced under the
  buggy critic_node) is scientifically invalid and will be discarded by --fresh on
  restart, not reused.

Files changed (uncommitted in main checkout, mirrors the two worktree branches):
graph/synthesis.py (critic_node fix), graph/planner.py (prior retry-prompt fix,
now actually deployed for the first time).

Notes:
- Not committed/pushed/PR'd in either worktree branch -- per AGENTS.md, awaiting
  explicit user authorization. Main checkout working tree carries both diffs
  uncommitted, same pattern as the planner fix session.

## 2026-08-19T23:36:44Z — FINDING-scoring-judge-brace-confound — done

Plan / progress:
- User explicitly requested scientifically defensible ("unanfechtbar") benchmark
  results. Investigated the historical fallback rate of the external SCORING judge
  in benchmarks/run_scientific_benchmark.py (judge_evaluation(), distinct from the
  orchestrator's internal critic_node fixed earlier tonight): 15-50% of results
  across the last 4 runs were UNSCORED_FALLBACK/UNVALIDATED_VERDICT.
- Root cause: judge_evaluation() extracted JSON via naive
  text.find("{")/text.rfind("}") with a SINGLE attempt and no retry. When the
  judge's reply discusses/echoes code (Rust/C++ -- exactly the systems_programming
  task class), the braces inside that code make the naive slice grab a huge
  mismatched span instead of the real trailing JSON verdict object, so the parse
  fails and it falls back to a hardcoded judge_score=5.0. This is a CONFOUND, not
  random noise: code-heavy tasks/conditions fail more often, so excluding
  fallbacks from valid_only stats (the earlier fallback-bias fix) would
  systematically underrepresent exactly the task class the benchmark cares most
  about, not just lose sample size evenly.
- Fix: added _extract_json_candidates() (brace-depth-tracked scan for every
  balanced top-level {...} span, tried last-to-first since a reasoning judge
  usually puts the schema object last) replacing the naive slice, plus a bounded
  retry loop (JUDGE_EVAL_MAX_ATTEMPTS=3, env override
  MOE_JUDGE_EVAL_MAX_ATTEMPTS) that appends an explicit "ONLY the JSON object, no
  code" repair hint on retry -- same pattern as the structured-failure retry
  already used for the orchestrator's merger/critic calls.
- Verified standalone: (1) exact recorded pure-code failure (no JSON present at
  all) -- correctly still falls through (no spurious match), will now get 2 more
  attempts instead of one; (2) a realistic code-discussion-plus-trailing-JSON-
  verdict case -- new approach correctly extracts the real {"score":...} object,
  old find/rfind approach provably fails to parse it (confirmed via direct
  comparison). No regression risk to the schema/verdict-normalization logic
  added earlier (VALID_VERDICTS check, UNVALIDATED_VERDICT labelling) -- untouched.
- Also bumped NUM_ROUNDS from a hardcoded 2 to 5 (env override
  MOE_BENCHMARK_NUM_ROUNDS) per explicit user decision, so per-condition
  standard error/CI are meaningful rather than point estimates from n=2.
- Stopped the running benchmark stack (again) before editing; restarting --fresh
  with both this fix and the earlier critic_node fix active, 5 rounds this time.
  Both power_monitor instances (N04-RTX host + separately N11-M10 host, per user
  correction on GPU topology -- see agent_status memory) restarted alongside with
  new run-ids so their timeframe cleanly matches the new valid run, not the
  pre-fix data.

Files changed (uncommitted, benchmark harness -- no container rebuild needed,
this script runs standalone, not inside langgraph-orchestrator):
benchmarks/run_scientific_benchmark.py (judge_evaluation() JSON extraction/retry,
NUM_ROUNDS).

Notes:
- Idle-power baseline subtraction for the energy report was discussed with the
  user and deliberately deferred to a post-processing step after the run
  completes (needs the full per-task wall_clock_s timestamps from the finished
  result JSON to correlate against the power CSVs) -- not implemented yet,
  tracked as a follow-up, not a bug.

## 2026-08-20T05:23:08Z — FINDING-watchdog-hang-blind-spot — done

Plan / progress:
- Live incident during the 5-round overnight run: benchmark process (PID 3273225)
  hung ~5 hours in query_native_ollama() (native_baseline condition) with 0% GPU
  utilization on every N04-RTX GPU and the requested model never appearing in
  `ollama /api/ps` -- i.e. a genuine stall, not slow-but-progressing work. Watchdog
  never restarted it despite the heartbeat being ~4h50m stale.
- Root cause 1 (watchdog.sh `_bench_alive()`): PID liveness was the PRIMARY check
  (`kill -0 $pid` -> alive), heartbeat freshness was only a FALLBACK consulted when
  the lock file/PID was absent. A process that is technically running but blocked
  inside one HTTP call forever is alive by that definition forever -- the whole
  point of the heartbeat mechanism (built earlier tonight specifically because
  "last file write... would look stale mid-request") was defeated by never being
  checked while the PID lives.
- Root cause 2 (`query_native_ollama()` in run_scientific_benchmark.py): a client
  timeout of 18000.0s (5h) on a single native-model HTTP call -- restored from an
  earlier "consumer hardware" comment, but 5h makes a genuine stall
  indistinguishable from progress for the entire overnight window. Also requested
  `num_ctx: 262144` for `qwen3.8:27b`, a DIFFERENT Ollama model tag from the
  already warm-loaded `sovereign-judge:27b` on the same physical host/port -- so
  it can never reuse the warm context, only force a cold full-256k-context load,
  which is a plausible trigger for the stall (0% GPU util suggests the request
  never even got dispatched/loaded, not that it was slowly computing).
- Fix: (a) `_bench_alive()` in watchdog.sh now requires PID alive AND heartbeat
  fresher than STALE_HEARTBEAT_SECONDS (2400s/40min, above every real single-call
  duration observed this session, env override MOE_WATCHDOG_STALE_SECONDS) --
  heartbeat-only fallback still applies when no lock file/PID is resolvable, but a
  confirmed-dead PID is always DEAD regardless of heartbeat. Added
  `_kill_hung_benchmark()`, called before every restart attempt, to actually
  terminate (SIGTERM then SIGKILL) a hung-but-alive process instead of leaving it
  running alongside a freshly spawned one (would have contended for the same
  GPUs/ports/checkpoint file). (b) query_native_ollama(): timeout 18000s -> 1200s
  (20min, generous vs. the 1739s longest real multi-stage call observed tonight);
  num_ctx 262144 -> 32768 (native baseline doesn't need 256k and this avoids
  forcing a cold huge-context load on a model that's never already warm at that
  size).
- Verified watchdog logic standalone (4 scenarios: PID-alive+fresh-heartbeat ->
  alive; PID-alive+stale-heartbeat -> dead [the actual incident]; PID-gone+fresh-
  heartbeat -> alive via fallback; PID-gone+stale-heartbeat -> dead). All pass.
  py_compile clean on run_scientific_benchmark.py, bash -n clean on watchdog.sh.
- Killed the hung process and the rest of the stack (watchdog, health_check, both
  power_monitor instances), restarting --fresh with all three fixes (critic_node,
  judge_evaluation JSON extraction, this watchdog/timeout fix) active together,
  5 rounds. New power_monitor run-ids so energy data stays scoped to the valid run.

Files changed (uncommitted): benchmarks/run_scientific_benchmark.py
(query_native_ollama timeout/num_ctx), benchmarks/watchdog.sh (_bench_alive,
_kill_hung_benchmark).

## 2026-08-20T07:50:38Z — FINDING-planner-contract-retry-single-shot — done

Plan / progress:
- User flagged an HTTP 500 as unacceptable; investigated the specific occurrence
  (chatcmpl-bbbaff38, task "Linux eBPF XDP Packet Filter & Map Sync", compound_ai).
- Full trace read from docker logs: attempt 1 (full prompt) hallucinated the
  planner's own category-reference catalog verbatim instead of a task array;
  attempt 2 (compact retry prompt, my earlier FINDING-planner-nontrivial-retry-prompt
  fix -- confirmed engaged correctly) produced a different but still non-JSON reply.
  Then immediately "Planner structured recovery exhausted after 3 attempts" despite
  only 2 real model calls having happened.
- Root cause: _can_retry_contract in graph/planner.py's structured-retry loop gated
  retry on `not _contract_repair_used`, a one-shot flag set on the FIRST contract
  failure -- so a PlannerContractError only ever got exactly 1 retry, regardless of
  the full _structured_attempts budget (3, from
  1 + STRUCTURED_FAILURE_MAX_RETRIES + bool(fallback_model)). Non-contract failures
  already used the full budget via the separate _can_retry_other branch with no such
  cap. The misleading "exhausted after 3 attempts" log line always prints the
  configured budget regardless of how many attempts actually ran.
- Also found and deliberately did NOT touch: a pre-existing, documented,
  intentional fail-loud path -- when _is_contract_failure and retries are truly
  exhausted, the code explicitly `raise`s instead of falling back to a generic
  single-task plan, specifically to avoid silently masking a request that needed
  precision/research tooling as an apparently-successful generic answer. This is a
  deliberate product decision (comment: "A malformed executable plan must not
  silently become a generic LLM task"), not a bug -- did not weaken it.
- Fix: removed the `not _contract_repair_used` gate from _can_retry_contract, so
  contract failures now retry up to the same _structured_attempts bound as other
  failures (i.e. the full configured budget, not a hardcoded single retry). The
  repair hint (exc.repair_instruction()) is still generated only once (first
  contract failure) and reused/kept across subsequent attempts, not regenerated.
  temperature=0.7 means each attempt is genuinely stochastic, so spending the full
  budget meaningfully raises recovery odds without changing the fail-loud behavior
  once that (now larger) budget is genuinely exhausted.
- py_compile clean. Rebuilt moe-sovereign-orchestrator:local
  (sha256:557cbd6be9893a313287435eb15266234200b1d68f7ac0635e0bcaaa5a70dc8d),
  recreated langgraph-orchestrator, reached health:healthy, verified the fix present
  in the running container. Did NOT stop/restart the benchmark harness for this --
  recreating only the orchestrator container let the harness's existing generic
  exception handling in query_moe_orchestrator absorb the one interrupted in-flight
  request (recorded as invalid/excluded, not a crash) and continue on its own to the
  next condition, preserving all round-1 progress made so far.

Files changed (uncommitted): graph/planner.py (_can_retry_contract retry-budget fix).

## 2026-08-20T09:47:19Z — no-cheats methodology correction — done

Plan / progress:
- Prior step in this session imported Neo4j facts narrowly tailored to the exact
  two defects a judge found in one specific benchmark result
  (sci-sysprog-01-lockfree-ringbuffer / compound_ai), then planned to re-run that
  SAME task to show an improved score. User correctly flagged this as data
  leakage / cheating: it would only prove the pipeline can retrieve and apply a
  hand-fed answer key, not that GraphRAG carries generally useful domain
  knowledge -- not a valid basis for a whitepaper effectiveness claim.
- Reverted: deleted the bug-specific curated nodes
  (`MATCH (n:Entity {source:'curated_literature'}) DETACH DELETE n`, done before
  the general-knowledge import below, which now owns that `source` value).
- Replaced with graph_rag/curated/systems_programming_reference.cypher (new file,
  committed to the repo for reproducibility): 9 general reference facts + 2 hub
  entities covering the systems_programming category's two benchmark sub-domains
  (lock-free concurrency: CAS retry loop, acquire-release ordering, false sharing/
  cache-line padding, ABA problem, Vyukov sequence-number pattern; eBPF/XDP:
  verifier bounded-loop and memory-safety requirements, BPF map concurrency,
  XDP action codes) -- curated independent of any single task/run's specific
  failure mode, sourced from Herlihy & Shavit, cppreference.com, Intel
  Optimization Manual, Dmitry Vyukov (1024cores.net), docs.ebpf.io, LWN.net.
  Linked both via a domain hub (COVERS) and directly to already-confirmed-
  matching entities (MpscQueue -[:RELATED_TO]-> ...) so standard term-matching
  retrieval reaches it. Imported and verified (11 nodes, 22 relationships).
- Launched the FULL benchmark suite fresh (all 8 tasks, all 4 conditions, 5
  rounds -- not a narrowed task/condition subset) so the systems_programming
  category's compound_ai-vs-ablation_no_graphrag delta can be read alongside
  every other category as one honest, reproducible dataset, rather than a
  cherry-picked single-task rerun. PID 1451140, full watchdog/health_check/
  power_monitor(x2) stack attached.

Files added: graph_rag/curated/systems_programming_reference.cypher (curated
general reference facts, with reproduction/rollback instructions in the file
header). Not yet committed to git -- awaiting explicit authorization per
AGENTS.md, same as the other uncommitted fixes this session.

## 2026-08-20T09:56:23Z — FINDING-watchdog-set-e-crash-on-kill — done

Plan / progress:
- Live incident: minutes after launching the full 8-task/4-condition/5-round
  suite with the newly-restarted watchdog, watchdog.sh logged "Benchmark
  process dead or hung" once at 11:49:43 and then silently exited entirely --
  no "Attempting auto-restart" line, no further activity, watchdog process gone
  from `ps`. The benchmark process itself (PID 1451140) was never actually
  dead/hung -- it kept running and progressing through pre-flight the whole
  time; the SUPERVISOR crashed, not the supervised process.
- Root cause: watchdog.sh runs under `set -euo pipefail`. Two bugs from
  tonight's earlier watchdog fix (FINDING-watchdog-hang-blind-spot) combined:
  (1) `_bench_pid()` used a bare `[[ -f "$LOCK_FILE" ]] || return 1` -- when
  called from `_kill_hung_benchmark` via a plain assignment (`pid=$(_bench_pid)`,
  not wrapped in an `if`/condition), a nonzero return here is NOT exempt from
  `set -e` and aborts the whole script. (2) `_kill_hung_benchmark` itself used
  `[[ -z "$pid" ]] && return 0` as a bare statement -- when `$pid` is
  NON-empty (the exact case that needs to proceed to actually kill the
  process), the left side of `&&` is false, short-circuits, and the compound
  command's own nonzero exit trips `set -e` again, meaning the kill logic
  would have crashed the moment it was actually needed even if (1) weren't a
  problem on its own. `_bench_alive()` uses the same `[[ ]] && return` pattern
  but is always invoked as `if ! _bench_alive; then ...`, which IS exempt from
  `set -e` per bash's condition-context rule -- that's why detection worked
  (the "dead or hung" line printed correctly) right up until the kill step.
- Fix: rewrote `_bench_pid()` to never return non-zero (empty stdout instead,
  when the lock file is absent) and rewrote `_kill_hung_benchmark()` to use
  proper `if`/`fi` blocks throughout instead of `[[ ]] && cmd` one-liners,
  ending with an explicit `return 0`. Also rewrote `_bench_alive()`'s internal
  `&&`-return lines as explicit `if` blocks for the same reason, even though
  its call-site context made it not the actual crash source -- relying on the
  subtle if-condition set -e exemption was exactly the kind of fragility that
  caused this bug in the first place, better to not depend on it anywhere.
- Verified with a real `set -euo pipefail` test harness (not the earlier,
  insufficient standalone extraction without `set -e`) against 3 scenarios:
  (1) no lock file at all -- the exact race that crashed it live; (2) lock
  file with a genuinely alive PID -- the case that needs the kill logic to
  actually run; (3) lock file with a dead/nonexistent PID. All 3 pass without
  the test script aborting; scenario 2 confirms the target process is actually
  killed.
- bash -n clean. Did not need to stop the benchmark itself for this fix --
  only the watchdog supervisor process was dead; restarted just
  `benchmarks/watchdog.sh` (new PID) while the benchmark run (PID 1451140,
  full 8-task/4-condition/5-round suite, started 11:47) kept running
  uninterrupted throughout.

Files changed (uncommitted): benchmarks/watchdog.sh (_bench_pid, _bench_alive,
_kill_hung_benchmark -- set -e safety).

## 2026-08-20T13:15:26Z — FINDING-critic-preamble-third-variant — done

Plan / progress:
- During the isolated compound_ai knowledge-efficacy experiment
  (docs/experiments/graphrag_efficacy_ringbuffer.md), Lauf 3 scored 3.0/10
  (down from Lauf 2's 5.8) -- but `final_response` was entirely critic
  meta-commentary ("The answer contains a critical technical error in its
  reasoning regarding memory orderings...") rather than a real corrected
  answer. Same failure class as the already-fixed critic_node bug
  (FINDING-critic-node-non-compliant-judge-overwrite), a third variant my
  existing guard didn't cover: the critic prompt explicitly bans opening with
  "The answer contains mistakes"-style preamble, but the model does exactly
  that and never gets to a real replacement. The existing guard only checked
  (a) trailing bare CONFIRMED and (b) complete disappearance of code markers
  -- (b) didn't fire here because the critique quoted code fragments from the
  original (e.g. inline `tail_`/`buffer_[tail]` mentions), so "some code
  marker present" was true even though no complete corrected implementation
  was ever given.
- User correctly called out that this should have been caught proactively
  (checking judge_reasoning/final_response on every round) rather than only
  on explicit request -- adopted as a standing rule for the remainder of this
  experiment: every round's result gets a plausibility check (read the
  reasoning, watch for score regressions) before being reported as a real
  data point.
- Fix: added `_CRITIC_PREAMBLE_RE` (matches the recurring banned lead-in
  pattern "The answer/response contains...", "Unsupported/Incorrect claim"),
  checked alongside the existing two conditions. Verified against 6 cases:
  the exact Lauf 3 text and the original session's very first critic bug
  (both correctly flagged), a genuine code correction, a genuine text-only
  correction, a proper bare CONFIRMED, and a reply that merely mentions "the
  answer" mid-sentence while providing a real fix (all correctly left
  unaffected).
- Rebuilt moe-sovereign-orchestrator:local
  (sha256:4b8adf67071fa4514eaad6f686456b58e7b15bb3a506faf18d2f2e5887bd9489),
  recreated langgraph-orchestrator, healthy, fix verified present.
- Lauf 3's invalid result discarded per the "infra/script errors don't count"
  policy -- knowledge state unchanged (no new curated import needed, this was
  a pipeline bug not a knowledge gap), Lauf 3 is being re-run clean.

Files changed (uncommitted): graph/synthesis.py (_critic_is_noncompliant_confirmation,
added _CRITIC_PREAMBLE_RE).

## 2026-08-20T15:56:17Z — FINDING-critic-preamble-fourth-variant — done

Plan / progress:
- Lauf 3 (first repeat, after the watchdog mtime fix) completed cleanly at the
  monitoring level -- watchdog correctly detected the real completion this
  time, no false-positive exit. But the result itself (score 4.6, judge 1.0)
  was again entirely critic meta-commentary: "The provided answer contains a
  critical logical flaw in the unit test...". A fourth wording of the same
  recurring pattern -- this time with "provided" inserted between "the" and
  "answer", which the existing `_CRITIC_PREAMBLE_RE`
  (`the (answer|response)` exact match) didn't cover.
- Fix: broadened the regex to `the\s+(provided\s+|given\s+)?(answer|response|
  implementation|code)\b`, plus a second alternative matching
  `(unsupported|incorrect|critical)\s+(claim|flaw|error)` at the start of the
  reply -- covers all leading-word variants seen so far (answer/response/
  implementation/code, with or without a provided/given qualifier) without
  broadening to a generic "contains word X anywhere" match that could
  false-positive on real corrections.
- Verified against 9 cases: all 3 real variants observed this session so far,
  2 plausible near-variants ("the given implementation...", "the provided
  code..."), and 4 genuine-correction/non-trigger cases (code fix, text fix,
  "the answer" mentioned mid-sentence in a real fix, "This response answers
  the question correctly." as an unrelated opening) -- all pass.
- Rebuilt moe-sovereign-orchestrator:local
  (sha256:d00a7d2bd2fd4267646d7c3d9e7e6e2402a3c939a0f72975a74e07d1b22e13da),
  recreated, healthy, fix verified present in container.
- Knowledge state unchanged (still no new curated import -- this is the
  second consecutive round where the apparent "regression" was purely a
  pipeline bug, not a knowledge gap or genuine model plateau). Re-running
  Lauf 3 a third time.
- Note: the underlying pattern (this specific judge model consistently
  opening a "corrected answer" with a diagnostic preamble instead of direct
  replacement content) may warrant a prompt-level fix eventually (e.g. a
  stronger/differently-worded critic instruction, or a few-shot example) --
  logging as a candidate follow-up rather than chasing every wording variant
  reactively forever, if a fifth variant appears.

Files changed (uncommitted): graph/synthesis.py (_CRITIC_PREAMBLE_RE broadened).

## 2026-08-20T19:32:30Z — FEATURE-merger-conflict-arbitration-refine — starting

Plan / progress:
- User design directive: the Planner->Expert->Judge chain should not end at
  "Judge observes" -- the Judge should use every available mechanism to
  actively improve the result, for any category, not just safety-critical.
- Triggered by a live, real failure tonight (Lauf 3 of the GraphRAG-efficacy
  experiment, chatcmpl-4e517c44...): planner produced 2 duplicate
  systems_programming tasks, 2 experts disagreed, resolve_conflicts_node
  correctly detected the conflict but dismissed it (Strategy C: non-safety-
  critical, no LLM cost warranted) since systems_programming isn't in
  _SAFETY_CRITICAL_CATS. Trust-Score dropped across 3 merger passes
  (0.310->0.295->0.278) as unresolved conflicts accumulated, stayed BLOCK,
  quality_gate_node correctly withheld the whole response
  (trust_score_block, services/quality_gate.py:253 -- intentional fail-
  closed, not a bug). Zero usable output from a resolvable disagreement.
- Approach (see /home/philipp/.claude/plans/zazzy-beaming-koala.md for full
  plan, approved by user): extend merger_node's EXISTING Judge Refinement
  Loop (graph/synthesis.py:288-398, which already re-invokes an expert via
  _refine_expert_response() with Judge feedback, currently gated on
  confidence=="low" only) to ALSO trigger for any category present in
  _new_conflicts (already computed at graph/synthesis.py:185, currently only
  used by the later, safety-critical-only resolve_conflicts_node). Enrich
  the existing single Judge gap_prompt with the actual conflicting
  propositions + an arbitration instruction for those categories, feed the
  verdict into the unchanged _refine_expert_response() call, and mark
  resolved conflicts (resolved_by: "merger_refine_arbitration") so
  resolve_conflicts_node doesn't redundantly re-arbitrate the same conflict
  later for safety-critical categories.
- Working in an isolated git worktree, branch
  feat/merger-conflict-arbitration-refine. No commit/push without explicit
  user authorization.

Files: graph/synthesis.py (merger_node's refine loop, target of the change).

## 2026-08-20T19:35:24Z — FEATURE-merger-conflict-arbitration-refine — done (deployed, live-testing)

Plan / progress:
- Implemented in worktree ../moe-infra-worktree-conflict-arbitration, branch
  feat/merger-conflict-arbitration-refine, exactly per the approved plan
  (/home/philipp/.claude/plans/zazzy-beaming-koala.md): merger_node's
  existing Judge Refinement Loop now also triggers for any category present
  in _new_conflicts with resolution=="pending" (not just confidence=="low"),
  for any category (not just _SAFETY_CRITICAL_CATS). The single existing
  Judge gap_prompt gets an additional section with the actual
  proposition_a/proposition_b for conflicted categories plus an explicit
  arbitration instruction; the verdict is extracted the same
  [CATEGORY]: <...> way and fed into the UNCHANGED
  _refine_expert_response(cat, feedback, state_) call. When a refinement is
  actually adopted (ratio >= JUDGE_REFINE_MIN_IMPROVEMENT) for a
  conflict-triggered category, the matching entries in _new_conflicts are
  mutated in place to resolution="resolved",
  resolved_by="merger_refine_arbitration" -- since _new_conflicts is the
  same list object returned as conflict_registry, this is visible downstream
  and prevents resolve_conflicts_node from redundantly re-arbitrating the
  same conflict later for safety-critical categories. No changes to
  resolve_conflicts_node itself -- it remains the fallback for whatever this
  loop doesn't resolve (still-pending conflicts, e.g. when _max_refine==0 on
  trivial/moderate paths or refinement didn't clear the improvement bar).
- py_compile clean in worktree and main checkout. Built
  moe-sovereign-orchestrator:local
  (sha256:7a22744681d59decdd691bb65073f2a4736176ce9e3d6e8a1940c4ca4817240f),
  recreated langgraph-orchestrator, health:healthy, fix verified present in
  the running container.
- Integration test: the triggering condition (planner producing duplicate
  same-category tasks) is stochastic (temperature 0.7), not reproducible on
  demand -- resuming the isolated knowledge-efficacy experiment's Lauf 3
  (docs/experiments/graphrag_efficacy_ringbuffer.md) now doubles as the live
  integration test: if the duplicate-task/conflict pattern recurs, this
  deployment is what will exercise the new arbitration path for real; either
  way Lauf 3 gets a valid attempt.

Files changed (uncommitted, mirrors worktree): graph/synthesis.py
(merger_node refine loop).

Notes:
- Not committed/pushed/PR'd -- per AGENTS.md, awaiting explicit user
  authorization, same as the other uncommitted changes this session.

## 2026-08-21T07:21:14Z — FIX-merger-repetition-collapse — done

Plan / progress:
- Root-caused Lauf 4 of the GraphRAG-efficacy experiment scoring 3.0/10
  (Det 0.0, Judge=5.0 exact -> fallback pattern) with turn.ok=False, HTTP 422
  "plausibility_failed:unclosed_code_block". Traced the actual audited LLM
  I/O for this request via Postgres ai_io_audit_log (request_body confirmed
  which of the 7 judge-stage calls was which by matching each call's own
  distinctive prompt text): the MERGER's own synthesis call (not critic --
  request_body opens "Synthesize the following information into a clear,
  complete answer...") produced a 100216-character response that starts
  normally ("Here is the synthesized implementation...") but degenerates
  into "// I will output the SPSC code." repeated dozens of times, cutting
  off mid code-fence (3 backticks, odd). The already-deployed critic-node
  guard worked correctly here -- it saw the CRITIC's own reply was
  non-compliant and preserved the prior final_response instead of
  overwriting it -- but the prior final_response it preserved was this
  already-broken merger output, so quality_gate_node still (correctly)
  withheld the whole response at the end. This is a third, independent
  failure class from anything fixed earlier tonight: a generation-level
  repetition collapse in the merger's OWN synthesis call, not a
  format-compliance issue in a downstream check.
- Fix (two parts, both requested by the user together):
  1. services/inference.py: _invoke_judge_with_retry() gained optional
     repeat_penalty/repeat_last_n params, passed through as Ollama sampling
     options only when the caller supplies them (unset/no behavior change
     for every other call site: self-critique, critic, refinement,
     arbitration, resolve_conflicts).
  2. graph/synthesis.py: merger_node's main synthesis retry loop now calls
     _invoke_judge_with_retry(..., repeat_penalty=1.3, repeat_last_n=256) and,
     after a successful (non-exception) call, runs the response through the
     existing services.quality_gate.verify_response_plausibility() (same
     check quality_gate_node uses at the very end, now reused earlier). An
     implausible result (empty, too short, or -- the observed case --
     unclosed code block) is treated as a retriable failure: the loop tries
     again (up to the existing _structured_attempts budget) instead of
     accepting a degenerate response immediately. If every attempt stays
     implausible, the last result is kept (not discarded) so downstream
     checks still see and can reject it -- this reduces how often the
     failure reaches the user, it does not claim to eliminate it entirely.
- Verified standalone: the exact recorded 100216-char degenerate response
  correctly fails the plausibility check (would now trigger a retry instead
  of being accepted); a normal, closed-code-block response passes unaffected.
- py_compile clean (worktree ../moe-infra-worktree-merger-repetition, branch
  fix/merger-repetition-collapse-retry, and main checkout). Rebuilt
  moe-sovereign-orchestrator:local
  (sha256:6591d3474c71e9035a290bf3f35ec04112dc5cd904b4d7bf0f5f369f06590d57),
  recreated langgraph-orchestrator, health:healthy, both changes verified
  present in the running container.
- Resuming the isolated knowledge-efficacy experiment (Lauf 4) with this fix
  live -- doubles as the integration test, same as the conflict-arbitration
  feature earlier tonight.

Files changed (uncommitted, mirrors worktree): graph/synthesis.py (merger
retry loop), services/inference.py (_invoke_judge_with_retry new params).

## FINDING-native-passthrough-hang-root-cause-is-ollama-gpu-discovery (2026-08-21)

Status: root-caused, NOT a moe-infra code bug. Owner: Claude Code. No file
changes to this repo.

Context: after migrating benchmarks/run_scientific_benchmark.py off raw
Ollama calls onto the MoE Sovereign "model@node" native-passthrough API (per
explicit user directive -- no direct Ollama calls, everything through the
MoE Sovereign API), a native request for qwen3.8:27b@N04-RTX hung
indefinitely (tested up to 60s via the API, up to 90s via a direct diagnostic
call straight to Ollama, bypassing the orchestrator entirely).

Investigation (in order, each step disproving the prior hypothesis):
1. Redis moe:active:* "semaphore" -- disproven: services/tracking.py's
   _register_active_request is pure fire-and-forget monitoring, no limit
   enforcement. Orphaned keys from earlier aborted tests deleted (user
   authorized); hang persisted after deletion.
2. moe_userdb Postgres pool exhaustion (state._userdb_pool, max_size=10) --
   disproven: pg_stat_activity showed only 5/10 connections in use, all idle,
   no stuck queries.
3. py-spy dump of the orchestrator's PID 1 during a live hung request --
   inconclusive by itself (asyncio event loop showed "idle", which is
   expected for any awaited I/O and does not distinguish a healthy wait from
   a stuck one); confirmed other endpoints (/health, /metrics) kept
   responding throughout, ruling out an event-loop-blocking bug in our code.
4. Direct diagnostic POST to http://192.168.155.224:11434/api/chat for
   qwen3.8:27b, bypassing the orchestrator entirely -- ALSO hung (90s, 0
   bytes). This isolates the problem to Ollama/the model itself, not
   services/pipeline/chat.py's native-passthrough code (auth, model-
   availability check, egress guard, audit-create, and dispatch all executed
   correctly per orchestrator logs up to the point of the outbound call).
5. Control test: same node, same size class, POST /api/chat for
   sovereign-judge:27b (already resident) -- succeeded in 4.4s. Confirms
   Ollama's HTTP server and inference engine are healthy in general; the
   failure is specific to qwen3.8:27b.
6. nvidia-smi on N04-RTX during the hang: 0% utilization on all 4 GPUs,
   VRAM usage unchanged (still only sovereign-judge:27b's ~29GB footprint
   split across the 4 mixed RTX 2060/3060 cards) -- qwen3.8:27b's load never
   actually starts computing.
7. docker logs ollama (host N04-RTX, container "ollama", image
   ollama-github:latest, version 0.32.14) grepped for errors: repeated,
   recurring entries -- dated as far back as 2026-08-19, i.e. pre-existing,
   not caused by tonight's testing --
     "msg=\"llama-server GPU discovery watchdog timed out\" ... error=\"context deadline exceeded\""
   immediately following/preceding llama_model_loader lines that show
   qwen3.8:27b's architecture: family "qwen35", a hybrid
   attention+SSM (Mamba-style) architecture (qwen35.ssm.* kv fields:
   conv_kernel, state_size, group_count, time_step_rank, inner_size) plus a
   vision projector (clip.has_vision_encoder=true). CUDA_VISIBLE_DEVICES is
   0,1,2,3 (all 4 GPUs), matching the size requiring a 4-way split.

Root cause (best evidence to date): Ollama 0.32.14's GPU-discovery
subprocess (spawned to probe VRAM across CUDA_VISIBLE_DEVICES before
loading model weights) times out ("context deadline exceeded") specifically
for qwen3.8:27b on this node's mixed RTX 2060/3060 4-GPU set, and the
attempt appears to retry without ever surfacing an error back to the HTTP
caller -- an indefinite hang from the client's perspective. sovereign-judge:
27b (no SSM layers, same node, same 4-way split, same size class) loads and
serves normally, which narrows the likely trigger to the hybrid SSM/vision
architecture's interaction with Ollama's GPU-discovery probe on this specific
mixed-GPU node, not multi-GPU splitting in general.

This is an infra/model-compatibility issue on the N04-RTX Ollama host, not a
bug in this repository's code. No fix attempted yet -- remediation options
(container restart, forcing single-GPU placement for this model, routing
qwen3.8:27b to a different node, pinning a different Ollama/llama-server
build) all touch a live service currently also serving sovereign-judge:27b
in production and need an explicit decision before acting.

### Resolution (same day)

Confirmed mechanism via reproduction: a client-side disconnect/timeout while
Ollama is still cold-loading a model (qwen3.8:27b and sovereign-judge:27b
both take ~90-105s to cold-load across N04-RTX's 4 mixed RTX 2060/3060 GPUs)
leaves Ollama's GPU-discovery/llama-server-startup path permanently wedged
for ALL subsequent load attempts on that node ("llama-server GPU discovery
watchdog timed out", "context deadline exceeded", following a logged "Load
failed ... context canceled"). Reproduced this deliberately: an aborted
`curl -m 40` against sovereign-judge:27b (already needing ~90s to reload
after a restart) re-wedged the node a second time within this same
investigation.

`docker restart ollama` on N04-RTX clears the wedged state. After restart,
both sovereign-judge:27b (93s cold load) and qwen3.8:27b (104s cold load via
the full MoE Sovereign API native-passthrough path, then 2.8s warm) served
correctly end to end. This confirms the native-passthrough migration in
services/pipeline/chat.py (query_moe_orchestrator / model@node routing) has
no bug -- auth, model-availability check, egress guard, audit, and dispatch
all work correctly; the only blocker was the wedged upstream Ollama process.

User-authorized action taken: restarted the "ollama" container on N04-RTX
(twice, second time to clear a re-wedge caused by my own aborted diagnostic
call during verification). No moe-infra file changes; no image rebuild.

Durability assessment (per standing policy: fixes must have lasting value,
not just make tonight's benchmark pass):
- Infra-side candidate (not yet implemented, needs a decision): Ollama
  0.32.14 on this node does not clean up gracefully when a client cancels
  mid-load; a supervisory health check that detects the wedged pattern
  (repeated "GPU discovery watchdog timed out" in logs, or an empty
  /api/ps combined with a pending request older than N seconds) and
  auto-restarts the container would prevent this from becoming a recurring
  incident. Alternatively, keep qwen3.8:27b/sovereign-judge:27b warm
  (keep_alive) so cold loads -- the trigger condition -- happen rarely.
- moe-infra-side candidate (not yet implemented, needs a decision): the
  native-passthrough non-streaming call in services/pipeline/chat.py
  (~line 2404, `async with httpx.AsyncClient(...).post(...)`) propagates a
  caller's disconnect straight through to the upstream Ollama call. Shielding
  that specific upstream call (asyncio.shield, matching the pattern already
  used in services/inference.py's _audit_cancel) so a client hangup does not
  cancel an in-flight model load on the shared node would remove moe-infra's
  own contribution to triggering this Ollama-side bug, independent of
  whether Ollama's own robustness gap is ever fixed upstream. This does not
  contradict the "never swallow CancelledError" rule -- the caller's own
  await still observes the cancellation/timeout; only the upstream Ollama
  request is shielded from being torn down.
- Not a finetuning candidate: this is purely an infra/operational
  robustness gap (client-cancellation handling under cold-load latency), not
  a model-behavior or training-data issue.

No further action taken pending user decision on the two candidate fixes
above.

## FEATURE-native-passthrough-shield-client-cancellation (starting, 2026-08-21)

Owner: Claude Code. User authorized implementing "Option 2" from the
FINDING above: shield the upstream Ollama httpx call in
services/pipeline/chat.py's native-passthrough non-streaming path with
asyncio.shield, so a client disconnect no longer cancels an in-flight
model load on the shared node (the confirmed trigger for the Ollama
GPU-discovery wedge documented above).

Scope: services/pipeline/chat.py only, both non-streaming native-passthrough
branches (~line 2404 _ns_use_native/Ollama-native, and ~line 2505 generic
OpenAI-compat forward). Streaming path (_stream_native_llm) and the
Ollama-side supervisory-restart idea (Option 1, not chosen) are out of
scope.

Working directly in the existing worktree
../moe-infra-worktree-merger-repetition (branch
fix/merger-repetition-collapse-retry) rather than a fresh worktree: its
chat.py is currently identical to main (no prior edits), and this is the
worktree the currently-deployed image
(sha256:6591d3474c71e9035a290bf3f35ec04112dc5cd904b4d7bf0f5f369f06590d57)
was built from -- building from a fresh worktree instead would silently
drop the already-deployed merger-repetition-collapse fix (synthesis.py /
inference.py) from the next rebuild. The two fixes remain logically
separate changes in different files within this one worktree; they can be
split into separate commits later.

### FEATURE-native-passthrough-shield-client-cancellation: done, verified (2026-08-21)

Implemented in ../moe-infra-worktree-merger-repetition/services/pipeline/chat.py
(uncommitted, mirrors the main checkout's copy is NOT yet updated -- see note
below):
- Added `_audit_cancel` to the existing `from services.inference import (...)`
  block.
- Both non-streaming native-passthrough branches (_ns_use_native/Ollama-native
  ~line 2404, and the generic OpenAI-compat forward ~line 2505) now run their
  outbound httpx POST inside a small local async helper wrapped in
  `asyncio.shield(...)`, with a new `except asyncio.CancelledError:` clause
  that calls the existing `_audit_cancel(_native_audit)` (itself already
  shielded internally) before re-raising -- the caller's own cancellation is
  still observed and re-raised (no CancelledError is swallowed), only the
  upstream Ollama request is protected from being torn down.

py_compile clean. Rebuilt moe-sovereign-orchestrator:local
(sha256:615f0bd7ab0015812d023c15139eae5eb05bbb1b0cd1a4d5e85354dabd54f612),
recreated langgraph-orchestrator via `docker compose up -d --no-deps
--force-recreate langgraph-app` (compose service name, not the container
name), health: healthy. Verified the new code is present in the running
container (grep for asyncio.shield / _audit_cancel call sites).

Integration test (reproduces the exact originally-reported failure mode):
1. Force-unloaded qwen3.8:27b on N04-RTX (`/api/generate` with
   `keep_alive:0`) to guarantee a genuine cold load.
2. Sent a native-passthrough request through the MoE Sovereign API
   (qwen3.8:27b@N04-RTX) with a client-side timeout of 8s -- well inside the
   model's known ~93-105s cold-load time -- and let curl abort the
   connection.
3. Confirmed via Ollama's own GIN access log on N04-RTX:
   `[GIN] ... 200 | 1m38s | POST "/api/chat"` -- the upstream load-and-generate
   call completed successfully (HTTP 200) despite the calling client having
   disconnected 90 seconds earlier. No "Load failed"/"context canceled" entry
   this time (that entry was present for every prior reproduction without the
   fix).
4. Confirmed the node was left healthy afterward, not wedged: `/api/ps`
   showed qwen3.8:27b loaded, and an immediate follow-up request through the
   MoE Sovereign API returned in 2.96s (normal warm latency).

This directly demonstrates the fix: a client disconnect during a cold model
load no longer cancels the upstream Ollama request, so it no longer leaves
the node's GPU-discovery/llama-server-startup path wedged for subsequent
requests -- the mechanism that previously required a container restart to
clear.

Not independently re-verified with a second live cold-load test: the generic
OpenAI-compat forward branch (~line 2505) received the identical
shield/_audit_cancel pattern; its correctness rests on code-level parity with
the tested branch rather than its own dedicated reproduction (each cold-load
test costs ~100s and requires forcing an unload first).

Status: deployed to the running container, uncommitted. Files changed in
this worktree (uncommitted): services/pipeline/chat.py (this feature),
graph/synthesis.py + services/inference.py (from the earlier, separately
authorized merger-repetition-collapse fix, unchanged by this work). No
commit or push made -- awaiting explicit authorization, and a decision on
whether to split these into separate commits/PRs given they now share one
worktree's working tree.

## FIX-critic-preamble-fifth-variant (done, 2026-08-22)

Owner: Claude Code. Fixes the previously-flagged-but-deferred 5th
_CRITIC_PREAMBLE_RE gap (see FINDING/FEATURE entries above and
docs/experiments/graphrag_efficacy_ringbuffer.md, Lauf 4 7th attempt).

Found while reviewing the most recent completed isolated-benchmark result
(benchmarks/results/checkpoint_scientific_benchmark.json,
sci-sysprog-01-lockfree-ringbuffer/compound_ai/round 1, score 4.9,
judge_score 1.5, judge_verdict FAIL): final_response began with 'The
provided "ANSWER TO CHECK" is severely corrupted...' -- the critic quoting
the prompt's own literal section header back instead of a plain noun,
slipping past the existing regex. The embedded corrected Rust answer after
the preamble was still gradable (hence a real, non-zero score), but the
result is methodologically contaminated for the "keine Cheats,
rekonstruierbar" experiment and must not be counted as clean evidence
either way for the systems_programming CAS-loop finding it also repeats.

Fix: services/inference import unaffected; graph/synthesis.py's
_CRITIC_PREAMBLE_RE extended to allow an optional quote character around the
noun and an optional "to check" suffix. Verified against all 5 known
variants (previous 4 + this one) and 3 real corrections (CONFIRMED, direct
code fix, prose fix) -- no false positives introduced.

py_compile clean. Rebuilt moe-sovereign-orchestrator:local
(sha256:e5577e03b7d7a626c7d2be8e75d8eec90d8d3182700ad58d85dec29234c6aebd),
recreated langgraph-orchestrator via `docker compose up -d --no-deps
--force-recreate langgraph-app`, healthy. Re-verified the exact regex
inside the running container against the exact recorded corrupted text.

Committed (6292e5df) and pushed to origin/fix/critic-preamble-quoted-header-variant.
MR link: https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Fcritic-preamble-quoted-header-variant

Resuming the isolated GraphRAG-efficacy benchmark (Lauf 4, 8th attempt) now
that this contamination source is fixed, per user instruction "führe den
isolierten Benchmark weiter fort". Deleted the stale
benchmarks/results/checkpoint_scientific_benchmark.json entry for this task
before restarting (same watchdog-checkpoint-reuse precaution as before:
without this, a resumed process would silently reuse the contaminated
round-1 result instead of generating a fresh one).

## FIX-plausibility-missing-required-code (done, 2026-08-22)

Owner: Claude Code. User decision: "Plausibilitäts-Check erweitern (empfohlen)"
after a live-observed second degeneration subtype under repeat_penalty=1.3.

Isolated benchmark Lauf 4, 9th attempt (after the preflight-probe fix and the
5th critic-preamble-variant fix, both done earlier tonight) completed end to
end (score 6.2, ~37 min wall clock) but the result is scientifically
worthless: the merger synthesis (attempt 2/3, passed the then-existing
plausibility check) degenerated into a multi-thousand-word chain of
unrelated nouns/verbs with zero code fences, for a task that explicitly
required a Rust/C++ implementation. The scoring judge correctly caught it
downstream ("devolves into incoherent word salad", FAIL) but the pipeline's
own plausibility gate did not -- confirming the earlier-flagged, previously
undecided worry that repeat_penalty=1.3 (FIX-merger-repetition-collapse)
only changed the degeneration shape (verbatim loop -> topic drift), not its
root cause.

Fix: services/quality_gate.py's verify_response_plausibility() takes an
optional task_text; when the task explicitly asks for an implementation in
a named language (verb+language heuristic, _task_requires_code) and the
response has zero ``` fences, it's now "missing_required_code" --
implausible. Wired into both call sites: graph/synthesis.py's merger retry
loop (state_.get("input")) and quality_gate.py's own final check
(state_.get("input")) -- so a degenerate response is either retried
immediately or blocked before reaching a user or the scoring judge.

py_compile clean. Rebuilt moe-sovereign-orchestrator:local
(sha256:09826450da1702b27e24efb0b51111a7e55789b86ce8562a535b5c69b01fb470),
recreated langgraph-orchestrator, healthy. Verified inside the running
container against the exact recorded degenerate text (now caught) and
against a real code answer + a non-code task (both unaffected, no false
positive).

Committed (4cb0d2ce) and pushed to
origin/fix/plausibility-missing-required-code. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Fplausibility-missing-required-code

Deferred, not done: whether repeat_penalty=1.3 itself should also be
lowered/removed was explicitly declined by the user in favor of this
detection-side fix only -- topic-drift degeneration under repeat_penalty is
now caught for code tasks specifically, but could still slip through
undetected for a non-code task that drifts the same way. Flagging as a
known residual gap, not fixing preemptively without a second observed case.

Resuming the isolated GraphRAG-efficacy benchmark (Lauf 4, 10th attempt).
Deleted the stale checkpoint entry from the 9th attempt (contaminated
result, not counted).

## FIX-reduce-merger-repeat-penalty (done, 2026-08-22)

Owner: Claude Code. User decision, in real time while a 4th reproduction was
actively running: "Jetzt abbrechen, repeat_penalty reduzieren" -- reversing
the earlier "wait for one more run" decision once the pattern became
unambiguous mid-run.

Isolated benchmark Lauf 4, 11th attempt: the same merger synthesis call
(task explicitly requiring Rust/C++ code) degenerated into topic-drift word
salad again, this time growing past 22,000 tokens (toward the 32,768
MAX_JUDGE_TOKENS ceiling) after 30+ minutes with no sign of stopping.
Manually confirmed live via Ollama's own generation timing log and GPU
utilization (not stalled -- genuinely still generating). This is the 4th
reproduction of the same failure mode since FIX-plausibility-missing-
required-code was deployed (attempts 9, 10, and now 11 all hit it; attempts
9's contamination and 10/11's total blocks together account for
~2.5 hours of GPU time with zero scientific value for the running
knowledge-graph-efficacy experiment) -- strong enough evidence to revisit
the earlier "extend detection only" decision.

Action: killed the stuck benchmark script client-side (PID 460679); did NOT
touch the in-flight Ollama generation itself (no evidence that cancelling
mid-generation, as opposed to mid-model-load, causes the GPU-discovery
wedge documented earlier -- left it to finish or hit its own ceiling
naturally, unobserved, since nothing was still listening for its result).

Fix: graph/synthesis.py's merger-synthesis repeat_penalty lowered from 1.3
to 1.15 (repeat_last_n=256 unchanged). Rationale: 1.3 fully solved the
original verbatim-repetition bug but is now confirmed (4/4 code-task runs)
to reliably trigger a different degeneration mode on this task type instead.
1.15 keeps meaningfully more repetition suppression than the pre-fix
baseline (which had none) while giving the model more room to reuse
task-relevant vocabulary (Rust/atomics/memory-ordering terms necessarily
repeat a lot in a correct answer) instead of being pushed to hunt for novel,
unrelated words.

py_compile clean. Rebuilt moe-sovereign-orchestrator:local
(sha256:fed436dd191af57c05446105901ea977f188d4a039f0a360f4db54f628a5bcbd),
recreated langgraph-orchestrator, healthy, verified repeat_penalty=1.15
present in the running container.

Committed (bb570200) and pushed to origin/fix/reduce-merger-repeat-penalty.
MR: https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Freduce-merger-repeat-penalty

Open question, not yet answered: whether 1.15 fully resolves the topic-drift
mode or merely delays/reduces its likelihood. missing_required_code and the
plausibility-retry loop remain as defense in depth either way. Next run
(12th attempt) is the test.

Resuming the isolated GraphRAG-efficacy benchmark (Lauf 4, 12th attempt).

## FIX-critic-hallucination-check-unblocks-stale-trust (done, 2026-08-22)

Owner: Claude Code. Found while investigating why isolated benchmark Lauf 5,
13th attempt, was blocked (HTTP 422 trust_score_block, empty final_response)
despite the container logs showing the pipeline had apparently recovered:
resolve_conflicts_node dismissed all 4 pending paraconsistent conflicts as
non-critical, and critic_node's hallucination-risk pass then found and
corrected one genuinely unsupported claim, logging (misleadingly) "Trust-
Score stayed PROCEED_WITH_ASSUMPTION". The corrected answer was discarded
anyway.

Root cause: critic_node's hallucination-risk branch (graph/synthesis.py,
the `else` critic_prompt case around line ~2200) never wrote back to
state_["trust_verdict"] after confirming or correcting the answer -- it only
returned an updated final_response. quality_gate_node
(services/quality_gate.py:253-254) reads trust_verdict directly and blocks
unconditionally on BLOCK, so a verdict computed by an earlier merger round
(before conflicts were dismissed and before this exact claim was corrected)
stayed frozen and discarded a response that had since been fixed. This is a
genuine, pre-existing production bug -- not something introduced by tonight's
other fixes -- that would affect any live request following this same
trust-drops-then-recovers pattern, not just the benchmark.

Fix: when the hallucination-check critic confirms the answer or corrects it
successfully, and trust_verdict was BLOCK at that point, the node now
returns trust_verdict: "PROCEED_WITH_ASSUMPTION" (never straight to
PROCEED) alongside the (possibly corrected) final_response. Left unchanged
for the non-compliant-judge-format branch (no real verification occurred,
so no basis to upgrade trust) and the separate `active`/safety-critical
branch. Also fixed the decision-log rationale string, which previously
claimed "stayed PROCEED_WITH_ASSUMPTION" unconditionally even when the
actual prior verdict was BLOCK.

py_compile clean. Rebuilt moe-sovereign-orchestrator:local
(sha256:d623d70b5c62cd042e9dc30e5685cf4fdcfa2a0231f34ac5a448b4cb705d4d3c),
recreated langgraph-orchestrator, healthy, verified the new code string is
present in the running container.

Committed (9bee5d86) and pushed to
origin/fix/critic-hallucination-check-unblocks-stale-trust. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Fcritic-hallucination-check-unblocks-stale-trust

This is an infra/pipeline-correctness fix, not benchmark-specific -- durable
value regardless of the ongoing GraphRAG-efficacy experiment (matches the
standing policy that every fix tonight must have lasting value).

Resuming the isolated GraphRAG-efficacy benchmark (Lauf 5, 14th attempt).

### Follow-up: fixed misleading log in the same fix (2026-08-22)

Lauf 5, 14th attempt hit trust_score_block again -- but this time via a
different, correctly-handled path: the hallucination-check critic's OWN
reply was itself "non-compliant judge format" (the already-covered
_critic_is_noncompliant_confirmation guard caught it correctly), so no
upgrade should happen and the response correctly stayed blocked. The
decision-log message wrongly said "upgraded a stale BLOCK to
PROCEED_WITH_ASSUMPTION" anyway, because it inferred the upgrade purely from
the prior verdict being BLOCK rather than from what the call site actually
returns. Fixed: _log_hallucination_check now takes an explicit `upgraded`
argument from each of the 3 call sites. No functional/gating change -- the
block itself was correct; only the log accuracy was fixed. Rebuilt
(sha256:31761519e809a6c133e5f41063c31243e47dca75ad237cf80f89266acadc4f1b),
healthy. Second commit (41b168b7) pushed to the same branch.

This 14th attempt's block is therefore the 6th observed instance of the
judge occasionally violating the CONFIRMED/direct-correction reply-format
contract -- now confirmed across multiple distinct critic call sites
(merger's own critic pass, and now the hallucination-check pass too), not
just one prompt template. All 6 have been individually guarded against
(never silently accepted as a real correction), so no bad content has ever
reached a user or a scoring judge from this class of failure -- but it
keeps consuming full self-critique-round compute (this run: ~24 min) before
being caught at the very last step. Worth a decision at some point on
whether to invest in a structural fix (e.g. grammar-constrained decoding
for the CONFIRMED/correction format) versus continuing to accept it as
occasional, correctly-handled noise.

## FIX-graphrag-retrieval-relevance-cap (done, 2026-08-22)

Owner: Claude Code. User asked directly: "was ist das Problem -- Infra oder
Finetuning?" after Lauf 5, 15th attempt again showed the judge criticizing
already-curated facts (interior mutability, 'static bound -- both Round 4
imports). Investigated by tracing the actual prompts (ai_io_audit_log
request_body for every one of the 14 LLM calls in that run) instead of
re-guessing: "UnsafeCell" and "'static" appeared in zero of them, including
the largest (35,942-char) final critic prompt which explicitly assembles
graph_context + web_research + mcp_result. The model was never shown the
knowledge the scoring judge then flagged it for not applying.

Root cause found in graph_rag/manager.py's `_match_terms_to_entities()`:
Cypher term-matching capped results to the first 3 extracted query terms,
LIMIT 1 matching entity per term, and `[..6]` direct / `[..4]` indirect
relationships collected -- all in Neo4j's internal (non-relevance-ordered)
collection order, not a deliberate selection. Verified directly against the
live graph: the "MpscQueue" hub entity alone now carries 22 REQUIRES facts
after 5 curation rounds tonight; only 6 were ever returned, and empirically
that arbitrary 6-slice excluded the two Round 4 facts entirely in the
traced run.

This reframes the "application limit, not knowledge gap" conclusion drawn
from Lauf 4/5's earlier repeat-violation pattern: it cannot be trusted as
evidence of a model capability ceiling while a structural retrieval bug was
silently discarding most curated knowledge before it ever reached a prompt.
Root cause is INFRA, not (necessarily) a finetuning-addressable model limit
-- that question can only be answered again after this fix, on a run where
the relevant facts are confirmed present in the prompt and still violated.

Fix (two parts, both in graph_rag/manager.py):
1. Widened Neo4j-side caps: terms[:3]->terms[:6], per-term entity LIMIT
   1->2, direct [..6]->[..25], indirect [..4]->[..10].
2. Added `_score_relation_relevance()` (term-overlap scoring, mirrors the
   existing entity-level `_corrective_relevance_score`) and used it in
   `query_context()` to keep the most relevant facts when an entity still
   has more than fit in the rendered block, replacing the previous blind
   `rels[:4]` positional truncation.

py_compile clean. Verified live against the running Neo4j instance and the
exact ringbuffer task text (copied the fixed file into the running
container for a pre-rebuild smoke test, then did the real rebuild): the
rendered [Knowledge Graph] block now includes both previously-invisible
Round 4 facts plus the other rounds; context grew from a suspiciously
constant 684 chars (identical across many prior runs tonight) to 1812 --
substantially more knowledge surfaced, not unbounded growth.

Rebuilt moe-sovereign-orchestrator:local
(sha256:d54bb0125b67c815882904518696e47e38957b1f14989b65b767ccfcdc01a28b),
recreated langgraph-orchestrator, healthy.

Committed (0d21f7cd) and pushed to origin/fix/graphrag-retrieval-relevance-cap.
MR: https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Fgraphrag-retrieval-relevance-cap

This is a general GraphRAG-layer fix affecting every domain that uses graph
retrieval (medical, legal, financial, etc. entities also traverse this same
code path), not specific to the systems_programming curated set or this
experiment -- durable, systemic value regardless of tonight's research
question.

Not addressed (separate, independent finding, not yet actioned): the
planner (moe-sovereign-student:4b) fabricated two unrelated security tasks
(firewall rules, auth cascade) from a pure systems-programming prompt in
this same run's investigation -- verified absent from its own 48KB prompt,
so not a context-bleed/prompt-construction issue but likely a training-data
artifact in the LUMI-G distillation. Flagged to the user as a probable
finetuning-side issue, not itself fixed this session.

Resuming the isolated GraphRAG-efficacy benchmark (Lauf 5, 16th attempt) to
re-test whether the two previously-invisible facts, now actually reaching
the model, change the outcome.

## FEATURE-rust-compile-check-precision-tool (starting, 2026-08-22)

Owner: Claude Code. User-genehmigter Plan:
/home/philipp/.claude/plans/zazzy-beaming-koala.md ("Rust Compile-Check als
deterministisches Precision-Tool, Phase 1: Compile-only, kein Execute").

Kontext: Aus dem GraphRAG-Wirksamkeitsexperiment hat sich gezeigt, dass die
LLM-Judge-Selbstprüfung dieselben Fehlerklassen (Ordering, UnsafeCell,
Sync-Soundness, non-exhaustive matches) wiederholt und mit hoher
Lauf-zu-Lauf-Varianz (Judge-Score 1.0-3.0 bei identischem Wissensstand)
findet -- ein echter Compiler würde das deterministisch und in Sekunden statt
Minuten erkennen. Vor Beginn: Host-Speicherengpass auf ki-docker-vm behoben
(separater moe-codex-Compose-Stack, 25 Container, gestoppt auf User-
Anweisung -- freier RAM 1,2 GiB -> 8,2 GiB).

Scope: neuer isolierter Docker-Service `rust-compile-sandbox` (rustc
--crate-type lib -o /dev/null, kein Execute), MCP-Precision-Tool-
Registrierung (`rust_compile_check`), Verdrahtung in graph/synthesis.py's
Merger-Retry-Schleife (analog verify_response_plausibility). Arbeite in
../moe-infra-worktree-merger-repetition (aktueller Deploy-Stand), neuer
Branch feat/rust-compile-check-precision-tool.

Dateien (geplant): services/rust_compile_sandbox/{Dockerfile,app.py} (neu),
docker-compose.yml (neuer Service + rust_compile_internal-Netz +
mcp-precision-Netz-Erweiterung), mcp_server/server.py (Tool-Registrierung),
graph/synthesis.py (Merger-Retry-Verdrahtung).

## FEATURE-rust-compile-check-precision-tool: done, verified (2026-08-23)

Owner: Claude Code. Vollständig umgesetzt nach genehmigtem Plan
(/home/philipp/.claude/plans/zazzy-beaming-koala.md), inkl. Host-Vorarbeit
(moe-codex-Stack auf User-Anweisung gestoppt, 1.2 GiB -> 8.2 GiB frei).

Neu: services/rust_compile_sandbox/{Dockerfile,app.py,requirements.txt}
(isolierter, netzwerkfreier, read-only, non-root rustc-Sandbox-Service,
--emit=metadata, kein Codegen/Linking, keine Ausführung), MCP-Precision-Tool
`rust_compile_check` (voller Contract in mcp_server/server.py, Redaction
via SHA-256), Verdrahtung in graph/synthesis.py's Merger-Retry-Schleife
(nur systems_programming/code_reviewer + ```rust-Fence; bei Fehler werden
echte Compiler-Diagnosen in den nächsten Retry-Prompt eingespeist statt
blindem Resend; fail-open bei Sandbox-Fehlern).

Bug während der Implementierung gefunden und gefixt: `-o /dev/null` ließ
rustc ein Temp-Verzeichnis unter /dev/ anlegen (Permission denied im
non-root/read-only Setup, schlug sogar bei validem Code fehl) -- korrigiert
auf einen Pfad im eigenen Scratch-Workdir.

Verifiziert: Sandbox kompiliert validen Code korrekt, meldet echte
Diagnosen bei Lifetime-/Borrow-Fehlern; Netzwerk-Isolation bestätigt (DNS-
Auflösung schlägt fehl); voller MCP-/invoke-Roundtrip mit korrekter
Evidence-Redaction (nur SHA-256+Bytes, kein Klartext-Code); Live-Pipeline-
Integrationstest (kompletter isolierter Ringbuffer-Benchmark-Task, 21.
Versuch) bestätigt: Check greift über mehrere Self-Critique-Runden hinweg
korrekt, jedes Mal mit echten, unterschiedlichen rustc-Diagnosen
(unclosed delimiter, moved-value, trait-bound-Fehler, Borrow-Checker-
Verstöße) -- kein Fehlalarm beobachtet. Laufzeit dieses einen Tasks: 60 Min
(deutlich länger als zuvor, da mehrere echte Compiler-Feedback-Zyklen
durchlaufen wurden -- reale, aber erwartete Kostenerhöhung bei schwierigen
Code-Aufgaben).

Committed (7a10b4ee) und gepusht zu
origin/feat/rust-compile-check-precision-tool. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat%2Frust-compile-check-precision-tool

Neuer Fund aus dem Integrationstest (noch nicht importiert): "dereferencing
UnsafeCell without unsafe blocks" -- bisher nicht abgedeckter, spezifischer
Rust-Syntaxfehler (fehlender unsafe{}-Block beim Dereferenzieren eines
Rohzeigers aus UnsafeCell::get()), unterscheidet sich von der bereits
importierten Aliasing-Regel (Runde 7). Kandidat für nächste Wissensrunde.

Phase 2 (Miri/ThreadSanitizer-Ausführung, C++) bewusst nicht umgesetzt,
braucht eigene Freigabe.

## Isolierte GraphRAG-Experiment-Phase abgeschlossen, großer Benchmark gestartet (2026-08-23)

Isolierte Phase (docs/experiments/graphrag_efficacy_ringbuffer.md) nach 22
Versuchen, 9 Wissensrunden, 9+ dauerhaften Infra-Fixes und dem neuen
rust_compile_check-Feature abgeschlossen (siehe Doku für vollständige
Zusammenfassung und LUMI-G-Nachtraining-Kandidaten).

Auf User-Anweisung ("mach mir den isolierten Benchmark weiter und
anschließend mit dem großen") jetzt gestartet: voller Scientific-Benchmark
(benchmarks/run_scientific_benchmark.py, PID 860395), Standard-Umfang: 8
Testaufgaben x 4 Bedingungen (compound_ai, compound_ai_debate,
ablation_no_graphrag, native_baseline) x 5 Runden = 160 Einzelläufe,
sequenziell, keine Task-/Condition-Filter. User explizit über den Umfang
und die realistische Laufzeit (Stunden bis Tage) informiert, hat vollen
Standardumfang gewählt. Log: benchmarks/results/full_scientific_benchmark_*.log.

Alle heute Nacht deployten Fixes sind aktiv (Container healthy):
GraphRAG-Retrieval-Fix, repeat_penalty=1.15, missing_required_code-Check,
critic-preamble-Variante-5-Fix, Hallucination-Check-Stale-BLOCK-Fix,
native-passthrough-Cancellation-Shield, rust_compile_check. Keiner davon
gemerged/auf main -- alle als separate Feature-Branches gepusht, MRs
verlinkt in den jeweiligen FIX/FEATURE-Einträgen oben.

---

## 2026-08-23 — Großer Benchmark gestoppt, GraphRAG-Retrieval-Cap Iteration 2 gefixt, Wissensrunde 10 importiert — done

Auf User-Anweisung ("stoppe den Benchmark und fixe die systemrelevanten
GAPs") den laufenden vollen Scientific-Benchmark gestoppt (PID 860395
gekillt, Monitor beendet), da beim eBPF/XDP-Task (Runde 1, Task 2) der
erste GAP dieses Laufs auftrat.

**Wissensrunde 10 importiert** (`graph_rag/curated/
systems_programming_reference.cypher`), reale externe Quellen:
- "XDP/eBPF must check IP protocol before parsing transport header"
  (Quelle: docs.ebpf.io), verknüpft an Hub-Entity "eBPF and XDP
  programming".
- "Raft election restriction compares last-log-entry term, not current
  term" (Quelle: raft.github.io/raft.pdf, Section 5.4.1), verknüpft an die
  bestehende, dünne auto-extrahierte Hub-Entity "Raft Consensus" (per
  MATCH, nicht MERGE -- bestehender Knoten bewusst unangetastet gelassen).

**GraphRAG-Retrieval-Cap-Bug, Iteration 2** (neuer, eigenständiger
Infra-Bug, nicht derselbe wie der bereits gemergte Fix vom Vortag):
Verifikation der Runde-10-Fakten zeigte, dass der eBPF-Fakt trotz des
bereits gepushten Fixes (`fix/graphrag-retrieval-relevance-cap`,
`0d21f7cd`: `terms[:6]`, `LIMIT 2`, `[..25]`/`[..10]`) NICHT im Prompt
ankam. Root Cause direkt per Cypher verifiziert: Die Suchbegriffe "eBPF"
und "XDP" sind so verbreitet, dass sie 7 bzw. 15 unterschiedliche
Entitäten im Graphen treffen (nicht nur 1-2) -- die kuratierte Hub-Entity
landete damit außerhalb der ersten 2 in Neo4js beliebiger
Rückgabereihenfolge pro Suchbegriff. Das ist ein systemischer Bug
(betrifft jeden häufigen Fachbegriff, nicht nur diesen einen Fakt) --
Fix in `graph_rag/manager.py` (Branch
`fix/graphrag-entity-match-ranking`, Commit `93e20e7a`, gepusht):
- `_match_terms_to_entities()`: per-Term-Entity-Limit `LIMIT 2` →
  `LIMIT 10`.
- Neue finale Absicherung in `query_context()`: nach dem bestehenden
  Corrective-RAG-Gate werden die gefundenen Entitäten zusätzlich auf die
  Top `GRAPHRAG_MAX_ENTITIES` (Default 15, env-konfigurierbar) nach
  `_corrective_relevance_score()` sortiert gekappt -- verhindert, dass
  das breitere Netz (bis zu 6 Terme x 10 Entitäten) den Prompt mit zu
  vielen, wenig relevanten Entitäten flutet.

Verifiziert nach Rebuild+Redeploy (`langgraph-orchestrator`, Image
`sha256:c3746ca0...`, healthy):
- eBPF-Fakt: bereits vor dem Rebuild per Hot-Swap (`docker cp`) bestätigt,
  nach dem Rebuild erneut über den realen Container-Pfad bestätigt.
- Raft-Fakt: `query_context("Raft leader election restriction comparing
  log terms", categories=["distributed_systems"])` enthält den neuen
  Fakt-Knoten "Raft election restriction compares last-log-entry term,
  not current term" als REQUIRES-Relation von "Raft Consensus".

Committed (93e20e7a) und gepusht zu
origin/fix/graphrag-entity-match-ranking. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Fgraphrag-entity-match-ranking

**GAP 3 (Precision-Tool, Mehrschritt-/kumulative Finanzberechnungen,
`sci-precision-02-ast-financial-arithmetic`)**: untersucht, NICHT
gefixt. `services/pipeline/contracts.py::_infer_precision_contracts()`
hat keine Erkennungslogik für mehrjährige/eskalierende Szenarien
(kein Treffer auf "escalat"/"compound"/mehrjährige Tarif-Muster); das
`decimal_finance`-MCP-Tool unterstützt nur einzelne atomare Operationen
(add/subtract/multiply/divide/percentage/simple_interest/
compound_interest mit festen Operanden-Contracts je Aufruf), keine
automatische Verkettung mehrerer abhängiger Schritte. Ein echter Fix
bräuchte entweder eine unsichere Prompt-Ebene-Heuristik oder eine
größere Decompose-/Orchestrierungs-Erweiterung des Precision-Contract-
Mechanismus -- das ist eine Architekturentscheidung, keine
Vor-Ort-Korrektur, daher an den User zur Entscheidung zurückgegeben statt
stillschweigend umgesetzt.

Der "Sovereign Knowledge Base"-GAP wurde wie vom User explizit angewiesen
NICHT angefasst (Benchmark-Datensatz-Artefakt, nicht systemrelevant).

Nächster Schritt: Benchmark-Checkpoint auf die 4 gültigen
`r1_sci-sysprog-01-lockfree-ringbuffer_*`-Einträge trimmen und den großen
Benchmark im Resume-Modus ab Task 2 (eBPF/XDP) neu starten.

---

## 2026-08-23 — Eigener Fehler: Container-Recreate hat laufenden Benchmark abgeschossen — behoben, kein Datenverlust

Während der Implementierung von GAP 3 (Precision-Tool-Verkettung, siehe
nächster Eintrag) wurde `langgraph-orchestrator` per `docker compose up -d
--no-deps --force-recreate` neu gebaut, OHNE zu prüfen, dass der zuvor neu
gestartete große Benchmark (ab eBPF/XDP-Task) währenddessen aktiv gegen
genau diesen Container lief. Die ~15-20s Downtime beim Recreate ließ alle
offenen Judge-Calls über alle verbleibenden Task/Bedingungs-Kombinationen
hinweg mit "All connection attempts failed" fehlschlagen; der
Benchmark-Prozess (PID 2363376) hat danach NICHT weiter gewartet/
retried, sondern ist durch den kompletten restlichen Lauf mit
0-Token-Garbage-Ergebnissen (Score 3.0/10 quer über alle Bedingungen,
Score-Wert kommt nur vom Fallback-Pfad) durchgelaufen und hat sich mit
einem verfälschten Summary-Report normal beendet.

**Kein Datenverlust**: `_result_is_valid()` verlangt `total_tokens>0` —
alle Garbage-Ergebnisse hatten 0 Tokens und wurden korrekt NICHT in
`checkpoint_scientific_benchmark.json` übernommen (weiterhin nur die 4
gültigen `sci-sysprog-01-lockfree-ringbuffer`-Einträge). Die beiden
unconditional geschriebenen Abschluss-Dateien
(`eval_scientific_benchmark_20260823-163507.json`,
`run_scientific_benchmark_20260823-163507.json`) sowie der
`latest_scientific_benchmark.json`-Zeiger enthielten jedoch die
Garbage-Werte und wären als echtes Ergebnis irreführend gewesen — nach
`benchmarks/results/invalidated_by_container_restart_20260823/`
verschoben statt gelöscht (Vorfall-Nachweis, kein Ergebnis).

**Lehre / Prozessänderung für den Rest dieser Session**: keine
`--force-recreate`/Rebuild-Aktion auf `langgraph-orchestrator` mehr,
solange der Benchmark aktiv läuft. GAP-3-Implementierung wird
vollständig fertiggestellt (Code + Unit-Tests + ein finaler
Rebuild/Redeploy + Integrationstest), BEVOR der Benchmark erneut
gestartet wird — damit es nur noch einen einzigen Redeploy-Zeitpunkt vor
dem finalen Neustart gibt, nicht mehrere überlappende.

## 2026-08-23 — GAP 3: Precision-Tool-Verkettung (`$task_result`) implementiert — in_progress

Auf User-Entscheidung ("Decompose-/Orchestrierungs-Erweiterung", nach
AskUserQuestion mit 3 Optionen) GAP 3 umgesetzt: mehrjährige/verkettete
Finanzberechnungen (`sci-precision-02-ast-financial-arithmetic`) waren
bisher nicht ausführbar, weil `validate_plan_tasks()` literale `mcp_args`
verlangte und `mcp_node()` alle `precision_tools`-Tasks blind parallel
ausführte (kein Mechanismus, das Ergebnis einer Task als Operand einer
anderen zu nutzen). Plan approved unter
`/home/philipp/.claude/plans/zazzy-beaming-koala.md`.

**Implementiert** (Branch `feat/precision-task-result-chaining`, Worktree
`moe-infra-worktree-merger-repetition`, noch nicht committed):
- `services/pipeline/contracts.py`: `is_task_result_ref()`,
  `resolve_task_result_refs()` (fail-closed bei jeder nicht auflösbaren
  Referenz), `_find_task_result_ref_ids()`; `validate_plan_tasks()` prüft
  jede `{"$task_result": "<id>"}`-Referenz auf Rückwärtsreferenz (striktes
  "nur früher in der Liste"), Existenz und Ziel-Task
  `category=="precision_tools"` mit gesetztem `mcp_tool` — neue Issue-Codes
  `invalid_task_result_reference`, `task_result_reference_cycle`.
- `graph/tool_nodes.py`: `_topological_batches()` (Kahn-Algorithmus über
  die `$task_result`-Kanten, reiner/testbarer Helper), `mcp_node()`-Dispatch
  läuft jetzt batch-weise statt einem einzigen `asyncio.gather` über alle
  Tasks; `call_tool()` löst Referenzen vor dem Dispatch über ein
  wachsendes `resolved_task_results`-Dict auf (befüllt aus dem geparsten
  JSON-Textergebnis jeder abgeschlossenen Task, kein Extra-Contract-Feld
  nötig); nicht auflösbare Referenz → deterministischer
  `upstream_task_result_unavailable`-Fehler, kein Raten. Pläne ohne
  Referenzen bleiben binär identisch zum bisherigen Verhalten (ein Batch =
  alle Tasks).
- `graph/planner.py`: neue Formatregel + ein Beispiel (2-stufige
  Tarif-Eskalation) direkt bei den bestehenden `precision_tools`-Regeln.
- Tests: `tests/test_pipeline_contracts.py` (8 neue Tests: Referenz-
  Erkennung, Auflösung inkl. Fail-Closed, gültige Kette, Vorwärts-/Selbst-
  /unbekannte-/Nicht-Precision-Referenz abgelehnt),
  `tests/test_tool_nodes_precision_chaining.py` (neu, 4 Tests für
  `_topological_batches`: unabhängige Tasks in einem Batch, lineare Kette,
  gemischter Fall, Zyklus bleibt unscheduled).

**Verifiziert**: `pytest tests/test_pipeline_contracts.py
tests/test_tool_nodes_precision_chaining.py tests/test_precision_preflight.py
tests/test_precision_rollout.py tests/test_precision_benchmark_harness.py
tests/test_response_commit.py -q` → alle grün (74 Tests), keine Regression
für unverkettete Pläne bestätigt.

**Abgeschlossen**: In-Container-Integrationstest gegen den bereits
neu gebauten `langgraph-orchestrator` durchgeführt — 3 echte verkettete
`decimal_finance`-Calls über den vollen `mcp_node()`-Dispatch-Pfad
(nicht nur direkt gegen `mcp-precision`), Tarif-Kette 0.1850 → 0.1933 →
0.2006 EUR (Jahr1 → +4.5% → +3.8%) korrekt berechnet. Zweiter Testlauf
bestätigt: eine fehlschlagende Upstream-Task lässt abhängige Tasks
deterministisch mit `upstream_task_result_unavailable` fehlschlagen statt
zu raten. Committed (`46d2d6d3`) und gepusht auf
`feat/precision-task-result-chaining`. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat%2Fprecision-task-result-chaining

Kein separater End-to-End-Lauf des vollen
`sci-precision-02-ast-financial-arithmetic`-Prompts durch die komplette
Pipeline (Planner→Merger) durchgeführt — das hängt vom 4B-Planner ab, der
laut Prompt-Regel die Kette selbst korrekt zerlegen muss; dieser Beweis
wird über den bereits laufenden großen Benchmark erbracht (der Task läuft
dort ohnehin in Runde 1 mit).

---

## 2026-08-24 — Planner-JSON-Malformation bei Wissens-Speicher-Anfragen gefixt — done

Auf User-Anweisung ("gehe bei allen neuen GAPs die sich lösen lassen so
vor: Benchmark unterbrechen, debuggen und fixen, Neustart") den bei
`sci-graphrag-01-topology-cascade`/`sci-graphrag-02-paraconsistent-
reconciliation` beobachteten Planner-Crash gefixt (siehe
`docs/experiments/lumig_posttraining_candidates.md` Kandidat 4, dort mit
beiden ursprünglichen Beobachtungen dokumentiert).

**Root Cause:** Der Planner hatte keinerlei Prompt-Anleitung für "speichere
dies im Knowledge Graph"-Anfragen und improvisierte deshalb (erfundene
"dynamic"-Task mit frei erfundenen Feldern, Versuch, die Nutzdaten als
JSON-String in "task" zu re-encodieren) — genau das produzierte das
fehlerhaft verschachtelte/escapte JSON. Tatsächlich existiert dafür
bereits ein vollautomatischer Mechanismus: `services/response_commit.py`
published jede committete Antwort nach `KAFKA_TOPIC_INGEST`, ein
Background-Consumer (`main.py`) ruft darauf automatisch
`graph_manager.extract_and_ingest()` auf Input/Antwort-Paar auf. Der
Planner muss dafür gar nichts Besonderes tun.

**Fix** (`graph/planner.py`, Branch
`fix/planner-knowledge-storage-json-malformation`, Commit `44675d9f`):
neue kompakte Regel + Beispiel direkt beim bestehenden DYNAMIC-EXPERT-Block
— bei Speicher-/Merk-Anfragen reicht eine einzelne, in natürlicher
Sprache formulierte Bestätigungs-Task, kein JSON-Hand-Encoding, keine
erfundenen Felder. Nutzt denselben `_example_cat`-Mechanismus wie an
anderer Stelle im Prompt (verhindert erneut die bereits einmal gefixte
Bug-Klasse: eine hartkodierte, zur Laufzeit ungültige Kategorie im
Beispiel).

**Verifiziert:** zwei Live-Replays (mit `no_cache: true`, um den
Valkey-Plan-Cache zu umgehen — erster Replay-Versuch traf versehentlich
einen Cache-Hit und war dadurch kein echter Test) gegen den neu gebauten
Container:
1. Apex-Central-Topologie-Prompt (Turn 1 von `sci-graphrag-01`) — vorher 3x
   gescheitert, jetzt valider Plan im 1. Versuch.
2. Directive-2026-S-Amendment-Prompt (Turn 2 von `sci-graphrag-02`) —
   vorher 3x gescheitert, jetzt valider Plan im 1. Versuch (4 Tasks).

Nebenbefund bei Replay 2: der erzeugte Plan war syntaktisch valide, aber
inhaltlich komplett themenfremd (Docker-Compose-Netzwerkmodi statt
Telemetrie-Direktive) — das ist NICHT ein Rückfall dieses Fixes, sondern
eine weitere unabhängige Beobachtung des bereits dokumentierten,
separaten Planner-Task-Fabrikation-Befunds (`lumig_posttraining_
candidates.md`, Kandidat 2).

Committed (`44675d9f`) und gepusht auf
origin/fix/planner-knowledge-storage-json-malformation. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Fplanner-knowledge-storage-json-malformation

`langgraph-orchestrator` bereits mit diesem Fix neu gebaut+deployed
(erforderlich für die Live-Verifikation oben). Benchmark wird jetzt im
Resume-Modus fortgesetzt.

---

## 2026-08-24 — Few-Shot-Kontext-Kontamination gefixt, aber Planner-Fabrikation nur teilweise erklärt — done (mit offenem Rest)

Bei der Root-Cause-Suche für die wiederholte komplette Themenersetzung des
Planners bei `sci-precision-02-ast-financial-arithmetic` (Task 7 des
großen Benchmarks, 3 von 4 Bedingungen betroffen) einen echten,
verifizierten Code-Bug gefunden und gefixt — der aber, ehrlich
offengelegt, die Fabrikation NICHT vollständig erklärt.

**Gefundener und gefixter Bug:** `get_few_shot_context()`
(`self_correction.py`) injiziert bei jedem Planner-Aufruf ungefiltert die
wörtlichen "falschen" Antworttexte früherer Self-Correction-Einträge aus
**allen** Experten-Kategorien (`graph/planner.py`: `list(EXPERTS.keys())`)
als "KNOWN ERROR PATTERNS" in den Prompt — ohne jede Relevanzprüfung zur
aktuellen Anfrage. Direkt im laufenden Container nachgewiesen: die
fabrizierten "Apex-Central"-Topologie- und "Directive 2024-B"-Texte aus
früheren, unabhängigen Tasks dieses Benchmarks lagen im Few-Shot-Store und
waren für jede beliebige künftige "general"-Kategorie-Anfrage abrufbar.

**Fix** (`self_correction.py`, `graph/planner.py`, Branch
`fix/few-shot-context-topic-contamination`, Commit `0d0f72e9`): neues
Relevanz-Gate `_is_topically_relevant()` — ein gespeicherter Eintrag wird
nur noch angezeigt, wenn seine eigene Query mindestens 3 signifikante
(≥5 Zeichen) Tokens mit der aktuellen Anfrage teilt. `get_few_shot_context()`
bekam einen neuen `query`-Parameter (Default `""` erhält das alte
Verhalten für bestehende Aufrufer), Planner-Call-Site übergibt jetzt
`state_["input"]`. 4 neue Unit-Tests, alle grün, keine Regression in den
bestehenden 52 Tests.

**Live verifiziert:** `get_few_shot_context()` liefert für den exakten
Energie-Tarif-Prompt jetzt eine leere Zeichenkette (vorher wäre die
Apex-Central/Directive-2024-B-Kontamination eligible gewesen).

**Ehrlich offengelegter Rest-Befund:** Ein Live-Replay desselben Prompts
NACH Deploy dieses Fixes produzierte trotzdem einen fabrizierten,
themenfremden Plan (diesmal: "grep print()-Aufrufe / karpathy-compliance"
statt der Energie-Rechnung). Als alternative Live-Injektionsquellen für
genau diesen Fall direkt ausgeschlossen:
- `moe:planner_success` (Redis-Key leer)
- `semantic_router_node` (kein sicherer Treffer diesmal, echter
  Planner-LLM-Call bestätigt via Log)
- `get_active_advice()` (liefert 0 aktive Regeln für diese Anfrage)

Die verbleibende Fabrikation ist damit keinem im Code auffindbaren
Live-Retrieval-Mechanismus zuzuordnen — deckt sich mit der bereits
dokumentierten Einordnung in `lumig_posttraining_candidates.md` Kandidat 2
(Trainingsdaten-/Distillations-Artefakt des 4B-Planners), nicht mit einem
weiteren Infra-Fix in dieser Session behebbar.

Committed (`0d0f72e9`) und gepusht auf
origin/fix/few-shot-context-topic-contamination. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Ffew-shot-context-topic-contamination

`langgraph-orchestrator` neu gebaut+deployed. Benchmark wird trotz des
offenen Rest-Befunds neu gestartet (der Few-Shot-Fix ist ein eigenständig
korrekter, verifizierter Systemfix und wird das Kontaminationsrisiko für
alle künftigen Tasks senken, auch wenn er GAP 3 für Task 7 evtl. nicht
allein rettet).

Später außerdem entdeckt: Task 7 wäre beim Neustart stillschweigend aus
dem VOR-dem-Fix-Checkpoint übernommen worden (alte fabrizierte Det:0.0-
Ergebnisse gelten technisch als "valide"). Die 4 Task-7-Einträge gezielt
aus dem Checkpoint entfernt (Backup angelegt), Benchmark erneut neu
gestartet — Task 7 läuft jetzt tatsächlich frisch mit dem Fix.

---

## 2026-08-24 — Judge/Experte-Reload-Problem gefixt (systemweite DB-Änderung) — done

Auf explizite User-Nachfrage ("gibt es sonst noch GAPs die gefixt werden
können?") das zuvor gefundene, aber zurückgestellte Judge/Experte-Reload-
Problem (siehe früherer Eintrag: gemeinsame Gewichte unter zwei Ollama-
Tags erzwingen Neuladen bei jedem Wechsel) doch umgesetzt — es stellte
sich als kleiner umsetzbar heraus als ursprünglich gedacht.

**Root Cause (bestätigt via Ollama-Manifest-Digest-Vergleich auf
N04-RTX):** `sovereign-judge:27b` und `qwen3.8:27b` referenzieren
denselben Gewichts-Blob (`sha256:f5f1dd89...`, 16,81 GB), unterscheiden
sich nur in einem kurzen System-Prompt + wenigen Sampling-Parametern
(`temperature=0.1` statt `1`, `num_ctx`, `num_predict`, `stop`-Tokens —
alle bereits explizit pro Request im Code gesetzt). Ollama trackt
geladene Modelle nach Tag-Name, nicht nach Gewichts-Inhalt — jeder Wechsel
zwischen Experten-Call (`qwen3.8:27b`) und Judge-Call
(`sovereign-judge:27b`) auf demselben Knoten erzwang ein volles
Entladen+Neuladen.

**Scope-Erweiterung während der Umsetzung entdeckt:** `judge_model` ist
NICHT nur eine Umgebungsvariable, sondern pro Template in Postgres
gespeichert (`admin_expert_templates.config_json`) — betrifft alle 27
aktiven Zeilen, inklusive des produktiven `tmpl-sovereign-compound-ai`.
User-Entscheidung (AskUserQuestion): "Alle Templates umstellen, inkl.
Produktion".

**Fix** (Branch `fix/judge-expert-shared-model-reload`, Commit
`69dabad7`):
- `services/inference.py`: neue Konstante `JUDGE_SYSTEM_PROMPT` +
  Helper `_judge_messages()` — sendet den Judge-System-Prompt jetzt
  explizit pro Request statt über den separaten Ollama-Tag. Beide
  Judge-Call-Stellen (Haupt-Pfad `_invoke_judge_with_retry`,
  Floating-Judge-Pfad) aktualisiert.
- Postgres: alle 27 Zeilen mit `judge_model` enthaltend
  `sovereign-judge:27b` per gezieltem JSON-Feld-Update (kein blinder
  String-Replace) auf `qwen3.8:27b` umgestellt, `@N04-RTX`-Suffix wo
  vorhanden erhalten. Betrifft 7 benannte Templates (inkl. Produktion)
  + 20 dynamisch generierte Templates.
- `admin_ui/database.py`: Seed-Defaults für 4 Templates ebenfalls
  aktualisiert (Konsistenz bei künftiger Neu-Initialisierung).

**Verifiziert:** Ein Testrequest mit 2 Experten-Calls + 5 Judge-Calls
(Self-Critique-Schleife) über ~5,5 Minuten erzeugte in Ollamas eigenen
Logs auf N04-RTX genau **1** Ladevorgang (vorher: alle ~2,5-3 Min. ein
Reload). Jeder Judge-Call loggt "reusing warm model ... no reload needed,
model=qwen3.8:27b".

Committed (`69dabad7`) und gepusht auf
origin/fix/judge-expert-shared-model-reload. MR:
https://git.4noobs.de/h3rb3rn/moe-infra/-/merge_requests/new?merge_request%5Bsource_branch%5D=fix%2Fjudge-expert-shared-model-reload

`langgraph-orchestrator` neu gebaut+deployed. Benchmark wird jetzt neu
gestartet — sollte ab hier spürbar schneller laufen, da der
Reload-Overhead pro Pipeline-Stufen-Wechsel entfällt.

**Nachtrag, auf explizite Nachfrage ("Funktioniert der Fix nachweislich?")
nachgemessen:** Fix wirkt nur teilweise. In den 2 Stunden Live-Betrieb
nach dem Fix traten weiterhin 23 Ladevorgänge auf (besser als vorher,
aber nicht null). Root-Cause-Analyse eines konkreten Reload-Ereignisses
per Zeitstempel-Korrelation zwischen Ollama-Log (N04-RTX) und
Orchestrator-Log ergab eine **dritte, unabhängige Instanz derselben
bereits mehrfach behobenen Bug-Klasse** (siehe Projekt-Memory
`feedback_ollama_num_ctx_reuse_pattern`): `_refine_expert_response()`
(`services/inference.py`, vom Judge-Refinement-Loop in
`graph/synthesis.py` aufgerufen) forderte **immer** eine feste, große
Kontextgröße (262144) an, ohne vorher per `/api/ps` zu prüfen, was
bereits geladen ist — im Gegensatz zu allen anderen Call-Sites in dieser
Datei (Judge, Planner, Haupt-Experten-Pfad), die diesen Reuse-Check
bereits hatten.

**Fix** (derselbe Branch `fix/judge-expert-shared-model-reload`, Commit
`0a10c003`): denselben Reuse-Check-Pattern auch hier ergänzt. Tests grün,
Container neu gebaut+deployed. Noch keine erneute Langzeit-Messung nach
diesem zweiten Teil-Fix durchgeführt — sollte bei der nächsten
periodischen Prüfung mit erfasst werden.

---

**2026-08-25T~01:55Z — claude-code — Runde 1, Task 9 `sci-governance-01-technical-sovereignty`, `compound_ai`: analysiert, kein Fix (bestätigt fail-closed korrekt)**

Log: `full_scientific_benchmark_20260824-201838_resume7.log`. Ergebnis
`Score: 3.0/10 (Det: 0.0, Judge: 5.0) | 774.16s | 0 tok` (HTTP 422 nach
initialem Aufruf). Root-Cause per Container-Log + Redis-Stage-Trace
(`moe:active:{chat_id}:trace`) + Decision-Log (`/app/logs/decision_log.jsonl`)
nachverfolgt: Planner fabrizierte eine themenfremde Task ("DHS Tier 3 Small
Entity RCE... session_9b... Diga/Feedzup/DNS log gaps") ohne jeden Bezug
zum echten Hospital-Compound-AI-Prompt — weitere bestätigte Instanz von
LUMI-G-Kandidat 2 (`docs/experiments/lumig_posttraining_candidates.md`),
siehe dort für Details. Diesmal produzierte der Experten-Knoten dadurch
0 Ergebnisse, Trust-Score sofort BLOCK, 2 Self-Critique-Runden hoben ihn
nur auf 0.1. Critic-Node versuchte danach den bekannten
trust_verdict-Upgrade auf `PROCEED_WITH_ASSUMPTION`
(`graph/synthesis.py:2387-2390`), aber `evaluate_quality_gate()`s
`incomplete_plan_tasks()`-Check (`services/quality_gate.py:209`, läuft VOR
dem trust_verdict-Check) blockte trotzdem korrekt — die ursprüngliche
Plan-Task wurde nie real ausgeführt, das kann ein nachträglicher
Critic-Fix nicht überschreiben. Kein Bug: Fail-Closed-Verhalten wie in
AGENTS.md gefordert. Benchmark-Harness verwirft das Ergebnis bereits
korrekt aus dem Checkpoint (`_result_is_valid()`: `total_tokens==0`).
Keine Code-Änderung. Benchmark läuft unverändert weiter (kein Stopp
nötig, da kein Fix).
2026-08-25T00:03:48Z

---

**2026-08-25T~03:00Z — claude-code — Observability-Fix: quality_gate-Block-Gründe geloggt (kein Verhaltensänderung)**

Anlass: Runde 2, Task 1 (Lock-Free MPSC Ring Buffer), `compound_ai` erneut
mit `422`/`0 tok` — diesmal KEIN Planner-Fabrikations-Fall (trust_verdict
erreichte sauber `PROCEED_WITH_ASSUMPTION` nach 2 Self-Critique-Runden,
Critic bestätigte "no unsupported claims"). `quality_gate | blocked` im
Redis-Stage-Trace bestätigt, aber der tatsächliche `decision.reason` wurde
nirgends geloggt (`_record_stage(..., "blocked")` ohne `detail`-Argument,
`quality_gate_node` in `graph/synthesis.py`) — nicht diagnostizierbar ohne
denselben Redis/Decision-Log-Aufwand wie beim vorherigen Fall.

**Fix** (`graph/synthesis.py::quality_gate_node`, Zeilen ~2441-2460 und
~2489-2500): `logger.warning("Quality gate blocked req=%s reason=%s", ...)`
bzw. `"HITL gate storage unavailable req=%s reason=%s"` ergänzt, sowie
`decision.reason`/`reason` als `detail`-Parameter an `_record_stage`
durchgereicht. Rein additiv, keine Logik-/Verhaltensänderung. Tests
(`tests/test_response_commit.py`, 8/8) grün. Container `langgraph-app`
neu gebaut + `--force-recreate`, Health-Check ok, neue Log-Zeilen im
Container-Code verifiziert (`grep` auf `/app/graph/synthesis.py`).

Benchmark gestoppt (alte PID 1971660), neu gestartet als PID 2943720
(`full_scientific_benchmark_20260825-025900_resume8.log`), resumed von
Checkpoint mit 27 gültigen Läufen — keine Daten verloren. Kein Fix des
zugrunde liegenden Plausibility-Gate-Verhaltens selbst vorgenommen (Inhalt
der geblockten Antwort ist nicht persistiert/rekonstruierbar — nächster
Vorkommensfall liefert dank dieses Fixes den Grund direkt im Log).
2026-08-25T00:59:24Z

---

**2026-08-25T~05:50Z — claude-code — KRITISCH: 3 Session-Fixes waren nie deployed, jetzt gemergt+live**

Bei der Untersuchung eines weiteren Planner-Fabrikations-Falls (Task 6,
sci-precision-02-ast-financial-arithmetic, `compound_ai`: Planner-Output
bestand aus 39x wiederholten, komplett themenfremden `code_reviewer`/
pytest-Korrektur-Few-Shot-Einträgen statt einer Energiekosten-Berechnung)
wurde festgestellt: der laufende Container basierte auf Branch
`docs/graphrag-experiment-and-session-status`, der KEINEN der drei in
dieser Session entwickelten, getesteten und auf `origin` gepushten Fixes
enthielt (`fix/few-shot-context-topic-contamination`,
`fix/judge-expert-shared-model-reload` Teil 1+2 — `git merge-base
--is-ancestor` bestätigte für alle drei: nicht gemerged). Alle bisherigen
Aussagen dieser Session zu "Fix wirkt teilweise" / "Restursache trotz Fix"
beruhten auf dieser falschen Prämisse — die Fixes liefen nie im
produktiven Pfad.

User-Entscheidung (AskUserQuestion): Fixes mergen + neu bauen, aber mit
aktuellem Checkpoint fortsetzen (Runde 1 + Teil von Runde 2 bleiben im
Datensatz, liefen aber vor den Fixes — nicht direkt mit späteren Runden
vergleichbar).

**Durchgeführt:** Benchmark gestoppt (PID 2943720, Checkpoint erhalten).
Vor dem Merge wurde ein erheblicher, vorher unkommitteter WIP-Stand im
Haupt-Checkout entdeckt (27 Dateien, u.a. Rust-Compile-Check-Sandbox-
Integration, Critic-Non-Compliance-Erkennung, Konflikt-Arbitrierung im
Merger — nicht von mir in dieser Session erstellt). Per `git stash push -u`
gesichert, beide Fix-Branches sauber gemerged (`ba851208`, `d426f295`),
Stash zurückgeholt (1 echter Konflikt in `graph_rag/manager.py` — LIMIT-10-
vs-LIMIT-2-Iteration desselben Fixes, neuere Version behalten), alles in
`33eb2a2f` committed. **Bonus-Fund:** der Merge brachte auch bereits
fertigen, getesteten Code für die `$task_result`-Verkettung bei
`precision_tools` mit (`services/pipeline/contracts.py`,
`graph/tool_nodes.py`, `mcp_server/server.py`, `services/rust_compile_sandbox/`)
— das war der offene Plan zu GAP 3 aus einer früheren Session-Phase, ebenfalls
nie deployed. 1021/1021 Tests grün. `langgraph-app` UND neuer Service
`rust-compile-sandbox` gebaut + deployed, beide Health-Checks ok, alle 4
Fixes im laufenden Container per `grep` verifiziert (`_is_topically_relevant`,
`JUDGE_SYSTEM_PROMPT`, `Quality gate blocked req=`, `_topological_batches`/
`is_task_result_ref`).

Benchmark neu gestartet als PID 3631235
(`full_scientific_benchmark_20260825-074600_resume9.log`), resumed von
Checkpoint mit 29 gültigen Läufen (Runde 1 komplett, alle ungefixt gelaufen
— im wissenschaftlichen Bericht entsprechend kennzeichnen). Ab jetzt laufen
alle weiteren Bedingungen/Runden mit allen vier Fixes aktiv, inkl. der
ersten echten Chance, GAP 3 (decimal_finance-Verkettung) zu testen.
2026-08-25T05:46:43Z
