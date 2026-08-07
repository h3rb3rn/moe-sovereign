"""Adaptive context allocation must not load 262k for short calls."""

from context_budget import adaptive_context_window


def test_short_prompt_uses_16k_tier_under_large_template_ceiling():
    assert adaptive_context_window(262_144, "short request", 1200) == 16_384


def test_medium_prompt_uses_smallest_fitting_tier():
    prompt = "x" * 70_000
    assert adaptive_context_window(262_144, prompt, 1200) == 32_768


def test_context_never_exceeds_template_ceiling():
    assert adaptive_context_window(8_192, "short", 1200) == 8_192
