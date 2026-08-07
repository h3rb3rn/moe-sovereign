"""Bounded in-process progress snapshots for terminal error telemetry.

LangGraph returns its final state only after a successful invocation. If the
outer request deadline cancels the graph, already computed routing/trust
signals would otherwise be lost. Nodes publish only non-content metadata here;
the HTTP boundary removes it on every terminal path.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_LOCK = threading.Lock()
_SNAPSHOTS: dict[str, dict[str, Any]] = {}
_MAX_AGE_SECONDS = 3600.0


def update_request_snapshot(request_id: str, **fields: Any) -> None:
    """Merge non-empty operational fields into one request snapshot."""
    if not request_id:
        return
    clean = {
        key: value
        for key, value in fields.items()
        if value is not None and value != ""
    }
    if not clean:
        return
    now = time.monotonic()
    with _LOCK:
        stale = [
            key
            for key, value in _SNAPSHOTS.items()
            if now - float(value.get("_updated_at", now)) > _MAX_AGE_SECONDS
        ]
        for key in stale:
            _SNAPSHOTS.pop(key, None)
        snapshot = _SNAPSHOTS.setdefault(request_id, {})
        snapshot.update(clean)
        snapshot["_updated_at"] = now


def consume_request_snapshot(request_id: str) -> dict[str, Any]:
    """Return and delete a request snapshot."""
    if not request_id:
        return {}
    with _LOCK:
        snapshot = dict(_SNAPSHOTS.pop(request_id, {}))
    snapshot.pop("_updated_at", None)
    return snapshot


def clear_request_snapshot(request_id: str) -> None:
    """Delete a snapshot without returning it."""
    if not request_id:
        return
    with _LOCK:
        _SNAPSHOTS.pop(request_id, None)
