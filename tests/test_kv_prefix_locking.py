"""Unit test for vLLM / Ollama Static Template KV-Locking (Pinned Prefix Cache)."""

import pytest
from services.inference import _planner_model_kw


def test_planner_model_kw_default():
    """Verify default planner kwargs does not set keep_alive to -1 unless requested."""
    state_data = {"pin_prefix_cache": False}
    kw = _planner_model_kw("qwen:3b", state_=state_data)
    opts = kw["extra_body"]["options"]
    assert "keep_alive" not in opts


def test_planner_model_kw_prefix_locked():
    """Verify setting pin_prefix_cache or template_prefix_locked adds keep_alive=-1."""
    state_data = {"pin_prefix_cache": True}
    kw = _planner_model_kw("qwen:3b", state_=state_data)
    opts = kw["extra_body"]["options"]
    assert opts["keep_alive"] == -1
