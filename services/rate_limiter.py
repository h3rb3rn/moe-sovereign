"""services/rate_limiter.py — Proactive, self-imposed outbound rate limiting.

Paces requests toward an inference provider *before* they are sent, instead
of reacting to a 429/timeout after the provider has already rejected the
call. A hard 429 mid-stream is what breaks an interactive coding-agent
session (Claude Code CLI, Codex CLI, ...) — an internally queued call that
simply arrives a little later never does.

This is a second, proactive line of defense in front of the existing
reactive handling in services/helpers.py (_update_rate_limit_headers,
_check_rate_limit_exhausted) and services/inference.py's 429/retry_after
back-off — those still apply for limits this layer does not know about yet
(e.g. a provider quota tightened without updating admin config).

A single endpoint/connection can carry more than one simultaneous limit
(e.g. OpenRouter free models: 20 requests/minute AND 1000 requests/24h) — a
call waits for whichever constraint is currently tightest.

Config: admin UI → Inference Servers → per-endpoint "N requests per <amount>
<unit>" (config.RATE_LIMITS, keyed by endpoint name and URL — see
config.rate_limit_to_rps / config.rate_limits_to_list). An endpoint absent
from that map is unlimited — throttle() is then a no-op unless the caller
passes an explicit `limit` override (used for a user-owned connection's own
rate limit(s)).
"""

import asyncio
import logging
import time
from typing import Dict, List, Mapping, Optional, Tuple

from config import RATE_LIMITS, URL_MAP
from services.deadline import sleep_with_budget

logger = logging.getLogger("MOE-SOVEREIGN")


def _normalize_url(url: str) -> str:
    return (url or "").rstrip("/").removesuffix("/v1")


# Resolve both by configured endpoint "name" and by its URL, so callers can
# throttle() with whichever identifier they already have on hand (graph/
# inference call sites mostly carry a URL, the CC tool-proxy path carries the
# endpoint name). Each value is a list of one or more (rps, burst) limits.
_RATE_LIMITS_BY_KEY: Dict[str, List[Tuple[float, float]]] = dict(RATE_LIMITS)
for _name, _url in URL_MAP.items():
    if _name in RATE_LIMITS:
        _RATE_LIMITS_BY_KEY[_normalize_url(_url)] = RATE_LIMITS[_name]


class _TokenBucket:
    """Async token bucket: `capacity` burst, refilling at `rps` tokens/second."""

    __slots__ = ("rps", "capacity", "_tokens", "_updated_at")

    def __init__(self, rps: float, capacity: float) -> None:
        self.rps = rps
        self.capacity = capacity
        self._tokens = capacity
        self._updated_at = time.monotonic()

    def _refill(self, now: float) -> None:
        elapsed = now - self._updated_at
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rps)
            self._updated_at = now


class _TokenBucketGroup:
    """One or more token buckets that must ALL have a token before a call
    proceeds — e.g. 20/minute AND 1000/24h simultaneously. A call waits for
    whichever bucket is currently tightest, then consumes one token from
    every bucket atomically (all-or-nothing under one lock, so a bucket
    already available never gets "spent" while another is still waited on)."""

    __slots__ = ("_buckets", "_lock")

    def __init__(self, limits: List[Tuple[float, float]]) -> None:
        self._buckets = [_TokenBucket(rps, capacity) for rps, capacity in limits]
        self._lock = asyncio.Lock()

    def _has_capacity(self) -> bool:
        return self._buckets[0]._tokens >= 1.0 if len(self._buckets) == 1 else all(
            b._tokens >= 1.0 for b in self._buckets
        )

    async def acquire(self, state: Optional[Mapping] = None, *, stage: str) -> None:
        """Block until every configured limit has a free slot, then consume it.

        Budget-aware when `state` carries a request_deadline_monotonic (see
        services/deadline.py): raises RequestDeadlineExceeded instead of
        waiting past the caller's own deadline, so a starved queue fails fast
        rather than hanging a session indefinitely.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                for b in self._buckets:
                    b._refill(now)
                if all(b._tokens >= 1.0 for b in self._buckets):
                    for b in self._buckets:
                        b._tokens -= 1.0
                    return
                wait_s = max(
                    (1.0 - b._tokens) / b.rps for b in self._buckets if b._tokens < 1.0
                )
            await sleep_with_budget(wait_s, state, stage=stage)


_buckets: Dict[str, _TokenBucketGroup] = {}


def _resolve(endpoint: str) -> Optional[Tuple[str, List[Tuple[float, float]]]]:
    if not endpoint:
        return None
    limits = _RATE_LIMITS_BY_KEY.get(endpoint)
    key = endpoint
    if limits is None:
        key = _normalize_url(endpoint)
        limits = _RATE_LIMITS_BY_KEY.get(key)
    if not limits:
        return None
    return key, limits


async def throttle(
    endpoint: str,
    state: Optional[Mapping] = None,
    *,
    stage: str = "outbound_rate_limit",
    limit: Optional[List[Tuple[float, float]]] = None,
    key: Optional[str] = None,
) -> None:
    """Proactively wait for a free send slot on `endpoint` before dispatching a call.

    Without `limit`: no-op unless the admin configured a rate limit for this
    endpoint (config.RATE_LIMITS). Accepts either the endpoint's configured
    "name" or its base URL.

    With `limit=[(rps, burst), ...]`: bypasses the admin config lookup and
    throttles against those limit(s) instead — e.g. a user's own
    per-connection rate limit(s) (admin_ui `user_api_connections.
    rate_limit_config`). Multiple entries all apply simultaneously (AND — the
    call waits for the tightest one). `key` scopes the bucket (default:
    `endpoint`) so, e.g., different users' identically named connections
    don't share one bucket.
    """
    if limit is not None:
        if not limit:
            return
        bucket_key, limits = (key or endpoint), limit
    else:
        resolved = _resolve(endpoint)
        if resolved is None:
            return
        bucket_key, limits = resolved
    group = _buckets.get(bucket_key)
    if group is None or len(group._buckets) != len(limits):
        group = _TokenBucketGroup(limits)
        _buckets[bucket_key] = group
    if not group._has_capacity():
        logger.debug("rate_limiter: throttling outbound call to %s (%d limit(s))", bucket_key, len(limits))
    await group.acquire(state, stage=stage)
