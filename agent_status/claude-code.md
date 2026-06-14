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
