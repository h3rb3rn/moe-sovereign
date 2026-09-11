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
    monkeypatch.setattr("state._userdb_pool", None)
    monkeypatch.setattr("state.redis_client", AsyncMock())

    from services.auth import _db_fallback_key_lookup
    result = await _db_fallback_key_lookup("any_hash")

    assert result is None


@pytest.mark.asyncio
async def test_returns_none_when_key_not_in_postgres(monkeypatch):
    """Postgres returns no row → None, no Redis call."""
    monkeypatch.setattr("state._userdb_pool", _make_pool_with_row(None))

    mock_redis = AsyncMock()
    monkeypatch.setattr("state.redis_client", mock_redis)

    from services.auth import _db_fallback_key_lookup
    result = await _db_fallback_key_lookup("unknown_hash")

    assert result is None
    mock_redis.hgetall.assert_not_called()


@pytest.mark.asyncio
async def test_returns_user_dict_when_key_synced_to_redis(monkeypatch):
    """Happy path: Postgres finds the key, sync writes to Redis, Redis returns data."""
    db_row = {"user_id": "u-42", "key_hash": "testhash", "is_active": True}
    monkeypatch.setattr("state._userdb_pool", _make_pool_with_row(db_row))

    redis_data = {"user_id": "u-42", "is_active": "1", "quota": "1000"}
    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value=redis_data)
    monkeypatch.setattr("state.redis_client", mock_redis)

    # Patch sync_user_to_redis so it does not try to open a DB connection.
    with patch("admin_ui.database.sync_user_to_redis", new=AsyncMock()):
        from services.auth import _db_fallback_key_lookup
        result = await _db_fallback_key_lookup("testhash")

    assert result is not None
    assert result["user_id"] == "u-42"
    assert result["is_active"] == "1"


@pytest.mark.asyncio
async def test_returns_none_on_pool_exception(monkeypatch):
    """Any exception from the pool must be swallowed — auth never crashes the app."""
    broken_pool = MagicMock()
    broken_pool.connection.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr("state._userdb_pool", broken_pool)
    monkeypatch.setattr("state.redis_client", None)

    from services.auth import _db_fallback_key_lookup
    result = await _db_fallback_key_lookup("any_hash")

    assert result is None


# ---------------------------------------------------------------------------
# require_admin_or_system — internal /v1/admin/* route dependency
#
# Added after the 2026-09-11 review found these routes (backup, knowledge
# ingestion, RLSF trigger, ontology healer control) had no auth at all,
# reachable by anything that can reach the core container's published port.
# ---------------------------------------------------------------------------

def _fake_request(headers: dict):
    req = MagicMock()
    req.headers = headers
    return req


class _FakeHTTPException(Exception):
    """Real exception class standing in for fastapi.HTTPException.

    conftest.py stubs the whole `fastapi` module with a MagicMock so the
    suite can collect without the package installed; a MagicMock class
    cannot be `raise`d or matched by pytest.raises. Patching
    services.auth.HTTPException to this for the duration of a test keeps
    that global stub intact for everything else.
    """
    def __init__(self, status_code: int, detail=None):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@pytest.mark.asyncio
async def test_require_admin_rejects_missing_key(monkeypatch):
    from services.auth import require_admin_or_system
    monkeypatch.setattr("services.auth.HTTPException", _FakeHTTPException)

    with pytest.raises(_FakeHTTPException) as exc:
        await require_admin_or_system(_fake_request({}))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_admin_accepts_system_key(monkeypatch):
    monkeypatch.setenv("SYSTEM_API_KEY", "moe-sk-the-system-key")
    from services.auth import require_admin_or_system

    result = await require_admin_or_system(
        _fake_request({"x-api-key": "moe-sk-the-system-key"})
    )
    assert result["is_admin"] is True
    assert result["auth_via"] == "system_key"


@pytest.mark.asyncio
async def test_require_admin_rejects_invalid_key(monkeypatch):
    from services.auth import require_admin_or_system
    monkeypatch.setattr("services.auth.HTTPException", _FakeHTTPException)

    monkeypatch.setenv("SYSTEM_API_KEY", "moe-sk-the-system-key")
    monkeypatch.setattr("state._userdb_pool", None)
    monkeypatch.setattr("state.redis_client", None)

    with pytest.raises(_FakeHTTPException) as exc:
        await require_admin_or_system(
            _fake_request({"x-api-key": "moe-sk-not-the-system-key"})
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_require_admin_rejects_valid_non_admin_key(monkeypatch):
    from services.auth import require_admin_or_system
    monkeypatch.setattr("services.auth.HTTPException", _FakeHTTPException)

    monkeypatch.setenv("SYSTEM_API_KEY", "moe-sk-the-system-key")
    with patch(
        "services.auth._validate_api_key",
        new=AsyncMock(return_value={"user_id": "u-1", "is_active": "1"}),
    ):
        with pytest.raises(_FakeHTTPException) as exc:
            await require_admin_or_system(
                _fake_request({"x-api-key": "moe-sk-a-regular-user-key"})
            )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_admin_accepts_key_with_is_admin_flag(monkeypatch):
    from services.auth import require_admin_or_system

    monkeypatch.setenv("SYSTEM_API_KEY", "moe-sk-the-system-key")
    with patch(
        "services.auth._validate_api_key",
        new=AsyncMock(return_value={"user_id": "u-2", "is_admin": True}),
    ):
        result = await require_admin_or_system(
            _fake_request({"x-api-key": "moe-sk-an-admin-users-key"})
        )
    assert result["user_id"] == "u-2"
