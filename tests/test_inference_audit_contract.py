"""The central LangChain invocation path must always close its audit entry."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.inference import _audited_ainvoke, ainvoke_guard_llm
from services.pipeline import chat as chat_pipeline


@pytest.mark.asyncio
async def test_audited_ainvoke_completes_success_with_tokens():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(
        content="answer",
        usage_metadata={"input_tokens": 12, "output_tokens": 4},
    ))
    entry = SimpleNamespace(audit_id="audit-1")
    with (
        patch("services.inference._audit_create", return_value=entry) as create,
        patch("services.inference._audit_complete", new=AsyncMock()) as complete,
    ):
        result = await _audited_ainvoke(
            llm,
            "prompt",
            endpoint="http://model",
            model="m",
            stage="planner",
            context={"session_id": "s", "response_id": "r"},
        )

    assert result.content == "answer"
    assert create.call_args.args[4] == "planner"
    complete.assert_awaited_once_with(
        entry, {"content": "answer"}, 12, 4
    )


@pytest.mark.asyncio
async def test_audited_ainvoke_completes_error_before_reraising():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=TimeoutError("provider timeout"))
    entry = SimpleNamespace(audit_id="audit-2")
    with (
        patch("services.inference._audit_create", return_value=entry),
        patch("services.inference._audit_complete", new=AsyncMock()) as complete,
        pytest.raises(TimeoutError),
    ):
        await _audited_ainvoke(
            llm,
            "prompt",
            endpoint="http://model",
            model="m",
            stage="expert",
        )

    complete.assert_awaited_once_with(
        entry,
        {"error": "provider timeout"},
        None,
        None,
        "error",
    )


@pytest.mark.asyncio
async def test_audited_ainvoke_closes_entry_when_cancelled():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=asyncio.CancelledError)
    entry = SimpleNamespace(audit_id="audit-cancelled")
    with (
        patch("services.inference._audit_create", return_value=entry),
        patch("services.inference._audit_complete", new=AsyncMock()) as complete,
        pytest.raises(asyncio.CancelledError),
    ):
        await _audited_ainvoke(
            llm,
            "prompt",
            endpoint="http://model",
            model="m",
            stage="expert",
        )

    complete.assert_awaited_once_with(
        entry,
        {"error": "cancelled"},
        None,
        None,
        "error",
    )


@pytest.mark.asyncio
async def test_guard_call_is_audited_and_fails_open_on_provider_error():
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(side_effect=TimeoutError("guard timeout"))
    entry = SimpleNamespace(audit_id="audit-guard")

    with (
        patch("services.inference._audit_create", return_value=entry) as create,
        patch("services.inference._audit_complete", new=AsyncMock()) as complete,
        patch("services.inference.httpx.AsyncClient", return_value=client),
        patch("config.GUARD_WARM_ONLY", False),
    ):
        result = await ainvoke_guard_llm(
            "input",
            guard_model="llama-guard",
            guard_url="http://guard.example/v1",
            session_id="session-1",
            request_id="request-1",
        )

    assert result == (False, "")
    assert create.call_args.args[:5] == (
        "session-1",
        "request-1",
        "llama-guard",
        "http://guard.example/api/chat",
        "guard",
    )
    complete.assert_awaited_once_with(
        entry,
        {"error": "guard timeout"},
        None,
        None,
        "error",
    )


def test_native_nonstream_path_has_audit_and_latency_contract():
    source = open(chat_pipeline.__file__, encoding="utf-8").read()
    assert '"native_direct"' in source
    assert "_native_latency_ms" in source
    assert "latency_ms=_native_latency_ms" in source
