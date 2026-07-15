"""tests/pipeline/test_chat_wrap_completion_as_sse.py — Unit tests for
services/pipeline/chat.py::_wrap_completion_as_sse.

Factored out of the former inline `_sse_wrap` closure in _handle_tool_calls
so the Augmented Tool Path cache-hit response (services/agent_enrichment.py)
can reuse the exact same, already-client-tested chunk sequence. These tests
lock in that the extraction did not change behaviour, and that it produces a
valid OpenAI-compatible chunk sequence for a synthetic cache-hit completion.
"""

import json

import pytest

from services.pipeline.chat import _wrap_completion_as_sse


def _parse_chunks(lines):
    """Parse a list of raw SSE lines into (is_done, payload_or_None) pairs."""
    parsed = []
    for line in lines:
        assert line.endswith("\n\n")
        payload = line[len("data: "):-2]
        if payload == "[DONE]":
            parsed.append(("done", None))
        else:
            parsed.append(("data", json.loads(payload)))
    return parsed


class TestWrapCompletionAsSSE:

    @pytest.mark.asyncio
    async def test_plain_text_completion_chunk_sequence(self):
        completion = {
            "id": "chatcmpl-x", "object": "chat.completion",
            "created": 1000, "model": "model-a",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "Hello world"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        chunks = [c async for c in _wrap_completion_as_sse(completion, "chatcmpl-x", "model-a")]
        parsed = _parse_chunks(chunks)

        # Opening role delta first
        assert parsed[0][0] == "data"
        assert parsed[0][1]["choices"][0]["delta"] == {"role": "assistant", "content": ""}

        # Content delta carries the full text
        content_chunk = next(p for _, p in parsed if p and p["choices"] and
                              p["choices"][0]["delta"].get("content") == "Hello world")
        assert content_chunk is not None

        # Finish chunk carries finish_reason
        finish_chunk = next(p for _, p in parsed
                             if p and p["choices"] and p["choices"][0].get("finish_reason") == "stop")
        assert finish_chunk is not None

        # Usage chunk (separate, choices=[])
        usage_chunk = next(p for _, p in parsed if p and p.get("choices") == [] and "usage" in p)
        assert usage_chunk["usage"]["total_tokens"] == 8

        # Terminal [DONE]
        assert parsed[-1] == ("done", None)

    @pytest.mark.asyncio
    async def test_tool_calls_completion_emits_tool_calls_delta(self):
        completion = {
            "id": "chatcmpl-x", "object": "chat.completion",
            "created": 1000, "model": "model-a",
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": "",
                                     "tool_calls": [{"id": "call_1", "type": "function",
                                                      "function": {"name": "bash", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        chunks = [c async for c in _wrap_completion_as_sse(completion, "chatcmpl-x", "model-a")]
        parsed = _parse_chunks(chunks)

        tc_chunk = next(p for _, p in parsed if p and p["choices"] and
                         p["choices"][0]["delta"].get("tool_calls"))
        assert tc_chunk["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "bash"

        finish_chunk = next(p for _, p in parsed
                             if p and p["choices"] and p["choices"][0].get("finish_reason") == "tool_calls")
        assert finish_chunk is not None

    @pytest.mark.asyncio
    async def test_cache_hit_synthetic_completion_shape(self):
        """The exact shape the Augmented Tool Path cache-hit path constructs
        (services/pipeline/chat.py, agent_cache lookup branch)."""
        cached_answer = "This project uses a LangGraph orchestrator."
        completion = {
            "id": "chatcmpl-hit", "object": "chat.completion",
            "created": 1000, "model": "moe-orchestrator-agent",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": cached_answer}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": len(cached_answer) // 4,
                      "total_tokens": len(cached_answer) // 4},
        }
        chunks = [c async for c in _wrap_completion_as_sse(completion, "chatcmpl-hit", "moe-orchestrator-agent")]
        parsed = _parse_chunks(chunks)
        content_chunk = next(p for _, p in parsed if p and p["choices"] and
                              p["choices"][0]["delta"].get("content") == cached_answer)
        assert content_chunk is not None
        assert parsed[-1] == ("done", None)
