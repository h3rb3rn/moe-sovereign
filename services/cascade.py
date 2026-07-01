"""
services/cascade.py — Typed Cascade Events for MoE-Sovereign agentic re-planning.

Upgrades the binary COMPLETE/NEEDS_MORE_INFO gap detector to typed cascade events
with specific re-plan strategies per failure mode. Each failure type carries a
routing hint so the planner knows *why* it is re-planning, not just *that* it should.

TASK-16 adds resolution tracking: each emitted event is stored in Valkey with a
`resolved` flag and can be queried via list_open_cascades(). STUCK_LOOP emission
marks all open events as unresolved in the decision_log.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger("MOE-SOVEREIGN")

_CASCADE_TTL    = int(os.getenv("CASCADE_EVENT_TTL_SECONDS", "86400"))  # 24h
_CASCADE_PREFIX = "cascade_events:"


class CascadeType(str, Enum):
    CONTEXT_GAP    = "CONTEXT_GAP"     # Missing retrieval context — different GraphRAG / web query needed
    EXPERT_FAILURE = "EXPERT_FAILURE"  # Expert returned capability disclaimer or empty
    CONTRADICTION  = "CONTRADICTION"   # Paraconsistent conflict between experts, unresolved
    SCOPE_DRIFT    = "SCOPE_DRIFT"     # Answer drifted from original request scope
    TOOL_FAILURE   = "TOOL_FAILURE"    # MCP tool call failed or returned empty
    COMPLETE       = "COMPLETE"        # No cascade — answer is sufficient


@dataclass
class CascadeEvent:
    cascade_type: CascadeType
    message: str          # Original gap description (passed through to planner prompt unchanged)
    replan_strategy: str  # Concrete hint injected into re-planner prompt
    # ── TASK-16: Resolution tracking ──────────────────────────────────────────
    event_id:    str = field(default_factory=lambda: str(uuid.uuid4()))
    request_id:  str = ""
    resolved:    bool = False
    resolved_at: Optional[str] = None


# Regex patterns for classifying gap text into cascade types. Order matters.
_EXPERT_LEAK_RE = re.compile(
    r"\b(cannot access|can'?t access|can'?t browse|no web|no internet|"
    r"unable to (browse|fetch|access)|don'?t have (web|internet|real.?time)|"
    r"capability disclaimer|attempt.{0,10}search)\b",
    re.I,
)
_TOOL_FAILURE_RE = re.compile(
    r"\b(mcp tool|tool (call|failure|failed|error|timeout)|"
    r"precision tool|tool not available)\b",
    re.I,
)
_CONTRADICTION_RE = re.compile(
    r"\b(conflict|contradict|inconsisten|disagree|divergen|"
    r"paraconsistent|unresolved)\b",
    re.I,
)
_SCOPE_DRIFT_RE = re.compile(
    r"\b(off.?topic|scope (drift|creep)|unrelated|changed (the )?subject|"
    r"didn'?t answer|not relevant to)\b",
    re.I,
)


def classify_gap(gap_text: str, strategy_hint: str = "") -> CascadeEvent:
    """Convert a gap description string into a typed CascadeEvent.

    Preserves the original gap_text as message so existing planner prompts
    remain unchanged. Only the cascade_type and replan_strategy are new.
    """
    if not gap_text or gap_text.upper() in ("COMPLETE", "NONE", ""):
        return CascadeEvent(CascadeType.COMPLETE, "", "")

    if _EXPERT_LEAK_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.EXPERT_FAILURE,
            gap_text,
            strategy_hint or "use web_researcher or fetch_pdf_text to retrieve the missing data directly",
        )

    if _TOOL_FAILURE_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.TOOL_FAILURE,
            gap_text,
            strategy_hint or "retry with a different MCP tool or fall back to web_search",
        )

    if _CONTRADICTION_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.CONTRADICTION,
            gap_text,
            strategy_hint or "use a single authoritative source to arbitrate the conflicting claims",
        )

    if _SCOPE_DRIFT_RE.search(gap_text):
        return CascadeEvent(
            CascadeType.SCOPE_DRIFT,
            gap_text,
            strategy_hint or "re-focus on the original request; discard tangential results",
        )

    return CascadeEvent(
        CascadeType.CONTEXT_GAP,
        gap_text,
        strategy_hint or "try a more specific search query or use GraphRAG with different entities",
    )


# ── TASK-16: Resolution Tracking ──────────────────────────────────────────────

def _valkey():
    try:
        import redis
        url = os.getenv("VALKEY_URL") or os.getenv("REDIS_URL") or "redis://terra_cache:6379/0"
        return redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
    except Exception:
        return None


def emit_cascade(event: CascadeEvent, request_id: str = "") -> CascadeEvent:
    """Persist a CascadeEvent to Valkey for lifecycle tracking.

    Sets request_id on the event and stores it under CASCADE_PREFIX:request_id.
    Fail-open: Valkey unavailability does not block the pipeline.
    """
    event.request_id = request_id or event.request_id
    try:
        client = _valkey()
        if client is None:
            return event
        key  = f"{_CASCADE_PREFIX}{request_id}"
        raw  = client.get(key)
        events: List[dict] = json.loads(raw) if raw else []
        events.append({
            "event_id":     event.event_id,
            "cascade_type": event.cascade_type.value,
            "message":      event.message,
            "resolved":     event.resolved,
            "resolved_at":  event.resolved_at,
        })
        client.setex(key, _CASCADE_TTL, json.dumps(events))
    except Exception as _e:
        logger.debug("cascade.emit_cascade: Valkey write failed: %s", _e)
    return event


def resolve_cascade(event: CascadeEvent) -> CascadeEvent:
    """Mark a CascadeEvent as resolved (in-place and in Valkey)."""
    import datetime
    event.resolved    = True
    event.resolved_at = datetime.datetime.utcnow().isoformat() + "Z"
    if not event.request_id:
        return event
    try:
        client = _valkey()
        if client is None:
            return event
        key  = f"{_CASCADE_PREFIX}{event.request_id}"
        raw  = client.get(key)
        if not raw:
            return event
        events = json.loads(raw)
        for e in events:
            if e.get("event_id") == event.event_id:
                e["resolved"]    = True
                e["resolved_at"] = event.resolved_at
        ttl = client.ttl(key)
        client.setex(key, max(ttl, 1), json.dumps(events))
    except Exception as _e:
        logger.debug("cascade.resolve_cascade: Valkey update failed: %s", _e)
    return event


def list_open_cascades(request_id: str) -> List[dict]:
    """Return all unresolved CascadeEvents for a given request_id."""
    try:
        client = _valkey()
        if client is None:
            return []
        raw = client.get(f"{_CASCADE_PREFIX}{request_id}")
        if not raw:
            return []
        events = json.loads(raw)
        return [e for e in events if not e.get("resolved")]
    except Exception as _e:
        logger.debug("cascade.list_open_cascades: Valkey read failed: %s", _e)
        return []
