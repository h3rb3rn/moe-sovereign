"""tests/pipeline/test_chat_deregister_on_stream_end.py — Unit tests for
services/pipeline/chat.py::_wrap_deregister_on_stream_end.

Regression test for a pre-existing gap: the tool-calling passthrough branch
in chat_completions() registers every request via _register_active_request
but had no matching deregister call on any return path, leaving entries in
the "Laufende API-Anfragen" admin monitoring table until their 2h Redis TTL
expired — even though the response had already been fully delivered.

The wrapper must (a) pass every chunk through unchanged, (b) deregister
exactly once after the stream ends normally, and (c) also deregister on
early close (client disconnect), since Starlette calls aclose() on the
body iterator in that case.
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.pipeline.chat import _wrap_deregister_on_stream_end


async def _achunks(chunks):
    for c in chunks:
        yield c


class TestWrapDeregisterOnStreamEnd:

    @pytest.mark.asyncio
    async def test_passthrough_is_unchanged(self):
        chunks = ["data: a\n\n", "data: b\n\n", "data: [DONE]\n\n"]
        with patch("services.pipeline.chat._deregister_active_request", new=AsyncMock()):
            out = [c async for c in _wrap_deregister_on_stream_end(_achunks(chunks), "chat-1")]
        assert out == chunks

    @pytest.mark.asyncio
    async def test_deregisters_exactly_once_after_normal_completion(self):
        chunks = ["data: a\n\n", "data: [DONE]\n\n"]
        with patch("services.pipeline.chat._deregister_active_request", new=AsyncMock()) as mock_dereg:
            async for _ in _wrap_deregister_on_stream_end(_achunks(chunks), "chat-1"):
                pass
            import asyncio
            await asyncio.sleep(0)  # let the fire-and-forget create_task run
        mock_dereg.assert_called_once_with("chat-1")

    @pytest.mark.asyncio
    async def test_deregisters_on_early_close(self):
        """Simulates a client disconnect: the consumer stops iterating and
        calls aclose() before the generator is exhausted."""
        chunks = ["data: a\n\n", "data: b\n\n", "data: c\n\n"]
        with patch("services.pipeline.chat._deregister_active_request", new=AsyncMock()) as mock_dereg:
            gen = _wrap_deregister_on_stream_end(_achunks(chunks), "chat-2")
            await gen.__anext__()  # consume only the first chunk
            await gen.aclose()
            import asyncio
            await asyncio.sleep(0)
        mock_dereg.assert_called_once_with("chat-2")

    @pytest.mark.asyncio
    async def test_no_chunks_still_deregisters(self):
        with patch("services.pipeline.chat._deregister_active_request", new=AsyncMock()) as mock_dereg:
            async for _ in _wrap_deregister_on_stream_end(_achunks([]), "chat-3"):
                pass
            import asyncio
            await asyncio.sleep(0)
        mock_dereg.assert_called_once_with("chat-3")
