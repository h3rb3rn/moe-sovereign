"""
test_expert_refinement.py — Regression test for _refine_expert_response()'s
category-to-expert-config resolution precedence.

Bug context: _refine_expert_response() previously read exclusively from the
global config.EXPERTS mapping, ignoring any active template's per-category
expert config carried in state["user_experts"]. Every other call site that
resolves "which expert config applies to this category" honors
`state.get("user_experts") or EXPERTS` (see graph/expert.py and
services/routing.py._get_template_expert_catalog's docstring). Because the
refinement path deviated from that precedence, a Judge-triggered refinement
round for a category could silently dispatch to whatever endpoint the global
EXPERT_MODELS fallback used for that category — bypassing the template's
explicit endpoint pinning entirely, with no error and no log signal pointing
at the mismatch.

No live network/Redis calls are required: _select_node and _get_expert_score
are patched to observe/control inputs, and _audited_ainvoke is patched to
raise so the (irrelevant, network-dependent) tail of the function short-
circuits via its existing except-and-return-None path.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services import inference as _inference


@pytest.mark.asyncio
async def test_refine_expert_response_prefers_template_endpoint_over_global():
    """A category present in state['user_experts'] must route refinement to
    the template's endpoint, not the global config.EXPERTS endpoint for the
    same category."""
    global_experts = {
        "code_reviewer": [
            {"model": "hf.co/h3rb3rn/moe-expert-coder-4b:latest",
             "endpoints": ["N04-RTX"], "enabled": True, "forced": True},
        ],
    }
    template_experts = {
        "code_reviewer": [
            {"model": "hf.co/h3rb3rn/moe-expert-coder-4b:latest",
             "endpoint": "N04-TM60-02", "url": "http://tm60-02-fake:11434",
             "token": "ollama", "forced": True},
        ],
    }
    state = {"user_experts": template_experts, "input": "task text"}

    captured_eps = {}

    async def _fake_select_node(model_name, allowed_endpoints, **kw):
        captured_eps["endpoints"] = list(allowed_endpoints)
        return {"name": allowed_endpoints[0], "url": "http://tm60-02-fake:11434", "token": "ollama"}

    with patch("config.EXPERTS", global_experts), \
         patch.object(_inference, "_get_expert_score", AsyncMock(return_value=1.0)), \
         patch.object(_inference, "_select_node", AsyncMock(side_effect=_fake_select_node)), \
         patch.object(_inference, "_audited_ainvoke", AsyncMock(side_effect=RuntimeError("no network in unit test"))):
        result = await _inference._refine_expert_response("code_reviewer", "feedback", state)

    assert captured_eps["endpoints"] == ["N04-TM60-02"], (
        f"expected refinement to use the template endpoint N04-TM60-02, "
        f"got {captured_eps['endpoints']} (global EXPERTS endpoint would be N04-RTX)"
    )
    assert result is None  # _audited_ainvoke was forced to raise; graceful None is correct


@pytest.mark.asyncio
async def test_refine_expert_response_falls_back_to_global_when_no_template_category():
    """A category absent from state['user_experts'] (or no template active at
    all) must still fall back to the global config.EXPERTS entry, unchanged
    from the pre-fix behavior."""
    global_experts = {
        "code_reviewer": [
            {"model": "hf.co/h3rb3rn/moe-expert-coder-4b:latest",
             "endpoints": ["N04-RTX"], "enabled": True, "forced": True},
        ],
    }
    state = {"user_experts": {}, "input": "task text"}

    captured_eps = {}

    async def _fake_select_node(model_name, allowed_endpoints, **kw):
        captured_eps["endpoints"] = list(allowed_endpoints)
        return {"name": allowed_endpoints[0], "url": "http://rtx-fake:11434", "token": "ollama"}

    with patch("config.EXPERTS", global_experts), \
         patch.object(_inference, "_get_expert_score", AsyncMock(return_value=1.0)), \
         patch.object(_inference, "_select_node", AsyncMock(side_effect=_fake_select_node)), \
         patch.object(_inference, "_audited_ainvoke", AsyncMock(side_effect=RuntimeError("no network in unit test"))):
        result = await _inference._refine_expert_response("code_reviewer", "feedback", state)

    assert captured_eps["endpoints"] == ["N04-RTX"]
    assert result is None
