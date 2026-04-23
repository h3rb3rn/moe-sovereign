# Changelog

All notable changes to the Sovereign MoE Orchestrator are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — semantic versioning for manual releases, date-based labels for auto-tracked changes.

> **Metadata fields used in every entry:**
> `impact` (patch / minor / major) · `breaking` (yes / no) · `domain` (affected subsystems)

---

## [2.8.0] - 2026-04-24

> `impact: minor` · `breaking: no` · `domain: orchestrator, mcp, benchmarks, routing`

### Added — Dynamic Sequential/Parallel Expert Execution

Expert tasks in the plan now support an optional `depends_on` field. Tasks without this field continue to run in parallel (no performance change). Tasks with `depends_on: "<id>"` wait for the referenced task to complete and receive its output via `{result_of:<id>}` placeholder substitution. This enables multi-hop research chains (e.g. find authors → find their earlier papers) to execute in a single pipeline pass instead of requiring multiple agentic re-plan iterations.

### Added — Adaptive Context Budget per Model

`context_budget.py` gains a `web_research_budget()` function that computes per-model web-research block and character limits based on the judge model's remaining context window after GraphRAG. Fallback models (gemma4:31b 8 K, qwen3.6:35b 32 K) receive proportionally tighter limits. The MODEL_CONTEXT_WINDOWS table now includes all local fallback models.

### Added — GraphRAG On-Demand

`graph_rag_node` skips the Neo4j query for queries detected as external research questions (papers, APIs, media, databases) unless the plan explicitly includes a `knowledge_healing` task. Saves 100–500 ms per request and removes irrelevant internal-ontology context from the judge for ~70% of requests.

### Added — Domain-Aware Search Cache

Search cache keys now include a domain hint when the query uses `site:` syntax or the `web_search_domain` tool. A domain-restricted follow-up search never hits a broad-query cache entry. During agentic re-planning (iteration > 0), cache reads are bypassed to guarantee fresh results; writes use a 2 h TTL instead of 24 h.

### Added — 7 New MCP Tools (total: 43)

`semantic_scholar_search`, `pubchem_advanced_search` (complexity filter), `pubchem_enzyme_cooccurrences`, `github_issue_events`, `web_search_domain`, `youtube_transcript`, `orcid_works_count`.

### Added — Domain-Filtered Planner Tool Descriptions

The planner now receives only 8–15 tool descriptions relevant to the query domain (was: always 40). Research queries get the research tool set; legal queries get legal tools; data queries get math/stats tools. Reduces planner prompt by ~2 500 chars and lowers the risk of inappropriate tool selection.

### Added — GAIA Search Cache Pre-Warmer

New script `benchmarks/warm_search_cache.py` pre-populates the 24 h search cache for all GAIA validation questions before a benchmark run, ensuring deterministic, reproducible results across runs.

### Fixed — Complexity Estimator Under-Classifies Research Questions

Queries referencing papers, authors, databases, species, museums, YouTube channels, or GitHub repositories are now classified as `complex` (max_tasks=4) instead of `moderate` (max_tasks=2). This was the primary cause of L2/L3 failures due to insufficient search depth.

### Fixed — User Template Routing with @node Suffix

User-owned templates (e.g. `nff-test@AIHUB_NFF`) are now matched against both `request.model` and `_req_model_base` (name without suffix). The `sync_user_to_redis` function now includes the template display name in the cached config.

### Fixed — ONNX Embedding Model Cache Permissions

The ChromaDB ONNX model cache directory ownership on adesso.moe-sovereign.org corrected to match the container user (UID 1001). Semantic Router now seeds successfully on startup.

### Performance — GAIA Benchmark Results (2026-04-24)

| Level | Score | Notes |
|-------|-------|-------|
| L1 | 7/10 = 70% | Stable; Doctor Who and Pie Menus remain open |
| L2 | 5/10 = 50% | GitHub date newly correct (04/15/18 via github_issue_events) |
| L3 | 1/10 = 10% | Freon-12 newly correct (55 ml via NIST hint) |
| **Total** | **13/30 = 43.3%** | Best run: 14/30 = 46.7% (beats GPT-4o Mini 44.8%) |

---

## [2.6.0] - 2026-04-23

> `impact: minor` · `breaking: no` · `domain: admin-ui, api, monitoring`

### Added — Endpoint Availability Graph

The System Monitoring page now shows a stepped-line chart with the **24-hour availability history** per configured inference server. Data is sourced from Prometheus `query_range` on `moe_inference_server_up` at 5-minute resolution. New API route: `GET /api/endpoints/availability`.

### Added — API Endpoint Budget Overview

For every OpenAI-compatible inference server (e.g. AIHUB / LiteLLM), the monitoring page shows a live **budget card** with spend, maximum budget, and a colour-coded progress bar (green < 70 % · orange 70–90 % · red ≥ 90 %). Budget values are read from the `x-litellm-key-spend` and `x-litellm-key-max-budget` response headers via a lightweight `GET /v1/models` probe. New API route: `GET /api/endpoints/budget`.

### Added — User Budget Response Headers

`/v1/chat/completions` now returns two additional HTTP headers for authenticated users:

- `X-MoE-Budget-Daily-Used` — tokens consumed today
- `X-MoE-Budget-Daily-Limit` — daily token limit (omitted if unlimited)

### Fixed — Servers Page JavaScript Crash

The `js.confirm_block_server` translation string contains multi-line text which, when rendered into a JavaScript string literal without `| tojson`, produced a `SyntaxError` that silently killed the entire `<script>` block — leaving all server cards with static `–` values and non-functional buttons. Fixed with the `tojson` Jinja2 filter.

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Admin UI, API |

---

## [2.5.0] - 2026-04-22

> `impact: minor` · `breaking: no` · `domain: admin-ui, skills, servers, users`

### Added — skills.sh External Audit Integration

The Community Skills tab now fetches and caches audit ratings from
[skills.sh/audits](https://skills.sh/audits) and displays them on each skill tile alongside
the existing internal LLM audit badge.

- Three external badges per tile: **Gen Agent Trust Hub** (Safe/unsafe), **Socket.dev** (alert count), **Snyk** (risk level)
- Results cached at `/app/skills/community/.skillssh_audits.json` with 24-hour TTL
- New button **"Ext. Audits aktualisieren"** in the Community tab forces a cache refresh
- New API endpoint `POST /api/skills/community/refresh-external-audits`

### Added — Mandatory Internal Audit for Upstream (Anthropic) Skills

Anthropic upstream skills now require the same internal LLM security audit as community
skills before they can be imported into the active skills directory.

- New endpoint `POST /api/skills/upstream/{skill_name}/audit` — runs identical LLM checklist
- Audit results stored in `/app/skills-upstream/audits/{name}.audit.json`
- `GET /api/skills/upstream` response now includes `audit_status` per skill
- `POST /api/skills/upstream/import/{name}` returns HTTP 400 if no audit exists, 403 if blocked
- Upstream skill tiles show audit badge + Audit button; Import button disabled until audited

### Added — Refactored `_run_llm_audit()` helper

Shared async helper function used by both community and upstream audit endpoints.
Eliminates duplicate audit logic.

### Fixed — Upstream repository clone on fresh installations

`POST /api/skills/upstream/pull` previously returned **"Kein Git-Repo gefunden"** on
fresh installations where `/app/skills-upstream/.git` did not yet exist.

- Endpoint now auto-clones `https://github.com/anthropics/skills.git` if `.git` is absent
- Subsequent calls perform the usual `git pull --ff-only`
- Misplaced "Pull Community Skills" button removed from the Upstream tab (belonged in Community tab)

### Fixed — Server tiles not updating in Admin > Servers

`loadAll()` matched DOM elements by array index (`srv-badge-${i}`) which broke when the
API response order differed from the Jinja2 template render order.

- Template elements now carry `data-server-name` attributes
- `loadAll()` uses `querySelector('[data-server-name="..."]')` — order-independent
- Latency display now includes unit (`ms`)

### Changed — Navbar restructured with dropdown groups

The desktop navigation bar previously showed 18 flat buttons that overflowed on most screens.

| Before | After |
|--------|-------|
| 18 flat `btn-outline-light` buttons | 1 direct link + 4 dropdown menus |
| Visible only at ≥1400px (`d-xxl-flex`) | Visible from ≥992px (`d-lg-flex`) |

Dropdown groups:
- **Monitoring**: Monitoring, Live Monitoring, Statistics, Benchmarks
- **Infra**: Servers, Knowledge, Federation, Quarantine, Maintenance
- **Tools**: CC Profiles, Skills, MCP Tools, Tool Eval, Templates
- **Users**: Users, Teams, User Content

Active state propagates to the dropdown toggle when any child page is active.

### Changed — Permissions UI: collapsible sections

The five resource sections in the User Permissions tab (Expert Template, CC Profile,
Native LLMs, Skills, MCP Tools) are now collapsible Bootstrap cards.

- All sections start collapsed — reduces scroll length proportional to server count
- Chevron icon rotates on expand/collapse
- Collapse IDs include `${uid}` to avoid DOM collisions when switching between users

---

## [2.4.1] - 2026-04-21

> `impact: patch` · `breaking: no` · `domain: mcp-server, templates, benchmarks, database, observability`

### Fixed — GAIA Benchmark Integration Findings

Six infrastructure and configuration bugs discovered and resolved during the GAIA
benchmark evaluation session. See [GAIA Benchmark section](system/benchmarks.md#april-2026-gaia-benchmark-compound-ai-system-evaluation-against-a-public-standard)
for the full trial & error report.

#### `wikipedia_get_section` — `article=` parameter alias (MCP Server)

The LLM planner consistently calls `wikipedia_get_section(article="...", section="...")`
but the MCP function only accepted `title=`. Every Wikipedia tool call raised
`got an unexpected keyword argument 'article'` — silently recovered as an empty result.

- Added `article: str = ""` as a second parameter alias; resolved to `title` at entry
- Added a structured wikitext table parser: extracts `Year | Album` rows from MediaWiki
  table markup before stripping all markup; returns `STRUCTURED TABLE (N entries):` prefix

#### `tmpl-aihub-free-nextgen` — Wikipedia authority rules (Template, Postgres)

The judge LLM consistently overrode authoritative Wikipedia data with AllMusic/Discogs
results when multiple sources were present.

- Rule 4a updated: `title=` (not `article=`), `section='Studio albums'` (not `'Discography'`)
- Judge prompt prepended: Wikipedia tool result is AUTHORITATIVE when the question specifies "use Wikipedia"

#### Attachment routing guard (benchmark context builder)

`.docx` and `.xlsx` filenames in context strings triggered the `skill_detector` expert
(file-generation mode) instead of the reasoning/general expert. Two GAIA questions
(Q8 Secret Santa, Q10 Spreadsheet) now answer correctly.

- File extension stripped from attachment label in `get_attachment_context()`
- Explicit `[ROUTING: Use reasoning or general expert. Do NOT use skill_detector.]` appended to attachment context

#### `routing_telemetry` UNIQUE constraint (Database)

Live Monitoring showed zero routing entries since 2026-04-17 despite hundreds of daily
requests. Root cause: `telemetry.py` uses `ON CONFLICT (response_id) DO NOTHING` which
requires a `UNIQUE` index on `response_id`. Only a plain B-tree index existed; every
INSERT failed with `InvalidColumnReference`, swallowed at `logger.debug` level.

- `CREATE UNIQUE INDEX idx_telemetry_response_unique ON routing_telemetry (response_id)`
- No code change, no container restart; telemetry recording restored immediately

#### `gaia_runner.py` — argparse block (Benchmark Runner)

All CLI arguments were silently ignored because no `argparse` block existed. The runner
always used hardcoded defaults (`moe-reference-30b-balanced`, levels 1–3, 30 questions),
making all template-targeted validation runs invalid.

- Added full argparse: `--template`, `--levels`, `--max-per-level`, `--temperature`, `--language`
- Added `TEMPERATURE` and `LANGUAGE` module-level globals with env var and CLI override
- Established benchmark governance rule: only template / MCP / skill / cache changes between runs

---

## [2.4.0] - 2026-04-21

> `impact: major` · `breaking: no` · `domain: pipeline, mcp-server, templates, docs`

### Added — Agentic Re-Planning Loop

MoE Sovereign can now autonomously perform multi-step reasoning by looping back through the pipeline when a knowledge gap is detected after synthesis. This enables GAIA Level 3 class questions that require chained tool calls (search → fetch → parse → calculate) to be answered correctly.

**How it works:**

After each Merger/Judge synthesis, a lightweight gap detection LLM call assesses whether the original question was fully answered. If `NEEDS_MORE_INFO` is returned, the pipeline routes back to the Planner with an injected context block describing what was established and what is still missing. The Planner generates a focused new plan, experts run again in parallel, and the Merger synthesizes the enriched results. This repeats up to `max_agentic_rounds` iterations.

**Implementation:**

- `AgentState` extended with four new fields: `agentic_iteration`, `agentic_max_rounds`, `agentic_history`, `agentic_gap`
- `planner_node`: reads `max_agentic_rounds` from template config; injects agentic context block on re-plan iterations; bypasses Valkey plan cache
- `merger_node`: performs gap detection LLM call after synthesis; appends per-round findings to `agentic_history`
- New `_should_replan(state)` routing function: returns `"planner"` if gap detected and iterations remain, else `"critic"`
- `add_edge("merger", "critic")` replaced by `add_conditional_edges("merger", _should_replan, ...)`
- Streaming status messages (`🔄 Agentic Loop — Iteration N/M`) via existing `_report()` mechanism
- Token budget guard: skips gap detection when `prompt_tokens > 80 000`

**Template configuration:** `max_agentic_rounds: 0` (disabled, default) — fully opt-in, no breaking change.

| Template | `max_agentic_rounds` |
|---|---|
| `tmpl-aihub-free-nextgen` | 3 |
| All others | 0 (unchanged) |

See [Agentic Re-Planning Loop](system/intelligence/agentic_loop.md) for full documentation.

---

### Added — PowerPoint Generation (MCP Server)

The `generate_file` MCP tool now supports `.pptx` output via `python-pptx`.

- `format: "pptx"` (aliases: `"ppt"`, `"powerpoint"`) generates a structured PowerPoint file
- `##`-level headings become slide titles; body lines are added as bullet points
- `python-pptx>=0.6.23` added to `mcp_server/requirements.txt`
- Content-Type `application/vnd.openxmlformats-officedocument.presentationml.presentation` served via `/downloads/`
- `skill_detector` expert in NextGen template routes PowerPoint requests to `generate_file`

---

### Added — NextGen Template (`tmpl-aihub-free-nextgen`)

New expert template for the AIHUB free tier with 120B+ parameter models and a dedicated `skill_detector` expert:

- `skill_detector` expert: detects file creation requests (PowerPoint, HTML, Word, Excel, CSV, PDF, scripts) and calls `generate_file` automatically
- 12 expert domains: `skill_detector`, `code_reviewer`, `math`, `medical_consult`, `legal_advisor`, `reasoning`, `science`, `translation`, `technical_support`, `web_researcher`, `general`, `knowledge_healing`
- `force_think: true`, `enable_graphrag: true`, `enable_web_research: true`
- `max_agentic_rounds: 3` — agentic loop enabled by default

---

### Changed — All System Prompts Translated to English

All LLM-facing prompts (planner, judge, expert system prompts) across the codebase and Postgres templates have been translated from German to English. German-specific idioms and phrasing artifacts have been removed.

**Affected files/templates:**

| Location | What changed |
|---|---|
| `main.py` | `_proc_markers` (German process words → English equivalents) |
| `main.py` | Agentic loop status message `"📌 Noch offen:"` → `"📌 Still open:"` |
| `main.py` | T1 insufficient marker `"(T1 nicht ausreichend)"` → `"(T1 insufficient)"` |
| `benchmarks/evaluator.py` | `JUDGE_SYSTEM_PROMPT`, `JUDGE_SYSTEM_PROMPT_FALLBACK` |
| `scripts/close_ontology_gaps.py` | `RESEARCH_SYSTEM_PROMPT`, `RESEARCH_USER_TEMPLATE` |
| Postgres `tmpl-aihub-free-nextgen` | Planner prompt, judge prompt, all 12 expert system prompts |
| Postgres `tmpl-m10-gremium-deep` | `legal_advisor` expert system prompt |

---

## [2.3.0] - 2026-04-19

### Added — System Cleanup Manager & Autonomous Disk Management

#### Admin UI — System Cleanup Manager (Maintenance page)

A new **System Cleanup Manager** section on the Maintenance page gives operators
full visibility and control over all automated disk-management jobs:

- **Job cards** per subsystem: Docker Prune, LangGraph Checkpoint Archive,
  Admin-Log Rotation, systemd Journal, Prometheus TSDB
- Each card displays: last run timestamp (relative), duration (current + rolling
  average), freed space (current + rolling average), run count, and
  job-specific detail metrics
- **Inline configuration panel** (collapsible) with TTL fields per job — changes
  are persisted immediately to `/opt/moe-infra/cleanup-config.json` and
  propagate to `.env` for environment-backed settings
- **Manual trigger** for `docker_prune` and `checkpoint_archive` via
  `POST /api/cleanup/run/{job}` — runs the cron script as a background task
- Auto-refresh every 60 seconds; flash notifications on save / trigger

#### New API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/cleanup/status` | All jobs: last run, averages, config |
| `GET` | `/api/cleanup/config` | Current cleanup configuration |
| `POST` | `/api/cleanup/config` | Persist configuration, propagate to `.env` |
| `POST` | `/api/cleanup/run/{job}` | Trigger job immediately |
| `GET` | `/api/cleanup/history` | Raw JSONL history, filterable by job |
| `POST` | `/api/cleanup/write-cron-env` | Update cron `.env` files from UI |

#### Autonomous Cleanup Infrastructure

- **`/etc/cron.daily/moe-docker-prune`** — removes dangling images, stopped
  containers, and entire build cache (`docker builder prune --all`); writes
  structured JSON to `/opt/moe-infra/cleanup-history.jsonl`
- **`/etc/cron.daily/moe-checkpoint-archive`** — daily `pg_dump --format=custom
  -Z9` of the LangGraph checkpoint database (265 MB → 32 MB), followed by a
  three-phase prune of `checkpoints`, `checkpoint_writes`, and orphaned
  `checkpoint_blobs`; reads TTL from `cleanup-config.json`
- **`/etc/logrotate.d/moe-admin-logs`** — daily logrotate for JSONL monitoring
  logs (`size 50M`, `rotate 3`, `compress`)
- **`_append_log()` rotation** in `app.py` — inline file-size check before every
  write; rotates at `LOG_MAX_BYTES` (default 50 MB) with `LOG_BACKUP_COUNT`
  backups

#### Docker Subsystem Hardening

- **Container log rotation** — `/etc/docker/daemon.json` now sets
  `"max-size": "50m", "max-file": "3"` as global defaults
- **Docker build cache** — `builder prune --all` clears 200+ GB of accumulated
  build layers from nightly rebuilds (first-run reclaim: 48 GB images +
  ~216 GB cache)
- **Valkey AOF compaction** — `auto-aof-rewrite-percentage 50`,
  `auto-aof-rewrite-min-size 32mb`, `maxmemory 400mb`,
  `maxmemory-policy allkeys-lru`
- **Neo4j TX-log retention** — `NEO4J_db_tx__log_rotation_retention__policy=2 files`
- **Prometheus retention** now configurable via `PROMETHEUS_RETENTION_DAYS`
  in `.env` (default: 30 d, previously hardcoded 90 d)
- **systemd journal** capped at 512 MB / 30 days via
  `/etc/systemd/journald.conf.d/size-limit.conf`

### Fixed

- **CSRF validation failure on Teams & Tenants page** — `teams_page` passed an
  empty template context `{}`, leaving `CSRF_TOKEN = ""` in `base.html`. The
  global `fetch()` monkey-patch therefore sent `X-CSRF-Token: ""`, rejected by
  `CsrfApiMiddleware`. Fixed by passing `get_csrf_token(request)` in the context.

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Admin UI, Infrastructure, Disk Management, Security |

---

## [2.2.0] - 2026-04-17

### Added — Responsive Design & Visual Refresh

#### Admin UI — Bootstrap 5 Offcanvas Navigation

The Admin UI navbar (17 navigation links) has been restructured using the
Bootstrap 5 Offcanvas pattern to prevent horizontal overflow on screens
narrower than 1400 px.

- **Desktop (≥1400px):** full horizontal link strip (`d-none d-xxl-flex`)
- **Tablet/Phone (<1400px):** hamburger icon (`d-xxl-none`) opens a slide-in
  drawer (`offcanvas offcanvas-end`) containing all 17 links as a vertical
  list with grouped section dividers
- Right-side controls (language selector, theme toggle, logout) remain always
  visible in a compact form

#### Public Websites — 3-Breakpoint CSS (moe-web, moe-web-int, moe-libris-web)

`custom.css` now implements a 3-tier responsive grid system:

| Breakpoint | Target | Key changes |
|---|---|---|
| ≤1024px (new) | Tablet | hw-grid, tools-grid, screenshots-grid → 2 columns |
| ≤768px (extended) | Phone | All grids → 1 column; tab-bar scrollable; routing-flow stacks |
| ≤480px (new) | Small Phone | Tab padding reduced; hero font clamp; cost-grid 1 column |

Notable fixes:
- `.screenshots-grid { minmax(420px) }` → breaks on 375px — corrected
- `.view-tabs` gains `overflow-x: auto` + `-webkit-overflow-scrolling: touch` for swipe on mobile
- `.routing-node { min-width: 160px }` → `unset` to prevent overflow on narrow phones

#### User Portal — Mobile Layout

`user_portal.html` sidebar layout switches to horizontal pill navigation on
screens narrower than 768px (`flex-direction: column`, `list-group` →
`flex-wrap: wrap`). Fixed-width sidebar containers converted to `max-width`
to prevent overflow.

#### Website Screenshots (6 new)

Added to `moe-web/assets/screenshots/` and `moe-web-int/assets/screenshots/`:

| File | Content |
|---|---|
| `moe-admin-overview.png` | Full-page Admin dashboard with Advanced Pipeline Settings expanded; all credentials privacy-blurred |
| `moe-admin-monitoring.png` | Full-page monitoring view with server tiles |
| `grafana-gpu-nodes.png` | GPU & Inference Nodes dashboard (kiosk mode) |
| `grafana-knowledge.png` | Knowledge Base Health dashboard |
| `dozzle.png` | Container log viewer |
| `neo4j-knowledge-graph.png` | Knowledge graph — 500+ entity subgraph |

### Changed — Gap Healer v2 (per-node Redis slots)

`scripts/gap_healer_templates.py` replaces the v1 systemd-timer approach:

- **Root cause fixed:** global `asyncio.Semaphore(4)` caused 300 hung tasks
  piling onto one warm node. Replaced with per-node `moe:healer:active:{node}`
  Redis counters and `ZPOPMAX` atomic gap claims.
- **Hardware caps:** M60→1, M10→3, RTX→4, GT→2 concurrent slots
- **Progressive unlock:** +1 slot per 5 successful runs up to hardware cap
- **Launcher:** started from Admin UI → Monitoring panel (no systemd required)

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Admin UI, Public Websites, Gap Healer, Documentation |
| `branch` | `feat/responsive-design-admin-websites` |

---

## [2.1.1] - 2026-04-15

### Fixed — Fresh-Install Reliability (Debian 13 LXC)

All bugs were discovered during a live install test on a clean Debian 13 (trixie)
LXC container. Each was fixed in `install.sh` and verified by redeploying on the
same container. Branch: `debug/lxc-install`.

- **`install.sh`**: Added `chown -R 1000:1000 kafka-data` — `moe-kafka` (confluentinc/cp-kafka)
  runs as `appuser` uid=1000; directories created by the installer as root caused an
  immediate crash loop on first start
- **`install.sh`**: Added `chown -R 65534:65534 prometheus-data` — `moe-prometheus` runs as
  uid=65534 (nobody); missing write permission caused a panic on `queries.active`
- **`install.sh`**: Added `chown -R 472:472 grafana/data grafana/dashboards` — `moe-grafana`
  runs as uid=472; `mkdir plugins` failed with permission denied
- **`install.sh`**: Added `chown -R 1001:0 agent-logs` — `langgraph-orchestrator` and
  `mcp-precision` run as `moe` uid=1001
- **`install.sh`**: Changed `.env` permissions from `chmod 600` to `chmod 644` — containers
  bind-mount `.env:ro` and run as uid=1001; `chmod 600` locked them out at startup with
  `PermissionError: [Errno 13]`. The `:ro` mount flag is the access control mechanism;
  world-readable on the host is acceptable.
- **`install.sh`**: Changed `EVAL_CACHE_FLAG_THRESHOLD=2.0` to `2` — `main.py:732` calls
  `int(os.getenv("EVAL_CACHE_FLAG_THRESHOLD", "2"))` which raises `ValueError` on float strings
- **`main.py`**: Added `GET /health` endpoint — the Dockerfile `HEALTHCHECK` called
  `http://127.0.0.1:8000/health` but no such route existed; all containers reported
  `(unhealthy)` despite functioning correctly

| Metadata | Value |
|---|---|
| `impact` | patch — fixes silent runtime failures on fresh installs |
| `breaking` | no |
| `domain` | Installer, Orchestrator |
| `branch` | `debug/lxc-install` |
| `tested-on` | Debian 13 (trixie) LXC, Docker CE, Proxmox |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `b1b945d3` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `requirements.txt`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`
- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | MCP Tools, Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 2 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`
- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | MCP Tools, Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 2 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`
- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | MCP Tools, Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 2 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `tool_eval.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `tool_eval.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `tool_eval.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `Dockerfile`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `requirements.txt`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `Dockerfile`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Docs Sync Agent**: `sync_agent.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Docs Sync Agent |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Docs Sync Agent**: `sync_agent.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Docs Sync Agent |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [2026-04-08] - Dozzle, Setup Wizard, install.sh

> `impact: minor` · `breaking: no` · `domain: infra, admin-ui, docs`

### Added

- **Dozzle log viewer** — new `moe-dozzle` container (amir20/dozzle:latest) accessible at `logs.moe-sovereign.org` via Caddy; bcrypt auth via `/opt/moe-infra/dozzle-users.yml`; added to CONTAINER_NAMES in Admin UI monitoring table
- **Setup Wizard** — first-run 4-step web wizard (`/setup`) in Admin UI; triggers automatically when `INFERENCE_SERVERS` is empty; covers inference servers, judge/planner models, and public URLs; implemented in all 4 UI languages
- **`install.sh`** — curl-able bash installer for Debian 11/12/13; auto-installs Docker CE, creates `/opt/` directories, generates secure secrets, prompts for admin credentials, deploys the full stack
- **`.env.example`** — comprehensive template with all 80+ variables documented and grouped by subsystem
- **`moe-sovereign.org` Caddy block** — serves `install.sh` via `file_server`; redirects all other traffic to docs
- **Installation guide** (`docs/guide/installation.md`) — requirements, one-line install, manual setup, directory layout, upgrade procedure
- **First-Time Setup guide** (`docs/guide/first-setup.md`) — wizard walkthrough, backend types, minimum viable config example

### Removed

- **"Expert Models — Global Default" card** — removed from Admin UI dashboard; model assignment is fully handled via Expert Templates and per-user API token auth; `rebuild_expert_models()` function deleted; `EXPERT_MODELS` no longer written by `save_config()`

### Changed

- Caddy config: added `moe-sovereign.org` and `logs.moe-sovereign.org` virtual hosts
- docker-socket-proxy: added `LOGS`, `EVENTS`, `VERSION`, `PING` endpoint permissions
- Quickstart docs: deployment section references `install.sh`

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [2026-04-08] — Memory Palace: Domain-Scoped Retrieval, Expert Memory Isolation & Auto-Save Hooks

### Added

- **Metadata-Filtered Semantic Search** (`main.py` — `planner_node`, `graph_rag_node`):
  The planner now optionally extracts `metadata_filters` (e.g. `{"expert_domain": "code_reviewer"}`)
  from the first task object. `graph_rag_node` applies these as a ChromaDB `where` clause
  after the Neo4j traversal, appending domain-scoped results to `graph_context` under a
  `[Domain-Filtered Memory]` label. Degrades silently on errors.

- **Isolated Expert Memory** (`main.py` — `merger_node`; `graph_rag/manager.py`):
  Every response stored in ChromaDB is tagged with `expert_domain` metadata.
  The `moe.ingest` Kafka payload gains a `source_expert` field that flows through the
  consumer into `extract_and_ingest()` and `ingest_synthesis()`, tagging all resulting
  `:Entity` nodes, relations, and `:Synthesis` nodes in Neo4j with their origin expert category.

- **`POST /v1/memory/ingest` endpoint** (`main.py`):
  New FastAPI endpoint that accepts `{session_summary, key_decisions, domain, source_model,
  confidence}` and publishes to `moe.ingest` for async GraphRAG processing. Enables
  external tools to persist knowledge without accessing Kafka directly.

- **Claude Code Auto-Save Hooks** (`hooks/`):
  Two bash scripts — `mempal_precompact_hook.sh` (PreCompact) and `mempal_save_hook.sh`
  (Stop) — POST session summaries to `/v1/memory/ingest` before context is lost.
  Configured via `~/.claude/settings.json`. See `hooks/README.md` for setup.

- **Documentation** (`docs/system/intelligence/memory_palace.md`):
  New chapter in the Intelligence & Learning section covering all three features with
  architecture diagrams, schema references, and operational runbook.

### Changed

- `AgentState` gains `metadata_filters: Dict` field
- `moe.ingest` Kafka payload gains `source_expert` field
- ChromaDB `moe_fact_cache` documents gain `expert_domain` metadata field
- Neo4j `:Entity` nodes and relations gain `expert_domain` property
- Neo4j `:Synthesis` nodes gain `expert_domain` property
- `docs/system/architecture.md`, `dataflow.md`, `pipeline.md`,
  `intelligence/index.md`, `intelligence/compounding_knowledge.md` updated

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | ChromaDB / Neo4j / Orchestrator / Claude Code Integration |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [2026-04-08] — Graph-basierte Wissensakkumulation

### Added

- **Synthesis Persistence** (`main.py`, `graph_rag/manager.py`): The `merger_node` now appends a `<SYNTHESIS_INSIGHT>` JSON block to its output when it produces a novel multi-source comparison, inference, or synthesis. The block is automatically stripped from the user-facing response. The Kafka consumer reads the `synthesis_insight` field on `moe.ingest` messages and calls the new `graph_manager.ingest_synthesis()` method, which creates a `:Synthesis` node in Neo4j (idempotent, sha256-keyed) and links it to relevant `:Entity` nodes via `:RELATED_TO`. These nodes surface in future `graph_rag_node` 2-hop traversals.

- **Graph Linting / Knowledge Janitor** (`main.py`, `graph_rag/manager.py`): New Kafka topic `moe.linting` (constant `KAFKA_TOPIC_LINTING`). Any message on this topic triggers `graph_manager.run_graph_linting()` as a background task. Phase 1 deletes orphaned `:Entity` nodes (no relationships, LIMIT 50). Phase 2 iterates contradictory relationship pairs from `_CONTRADICTORY_PAIRS`, detects same-subject conflicts, calls the Ingest LLM for resolution, and flags the losing relationship (`flagged=true`, `lint_note`, `lint_ts`, `lint_model`). All LLM calls are throttled via the existing `_ingest_semaphore` with 0.5 s sleep between iterations.

- **New documentation chapter** `docs/system/intelligence/compounding_knowledge.md`: Full description of both features including architecture diagrams, Cypher queries, operational runbook, and Neo4j schema changes.

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG, Kafka, Orchestrator, Documentation |
| `files changed` | 8 (`main.py`, `graph_rag/manager.py`, `docs/system/kafka.md`, `docs/system/architecture.md`, `docs/system/dataflow.md`, `docs/system/pipeline.md`, `docs/system/toolstack/graphrag_neo4j.md`, `docs/system/intelligence/compounding_knowledge.md`) |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `login.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [3.0.0] - 2026-04-07

### Added — Causal Learning Loop

- **GraphRAG / Neo4j** (`graph_rag/ontology.py`): Three new procedural relation types with enterprise seed triples
  - `NECESSITATES_PRESENCE` (Action → Location): performing an action requires physical presence at a location
  - `DEPENDS_ON_LOCATION` (Action → Location): action outcome depends on a reachable location
  - `ENABLES_ACTION` (Condition → Action): a prerequisite resource or state makes an action possible
  - 25 new entities across three new types: `Action`, `Location`, `Condition`
  - Seed triples cover IT operations workflows (on-premises deployment, hardware installation, remote access)

- **GraphRAG Manager** (`graph_rag/manager.py`): Procedural reasoning in extraction and retrieval
  - Extended extraction prompt to elicit factual and procedural triples in a single LLM call
  - `_query_procedural_requirements()`: targeted Cypher traversal when `Action`-type entities appear in results
  - `_get_ingest_semaphore()`: `asyncio.Semaphore(2)` caps concurrent background ingest calls
  - Auto-detection: extracted procedural triples override upstream `knowledge_type` hint
  - `extract_and_ingest()` extended with optional `knowledge_type` param; returns detected type

- **Orchestrator** (`main.py`): Graph ingest LLM decoupled from judge/merger
  - `GRAPH_INGEST_MODEL` / `GRAPH_INGEST_ENDPOINT`: dedicated model for background extraction; falls back to judge LLM when unset
  - `knowledge_type` field added to `moe.ingest` Kafka events
  - `graph_rag_node`: prepends explicit merger annotation when `[Procedural Requirements]` block is present

- **Admin UI** (`admin_ui/templates/dashboard.html`, `admin_ui/app.py`): Graph Ingest Model selector
  - New field in Models card following `model@endpoint` pattern
  - Persisted via `GRAPH_INGEST_MODEL` / `GRAPH_INGEST_ENDPOINT` env keys

- **Documentation** (`docs/system/intelligence/`): New *Intelligence & Learning* section
  - `causal_learning.md`: motivation, ontology, extraction pipeline, Cypher queries, enterprise use cases
  - `context_extension.md`: six mechanisms for effective context extension despite small LLM context windows
  - Updated `architecture.md` and `dataflow.md` with corrected diagrams and topic names

### Changed

- `mkdocs.yml`: new top-level nav section *Intelligence & Learning*
- `sync_agent.py`: all generated content translated to English
- `docs/changelog.md`: deduplicated 1051 auto-generated duplicate entries; all entries translated to English; metadata fields added to every release

| Metadata | Value |
|---|---|
| `impact` | major — new knowledge type in graph; existing triples unaffected (MERGE is idempotent) |
| `breaking` | no — `extract_and_ingest()` extended with optional param only |
| `domain` | GraphRAG, Kafka, Admin UI, Documentation |

---

## [2.3.0] - 2026-04-07

### Changed — Security Hardening & Admin UI Overhaul

- **Admin UI**: CSRF tokens on all state-changing forms; rate limiting on auth endpoints; session management improvements; all four locale files updated (de_DE, en_EN, fr_FR, zh_CN)
- **Infrastructure** (`docker-compose.yml`): Redis Stack configured via `REDIS_ARGS`; middleware ordering verified

| Metadata | Value |
|---|---|
| `impact` | minor — no API changes; session behaviour hardened |
| `breaking` | no |
| `domain` | Admin UI, Infrastructure, Security |

---

## [2.2.1] - 2026-04-06

### Changed — Admin UI Internationalisation & Claude Code Profile Fixes

- **Admin UI**: Full i18n pass across all 14 templates and four locale files; `Dockerfile` rebuilt
- **Orchestrator** (`main.py`): Claude Code profile `expert_template_id` resolution corrected; vision mode fixed for image attachments; process table display fixed
- **Documentation** (`mkdocs.yml`): navigation labels standardised to English

| Metadata | Value |
|---|---|
| `impact` | patch — UI and i18n fixes; API unchanged |
| `breaking` | no |
| `domain` | Admin UI, i18n, Claude Code Integration |

---

## [2.2.0] - 2026-04-05

### Added

- **Admin UI** (`profiles.html`, `app.py`): Expert-template selector in Claude Code profile editor — profiles can now assign an individual expert template (visible only in `moe_orchestrated` mode)
- **Orchestrator** (`main.py`): `expert_template_id` in CC profiles loaded via `admin_override=True` without permission check
- **Auto-documentation** (`sync_agent.py`, `scripts/doc_sync_hook.sh`): Claude Code PostToolUse hook writes changelog entries to `docs/changelog_pending.jsonl`; `sync_agent.py --changelog-only` flushes pending entries every 15 min
- **Expert template `claude-code-agent`**: system prompts for all expert categories optimised for autonomous tool execution in agentic loops

### Changed

- `_resolve_user_experts` / `_resolve_template_prompts`: new optional `admin_override: bool` parameter
- CC profiles: new optional field `expert_template_id`

| Metadata | Value |
|---|---|
| `impact` | minor — new optional field; existing profiles unaffected |
| `breaking` | no |
| `domain` | Admin UI, Claude Code Integration, Expert Templates |

---

## [2.1.0] - 2026-03-29

### Added

- **Two-Tier Expert System**: `_infer_tier()` routes T1 (≤20B params) first; T2 (>20B) only when T1 confidence is below threshold — saves VRAM on simple requests, escalates for complex ones
- **Chat-history injection**: `_truncate_history()` — last 4 turns, max 3000 chars — all expert LLMs receive conversation context; `chat_history` field added to `AgentState` and API layer
- **Citation tracking**: `_web_search_with_citations()` appends structured source references (title + URL) to web research results
- **`thinking_node`**: new node between `research_fallback` and `merger`; activated for complex plans or low-confidence results; 4-step chain-of-thought; output as `reasoning_trace`
- **Expert deduplication**: `_dedup_by_category()` keeps highest-confidence result per category; prevents echo chamber
- **`critic_node`**: post-merger validation for `medical_consult` and `legal_advisor` (safety-critical); judge LLM checks for factual errors and dangerous statements

### Changed

- Graph topology: `research_fallback → thinking → merger → critic → END`
- `expert_worker`: two-tier logic replaces simple parallel fan-out
- Judge LLM updated to reasoning-focused model

| Metadata | Value |
|---|---|
| `impact` | minor — pipeline extended; API compatible |
| `breaking` | no |
| `domain` | Orchestrator, Expert Routing, Safety |

---

## [2.0.0] - 2026-03-29

### Added

- **Kafka integration** (KRaft mode, no Zookeeper): topics `moe.ingest`, `moe.requests`, `moe.feedback`; `AIOKafkaProducer` with 12-retry backoff; `AIOKafkaConsumer` as permanent asyncio background task; graceful degradation when Kafka unavailable
- **GraphRAG / Neo4j Knowledge Graph**: `GraphRAGManager` with 2-hop traversal; base ontology 104 entities / 100 relations across 4 domains; background triple extraction via judge LLM; conflict detection; domain filter prevents cross-domain contamination; API endpoints `GET /graph/stats`, `GET /graph/search`
- **MCP Precision Tools Server** (port 8003): 16 deterministic tools — calculate (safe AST evaluator), date arithmetic, unit conversion, statistics, regex, subnet calculator, base64, JSON query
- **Self-learning infrastructure**: `POST /v1/feedback` endpoint; expert performance tracking in Redis (Laplace smoothing); cache flags low-rated entries; response metadata TTL 7 days
- **LangGraph pipeline extended**: `mcp_node`, `graph_rag_node` added to parallel fan-out; cache short-circuit at cosine distance < 0.15; expert output cap 2400 chars
- **Confidence scoring**: structured expert output `KONFIDENZ: hoch | mittel | niedrig`; merger prioritises by confidence
- **Research fallback node**: automatic web search on low-confidence results; forced for safety-critical categories
- **Output modes**: `moe-orchestrator`, `moe-orchestrator-code`, `moe-orchestrator-concise`
- **Token tracking**: accumulated `prompt_tokens` + `completion_tokens` across all LLM calls in every response
- **Open WebUI `<think>` panel**: progress reports from all nodes via `contextvars.ContextVar`
- **Open WebUI internal-request detection**: fast-paths title/autocomplete prompts directly to judge

### Fixed

- Planner returning strings instead of task dicts → `_sanitize_plan()` added
- Neo4j deprecated relation type warnings removed from `_CONTRADICTORY_PAIRS`
- docker-compose duplicate `environment` block merged

| Metadata | Value |
|---|---|
| `impact` | major — Kafka and Neo4j are new required infrastructure |
| `breaking` | yes — requires `moe-kafka` and `neo4j-knowledge` services |
| `domain` | Orchestrator, Infrastructure, GraphRAG, MCP, Kafka |

---

## [1.2.0] - 2026-02-12

### Added

- SymPy mathematics module: equation solving, simplification, differentiation, integration

### Changed

- Expert worker: improved stagger algorithm
- GPU count dynamically configurable via environment variable
- Improved CUDA OOM error detection and handling

### Fixed

- Checkpointer initialisation error (`_GeneratorContextManager`)
- Memory leak in expert worker semaphore management

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator, Math |

---

## [1.1.0] - 2026-02-11

### Added

- LangGraph Multi-Model Orchestration (initial pipeline)
- Multi-node GPU cluster support via Ollama
- Redis checkpoint persistence for graph state
- ChromaDB vector store for knowledge caching
- SearXNG web research integration
- Docker Compose deployment
- OpenAI-compatible API endpoint (`/v1/chat/completions`) with SSE streaming

| Metadata | Value |
|---|---|
| `impact` | minor — initial functional release |
| `breaking` | no |
| `domain` | Orchestrator, Infrastructure |

---

## [1.0.0] - 2026-02-10

### Added

- Project initialisation with LangGraph base structure
- Core nodes: `cache_lookup`, `planner`, `expert_workers`, `merger`
- Docker configuration and basic error handling

| Metadata | Value |
|---|---|
| `impact` | major — initial release |
| `breaking` | n/a |
| `domain` | Orchestrator |
