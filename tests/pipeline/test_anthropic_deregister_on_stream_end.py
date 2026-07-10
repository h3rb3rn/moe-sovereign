"""tests/pipeline/test_anthropic_deregister_on_stream_end.py — Unit tests for
services/pipeline/anthropic.py::_wrap_deregister_on_stream_end.

Belt-and-suspenders safety net: _live_ollama_sse / _live_openai_sse already
call _deregister_active_request on each known exit path, but this wrapper
guarantees deregistration fires regardless — including on an exit path that
turns out to skip the explicit call (observed live: Claude Code sessions
with tool_choice='required' occasionally left a `moe:active:{chat_id}` entry
registered past turn completion). Mirrors
tests/pipeline/test_chat_deregister_on_stream_end.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.pipeline.anthropic import _wrap_deregister_on_stream_end


async def _achunks(chunks):
    for c in chunks:
        yield c


class TestWrapDeregisterOnStreamEnd:

    @pytest.mark.asyncio
    async def test_passthrough_is_unchanged(self):
        chunks = ["event: message_start\ndata: {}\n\n", "event: message_stop\ndata: {}\n\n"]
        with patch("services.pipeline.anthropic._deregister_active_request", new=AsyncMock()):
            out = [c async for c in _wrap_deregister_on_stream_end(_achunks(chunks), "msg-1")]
        assert out == chunks

    @pytest.mark.asyncio
    async def test_deregisters_exactly_once_after_normal_completion(self):
        chunks = ["event: message_start\ndata: {}\n\n"]
        with patch("services.pipeline.anthropic._deregister_active_request", new=AsyncMock()) as mock_dereg:
            async for _ in _wrap_deregister_on_stream_end(_achunks(chunks), "msg-1"):
                pass
            import asyncio
            await asyncio.sleep(0)
        mock_dereg.assert_called_once_with("msg-1")

    @pytest.mark.asyncio
    async def test_deregisters_on_early_close(self):
        """Simulates a client disconnect mid-stream (aclose() before exhaustion)."""
        chunks = ["a", "b", "c"]
        with patch("services.pipeline.anthropic._deregister_active_request", new=AsyncMock()) as mock_dereg:
            gen = _wrap_deregister_on_stream_end(_achunks(chunks), "msg-2")
            await gen.__anext__()
            await gen.aclose()
            import asyncio
            await asyncio.sleep(0)
        mock_dereg.assert_called_once_with("msg-2")

    @pytest.mark.asyncio
    async def test_idempotent_with_an_earlier_explicit_deregister_call(self):
        """The wrapper still fires even when an inner code path already called
        _deregister_active_request explicitly — deregistration must tolerate
        being invoked twice for the same chat_id without error."""
        chunks = ["a"]
        with patch("services.pipeline.anthropic._deregister_active_request", new=AsyncMock()) as mock_dereg:
            async for _ in _wrap_deregister_on_stream_end(_achunks(chunks), "msg-3"):
                await mock_dereg("msg-3")  # simulate the inner explicit call
            import asyncio
            await asyncio.sleep(0)
        assert mock_dereg.call_count == 2
