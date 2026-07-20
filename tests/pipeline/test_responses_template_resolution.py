import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from services.pipeline.responses import (
    _ResponsesRequest,
    _invoke_pipeline_for_responses,
    _stream_responses_api,
)


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        headers={"authorization": "Bearer moe-sk-test"},
        client=SimpleNamespace(host="127.0.0.1"),
    )


@pytest.mark.asyncio
async def test_responses_passes_owned_template_experts_and_identity_to_graph():
    template = {
        "name": "moe-n04-rtx-qwen3.6:35b-256k",
        "planner_prompt": "owned planner",
        "experts": {
            "coding": {
                "context_window": 262144,
                "models": [{"model": "qwen3.6:35b", "endpoint": "RTX"}],
            }
        },
    }
    user_ctx = {
        "user_id": "user-1",
        "key_id": "key-1",
        "permissions_json": json.dumps({"expert_template": []}),
        "user_templates_json": json.dumps({"owned-template-id": template}),
        "user_connections_json": "{}",
    }
    captured = {}

    async def fake_stream_response(*args, **kwargs):
        captured.update(kwargs)
        yield 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        yield "data: [DONE]\n\n"

    request = _ResponsesRequest(
        model=template["name"],
        input="test",
        stream=True,
    )
    with (
        patch(
            "services.pipeline.responses._validate_api_key",
            new=AsyncMock(return_value=user_ctx),
        ),
        patch(
            "services.pipeline.responses._read_expert_templates",
            return_value=[],
        ),
        patch(
            "services.routing._read_expert_templates",
            return_value=[],
        ),
        patch(
            "services.pipeline.responses._register_active_request",
            new=AsyncMock(),
        ),
        patch("main.stream_response", new=fake_stream_response),
    ):
        text, _, _ = await _invoke_pipeline_for_responses(
            _request(), request, [{"role": "user", "content": "test"}]
        )

    assert text == "ok"
    assert captured["user_permissions"] == {"expert_template": []}
    assert captured["user_experts"]["coding"][0]["model"] == "qwen3.6:35b"
    assert captured["planner_prompt"] == "owned planner"


@pytest.mark.asyncio
async def test_stream_emits_response_failed_instead_of_disconnect():
    request = _ResponsesRequest(model="template", input="test", stream=True)
    with patch(
        "services.pipeline.responses._invoke_pipeline_for_responses",
        new=AsyncMock(side_effect=RuntimeError("backend unavailable")),
    ):
        chunks = [
            chunk async for chunk in _stream_responses_api(
                _request(), request, "resp_test"
            )
        ]

    assert any("event: response.failed" in chunk for chunk in chunks)
    assert any('"status": "failed"' in chunk for chunk in chunks)


@pytest.mark.asyncio
async def test_stream_emits_protocol_progress_heartbeat_for_slow_pipeline():
    request = _ResponsesRequest(model="template", input="test", stream=True)

    async def slow_pipeline(*args, **kwargs):
        await asyncio.sleep(0.02)
        return "ok", 1, 1

    original_wait_for = asyncio.wait_for
    async def short_wait_for(awaitable, timeout):
        return await original_wait_for(awaitable, timeout=0.005)

    with (
        patch(
            "services.pipeline.responses._invoke_pipeline_for_responses",
            new=slow_pipeline,
        ),
        patch(
            "services.pipeline.responses.asyncio.wait_for",
            new=short_wait_for,
        ),
    ):
        chunks = [
            chunk async for chunk in _stream_responses_api(
                _request(), request, "resp_test"
            )
        ]

    progress_events = [
        chunk for chunk in chunks if "event: response.in_progress" in chunk
    ]
    assert len(progress_events) >= 2
