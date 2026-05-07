"""
pipeline/logic_types.py — Formal logic state types for MoE Sovereign.

Mathematical foundations (see docstrings per class for precise attribution):

  Intuitionistic Logic  — de Vries (2007), Section 3: Heyting algebra structure.
                          A claim is only valid when a constructive proof is provided;
                          truth is not assumed by default.

  Paraconsistent Logic  — de Vries (2007), Section 2: paraconsistent systems tolerate
                          contradictions without collapsing to trivial (ex contradictione
                          quodlibet is rejected).

References:
  A. de Vries, "Algebraic hierarchy of logics unifying fuzzy logic and quantum logic",
  arXiv:0707.2161 [math.LO], 2007. https://arxiv.org/abs/0707.2161
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ConstructiveProof(BaseModel, Generic[T]):
    """Wrapper that enforces the intuitionistic validity condition on any claim.

    Intuitionistic Logic requires that a proposition be accompanied by a
    constructive proof to be considered true; mere assertion is insufficient.
    This is captured in Heyting algebra semantics where 'p → q' holds only
    when there is an explicit construction mapping proofs of p to proofs of q.

    Mathematical foundation:
        de Vries (2007), arXiv:0707.2161, Section 3 — Heyting algebras as the
        algebraic model of intuitionistic logic. A formula ϕ is valid iff it has
        a proof object; the default truth value without a proof is ⊥ (bottom).

    Fields:
        content       — The claim, generated code, policy, or technical output.
        is_proven     — True only when a constructive execution has verified the
                        content (e.g. sandbox run, unit test pass). Defaults to
                        False: LLM-generated content is always unproven until
                        verified by an executor node.
        proof_method  — Human-readable description of how the proof was obtained.
                        'unverified' means no proof exists yet.
    """

    content: T
    is_proven: bool = False
    proof_method: Literal["unverified", "sandbox_exec", "unit_test", "static_analysis"] = "unverified"

    model_config = {"arbitrary_types_allowed": True}

    def assert_proven(self) -> "ConstructiveProof[T]":
        """Return this instance only if is_proven; raise otherwise.

        Use at decision boundaries where unproven claims must not proceed.
        """
        if not self.is_proven:
            raise ValueError(
                f"Intuitionistic validity violation: content has not been "
                f"constructively proven (proof_method='{self.proof_method}'). "
                f"An executor node must set is_proven=True before this claim "
                f"can be used as a verified fact."
            )
        return self


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
