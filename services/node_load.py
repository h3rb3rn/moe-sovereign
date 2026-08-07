"""
services/node_load.py — In-process in-flight counters per inference node.

Lightweight load signal for routing decisions: every LLM call increments the
node's counter for its duration. When two expert candidates have the same
tier, the one on the less-loaded node is preferred.

Flag consumers check MOE_LOAD_AWARE_ROUTING=1 themselves.
"""

import threading
from collections import defaultdict
from contextlib import asynccontextmanager

_lock = threading.Lock()
_inflight: dict = defaultdict(int)


@asynccontextmanager
async def track(node: str):
    """Context manager: marks one in-flight request on `node`."""
    node = node or "unknown"
    with _lock:
        _inflight[node] += 1
    try:
        yield
    finally:
        with _lock:
            _inflight[node] -= 1


def inflight(node: str) -> int:
    with _lock:
        return _inflight.get(node or "unknown", 0)
