"""Tests for services/trust_score.py (TASK-10)."""
import pytest
from services.trust_score import (
    TrustVerdict, compute_trust_score,
    _extract_checkable_claims, _unsupported_claim_ratio,
)


def _state(**kwargs):
    base = {
        "expert_results": [],
        "plan": [],
        "graph_context": "",
        "web_research": "",
        "conflict_registry": [],
        "judge_before_after": {},
    }
    base.update(kwargs)
    return base


def test_hard_block_when_no_content():
    ts = compute_trust_score(_state())
    assert ts.hard_blocked is True
    assert ts.verdict == TrustVerdict.BLOCK
    assert ts.score == 0.0


def test_proceed_with_good_experts_and_context():
    # 5 expert results (max), 5 tasks covered, 10 web sources, rich graph context
    ts = compute_trust_score(_state(
        expert_results=["[RESEARCH / llama3]: GraphQL is a query language with many features and advantages."] * 5,
        plan=[{"task": f"t{i}", "category": "research"} for i in range(5)],
        web_research="\n".join(f"https://site{i}.com — reference" for i in range(10)),
        graph_context=" ".join(f"[NEO4J:entity_{i}]" for i in range(10)),
    ))
    assert ts.hard_blocked is False
    assert ts.verdict == TrustVerdict.PROCEED
    assert ts.score >= 0.65


def test_proceed_with_assumption_borderline():
    ts = compute_trust_score(_state(
        expert_results=["[RESEARCH / llama3]: Some answer with minimal detail."],
        plan=[{"task": "t1"}, {"task": "t2"}],  # 2 tasks, 1 expert → 50% coverage
        web_research="",
        graph_context="",
    ))
    assert ts.hard_blocked is False
    assert ts.verdict in (TrustVerdict.PROCEED_WITH_ASSUMPTION, TrustVerdict.BLOCK)


def test_conflict_reduces_score():
    no_conflict = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Good long expert answer with details."] * 2,
        plan=[{"task": "t1"}],
        web_research="https://a.com",
        graph_context="entity: X",
    ))
    with_conflict = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Good long expert answer with details."] * 2,
        plan=[{"task": "t1"}],
        web_research="https://a.com",
        graph_context="entity: X",
        conflict_registry=[{"category": "research", "a": "x", "b": "y"}] * 3,
    ))
    assert with_conflict.score < no_conflict.score


def test_factors_keys_present():
    ts = compute_trust_score(_state(
        expert_results=["[CODE / m]: def foo(): pass"],
        plan=[{"task": "t"}],
        web_research="https://docs.python.org",
    ))
    assert "source_count" in ts.factors
    assert "expert_count" in ts.factors
    assert "conflict_penalty" in ts.factors
    assert "cross_reference_coverage" in ts.factors


def test_score_clipped_to_zero_one():
    ts = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: " + "x" * 100] * 10,
        plan=[{"task": f"t{i}"} for i in range(10)],
        web_research=" ".join(f"https://site{i}.com" for i in range(20)),
        graph_context=" ".join(f"[NEO4J:entity_{i}]" for i in range(20)),
    ))
    assert 0.0 <= ts.score <= 1.0


# ── Hallucination proxy: unsupported_claims_penalty ──────────────────────────

def test_extract_checkable_claims_finds_numbers_and_proper_nouns():
    claims = _extract_checkable_claims(
        "Construction of the Eiffel Tower finished in 1889, reaching 330 meters."
    )
    assert "330" in claims
    assert "1889" in claims
    assert any("Eiffel Tower" in c for c in claims)


def test_extract_checkable_claims_empty_for_no_text():
    assert _extract_checkable_claims("") == set()
    assert _extract_checkable_claims("the quick brown fox") == set()  # no caps/numbers


def test_unsupported_claim_ratio_zero_when_no_source():
    # No source material to check against → can't penalise, ratio is 0.0
    assert _unsupported_claim_ratio("The Eiffel Tower is 330 meters tall.", "") == 0.0


def test_unsupported_claim_ratio_zero_when_claims_covered():
    ratio = _unsupported_claim_ratio(
        "The Eiffel Tower is 330 meters tall.",
        "Source: The Eiffel Tower stands 330 meters tall in Paris.",
    )
    assert ratio == 0.0


def test_unsupported_claim_ratio_high_when_claims_absent():
    ratio = _unsupported_claim_ratio(
        "The Eiffel Tower is 330 meters tall.",
        "Source: unrelated content about database indexing strategies.",
    )
    assert ratio == 1.0


def test_unsupported_claims_penalty_factor_present():
    ts = compute_trust_score(_state(
        expert_results=["[CODE / m]: def foo(): pass"],
        plan=[{"task": "t"}],
        web_research="https://docs.python.org",
    ))
    assert "unsupported_claims_penalty" in ts.factors


def test_unsupported_claims_lower_score_than_grounded_claims():
    # Same expert-count/source-count/coverage shape, only the claim-vs-source
    # overlap differs — isolates the new factor's effect on the final score.
    grounded = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Berlin has 3800000 residents."],
        plan=[{"task": "t1"}],
        web_research="https://stats.example.com",
        graph_context="Berlin population figures: 3800000 residents recorded.",
    ))
    ungrounded = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Berlin has 3800000 residents."],
        plan=[{"task": "t1"}],
        web_research="https://stats.example.com",
        graph_context="unrelated content about database indexing strategies.",
    ))
    assert ungrounded.factors["unsupported_claims_penalty"] > grounded.factors["unsupported_claims_penalty"]
    assert ungrounded.score < grounded.score
