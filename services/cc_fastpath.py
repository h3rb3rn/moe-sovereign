"""
services/cc_fastpath.py — Fast-path triage for the /v1/messages endpoint.

Detects requests that must NOT run through the full MoE pipeline
(planner → experts → judge): Claude Code's internal utility calls
(topic detection, title generation, quota probes) and trivially short
prompts. These are answered by a single direct LLM call instead.

Flag: CC_FASTPATH=1 enables the fast path (default: off).
      CC_FASTPATH_MAX_CHARS overrides the trivial-length threshold (default 600).
"""

import os
import re

_UTILITY_PATTERNS = re.compile(
    r"(analyze if this message indicates a new conversation topic"
    r"|write a 5-10 word title"
    r"|generate a concise title"
    r"|please write a .{0,20}title for the"
    r"|^quota$"
    r"|respond only with json"
    r"|isNewTopic)",
    re.IGNORECASE,
)


def _extract_text(body: dict) -> str:
    parts: list = []
    for m in body.get("messages", []):
        c = m.get("content")
        if isinstance(c, str):
            parts.append(c)
        elif isinstance(c, list):
            parts.extend(
                b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"
            )
    return "\n".join(parts)


def is_fastpath_request(body: dict) -> str:
    """Return the fast-path reason ('' = no fast path).

    'utility'  — CC-internal side request (topic/title/quota); ALWAYS eligible,
                 these must never occupy the MoE pipeline.
    'trivial'  — no tools and total prompt text below threshold.
    """
    if os.getenv("CC_FASTPATH", "0") != "1":
        return ""
    if body.get("tools"):
        return ""
    text = _extract_text(body)
    if _UTILITY_PATTERNS.search(text):
        return "utility"
    max_chars = int(os.getenv("CC_FASTPATH_MAX_CHARS", "600"))
    if max_chars > 0 and len(text.strip()) <= max_chars and len(body.get("messages", [])) <= 4:
        return "trivial"
    return ""
