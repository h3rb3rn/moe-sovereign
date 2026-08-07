"""Tests for the shared monotonic request budget."""

import asyncio
import time

import pytest

from services.deadline import (
    RequestDeadlineExceeded,
    bounded_output_tokens,
    remaining_timeout,
    sleep_with_budget,
    wait_for_budget,
)


def test_remaining_timeout_is_capped_by_stage_limit():
    state = {"request_deadline_monotonic": time.monotonic() + 30}
    assert 4.9 < remaining_timeout(state, 5, reserve_seconds=0) <= 5


def test_remaining_timeout_uses_smaller_request_budget():
    state = {"request_deadline_monotonic": time.monotonic() + 0.5}
    timeout = remaining_timeout(state, 10, reserve_seconds=0)
    assert 0 < timeout <= 0.5


def test_expired_deadline_fails_before_dispatch():
    state = {"request_deadline_monotonic": time.monotonic() - 1}
    with pytest.raises(RequestDeadlineExceeded, match="exhausted"):
        remaining_timeout(state, 10, stage="planner")


def test_client_output_budget_caps_internal_generation():
    state = {"client_max_output_tokens": 1200}
    assert bounded_output_tokens(state, 16384) == 1200


def test_tiny_client_budget_preserves_minimum_internal_contract_room():
    state = {"client_max_output_tokens": 1}
    assert bounded_output_tokens(state, 4096, minimum_internal=128) == 128


@pytest.mark.asyncio
async def test_wait_for_budget_cancels_slow_work():
    state = {"request_deadline_monotonic": time.monotonic() + 0.08}
    with pytest.raises(RequestDeadlineExceeded, match="expert"):
        await wait_for_budget(
            asyncio.sleep(0.2),
            state,
            1,
            stage="expert",
        )


@pytest.mark.asyncio
async def test_retry_sleep_does_not_overrun_deadline():
    state = {"request_deadline_monotonic": time.monotonic() + 0.2}
    with pytest.raises(RequestDeadlineExceeded, match="retry delay"):
        await sleep_with_budget(1, state, stage="judge_retry")
