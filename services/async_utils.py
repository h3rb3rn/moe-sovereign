"""Small async resource helpers used by timeout-bound sync integrations."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from typing import Any


async def run_sync_daemon(
    function: Callable[..., Any],
    *args: Any,
    timeout: float,
    **kwargs: Any,
) -> Any:
    """Run blocking work in a daemon thread with an async timeout.

    ``asyncio.to_thread`` uses the event loop's default executor. Cancelling
    the await does not cancel the underlying worker, and Python waits for those
    non-daemon executor threads during loop/process shutdown. A stalled search
    or Chroma client therefore made otherwise-passing test runs and container
    shutdown hang. This helper isolates only integrations that cannot accept a
    native timeout; timed-out daemon workers cannot hold shutdown open.
    """
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def resolve(result: Any = None, error: BaseException | None = None) -> None:
        if future.done():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def worker() -> None:
        try:
            result = function(*args, **kwargs)
        except BaseException as exc:
            try:
                loop.call_soon_threadsafe(resolve, None, exc)
            except RuntimeError:
                pass
        else:
            try:
                loop.call_soon_threadsafe(resolve, result, None)
            except RuntimeError:
                pass

    threading.Thread(
        target=worker,
        name=f"moe-bounded-{getattr(function, '__name__', 'sync')}",
        daemon=True,
    ).start()
    return await asyncio.wait_for(future, timeout=timeout)
