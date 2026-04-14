"""
test_routing.py — Unit tests for the deterministic hardware-routing logic in main.py.

Covers:
  - assign_gpu(): round-robin GPU index assignment within an endpoint
  - _resolve_user_experts(): expert template → category→model-config mapping
  - _select_node(): warm/cold preference and load-score-based node selection

No live connections are required. HTTP calls inside _select_node are mocked via
unittest.mock.AsyncMock.
"""

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# conftest.py has already stubbed all heavy deps and set env vars,
# so importing main is safe at collection time.
import main


# ── Helpers ───────────────────────────────────────────────────────────────────


def _reset_gpu_counter(endpoint: str, value: int = 0) -> None:
    """Reset the round-robin counter for an endpoint to a known value."""
    main._endpoint_gpu_indices[endpoint] = value


def _clear_ps_cache() -> None:
    """Discard all cached Ollama /api/ps results so tests are independent."""
    main._ps_cache.clear()


def _make_ps_response(model_names: list) -> MagicMock:
    """Build a mock httpx response whose .json() returns an Ollama /api/ps payload."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"models": [{"name": n} for n in model_names]}
    return resp


# ── assign_gpu tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_gpu_round_robin():
    """Indices should cycle 0→1→2→3→0→1→2→3 for a 4-GPU endpoint."""
    _reset_gpu_counter("RTX", 0)
    results = [await main.assign_gpu("RTX") for _ in range(8)]
    assert results == [0, 1, 2, 3, 0, 1, 2, 3]


@pytest.mark.asyncio
async def test_assign_gpu_single_gpu():
    """A single-GPU endpoint always returns index 0."""
    # Temporarily add a 1-GPU server to INFERENCE_SERVERS_LIST.
    single_srv = {"name": "SINGLE", "url": "http://single:11434", "gpu_count": 1}
    main.INFERENCE_SERVERS_LIST.append(single_srv)
    _reset_gpu_counter("SINGLE", 0)
    try:
        results = [await main.assign_gpu("SINGLE") for _ in range(4)]
        assert results == [0, 0, 0, 0]
    finally:
        main.INFERENCE_SERVERS_LIST.remove(single_srv)
        main._endpoint_gpu_indices.pop("SINGLE", None)


@pytest.mark.asyncio
async def test_assign_gpu_unknown_endpoint_defaults_to_index_zero():
    """An endpoint not in INFERENCE_SERVERS_LIST still returns a valid index."""
    idx = await main.assign_gpu("GHOST")
    assert isinstance(idx, int)
    assert idx >= 0


# ── _resolve_user_experts tests ───────────────────────────────────────────────


def test_resolve_user_experts_primary_tier(fake_template, fake_perms_with_template):
    """The 'primary' role maps to _tier=1; 'fallback' maps to _tier=2."""
    with patch.object(main, "_read_expert_templates", return_value=fake_template):
        result = main._resolve_user_experts(fake_perms_with_template)

    assert result is not None
    research_models = result["research"]
    primary   = next(m for m in research_models if m["model"] == "llama2")
    fallback  = next(m for m in research_models if m["model"] == "mistral")

    assert primary["_tier"]  == 1
    assert primary["forced"] is False
    assert fallback["_tier"] == 2
    assert fallback["forced"] is False


def test_resolve_user_experts_always_role(fake_template, fake_perms_with_template):
    """The 'always' role sets forced=True and has no _tier key."""
    with patch.object(main, "_read_expert_templates", return_value=fake_template):
        result = main._resolve_user_experts(fake_perms_with_template)

    assert result is not None
    coding_models = result["coding"]
    always_model = next(m for m in coding_models if m["model"] == "deepseek")

    assert always_model["forced"] is True
    assert "_tier" not in always_model


def test_resolve_user_experts_no_template_returns_none(fake_perms_no_template):
    """When no template is assigned, the function returns None (global EXPERTS used)."""
    with patch.object(main, "_read_expert_templates", return_value=[]):
        result = main._resolve_user_experts(fake_perms_no_template)

    assert result is None


def test_resolve_user_experts_missing_template_id_returns_none(fake_template):
    """A permissions string referencing a non-existent template ID → None."""
    perms = json.dumps({"expert_template": ["nonexistent-id"]})
    with patch.object(main, "_read_expert_templates", return_value=fake_template):
        result = main._resolve_user_experts(perms)

    assert result is None


def test_resolve_user_experts_invalid_json_returns_none():
    """Malformed permissions JSON must not raise; it must return None."""
    result = main._resolve_user_experts("{not valid json!!!")
    assert result is None


# ── _select_node tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_select_node_prefers_warm_model():
    """When RTX already has llama2 loaded, it is preferred over cold TESLA."""
    _clear_ps_cache()
    rtx_response   = _make_ps_response(["llama2"])
    tesla_response = _make_ps_response([])          # TESLA is openai → always cold anyway

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    with patch("main.httpx.AsyncClient", return_value=mock_client):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] == "RTX"


@pytest.mark.asyncio
async def test_select_node_lowest_load_wins_when_all_cold():
    """When all servers are cold, the one with the lowest load score wins.

    RTX has 2 running models on 4 GPUs (score 0.5).
    TESLA is openai (always cold, 0 models, 2 GPUs → score 0.0).
    Expected winner: TESLA.
    """
    _clear_ps_cache()
    # RTX reports 2 loaded models (not llama2 → cold for our query)
    rtx_response = _make_ps_response(["phi4:14b", "magistral:24b"])

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value  = None
    mock_client.get.return_value        = rtx_response

    with patch("main.httpx.AsyncClient", return_value=mock_client):
        chosen = await main._select_node("llama2", ["RTX", "TESLA"])

    assert chosen["name"] == "TESLA"


@pytest.mark.asyncio
async def test_select_node_single_candidate_skips_scoring():
    """With only one allowed endpoint, it is returned directly without HTTP calls."""
    _clear_ps_cache()
    with patch("main.httpx.AsyncClient") as mock_cls:
        chosen = await main._select_node("llama2", ["RTX"])

    # No HTTP call should have been made.
    mock_cls.assert_not_called()
    assert chosen["name"] == "RTX"


@pytest.mark.asyncio
async def test_select_node_unknown_endpoint_returns_fallback():
    """An endpoint not in INFERENCE_SERVERS_LIST returns a dict, not an exception."""
    _clear_ps_cache()
    result = await main._select_node("llama2", ["UNKNOWN_GPU"])
    assert isinstance(result, dict)
    assert result.get("name") == "UNKNOWN_GPU"
