# Best Practices: LLM Selection & Template Design

This guide is derived from empirical testing of 69 LLMs across 5 inference nodes,
covering Planner suitability, Judge suitability, and Expert role performance.

## LLM Selection for Pipeline Roles

### Planner (Task Decomposition)

The Planner must output **strictly valid JSON** — no prose, no markdown fences,
no thinking blocks. This eliminates a surprising number of models.

| Tier | Recommended Models | Latency | Notes |
|------|-------------------|---------|-------|
| **Best** | `phi4:14b` | 27-36s | Fastest reliable Planner. Consistent JSON output. |
| **Best** | `hermes3:8b` | 16s | Ultra-fast, good for simple decompositions |
| **Good** | `gpt-oss:20b` | 38s | Reliable, widely available |
| **Good** | `devstral-small-2:24b` | 45s | Strong on code-related planning |
| **Good** | `nemotron-cascade-2:30b` | ~200s | Excellent quality but slow |
| **Avoid** | `qwen3.5:35b` | FAIL | Thinking mode produces `<think>` blocks, not JSON |
| **Avoid** | `deepseek-r1:32b` | P-only | Chain-of-thought interferes with JSON output |
| **Avoid** | `starcoder2:15b` | FAIL | Code completion model, no instruction following |

**Key insight:** Models with "thinking" or "reasoning" modes (qwen3.5, deepseek-r1)
tend to wrap their output in `<think>` tags, breaking JSON parsing. Disable thinking
mode in the Planner prompt or use non-reasoning models.

### Judge / Merger (Response Synthesis & Scoring)

The Judge must synthesize multiple expert responses AND produce structured output
(scores, provenance tags). It needs strong instruction following.

| Tier | Recommended Models | Latency | Notes |
|------|-------------------|---------|-------|
| **Best** | `phi4:14b` | 1.7-4.2s | Extremely fast Judge responses |
| **Best** | `qwen3-coder:30b` | 1.7s | Fast, code-aware synthesis |
| **Good** | `Qwen3-Coder-Next` (80B) | 2.6s | Highest quality but large |
| **Good** | `devstral-small-2:24b` | 2.5s | Good for code-focused synthesis |
| **Good** | `glm-4.7-flash` | 15s | Strong general synthesis |
| **Avoid** | `gpt-oss:20b` in pipeline | — | Works in isolation but gets unloaded by Ollama TTL between expert calls |
| **Avoid** | `qwen3.5:35b` | FAIL | Same thinking-mode issue as Planner |

**Critical finding:** `gpt-oss:20b` passes isolated Judge tests (4.7s, valid JSON)
but fails in the MoE pipeline because Ollama unloads it between expert inference
calls. The solution: use sticky sessions or a dedicated Judge node.

### Expert Models

Experts are more forgiving — they produce free-text responses, not structured JSON.
Almost any instruction-following model works as an Expert.

| Domain | Recommended | Why |
|--------|------------|-----|
| Code Review | `devstral-small-2:24b` | SWE-bench 68%, code-focused |
| Code Generation | `qwen3-coder:30b` | 370 languages, strong tool calling |
| Reasoning | `deepseek-r1:32b` | Best chain-of-thought on consumer GPUs |
| Security Analysis | `devstral-small-2:24b` | CWEval-aware, OWASP coverage |
| Research | `gemma4:31b` | Strong general knowledge |
| Math | `phi4:14b` + MCP tools | MCP handles calculation, LLM extracts params |
| Legal | `gpt-oss:20b` | German law knowledge, Gesetze-im-Internet tools |

## Template Composition

### T1/T2 Tier Strategy

- **T1 (Primary, ≤20B):** Fast screening. Models that respond in <30s.
  Use `phi4:14b`, `hermes3:8b`, `gpt-oss:20b`.
- **T2 (Fallback, >20B):** Deep analysis. Engaged only when T1 reports
  `CONFIDENCE: low`. Use `devstral-small-2:24b`, `qwen3-coder:30b`,
  `deepseek-r1:32b`.

### Node Assignment

- **Pinned (`model@node`):** For production templates. Guarantees VRAM availability.
- **Floating (`model` only):** For elastic/low-priority workloads. System finds
  the best available node automatically.

Rule: Pin the Planner and Judge to fast nodes (RTX). Float T2 experts.

### Service Toggles

Each template can disable pipeline components:

| Toggle | Default | When to Disable |
|--------|---------|-----------------|
| `enable_cache` | true | Testing, debugging (need fresh responses) |
| `enable_graphrag` | true | Privacy-sensitive queries (no knowledge persistence) |
| `enable_web_research` | true | Air-gapped environments, speed-critical tasks |

### Compliance Badge

Templates are automatically classified:

- **Local Only** (green): All models on local infrastructure
- **Mixed** (yellow): Some models on external APIs
- **External** (red): Primarily external APIs

The CISO sees at a glance whether data leaves the network.

## System Prompt Engineering

### Planner Prompts

**DO:**
- Demand JSON-only output explicitly
- List valid categories
- Provide format examples
- Include `PRECISION_TOOLS` block for MCP routing

**DON'T:**
- Allow free-text explanations
- Use thinking/reasoning instructions
- Request markdown formatting

### Judge Prompts

**DO:**
- Instruct to preserve code blocks verbatim
- Require provenance tags `[REF:entity]`
- Demand verification steps
- Cite which expert provided each insight

**DON'T:**
- Allow summarization of code
- Skip security findings

### Expert Prompts

**DO:**
- Define the expert's domain boundary clearly
- Require structured output (CONFIDENCE, GAPS, REFERRAL)
- Include domain-specific methodology (OWASP for security, etc.)
- End with language enforcement

**DON'T:**
- Mix domains (security expert should NOT comment on style)
- Allow the expert to refuse ("I cannot help with that")

## CC Profile Best Practices

| Profile Type | Tool Model | Thinking | Max Tokens | Use Case |
|-------------|-----------|----------|------------|----------|
| Fast | `gemma4:31b` | off | 4,096 | Quick edits, simple questions |
| Balanced | `Qwen3-Coder-Next` | on | 8,192 / 16K reasoning | Daily development |
| Deep | `Qwen3-Coder-Next` | on | 8,192 / 32K reasoning | Architecture, security audits |

**Key:** The `tool_choice: required` setting forces the model to always use tools
when available. This is critical for Claude Code integration — without it, the model
may generate prose instead of executing file edits.
