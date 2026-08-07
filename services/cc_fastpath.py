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


def _latest_user_text(body: dict) -> str:
    """Return text from the current user turn, excluding conversation history."""
    for message in reversed(body.get("messages", [])):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        return ""
    return ""


def requires_precision_pipeline(body: dict) -> bool:
    """Whether this plain-text turn must use the evidence-bound MoE graph.

    Native/tool profiles remain authoritative for actual client tool turns.
    A plain-text deterministic request, however, must not be able to bypass
    the precision contract merely because a Claude-Code profile is ``native``.
    """
    if body.get("tools"):
        return False
    for message in body.get("messages", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for block in content
        ):
            return False

    from services.pipeline.contracts import detect_required_precision_intents

    return bool(detect_required_precision_intents(_latest_user_text(body)))


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
    # A short precision request is not a generic convenience prompt. Routing
    # it to the direct tool-model path would bypass the mandatory contract,
    # typed MCP evidence and final binding used by the other API facades.
    if requires_precision_pipeline(body):
        return ""
    if _UTILITY_PATTERNS.search(text):
        return "utility"
    max_chars = int(os.getenv("CC_FASTPATH_MAX_CHARS", "600"))
    if max_chars > 0 and len(text.strip()) <= max_chars and len(body.get("messages", [])) <= 4:
        return "trivial"
    return ""
