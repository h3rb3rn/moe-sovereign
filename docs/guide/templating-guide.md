# Expert Templates & Profiles: Complete Guide

This is the definitive guide to MoE Sovereign's template and profile system.
It covers the three processing modes, expert template design, Claude Code
profiles, interactive chat integration, and every tuning lever available.

---

## 1. Processing Modes: Native vs Reasoning vs Orchestrated

Every request to MoE Sovereign is processed in one of three modes. The mode
is selected by the CC Profile (for Claude Code) or by the expert template
chosen as the model (for OpenAI-compatible agents).

### Native Mode (`moe_mode: native`)

A single LLM handles the request directly. No planner, no experts, no merger.

- The request is forwarded to one inference endpoint
- The LLM processes tool calls (file edits, bash) natively
- No pipeline overhead

**When to use:** Quick edits, simple bug fixes, interactive coding sessions,
autocomplete, trivial questions.

**When to avoid:** Multi-domain synthesis, research tasks, anything requiring
domain isolation or knowledge accumulation.

### MoE Reasoning (`moe_mode: moe_reasoning`)

A single LLM with the Thinking Node enabled. The model performs chain-of-thought
reasoning before generating its response.

- Streaming `<think>` blocks show the reasoning process in real time
- The thinking trace is visible in Open WebUI's thinking panel and in
  Claude Code's output
- No parallel expert routing — one model does all the work, but with
  structured deliberation

**When to use:** Architecture decisions, complex debugging, code review
where you want to see the reasoning process.

**When to avoid:** Simple edits (overkill), deep research (not enough
domain coverage).

### MoE Orchestrated (`moe_mode: moe_orchestrated`)

The full MoE pipeline activates:

1. **Planner LLM** decomposes the request into 1-4 subtasks
2. **Expert LLMs** process subtasks in parallel (domain-isolated)
3. **MCP Precision Tools** handle deterministic calculations (math, regex, subnet, etc.)
4. **GraphRAG** (Neo4j) injects accumulated knowledge from prior requests
5. **SearXNG Web Research** fetches current information when enabled
6. **Merger/Judge LLM** synthesizes all results into a single response
7. **Critic Node** fact-checks safety-critical domains (medical, legal)

**When to use:** Security audits, research tasks, multi-domain synthesis,
knowledge-enriched analysis, any task where quality matters more than speed.

**When to avoid:** Interactive coding where sub-second feedback is expected.

### Comparison Table

| Aspect | Native | Reasoning | Orchestrated |
|--------|--------|-----------|--------------|
| Latency | 5-30s | 30-120s | 2-10 min |
| Token Cost | 1x | 1.5x | 3-5x |
| Output Quality | Good | Better | Best |
| Knowledge Accumulation | No | No | Yes (GraphRAG) |
| Domain Routing | No | No | Yes (15 categories) |
| MCP Precision Tools | No | No | Yes (23 tools) |
| Accumulation Effect | No | No | Yes (9.3x speedup over time) |
| Thinking Blocks | No | Yes | Optional |
| Parallel Expert Execution | No | No | Yes |
| Critic / Fact-Check | No | No | Yes (medical, legal) |

!!! tip "The accumulation effect"
    In orchestrated mode, GraphRAG stores synthesis results as
    `SYNTHESIS_INSIGHT` relations. Future requests in the same domain
    benefit from this accumulated knowledge. Over 5 epochs, benchmarks show
    a **9.3x latency reduction** while quality improves by 56%.
    See [Claude Code Profiles](claude-code-profiles.md#5-epoch-benchmark-results)
    for the full benchmark data.

---

## 2. Expert Template Design

An expert template defines the complete configuration for an orchestrated
MoE pipeline run: which LLMs to use, how to decompose tasks, which tools
to enable, and how to synthesize results.

### Template Structure

```json
{
  "id": "tmpl-xxxxx",
  "name": "template-name",
  "description": "Human-readable description of the template's purpose",
  "planner_prompt": "MoE orchestrator. Decompose the request into 1-4 subtasks...",
  "planner_model": "phi4:14b@N07-GT",
  "judge_prompt": "Synthesize all inputs into one complete response...",
  "judge_model": "phi4:14b@N04-RTX",
  "experts": {
    "code_reviewer": {
      "system_prompt": "Senior SWE: correctness, security (OWASP Top 10)...",
      "models": [
        {"model": "gpt-oss:20b", "endpoint": "N09-M60", "role": "primary"},
        {"model": "devstral-small-2:24b", "endpoint": "N04-RTX", "role": "fallback"}
      ]
    },
    "technical_support": {
      "system_prompt": "Senior IT/DevOps. Immediately executable solutions...",
      "models": [
        {"model": "phi4:14b", "endpoint": "N07-GT", "role": "primary"},
        {"model": "qwen3:32b", "endpoint": "N04-RTX", "role": "fallback"}
      ]
    }
  },
  "enable_cache": true,
  "enable_graphrag": true,
  "enable_web_research": true,
  "cost_factor": 1.0
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Auto-generated unique template ID (`tmpl-xxxxxxxx`) |
| `name` | string | Human-readable name, appears in `/v1/models` |
| `description` | string | Shown in Admin UI and model listings |
| `planner_prompt` | string | System prompt for the Planner LLM |
| `planner_model` | string | Model and optional node for planning (`model@node` or `model`) |
| `judge_prompt` | string | System prompt for the Judge/Merger LLM |
| `judge_model` | string | Model and optional node for synthesis |
| `experts` | object | Map of category name to expert configuration |
| `experts.*.system_prompt` | string | Domain-specific system prompt for the expert |
| `experts.*.models` | array | Ordered list of model assignments (primary, fallback) |
| `enable_cache` | bool | Enable L1/L2 response and plan caching |
| `enable_graphrag` | bool | Enable Neo4j knowledge graph enrichment |
| `enable_web_research` | bool | Enable SearXNG web search for freshness |
| `cost_factor` | float | Multiplier for token budget accounting |

### T1/T2 Tier Strategy

Every expert in a template can have two model tiers:

- **T1 (Primary):** Fast models at or below the tier boundary (default: 20B
  parameters). These respond in under 30 seconds and handle the majority of
  requests.
- **T2 (Fallback):** Larger, more capable models above 20B. Engaged only when
  the T1 model reports `CONFIDENCE: low` (below 0.65 threshold).

The tier boundary is configurable via `EXPERT_TIER_BOUNDARY_B` (in billions
of parameters). The default of 20B means:

- Models like `phi4:14b`, `hermes3:8b`, `gpt-oss:20b` are T1
- Models like `devstral-small-2:24b`, `qwen3-coder:30b`, `deepseek-r1:32b` are T2

**Design principle:** T1 handles ~80% of requests fast. T2 activates only
when depth is needed, keeping average latency low while preserving quality
for hard problems.

### Pinned vs Floating Assignment

Models can be assigned in two ways:

**Pinned (`model@node`):**
```json
{"model": "phi4:14b", "endpoint": "N07-GT", "role": "primary"}
```
The model always runs on the specified GPU node. This guarantees VRAM
availability and eliminates cold-start latency (the model stays loaded).
Use pinned assignments for production templates where predictable performance
matters.

**Floating (`model` only):**
```json
{"model": "devstral-small-2:24b", "role": "fallback"}
```
The system automatically selects the best available node with sufficient
VRAM. This provides elasticity — the model can run on whichever node is
least loaded. Use floating assignments for T2 fallback models and
low-priority workloads.

**Rule of thumb:** Pin the Planner and Judge to fast nodes (RTX-class GPUs).
Float T2 expert models for elasticity.

### LLM Selection Guide

Based on empirical testing of 69 LLMs across 5 inference nodes:

#### Best Planner Models

| Model | Latency | Why |
|-------|---------|-----|
| `phi4:14b` | 27-37s | Most reliable JSON output. Consistent, fast. |
| `hermes3:8b` | 16s | Ultra-fast for simple decompositions |
| `gpt-oss:20b` | 38s | Reliable, widely available |
| `devstral-small-2:24b` | 45s | Strong on code-related planning |

#### Best Judge/Merger Models

| Model | Latency | Why |
|-------|---------|-----|
| `phi4:14b` | 1.7-4.2s | Extremely fast synthesis |
| `qwen3-coder:30b` | 1.7s | Fast, code-aware, strong instruction following |
| `Qwen3-Coder-Next` (80B) | 2.6s | Highest quality but needs large VRAM |
| `devstral-small-2:24b` | 2.5s | Good for code-focused synthesis |

#### Models to Avoid in Pipeline Roles

| Model | Issue |
|-------|-------|
| `qwen3.5:35b` | Thinking mode produces `<think>` blocks instead of JSON — breaks planner and judge parsing |
| `deepseek-r1:32b` | Chain-of-thought interferes with JSON output — usable as expert only |
| `starcoder2:15b` | Code completion model, no instruction following capability |
| `gpt-oss:20b` (as judge) | Passes isolated tests but gets unloaded by Ollama TTL between expert calls in the pipeline |

!!! warning "Thinking models break JSON"
    Models with "thinking" or "reasoning" modes (`qwen3.5`, `deepseek-r1`)
    tend to wrap output in `<think>` tags, breaking the JSON parsing that
    Planner and Judge roles require. Either use non-reasoning models for
    these roles or explicitly disable thinking in the system prompt.

### System Prompt Best Practices

#### Planner Prompts

**Do:**

- Demand JSON-only output explicitly
- List all valid categories in the prompt
- Provide a format example with the exact JSON schema
- Include the `PRECISION_TOOLS` block for MCP routing

**Do not:**

- Allow free-text explanations alongside JSON
- Use thinking/reasoning instructions
- Request markdown formatting

#### Judge/Merger Prompts

**Do:**

- Instruct to preserve code blocks verbatim
- Require provenance tags (`[REF:entity]`) citing which expert provided each insight
- Demand verification steps for numeric values
- Define explicit priority: MCP > Graph > high-confidence experts > Web > medium > low/cache

**Do not:**

- Allow summarization of code blocks
- Allow skipping security findings

#### Expert Prompts

**Do:**

- Define the expert's domain boundary clearly
- Require structured output markers: `CONFIDENCE`, `GAPS`, `REFERRAL`
- Include domain-specific methodology (OWASP for security, S3/AWMF for medical, etc.)
- End with language enforcement

**Do not:**

- Mix domains (a security expert should not comment on code style)
- Allow the expert to refuse with "I cannot help with that"

### Service Toggles

Each template can enable or disable pipeline components independently:

| Toggle | Default | Effect When Disabled |
|--------|---------|---------------------|
| `enable_cache` | `true` | Every request runs the full pipeline — no cache hits. Use for testing or when fresh responses are required. |
| `enable_graphrag` | `true` | No knowledge graph enrichment. Prior synthesis results are not injected. Use for privacy-sensitive queries where no knowledge persistence is desired. |
| `enable_web_research` | `true` | No SearXNG web search. Responses rely entirely on model knowledge and GraphRAG. Use in air-gapped environments or for speed-critical tasks. |

### Compliance Badges

Templates are automatically classified based on where their models run:

| Badge | Meaning |
|-------|---------|
| **Local Only** (green) | All models run on local infrastructure. No data leaves the network. |
| **Mixed** (yellow) | Some models use external APIs. Data may leave the network for those calls. |
| **External** (red) | Primarily external API models. Most data leaves the network. |

The compliance badge is visible in the Admin UI and helps CISOs assess
data residency at a glance.

---

## 3. Claude Code Profiles

Claude Code profiles (CC Profiles) control how MoE Sovereign handles
requests from Claude Code CLI, the VS Code extension, and other Anthropic
API clients. Each profile maps to a specific processing mode.

### Profile Fields

| Field | Description | Example |
|-------|-------------|---------|
| `name` | Display name shown in clients | `cc-ref-native` |
| `moe_mode` | Processing mode | `native`, `moe_reasoning`, `moe_orchestrated` |
| `tool_model` | LLM for tool execution | `gemma4:31b` |
| `tool_endpoint` | Inference server node | `N04-RTX` |
| `expert_template_id` | Expert template for orchestrated mode | `tmpl-d2300eb6` |
| `tool_max_tokens` | Max output tokens for tool calls | `4096` |
| `reasoning_max_tokens` | Max tokens for thinking blocks | `16384` |
| `tool_choice` | Tool selection behavior | `auto`, `required`, `any` |
| `stream_think` | Whether to stream thinking blocks | `true` / `false` |

### Three Reference Profile Families

#### Reference Profiles (Baseline)

| Profile | Mode | Latency | Use Case |
|---------|------|---------|----------|
| `cc-ref-native` | `native` | 5-30s | Quick edits, simple fixes, interactive coding |
| `cc-ref-reasoning` | `moe_reasoning` | 30-120s | Architecture decisions, complex debugging |
| `cc-ref-orchestrated` | `moe_orchestrated` | 2-10 min | Deep research, security audits, multi-domain synthesis |

Reference profiles use conservative settings and are suitable for most users
out of the box.

#### Innovator Profiles (Power Users)

All three innovator profiles use `moe_orchestrated` mode with dedicated
expert templates tuned for different quality/speed trade-offs:

| Profile | Tool Model | Thinking | Max Tokens | Target Latency |
|---------|-----------|----------|------------|----------------|
| `cc-innovator-fast` | `gemma4:31b` | Off | 4,096 | 30-90s |
| `cc-innovator-balanced` | `Qwen3-Coder-Next` | On | 8,192 / 16K reasoning | 2-5 min |
| `cc-innovator-deep` | `Qwen3-Coder-Next` | On | 8,192 / 32K reasoning | 5-15 min |

**Key differences:**

- **Fast** uses `tool_choice: required` with lightweight models and
  `stream_think: false` for minimal overhead. Best for rapid iteration.
- **Balanced** enables thinking blocks and escalates to domain-specialist
  models via T2 fallback. Good daily-driver default.
- **Deep** uses the largest available models with a security-analyst expert
  category and an assertive system prompt demanding complete, production-grade
  code. Best for security audits and architecture reviews.

### Creating Custom Profiles

1. Navigate to Admin UI > **CC Profile**
2. Click **Create Profile**
3. Fill in the profile fields (see table above)
4. Assign an expert template for orchestrated mode
5. Save the profile

Users can also create personal profiles in the User Portal under
**My Templates** > **CC Profiles**. Personal profiles override
admin-assigned profiles.

### Binding Profiles to API Keys

1. Admin UI > **Users** > select user > **API Keys**
2. Set the **CC Profile** dropdown for the target key
3. All requests made with that key now use the bound profile

This allows a single user to have multiple API keys, each bound to a
different profile, and switch workflows by changing which key is exported.

### 5-Epoch Benchmark Results

A controlled benchmark across 5 consecutive runs measures the compounding
effect of the MoE knowledge pipeline:

| Epoch | Avg Score | Avg Latency | Latency vs Epoch 1 |
|------:|----------:|------------:|--------------------:|
| 1 | 5.2 / 10 | 280s | baseline |
| 2 | 6.4 / 10 | 125s | 0.45x |
| 3 | 7.1 / 10 | 72s | 0.26x |
| 4 | 7.8 / 10 | 45s | 0.16x |
| 5 | 8.1 / 10 | 30s | 0.11x |

By Epoch 5, the system delivers **9.3x faster** responses than Epoch 1
while improving answer quality by 56%. Three mechanisms drive this:

1. **GraphRAG context enrichment** -- prior synthesis results stored as
   `SYNTHESIS_INSIGHT` relations are injected into future expert prompts
2. **L2 plan cache** -- identical task decompositions hit the Valkey
   SHA-256 plan cache, skipping the planner LLM entirely
3. **Model warmth** -- sticky sessions keep frequently used models loaded
   in VRAM, eliminating cold-start overhead

---

## 4. Interactive Chat (Open WebUI, AnythingLLM)

Expert templates are not limited to coding agents. They work equally well
with interactive chat interfaces like Open WebUI and AnythingLLM.

### How It Works

Every expert template configured in MoE Sovereign appears as a "model"
in the `/v1/models` endpoint response. Chat interfaces that support
OpenAI-compatible backends can select these models directly.

```bash
# List all available models (templates)
curl https://your-moe-instance.example.com/v1/models \
  -H "Authorization: Bearer moe-sk-xxxxxxxx..." | jq '.data[].id'
```

### Recommended Templates by Use Case

| Use Case | Recommended Template | Why |
|----------|---------------------|-----|
| Quick Q&A | `8b-fast` or native template | Minimal latency, good for simple questions |
| Code assistance | `30b-balanced` | Strong code models, reasonable latency |
| Deep research | `70b-deep` or `cc-expert-deep` | Full pipeline with largest models |
| Creative writing | Template with `creative_writer` expert | Stylistically tuned models |
| Data analysis | Template with `data_analyst` + MCP tools | MCP provides exact calculations |

### The Accumulation Effect in Chat Sessions

In interactive sessions using orchestrated templates, every exchange
enriches the GraphRAG knowledge graph. This means:

- **First question** in a new domain: full pipeline, highest latency
- **Follow-up questions** in the same domain: GraphRAG injects prior
  context, reducing latency and improving relevance
- **Returning to a topic days later**: accumulated knowledge is still
  available, providing continuity across sessions

This accumulation effect is most valuable for ongoing projects, research
topics, or domains you query repeatedly.

### Session Management Tips

- **Start broad, then narrow.** The first question in a domain seeds the
  knowledge graph. Subsequent specific questions benefit from that context.
- **Use the same template** for related questions within a project to ensure
  GraphRAG continuity.
- **Switch to native mode** for quick follow-ups that do not need the full
  pipeline (e.g., "format that as a table").
- **Check the thinking panel** in Open WebUI to see exactly which experts
  were consulted, which MCP tools ran, and what GraphRAG context was injected.

---

## 5. Levers and Trade-offs

Every configuration choice in MoE Sovereign is a trade-off. This section
explains what each lever does and how to make informed decisions.

### Cache Toggle (`enable_cache`)

Controls the L1 (ChromaDB) and L2 (Valkey) caching layers.

| State | Behavior |
|-------|----------|
| **Enabled** (default) | L1 hit = instant response (no pipeline). L2 plan cache hit = skip planner, run experts only. |
| **Disabled** | Every request runs the full pipeline from scratch. |

**Impact:** L1 cache hits reduce latency to near-zero for repeated or
semantically similar queries. Disabling cache is useful for testing,
debugging, or when you need guaranteed fresh responses.

### GraphRAG Toggle (`enable_graphrag`)

Controls Neo4j knowledge graph enrichment.

| State | Behavior |
|-------|----------|
| **Enabled** (default) | Prior synthesis results are stored and injected into future requests. Knowledge accumulates over time. |
| **Disabled** | No knowledge persistence. Each request is independent. |

**Impact:** Enabling GraphRAG is what drives the accumulation effect (9.3x
speedup over 5 epochs). Disabling it is appropriate for privacy-sensitive
queries where you do not want knowledge to persist, or for isolated
one-off questions.

### Web Research Toggle (`enable_web_research`)

Controls SearXNG web search integration.

| State | Behavior |
|-------|----------|
| **Enabled** (default) | The planner can flag subtasks with `"web": true`, triggering a SearXNG search. Results are injected into the merger. |
| **Disabled** | No web search. Responses rely on model training data and GraphRAG context only. |

**Impact:** Enabling web research improves freshness for current events,
recent documentation, and rapidly changing domains. Disabling it reduces
latency (no search round-trip) and is required for air-gapped deployments.

### T1/T2 Tier Boundary (`EXPERT_TIER_BOUNDARY_B`)

The parameter size threshold (in billions) that separates T1 (fast) from
T2 (deep) models. Default: **20B**.

| Boundary | T1 Models (fast) | T2 Models (deep) |
|----------|-------------------|-------------------|
| 20B (default) | `phi4:14b`, `hermes3:8b`, `gpt-oss:20b` | `devstral-small-2:24b`, `qwen3-coder:30b`, `deepseek-r1:32b` |
| 14B (aggressive) | `hermes3:8b` only | Everything above 14B |
| 30B (conservative) | Most models including 24B | Only 32B+ models |

**Trade-off:** Lowering the boundary means more requests escalate to T2
(higher quality, higher latency). Raising it keeps more requests on T1
(faster, but potentially lower quality for hard problems).

### Node Pinning vs Floating

| Strategy | Pros | Cons |
|----------|------|------|
| **Pinned** (`model@node`) | Predictable latency, no cold starts, guaranteed VRAM | No elasticity, node failure = expert unavailable |
| **Floating** (`model` only) | Elastic, auto-balances across nodes | Cold-start latency if model is not loaded, less predictable |

**Best practice:** Pin the Planner and Judge (they run on every request and
need consistent low latency). Float T2 fallback experts (they run
infrequently and benefit from elasticity).

### VRAM Planning per Template

To calculate total VRAM needed for a template, sum the VRAM requirements
of all models that may be loaded simultaneously:

```
Total VRAM = Planner + Judge + (number of parallel experts x largest expert model)
```

**Approximate VRAM per model size:**

| Model Size | VRAM (Q4 quantized) | VRAM (FP16) |
|------------|--------------------:|------------:|
| 7-8B | ~5 GB | ~16 GB |
| 14B | ~9 GB | ~28 GB |
| 20-24B | ~14 GB | ~48 GB |
| 30-32B | ~20 GB | ~64 GB |
| 70B | ~40 GB | ~140 GB |

**Example:** A template with `phi4:14b` (planner, 9 GB) + `phi4:14b`
(judge, shared, 0 additional) + 3 parallel experts at `gpt-oss:20b`
(3 x 14 GB = 42 GB) needs approximately **51 GB** total VRAM across
all nodes.

!!! tip "Shared models reduce VRAM"
    If the planner and judge use the same model on the same node, the model
    is loaded once. Similarly, if multiple experts use the same model on the
    same node, VRAM is shared. Design templates to reuse models where possible.

### Decision Matrix

| Priority | Recommended Settings |
|----------|---------------------|
| **Minimum latency** | Native mode, cache enabled, no GraphRAG, no web research |
| **Maximum quality** | Orchestrated mode, all toggles enabled, T2 boundary at 14B |
| **Privacy / air-gap** | Any mode, GraphRAG disabled, web research disabled |
| **Cost efficiency** | Native for simple tasks, orchestrated only for complex. Cache enabled. |
| **Knowledge building** | Orchestrated mode, GraphRAG enabled, cache enabled |
| **Predictable performance** | All models pinned, cache enabled |
| **Maximum elasticity** | All models floating, cache enabled |
