"""
context_budget.py — Per-template context window budget management.

Provides a static lookup table of known model context windows and helper
functions to compute the GraphRAG character budget that fits within a target
model's context window.

The orchestrator pipeline injects several content blocks into the merger
(judge) prompt:
  - System instruction + mode prefix   (~500–800 tokens)
  - REQUEST (user query)               (variable)
  - EXPERT RESPONSES (4 × 2000 chars)  (~2 000 tokens)
  - STRUCTURED KNOWLEDGE (GraphRAG)    (variable — this is what we cap)
  - Other sections (web, math, MCP)    (~200–600 tokens)

Target: leave at least MERGER_HEADROOM_TOKENS tokens for the merger model's
own generation.  Total budget = model_ctx - headroom - fixed_sections.

Usage:
  from context_budget import graphrag_budget_chars

  # Returns the char limit to pass to graph_rag truncation.
  limit = graphrag_budget_chars(
      model="phi4:14b",
      query_chars=len(user_input),
      override_chars=template_graphrag_max_chars,  # 0 = auto
  )
"""

from __future__ import annotations

# ── Char-to-token approximation ──────────────────────────────────────────────
# Conservative estimate: 4 chars ≈ 1 token (English/German mixed text).
CHARS_PER_TOKEN: int = 4

# ── Fixed overhead reserved for non-GraphRAG merger sections (tokens) ────────
# system instruction + mode prefix + expert responses (4 × 500) + misc
MERGER_FIXED_TOKENS: int = 3_500

# ── Headroom for merger generation ───────────────────────────────────────────
# Reserve this many tokens for the merger model to produce its output.
MERGER_HEADROOM_TOKENS: int = 2_000

# ── Minimum GraphRAG budget (chars) ──────────────────────────────────────────
# Even on very small models, don't drop below this — too small is useless.
MIN_GRAPHRAG_CHARS: int = 800

# ── Fallback when model is not in the lookup table ───────────────────────────
# Corresponds to the existing MAX_GRAPH_CONTEXT_CHARS default of 6 000.
DEFAULT_GRAPHRAG_CHARS: int = 6_000

# ── Known model context windows (tokens) ─────────────────────────────────────
# Key: lowercase model name without tag (or with specific tag for exceptions).
# Value: context window in tokens.
#
# Sources: model cards, Ollama GGUF metadata, vendor documentation.
# When a model has a configurable context window, list the default/safe value
# for the hardware tier the model is commonly used on in this deployment.
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    # ── phi family ────────────────────────────────────────────────────────────
    "phi4:14b":              16_384,   # microsoft/phi-4 — default 16k window
    "phi4":                  16_384,
    "phi3:14b":              16_384,
    "phi3:medium":           16_384,
    "phi3":                   4_096,   # phi3 mini — 4k default
    # ── qwen2.5 / qwen3 family ───────────────────────────────────────────────
    "qwen2.5-coder:7b":     32_768,
    "qwen2.5-coder:14b":    32_768,
    "qwen2.5-coder:32b":    32_768,
    "qwen2.5:7b":           32_768,
    "qwen2.5:14b":          32_768,
    "qwen2.5:32b":          32_768,
    "qwen3:8b":             32_768,
    "qwen3:32b":            32_768,
    "qwen3-coder:30b":      32_768,
    # ── mistral / hermes / llama family ──────────────────────────────────────
    "mistral:7b":            8_192,
    "mistral-nemo:latest":  32_768,
    "hermes3:8b":            8_192,
    "llama3.1:8b":          16_384,
    "llama3.2:3b":          16_384,
    "llama3.1:70b":         16_384,
    # ── math / science specialists ────────────────────────────────────────────
    "mathstral:7b":          4_096,
    "meditron:7b":           4_096,
    # ── glm4 / gemma ─────────────────────────────────────────────────────────
    "glm4:9b":               8_192,
    "gemma2:9b":             8_192,
    # ── translation / German ─────────────────────────────────────────────────
    "translategemma:27b":    8_192,
    "sroecker/sauerkrautlm-7b-hero:latest":  4_096,
    # ── large / cloud-tier models ─────────────────────────────────────────────
    "gpt-oss:20b":          32_768,
    "solar-pro:22b":        32_768,
    "qwen3.5:27b":          32_768,
    # ── AIHUB sovereign (H200 cloud) ─────────────────────────────────────────
    "gpt-oss-120b-sovereign":   32_768,
    "qwen-3.5-122b-sovereign":  32_768,
    # ── Local fallback models (N04-RTX) ───────────────────────────────────────
    "qwen3.6:35b":              32_768,
    "qwen3.6":                  32_768,
    "gemma4:31b":                8_192,
    "gemma4:12b":                8_192,
    "gemma4":                    8_192,
    "gemma3:27b":                8_192,
    "gemma3":                    8_192,
    # ── Additional qwen3 variants ─────────────────────────────────────────────
    "qwen3:14b":                32_768,
    "qwen3:7b":                 32_768,
    "qwen3:4b":                 32_768,
}


def get_model_context_window(model: str) -> int:
    """Return the known context window (tokens) for *model*.

    Falls back to a generic lookup by base model name (strips the tag).
    Returns 0 if unknown.
    """
    name = (model or "").strip().lower()
    if name in MODEL_CONTEXT_WINDOWS:
        return MODEL_CONTEXT_WINDOWS[name]
    # Try base name without tag (e.g. "phi4:14b-q4" → "phi4:14b" → "phi4")
    base = name.split(":")[0]
    for key, val in MODEL_CONTEXT_WINDOWS.items():
        if key.startswith(base + ":") or key == base:
            return val
    return 0


def graphrag_budget_chars(
    model: str,
    query_chars: int = 0,
    override_chars: int = 0,
) -> int:
    """Compute the maximum number of characters for GraphRAG context injection.

    Priority (highest to lowest):
    1. ``override_chars > 0``  — explicit char limit from template config.
    2. ``override_chars == -1``  — skip template limit; auto-compute from model
       context window only (sentinel: "no internal cap, only the env-var global
       cap ``MAX_GRAPH_CONTEXT_CHARS`` applies").
    3. ``override_chars == 0``  — auto (same as -1, kept for clarity).
       Auto-compute from ``model``'s known context window.
    4. ``DEFAULT_GRAPHRAG_CHARS``  — global fallback when model is unknown.

    The per-template ``graphrag_max_chars`` field maps directly to
    ``override_chars``:
      - ``graphrag_max_chars: N  (N > 0)``  → exact N chars.
      - ``graphrag_max_chars: 0``            → auto (full model capacity).
      - ``graphrag_max_chars: -1``           → same as 0, explicit intent.

    The global ceiling ``MAX_GRAPH_CONTEXT_CHARS`` (env var, applied by the
    caller) is the only remaining cap after this function returns.

    Args:
        model:          The merger/judge model name (e.g. "phi4:14b").
        query_chars:    Length of the user query in characters.
        override_chars: Per-template explicit limit (0 or -1 = auto).

    Returns:
        Maximum GraphRAG chars that fit within the model's context window.
        Always at least ``MIN_GRAPHRAG_CHARS``.
    """
    if override_chars > 0:
        return override_chars

    # override_chars == 0 or -1 → auto-compute from model context window.
    ctx_tokens = get_model_context_window(model)
    if ctx_tokens <= 0:
        # Unknown model — use the conservative fallback.
        return DEFAULT_GRAPHRAG_CHARS

    query_tokens = (query_chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
    available = ctx_tokens - MERGER_FIXED_TOKENS - MERGER_HEADROOM_TOKENS - query_tokens
    return max(MIN_GRAPHRAG_CHARS, available * CHARS_PER_TOKEN)


# ── Web-research budget constants ─────────────────────────────────────────────
# After GraphRAG and expert responses are allocated, what remains goes to web.
# Minimum: always pass at least this much web context to the judge.
MIN_WEB_CHARS: int = 1_200
DEFAULT_WEB_CHARS: int = 4_000

# Fraction of remaining context budget allocated to web research (after GraphRAG).
# The rest is reserved for expert outputs and headroom already in MERGER_FIXED_TOKENS.
WEB_BUDGET_FRACTION: float = 0.55


def web_research_budget(
    model: str,
    query_chars: int = 0,
    graphrag_chars_used: int = 0,
) -> tuple[int, int]:
    """Compute adaptive web-research block and per-block character limits.

    Returns (max_blocks, max_block_chars) that the merger should use when
    compressing web_research before injecting it into the judge prompt.

    The limits scale with the judge model's context window so that:
      - Small models (gemma4:31b  8K): fewer / shorter blocks
      - Mid models   (qwen3.6    32K): moderate blocks
      - Large models (gpt-120B  128K): generous blocks

    Args:
        model:              Judge model name.
        query_chars:        Length of the user query in characters.
        graphrag_chars_used: Actual chars of GraphRAG context that will be included.

    Returns:
        (max_blocks, max_block_chars)
    """
    ctx_tokens = get_model_context_window(model)
    if ctx_tokens <= 0:
        return 5, DEFAULT_WEB_CHARS // 5  # fallback: 5 blocks × 800 chars

    query_tokens  = (query_chars + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
    graph_tokens  = (graphrag_chars_used + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN
    remaining     = ctx_tokens - MERGER_FIXED_TOKENS - MERGER_HEADROOM_TOKENS - query_tokens - graph_tokens
    web_tokens    = max(MIN_WEB_CHARS // CHARS_PER_TOKEN, int(remaining * WEB_BUDGET_FRACTION))
    web_chars     = web_tokens * CHARS_PER_TOKEN

    # Distribute across blocks: prefer more shorter blocks over fewer long ones.
    if web_chars >= 8_000:
        return 7, 1_200
    elif web_chars >= 4_000:
        return 5, 900
    elif web_chars >= 2_000:
        return 3, 700
    else:
        return 2, max(MIN_WEB_CHARS // 2, web_chars // 2)
