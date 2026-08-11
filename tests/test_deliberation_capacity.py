from __future__ import annotations

import pytest

from services.deliberation.capacity import (
    CapacityInputs,
    _dependency_depth,
    plan_deliberation_capacity,
)
from services.deliberation.contracts import (
    DeliberationPolicy,
    DeliberationPolicyError,
    legacy_deliberation_policy,
    parse_deliberation_policy,
)


def _complex_plan() -> list[dict]:
    return [
        {"id": "a", "category": "legal_advisor", "task": "Check the law"},
        {"id": "b", "category": "code_reviewer", "task": "Audit code", "depends_on": ["a"]},
        {"id": "c", "category": "data_analyst", "task": "Validate data", "depends_on": ["b"]},
    ]


def test_policy_rejects_unknown_or_inconsistent_fields():
    with pytest.raises(DeliberationPolicyError, match="unknown"):
        parse_deliberation_policy({"unknown": True})
    with pytest.raises(DeliberationPolicyError, match="initial_agent_cap"):
        parse_deliberation_policy({"min_agents": 4, "initial_agent_cap": 3})


def test_explicit_disabled_policy_wins_over_complexity():
    policy = parse_deliberation_policy({"activation": "disabled"})
    capacity = plan_deliberation_capacity(
        policy,
        CapacityInputs(
            complexity_level="complex",
            cynefin_domain="COMPLEX",
            plan=_complex_plan(),
            remaining_seconds=300,
            available_models=4,
        ),
    )
    assert capacity.active is False
    assert capacity.activation_reason == "template_disabled"


def test_adaptive_policy_skips_clear_single_domain_request():
    policy = DeliberationPolicy(activation="adaptive")
    capacity = plan_deliberation_capacity(
        policy,
        CapacityInputs(
            complexity_level="trivial",
            cynefin_domain="CLEAR",
            plan=[{"id": "a", "category": "general", "task": "Define X"}],
            remaining_seconds=300,
        ),
    )
    assert capacity.active is False
    assert capacity.activation_reason == "complexity_below_threshold"


def test_complex_policy_allocates_agents_rounds_and_separate_reserve():
    policy = DeliberationPolicy(
        activation="adaptive",
        mode="auto",
        max_model_calls=40,
    )
    capacity = plan_deliberation_capacity(
        policy,
        CapacityInputs(
            complexity_level="complex",
            cynefin_domain="COMPLEX",
            plan=_complex_plan(),
            remaining_seconds=1000,
            available_models=3,
        ),
    )
    assert capacity.active is True
    assert capacity.selected_mode == "moderated"
    assert capacity.initial_agents >= 4
    assert capacity.reserve_agents > 0
    assert capacity.initial_rounds >= 3
    assert capacity.reserve_rounds > 0
    assert capacity.max_agents == capacity.initial_agents + capacity.reserve_agents
    assert capacity.max_rounds == capacity.initial_rounds + capacity.reserve_rounds


def test_capacity_shrinks_to_shared_deadline_and_preserves_synthesis_reserve():
    policy = DeliberationPolicy(
        activation="required",
        mode="moderated",
        estimated_turn_seconds=10.0,
        synthesis_reserve_seconds=30.0,
        max_model_calls=40,
    )
    capacity = plan_deliberation_capacity(
        policy,
        CapacityInputs(
            complexity_level="complex",
            cynefin_domain="COMPLEX",
            plan=_complex_plan(),
            remaining_seconds=90,
        ),
    )
    assert capacity.model_call_budget == 6
    assert capacity.budget_limited is True
    assert capacity.active is True
    # Under a tight budget the planner preserves perspective coverage before
    # spending calls on additional rounds.
    assert capacity.initial_agents == 5
    assert capacity.initial_rounds == 1
    assert capacity.reserve_agents == 0
    assert capacity.reserve_rounds == 0


def test_insufficient_budget_deactivates_required_workflow_explicitly():
    policy = DeliberationPolicy(
        activation="required",
        mode="moderated",
        estimated_turn_seconds=20.0,
        synthesis_reserve_seconds=30.0,
    )
    capacity = plan_deliberation_capacity(
        policy,
        CapacityInputs(
            complexity_level="complex",
            cynefin_domain="COMPLEX",
            plan=_complex_plan(),
            remaining_seconds=50,
        ),
    )
    assert capacity.active is False
    assert capacity.activation_reason == "insufficient_budget"
    assert capacity.budget_limited is True


def test_trust_block_never_adds_agents_or_rounds():
    policy = DeliberationPolicy(activation="required", mode="moderated")
    capacity = plan_deliberation_capacity(
        policy,
        CapacityInputs(
            complexity_level="complex",
            cynefin_domain="CHAOTIC",
            trust_verdict="BLOCK",
            plan=_complex_plan(),
            remaining_seconds=500,
        ),
    )
    assert capacity.active is False
    assert capacity.activation_reason == "trust_blocked"


def test_legacy_policy_preserves_three_call_micro_debate():
    enabled = legacy_deliberation_policy(True)
    disabled = legacy_deliberation_policy(False)
    assert enabled.activation == "required"
    assert enabled.mode == "micro"
    assert enabled.max_model_calls == 3
    assert disabled.activation == "disabled"


# ── depends_on resolution (regression: description-prefix form) ──────────────
# The planner's trained system prompt documents depends_on as
# "<prior task description prefix>"; "id" is optional and, measured over 2,000
# real training samples, emitted by only 3% of plans while 15% emit depends_on.
# Resolving ids alone pinned dependency_depth at 1 for virtually every real
# plan and silently disabled the dependency_depth branch of adaptive activation.

def _prefix_plan() -> list[dict]:
    """A chain in the shape the trained planner actually emits: no ids."""
    return [
        {"category": "research", "task": "Recherchiere die Tetris-Regeln"},
        {
            "category": "code_reviewer",
            "task": "Implementiere Tetris in Python",
            "depends_on": ["Recherchiere die Tetris-Regeln"],
        },
        {
            "category": "technical_support",
            "task": "Beschreibe das Deployment",
            "depends_on": ["Implementiere Tetris"],
        },
    ]


def test_dependency_depth_resolves_description_prefixes():
    assert _dependency_depth(_prefix_plan()) == 3


def test_dependency_depth_still_resolves_explicit_ids():
    assert _dependency_depth(_complex_plan()) == 3


def test_dependency_depth_ignores_ambiguous_prefix():
    plan = [
        {"category": "general", "task": "Analysiere den Vertrag Teil A"},
        {"category": "general", "task": "Analysiere den Vertrag Teil B"},
        {
            "category": "legal_advisor",
            "task": "Fasse zusammen",
            "depends_on": ["Analysiere den Vertrag"],  # matches both → no edge
        },
    ]
    assert _dependency_depth(plan) == 1


def test_dependency_depth_ignores_self_and_unresolvable_reference():
    plan = [
        {"category": "general", "task": "Erkläre X", "depends_on": ["Erkläre X"]},
        {"category": "general", "task": "Erkläre Y", "depends_on": ["gibt es nicht"]},
    ]
    assert _dependency_depth(plan) == 1


def test_dependency_depth_survives_cycles():
    plan = [
        {"id": "a", "category": "general", "task": "A", "depends_on": ["b"]},
        {"id": "b", "category": "general", "task": "B", "depends_on": ["a"]},
    ]
    assert _dependency_depth(plan) >= 1  # terminates, no recursion error


def test_prefix_dependency_chain_scales_deliberation_rounds():
    """The real behavioural effect of the fix: depth scales rounds.

    dependency_depth >= 2 is redundant inside the adaptive-activation OR chain
    (any plan deep enough to reach 2 already satisfies task_count >= 2). Where
    the value genuinely decides something is round scaling: with depth pinned to
    1 by id-only resolution, a three-stage dependent plan deliberated for as
    many rounds as a flat one.
    """
    capacity = plan_deliberation_capacity(
        DeliberationPolicy(activation="required", mode="moderated"),
        CapacityInputs(
            complexity_level="moderate",
            cynefin_domain="COMPLICATED",
            plan=_prefix_plan(),  # depth 3, description-prefix deps, no ids
            remaining_seconds=600,
            available_models=4,
        ),
    )
    assert capacity.dependency_depth == 3
    assert capacity.active is True
    assert capacity.initial_rounds == 3  # was 2 while depth was pinned to 1
