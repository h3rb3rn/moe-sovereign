"""Conservative eligibility gates for planner-free trivial requests.

The gate is shared by the API preflight and the graph planner so both layers
make the same decision.  It intentionally favours false negatives: bypassing
the planner is only safe for context-free, non-operational one-shot prompts.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from prompts import (
    _DATA_DETECT,
    _FILE_DETECT,
    _LEGAL_DETECT,
    _RESEARCH_DETECT,
)


MATH_SIGNAL_PATTERN = re.compile(
    r'\b(berechne?|berechnung|integral|ableitung|differentialgleichung|löse?|solve|'
    r'calculate|calculation|subnet|cidr|bgp|ospf|hash|checksum|statistics|statistik|'
    r'wie viel|how much|how many|wie viele|convert|umrechnen|prozent|percent|'
    r'gcd|ggt|wochentag|weekday|day of (?:the )?week)\b',
    re.I,
)
CURRENT_INFO_PATTERN = re.compile(
    r'\b(aktuell(?:e[snm]?)?|heute|jetzt|neueste[snm]?|stand\s+\d|'
    r'current|currently|today|latest|recent|news|weather|wetter|'
    r'preis(?:e)?|price(?:s)?|kurs(?:e)?|release|version|präsident|president|'
    r'geschäftsführer|ceo)\b',
    re.I,
)
EXACT_OPERATION_PATTERN = re.compile(
    r'(?:\d[\d.,]*\s*[-+*/^%=]\s*\d)|(?:§+\s*\d+)|'
    r'(?:\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b)',
    re.I,
)


def is_trivial_fast_path_eligible(
    request_state: Mapping[str, Any],
    complexity: str,
) -> bool:
    """Return whether a request can safely bypass planning/template compilation."""
    # ``moe-auto`` enters the graph as mode "auto".  Once its API preflight
    # deliberately skipped dynamic template compilation, that mode is the same
    # context-free execution contract as "default".  All specialized modes
    # (research, plan, agent, code, …) must retain their full planning path.
    if (
        complexity != "trivial"
        or request_state.get("mode", "default") not in ("default", "auto")
    ):
        return False
    if request_state.get("chat_history") or request_state.get("images"):
        return False
    if any(
        request_state.get(key)
        for key in (
            "files",
            "attachments",
            "system_prompt",
            "tools",
            "user_experts",
            "agentic_iteration",
        )
    ):
        return False
    query = (request_state.get("input") or "").strip()
    if not query:
        return False
    blocked_patterns = (
        MATH_SIGNAL_PATTERN,
        _RESEARCH_DETECT,
        _LEGAL_DETECT,
        _DATA_DETECT,
        _FILE_DETECT,
        CURRENT_INFO_PATTERN,
        EXACT_OPERATION_PATTERN,
    )
    return not any(pattern.search(query) for pattern in blocked_patterns)


def is_moe_auto_preflight_eligible(
    query: str,
    complexity: str,
    *,
    mode: str,
    has_history: bool,
    has_multimodal: bool,
    system_prompt: str,
    tools: Any = None,
    files: Any = None,
) -> bool:
    """Adapt an API request to the graph-level eligibility contract."""
    return is_trivial_fast_path_eligible(
        {
            "input": query,
            "mode": mode,
            "chat_history": ["present"] if has_history else [],
            "images": ["present"] if has_multimodal else [],
            "system_prompt": system_prompt,
            "tools": tools,
            "files": files,
        },
        complexity,
    )
