"""Unit test for DSPy Dynamic Demonstration Inlining and Z3 Verification Filtering."""

import pytest
import asyncio
from self_correction import save_few_shot, get_few_shot_context, _is_topically_relevant


@pytest.mark.asyncio
async def test_unverified_few_shots_rejected():
    """Verify that unverified few-shot entries (verified_by_z3=False) are rejected."""
    await save_few_shot(
        category="math_test",
        query="2 + 2",
        wrong_output="5",
        correction="4",
        mismatches=[{"original": {"value": 2, "unit": ""}, "expert": {"value": 5, "unit": ""}, "rel_diff": 0.5}],
        verified_by_z3=False
    )
    
    context = await get_few_shot_context("math_test")
    assert "Expected: 2" not in context


@pytest.mark.asyncio
async def test_verified_few_shots_accepted():
    """Verify that Z3-verified few-shot entries are saved and context generated."""
    await save_few_shot(
        category="math_verified_test",
        query="Compute 10 * 10",
        wrong_output="105",
        correction="100",
        mismatches=[{"original": {"value": 100, "unit": ""}, "expert": {"value": 105, "unit": ""}, "rel_diff": 0.05}],
        verified_by_z3=True
    )
    
    context = await get_few_shot_context("math_verified_test")
    assert len(context) > 0
    assert "Compute 10 * 10" in context


def test_is_topically_relevant_requires_real_overlap():
    assert _is_topically_relevant(
        "Calculate the tariff escalation for datacenter energy costs",
        "Calculate the annual energy costs and tariff escalation",
    ) is True
    assert _is_topically_relevant(
        "Calculate the tariff escalation for datacenter energy costs",
        "Persistent Architecture Registration: Cluster 'Apex-Central' runs the following topology",
    ) is False
    assert _is_topically_relevant("", "anything") is False
    assert _is_topically_relevant("anything", "") is False


@pytest.mark.asyncio
async def test_few_shot_context_filters_unrelated_topic_contamination():
    """A stored wrong-output from an unrelated prior request must not surface
    as an in-context example for a topically unrelated new request -- this is
    the exact mechanism observed live causing complete plan topic replacement
    during the scientific benchmark (see agent_status/claude-code.md,
    FIX-few-shot-context-topic-contamination)."""
    await save_few_shot(
        category="contamination_test",
        query="Persistent Architecture Registration: Cluster 'Apex-Central' runs the following topology with services and ports",
        wrong_output="Datacenter PUE and PCE compliance audit checking thermal design power",
        correction="The relationship topology for cluster Apex-Central has been registered",
        mismatches=[],
        verified_by_z3=True,
    )

    unrelated_context = await get_few_shot_context(
        "contamination_test",
        query="Calculate the total electricity cost for a datacenter given tariff escalation percentages",
    )
    assert "Apex-Central" not in unrelated_context

    related_context = await get_few_shot_context(
        "contamination_test",
        query="Persistent Architecture Registration for the Apex-Central cluster topology",
    )
    assert "Apex-Central" in related_context

    # Backward-compatible default: no query means no filtering (existing callers).
    unfiltered_context = await get_few_shot_context("contamination_test")
    assert "Apex-Central" in unfiltered_context
