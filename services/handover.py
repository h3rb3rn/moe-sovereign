"""
services/handover.py — Handover / Context-Preservation (TASK-18).

Serializes a snapshot of AgentState to Valkey when the pipeline hits a STUCK
condition, enabling continuation in a new session via POST /handover/{id}/resume.

Preserved fields (subset of AgentState — only what's needed to resume):
  plan, expert_results, chat_history, trust_verdict, self_critique_round,
  agentic_iteration, agentic_history, agentic_gap, working_memory
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN")

_HANDOVER_TTL    = int(os.getenv("HANDOVER_TTL_SECONDS", "14400"))  # 4 hours
_HANDOVER_PREFIX = "handover:"

_PRESERVED_KEYS = (
    "input",
    "plan",
    "expert_results",
    "chat_history",
    "trust_verdict",
    "trust_score",
    "self_critique_round",
    "agentic_iteration",
    "agentic_history",
    "agentic_gap",
    "working_memory",
    "mode",
    "response_id",
    "user_id",
    "session_id",
    "complexity_level",
    "cynefin_domain",
    "conflict_registry",
    "cascade_type",
)


def _valkey():
    try:
        import redis
        url = os.getenv("VALKEY_URL") or os.getenv("REDIS_URL") or "redis://terra_cache:6379/0"
        return redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
    except Exception:
        return None


def create_handover(state_: dict, reason: str) -> Optional[str]:
    """Serialize AgentState snapshot to Valkey for later resumption.

    Returns handover_id or None if Valkey unavailable.
    """
    handover_id = str(uuid.uuid4())
    snapshot = {k: state_.get(k) for k in _PRESERVED_KEYS if state_.get(k) is not None}
    payload = {
        "handover_id": handover_id,
        "reason":      reason,
        "snapshot":    snapshot,
    }
    try:
        client = _valkey()
        if client is None:
            logger.warning("Handover: Valkey unavailable — handover not created")
            return None
        client.setex(f"{_HANDOVER_PREFIX}{handover_id}", _HANDOVER_TTL, json.dumps(payload))
        logger.info("🤝 Handover created: %s (reason=%s)", handover_id, reason[:80])
        return handover_id
    except Exception as e:
        logger.warning("Handover: create_handover failed: %s", e)
        return None


def list_handovers_for_user(user_id: str) -> list:
    """Return all pending handovers for a specific user; fail-open returns empty list."""
    try:
        client = _valkey()
        if client is None:
            return []
        handovers = []
        for key in client.scan_iter(f"{_HANDOVER_PREFIX}*", count=200):
            raw = client.get(key)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            snapshot = payload.get("snapshot", {})
            if snapshot.get("user_id") != user_id:
                continue
            ttl = client.ttl(key)
            handovers.append({
                "handover_id":       payload.get("handover_id", ""),
                "reason":            payload.get("reason", ""),
                "input":             (snapshot.get("input") or "")[:200],
                "mode":              snapshot.get("mode", ""),
                "trust_verdict":     snapshot.get("trust_verdict", ""),
                "cynefin_domain":    snapshot.get("cynefin_domain", ""),
                "agentic_iteration": snapshot.get("agentic_iteration", 0),
                "ttl_remaining":     ttl,
            })
        return handovers
    except Exception as e:
        logger.warning("Handover: list_handovers_for_user failed: %s", e)
        return []


def restore_handover(handover_id: str) -> Optional[dict]:
    """Retrieve handover snapshot; returns None if not found or expired."""
    try:
        client = _valkey()
        if client is None:
            return None
        raw = client.get(f"{_HANDOVER_PREFIX}{handover_id}")
        if raw is None:
            return None
        payload = json.loads(raw)
        return payload.get("snapshot", {})
    except Exception as e:
        logger.warning("Handover: restore_handover failed: %s", e)
        return None
