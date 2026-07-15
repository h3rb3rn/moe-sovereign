"""
services/cynefin.py — Cynefin Complexity Classification (TASK-15).

Deterministic (no LLM) classification of requests into Cynefin domains,
which governs the autonomy level and whether HITL-Gate is required.

Mapping (priority order):
  CHAOTIC     — Trust-Score BLOCK (no grounded answer possible)
  COMPLEX     — complexity_level == "complex" OR >2 expert domains
  COMPLICATED — complexity_level == "moderate" OR ≤2 domains, GraphRAG active
  CLEAR       — trivial / single-domain / short input
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger("MOE-SOVEREIGN")

_COMPLEX_DOMAIN_THRESHOLD = 2   # More than this many distinct expert domains → COMPLEX
_CHAOTIC_INPUT_LEN        = 0   # Reserved for future use (very long / adversarial input)


class CynefinDomain(str, Enum):
    CLEAR       = "CLEAR"
    COMPLICATED = "COMPLICATED"
    COMPLEX     = "COMPLEX"
    CHAOTIC     = "CHAOTIC"


def classify_cynefin(state_: dict) -> CynefinDomain:
    """Classify the current request into a Cynefin domain.

    Pure function — reads AgentState fields only, emits no side-effects.
    """
    trust_verdict    = (state_.get("trust_verdict") or "").upper()
    complexity_level = (state_.get("complexity_level") or "trivial").lower()
    plan             = state_.get("plan") or []
    enable_graphrag  = bool(state_.get("enable_graphrag"))
    user_input       = state_.get("input") or ""

    # CHAOTIC: no grounded answer possible (hard-blocked by Trust-Score)
    if trust_verdict == "BLOCK":
        logger.debug("Cynefin: CHAOTIC (Trust-Score BLOCK)")
        return CynefinDomain.CHAOTIC

    # Count distinct expert domains in the plan
    expert_domains = {
        t.get("category", "general")
        for t in plan
        if isinstance(t, dict) and t.get("category") not in (None, "", "precision_tools", "research")
    }
    domain_count = len(expert_domains)

    # COMPLEX: explicit complex level OR many expert domains
    if complexity_level == "complex" or domain_count > _COMPLEX_DOMAIN_THRESHOLD:
        logger.debug("Cynefin: COMPLEX (complexity=%s, domains=%d)", complexity_level, domain_count)
        return CynefinDomain.COMPLEX

    # COMPLICATED: moderate complexity, or graph knowledge active with any plan content
    has_plan_content = len(plan) > 0
    if complexity_level == "moderate" or (enable_graphrag and has_plan_content):
        logger.debug("Cynefin: COMPLICATED (complexity=%s, graphrag=%s)", complexity_level, enable_graphrag)
        return CynefinDomain.COMPLICATED

    # CLEAR: trivial, single-domain, or direct answer
    logger.debug("Cynefin: CLEAR (complexity=%s, domains=%d)", complexity_level, domain_count)
    return CynefinDomain.CLEAR
