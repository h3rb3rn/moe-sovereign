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
