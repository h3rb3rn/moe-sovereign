"""services/ai_io_audit.py — Structured AI I/O Audit Service (TASK-29).

Records every LLM call made by inference.py with:
  - API-key redaction (recursive, case-insensitive)
  - In-memory "live" entries for active calls
  - Postgres persistence on completion

EU-AI-Act Art. 13 transparency + DSGVO logging baseline.
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("MOE-SOVEREIGN")

_REDACTED = "[redacted]"
_SENSITIVE_KEYS = frozenset({"authorization", "api-key", "apikey", "x-api-key", "token"})

# In-memory registry of calls that are still in flight
_live_entries: Dict[str, "AiIoAuditEntry"] = {}


# ── Sanitisation ──────────────────────────────────────────────────────────────

def sanitize_audit_payload(payload: object) -> object:
    """Recursively redact values whose key (case-insensitive) is in _SENSITIVE_KEYS."""
    if isinstance(payload, dict):
        return {
            k: (_REDACTED if k.lower() in _SENSITIVE_KEYS else sanitize_audit_payload(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [sanitize_audit_payload(item) for item in payload]
    return payload


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class AiIoAuditEntry:
    audit_id:          str
    session_id:        str
    request_id:        str
    model:             str
    endpoint:          str
    stage:             str
    prompt_tokens:     Optional[int]
    completion_tokens: Optional[int]
    started_at:        str
    completed_at:      Optional[str]
    status:            str                          # "pending" | "completed" | "error"
    request_body:      dict                         # already sanitized
    response_body:     Optional[dict]               # sanitized on completion

    def to_dict(self) -> dict:
        return {
            "audit_id":          self.audit_id,
            "session_id":        self.session_id,
            "request_id":        self.request_id,
            "model":             self.model,
            "endpoint":          self.endpoint,
            "stage":             self.stage,
            "prompt_tokens":     self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "started_at":        self.started_at,
            "completed_at":      self.completed_at,
            "status":            self.status,
            "request_body":      self.request_body,
            "response_body":     self.response_body,
        }


# ── Lifecycle ─────────────────────────────────────────────────────────────────

def create_audit_entry(
    session_id: str,
    request_id: str,
    model: str,
    endpoint: str,
    stage: str,
    request_body: dict,
) -> AiIoAuditEntry:
    """Create and register a new live audit entry. Returns the entry."""
    audit_id = f"{session_id}:{request_id}"
    entry = AiIoAuditEntry(
        audit_id=audit_id,
        session_id=session_id,
        request_id=request_id,
        model=model,
        endpoint=endpoint,
        stage=stage,
        prompt_tokens=None,
        completion_tokens=None,
        started_at=_utc_now(),
        completed_at=None,
        status="pending",
        request_body=sanitize_audit_payload(request_body),
        response_body=None,
    )
    _live_entries[audit_id] = entry
    return entry


async def complete_audit_entry(
    audit_id: str,
    response_body: Optional[dict],
    prompt_tokens: Optional[int],
    completion_tokens: Optional[int],
    status: str = "completed",
) -> None:
    """Finalise an audit entry and persist it to Postgres, then remove from live map."""
    entry = _live_entries.pop(audit_id, None)
    if entry is None:
        logger.debug("ai_io_audit: entry %s not in live map (already completed?)", audit_id)
        return

    entry.completed_at      = _utc_now()
    entry.status            = status
    entry.prompt_tokens     = prompt_tokens
    entry.completion_tokens = completion_tokens
    entry.response_body     = sanitize_audit_payload(response_body) if response_body else None

    await _persist(entry)


def get_live_entries() -> List[AiIoAuditEntry]:
    """Return all active (not yet completed) audit entries."""
    return list(_live_entries.values())


# ── Persistence ───────────────────────────────────────────────────────────────

async def _persist(entry: AiIoAuditEntry) -> None:
    """Write a completed AiIoAuditEntry to Postgres (best-effort, errors are logged)."""
    try:
        import json as _json
        import state as _state
        if _state._userdb_pool is None:
            return
        async with _state._userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO ai_io_audit_log
                       (audit_id, session_id, request_id, model, endpoint, stage,
                        prompt_tokens, completion_tokens, started_at, completed_at,
                        status, request_body, response_body)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (audit_id) DO NOTHING""",
                    (
                        entry.audit_id, entry.session_id, entry.request_id,
                        entry.model, entry.endpoint, entry.stage,
                        entry.prompt_tokens, entry.completion_tokens,
                        entry.started_at, entry.completed_at,
                        entry.status,
                        _json.dumps(entry.request_body),
                        _json.dumps(entry.response_body) if entry.response_body else None,
                    ),
                )
    except Exception as exc:
        logger.warning("ai_io_audit: persist failed for %s: %s", entry.audit_id, exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
