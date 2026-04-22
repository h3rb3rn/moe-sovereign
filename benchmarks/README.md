# MoE-Eval Benchmark Suite

An evaluation framework for the MoE Sovereign orchestrator, designed to test
**cognitive accuracy, expert routing, deterministic tool usage, and compounding
knowledge** — not raw token throughput.

Inspired by [GAIA](https://arxiv.org/abs/2311.12983) (agentic reasoning) and
[LongMemEval](https://arxiv.org/abs/2410.10813) (long-term memory), adapted
for the MoE architecture's specific USPs.

## Quick Start

```bash
# Install dependencies (if not already available)
pip install httpx pydantic

# Set your API key (create via Admin UI → Users → API Keys)
export MOE_API_KEY="moe-sk-..."

# Run the benchmark with the balanced template
python benchmarks/runner.py

# Run with a specific template
MOE_TEMPLATE=moe-reference-8b-fast python benchmarks/runner.py

# Evaluate the results (LLM-as-a-Judge + deterministic checks)
python benchmarks/evaluator.py
```

## Architecture

```
benchmarks/
├── README.md              # this file
├── runner.py              # async benchmark runner (httpx + asyncio)
├── evaluator.py           # LLM-as-a-Judge + deterministic scoring
├── datasets/
│   └── moe_eval_v1.json   # 9 test cases across 4 categories
└── results/
    ├── run_<template>_<ts>.json   # raw run results
    └── eval_<ts>.json             # evaluated results with scores
```

## Test Categories

### 1. Precision / MCP Tests (3 tests)
Tests that standard LLMs hallucinate but our AST-whitelisted MCP tools solve
exactly:
- **Subnet calculation** (`172.20.128.0/19`) — tests `subnet_calc` MCP tool
- **Multi-step arithmetic with units** (server power consumption) — tests
  `calculate` + `unit_convert`
- **Date arithmetic across leap year** — tests `date_diff`

Expected: MCP tools fire deterministically, final answer contains the exact
correct values (keyword match or numeric tolerance).

### 2. Compounding Memory Tests (2 tests, multi-turn)
Tests the GraphRAG SYNTHESIS_INSIGHT loop — whether novel facts injected in
early turns can be recalled and synthesised in later turns:
- **3-turn**: inject protocol facts → ask synthesis question
- **5-turn**: inject cross-domain facts (legal + technical) → ask summary

Expected: The final answer references facts from ALL prior turns, including
novel fictional entities that cannot come from pre-training (e.g. "Project
Sovereign Shield uses the X7 protocol on port 9977").

### 3. Domain Routing Tests (3 tests)
Tests whether the planner correctly routes to the right expert domain:
- **Legal**: BGB §823 vs §280 — must route to `legal_advisor`
- **Medical**: Hashimoto vs Basedow — must route to `medical_consult` (+ disclaimer)
- **Code Review**: SQL injection — must route to `code_reviewer`

Expected: Domain-appropriate expert vocabulary, correct paragraph/concept
references, medical disclaimer present.

### 4. Multi-Expert Synthesis (1 test)
A question requiring simultaneous activation of legal + technical + math
experts. Tests parallel fan-out and merger quality.

## Scoring

Each test case receives three scores:

1. **Deterministic score** (0-10): keyword matching, numeric tolerance, exact
   match. Objective, reproducible.
2. **LLM judge score** (0-10): the MoE orchestrator itself (using a judge
   template) rates the answer quality on a rubric.
3. **Combined score**: `0.4 × deterministic + 0.6 × LLM judge`

The 60/40 weighting favours the LLM judge because many valid answers may use
different phrasing than the expected keywords.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MOE_API_BASE` | `http://localhost:8002` | Orchestrator base URL |
| `MOE_API_KEY` | *(required)* | API key (from Admin UI) |
| `MOE_TEMPLATE` | `moe-reference-30b-balanced` | Template for the benchmark run |
| `MOE_JUDGE_TEMPLATE` | `moe-reference-30b-balanced` | Template for the LLM judge |
| `MOE_EVAL_DATASET` | `datasets/moe_eval_v1.json` | Dataset path |
| `MOE_EVAL_RESULTS` | *(auto-detect latest)* | Results JSON for evaluator |

## Interpreting Results

| Score Range | Meaning |
|---|---|
| 9.0 - 10.0 | Excellent: all aspects correct, precise, well-structured |
| 7.0 - 8.9  | Good: core aspects correct, minor issues |
| 5.0 - 6.9  | Acceptable: basics right, notable gaps |
| 3.0 - 4.9  | Poor: major errors or missing content |
| 0.0 - 2.9  | Failed: wrong, misleading, or empty |

A healthy MoE Sovereign deployment should achieve **≥7.0 average** on the
precision tests (MCP tools are deterministic) and **≥5.0** on the compounding
memory tests (which depend on GraphRAG ingest timing and model quality).

## Adding Test Cases

To add a new test case, edit `datasets/moe_eval_v1.json` and add an entry to
the `test_cases` array. See the existing entries for the schema. Key fields:

```json
{
  "id": "unique-kebab-case-id",
  "category": "precision | compounding_knowledge | domain_routing | multi_expert",
  "name": "Human-readable test name",
  "type": "single_turn | multi_turn",
  "prompt": "The question (for single_turn)",
  "turns": [...],  // for multi_turn
  "expected_answer": { "verification_keywords": [...] },
  "scoring": { "type": "keyword_match | numeric_tolerance | exact_match | combined", ... }
}
```

## Dependencies

- Python 3.11+
- `httpx` (async HTTP client)
- Access to a running MoE Sovereign orchestrator with a valid API key
- For multi-turn tests: a template with GraphRAG/Neo4j enabled

## Shell Scripts & Automation

### `run_overnight.sh` — Multi-Epoch Stability Benchmark

Runs the overnight dataset N times (epochs) with full resilience features:
API health-wait, model warm-up, per-epoch retry, and Ollama self-healing via SSH.

```bash
# Requires benchmarks/.env — copy from benchmarks/.env.example
cp benchmarks/.env.example benchmarks/.env
# Edit: MOE_API_KEY, node SSH addresses, GPU counts

bash benchmarks/run_overnight.sh             # default epochs
MOE_EPOCHS=5 bash benchmarks/run_overnight.sh   # override epoch count
```

Results land in `benchmarks/results/overnight_<timestamp>/`.

---

### `run_all_sequential.sh` — All Templates, One After Another

Runs all configured benchmark templates sequentially to avoid GPU contention
on N04-RTX (which hosts Planner/Judge). AIHUB (remote) runs first.

```bash
bash benchmarks/run_all_sequential.sh
```

---

### `run_all_parallel.sh` — All Templates Simultaneously

Pins each template to a different GPU node and fires all runs in parallel.
Calls `evaluator.py` for every result file when done.

```bash
MOE_API_KEY=moe-sk-... bash benchmarks/run_all_parallel.sh
```

---

### `watchdog_restart.sh` — Crash Recovery Monitor

Monitors a **running** benchmark and restarts it only on crash (lock-file
gone + process dead). Does **not** auto-start benchmarks — that is intentional
to prevent unattended resource consumption.

```bash
# Typically invoked via /loop in Claude Code:
bash benchmarks/watchdog_restart.sh
```

Log: `benchmarks/results/overnight_watchdog.log`

---

### `inject_results_into_docs.py` — Documentation Updater

Reads the latest `eval_*.json` result file, computes per-category scores,
and injects a formatted table into `docs/system/benchmarks.md` at the
`<!-- Results injected after benchmark completion -->` marker.

```bash
python3 benchmarks/inject_results_into_docs.py              # auto-detect latest run
python3 benchmarks/inject_results_into_docs.py --run-dir benchmarks/results/overnight_20260419-225041
```

## Results Directory Structure

```
benchmarks/results/
├── overnight_<timestamp>/     # run_overnight.sh output
│   ├── summary.txt            # epoch scores + aggregate stats
│   ├── epoch_<N>_prompt_<id>.txt    # raw prompt sent
│   ├── epoch_<N>_response_<id>.txt  # raw model response
│   └── epoch_<N>_error_<id>.txt     # error detail (if failed)
├── all_run_<timestamp>/       # run_all_sequential.sh output
├── context_run_<timestamp>/   # context-focused runs
└── overnight_watchdog.log     # watchdog restart log
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `MOE_API_KEY missing` at start | `.env` not created | `cp benchmarks/.env.example benchmarks/.env` and fill in values |
| All tests timeout (300s) | Model not loaded / Ollama not running | `curl http://localhost:8002/health` — check orchestrator logs |
| `compounding-memory-5turn` fails | GraphRAG not enabled on template | Enable Neo4j + use a template with `graph_rag: true` |
| Evaluator score unexpectedly 0 | Judge template not returning JSON | Check `MOE_JUDGE_TEMPLATE` — needs a model that reliably outputs JSON |
| Watchdog loops without restarting | Lock file exists but process is hung | `rm benchmarks/.bench_running` then recheck process list |
