"""
services/tool_turn_router.py — Route synthesis turns to a stronger expert.

V1 scope: only turns that carry tool_result blocks (synthesis phase) are
re-routed to the template's 'code' or 'general' expert. Fresh instruction
turns stay on the tool_agent — its function-calling reliability is the
priority there.

Flag: CC_TOOL_EXPERT_ROUTING=1 (default off).
"""

import os


def pick_synthesis_expert(messages: list, experts: dict, current_model: str) -> dict | None:
    """Return an expert dict {model,url,token,endpoint,context_window} or None."""
    if os.getenv("CC_TOOL_EXPERT_ROUTING", "0") != "1":
        return None
    has_tool_results = any(
        isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m.get("content", []))
        for m in messages
    )
    if not has_tool_results:
        return None
    for cat in ("code", "general"):
        for e in (experts or {}).get(cat) or []:
            if e.get("model") and e.get("url") and e.get("model") != current_model:
                return e
    return None
