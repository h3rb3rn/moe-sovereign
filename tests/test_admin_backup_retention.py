"""Tests for admin_ui.backup_retention — the Autobackup job's retention rules.

admin_ui.app itself pulls in FastAPI/httpx/docker and cannot be imported from
the plain test environment (see admin_ui/deliberation_policy.py for the same
constraint), so this module is deliberately dependency-free and tested here
directly, mirroring the existing admin/orchestrator contract-boundary tests.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from admin_ui.backup_retention import enforce_backup_retention


def _touch(path: Path, size: int, age_days: float = 0) -> None:
    path.write_bytes(b"x" * size)
    if age_days:
        mtime = time.time() - age_days * 86400
        os.utime(path, (mtime, mtime))


def test_no_rules_deletes_nothing(tmp_path: Path):
    _touch(tmp_path / "a.tar.gz", 100, age_days=400)
    result = enforce_backup_retention(tmp_path, retain_days=0, max_size_mb=0)
    assert result == {
        "freed_bytes": 0,
        "deleted_by_age": 0,
        "deleted_by_size_cap": 0,
        "total_backups": 1,
        "total_size_bytes": 100,
    }


def test_age_rule_deletes_only_older_files(tmp_path: Path):
    _touch(tmp_path / "old.tar.gz", 100, age_days=40)
    _touch(tmp_path / "new.tar.gz", 100, age_days=1)

    result = enforce_backup_retention(tmp_path, retain_days=30, max_size_mb=0)

    assert result["deleted_by_age"] == 1
    assert result["freed_bytes"] == 100
    remaining = {p.name for p in tmp_path.glob("*")}
    assert remaining == {"new.tar.gz"}


def test_size_cap_deletes_oldest_first_until_under_cap(tmp_path: Path):
    one_mb = 1_048_576
    _touch(tmp_path / "day3.tar.gz", 2 * one_mb, age_days=3)
    _touch(tmp_path / "day2.tar.gz", 2 * one_mb, age_days=2)
    _touch(tmp_path / "day1.tar.gz", 2 * one_mb, age_days=1)

    # Total is 6 MB; cap at 3 MB must evict the two oldest, keep the newest.
    result = enforce_backup_retention(tmp_path, retain_days=0, max_size_mb=3)

    assert result["deleted_by_size_cap"] == 2
    assert result["freed_bytes"] == 4 * one_mb
    assert result["total_backups"] == 1
    assert result["total_size_bytes"] == 2 * one_mb
    remaining = {p.name for p in tmp_path.glob("*")}
    assert remaining == {"day1.tar.gz"}


def test_size_cap_not_triggered_when_already_under_cap(tmp_path: Path):
    _touch(tmp_path / "a.tar.gz", 1024, age_days=1)
    result = enforce_backup_retention(tmp_path, retain_days=0, max_size_mb=1000)
    assert result["deleted_by_size_cap"] == 0
    assert result["total_backups"] == 1


def test_age_and_size_rules_combine(tmp_path: Path):
    one_mb = 1_048_576
    _touch(tmp_path / "ancient.tar.gz", 5 * one_mb, age_days=100)  # evicted by age
    _touch(tmp_path / "day2.tar.gz", 5 * one_mb, age_days=2)       # evicted by size cap
    _touch(tmp_path / "day1.tar.gz", 5 * one_mb, age_days=1)       # kept

    result = enforce_backup_retention(tmp_path, retain_days=30, max_size_mb=6)

    assert result["deleted_by_age"] == 1
    assert result["deleted_by_size_cap"] == 1
    remaining = {p.name for p in tmp_path.glob("*")}
    assert remaining == {"day1.tar.gz"}


def test_hidden_files_are_ignored(tmp_path: Path):
    _touch(tmp_path / ".tmp-inprogress.tar.gz", 100, age_days=400)
    result = enforce_backup_retention(tmp_path, retain_days=1, max_size_mb=0)
    assert result["deleted_by_age"] == 0
    assert (tmp_path / ".tmp-inprogress.tar.gz").exists()


def test_missing_directory_is_a_no_op(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    result = enforce_backup_retention(missing, retain_days=30, max_size_mb=100)
    assert result == {
        "freed_bytes": 0,
        "deleted_by_age": 0,
        "deleted_by_size_cap": 0,
        "total_backups": 0,
        "total_size_bytes": 0,
    }
