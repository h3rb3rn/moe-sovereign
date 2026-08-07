"""
pipeline/logic_types.py — Formal logic state types for MoE Sovereign.

Mathematical foundation (see class docstrings for precise attribution):

  Paraconsistent Logic  — de Vries (2007), Section 2: paraconsistent systems tolerate
                          contradictions without collapsing to trivial (ex contradictione
                          quodlibet is rejected).

References:
  A. de Vries, "Algebraic hierarchy of logics unifying fuzzy logic and quantum logic",
  arXiv:0707.2161 [math.LO], 2007. https://arxiv.org/abs/0707.2161
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

class ConflictEntry(BaseModel):
    """A single entry in the paraconsistent conflict registry.

    Paraconsistent Logic (de Vries 2007, arXiv:0707.2161, Section 2) rejects
    the principle of explosion (ex contradictione quodlibet): from a
    contradiction A ∧ ¬A, not every proposition follows. Instead,
    contradictions are tolerated and recorded for explicit resolution.

    This model captures two mutually exclusive propositions from two agents
    within the same domain category, without discarding either.

    Fields:
        category         — Expert domain where the contradiction was detected.
        proposition_a    — First expert's output (truncated to 600 chars).
        proposition_b    — Second expert's output (truncated to 600 chars).
        divergence_score — Text divergence in [0.0, 1.0]; 0=identical, 1=fully
                           different. Computed via SequenceMatcher ratio.
        resolution       — Lifecycle status: 'pending' | 'resolved' | 'dismissed'.
        resolved_by      — Which node or method resolved the conflict, if any.
    """

    category: str
    proposition_a: str
    proposition_b: str
    divergence_score: float = Field(ge=0.0, le=1.0)
    resolution: Literal["pending", "resolved", "dismissed"] = "pending"
    resolved_by: str = ""


# ── Fuzzy Logic t-norms ────────────────────────────────────────────────────────

def goedel_tnorm(a: float, b: float) -> float:
    """Gödel t-norm: T_G(a, b) = min(a, b).

    The most conservative conjunction in fuzzy logic: the combined truth value
    is bounded by the weaker of the two inputs. Any degree of uncertainty in
    either signal caps the conjunction.

    Mathematical foundation:
        Gödel (1932), as discussed in de Vries (2007), arXiv:0707.2161, §4.
        T_G is the largest t-norm; it corresponds to Gödel's many-valued logic
        where implication is defined as: a → b = 1 if a ≤ b, else b.

    Use for routing when BOTH signals must be strong (conservative gate).

    Args:
        a: First confidence score in [0.0, 1.0].
        b: Second confidence score in [0.0, 1.0].

    Returns:
        Float in [0.0, 1.0].
    """
    return min(max(0.0, a), max(0.0, b))


def lukasiewicz_tnorm(a: float, b: float) -> float:
    """Łukasiewicz t-norm: T_Ł(a, b) = max(0, a + b − 1).

    A tolerant conjunction: two partial signals can combine to cross the
    threshold even if neither alone would. Only clips at zero when the
    combined uncertainty exceeds total confidence.

    Mathematical foundation:
        Łukasiewicz (1920), as discussed in de Vries (2007), arXiv:0707.2161,
        §4. Corresponds to the MV-algebra structure where negation is
        n(a) = 1 − a and conjunction is the bold intersection.

    Use for routing when partial evidence from either signal should suffice.

    Args:
        a: First confidence score in [0.0, 1.0].
        b: Second confidence score in [0.0, 1.0].

    Returns:
        Float in [0.0, 1.0].
    """
    return max(0.0, a + b - 1.0)
