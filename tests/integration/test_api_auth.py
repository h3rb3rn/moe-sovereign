"""
Auth unit tests for _db_fallback_key_lookup().

Tests the three critical code paths without any live infrastructure:
  1. _userdb_pool is None  → returns None immediately (graceful degradation)
  2. Key found in Postgres  → syncs to Redis, returns user dict
  3. Redis and Postgres both unavailable → returns None, no exception raised

These tests use monkeypatch to replace module-level globals in main.py.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pool_with_row(row: dict | None):
    """Build a mock connection pool whose fetchone() returns `row`."""
    mock_cur = AsyncMock()
    mock_cur.fetchone = AsyncMock(return_value=row)
    mock_cur.__aenter__ = AsyncMock(return_value=mock_cur)
    mock_cur.__aexit__ = AsyncMock(return_value=None)

    mock_conn = MagicMock()
    mock_conn.cursor = MagicMock(return_value=mock_cur)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.connection = MagicMock(return_value=mock_conn)
    return mock_pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_none_when_pool_is_none(monkeypatch):
    """Early-return path: no Postgres pool → None without touching Redis."""
    monkeypatch.setattr("main._userdb_pool", None)
    monkeypatch.setattr("main.redis_client", AsyncMock())

    from main import _db_fallback_key_lookup
    result = await _db_fallback_key_lookup("any_hash")

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_key_not_in_postgres(monkeypatch):
    """Postgres returns no row → None, no Redis call."""
    monkeypatch.setattr("main._userdb_pool", _make_pool_with_row(None))

    mock_redis = AsyncMock()
    monkeypatch.setattr("main.redis_client", mock_redis)

    from main import _db_fallback_key_lookup
    result = await _db_fallback_key_lookup("unknown_hash")

    assert result is None
    mock_redis.hgetall.assert_not_called()


@pytest.mark.asyncio
async def test_returns_user_dict_when_key_synced_to_redis(monkeypatch):
    """Happy path: Postgres finds the key, sync writes to Redis, Redis returns data."""
    db_row = {"user_id": "u-42", "key_hash": "testhash", "is_active": True}
    monkeypatch.setattr("main._userdb_pool", _make_pool_with_row(db_row))

    redis_data = {"user_id": "u-42", "is_active": "1", "quota": "1000"}
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value=redis_data)
    monkeypatch.setattr("main.redis_client", mock_redis)

    # Patch sync_user_to_redis so it does not try to open a DB connection.
    with patch("admin_ui.database.sync_user_to_redis", new=AsyncMock()):
        from main import _db_fallback_key_lookup
        result = await _db_fallback_key_lookup("testhash")

    assert result is not None
    assert result["user_id"] == "u-42"
    assert result["is_active"] == "1"


@pytest.mark.asyncio
async def test_returns_none_on_pool_exception(monkeypatch):
    """Any exception from the pool must be swallowed — auth never crashes the app."""
    broken_pool = MagicMock()
    broken_pool.connection.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr("main._userdb_pool", broken_pool)
    monkeypatch.setattr("main.redis_client", None)

    from main import _db_fallback_key_lookup
    result = await _db_fallback_key_lookup("any_hash")

    assert result is None
