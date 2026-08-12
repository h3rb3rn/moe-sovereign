"""Unit tests for the Outlines-Style Pre-Indexed Token Masking Engine."""

import pytest
import numpy as np
from services.gbnf_masking import TokenMaskEngine


def test_precompile_and_apply_bitwise_mask():
    """Verify bitwise AND logit filtering sets disallowed tokens to -inf."""
    engine = TokenMaskEngine(vocab_size=100)
    allowed_tokens = {5, 10, 20}
    engine.precompile_rule_mask("json_start", allowed_tokens)
    
    logits = np.zeros(100)
    filtered = engine.apply_bitwise_mask(logits, "json_start")
    
    assert filtered[5] == 0.0
    assert filtered[10] == 0.0
    assert filtered[20] == 0.0
    assert filtered[0] == -np.inf
    assert filtered[1] == -np.inf


def test_missing_rule_fallback():
    """Verify uncompiled rule returns original un-altered logits."""
    engine = TokenMaskEngine(vocab_size=100)
    logits = np.zeros(100)
    filtered = engine.apply_bitwise_mask(logits, "non_existent_rule")
    assert np.array_equal(logits, filtered)
