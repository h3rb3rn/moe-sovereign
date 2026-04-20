# CLI Agents × MoE Sovereign — Best Of

This page explains **why coding agents like Aider, Open Interpreter, and
Continue.dev get dramatically more out of MoE Sovereign** than from a standard
cloud API — and what that means in practice for your daily workflow.

No prior knowledge of the MoE internals required. Where relevant, concrete
numbers from the implementation are given so you can verify the claims yourself.

---

## The Core Idea in One Sentence

A standard LLM API answers your question and forgets everything.
MoE Sovereign answers your question, **learns from the result**, and is already
smarter the next time you ask something similar.

---

## What Makes CLI Agents Special

Most LLM clients send one question and receive one answer. CLI coding agents do
something fundamentally different: they **execute code**, look at what happens,
and try again. That loop — try → fail → fix → try again — happens dozens of
times in a single session.

```
Standard client:     Question ──▶ Answer
                     (and that's it)

Aider / Open Interpreter:
    Question ──▶ Answer ──▶ Execute ──▶ Error
         ▲                                │
         └────────────── Fix ◀────────────┘
         (loop repeats many times per session)
```

This loop is exactly what MoE Sovereign was built for. Every iteration becomes
a data point that makes the next answer better — not just for you in this
session, but for every future request with a similar pattern.

---

## The Two Categories That Benefit Most

### 1. Execution-Loop Agents — Aider, Open Interpreter

These tools write code, run it, see the error, fix it, run it again. This
creates a high-frequency feedback stream that hits all four MoE capabilities
at once.

**What you notice in practice:**

- The same `pytest` error pattern answered in **under 200ms** on the second
  occurrence — the semantic cache recognises it without calling any LLM.
- A `TypeError` that was corrected once in a previous session is **never
  repeated** for similar code — the correction was stored in the Knowledge
  Graph and is injected into the expert's prompt automatically.
- When you're clearly fixing Python code, the request is routed to the
  `code_reviewer` expert without wasting time on a planning step — saving
  roughly **300ms** per request.

### 2. Infrastructure Orchestrators — Hermes, Continue.dev (server mode)

These tools coordinate work across multiple files, services, or systems. They
benefit most from the Knowledge Graph and parallel expert execution.

**What you notice in practice:**

- When you ask about a node in your cluster (e.g. "What runs on N06-M10?"),
  the answer comes from the Knowledge Graph — no guessed IP addresses, no
  hallucinated container names.
- A task that involves checking logic, reviewing architecture, and estimating
  performance impact runs on **three experts in parallel** — total wait time
  equals the slowest expert, not the sum of all three.
- Deployment patterns that worked are retained across sessions as synthesis
  nodes in Neo4j — the system doesn't rediscover the same solution from scratch.

---

## Four Capabilities, Plain Language

### 1. Domain Experts (not a generic "assistant")

MoE Sovereign routes each request to the most appropriate specialist:

| What you type | Expert assigned | What's different |
|---|---|---|
| "Fix this Python error" | `code_reviewer` | System prompt with code-specific conventions, no medical or legal knowledge bleeding in |
| "Calculate network subnet" | `tool_expert` + MCP | Deterministic result from a math tool — not an LLM estimate |
| "Review this architecture" | `reasoning` | Focused purely on logical structure, no web search noise |
| "What does this regulation mean?" | `legal_advisor` | Only German law entities from the Knowledge Graph — BGB, DSGVO, GG |

Routing happens automatically. You don't configure it — you just describe your
problem in plain language.

**How quickly it routes:** If the request closely matches a known pattern
(cosine distance < 0.18, with a clear winner over second place), the expert
is called directly in about **300ms less** than going through the planner.

---

### 2. Correction Memory (learns from mistakes)

Every time the Judge LLM detects that a first answer was wrong and a second
attempt was meaningfully better (by at least 15%), the correction is stored:

```
What went wrong:  "Suggested pip install X — X doesn't exist"
What was correct: "The package is named Y, installed with pip install Y"
Pattern match:    "import error / package not found / pip install"
```

The next time a similar import error appears — even in a completely different
project — the correction is injected into the expert's prompt **before** the
LLM starts generating. The mistake is not repeated.

!!! info "The more you use it, the better it gets"
    The correction record tracks `times_applied` — how often a stored correction
    prevented a repeat mistake. Corrections that fire frequently rank higher in
    similarity searches and are surfaced with higher priority.

---

### 3. Knowledge Graph (your infrastructure, remembered)

The Neo4j Knowledge Graph stores facts that the system has encountered and
verified. For infrastructure work, this includes:

- Which models run on which hardware nodes
- Which services listen on which ports
- Which containers belong to which stack
- Procedural rules: "deploying service X requires node Y to be reachable first"

When you ask about your infrastructure, the system retrieves these facts
directly from the graph rather than relying on the LLM to guess:

```
[Knowledge Graph]
• N04-RTX (InferenceNode): HOSTS qwen3-coder:30b | PORT 11434 | VRAM 24GB × 5
• N06-M10-01 (InferenceNode): HOSTS phi4:14b | PORT 11434 | VRAM 10GB
```

This context is injected into the expert prompt automatically. No prompt
engineering needed on your part.

**Domain isolation:** A `code_reviewer` request only sees technical entities
from the graph. A `legal_advisor` request only sees law and regulation entities.
Medical facts never appear in code review answers — and vice versa.

---

### 4. Four-Layer Cache (fast answers for repeated patterns)

The cache is not a simple key-value store — it uses **semantic similarity** to
recognise when two questions mean the same thing even if they're worded
differently.

```
Layer 0  Exact hash match             ~10ms   (identical query repeated)
Layer 1  Semantic similarity < 0.15   ~100ms  (same meaning, different words)
Layer 1  Soft hit: 0.15–0.50          —       (similar — injected as examples)
Layer 2  GraphRAG context cache        ~10ms  (same topic, infrastructure queries)
Layer 3  LangGraph checkpoints        always  (session history, survives restarts)
```

**In practice for Aider:** If you're iterating on the same module and hitting
similar errors, by the third or fourth iteration the answer often comes from
the semantic cache in under 200ms — no LLM inference at all.

**Cache quality control:** If you submit a negative rating (`POST /v1/feedback`,
rating 1–2), the cached response is flagged. Future lookups skip flagged entries.
Bad answers don't poison the cache.

---

## Before / After: A Typical Aider Session

=== "Without MoE Sovereign (standard API)"

    ```
    Attempt 1: Ask GPT-4o to fix ImportError
               → 1.2s response, generic suggestion
               → Execute: still fails

    Attempt 2: Same error, different wording
               → 1.1s response, same generic suggestion
               → Execute: still fails

    Attempt 3: Rephrased again
               → 0.9s response, slightly different suggestion
               → Execute: works

    Next session, same codebase, same error:
               → Start from zero. No memory. Same 3 attempts.
    ```

=== "With MoE Sovereign"

    ```
    Attempt 1: Routed to code_reviewer expert (~0.9s)
               → Execute: still fails
               → Judge detects issue, correction stored in Neo4j

    Attempt 2: L1 semantic cache hit (~120ms)
               + correction from Attempt 1 injected into prompt
               → Execute: works

    Next session, same codebase, similar error:
               → Correction already in Neo4j
               → Injected into expert prompt on first attempt
               → Execute: works on attempt 1
    ```

---

## Connecting Your Agent

All agents connect via the standard OpenAI-compatible endpoint — no plugins,
no special drivers.

=== "Aider"

    ```bash
    aider \
      --openai-api-base http://localhost:8002/v1 \
      --openai-api-key moe-sk-yourkey \
      --model moe-orchestrator
    ```

    For faster responses on pure code tasks (skips web research):

    ```bash
    aider \
      --openai-api-base http://localhost:8002/v1 \
      --openai-api-key moe-sk-yourkey \
      --model moe-orchestrator-code
    ```

=== "Open Interpreter"

    ```bash
    export OPENAI_API_BASE=http://localhost:8002/v1
    export OPENAI_API_KEY=moe-sk-yourkey
    interpreter --model moe-orchestrator
    ```

=== "Continue.dev"

    In `~/.continue/config.json`:

    ```json
    {
      "models": [
        {
          "title": "MoE Sovereign",
          "provider": "openai",
          "model": "moe-orchestrator",
          "apiBase": "http://localhost:8002/v1",
          "apiKey": "moe-sk-yourkey"
        }
      ]
    }
    ```

=== "Hermes / any OpenAI SDK"

    ```python
    from openai import OpenAI

    client = OpenAI(
        base_url="http://localhost:8002/v1",
        api_key="moe-sk-yourkey",
    )
    response = client.chat.completions.create(
        model="moe-orchestrator",
        messages=[{"role": "user", "content": "..."}],
    )
    ```

---

## Activating the RL Loop

The feedback endpoint activates the reinforcement learning pipeline:

```bash
# After a response you want to rate:
curl -X POST http://localhost:8002/v1/feedback \
  -H "Authorization: Bearer moe-sk-yourkey" \
  -H "Content-Type: application/json" \
  -d '{"response_id": "chatcmpl-...", "rating": 5}'
```

| Rating | Effect |
|---|---|
| 4–5 | Positive: expert score in Valkey incremented, response stays in cache |
| 1–2 | Negative: expert score decremented, ChromaDB entry flagged, skipped on future lookups |
| 3 | Neutral: no score update |

After approximately 5 ratings per expert category, Thompson Sampling (Beta
distribution sampling) begins actively experimenting with model combinations,
gradually steering traffic toward consistently better-performing experts.

---

## Frequently Asked Questions

**Does my agent need to be modified to use MoE Sovereign?**

No. Any tool that supports an OpenAI-compatible `base_url` works without
modification. The routing, caching, and learning happen transparently on the
server side.

**Does the system work without feedback ratings?**

Yes. Cache hits, correction memory, and knowledge graph enrichment all happen
automatically from the execution loop alone — no ratings needed. Ratings
accelerate the expert score learning and add an explicit quality signal,
but are optional.

**How long until correction memory starts helping?**

From the first corrected mistake. The correction is stored immediately after
the Judge detects a ≥15% improvement. The next semantically similar query
benefits from it.

**Can I use different models for different task types?**

Yes — via Expert Templates in the Admin UI. Each template defines which model
runs for each expert category. You can assign heavier models to `code_reviewer`
and lighter models to `translation` within the same template. See
[Expert Templates](../admin/templates.md).

---

## Further Reading

- [Architectural Deep Dive](../system/intelligence/cli_agent_integration.md) — Full technical analysis with Mermaid diagrams, code references, and measured thresholds
- [RL Flywheel](../system/intelligence/rl_flywheel.md) — Thompson Sampling, telemetry table, correction memory storage schema
- [Agent Integration Profiles](agent-profiles.md) — Pre-configured profiles for Aider, Continue.dev, and Claude Code
- [Continue / Cursor Setup](continue-dev.md) — Step-by-step configuration guide
- [Expert Templates](../admin/templates.md) — Customising which model runs for which task type
