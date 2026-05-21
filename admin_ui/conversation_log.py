"""
admin_ui/conversation_log.py — Read-side helpers for conversation audit logs.

The write-side (log_conversation) lives in services/conversation_log.py inside
langgraph-app.  This module provides the reader and cleanup functions used by
the moe-admin portal.

Both containers share /app/logs/users via a bind-mounted host directory
(${MOE_DATA_ROOT}/user-audit-logs).
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

logger = logging.getLogger("moe-admin")

_DATE_RE = re.compile(r"\.jsonl-(\d{8})(?:\.gz)?$")


def _log_dir() -> Path:
    return Path(os.getenv("CONVERSATION_LOG_DIR", "/app/logs/users"))


def _log_path(user_id: str) -> Path:
    return _log_dir() / f"{user_id}.jsonl"


def _parse_rotation_date(filename: str) -> Optional[datetime]:
    m = _DATE_RE.search(filename)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iter_log_files(user_id: str) -> list[Path]:
    log_dir = _log_dir()
    current = log_dir / f"{user_id}.jsonl"
    rotated = sorted(log_dir.glob(f"{user_id}.jsonl-*"), key=lambda p: p.name, reverse=True)
    files: list[Path] = []
    if current.exists():
        files.append(current)
    files.extend(rotated)
    return files


def _read_lines_from_file(path: Path) -> list[str]:
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                return fh.readlines()
        with open(path, encoding="utf-8") as fh:
            return fh.readlines()
    except Exception as exc:
        logger.warning("conversation_log read error %s: %s", path, exc)
        return []


def _reverse_read_current(path: Path, max_lines: int) -> list[str]:
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
        logger.warning("reverse_read error %s: %s", path, exc)
        return []


async def read_user_log(
    user_id: str,
    page: int = 1,
    per_page: int = 50,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
) -> tuple[list[dict], int]:
    """Return a paginated slice of the user's conversation audit log (newest first)."""
    from datetime import date as _date
    per_page = min(max(1, per_page), 200)
    page     = max(1, page)
    has_filter = bool(date_from or date_to or search)
    files = _iter_log_files(user_id)
    if not files:
        return [], 0

    needle  = search.lower() if search else None
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
            in_msgs = any(
                needle in m.get("content", "").lower()
                for m in entry.get("messages", [])
                if isinstance(m, dict)
            )
            if not in_msgs and needle not in entry.get("response", "").lower():
                return False
        return True

    def _parse(line: str) -> Optional[dict]:
        line = line.strip()
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    if not has_filter:
        need = page * per_page
        collected: list[dict] = []
        for path in files:
            if len(collected) >= need + per_page:
                break
            if path.suffix == ".gz" or not path.exists():
                raw = await asyncio.to_thread(_read_lines_from_file, path)
                entries = [e for l in reversed(raw) if (e := _parse(l)) is not None]
            else:
                want = need + per_page - len(collected)
                raw = await asyncio.to_thread(_reverse_read_current, path, want)
                entries = [e for l in reversed(raw) if (e := _parse(l)) is not None]
            collected.extend(entries)
        total = len(collected)
        start = (page - 1) * per_page
        return collected[start:start + per_page], total

    all_entries: list[dict] = []
    for path in files:
        raw = await asyncio.to_thread(_read_lines_from_file, path)
        for line in raw:
            entry = _parse(line)
            if entry is not None and _passes(entry):
                all_entries.append(entry)
    all_entries.sort(key=lambda e: e.get("ts", ""), reverse=True)
    total = len(all_entries)
    start = (page - 1) * per_page
    return all_entries[start:start + per_page], total


async def run_retention_cleanup() -> int:
    """Delete rotated log files exceeding each user's retention period."""
    import database as db
    user_retentions = await db.get_all_user_retentions()
    retention_map = {u["user_id"]: u["retention_days"] for u in user_retentions}

    default_days = int(os.getenv("CONVERSATION_LOG_RETENTION_DAYS_DEFAULT", "90"))
    log_dir = _log_dir()
    if not log_dir.exists():
        return 0

    deleted = 0
    now = datetime.now(timezone.utc)
    for rotated_file in log_dir.glob("*.jsonl-*"):
        rotation_date = _parse_rotation_date(rotated_file.name)
        if rotation_date is None:
            continue
        stem = rotated_file.name.split(".jsonl-")[0]
        retention_days = retention_map.get(stem, default_days)
        if (now - rotation_date).days > retention_days:
            try:
                rotated_file.unlink()
                deleted += 1
            except Exception as exc:
                logger.warning("Failed to delete %s: %s", rotated_file, exc)
    if deleted:
        logger.info("Conversation log cleanup: deleted %d file(s)", deleted)
    return deleted
