"""
services/conversation_log.py — Per-user conversation audit logging.

Each authenticated request is appended as a JSON line to
  {CONVERSATION_LOG_DIR}/{user_id}.jsonl

Log rotation is handled externally by logrotate (daily, with dateext).
Retention cleanup is driven by run_retention_cleanup(), called once per day
from the admin UI's lifespan background loop.

JSONL entry schema:
    ts                  ISO-8601 UTC timestamp
    request_id          MoE chat ID (moe-*)
    session_id          optional session identifier
    model               model name used
    moe_mode            routing mode (default/research/code/…)
    messages            full input message list [{role, content}, …]
    response            full assistant response text
    prompt_tokens       int
    completion_tokens   int
    latency_ms          int or null
    expert_domains      comma-separated routing categories
    cache_hit           bool
    agentic_rounds      int
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MOE-SOVEREIGN")

# Per-user asyncio locks prevent JSONL line corruption under concurrent writes.
_user_locks: dict[str, asyncio.Lock] = {}
_locks_meta: asyncio.Lock = asyncio.Lock()


def _log_path(user_id: str) -> Path:
    from config import CONVERSATION_LOG_DIR
    return Path(CONVERSATION_LOG_DIR) / f"{user_id}.jsonl"


async def _get_user_lock(user_id: str) -> asyncio.Lock:
    async with _locks_meta:
        if user_id not in _user_locks:
            _user_locks[user_id] = asyncio.Lock()
        return _user_locks[user_id]


def _write_entry(path: Path, line: str) -> None:
    """Blocking file append — run in a thread via asyncio.to_thread."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


async def log_conversation(
    user_id: str,
    request_id: str,
    messages: list[dict],
    response: str,
    model: str = "",
    moe_mode: str = "",
    session_id: Optional[str] = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: Optional[int] = None,
    expert_domains: str = "",
    cache_hit: bool = False,
    agentic_rounds: int = 0,
) -> None:
    """Append one conversation entry to the user's JSONL audit log.

    Fire-and-forget: never raises exceptions — all errors are logged as warnings.
    Acquires a per-user asyncio.Lock to prevent line interleaving under concurrent requests.
    """
    from config import CONVERSATION_LOG_ENABLED
    if not CONVERSATION_LOG_ENABLED or not user_id or user_id == "anon":
        return
    entry = {
        "ts":                datetime.now(timezone.utc).isoformat(),
        "request_id":        request_id,
        "session_id":        session_id,
        "model":             model,
        "moe_mode":          moe_mode,
        "messages":          messages,
        "response":          response,
        "prompt_tokens":     prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms":        latency_ms,
        "expert_domains":    expert_domains,
        "cache_hit":         cache_hit,
        "agentic_rounds":    agentic_rounds,
    }
    try:
        lock = await _get_user_lock(user_id)
        async with lock:
            await asyncio.to_thread(_write_entry, _log_path(user_id), json.dumps(entry, ensure_ascii=False))
    except Exception as exc:
        logger.warning("conversation_log write failed for user=%s: %s", user_id, exc)


# ─── Date pattern for rotated files: {user_id}.jsonl-20260521 ─────────────────

_DATE_RE = re.compile(r"\.jsonl-(\d{8})(?:\.gz)?$")


def _parse_rotation_date(filename: str) -> Optional[datetime]:
    """Extract rotation date from a logrotate-produced filename."""
    m = _DATE_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iter_log_files(user_id: str) -> list[Path]:
    """Return all log files for a user: current + rotated, newest first."""
    from config import CONVERSATION_LOG_DIR
    log_dir = Path(CONVERSATION_LOG_DIR)
    current = log_dir / f"{user_id}.jsonl"
    rotated = sorted(
        log_dir.glob(f"{user_id}.jsonl-*"),
        key=lambda p: p.name,
        reverse=True,
    )
    files = []
    if current.exists():
        files.append(current)
    files.extend(rotated)
    return files


def _read_lines_from_file(path: Path) -> list[str]:
    """Read all lines from a plain or gzip-compressed JSONL file."""
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return fh.readlines()
        with open(path, encoding="utf-8") as fh:
            return fh.readlines()
    except Exception as exc:
        logger.warning("conversation_log read failed for %s: %s", path, exc)
        return []


def _reverse_read_current(path: Path, max_lines: int) -> list[str]:
    """Read the last max_lines lines from a plain JSONL file efficiently.

    Uses seek() to avoid loading the entire file for the common case of
    displaying the most recent N entries.
    """
    try:
        chunk_size = 65536
        lines: list[str] = []
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            remaining = fh.tell()
            buf = b""
            while remaining > 0 and len(lines) < max_lines + 1:
                read_size = min(chunk_size, remaining)
                remaining -= read_size
                fh.seek(remaining)
                buf = fh.read(read_size) + buf
                lines = buf.decode("utf-8", errors="replace").splitlines()
            return [l for l in lines if l.strip()][-max_lines:] if lines else []
    except Exception as exc:
        logger.warning("reverse_read failed for %s: %s", path, exc)
        return []


async def read_user_log(
    user_id: str,
    page: int = 1,
    per_page: int = 50,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
) -> tuple[list[dict], int]:
    """Return a paginated slice of the user's conversation audit log (newest first).

    For the default view (no filters) the current file is read in reverse to
    avoid an O(n) full-file scan.  When filters are active all files are read
    fully so results can be sorted globally before slicing.
    """
    from datetime import date as _date
    per_page = min(max(1, per_page), 200)
    page     = max(1, page)
    has_filter = bool(date_from or date_to or search)
    files = _iter_log_files(user_id)
    if not files:
        return [], 0

    needle = search.lower() if search else None
    dt_from = _date.fromisoformat(date_from) if date_from else None
    dt_to   = _date.fromisoformat(date_to)   if date_to   else None

    def _passes(entry: dict) -> bool:
        if dt_from or dt_to:
            try:
                d = datetime.fromisoformat(entry["ts"]).date()
                if dt_from and d < dt_from:
                    return False
                if dt_to   and d > dt_to:
                    return False
            except (KeyError, ValueError):
                return False
        if needle:
            in_messages = any(
                needle in m.get("content", "").lower()
                for m in entry.get("messages", [])
                if isinstance(m, dict)
            )
            if not in_messages and needle not in entry.get("response", "").lower():
                return False
        return True

    def _parse_line(line: str) -> Optional[dict]:
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    if not has_filter:
        # Fast path: reverse-read the current file for the most recent entries,
        # fall through to archives only if we need more.
        need = page * per_page
        collected: list[dict] = []
        for path in files:
            if len(collected) >= need + per_page:
                break
            if path.suffix == ".gz" or not path.exists():
                raw_lines = await asyncio.to_thread(_read_lines_from_file, path)
                entries = [e for l in reversed(raw_lines) if (e := _parse_line(l)) is not None]
            else:
                want = need + per_page - len(collected)
                raw_lines = await asyncio.to_thread(_reverse_read_current, path, want)
                entries = [e for l in reversed(raw_lines) if (e := _parse_line(l)) is not None]
            collected.extend(entries)
        # Total is approximate for the no-filter fast path — count lines in all files
        total = len(collected)
        start = (page - 1) * per_page
        return collected[start:start + per_page], total

    # Filtered path: read all files, apply filters, sort, paginate.
    all_entries: list[dict] = []
    for path in files:
        raw_lines = await asyncio.to_thread(_read_lines_from_file, path)
        for line in raw_lines:
            entry = _parse_line(line)
            if entry is not None and _passes(entry):
                all_entries.append(entry)

    all_entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    total = len(all_entries)
    start = (page - 1) * per_page
    return all_entries[start:start + per_page], total


async def run_retention_cleanup() -> int:
    """Delete rotated log files that exceed each user's retention period.

    Called once per day from the admin UI lifespan loop.
    Returns the number of files deleted.
    """
    from admin_ui import database as db
    user_retentions = await db.get_all_user_retentions()
    retention_map = {u["user_id"]: u["retention_days"] for u in user_retentions}

    from config import CONVERSATION_LOG_DIR, CONVERSATION_LOG_RETENTION_DAYS_DEFAULT
    log_dir = Path(CONVERSATION_LOG_DIR)
    if not log_dir.exists():
        return 0

    deleted = 0
    now = datetime.now(timezone.utc)

    for rotated_file in log_dir.glob("*.jsonl-*"):
        rotation_date = _parse_rotation_date(rotated_file.name)
        if rotation_date is None:
            continue
        # Extract user_id from filename: "{user_id}.jsonl-YYYYMMDD[.gz]"
        stem = rotated_file.name.split(".jsonl-")[0]
        retention_days = retention_map.get(stem, CONVERSATION_LOG_RETENTION_DAYS_DEFAULT)
        age_days = (now - rotation_date).days
        if age_days > retention_days:
            try:
                rotated_file.unlink()
                deleted += 1
                logger.debug("Deleted old audit log %s (age=%dd > retention=%dd)", rotated_file.name, age_days, retention_days)
            except Exception as exc:
                logger.warning("Failed to delete %s: %s", rotated_file, exc)

    if deleted:
        logger.info("Conversation log cleanup: deleted %d rotated file(s)", deleted)
    return deleted
