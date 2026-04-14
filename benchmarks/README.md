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
