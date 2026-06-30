"""
services/cascade.py — Typed Cascade Events for MoE-Sovereign agentic re-planning.

Upgrades the binary COMPLETE/NEEDS_MORE_INFO gap detector to typed cascade events
with specific re-plan strategies per failure mode. Each failure type carries a
routing hint so the planner knows *why* it is re-planning, not just *that* it should.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class CascadeType(str, Enum):
    CONTEXT_GAP    = "CONTEXT_GAP"     # Missing retrieval context — different GraphRAG / web query needed
    EXPERT_FAILURE = "EXPERT_FAILURE"  # Expert returned capability disclaimer or empty
    CONTRADICTION  = "CONTRADICTION"   # Paraconsistent conflict between experts, unresolved
    SCOPE_DRIFT    = "SCOPE_DRIFT"     # Answer drifted from original request scope
    TOOL_FAILURE   = "TOOL_FAILURE"    # MCP tool call failed or returned empty
    COMPLETE       = "COMPLETE"        # No cascade — answer is sufficient


@dataclass
class CascadeEvent:
    cascade_type: CascadeType
    message: str          # Original gap description (passed through to planner prompt unchanged)
    replan_strategy: str  # Concrete hint injected into re-planner prompt


# Regex patterns for classifying gap text into cascade types. Order matters.
_EXPERT_LEAK_RE = re.compile(
    r"\b(cannot access|can'?t access|can'?t browse|no web|no internet|"
    r"unable to (browse|fetch|access)|don'?t have (web|internet|real.?time)|"
    r"capability disclaimer|attempt.{0,10}search)\b",
    re.I,
)
_TOOL_FAILURE_RE = re.compile(
    r"\b(mcp tool|tool (call|failure|failed|error|timeout)|"
    r"precision tool|tool not available)\b",
    re.I,
)
_CONTRADICTION_RE = re.compile(
    r"\b(conflict|contradict|inconsisten|disagree|divergen|"
    r"paraconsistent|unresolved)\b",
    re.I,
)
_SCOPE_DRIFT_RE = re.compile(
    r"\b(off.?topic|scope (drift|creep)|unrelated|changed (the )?subject|"
    r"didn'?t answer|not relevant to)\b",
    re.I,
)


def classify_gap(gap_text: str, strategy_hint: str = "") -> CascadeEvent:
    """Convert a gap description string into a typed CascadeEvent.

    Preserves the original gap_text as message so existing planner prompts
    remain unchanged. Only the cascade_type and replan_strategy are new.
    """
    if not gap_text or gap_text.upper() in ("COMPLETE", "NONE", ""):
        return CascadeEvent(CascadeType.COMPLETE, "", "")

    if _EXPERT_LEAK_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.EXPERT_FAILURE,
            gap_text,
            strategy_hint or "use web_researcher or fetch_pdf_text to retrieve the missing data directly",
        )

    if _TOOL_FAILURE_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.TOOL_FAILURE,
            gap_text,
            strategy_hint or "retry with a different MCP tool or fall back to web_search",
        )

    if _CONTRADICTION_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.CONTRADICTION,
            gap_text,
            strategy_hint or "use a single authoritative source to arbitrate the conflicting claims",
        )

    if _SCOPE_DRIFT_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.SCOPE_DRIFT,
            gap_text,
            strategy_hint or "re-focus on the original request; discard tangential results",
        )

    return CascadeEvent(
        CascadeType.CONTEXT_GAP,
        gap_text,
        strategy_hint or "try a more specific search query or use GraphRAG with different entities",
    )
