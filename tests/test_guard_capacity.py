"""Capacity and observability contracts for the shared-endpoint guard."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.inference import GuardDecision, ainvoke_guard_decision


def _response(payload: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _client(*, loaded: list[dict] | None = None, chat: dict | None = None):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=_response({"models": loaded or []}))
    client.post = AsyncMock(return_value=_response(chat or {}))
    return client


@pytest.mark.asyncio
async def test_cold_guard_fails_open_without_loading_model():
    client = _client(loaded=[{"name": "qwen3.6:35b"}])
    entry = SimpleNamespace(audit_id="guard-cold")

    with (
        patch("services.inference.httpx.AsyncClient", return_value=client),
        patch("services.inference._audit_create", return_value=entry),
        patch("services.inference._audit_complete", new=AsyncMock()) as complete,
        patch("config.GUARD_WARM_ONLY", True),
    ):
        decision = await ainvoke_guard_decision(
            "hello",
            guard_model="llama-guard3:8b",
            guard_url="http://guard.example/v1",
        )

    assert decision == GuardDecision(False, status="fail_open_not_warm")
    client.get.assert_awaited_once()
    client.post.assert_not_awaited()
    complete.assert_awaited_once_with(
        entry,
        {"error": "guard_model_not_warm", "fail_open": True},
        None,
        None,
        "error",
    )


@pytest.mark.asyncio
async def test_warm_guard_still_blocks_unsafe_input():
    client = _client(
        loaded=[{"name": "llama-guard3:8b"}],
        chat={
            "message": {"content": "unsafe\nS9"},
            "prompt_eval_count": 9,
            "eval_count": 2,
        },
    )
    entry = SimpleNamespace(audit_id="guard-warm")

    with (
        patch("services.inference.httpx.AsyncClient", return_value=client),
        patch("services.inference._audit_create", return_value=entry),
        patch("services.inference._audit_complete", new=AsyncMock()) as complete,
        patch("config.GUARD_WARM_ONLY", True),
    ):
        decision = await ainvoke_guard_decision(
            "unsafe input",
            guard_model="llama-guard3:8b",
            guard_url="http://guard.example/v1",
        )

    assert decision == GuardDecision(True, "S9", "unsafe")
    client.post.assert_awaited_once()
    complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_guard_probe_error_is_explicit_fail_open():
    client = _client()
    client.get = AsyncMock(side_effect=TimeoutError("probe timeout"))
    entry = SimpleNamespace(audit_id="guard-probe-error")

    with (
        patch("services.inference.httpx.AsyncClient", return_value=client),
        patch("services.inference._audit_create", return_value=entry),
        patch("services.inference._audit_complete", new=AsyncMock()) as complete,
        patch("config.GUARD_WARM_ONLY", True),
    ):
        decision = await ainvoke_guard_decision(
            "hello",
            guard_model="llama-guard3:8b",
            guard_url="http://guard.example/v1",
        )

    assert decision.status == "fail_open_error"
    client.post.assert_not_awaited()
    complete.assert_awaited_once_with(
        entry,
        {"error": "probe timeout"},
        None,
        None,
        "error",
    )


@pytest.mark.asyncio
async def test_guard_cancellation_is_audited_and_propagated():
    client = _client(loaded=[{"name": "llama-guard3:8b"}])
    client.post = AsyncMock(side_effect=asyncio.CancelledError)
    entry = SimpleNamespace(audit_id="guard-cancel")

    with (
        patch("services.inference.httpx.AsyncClient", return_value=client),
        patch("services.inference._audit_create", return_value=entry),
        patch("services.inference._audit_cancel", new=AsyncMock()) as cancel,
        patch("config.GUARD_WARM_ONLY", True),
        pytest.raises(asyncio.CancelledError),
    ):
        await ainvoke_guard_decision(
            "hello",
            guard_model="llama-guard3:8b",
            guard_url="http://guard.example/v1",
        )

    cancel.assert_awaited_once_with(entry)
