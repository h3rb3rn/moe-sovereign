"""
mission_context — Persistent cross-session project state (Star Trek Layer 2: Remember).

Stores a single JSON document at $MOE_DATA_ROOT/mission_context.json describing
the current "mission": what is being worked on, open tasks, recent decisions.

No database dependency — plain JSON file with atomic write (write-then-rename).
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("MOE-SOVEREIGN.mission_context")

_DATA_ROOT = Path(os.getenv("MOE_DATA_ROOT", "/opt/moe-infra"))
_CONTEXT_FILE = _DATA_ROOT / "mission_context.json"

_EMPTY_CONTEXT: dict[str, Any] = {
    "updated_at":       None,
    "title":            "",
    "description":      "",
    "active_nodes":     [],
    "open_tasks":       [],
    "recent_decisions": [],
    "tags":             [],
}

_MAX_DECISIONS = 20   # keep only the last N decisions to bound file growth


def _load_raw() -> dict:
    """Read context file; return empty skeleton on any error."""
    try:
        if _CONTEXT_FILE.exists():
            return json.loads(_CONTEXT_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("mission_context: could not read %s: %s", _CONTEXT_FILE, exc)
    return dict(_EMPTY_CONTEXT)


def _save_raw(ctx: dict) -> None:
    """Atomic write: write to a temp file then rename (avoids partial writes)."""
    _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_DATA_ROOT, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(ctx, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _CONTEXT_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


async def get_context() -> dict:
    """Return the current mission context."""
    return _load_raw()


async def set_context(data: dict) -> dict:
    """Replace the entire mission context.

    Unknown keys are preserved so future schema extensions are non-destructive.
    """
    ctx = dict(_EMPTY_CONTEXT)
    ctx.update(data)
    ctx["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Enforce decision list cap.
    if isinstance(ctx.get("recent_decisions"), list):
        ctx["recent_decisions"] = ctx["recent_decisions"][-_MAX_DECISIONS:]
    _save_raw(ctx)
    logger.info("mission_context: replaced (title=%r)", ctx.get("title"))
    return ctx


async def patch_context(patch: dict) -> dict:
    """Merge-update the mission context.

    List fields (open_tasks, recent_decisions, tags, active_nodes) are replaced,
    not appended, to keep the operation idempotent and predictable.
    If 'recent_decisions' contains a bare string it is wrapped as {"at": ..., "text": ...}.
    """
    ctx = _load_raw()
    for key, value in patch.items():
        if key == "updated_at":
            continue  # always auto-set
        ctx[key] = value

    # Normalise decision entries.
    decisions = ctx.get("recent_decisions", [])
    normalised = []
    for d in decisions:
        if isinstance(d, str):
            normalised.append({"at": datetime.now(timezone.utc).isoformat(), "text": d})
        elif isinstance(d, dict):
            normalised.append(d)
    ctx["recent_decisions"] = normalised[-_MAX_DECISIONS:]

    ctx["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_raw(ctx)
    logger.info("mission_context: patched fields=%s", list(patch.keys()))
    return ctx


async def append_decision(text: str) -> dict:
    """Convenience helper: append one decision entry to recent_decisions."""
    return await patch_context({
        "recent_decisions": _load_raw().get("recent_decisions", []) + [
            {"at": datetime.now(timezone.utc).isoformat(), "text": text}
        ]
    })
