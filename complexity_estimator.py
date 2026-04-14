"""
Complexity Estimator — heuristic query routing without an LLM call.

Routing rules:
  trivial   → 1 subtask, tier-1 model, no research/GraphRAG
  moderate  → standard MoE, no thinking node
  complex   → full stack (GraphRAG + web + thinking node)

Heuristics (no LLM, no network):
  1. Token count of the request
  2. Multi-step markers (keywords)
  3. Domain markers (law, medicine, math)
  4. Code/config markers
  5. Simple factual questions / single-word queries
"""

from __future__ import annotations
import re
from typing import Literal

ComplexityLevel = Literal["trivial", "moderate", "complex"]

# ── Thresholds ───────────────────────────────────────────────────────────────
_TRIVIAL_TOKEN_MAX  = 15   # queries with ≤15 words → trivial candidate
_COMPLEX_TOKEN_MIN  = 80   # queries with ≥80 words → always complex

# ── Multi-step markers → complex ─────────────────────────────────────────────
_COMPLEX_MARKERS = re.compile(
    r'\b(vergleiche?n?|analysiere?n?|erkläre? warum|untersuche?n?|bewerte?n?|evaluiere?n?|'
    r'entwirf|entwickle?n?|plane?n?|implementiere?n?|refaktoriere?n?|optimiere?n?|'
    r'unterschied|vor- und nachteile?|pros? and cons?|step[- ]by[- ]step|'
    r'schritt für schritt|warum|wie genau|inwiefern|welche auswirkungen|'
    r'compare|analyze|explain why|evaluate|design|implement|optimize)\b',
    re.I,
)

# ── Domain markers → at least moderate ───────────────────────────────────────
_DOMAIN_MARKERS = re.compile(
    r'\b(§+\s*\d+|bgh|bverfg|awmf|s3-leitlinie?|icd-\d+|dosierung|wirkstoff|'
    r'differentialdiagnose?|subnetz|cidr|bgp|ospf|ldap|oauth|openid|'
    r'integral|ableitung|differentialgleichung|eigenwert|fourier|'
    r'sql|cypher|neo4j|docker|kubernetes|terraform|ansible)\b',
    re.I,
)

# ── Code/config markers → moderate ───────────────────────────────────────────
_CODE_MARKERS = re.compile(
    r'```|`[^`]+`|\bdef \b|\bclass \b|\bfunction\b|\bimport \b|'
    r'\{["\']|\[\s*\{|<[a-z]+>|#!/',
    re.I,
)

# ── Trivial markers: factual questions / definitions ─────────────────────────
_TRIVIAL_MARKERS = re.compile(
    r'^(was ist|what is|wer ist|who is|wann ist|when is|wo ist|where is|'
    r'wie viel|how much|wie viele|how many|nenne|list|zeige mir|show me|'
    r'übersetze?|translate)\b',
    re.I,
)


def estimate_complexity(query: str) -> ComplexityLevel:
    """Returns the estimated complexity of a query without an LLM call.

    Args:
        query: The user's request text.

    Returns:
        'trivial' | 'moderate' | 'complex'
    """
    words = query.split()
    n = len(words)

    # Hard length limits decide immediately
    if n >= _COMPLEX_TOKEN_MIN:
        return "complex"

    # Multi-step markers → immediately complex
    if _COMPLEX_MARKERS.search(query):
        return "complex"

    # Short factual questions take priority over domain markers
    # ("What is Docker?" is trivial, not moderate)
    if n <= _TRIVIAL_TOKEN_MAX and _TRIVIAL_MARKERS.search(query):
        return "trivial"

    # Very short queries without domain markers → trivial
    if n <= 8 and not _DOMAIN_MARKERS.search(query) and not _CODE_MARKERS.search(query):
        return "trivial"

    # Domain markers → at least moderate
    has_domain = bool(_DOMAIN_MARKERS.search(query))

    # Code block → moderate
    has_code = bool(_CODE_MARKERS.search(query))

    if has_domain or has_code:
        return "moderate"

    # Default: moderate
    return "moderate"


def complexity_routing_hint(level: ComplexityLevel) -> dict:
    """Returns routing hints for the planner.

    Returns dict with:
      - max_tasks: maximum number of subtasks
      - skip_research: True = no web research node
      - skip_graph: True = no GraphRAG node
      - skip_thinking: True = no thinking node
      - force_tier1: True = use tier-1 models only
    """
    if level == "trivial":
        return {
            "max_tasks":      1,
            "skip_research":  True,
            "skip_graph":     True,
            "skip_thinking":  True,
            "force_tier1":    True,
        }
    elif level == "moderate":
        return {
            "max_tasks":      2,
            "skip_research":  False,
            "skip_graph":     False,
            "skip_thinking":  True,
            "force_tier1":    False,
        }
    else:  # complex
        return {
            "max_tasks":      4,
            "skip_research":  False,
            "skip_graph":     False,
            "skip_thinking":  False,
            "force_tier1":    False,
        }
