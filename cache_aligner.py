"""cache_aligner.py — Anthropic prompt-caching for moe-sovereign expert/judge calls.

Native adaptation of Headroom's CacheAligner: the static system prompt (expert
instructions + tool definitions) is marked with cache_control so Anthropic's
server-side KV cache hits on every repeat call within a 5-minute TTL window.

Result: cached system-prompt tokens cost 10% of normal input price.
Typical expert call: 1500-5000 static tokens → 85-90% cost reduction per call.

Only active when api_type == "anthropic" in the inference server config.
All other paths (ollama, openai-compat, litellm) are unaffected.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("MOE-SOVEREIGN")

_ANTHROPIC_CACHE_BETA  = "prompt-caching-2024-07-31"
_ANTHROPIC_API_VERSION = "2023-06-01"

# Anthropic's minimum cacheable block is 1024 tokens (~4096 chars for English prose,
# fewer for German/code). We use a conservative 800-char floor so short expert prompts
# without tool injections don't waste a cache breakpoint slot.
_MIN_CACHE_CHARS = 800


def is_anthropic_native(api_type: str) -> bool:
    """True when the endpoint is Anthropic's Messages API (not OpenAI-compat or Ollama)."""
    return (api_type or "").lower() == "anthropic"


def build_system_blocks(static_text: str, dynamic_text: str = "") -> List[Dict]:
    """Assemble Anthropic system content blocks with cache_control on the static part.

    The static block (expert prompt + tool definitions, identical across calls for
    the same category) carries cache_control so Anthropic's KV cache can be reused.
    The dynamic block (agent context, correction memory, behavioral directives) is
    appended without caching — it varies per query or session.

    Anthropic allows up to 4 cache breakpoints per request; we use exactly 1 here.
    """
    blocks: List[Dict] = []

    if static_text:
        block: Dict[str, Any] = {"type": "text", "text": static_text}
        if len(static_text) >= _MIN_CACHE_CHARS:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)

    if dynamic_text and dynamic_text.strip():
        blocks.append({"type": "text", "text": dynamic_text})

    return blocks


def oai_to_anthropic_messages(messages: List[Dict]) -> List[Dict]:
    """Strip system messages and convert OpenAI-format messages to Anthropic format.

    The system prompt is passed separately via build_system_blocks(), so system-role
    messages are dropped here. All other roles pass through unchanged.
    Multimodal content (list of blocks) is forwarded as-is.
    """
    result: List[Dict] = []
    for m in messages:
        role = m.get("role", "")
        if role == "system":
            continue
        content = m.get("content", "")
        if isinstance(content, list):
            result.append({"role": role, "content": content})
        else:
            result.append({"role": role, "content": str(content) if content is not None else ""})
    return result


async def call_anthropic_cached(
    url: str,
    token: str,
    model: str,
    messages_oai: List[Dict],
    static_system: str,
    dynamic_system: str = "",
    max_tokens: int = 4096,
    timeout: float = 120.0,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, Dict[str, int]]:
    """POST to Anthropic /v1/messages with cache_control on the static system block.

    Returns (response_text, usage_dict).

    usage_dict keys:
        prompt_tokens                — uncached input tokens (billed at full rate)
        completion_tokens            — output tokens
        cache_creation_input_tokens  — tokens written to cache (billed at 1.25× for first write)
        cache_read_input_tokens      — tokens read from cache (billed at 0.1× — the saving)

    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    system_blocks = build_system_blocks(static_system, dynamic_system)
    anthr_messages = oai_to_anthropic_messages(messages_oai)

    base = url.rstrip("/").removesuffix("/v1")
    endpoint = f"{base}/v1/messages"

    headers: Dict[str, str] = {
        "x-api-key":         token,
        "anthropic-version": _ANTHROPIC_API_VERSION,
        "anthropic-beta":    _ANTHROPIC_CACHE_BETA,
        "content-type":      "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload: Dict[str, Any] = {
        "model":      model,
        "system":     system_blocks,
        "messages":   anthr_messages,
        "max_tokens": max_tokens,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    text = "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    )

    usage = data.get("usage", {})
    usage_dict: Dict[str, int] = {
        "prompt_tokens":               usage.get("input_tokens", 0),
        "completion_tokens":           usage.get("output_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens":     usage.get("cache_read_input_tokens", 0),
    }

    _cache_read    = usage_dict["cache_read_input_tokens"]
    _cache_created = usage_dict["cache_creation_input_tokens"]
    if _cache_read > 0:
        logger.info(
            "⚡ Anthropic cache HIT  model=%s  read=%d tok  created=%d tok  uncached=%d tok",
            model, _cache_read, _cache_created, usage_dict["prompt_tokens"],
        )
    elif _cache_created > 0:
        logger.debug(
            "📦 Anthropic cache WRITE  model=%s  written=%d tok",
            model, _cache_created,
        )

    return text, usage_dict
