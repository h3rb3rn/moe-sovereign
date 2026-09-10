"""tests/test_context_budget_ctx_override.py — get_model_ctx_async(override=...)
must win over live auto-detection, not just over total detection failure.

Regression for a design gap: services/pipeline/anthropic.py's call site never
passed the CC profile's explicit context_window as `override`, so it was only
consulted when auto-detection returned exactly 0 (total failure) — an
explicit admin/profile value was silently ignored whenever detection
"succeeded" with a different (possibly wrong) value. get_model_ctx_async
itself already had override-priority semantics (`if override > 0: return
override`); this test locks in that contract so a future caller-side
regression is caught even though this file doesn't touch the caller.
"""

from unittest.mock import AsyncMock, patch

import pytest

from context_budget import get_model_ctx_async


@pytest.mark.asyncio
async def test_override_wins_without_any_lookup():
    # No redis_client/base_url given at all — if override didn't short-circuit
    # first, this would hit network/DB code paths and likely raise or hang.
    result = await get_model_ctx_async("some-model", override=262_144)
    assert result == 262_144


@pytest.mark.asyncio
async def test_override_wins_over_a_different_cached_value():
    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value="32768")  # would win without override
    result = await get_model_ctx_async(
        "some-model", base_url="https://api.example.com/v1",
        redis_client=redis_client, override=262_144,
    )
    assert result == 262_144
    redis_client.get.assert_not_called()  # short-circuits before the cache lookup


@pytest.mark.asyncio
async def test_override_wins_over_a_different_live_fetch_result():
    with patch("context_budget.fetch_openai_context_window", AsyncMock(return_value=32_768)):
        result = await get_model_ctx_async(
            "some-model", base_url="https://api.example.com/v1", token="sk-x",
            override=262_144,
        )
    assert result == 262_144


@pytest.mark.asyncio
async def test_zero_override_falls_through_to_auto_detect():
    with patch("context_budget.fetch_openai_context_window", AsyncMock(return_value=262_144)):
        result = await get_model_ctx_async(
            "some-model", base_url="https://api.example.com/v1", token="sk-x",
            override=0,
        )
    assert result == 262_144
