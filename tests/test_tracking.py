"""
tests/test_tracking.py — services.tracking._record_stage unit tests.

Covers the fire-and-forget pipeline-stage trace helper used by the
live-pipeline-visualization diagram (Redis list moe:active:{chat_id}:trace,
bounded to 30 entries, TTL refreshed on every write).
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import state
from services.tracking import _record_stage


def _mock_redis_with_pipeline():
    mock_pipe = MagicMock()
    mock_pipe.execute = AsyncMock()
    mock_redis = MagicMock()
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)
    return mock_redis, mock_pipe


@pytest.mark.asyncio
async def test_record_stage_writes_rpush_ltrim_expire():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("chat123", "cache", "hit_l0", "detail-text")

    mock_redis.pipeline.assert_called_once()
    assert mock_pipe.rpush.call_count == 1
    key, entry_json = mock_pipe.rpush.call_args[0]
    assert key == "moe:active:chat123:trace"
    entry = json.loads(entry_json)
    assert entry["stage"] == "cache"
    assert entry["status"] == "hit_l0"
    assert entry["detail"] == "detail-text"
    assert isinstance(entry["ts"], float)

    # Bounded to the 30 most recent entries
    mock_pipe.ltrim.assert_called_once_with("moe:active:chat123:trace", -30, -1)
    # TTL refreshed on every write, matching moe:active:{chat_id}'s 7200s TTL
    mock_pipe.expire.assert_called_once_with("moe:active:chat123:trace", 7200)
    mock_pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_stage_default_status_started():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("chat123", "planner")

    entry = json.loads(mock_pipe.rpush.call_args[0][1])
    assert entry["status"] == "started"
    assert entry["detail"] == ""


@pytest.mark.asyncio
async def test_record_stage_noop_without_chat_id():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    with patch.object(state, "redis_client", mock_redis):
        await _record_stage("", "cache", "started")

    mock_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_record_stage_noop_without_redis_client():
    with patch.object(state, "redis_client", None):
        # Must not raise even though there is no Redis connection.
        await _record_stage("chat123", "cache", "started")


@pytest.mark.asyncio
async def test_record_stage_never_raises_on_redis_error():
    mock_redis, mock_pipe = _mock_redis_with_pipeline()
    mock_pipe.execute.side_effect = ConnectionError("redis down")
    with patch.object(state, "redis_client", mock_redis):
        # Fire-and-forget: errors are swallowed (logged, not raised).
        await _record_stage("chat123", "cache", "started")
