"""
services/hitl_gate.py — Human-in-the-Loop Gate (TASK-14).

State-based approval flow via Valkey. When a request lands in PROCEED_WITH_ASSUMPTION
+ COMPLEX/CHAOTIC Cynefin domain, the response draft is frozen here and only released
after POST /gates/{id}/approve.

Gate lifecycle:
  pending → approved  (human approved)
  pending → rejected  (human rejected)
  pending → expired   (TTL elapsed, Valkey key evicted)

All operations are fail-open: if Valkey is unavailable, gate creation returns None
and the pipeline continues without freezing.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN")

_VALKEY_TTL  = int(os.getenv("HITL_GATE_TTL_SECONDS", "3600"))
_KEY_PREFIX  = "hitl_gate:"


def _valkey():
    """Lazy Valkey client — returns None if not available."""
    try:
        import redis
        url = os.getenv("VALKEY_URL") or os.getenv("REDIS_URL") or "redis://terra_cache:6379/0"
        return redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
    except Exception:
        return None


def create_gate(
    request_id: str,
    reason: str,
    response_draft: str,
    ttl_seconds: int = _VALKEY_TTL,
    user_id: str = "",
) -> Optional[str]:
    """Freeze a response draft behind a gate; return gate_id or None on failure."""
    gate_id = str(uuid.uuid4())
    payload = {
        "gate_id":       gate_id,
        "request_id":    request_id,
        "user_id":       user_id,
        "reason":        reason,
        "response_draft": response_draft,
        "status":        "pending",
        "ttl_seconds":   ttl_seconds,
    }
    try:
        client = _valkey()
        if client is None:
            logger.warning("HITL Gate: Valkey unavailable — gate not created")
            return None
        client.setex(f"{_KEY_PREFIX}{gate_id}", ttl_seconds, json.dumps(payload))
        logger.info("🚦 HITL Gate created: %s (reason=%s)", gate_id, reason[:80])
        return gate_id
    except Exception as e:
        logger.warning("HITL Gate: create_gate failed: %s", e)
        return None


def get_gate(gate_id: str) -> Optional[dict]:
    """Retrieve gate state; returns None if not found or expired."""
    try:
        client = _valkey()
        if client is None:
            return None
        raw = client.get(f"{_KEY_PREFIX}{gate_id}")
        if raw is None:
            return None
        data = json.loads(raw)
        # TTL elapsed → key evicted by Valkey, but mark explicitly for clarity
        return data
    except Exception as e:
        logger.warning("HITL Gate: get_gate failed: %s", e)
        return None


def approve_gate(gate_id: str, approved_by: str = "") -> bool:
    """Approve a pending gate; returns True on success."""
    return _set_status(gate_id, "approved", by=approved_by)


def reject_gate(gate_id: str, rejected_by: str = "") -> bool:
    """Reject a pending gate; returns True on success."""
    return _set_status(gate_id, "rejected", by=rejected_by)


def _set_status(gate_id: str, status: str, by: str = "") -> bool:
    try:
        client = _valkey()
        if client is None:
            return False
        key = f"{_KEY_PREFIX}{gate_id}"
        raw = client.get(key)
        if raw is None:
            return False
        data = json.loads(raw)
        if data.get("status") != "pending":
            return False  # Already decided
        data["status"]    = status
        data["decided_by"] = by
        ttl = client.ttl(key)
        if ttl > 0:
            client.setex(key, ttl, json.dumps(data))
        else:
            client.set(key, json.dumps(data))
        logger.info("🚦 HITL Gate %s: %s (by=%s)", gate_id, status, by)
        return True
    except Exception as e:
        logger.warning("HITL Gate: _set_status failed: %s", e)
        return False
