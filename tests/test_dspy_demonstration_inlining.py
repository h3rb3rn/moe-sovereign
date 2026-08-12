"""Unit test for DSPy Dynamic Demonstration Inlining and Z3 Verification Filtering."""

import pytest
import asyncio
from self_correction import save_few_shot, get_few_shot_context


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
