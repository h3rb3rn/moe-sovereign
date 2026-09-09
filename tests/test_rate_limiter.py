"""Proactive per-endpoint outbound throttling (services/rate_limiter.py).

Verifies the token bucket paces calls instead of letting them through
unthrottled, that an endpoint without a configured rate limit stays a no-op,
that the "N requests per <amount> <unit>" conversion in config.py is correct,
that multiple simultaneous limits on one connection (e.g. 20/minute AND
1000/24h) both apply — a call waits for whichever is currently tightest —
and that a caller-supplied `limit` override (used for a user-owned BYOK
connection's own rate limit(s)) bypasses the admin config lookup.
"""

import asyncio
import time

import pytest

import services.rate_limiter as rate_limiter
from config import _parse_rate_limit, rate_limit_to_rps, rate_limits_to_list


@pytest.mark.asyncio
async def test_throttle_is_noop_for_unconfigured_endpoint():
    t0 = time.monotonic()
    for _ in range(20):
        await rate_limiter.throttle("http://unconfigured-endpoint:11434")
    assert time.monotonic() - t0 < 0.1


@pytest.mark.asyncio
async def test_throttle_paces_calls_to_configured_rps(monkeypatch):
    # 5 req/s, burst 1 → the 2nd call must wait ~0.2s for a refilled token.
    monkeypatch.setitem(rate_limiter._RATE_LIMITS_BY_KEY, "limited-endpoint", [(5.0, 1.0)])
    rate_limiter._buckets.pop("limited-endpoint", None)

    t0 = time.monotonic()
    await rate_limiter.throttle("limited-endpoint")  # consumes the initial burst token
    first = time.monotonic() - t0
    await rate_limiter.throttle("limited-endpoint")  # must wait for refill
    second = time.monotonic() - t0

    assert first < 0.05
    assert second >= 0.19


@pytest.mark.asyncio
async def test_throttle_resolves_by_normalized_url(monkeypatch):
    monkeypatch.setitem(rate_limiter._RATE_LIMITS_BY_KEY, "http://cloud-provider", [(5.0, 1.0)])
    rate_limiter._buckets.pop("http://cloud-provider", None)

    t0 = time.monotonic()
    await rate_limiter.throttle("http://cloud-provider/v1/")
    assert time.monotonic() - t0 < 0.05


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_bucket_per_endpoint(monkeypatch):
    # 10 req/s, burst 1 → 4 concurrent callers must together span ~0.3s.
    monkeypatch.setitem(rate_limiter._RATE_LIMITS_BY_KEY, "shared-endpoint", [(10.0, 1.0)])
    rate_limiter._buckets.pop("shared-endpoint", None)

    t0 = time.monotonic()
    await asyncio.gather(*(rate_limiter.throttle("shared-endpoint") for _ in range(4)))
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.29


@pytest.mark.asyncio
async def test_throttle_override_bypasses_admin_config(monkeypatch):
    # No admin config for this endpoint at all — override must still throttle.
    monkeypatch.delitem(rate_limiter._RATE_LIMITS_BY_KEY, "override-endpoint", raising=False)
    rate_limiter._buckets.pop("override-key", None)

    t0 = time.monotonic()
    await rate_limiter.throttle("override-endpoint", limit=[(5.0, 1.0)], key="override-key")
    await rate_limiter.throttle("override-endpoint", limit=[(5.0, 1.0)], key="override-key")
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.19


@pytest.mark.asyncio
async def test_throttle_override_keys_are_isolated_per_caller(monkeypatch):
    # Two different `key`s (e.g. two users' identically-named BYOK connection)
    # must not share a bucket.
    rate_limiter._buckets.pop("user-a", None)
    rate_limiter._buckets.pop("user-b", None)

    t0 = time.monotonic()
    await rate_limiter.throttle("shared-name", limit=[(1.0, 1.0)], key="user-a")
    await rate_limiter.throttle("shared-name", limit=[(1.0, 1.0)], key="user-b")
    elapsed = time.monotonic() - t0

    assert elapsed < 0.05  # both consumed their own burst token, no cross-wait


@pytest.mark.asyncio
async def test_multi_limit_waits_for_tightest_constraint(monkeypatch):
    # OpenRouter-style: 20/minute (loose, big burst) AND a tight 2 req/burst-1
    # second-level cap. The tight cap must bind even though the loose one has
    # plenty of remaining burst.
    key = "multi-limit-endpoint"
    rate_limiter._buckets.pop(key, None)

    t0 = time.monotonic()
    await rate_limiter.throttle(key, limit=[(20 / 60, 20.0), (5.0, 1.0)], key=key)  # consumes both burst tokens
    first = time.monotonic() - t0
    await rate_limiter.throttle(key, limit=[(20 / 60, 20.0), (5.0, 1.0)], key=key)  # tight limit (5/s) forces the wait
    second = time.monotonic() - t0

    assert first < 0.05
    assert second >= 0.19  # ~0.2s refill for the 5 req/s bucket, not the near-instant 20/min one


@pytest.mark.asyncio
async def test_multi_limit_all_buckets_consumed_atomically(monkeypatch):
    # Both bucket token counts must drop together — never one without the other.
    key = "atomic-multi-limit"
    rate_limiter._buckets.pop(key, None)
    limits = [(1.0, 3.0), (1.0, 3.0)]

    await rate_limiter.throttle(key, limit=limits, key=key)
    group = rate_limiter._buckets[key]
    assert group._buckets[0]._tokens == pytest.approx(2.0)
    assert group._buckets[1]._tokens == pytest.approx(2.0)


def test_parse_rate_limit_ignores_invalid_and_zero_values():
    assert _parse_rate_limit({}) is None
    assert _parse_rate_limit({"rate_limit_requests": 0}) is None
    assert _parse_rate_limit({"rate_limit_requests": -1}) is None
    assert _parse_rate_limit({"rate_limit_requests": "not-a-number"}) is None


def test_parse_rate_limit_defaults_period_to_one_second():
    assert _parse_rate_limit({"rate_limit_requests": 3}) == [(3.0, 3.0)]


def test_parse_rate_limit_uses_explicit_burst():
    assert _parse_rate_limit({"rate_limit_requests": 3, "rate_limit_burst": 10}) == [(3.0, 10.0)]


def test_parse_rate_limit_prefers_rate_limit_limits_list():
    entry = {
        "rate_limit_requests": 999,  # must be ignored — rate_limit_limits takes precedence
        "rate_limit_limits": [
            {"requests": 20, "period_amount": 1, "period_unit": "minute"},
            {"requests": 1000, "period_amount": 24, "period_unit": "hour"},
        ],
    }
    result = _parse_rate_limit(entry)
    assert result == [
        pytest.approx((20 / 60, 20.0)),
        pytest.approx((1000 / 86400, 1000.0)),
    ]


def test_rate_limit_to_rps_requests_per_second():
    rps, burst = rate_limit_to_rps(4, 1, "second")
    assert rps == pytest.approx(4.0)
    assert burst == 4.0


def test_rate_limit_to_rps_requests_per_minute():
    rps, burst = rate_limit_to_rps(60, 1, "minute")
    assert rps == pytest.approx(1.0)
    assert burst == 60.0


def test_rate_limit_to_rps_requests_per_hour():
    rps, burst = rate_limit_to_rps(1000, 1, "hour")
    assert rps == pytest.approx(1000 / 3600)
    assert burst == 1000.0


def test_rate_limit_to_rps_configurable_period_amount():
    # 200 requests per 5 minutes
    rps, _burst = rate_limit_to_rps(200, 5, "minute")
    assert rps == pytest.approx(200 / 300)


def test_rate_limit_to_rps_none_for_unset_or_invalid():
    assert rate_limit_to_rps(None) is None
    assert rate_limit_to_rps(0) is None
    assert rate_limit_to_rps(-5) is None
    assert rate_limit_to_rps("not-a-number") is None


def test_rate_limits_to_list_combines_multiple_entries():
    # OpenRouter free models: 20/minute AND 1000/24h simultaneously.
    result = rate_limits_to_list([
        {"max_requests": 20, "period_amount": 1, "period_unit": "minute"},
        {"max_requests": 1000, "period_amount": 24, "period_unit": "hour"},
    ])
    assert result == [
        pytest.approx((20 / 60, 20.0)),
        pytest.approx((1000 / 86400, 1000.0)),
    ]


def test_rate_limits_to_list_skips_invalid_entries():
    result = rate_limits_to_list([
        {"max_requests": 0},  # invalid — dropped
        {"max_requests": 5, "period_amount": 1, "period_unit": "second"},
    ])
    assert result == [(5.0, 5.0)]


def test_rate_limits_to_list_none_for_empty_or_all_invalid():
    assert rate_limits_to_list([]) is None
    assert rate_limits_to_list([{"max_requests": 0}]) is None
    assert rate_limits_to_list("not-a-list") is None
