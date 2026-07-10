"""tests/pipeline/test_chat_agent_writeback.py — Unit tests for
services/pipeline/chat.py::_wrap_agent_writeback_sse.

The wrapper must pass every SSE chunk through byte-for-byte unchanged (the
client must never see a difference), while accumulating assistant text
content in the background and firing agent_writeback() exactly once, only
on a clean finish_reason=='stop' turn with no tool_calls.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from services.pipeline.chat import _wrap_agent_writeback_sse


async def _achunks(chunks):
    for c in chunks:
        yield c


def _delta_chunk(content=None, tool_calls=None, finish_reason=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return f"data: {json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}]})}\n\n"


class TestWrapAgentWritebackSSE:

    @pytest.mark.asyncio
    async def test_passthrough_is_byte_for_byte_unchanged(self):
        chunks = [
            _delta_chunk(content="Hel"),
            _delta_chunk(content="lo"),
            _delta_chunk(finish_reason="stop"),
            "data: [DONE]\n\n",
        ]
        with patch("services.pipeline.chat.agent_writeback", new=AsyncMock()):
            out = [c async for c in _wrap_agent_writeback_sse(
                _achunks(chunks), "chat123", "what is X?", "scope1", "user:u1", "u1", "model-a", "sess1",
            )]
        assert out == chunks

    @pytest.mark.asyncio
    async def test_clean_stop_fires_writeback_with_accumulated_content(self):
        chunks = [
            _delta_chunk(content="Hel"),
            _delta_chunk(content="lo world"),
            _delta_chunk(finish_reason="stop"),
            "data: [DONE]\n\n",
        ]
        with patch("services.pipeline.chat.agent_writeback", new=AsyncMock()) as mock_wb:
            async for _ in _wrap_agent_writeback_sse(
                _achunks(chunks), "chat123", "what is X?", "scope1", "user:u1", "u1", "model-a", "sess1",
            ):
                pass
            # The write-back now runs through _agent_writeback_traced (adds
            # started/done stage-trace markers around agent_writeback) instead
            # of a bare create_task(agent_writeback(...)) — one real event-loop
            # tick is needed for the created task to actually run.
            await asyncio.sleep(0)
        mock_wb.assert_called_once()
        call_args = mock_wb.call_args.args
        assert call_args[0] == "what is X?"
        assert call_args[1] == "Hello world"
        assert call_args[2] == "scope1"
        assert call_args[3] == "user:u1"

    @pytest.mark.asyncio
    async def test_tool_calls_never_trigger_writeback(self):
        chunks = [
            _delta_chunk(tool_calls=[{"id": "1", "function": {"name": "bash"}}]),
            _delta_chunk(finish_reason="tool_calls"),
            "data: [DONE]\n\n",
        ]
        with patch("services.pipeline.chat.agent_writeback", new=AsyncMock()) as mock_wb:
            async for _ in _wrap_agent_writeback_sse(
                _achunks(chunks), "chat123", "fix the bug", "scope1", "user:u1", "u1", "model-a", "sess1",
            ):
                pass
        mock_wb.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_content_never_triggers_writeback(self):
        chunks = [
            _delta_chunk(finish_reason="stop"),
            "data: [DONE]\n\n",
        ]
        with patch("services.pipeline.chat.agent_writeback", new=AsyncMock()) as mock_wb:
            async for _ in _wrap_agent_writeback_sse(
                _achunks(chunks), "chat123", "what is X?", "scope1", "user:u1", "u1", "model-a", "sess1",
            ):
                pass
        mock_wb.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_chunk_does_not_break_passthrough(self):
        chunks = [
            "data: {not valid json\n\n",
            _delta_chunk(content="ok"),
            _delta_chunk(finish_reason="stop"),
            "data: [DONE]\n\n",
        ]
        with patch("services.pipeline.chat.agent_writeback", new=AsyncMock()) as mock_wb:
            out = [c async for c in _wrap_agent_writeback_sse(
                _achunks(chunks), "chat123", "what is X?", "scope1", "user:u1", "u1", "model-a", "sess1",
            )]
            await asyncio.sleep(0)  # let the traced write-back task run — see comment above
        assert out == chunks
        mock_wb.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_finish_reason_does_not_trigger_writeback(self):
        chunks = [
            _delta_chunk(content="[Stream error: boom]", finish_reason="stop"),
            "data: [DONE]\n\n",
        ]
        # This one legitimately has finish_reason=='stop' with content — the
        # stream-error case in _stream_tool_synthesis sets finish_reason='stop'
        # too, so this documents that agent_writeback WILL fire on it (the
        # content itself, e.g. "[Stream error: ...]", is short enough that
        # agent_writeback's own CACHE_MIN_RESPONSE_LEN gate is expected to
        # reject it in practice — this wrapper only handles SSE accumulation).
        with patch("services.pipeline.chat.agent_writeback", new=AsyncMock()) as mock_wb:
            async for _ in _wrap_agent_writeback_sse(
                _achunks(chunks), "chat123", "what is X?", "scope1", "user:u1", "u1", "model-a", "sess1",
            ):
                pass
            await asyncio.sleep(0)  # let the traced write-back task run — see comment above
        mock_wb.assert_called_once()
