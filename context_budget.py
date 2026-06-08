"""
context_budget.py — Per-template context window budget management.

Context window resolution uses a four-tier priority chain:

  1. Explicit override (JUDGE_NUM_CTX / PLANNER_NUM_CTX env vars, or template
     context_window field) — always wins.
  2. Redis cache (TTL 1 h) — warm after first use, avoids repeated API calls.
  3. Ollama /api/show (async) — reads GGUF metadata, always accurate and
     model-agnostic. This is the primary dynamic source.
  4. Parameter-count heuristic (sync fallback) — extracts "Xb" from the model
     name and maps it to a conservative context estimate. Works for any future
     model without requiring a table update.

The old name-based lookup table has been removed. It was maintenance-heavy and
would silently return wrong values for renamed or future models.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional

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

# ── Parameter-count → context-window heuristic ───────────────────────────────
# Maps billion-parameter ranges to conservative context window estimates.
# Used only as a last-resort sync fallback when Redis is cold and Ollama is
# unreachable. The Ollama /api/show path (tier 3) always wins when available.
#
# Rationale: parameter count is embedded in every Ollama model tag ("7b", "14b",
# "35b" …) and is stable across model families and generations. This heuristic
# will remain correct for future models without any table maintenance.
#
# Values are intentionally conservative — they represent what fits in typical
# VRAM rather than the model's theoretical maximum.
_PARAM_CTX_HEURISTIC: list[tuple[float, int]] = [
    (3.0,   4_096),   # ≤3 B  — tiny models, usually 4 k default
    (9.0,   8_192),   # 4–9 B — standard small models
    (15.0, 16_384),   # 10–15 B
    (25.0, 32_768),   # 16–25 B
    (40.0, 32_768),   # 26–40 B (may be VRAM-constrained)
    (70.0, 32_768),   # 41–70 B
]
_DEFAULT_CTX_HEURISTIC: int = 32_768   # > 70 B or unknown size


def _params_from_name(model: str) -> float:
    """Extract parameter count (billions) from a model tag like 'qwen3:35b' or 'phi4:14b-fp16'.

    Returns 0.0 when no parameter count is found.
    """
    m = re.search(r"[:\-_](\d+(?:\.\d+)?)b\b", model.lower())
    return float(m.group(1)) if m else 0.0


# ── Per-model max output tokens ───────────────────────────────────────────────
# Populated dynamically via fetch_openai_max_output() and cached in Redis.
# Admin-configured overrides belong in the inference server / template config,
# not here. This dict stays empty in the codebase.
MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {}


def get_model_max_output(model: str, fetched_max_output: int = 0) -> int:
    """Return the max output tokens for *model*.

    Priority: fetched_max_output (from Redis / live API) → static table →
    MERGER_HEADROOM_TOKENS fallback.  The static table (MODEL_MAX_OUTPUT_TOKENS)
    is intentionally empty; all runtime values come from the dynamic fetch path
    in get_model_max_output_async().
    """
    if fetched_max_output > 0:
        return fetched_max_output
    name = (model or "").strip().lower()
    return MODEL_MAX_OUTPUT_TOKENS.get(name, MERGER_HEADROOM_TOKENS)


async def get_model_max_output_async(
    model: str,
    base_url: str = "",
    token: str = "ollama",
    redis_client=None,
) -> int:
    """Resolve max output tokens: Redis cache → /v1/models API → fallback.

    Cache-Key: moe:maxout:{sha256(base_url)[:12]}:{model}  TTL: 3600s
    """
    if redis_client and base_url:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:maxout:{url_hash}:{model}"
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception:
            pass

    fetched = 0
    if base_url and token != "ollama":
        fetched = await fetch_openai_max_output(model, base_url, token)

    result = get_model_max_output(model, fetched)

    if redis_client and base_url and fetched > 0:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:maxout:{url_hash}:{model}"
        try:
            await redis_client.setex(cache_key, 3600, str(fetched))
        except Exception:
            pass

    return result


def get_model_context_window(model: str) -> int:
    """Estimate context window (tokens) for *model* using a parameter-count heuristic.

    Extracts the billion-parameter count from the model tag (e.g. "qwen3:35b" → 35,
    "phi4:14b-fp16" → 14) and maps it to a conservative VRAM-safe context estimate.
    Returns 0 when no parameter count can be parsed (caller should treat as unknown).

    This replaces the old name-based lookup table, which required manual maintenance
    and silently returned wrong values for renamed or future models.
    """
    params = _params_from_name(model)
    if params <= 0:
        return 0
    for threshold, ctx in _PARAM_CTX_HEURISTIC:
        if params <= threshold:
            return ctx
    return _DEFAULT_CTX_HEURISTIC


async def _fetch_litellm_model_info(model: str, base_url: str, token: str,
                                    timeout: float = 5.0) -> dict:
    """Fetch model capabilities from LiteLLM's /model/info endpoint.

    LiteLLM exposes richer metadata (max_input_tokens, max_output_tokens) at
    /model/info — a non-standard path outside the /v1 prefix.  Returns the
    model_info sub-dict for the requested model, or {} on any failure.
    """
    try:
        import httpx
        # Strip /v1 suffix: LiteLLM's admin routes live at the root
        root = base_url.rstrip("/").removesuffix("/v1")
        url  = f"{root}/model/info"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return {}
            for entry in resp.json().get("data", []):
                if entry.get("model_name") == model:
                    return entry.get("model_info", {})
    except Exception:
        pass
    return {}


async def fetch_openai_context_window(model: str, base_url: str, token: str,
                                      timeout: float = 5.0) -> int:
    """Query an OpenAI-compatible /v1/models/{model} endpoint for its context window.

    Falls back to LiteLLM's /model/info when the standard endpoint returns no
    context-window field (common with LiteLLM-backed providers like AIHUB).
    Returns 0 when completely unavailable.
    """
    try:
        import httpx
        url = f"{base_url.rstrip('/')}/models/{model}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                for field in ("context_window", "context_length", "max_context",
                              "max_input_tokens", "max_tokens"):
                    val = data.get(field)
                    if isinstance(val, int) and val > 0:
                        return val
    except Exception:
        pass
    # LiteLLM fallback: /model/info exposes max_input_tokens per model
    info = await _fetch_litellm_model_info(model, base_url, token, timeout)
    for field in ("context_window", "max_input_tokens"):
        val = info.get(field)
        if isinstance(val, int) and val > 0:
            return val
    return 0


async def fetch_openai_max_output(model: str, base_url: str, token: str,
                                  timeout: float = 5.0) -> int:
    """Query /v1/models/{model} for the model's max output token limit.

    Falls back to LiteLLM's /model/info when the standard endpoint returns no
    output-limit field.  Returns 0 when unavailable.
    """
    try:
        import httpx
        url = f"{base_url.rstrip('/')}/models/{model}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                data = resp.json()
                for field in ("max_output_tokens", "max_completion_tokens",
                              "max_tokens_output", "output_context_window"):
                    val = data.get(field)
                    if isinstance(val, int) and val > 0:
                        return val
    except Exception:
        pass
    # LiteLLM fallback
    info = await _fetch_litellm_model_info(model, base_url, token, timeout)
    for field in ("max_output_tokens", "max_completion_tokens", "max_tokens"):
        val = info.get(field)
        if isinstance(val, int) and val > 0:
            return val
    return 0


async def fetch_ollama_num_ctx(model: str, base_url: str, token: str = "ollama",
                               timeout: float = 5.0) -> int:
    """Query Ollama for a model's effective context window.

    Strategy (in priority order):
    1. /api/ps  — context_window field of a running model. This reflects the
       actual allocation set by OLLAMA_CONTEXT_LENGTH or per-model num_ctx.
    2. /api/show parameters — explicit num_ctx in the modelfile.
    Returns 0 if unavailable.
    """
    try:
        import httpx
        api_base = base_url.rstrip("/").removesuffix("/v1")
        async with httpx.AsyncClient(timeout=timeout) as client:
            # 1. Check /api/ps — gives the real allocated context for running models
            try:
                ps_resp = await client.get(
                    f"{api_base}/api/ps",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if ps_resp.status_code == 200:
                    for entry in ps_resp.json().get("models", []):
                        entry_name = entry.get("name") or entry.get("model") or ""
                        if entry_name == model or entry_name.split(":")[0] == model.split(":")[0]:
                            ctx = entry.get("context_window") or entry.get("context_length")
                            if ctx and int(ctx) > 0:
                                return int(ctx)
            except Exception:
                pass

            # 2. /api/show — explicit num_ctx in parameters OR native context_length in model_info
            resp = await client.post(
                f"{api_base}/api/show",
                json={"model": model},
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code != 200:
                return 0
            data = resp.json()
            params = data.get("parameters", "") or ""
            # parameters is a multi-line string: "num_ctx 32768\nstop ...\n..."
            for line in params.splitlines():
                m = re.match(r"^\s*num_ctx\s+(\d+)", line, re.IGNORECASE)
                if m:
                    return int(m.group(1))
            # 3. model_info — native context_length from the GGUF metadata
            # Covers models without explicit num_ctx in their Modelfile (e.g. qwen3.6:35b).
            # Field names vary by architecture; scan all keys ending in "context_length".
            model_info = data.get("model_info") or {}
            for key, val in model_info.items():
                if key.endswith("context_length") and isinstance(val, int) and val > 0:
                    return val
    except Exception:
        pass
    return 0


async def get_model_ctx_async(
    model: str,
    base_url: str = "",
    token: str = "ollama",
    redis_client=None,
    override: int = 0,
) -> int:
    """Resolve context window: override → Redis-cache → Ollama /api/show → static table.

    Cache-Key: moe:ctx:{sha256(base_url)[:12]}:{model}  TTL: 3600s
    Falls back gracefully at every step — never raises.
    Returns 0 if completely unknown.
    """
    if override > 0:
        return override

    # Redis cache lookup
    if redis_client and base_url:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:ctx:{url_hash}:{model}"
        try:
            cached = await redis_client.get(cache_key)
            if cached is not None:
                return int(cached)
        except Exception:
            pass

    # Dynamic fetch — Ollama via /api/show, OpenAI-compatible via /v1/models/{id}
    fetched = 0
    if base_url:
        if token == "ollama":  # heuristic: local Ollama nodes use "ollama" token
            fetched = await fetch_ollama_num_ctx(model, base_url, token)
        else:
            fetched = await fetch_openai_context_window(model, base_url, token)

    # Static table fallback (local model families only — no deployment-specific names)
    result = fetched or get_model_context_window(model)

    # Cache the result
    if redis_client and base_url and result > 0:
        url_hash = hashlib.sha256(base_url.encode()).hexdigest()[:12]
        cache_key = f"moe:ctx:{url_hash}:{model}"
        try:
            await redis_client.setex(cache_key, 3600, str(result))
        except Exception:
            pass

    return result


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
    max_output   = get_model_max_output(model)
    available    = ctx_tokens - MERGER_FIXED_TOKENS - max_output - query_tokens
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
