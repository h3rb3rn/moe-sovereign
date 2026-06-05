# Expert Template Guide

Expert Templates define **how** MoE Sovereign routes, processes, and synthesises
requests. Every setting in a template has a concrete effect on latency, quality,
and cost. This guide explains each field and illustrates it with three reference
templates shipped with the system.

---

## Template Fields Reference

### Top-Level Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `judge_model` | `"model@endpoint"` | global judge | LLM that synthesises all expert outputs into the final response. Also used as the tool-calling model in the two-phase Kanban handler. |
| `planner_model` | `"model@endpoint"` | global planner | LLM that decomposes the user request into 1–4 expert subtasks. Should be fast and instruction-following (not a thinking model). |
| `judge_prompt` | string | built-in | Overrides the default synthesis instruction. Use to enforce output format (e.g. `ANSWER: <value>` for benchmarks). |
| `planner_prompt` | string | built-in | Overrides the routing ruleset. Add domain-specific routing rules and MCP tool hints here. |
| `enable_cache` | bool | `true` | When `true`, semantically similar past responses are retrieved from the L1 vector cache before starting the pipeline. Disable for benchmarks to prevent cross-question contamination. |
| `enable_graphrag` | bool | `true` | Injects relevant facts from the Neo4j knowledge graph into expert context. Disable when the graph adds noise (e.g. short factual GAIA questions). |
| `enable_web_research` | bool | `true` | Triggers a SearXNG web search when the query complexity score exceeds the routing threshold. |
| `max_agentic_rounds` | int | unlimited | Hard cap on pipeline re-planning cycles. `3` prevents unbounded 49-minute loops while allowing enough rounds for multi-step research. |
| `force_think` | bool | `false` | Forces the extended thinking mode for ALL experts, regardless of their individual `thinking_mode`. |
| `history_max_turns` | int | `0` (global) | Maximum conversation turns injected into expert context. `0` = use global default. |
| `graphrag_max_chars` | int | `0` (global) | Maximum characters from GraphRAG context injection. |

---

### The `experts` Object

Each key in `experts` defines a **domain specialist**. The planner routes
subtasks to these categories by name.

```json
"experts": {
  "category_name": {
    "context_window": 262144,
    "system_prompt": "...",
    "thinking_mode": true,
    "models": [
      {"model": "qwen3.6:35b", "endpoint": "N04-RTX", "role": "primary"},
      {"model": "deepseek-r1:32b", "endpoint": "N04-RTX", "role": "fallback"}
    ]
  }
}
```

#### Expert Sub-Fields

| Field | Type | Description |
|---|---|---|
| `context_window` | int | **Actual model context window in tokens.** The pipeline uses this to truncate input so the model never receives more tokens than it can process. Setting this incorrectly (too high) causes silent truncation by the model itself; too low wastes available context. |
| `system_prompt` | string | The expert's role instruction. For **creation** tasks (games, services) use "generate complete, runnable code". For **review** tasks use "identify OWASP issues". |
| `thinking_mode` | bool | When `false`, the pipeline prepends `/no_think` to the user message — this disables qwen3's Extended Thinking, reducing latency by 10–20×. Set `false` for factual lookup categories (`general`, `data_analysis`), `true` for categories that benefit from reasoning (`reasoning`, `science`, `security_analysis`). |
| `models[].role` | `"always"` / `"primary"` / `"fallback"` | `always` = only this model is used. `primary` / `fallback` = Two-Tier escalation: T1 (primary) runs first; if confidence is low, T2 (fallback) runs. |

#### Standard Expert Categories

| Category | Routing Use Case | Thinking |
|---|---|---|
| `vision` | Image, chart, diagram, chess position analysis | off |
| `math` | Calculations, formulas (precision_tools has priority for exact arithmetic) | on |
| `code_reviewer` | Code creation AND review, full implementations, OWASP analysis | off |
| `reasoning` | Logic puzzles, formal deduction, probability trees | **on** |
| `science` | Physics, chemistry, biology, earth science | **on** |
| `data_analysis` | Statistics, pandas/SQL, data wrangling | off |
| `creative_writing` | Poems, stories, constrained text generation | off |
| `devops_sre` | Kubernetes, Docker, CI/CD, incident diagnosis | off |
| `security_analysis` | CVEs, threat modelling, zero-trust hardening | **on** |
| `translation` | Cross-lingual tasks, cultural nuance | off |
| `legal_advisor` | Statutory interpretation, case law | **on** |
| `medical_consult` | Evidence-based medical knowledge | **on** |
| `long_context` | Documents/codebases >32K tokens, full conversation histories | off |
| `general` | Factual lookups, summaries, everything else | off |

---

## Reference Template 1: `moe-n04rtx-specialist`

All experts run exclusively on **N04-RTX** local hardware.
Maximum quality through specialised local models. No external API dependencies.

```json
{
  "judge_model": "qwen3.6:35b@N04-RTX",
  "planner_model": "phi4:14b-fp16@N04-RTX",
  "enable_cache": true,
  "enable_graphrag": true,
  "enable_web_research": true,
  "experts": {
    "vision": {
      "context_window": 128000,
      "system_prompt": "Visual analysis expert. Analyze the image carefully and answer precisely.",
      "thinking_mode": false,
      "models": [{"model": "qwen2.5vl:32b", "endpoint": "N04-RTX", "role": "always"}]
    },
    "math": {
      "context_window": 32768,
      "system_prompt": "Mathematics expert. Use MCP calculate for numeric results. Show rigorous steps.",
      "thinking_mode": true,
      "models": [
        {"model": "mathstral:7b",    "endpoint": "N04-RTX", "role": "primary"},
        {"model": "deepseek-r1:32b", "endpoint": "N04-RTX", "role": "fallback"}
      ]
    },
    "code_reviewer": {
      "context_window": 32768,
      "system_prompt": "Senior full-stack engineer. For creation: complete, runnable code — no placeholders. For review: OWASP Top 10, performance, style.",
      "thinking_mode": false,
      "models": [{"model": "qwen2.5-coder:32b", "endpoint": "N04-RTX", "role": "always"}]
    },
    "reasoning": {
      "context_window": 262144,
      "system_prompt": "Analytical reasoning expert. Formal logic, identify hidden assumptions.",
      "thinking_mode": true,
      "models": [
        {"model": "qwen3.6:35b",     "endpoint": "N04-RTX", "role": "primary"},
        {"model": "deepseek-r1:32b", "endpoint": "N04-RTX", "role": "fallback"}
      ]
    },
    "science": {
      "context_window": 262144,
      "system_prompt": "Natural scientist. Precise terminology, correct units, formulas, scientific consensus.",
      "thinking_mode": true,
      "models": [{"model": "qwen3.6:35b", "endpoint": "N04-RTX", "role": "always"}]
    },
    "devops_sre": {
      "context_window": 393216,
      "system_prompt": "DevOps/SRE expert. Production-ready configs, Kubernetes, Docker, incident diagnosis.",
      "thinking_mode": false,
      "models": [
        {"model": "devstral-small-2:24b", "endpoint": "N04-RTX", "role": "primary"},
        {"model": "qwen3-coder:30b",      "endpoint": "N04-RTX", "role": "fallback"}
      ]
    },
    "security_analysis": {
      "context_window": 131072,
      "system_prompt": "Cybersecurity expert. CVEs, threat modelling, zero-trust hardening.",
      "thinking_mode": true,
      "models": [{"model": "deepseek-r1:32b", "endpoint": "N04-RTX", "role": "always"}]
    },
    "translation": {
      "context_window": 131072,
      "system_prompt": "Professional translator. Preserve meaning, tone, cultural nuance.",
      "thinking_mode": false,
      "models": [
        {"model": "translategemma:27b", "endpoint": "N04-RTX", "role": "primary"},
        {"model": "mistral-small:24b",  "endpoint": "N04-RTX", "role": "fallback"}
      ]
    },
    "long_context": {
      "context_window": 1048576,
      "system_prompt": "Long-context expert with 1M token window. Process full codebases, long documents, histories >32K tokens. Extract key facts, structured summaries.",
      "thinking_mode": false,
      "models": [
        {"model": "mistral-nemo:12b",    "endpoint": "N04-RTX", "role": "primary"},
        {"model": "llama3-gradient:8b",  "endpoint": "N04-RTX", "role": "fallback"}
      ]
    },
    "general": {
      "context_window": 262144,
      "system_prompt": "Knowledgeable assistant. Factual, accurate, concise.",
      "thinking_mode": false,
      "models": [{"model": "qwen3.6:35b", "endpoint": "N04-RTX", "role": "always"}]
    }
  }
}
```

**Design decisions:**

- `devops_sre` uses `devstral-small-2:24b` (Mistral's DevOps-specialised model) with 393K context — ideal for processing full Dockerfiles, Helm charts, and IaC configs in a single pass.
- `reasoning` and `science` have `thinking_mode: true` because extended Chain-of-Thought is essential for correctness in logic and STEM domains.
- `creative_writing` uses `solar-pro:22b` which only has **4K native context** — the template must reflect this (`context_window: 4096`) to prevent the pipeline from sending overlong prompts.
- `long_context` uses `mistral-nemo:12b` (1M context) for tasks where the full codebase or document history must be read without chunking.

---

## Reference Template 2: `moe-quality-optimal`

Production-grade template. Best-per-category model from the Constellation
Benchmark (N04-RTX). GraphRAG and web research enabled for maximum accuracy.

```json
{
  "judge_model": "qwen3.6:35b@N04-RTX",
  "planner_model": "phi4:14b-fp16@N04-RTX",
  "enable_cache": true,
  "enable_graphrag": true,
  "enable_web_research": true,
  "experts": {
    "general":    {"context_window": 262144, "thinking_mode": false,
                   "models": [{"model": "qwen3.6:35b", "endpoint": "N04-RTX"}]},
    "math":       {"context_window": 32768,  "thinking_mode": true,
                   "models": [{"model": "mathstral:7b", "endpoint": "N04-RTX"}]},
    "code_reviewer": {"context_window": 32768, "thinking_mode": false,
                   "models": [{"model": "qwen2.5-coder:32b", "endpoint": "N04-RTX"}]},
    "reasoning":  {"context_window": 262144, "thinking_mode": true,
                   "models": [{"model": "qwen3.6:35b", "endpoint": "N04-RTX"}]},
    "science":    {"context_window": 262144, "thinking_mode": true,
                   "models": [{"model": "qwen3.6:35b", "endpoint": "N04-RTX"}]},
    "data_analysis": {"context_window": 262144, "thinking_mode": false,
                   "models": [{"model": "qwen3.6:35b", "endpoint": "N04-RTX"}]},
    "creative_writing": {"context_window": 4096, "thinking_mode": false,
                   "models": [{"model": "solar-pro:22b", "endpoint": "N04-RTX"}]},
    "devops_sre": {"context_window": 393216, "thinking_mode": false,
                   "models": [{"model": "devstral-small-2:24b", "endpoint": "N04-RTX"},
                               {"model": "qwen3-coder:30b",      "endpoint": "N04-RTX", "role": "fallback"}]},
    "security_analysis": {"context_window": 131072, "thinking_mode": true,
                   "models": [{"model": "deepseek-r1:32b", "endpoint": "N04-RTX"}]},
    "long_context": {"context_window": 1048576, "thinking_mode": false,
                   "models": [{"model": "mistral-nemo:12b",   "endpoint": "N04-RTX"},
                               {"model": "llama3-gradient:8b", "endpoint": "N04-RTX", "role": "fallback"}]}
  }
}
```

---

## Reference Template 3: `moe-openrouter-free`

All experts route through the **openrouterai** user connection using
free-tier models. No local GPU required. 13 specialised categories.

```json
{
  "judge_model":   "nvidia/nemotron-3-ultra-550b-a55b:free@openrouterai",
  "planner_model": "meta-llama/llama-3.3-70b-instruct:free@openrouterai",
  "enable_cache": true,
  "enable_graphrag": false,
  "enable_web_research": true,
  "experts": {
    "vision":        {"models": [{"model": "nvidia/nemotron-nano-12b-v2-vl:free", "endpoint": "openrouterai"}]},
    "math":          {"models": [{"model": "nvidia/nemotron-3-ultra-550b-a55b:free", "endpoint": "openrouterai"},
                                  {"model": "openai/gpt-oss-120b:free",              "endpoint": "openrouterai", "role": "fallback"}]},
    "code_reviewer": {"models": [{"model": "poolside/laguna-m.1:free",  "endpoint": "openrouterai"},
                                  {"model": "qwen/qwen3-coder:free",     "endpoint": "openrouterai", "role": "fallback"}]},
    "reasoning":     {"models": [{"model": "nvidia/nemotron-3-ultra-550b-a55b:free", "endpoint": "openrouterai"},
                                  {"model": "nousresearch/hermes-3-llama-3.1-405b:free", "endpoint": "openrouterai", "role": "fallback"}]},
    "science":       {"models": [{"model": "nvidia/nemotron-3-super-120b-a12b:free", "endpoint": "openrouterai"}]},
    "data_analysis": {"models": [{"model": "qwen/qwen3-next-80b-a3b-instruct:free", "endpoint": "openrouterai"}]},
    "creative_writing": {"models": [{"model": "z-ai/glm-4.5-air:free",              "endpoint": "openrouterai"}]},
    "devops_sre":    {"models": [{"model": "poolside/laguna-xs.2:free",  "endpoint": "openrouterai"}]},
    "security_analysis": {"models": [{"model": "nvidia/nemotron-3.5-content-safety:free", "endpoint": "openrouterai"}]},
    "translation":   {"context_window": 131072,
                      "models": [{"model": "moonshotai/kimi-k2.6:free",  "endpoint": "openrouterai"}]},
    "legal_advisor": {"models": [{"model": "openai/gpt-oss-120b:free",   "endpoint": "openrouterai"}]},
    "medical_consult": {"models": [{"model": "openai/gpt-oss-120b:free", "endpoint": "openrouterai"}]},
    "long_context":  {"context_window": 131072,
                      "models": [{"model": "moonshotai/kimi-k2.6:free",  "endpoint": "openrouterai"}]},
    "general":       {"models": [{"model": "google/gemma-4-31b-it:free",  "endpoint": "openrouterai"}]}
  }
}
```

**Design decisions:**

- `enable_graphrag: false` — GraphRAG is only useful if the local Neo4j knowledge graph is populated with domain facts. For a pure API setup without local infra, it adds latency without benefit.
- Judge is `nemotron-3-ultra-550B` — the largest available free model (550B parameters). Synthesis quality scales with judge size.
- Planner is `llama-3.3-70b` — fast and reliable at JSON instruction following, appropriate for the simple decomposition task.
- `translation` uses `kimi-k2.6` with 131K context — Moonshot AI's model excels at multilingual tasks and has the largest free-tier context window.
- **Rate limiting**: Free models route through shared upstream providers (Venice, etc.). A 429 response triggers automatic `retry_after` backoff (29s by default) without marking the endpoint as degraded.

---

## Claude Code Profile: `openrouterai-deep`

CC Profiles extend Expert Templates for Claude Code CLI use. They add
a dedicated **Tooling LLM** for function-calling alongside the MoE pipeline.

```json
{
  "tool_model":         "moonshotai/kimi-k2.6:free",
  "tool_endpoint":      "openrouterai",
  "moe_mode":           "moe_orchestrated",
  "expert_template_id": "<id-of-moe-openrouter-free>",
  "tool_max_tokens":    8192,
  "reasoning_max_tokens": 32768,
  "tool_choice":        "required",
  "stream_think":       false,
  "system_prompt_prefix": "You are a principal-level software engineer. Deliver production-grade solutions with security analysis, test coverage strategy, and architectural rationale."
}
```

| Field | Description |
|---|---|
| `tool_model` | The LLM that handles Claude Code's function calls (read_file, bash, write_file). Must support OpenAI function-calling format reliably. `kimi-k2.6` is chosen for its 131K context and strong agentic capabilities. |
| `moe_mode` | `moe_orchestrated` = tool calls go directly to `tool_model`; content generation uses the MoE expert pipeline. `native` = bypass MoE entirely (Claude Code controls its own model). |
| `expert_template_id` | Links to the Expert Template that handles content requests. The Tooling LLM handles structure; the MoE pipeline handles knowledge. |
| `tool_max_tokens` | Max tokens for a single tool response. 8192 is sufficient for most file reads and bash outputs. |
| `tool_choice` | `required` forces Claude Code to always call a tool rather than generating freeform text, which prevents hallucinated file paths. |

---

## Key Design Principles

### 1. Context Window Accuracy

The `context_window` field must match the **actual model context window**:

```
qwen3.6:35b      → 262,144   (256K — MoE architecture, not 32K!)
devstral-small-2 → 393,216   (384K)
deepseek-r1:32b  → 131,072   (128K)
solar-pro:22b    →   4,096   (4K — CRITICAL: must not exceed this!)
mistral-nemo:12b → 1,024,000 (1M)
```

The pipeline uses `context_window` to truncate task input. An incorrect value
either wastes context (too low) or causes silent mid-sentence truncation by
the model itself (too high).

### 2. Thinking Mode Granularity

`thinking_mode: false` prepends `/no_think` to the user message, suppressing
qwen3's Extended Thinking. This reduces generation by **10–20× tokens** for
categories that do not need deep reasoning:

```
Off  → general, data_analysis, devops_sre, translation, creative_writing
On   → reasoning, science, security_analysis, legal_advisor, medical_consult
```

Never disable thinking for security or reasoning experts — correctness suffers.

### 3. Two-Tier Expert Escalation

`role: "primary"` / `role: "fallback"` implements T1/T2 escalation. The
primary runs first; if its confidence score is `low`, the fallback runs and
the judge merges both answers:

```json
"math": {
  "models": [
    {"model": "mathstral:7b",    "role": "primary"},   // T1: fast, specialised
    {"model": "deepseek-r1:32b", "role": "fallback"}   // T2: larger, more thorough
  ]
}
```

Use T1 for speed, T2 for quality. The cost of T2 is only paid when needed.

### 4. The Long-Context Expert

For tasks exceeding 32K tokens (full codebases, long documents, 1000-turn
conversation histories), route to `long_context`:

```json
"long_context": {
  "context_window": 1048576,        // 1M tokens
  "models": [
    {"model": "mistral-nemo:12b"},  // 1,024,000 tokens
    {"model": "llama3-gradient:8b"} // 1,048,576 tokens
  ]
}
```

These models process the **entire** input without chunking or summarisation —
important for code review of large repositories where global dependencies matter.
