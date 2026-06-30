"""
services/retry_budget.py — Retry budget tracking and STUCK_REQUEST detection.

Tracks per-request re-plan iteration counts and emits a STUCK_REQUEST Kafka
event when the agentic loop budget is exhausted without resolving the gap.
Operators subscribe to moe.stuck for alerts without polling.

Counter is keyed per response_id (not session) so parallel requests in the
same session do not interfere with each other.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

logger = logging.getLogger("MOE-SOVEREIGN")

KAFKA_TOPIC_STUCK = "moe.stuck"

# Redis TTL for stuck markers (1 hour)
_STUCK_TTL_S = 3600


async def check_and_emit_stuck(
    response_id: str,
    iteration: int,
    max_rounds: int,
    state_: dict,
    redis_client=None,
) -> bool:
    """Emit STUCK_REQUEST when the agentic loop has exhausted its budget.

    Returns True when the request is considered stuck.
    Fail-open: Redis / Kafka errors are caught and logged; the pipeline is never blocked.
    """
    if iteration < max_rounds:
        return False

    payload = {
        "response_id": response_id,
        "session_id":  state_.get("session_id", ""),
        "user_id":     state_.get("user_id", "anon"),
        "template":    state_.get("template_name", ""),
        "iteration":   iteration,
        "max_rounds":  max_rounds,
        "cascade_type": state_.get("cascade_type", "CONTEXT_GAP"),
        "last_gap":    (state_.get("agentic_gap") or "")[:300],
        "ts":          time.time(),
    }

    if redis_client is not None:
        try:
            await redis_client.setex(
                f"moe:stuck:{response_id}",
                _STUCK_TTL_S,
                json.dumps(payload),
            )
        except Exception as e:
            logger.debug("retry_budget: Redis write failed: %s", e)

    try:
        from services.kafka import _kafka_publish
        asyncio.create_task(_kafka_publish(KAFKA_TOPIC_STUCK, payload))
    except Exception as e:
        logger.debug("retry_budget: Kafka emit failed: %s", e)

    logger.warning(
        "🔴 STUCK_REQUEST: id=%s iter=%d/%d cascade=%s gap=%s",
        response_id, iteration, max_rounds,
        payload["cascade_type"],
        (state_.get("agentic_gap") or "")[:80],
    )
    return True
