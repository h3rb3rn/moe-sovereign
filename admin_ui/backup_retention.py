"""admin_ui/backup_retention.py — Retention enforcement for the Autobackup job.

Kept dependency-free (stdlib only) so it can be unit tested without importing
the full admin_ui.app FastAPI stack (see admin_ui/deliberation_policy.py for
the same isolation rationale — the admin image is a separate build context).
"""

from __future__ import annotations

import time
from pathlib import Path


def _backup_files(backup_dir: Path) -> list[Path]:
    if not backup_dir.exists():
        return []
    return [f for f in backup_dir.glob("*") if f.is_file() and not f.name.startswith(".")]


def enforce_backup_retention(
    backup_dir: Path,
    retain_days: float,
    max_size_mb: float,
) -> dict:
    """Deletes backup files older than retain_days, then deletes the oldest
    remaining files until the total size is at or under max_size_mb.

    Either rule is disabled by passing 0 (or a negative value) for it. Hidden
    files (leading '.') are ignored, e.g. a job's in-progress temp state.

    Returns freed bytes, per-rule deletion counts, and the post-run backup
    count/size so callers can log a single combined run record.
    """
    files = _backup_files(backup_dir)

    freed_bytes = 0
    deleted_by_age = 0
    deleted_by_size_cap = 0

    if retain_days and retain_days > 0:
        cutoff = time.time() - retain_days * 86400
        for f in list(files):
            st = f.stat()
            if st.st_mtime < cutoff:
                freed_bytes += st.st_size
                f.unlink()
                files.remove(f)
                deleted_by_age += 1

    if max_size_mb and max_size_mb > 0:
        cap_bytes = max_size_mb * 1_048_576
        files.sort(key=lambda f: f.stat().st_mtime)  # oldest first
        total = sum(f.stat().st_size for f in files)
        i = 0
        while total > cap_bytes and i < len(files):
            f = files[i]
            sz = f.stat().st_size
            f.unlink()
            total -= sz
            freed_bytes += sz
            deleted_by_size_cap += 1
            i += 1

    remaining = _backup_files(backup_dir)
    return {
        "freed_bytes": freed_bytes,
        "deleted_by_age": deleted_by_age,
        "deleted_by_size_cap": deleted_by_size_cap,
        "total_backups": len(remaining),
        "total_size_bytes": sum(f.stat().st_size for f in remaining),
    }
