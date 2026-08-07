"""Monotonic end-to-end request deadline helpers.

Every expensive stage derives its local timeout from the same absolute
monotonic deadline. Local retries and fallbacks therefore consume one shared
budget instead of multiplying independent timeout windows.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Mapping, TypeVar

T = TypeVar("T")


class RequestDeadlineExceeded(asyncio.TimeoutError):
    """Raised when a stage cannot start or finish within the request budget."""


def remaining_timeout(
    state: Mapping[str, Any] | None,
    cap_seconds: float,
    *,
    stage: str = "pipeline",
    reserve_seconds: float = 0.05,
) -> float:
    """Return the smaller of the stage cap and remaining request budget."""
    cap = max(0.001, float(cap_seconds))
    if not state:
        return cap

    raw_deadline = state.get("request_deadline_monotonic")
    if raw_deadline in (None, ""):
        return cap
    try:
        deadline = float(raw_deadline)
    except (TypeError, ValueError) as exc:
        raise RequestDeadlineExceeded(
            f"{stage}: invalid request_deadline_monotonic"
        ) from exc

    remaining = deadline - time.monotonic() - max(0.0, reserve_seconds)
    if remaining <= 0:
        raise RequestDeadlineExceeded(
            f"{stage}: end-to-end request deadline exhausted"
        )
    return max(0.001, min(cap, remaining))


def bounded_output_tokens(
    state: Mapping[str, Any] | None,
    configured_limit: int,
    *,
    minimum_internal: int = 128,
) -> int:
    """Bound an internal generation by the caller's positive output budget."""
    configured = max(1, int(configured_limit))
    if not state:
        return configured
    raw_client_limit = state.get("client_max_output_tokens")
    try:
        client_limit = int(raw_client_limit or 0)
    except (TypeError, ValueError):
        client_limit = 0
    if client_limit <= 0:
        return configured
    return min(configured, max(int(minimum_internal), client_limit))


async def wait_for_budget(
    awaitable: Awaitable[T],
    state: Mapping[str, Any] | None,
    cap_seconds: float,
    *,
    stage: str,
) -> T:
    """Await work within the stage cap and the shared absolute deadline."""
    try:
        timeout = remaining_timeout(state, cap_seconds, stage=stage)
    except Exception:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RequestDeadlineExceeded(
            f"{stage}: deadline exceeded after {timeout:.3f}s"
        ) from exc


async def sleep_with_budget(
    delay_seconds: float,
    state: Mapping[str, Any] | None,
    *,
    stage: str,
) -> None:
    """Sleep only when the complete delay still fits in the request budget."""
    delay = max(0.0, float(delay_seconds))
    allowed = remaining_timeout(
        state,
        max(delay, 0.001),
        stage=stage,
        reserve_seconds=0.05,
    )
    if allowed + 1e-6 < delay:
        raise RequestDeadlineExceeded(
            f"{stage}: retry delay {delay:.3f}s exceeds remaining budget"
        )
    await asyncio.sleep(delay)
