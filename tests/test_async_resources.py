"""Timeout-bound sync integrations must not retain executor workers."""

import asyncio
import time

import pytest

from services.async_utils import run_sync_daemon


@pytest.mark.asyncio
async def test_daemon_sync_timeout_returns_without_waiting_for_worker():
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await run_sync_daemon(time.sleep, 0.5, timeout=0.01)
    assert time.monotonic() - started < 0.2
