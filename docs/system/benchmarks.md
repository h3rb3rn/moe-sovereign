# MoE-Eval Benchmark Suite

The MoE-Eval benchmark suite (`benchmarks/`) evaluates the orchestrator as a
**Compound AI System** — not raw token throughput. It tests cognitive accuracy,
expert routing, deterministic tool usage, and compounding knowledge (GraphRAG).

## Test categories

| Category | Tests | What it measures |
|---|---|---|
| **Precision / MCP** | 3 | Deterministic calculations via MCP tools (subnet, math, dates) — things LLMs hallucinate |
| **Compounding Memory** | 2 | Multi-turn knowledge accumulation via GraphRAG SYNTHESIS_INSIGHT loop |
| **Domain Routing** | 3 | Planner correctly routes to legal/medical/code expert domains |
| **Multi-Expert Synthesis** | 1 | Parallel expert fan-out + merger quality for cross-domain questions |

## Quick start

```bash
# Set your API key
export MOE_API_KEY="moe-sk-..."

# Run all 9 tests with the balanced template
python benchmarks/runner.py

# Run with a specific template
MOE_TEMPLATE=moe-reference-8b-fast python benchmarks/runner.py

# Evaluate results (deterministic checks + LLM-as-a-Judge)
python benchmarks/evaluator.py
```

## Scoring methodology

Each test case receives:

1. **Deterministic score** (0-10): keyword matching, numeric tolerance, or exact match
2. **LLM judge score** (0-10): the orchestrator itself rates the answer quality
3. **Combined score**: `0.4 × deterministic + 0.6 × LLM judge`

## Example: MCP precision test

The subnet calculation test sends `172.20.128.0/19` and expects:
- Subnet mask: `255.255.224.0`
- Broadcast: `172.20.159.255`
- Usable hosts: `8190`

The MCP `subnet_calc` tool solves this deterministically. A standard LLM
would likely hallucinate incorrect values — the benchmark measures whether
the orchestrator correctly delegates to MCP.

## Example: Compounding memory test

A 3-turn session:
1. **Inject**: "Project Sovereign Shield uses the X7 protocol"
2. **Inject**: "X7 protocol uses TCP port 9977 with TLS 1.3"
3. **Query**: "What port do I need for Project Sovereign Shield?"

The system must synthesise both facts (which are novel and fictional —
they cannot come from pretraining) and answer: "Port 9977 with TLS 1.3".

For details, see `benchmarks/README.md` in the repository.

## LLM Role Suitability Study

Systematic evaluation of local LLMs for MoE orchestration roles. Each model
was tested in two roles:

- **Planner**: Can the model decompose a user query into structured subtasks
  with valid JSON output?
- **Judge**: Can the model evaluate and merge expert outputs, assign a quality
  score, and produce a final synthesis?

Tests run on a 5-node heterogeneous GPU cluster (RTX 3060, GT 1060, Tesla M60,
Tesla M10). Timeout: 300s. Quantization: Q4_K_M where applicable.

### Results

| Model | Params | Planner | Judge | Both | Planner Latency | Judge Latency | Notes |
|-------|--------|---------|-------|------|-----------------|---------------|-------|
| `olmo2:13b` | 13B | Fail | Pass | Fail | 41.6s | 1.7s | Judge-only viable |
| `phi3:14b` | 14B | Pass | Pass | Pass | 45.5s | 6.8s | Solid all-rounder |
| `phi3:medium` | 14B | Pass | Pass | Pass | 51.2s | 6.9s | |
| `phi4:14b` | 14B | Pass | Pass | Pass | 36.1s | 56.3s | **Best all-rounder** |
| `qwen2.5-coder:7b` | 7B | Pass | Pass | Pass | 27.5s | 4.2s | Fast, T1-capable |
| `qwen2.5-coder:32b` | 32B | Pass | Pass | Pass | 60.2s | 92.3s | |
| `qwen2.5vl:7b` | 7B | Fail | Fail | Fail | 300.1s | 300.0s | Timeout |
| `qwen2.5vl:32b` | 32B | Fail | Fail | Fail | 81.0s | 72.3s | Vision model, no text routing |
| `qwen3:32b` | 32B | Pass | Pass | Pass | 83.0s | 34.1s | |
| `qwen3-coder:30b` | 30B | Pass | Pass | Pass | 128.9s | 20.0s | |
| `qwen3-vl:8b` | 8B | Fail | Pass | Fail | 300.1s | 229.4s | Timeout on planner |
| `qwen3.5:27b` | 27B | Fail | Fail | Fail | 300.1s | 300.0s | Thinking tags break JSON |
| `qwen3.5:35b` | 35B | Fail | Fail | Fail | 300.1s | 225.3s | Thinking tags break JSON |
| `qwq:32b` | 32B | Fail | Fail | Fail | 300.1s | 300.1s | Timeout, excessive reasoning |
| `samantha-mistral:7b` | 7B | Pass | Fail | Fail | 25.7s | 6.8s | Planner-only |
| `solar-pro:22b` | 22B | Pass | Pass | Pass | 104.0s | 2.7s | Very fast judge |
| `sroecker/sauerkrautlm-7b-hero` | 7B | Pass | Pass | Pass | 169.2s | 31.6s | German-tuned |
| `starcoder2:15b` | 15B | Fail | Fail | Fail | 92.3s | 50.8s | No instruction following |
| `translategemma:27b` | 27B | Pass | Pass | Pass | 213.9s | 62.2s | |
| `vanta-research/atom-astronomy-7b` | 7B | Fail | Fail | Fail | 18.9s | 4.3s | Domain-specific, no routing |
| `vanta-research/atom-olmo3-7b` | 7B | Pass | Pass | Pass | 33.8s | 1.0s | Fast judge |
| `x/z-image-turbo` | — | Fail | Fail | Fail | 0.1s | 0.2s | Image-only model |

### Summary

| Category | Count | Share |
|----------|-------|-------|
| Both Planner + Judge suitable | 11 | 50% |
| Planner only | 1 | 5% |
| Judge only | 2 | 9% |
| Not suitable | 8 | 36% |

### Key Findings

1. **phi4:14b** is the best all-rounder: fast, reliable JSON output, strong
   judge quality. Used as default Planner and Judge in production templates.
2. **qwen2.5-coder:7b** offers the best speed/quality ratio for T1 (fast)
   templates at only 27.5s planner latency.
3. **Thinking-mode models** (qwen3.5, qwq) systematically fail because their
   `<think>...</think>` tags corrupt the expected JSON output format.
4. **Vision models** (qwen2.5vl, qwen3-vl) are unsuitable for text routing
   but can serve as vision experts within a template.
5. **Domain-specific models** (starcoder2, atom-astronomy) lack instruction
   following for structured orchestration tasks.

### Dataset

Full results are published on HuggingFace:
[h3rb3rn/moe-sovereign-benchmarks](https://huggingface.co/datasets/h3rb3rn/moe-sovereign-benchmarks)

---

## Hardware Tier Implications

The LLM suitability study ran on a 5-node heterogeneous cluster spanning Legacy and
Consumer GPU tiers. The latency data reflects real inference throughput on that mixed
hardware — not theoretical peak performance.

### Tier to Model Mapping

| Hardware tier | VRAM | Max viable model | Roles available | Latency range |
|---|---|---|---|---|
| **Legacy** (GT 1060, Tesla M10) | 6–8 GB | 7B Q4 | T1 experts (fast path) | 20–170s |
| **Legacy** (Tesla M60) | 16 GB | 14B Q4 | T1 + limited T2 | 36–104s |
| **Consumer** (RTX 3060–4090) | 12–24 GB | 7–14B Q4 | T1 + T2 planner | 27–60s |
| **Semi-Pro** (A5000, RTX 6000 Ada) | 24–48 GB | 32B Q4 | Full T2 stack | 60–130s |
| **Enterprise** (A100, H100) | 40–80 GB | 70B FP16 | All roles, parallel | 10–40s |

### Latency vs. Quality Trade-off

**Critical insight:** Hardware tier affects latency only — not answer quality.
The same `phi4:14b` Q4_K_M model produces identical output on a Tesla M10 and on an
RTX 4090. The RTX is faster. The answer is the same.

Quality is determined by:
1. **Model capability** (weights, size, training quality) — hardware-independent
2. **Knowledge graph density** (accumulated triples in Neo4j) — improves with usage
3. **Cache hit rate** (semantic similarity in ChromaDB) — improves with usage

This means **Legacy hardware clusters remain valid long-term** — they produce the
same quality as enterprise hardware at lower throughput. The quality-vs-time graph
converges regardless of hardware tier; only the speed of convergence differs.

### Concurrent Expert Capacity

MoE Sovereign runs multiple expert workers in parallel for each request. The number
of simultaneous experts is bounded by available VRAM:

| Tier | Simultaneous T1 experts | Simultaneous T2 experts | Notes |
|---|---|---|---|
| Legacy (6–8 GB/node) | 1 per node | 0 | Single-model GPU; pool across nodes |
| Consumer (24 GB) | 3–4 | 1–2 | Can run judge + planner simultaneously |
| Semi-Pro (48 GB) | 6–8 | 2–4 | Full T2 fan-out without queuing |
| Enterprise (80 GB) | 10+ | 4–8 | Parallel execution of all 16 expert roles possible |

**Practical cluster strategy:** Mix tiers. Route T1 tasks (deterministic, fast) to
Legacy nodes; route T2 tasks (planner, judge, merger) to Consumer/Semi-Pro nodes.
The existing 5-node benchmark cluster uses exactly this pattern.

See [Intelligence Growth Prognosis](intelligence/growth_prognosis.md) for projected
quality curves at each hardware tier over time.

---

## April 2026 — Dense-Graph Benchmark Campaign

This benchmark campaign was conducted on **2026-04-15** after extensive system
operation had grown the Neo4j knowledge graph to a substantial density. The
purpose: measure whether accumulated graph knowledge meaningfully improves
Compounding Memory test scores compared to the earlier sparse-graph run.

### Knowledge Graph State at Run Time

| Metric | Value |
|---|---|
| Entity nodes | 4,962 |
| Synthesis nodes | 391 |
| **Total nodes** | **5,353** |
| Edges (relationships) | 5,909 |
| Avg. edges per entity | ~1.19 |

This represents significant domain knowledge accumulated across legal, medical,
technical, and scientific domains through production use.

### New Per-Node Benchmark Templates

Four new templates were created alongside the existing reference template to
maximise cluster utilisation — each template pins experts to a distinct hardware
tier, so all nodes inference simultaneously during a parallel run.

| Template | Planner | Judge | Expert Assignment | Hardware |
|---|---|---|---|---|
| `moe-reference-30b-balanced` | phi4:14b@N04-RTX | gpt-oss:20b@N04-RTX | Mix N04-RTX | RTX cluster (60 GB) |
| `moe-benchmark-n04-rtx` | phi4:14b@N04-RTX | qwen3-coder:30b@N04-RTX | All on N04-RTX | RTX cluster (60 GB) |
| `moe-benchmark-n07-n09` | phi4:14b@N07-GT | gpt-oss:20b@N09-M60 | Split N07-GT / N09-M60 | GT1060 + Tesla M60 |
| `moe-benchmark-n06-m10` | phi4:14b@N06-M10-01 | phi4:14b@N06-M10-02 | Spread N06-M10-01…04 | Tesla M10 × 4 (32 GB) |
| `moe-benchmark-n11-m10` | phi4:14b@N11-M10-01 | phi4:14b@N11-M10-02 | Spread N11-M10-01…04 | Tesla M10 × 4 (32 GB) |

All templates have `enable_graphrag: true` and `enable_cache: false` to ensure
each test receives fresh GraphRAG context rather than a cached response.

### Parallel Run Architecture

Tests were submitted concurrently: `MOE_PARALLEL_TESTS=3` allows up to 3
single-turn tests per runner in parallel. With 5 template runners launched
simultaneously this generates up to **15 concurrent API requests**, keeping all
GPU nodes loaded throughout the run.

The runner script: `benchmarks/run_all_parallel.sh`

### Results

<!-- Results injected after benchmark completion -->
<!-- Generated by: python3 benchmarks/evaluator.py for each run -->

#### Score Summary

| Template | Precision | Compounding | Routing | Multi-Expert | **Average** |
|---|---|---|---|---|---|
| `ref-30b` | 9.6 | 4.5 | 8.4 | 5.7 | **7.6** |
| `n04-rtx` | 7.6 | 4.5 | 5.9 | 4.9 | **6.0** |
| `n07-n09` | 6.0 | 0.0 | 7.8 | 0.0 | **4.6** |
| `n06-m10` | 0.0 | 0.0 | 0.0 | 0.0 | **0.0** |
| `n11-m10` | 0.0 | 0.0 | 0.0 | 0.0 | **0.0** |

#### Per-Test Detail

| Test ID | Category | ref-30b | n04-rtx | n07-n09 | n06-m10 | n11-m10 |
|---|---|---|---|---|---|---|
| precision-mcp-subnet | precision | 8.8 | 8.8 | 8.8 | 0.0 | 0.0 |
| precision-mcp-math | precision | 10.0 | 8.8 | 7.4 | 0.0 | 0.0 |
| precision-mcp-date | precision | 10.0 | 5.2 | 1.8 | 0.0 | 0.0 |
| compounding-memory-3turn | compounding | 9.0 | 9.0 | 0.0 | 0.0 | 0.0 |
| compounding-memory-5turn | compounding | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| routing-legal | routing | 8.2 | 8.2 | 7.6 | 0.0 | 0.0 |
| routing-medical | routing | 8.6 | 0.6 | 7.2 | 0.0 | 0.0 |
| routing-code-review | routing | 8.4 | 8.9 | 8.7 | 0.0 | 0.0 |
| multi-expert-synthesis | multi_expert | 5.7 | 4.9 | 0.0 | 0.0 | 0.0 |

### Full Measurement Series (ref-30b template)

| Date | Graph nodes | Precision | Compounding | Routing | Multi-Expert | **Avg** |
|---|---|---|---|---|---|---|
| Apr 10 run 1 | ~500 | 7.6 | 4.1 | 5.0 | 0.9 | **5.2** |
| Apr 10 runs 2–4 | ~800 | 9.3 | 3.9 | 5.8 | 0.9 | **6.0** |
| Apr 12 | ~2,000 | 8.3 | 4.4 | 7.6 | 5.1 | **6.8** |
| Apr 15 | 5,353 | 9.6 | 4.5 | 8.4 | 5.7 | **7.6** |

### Why Did the Score Change? Four Factors

1. **Graph density (+2.4 pts, primary driver)** — Routing improved +3.4 pts, multi-expert synthesis +4.8 pts as GraphRAG context grows richer with more domain triples.
2. **M10 hardware split (structural break)** — M10 nodes were split from 4×8 GB combined blocks into separate 8 GB Ollama instances. Old 30b/70b M10 templates no longer function; M10 benchmark templates timed out entirely under parallel load.
3. **Evaluation methodology correction** — Earlier runs lacked deterministic scoring (det=0); from Apr 15 onward keyword-match and numeric-tolerance scores are computed. Explains routing-legal jump 4.8→8.2.
4. **Concurrency effect** — n04-rtx scored 6.0 (vs. 7.6 for ref-30b) running simultaneously with 4 other templates (15 concurrent requests); isolated run would score higher.

### Comparison: Before and After Graph Growth

| Metric | April 12 run | April 15 run | Delta |
|---|---|---|---|
| Graph nodes at run time | ~2,000 (est.) | 5,353 | +3,353 |
| Graph edges at run time | ~2,200 (est.) | 5,909 | +3,709 |
| compounding-memory-3turn | 8.2 | 9.0 | +0.8 |
| compounding-memory-5turn | 0.6 | 0.0 (timeout) | -0.6 |
| Average score (ref-30b) | 6.8 | 7.6 | +0.8 |

---

## April 2026 — M10-Gremium Evaluation: Can Graph Density Compensate for Small LLMs?

**Test date:** 2026-04-15. Research question: Does a dense knowledge graph (5,353 nodes) compensate
for using only 7–9B models distributed across 8 Tesla M10 nodes (8 GB VRAM each)?

### Template: `moe-m10-8b-gremium`

| Component | Model | Node |
|---|---|---|
| Planner | phi4:14b | N07-GT (2× GT 1060, 12 GB total) |
| Judge | phi4:14b | N07-GT |
| code_reviewer | qwen2.5-coder:7b | N06-M10-01 |
| math | mathstral:7b | N06-M10-02 |
| medical_consult | meditron:7b | N06-M10-03 |
| legal_advisor | sauerkrautlm-7b-hero | N06-M10-04 |
| reasoning | qwen3:8b | N11-M10-01 |
| science | gemma2:9b | N11-M10-02 |
| translation | glm4:9b | N11-M10-03 |
| data_analyst | qwen2.5:7b | N11-M10-04 |

### Multi-Domain Challenge Prompt

A single-turn prompt (1,893 chars) spanning four domains requiring cross-expert synthesis:
legal/compliance (DSGVO, EU AI Act), medical statistics (sensitivity/specificity, sample size),
technical infrastructure (10 TB/day, 5-year archive with compression), and ML fundamentals
(bias-variance, regularization, DICOM augmentation).

Deterministic scoring checks (7 items, total weight 10.5):
`10 TB/day` (2.0), `2.74 PB archive` (2.0), `Art. 9 DSGVO` (1.5),
`EU AI Act high risk` (1.5), `AUROC/MCC metric` (1.5), `bias-variance` (1.0), `regularization` (1.0).

### Results

| Template | det_score | Elapsed | Tokens in | Tokens out | Experts invoked | Planner retries |
|---|---|---|---|---|---|---|
| `moe-reference-30b-balanced` | **6.67 / 10** | 528s | 15,875 | 14,615 | Multiple (N04-RTX + N09-M60) | 0 |
| `moe-m10-8b-gremium` | **4.29 / 10** | 2,542s | 31,926 | 8,172 | 1 (legal_advisor only) | 2 failures |

#### Deterministic Hit/Miss Detail

| Check | ref-30b | m10-gremium |
|---|---|---|
| daily volume = 10 TB | ✓ | ✓ |
| 5y archive ≈ 2.74 PB | ✗ (computed ~14.5 PB) | ✗ |
| Art. 9 DSGVO | ✗ (regex miss — cited as "Art. 9 § 2") | ✗ (cited as "GDPR Article 9") |
| EU AI Act high risk | ✓ | ✓ |
| AUROC / MCC | ✓ | ✗ |
| bias-variance tradeoff | ✓ | ✓ |
| regularization technique | ✓ | ✗ |

### Root-Cause Analysis

**Critical failure: GraphRAG context overflow on N07-GT**

With 5,353 graph nodes the GraphRAG retrieval injects ~5,000 tokens of triples into the
planner prompt. phi4:14b on N07-GT has a context window of 8,192 tokens. The resulting
prompt (system instruction + graph context + user query) saturates the window, causing
phi4:14b to answer the question in prose rather than return the required JSON routing plan.

| Planner attempt | Duration | Outcome |
|---|---|---|
| 1 | ~11 min | Prose answer — "Planner parse error (attempt 1)" |
| 2 | ~8 min | Prose answer — "Planner could not parse JSON — fallback" |
| 3 | ~9 min | **Valid JSON** (partial — only `legal_advisor` routed) |

After 3 attempts and 28 minutes, only the `legal_advisor` expert was dispatched.
The sauerkrautlm-7b-hero model responded in critique/evaluation mode rather than providing
direct answers, further degrading coverage.

**Total overhead:** 2,542s vs 528s for ref-30b — a **4.8× penalty** from context overflow alone.

### Key Findings

1. **Graph density hurts small-context planners.** At 5,353 nodes the GraphRAG injection
   volume exceeds phi4:14b's effective instruction-following capacity on an 8,192-token window.
   The planner model needs a context window of ≥ 16,384 tokens, or GraphRAG retrieval must be
   capped (e.g. top-k = 10 triples instead of exhaustive retrieval) when the planner is on
   legacy hardware.

2. **M10 experts are viable in isolation** — sauerkrautlm-7b-hero returned a coherent legal
   analysis within its domain. The weakness was routing (only 1 of 8 experts invoked) and
   response style (critique mode).

3. **The knowledge graph does NOT compensate for context overflow.** Graph density improves
   answer quality only when the planner can parse and route correctly. A failed planner
   negates all expert and graph benefits.

4. **Mitigation:** Either (a) pin the planner to a node with a larger context window
   (≥ 16 k tokens, e.g. N04-RTX with qwen2.5-coder:7b or phi4:14b at extended context),
   or (b) hard-cap GraphRAG retrieval depth for templates with legacy-hardware planners.
